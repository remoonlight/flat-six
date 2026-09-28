/**
 * Bounded read-only diagnostics:session manager.
 * Spawn `python -m scripts.diagnostics.sessions --stdio`. One child.
 * Live allowed only with whitelist + confirmations; denied in test/headless/injected (unless probe).
 */
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import { resolvePythonCandidates } from "./offline-diagnostics.mjs";

export const SESSION_CHANNEL = "diagnostics:session";

const ACTIONS = new Set(["prepare", "start", "status", "cancel", "overview"]);
const PROFILES = new Set(["porsche-981-2014-dme", "porsche-981-2014-gateway"]);
const MODES = new Set(["simulation", "live"]);
const SESSION_TASKS = new Set(["read", "clear", "engine"]);
const ENGINE_PROFILE = "porsche-981-2014-dme";
const PID_RE = /^[0-9A-F]{2}$/;
const ENGINE_SAMPLE_CYCLES_MIN = 1;
const ENGINE_SAMPLE_CYCLES_MAX = 10;
const ENGINE_SAMPLE_CYCLES_DEFAULT = 5;
const ENGINE_INTERVAL_MS_MIN = 500;
const ENGINE_INTERVAL_MS_MAX = 5000;
const ENGINE_INTERVAL_MS_DEFAULT = 1000;
const SCENARIOS = new Set([
  "success",
  "identity-mismatch",
  "negative",
  "pending-timeout",
  "disconnect",
  "slow",
]);
const EVENT_TYPES = new Set(["plan", "progress", "result"]);
const RESULT_STATUS = new Set(["completed", "failed", "cancelled"]);

const PREPARE_START_KEYS = Object.freeze([
  "action",
  "profileId",
  "mode",
  "scenario",
  "operationIds",
  "resumeRunId",
  "confirmedReadOnly",
  "x431Inactive",
  "sessionTask",
  "confirmedClearDtc",
  "sampleCycles",
  "intervalMs",
]);
const JOB_KEYS = Object.freeze(["action", "jobId"]);
const OVERVIEW_KEYS = Object.freeze(["action"]);

const MAX_JSON = 16 * 1024;
const MAX_STDOUT = 512 * 1024;
const MAX_STDERR = 32 * 1024;
const MAX_OP_IDS = 32;
const MAX_EVENTS = 40;
const MAX_HISTORY = 8;
const OP_ID_RE = /^[A-Za-z0-9:_-]{1,80}$/;
const RUN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/;
const JOB_ID_RE = /^j[a-f0-9]{16}$/;

const FLAGS = Object.freeze({
  executionEnabled: false,
  liveVerified: false,
  writePayload: null,
});

export function fail(error, extra = {}) {
  return { ok: false, error, ...FLAGS, ...extra };
}

function isPlain(v) {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

function newJobId() {
  return "j" + randomBytes(8).toString("hex");
}

export function liveBlocked(opts = {}, env = process.env) {
  if (opts.spawnFn && opts.allowInjectedLive !== true) return "injected_child";
  if (env.PORSCHE981_SESSION_DENY_LIVE === "1") return "deny_live";
  if (env.PORSCHE981_HEADLESS === "1") return "headless";
  if (env.PORSCHE981_SESSION_STUB === "1") return "stub_env";
  return null;
}

export function validateSessionRequest(req) {
  if (!isPlain(req)) return fail("malformed_request");
  const keys = Object.keys(req);
  if (!ACTIONS.has(req.action)) return fail("invalid_action", { action: req.action });
  const allowed =
    req.action === "overview" ? OVERVIEW_KEYS : req.action === "status" || req.action === "cancel" ? JOB_KEYS : PREPARE_START_KEYS;
  const extra = keys.filter((k) => !allowed.includes(k));
  if (extra.length) return fail("forbidden_field", { fields: extra });
  let encoded;
  try {
    encoded = Buffer.byteLength(JSON.stringify(req), "utf8");
  } catch {
    return fail("malformed_request");
  }
  if (encoded > MAX_JSON) return fail("request_too_large");

  if (req.action === "status" || req.action === "cancel") {
    if (typeof req.jobId !== "string" || !JOB_ID_RE.test(req.jobId)) {
      return fail("malformed_job_id");
    }
    return null;
  }
  if (req.action === "overview") {
    return null;
  }

  if (typeof req.profileId !== "string" || !PROFILES.has(req.profileId)) {
    return fail("invalid_profile_id");
  }
  const mode = req.mode == null ? "simulation" : req.mode;
  if (!MODES.has(mode)) return fail("invalid_mode");
  if (req.scenario != null) {
    if (mode !== "simulation" || !SCENARIOS.has(req.scenario)) return fail("invalid_scenario");
  }
  if (req.operationIds != null) {
    if (!Array.isArray(req.operationIds) || req.operationIds.length > MAX_OP_IDS) {
      return fail("malformed_operation_ids");
    }
    const seen = new Set();
    for (const id of req.operationIds) {
      if (typeof id !== "string" || !OP_ID_RE.test(id) || seen.has(id)) {
        return fail("malformed_operation_ids");
      }
      seen.add(id);
    }
  }
  if (req.resumeRunId != null && (typeof req.resumeRunId !== "string" || !RUN_ID_RE.test(req.resumeRunId))) {
    return fail("malformed_resume_run_id");
  }
  if (req.confirmedReadOnly != null && req.confirmedReadOnly !== true && req.confirmedReadOnly !== false) {
    return fail("malformed_confirmed_read_only");
  }
  if (req.x431Inactive != null && req.x431Inactive !== true && req.x431Inactive !== false) {
    return fail("malformed_x431_inactive");
  }
  if (req.confirmedClearDtc != null && req.confirmedClearDtc !== true && req.confirmedClearDtc !== false) {
    return fail("malformed_confirmed_clear_dtc");
  }
  const sessionTask = req.sessionTask == null ? "read" : req.sessionTask;
  if (req.sessionTask != null && !SESSION_TASKS.has(req.sessionTask)) {
    return fail("invalid_session_task");
  }
  if (sessionTask !== "engine" && (req.sampleCycles != null || req.intervalMs != null)) {
    return fail("engine_fields_without_task");
  }
  if (sessionTask === "engine") {
    if (req.profileId !== ENGINE_PROFILE) return fail("invalid_profile_id");
    if (req.resumeRunId != null) return fail("engine_resume_forbidden");
    if (req.operationIds != null) return fail("engine_operation_ids_forbidden");
    if (req.confirmedClearDtc === true) return fail("engine_conflicts_clear");
    if (req.sampleCycles != null) {
      if (!Number.isInteger(req.sampleCycles) || req.sampleCycles < ENGINE_SAMPLE_CYCLES_MIN || req.sampleCycles > ENGINE_SAMPLE_CYCLES_MAX) {
        return fail("invalid_sample_cycles");
      }
    }
    if (req.intervalMs != null) {
      if (!Number.isInteger(req.intervalMs) || req.intervalMs < ENGINE_INTERVAL_MS_MIN || req.intervalMs > ENGINE_INTERVAL_MS_MAX) {
        return fail("invalid_interval_ms");
      }
    }
    if (mode === "live" && (req.confirmedReadOnly !== true || req.x431Inactive !== true)) {
      return fail("live_confirmation_required");
    }
  } else if (sessionTask === "clear") {
    if (req.resumeRunId != null) return fail("clear_resume_forbidden");
    if (req.operationIds != null) return fail("clear_operation_ids_forbidden");
    if (mode === "live") {
      if (req.confirmedReadOnly === true) return fail("clear_conflicts_read_only");
      if (req.confirmedClearDtc !== true || req.x431Inactive !== true) {
        return fail("live_clear_confirmation_required");
      }
    }
  } else if (req.confirmedClearDtc === true) {
    return fail("clear_flag_without_task");
  } else if (mode === "live" && (req.confirmedReadOnly !== true || req.x431Inactive !== true)) {
    return fail("live_confirmation_required");
  }
  return null;
}

function toBackend(req, extra = {}) {
  const mode = req.mode || "simulation";
  const sessionTask = req.sessionTask || "read";
  const body = { action: req.action === "start" ? "run" : "prepare", profileId: req.profileId, mode };
  if (mode === "live" && extra.deviceId) body.deviceId = extra.deviceId;
  if (sessionTask === "clear") body.sessionTask = "clear";
  if (sessionTask === "engine") {
    body.sessionTask = "engine";
    body.sampleCycles = req.sampleCycles == null ? ENGINE_SAMPLE_CYCLES_DEFAULT : req.sampleCycles;
    body.intervalMs = req.intervalMs == null ? ENGINE_INTERVAL_MS_DEFAULT : req.intervalMs;
  }
  if (mode === "simulation") body.scenario = req.scenario || "success";
  if (req.operationIds) body.operationIds = req.operationIds;
  if (req.resumeRunId) body.resumeRunId = req.resumeRunId;
  if (mode === "live") {
    body.x431Inactive = true;
    if (sessionTask === "clear") body.confirmedClearDtc = true;
    else body.confirmedReadOnly = true;
  }
  return body;
}

function pidListOk(arr) {
  if (!Array.isArray(arr)) return false;
  for (const id of arr) {
    if (typeof id !== "string" || !PID_RE.test(id)) return false;
  }
  return true;
}

function engineSampleOk(s) {
  if (!isPlain(s)) return false;
  if (typeof s.pid !== "string" || !PID_RE.test(s.pid)) return false;
  if (typeof s.label !== "string") return false;
  if (typeof s.value !== "number" || !Number.isFinite(s.value)) return false;
  if (typeof s.unit !== "string") return false;
  if (typeof s.capturedUtc !== "string") return false;
  if (typeof s.elapsedMs !== "number" || !Number.isFinite(s.elapsedMs)) return false;
  if (!Number.isInteger(s.cycle) || s.cycle < 1) return false;
  if (typeof s.synthetic !== "boolean") return false;
  return true;
}

function checkEnginePayload(engine, backend) {
  if (!isPlain(engine)) return "protocol_error";
  if (!pidListOk(engine.supportedPids) || !pidListOk(engine.unsupportedPids)) return "protocol_error";
  if (!Array.isArray(engine.samples)) return "protocol_error";
  for (const s of engine.samples) {
    if (!engineSampleOk(s)) return "protocol_error";
  }
  if (!Number.isInteger(engine.completedCycles) || engine.completedCycles < 0) return "protocol_error";
  if (!Number.isInteger(engine.sampleCycles) || !Number.isInteger(engine.intervalMs)) return "protocol_error";
  if (backend.sampleCycles != null && engine.sampleCycles !== backend.sampleCycles) return "protocol_error";
  if (backend.intervalMs != null && engine.intervalMs !== backend.intervalMs) return "protocol_error";
  return null;
}

function sessionsMissing(repoRoot) {
  const p = path.join(repoRoot, "scripts", "diagnostics", "sessions.py");
  return fs.existsSync(p) ? null : p;
}

function killChild(child) {
  if (!child || child.killed || child.exitCode != null) return;
  try {
    child.kill();
  } catch {
    /* */
  }
}

function stamp(doc) {
  if (!isPlain(doc)) return { ...FLAGS };
  return { ...doc, executionEnabled: false, liveVerified: false, writePayload: null };
}

function checkSuccessResult(doc, backend) {
  if (doc.ok !== true || doc.type !== "result") return null;
  if (doc.mode !== backend.mode) return "protocol_error";
  if (doc.profileId !== backend.profileId) return "protocol_error";
  if (doc.status !== "completed") return "protocol_error";
  if (typeof doc.runId !== "string" || !RUN_ID_RE.test(doc.runId)) return "protocol_error";
  if (!Array.isArray(doc.results)) return "protocol_error";
  if (doc.simulation !== (backend.mode === "simulation")) return "protocol_error";
  if (doc.liveVerified !== false) return "protocol_error";
  if (doc.writePayload !== null) return "protocol_error";
  if (doc.error != null) return "protocol_error";
  const task = backend.sessionTask || "read";
  if (task === "clear") {
    if (doc.sessionTask !== "clear") return "protocol_error";
    if (!isPlain(doc.clear) || doc.clear.clearSucceeded !== true) return "protocol_error";
    if (doc.clear.writePayload != null && doc.clear.writePayload !== null) return "protocol_error";
    const post = doc.results.filter((r) => r && r.postClear === true && typeof r.operationId === "string");
    if (!post.length) return "protocol_error";
  }
  if (task === "engine") {
    if (doc.sessionTask !== "engine") return "protocol_error";
    const bad = checkEnginePayload(doc.engine, backend);
    if (bad) return bad;
  }
  return null;
}

function checkEvent(ev, backend) {
  if (!EVENT_TYPES.has(ev.type)) return "protocol_error";
  if (ev.type === "progress") {
    if (typeof ev.runId !== "string" || typeof ev.stage !== "string") return "protocol_error";
    if (ev.profileId != null && ev.profileId !== backend.profileId) return "protocol_error";
    if (!Number.isInteger(ev.completed) || !Number.isInteger(ev.total)) return "protocol_error";
    if (ev.stage === "engine" && ev.engine != null) {
      const bad = checkEnginePayload(ev.engine, backend);
      if (bad) return bad;
    }
  }
  if (ev.type === "plan" && typeof ev.ok !== "boolean") return "protocol_error";
  if (ev.type === "result") {
    if (typeof ev.ok !== "boolean") return "protocol_error";
    const successBad = checkSuccessResult(ev, backend);
    if (successBad) return successBad;
    if (ev.profileId != null && ev.profileId !== backend.profileId) return "protocol_error";
    if (ev.mode != null && ev.mode !== backend.mode) return "protocol_error";
    if (ev.status != null && !RESULT_STATUS.has(ev.status)) return "protocol_error";
  }
  return null;
}

function parseLine(line, backend) {
  const t = line.trim();
  if (!t) return { skip: true };
  let doc;
  try {
    doc = JSON.parse(t);
  } catch (e) {
    return { error: "malformed_output", detail: String(e) };
  }
  if (!isPlain(doc)) return { error: "protocol_error" };
  const bad = checkEvent(doc, backend);
  if (bad) return { error: bad };
  return { event: stamp(doc) };
}

/** Selfcheck-only NDJSON child. Not used by production manager. */
export function stubSpawnFn() {
  return function spawnStub() {
    const child = new EventEmitter();
    child.killed = false;
    child.exitCode = null;
    child.stdin = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    let buf = "";
    let req = null;
    let hangTimer = null;
    const finish = (code, text) => {
      if (child.exitCode != null) return;
      if (hangTimer) clearTimeout(hangTimer);
      hangTimer = null;
      if (text) queueMicrotask(() => child.stdout.emit("data", Buffer.from(text)));
      child.exitCode = code;
      queueMicrotask(() => child.emit("close", code));
    };
    child.kill = () => {
      child.killed = true;
      finish(1, "");
    };
    child.stdin.write = (chunk) => {
      buf += String(chunk);
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        let parsed;
        try {
          parsed = JSON.parse(t);
        } catch {
          finish(2, "");
          return true;
        }
        if (parsed.action === "cancel") {
          finish(
            0,
            JSON.stringify({
              type: "result",
              ok: false,
              mode: parsed.mode || req?.mode || "simulation",
              runId: req?.resumeRunId || "simcancelledxx",
              profileId: req?.profileId,
              status: "cancelled",
              error: "cancelled",
              results: [],
              simulation: true,
            }) + "\n",
          );
          return true;
        }
        req = parsed;
        if (parsed.action === "prepare") {
          finish(
            0,
            JSON.stringify({
              type: "plan",
              ok: true,
              plan: {
                profileId: parsed.profileId,
                session: { requestHex: "1089", expectedPositiveHex: "5089" },
                identityOperations: [{ field: "dsn", operationId: "dme-dsn" }],
                dependentOperations: [{ operationId: "dme-dtc", kind: "kwp-dtc-18" }],
                blockedCandidates: [{ reason: "not-liveAllowed" }],
              },
            }) + "\n",
          );
          return true;
        }
        const scenario = parsed.scenario || "success";
        const runId = parsed.resumeRunId || "simsuccessxxxxxxxx";
        if (scenario === "slow") {
          hangTimer = setTimeout(() => finish(1, ""), 60_000);
          return true;
        }
        const ok = scenario === "success";
        queueMicrotask(() =>
          finish(
            ok ? 0 : 1,
            JSON.stringify({
              type: "progress",
              runId,
              stage: "identity",
              profileId: parsed.profileId,
              completed: 1,
              total: 3,
            }) +
              "\n" +
              JSON.stringify({
                type: "result",
                ok,
                mode: "simulation",
                runId,
                profileId: parsed.profileId,
                status: ok ? "completed" : "failed",
                error: ok ? null : scenario,
                results: [],
                simulation: true,
                liveVerified: false,
                writePayload: null,
              }) +
              "\n",
          ),
        );
      }
      return true;
    };
    child.stdin.end = () => {};
    return child;
  };
}

function pushEvent(job, ev) {
  if (ev.type === "progress") {
    job.events.push(ev);
    if (job.events.length > MAX_EVENTS) job.events.splice(0, job.events.length - MAX_EVENTS);
  }
  job.latest = ev;
  if (ev.type === "plan") job.plan = ev;
  if (ev.type === "result") job.final = ev;
}

export function createReadOnlySessionManager(opts) {
  const repoRoot = opts.repoRoot;
  const timeoutMs = opts.timeoutMs ?? 180_000;
  const prepareTimeoutMs = opts.prepareTimeoutMs ?? 60_000;
  const maxStdout = opts.maxStdout ?? MAX_STDOUT;
  const gracefulMs = opts.gracefulMs ?? 1500;
  const killWatchMs = opts.killWatchMs ?? 400;
  const envIn = opts.env || process.env;
  const spawnFn = opts.spawnFn || spawn;
  const injected = Boolean(opts.spawnFn);
  const gate = opts.gate;
  const getLiveDeviceId = opts.getLiveDeviceId;

  let active = null;
  const jobs = new Map();
  const historyOrder = [];
  const timers = new Set();
  let lastTransportDisconnect = false;

  function trackTimer(t) {
    timers.add(t);
    return t;
  }
  function clearT(t) {
    clearTimeout(t);
    timers.delete(t);
  }

  function remember(job) {
    if (!historyOrder.includes(job.id)) historyOrder.push(job.id);
    while (historyOrder.length > MAX_HISTORY) {
      const drop = historyOrder.shift();
      const j = jobs.get(drop);
      if (j && j.state !== "running" && j.state !== "cancelling") jobs.delete(drop);
    }
  }

  function snapshot(job) {
    return {
      ok: !job.error,
      ...FLAGS,
      jobId: job.id,
      state: job.state,
      events: job.events.slice(-MAX_EVENTS),
      latest: job.latest || null,
      final: job.final || null,
      plan: job.plan || null,
      error: job.error || null,
      resumed: Boolean(job.resumed),
    };
  }

  function spawnSession(backend, job, onDone) {
    const env = { ...envIn, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
    delete env.PORSCHE981_SERIAL;
    delete env.PORSCHE981_SERIAL_PORT;
    const payload = JSON.stringify(backend) + "\n";
    job.backendPayload = payload;
    const candidates = injected ? [{ exe: "stub", prefix: [] }] : resolvePythonCandidates(env);
    let idx = 0;
    let settled = false;

    const settle = (error) => {
      if (settled) return;
      settled = true;
      onDone(error);
    };

    const tryOne = () => {
      if (settled) return;
      if (idx >= candidates.length) {
        if (active && active.jobId === job.id) active = null;
        settle("python_runtime_missing");
        return;
      }
      const { exe, prefix } = candidates[idx++];
      const args = injected
        ? ["-m", "scripts.diagnostics.sessions", "--stdio"]
        : [...prefix, "-m", "scripts.diagnostics.sessions", "--stdio"];
      let child;
      try {
        child = spawnFn(exe, args, {
          cwd: repoRoot,
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          shell: false,
        });
      } catch {
        tryOne();
        return;
      }

      const attempt = { abandoned: false };
      job.child = child;
      active = { jobId: job.id, child };
      let stdout = Buffer.alloc(0);
      let rest = "";
      const decoder = new StringDecoder("utf8");
      let timedOut = false;
      let capped = false;
      let protocolErr = null;
      let watchdog = null;
      const tmo = backend.action === "prepare" ? prepareTimeoutMs : timeoutMs;

      const finishWait = (error) => {
        if (attempt.abandoned || settled) return;
        killChild(child);
        if (!watchdog) {
          watchdog = trackTimer(
            setTimeout(() => {
              if (active && active.jobId === job.id) active = null;
              settle(error || "cancel_timeout");
            }, killWatchMs),
          );
        }
      };

      const timer = trackTimer(
        setTimeout(() => {
          timedOut = true;
          sendCancel(job, true);
          if (!watchdog) {
            watchdog = trackTimer(
              setTimeout(() => {
                if (settled) return;
                if (active && active.jobId === job.id) active = null;
                settle("timeout");
              }, gracefulMs + killWatchMs),
            );
          }
        }, tmo),
      );

      const consume = (chunk) => {
        if (attempt.abandoned || capped || protocolErr) return;
        const room = maxStdout + 1 - stdout.length;
        const take = chunk.subarray(0, Math.max(0, room));
        stdout = Buffer.concat([stdout, take]);
        if (stdout.length > maxStdout) {
          capped = true;
          finishWait("output_cap");
          return;
        }
        rest += decoder.write(take);
        const lines = rest.split("\n");
        rest = lines.pop() || "";
        for (const line of lines) {
          const parsed = parseLine(line, backend);
          if (parsed.skip) continue;
          if (parsed.error) {
            protocolErr = parsed.error;
            finishWait(protocolErr);
            return;
          }
          if (job.final && parsed.event.type !== "result") {
            protocolErr = "protocol_error";
            finishWait(protocolErr);
            return;
          }
          if (job.final && parsed.event.type === "result") {
            protocolErr = "protocol_error";
            finishWait(protocolErr);
            return;
          }
          pushEvent(job, parsed.event);
        }
      };

      child.on("error", (err) => {
        if (attempt.abandoned || settled) return;
        if (err && (err.code === "ENOENT" || err.code === "EINVAL") && !injected) {
          attempt.abandoned = true;
          clearT(timer);
          killChild(child);
          if (active && active.jobId === job.id) active = null;
          tryOne();
          return;
        }
        finishWait("spawn_failed");
      });
      child.stdout?.on("data", consume);
      child.stderr?.on("data", (chunk) => {
        if (attempt.abandoned) return;
        job.stderr = (job.stderr || "") + chunk.toString("utf8");
        if (job.stderr.length > MAX_STDERR) job.stderr = job.stderr.slice(0, MAX_STDERR);
      });
      child.stdin?.on("error", (err) => {
        if (attempt.abandoned || settled) return;
        if (err && err.code === "EPIPE") finishWait("stdin_closed");
      });
      child.on("close", (code) => {
        if (attempt.abandoned || settled) return;
        rest += decoder.end();
        if (rest.trim()) {
          if (job.final) {
            protocolErr = protocolErr || "malformed_output";
          } else {
            const parsed = parseLine(rest, backend);
            if (parsed.error) protocolErr = parsed.error;
            else if (!parsed.skip && parsed.event) pushEvent(job, parsed.event);
          }
        }
        clearT(timer);
        if (watchdog) clearT(watchdog);
        if (active && active.jobId === job.id) active = null;
        if (protocolErr) {
          settle(protocolErr);
          return;
        }
        if (capped) {
          settle("output_cap");
          return;
        }
        if (timedOut) {
          settle("timeout");
          return;
        }
        if (backend.action === "prepare") {
          if (!job.plan) {
            settle("missing_result");
            return;
          }
          if (code !== 0 && job.plan.ok === true) {
            settle("process_failed");
            return;
          }
          settle(null);
          return;
        }
        if (!job.final) {
          settle("missing_result");
          return;
        }
        if (job.final.ok === true && code !== 0) {
          settle("process_failed");
          return;
        }
        settle(null);
      });

      try {
        child.stdin.write(payload, "utf8");
      } catch {
        finishWait("stdin_closed");
      }
    };
    tryOne();
  }

  function sendCancel(job, fromTimeout) {
    const child = job.child;
    if (!child || child.exitCode != null) return;
    try {
      child.stdin.write(JSON.stringify({ action: "cancel" }) + "\n", "utf8");
    } catch {
      /* */
    }
    if (job.cancelTimer) clearT(job.cancelTimer);
    job.cancelTimer = trackTimer(
      setTimeout(() => {
        killChild(child);
        job.cancelTimer = trackTimer(
          setTimeout(() => {
            if (job.state === "running" || job.state === "cancelling") {
              job.state = "failed";
              job.error = fromTimeout ? "timeout" : "cancel_timeout";
            }
          }, killWatchMs),
        );
      }, gracefulMs),
    );
  }

  function completeJob(job, error) {
    if (job.cancelTimer) clearT(job.cancelTimer);
    if (error) {
      job.state = "failed";
      job.error = error;
      if (job.final?.status === "cancelled" && error === "timeout") {
        job.state = "cancelled";
        job.error = "cancelled";
      }
    } else if (job.final) {
      job.state = job.final.status === "cancelled" ? "cancelled" : job.final.ok ? "completed" : "failed";
      job.error = job.final.error || null;
    } else if (job.plan) {
      job.state = job.plan.ok ? "completed" : "failed";
      job.error = job.plan.error || null;
    } else {
      job.state = "failed";
      job.error = "missing_result";
    }
    const blob = `${job.error || ""} ${job.final?.error || ""}`;
    if (
      /vlinker-port-not-unique|port-io|python_runtime_missing|sessions_source_missing|disconnect|pending-timeout|nrc78-timeout|prompt-timeout/i.test(
        blob,
      )
    ) {
      lastTransportDisconnect = true;
    } else if (job.state === "completed") {
      lastTransportDisconnect = false;
    }
    remember(job);
    gate?.release("session");
    if (job.kind !== "prepare") opts.onSessionEnded?.(job);
  }

  async function runPrepare(req, ownerId) {
    if (gate && !gate.tryAcquire("session")) return fail("busy");
    if (active) {
      gate?.release("session");
      return fail("busy");
    }
    if (!injected) {
      const missing = sessionsMissing(repoRoot);
      if (missing) {
        gate?.release("session");
        return fail("sessions_source_missing", { path: missing });
      }
    }
    const backend = toBackend({ ...req, action: "prepare" }, { deviceId: getLiveDeviceId?.() });
    const job = {
      id: newJobId(),
      ownerId,
      state: "running",
      events: [],
      latest: null,
      final: null,
      plan: null,
      error: null,
      child: null,
      kind: "prepare",
    };
    jobs.set(job.id, job);
    return new Promise((resolve) => {
      spawnSession(backend, job, (error) => {
        completeJob(job, error);
        if (error) {
          resolve(fail(error, { plan: job.plan?.plan || null }));
          return;
        }
        resolve({
          ok: !!job.plan?.ok,
          ...FLAGS,
          plan: job.plan?.plan ?? null,
          error: job.plan?.error || null,
        });
      });
    });
  }

  function start(req, ownerId) {
    const mode = req.mode || "simulation";
    if (mode === "live") {
      const blocked = liveBlocked(opts, envIn);
      if (blocked) return fail("live_not_enabled", { reason: blocked });
      const deviceId = typeof getLiveDeviceId === "function" ? getLiveDeviceId() : null;
      if (typeof getLiveDeviceId === "function" && !deviceId) return fail("device_not_selected");
    }
    if (gate && !gate.tryAcquire("session")) return fail("busy");
    if (active) {
      gate?.release("session");
      return fail("busy");
    }
    if (!injected) {
      const missing = sessionsMissing(repoRoot);
      if (missing) {
        gate?.release("session");
        return fail("sessions_source_missing", { path: missing });
      }
    }
    const liveId = typeof getLiveDeviceId === "function" ? getLiveDeviceId() : null;
    const backend = toBackend({ ...req, action: "start" }, { deviceId: liveId });
    const job = {
      id: newJobId(),
      ownerId,
      state: "running",
      events: [],
      latest: null,
      final: null,
      plan: null,
      error: null,
      child: null,
      kind: "run",
      resumed: Boolean(req.resumeRunId),
    };
    jobs.set(job.id, job);
    spawnSession(backend, job, (error) => completeJob(job, error));
    return { ok: true, jobId: job.id, ...FLAGS };
  }

  function owned(job, ownerId) {
    if (!job) return fail("unknown_job");
    if (job.ownerId !== ownerId) return fail("ownership_denied");
    return null;
  }

  function status(req, ownerId) {
    const job = jobs.get(req.jobId);
    const denied = owned(job, ownerId);
    if (denied) return denied;
    return snapshot(job);
  }

  function cancel(req, ownerId) {
    const job = jobs.get(req.jobId);
    const denied = owned(job, ownerId);
    if (denied) return denied;
    if (job.state !== "running") return { ok: true, jobId: job.id, state: job.state, ...FLAGS };
    job.state = "cancelling";
    sendCancel(job, false);
    return { ok: true, jobId: job.id, state: "cancelling", ...FLAGS };
  }

  function overview(ownerId) {
    const missing = sessionsMissing(repoRoot);
    let taskState = "idle";
    if (missing) taskState = "offline";
    else if (!isIdle()) taskState = "running";
    else if (lastTransportDisconnect) taskState = "offline";
    const out = { ok: true, taskState, ...FLAGS };
    for (let i = historyOrder.length - 1; i >= 0; i--) {
      const j = jobs.get(historyOrder[i]);
      if (j && j.ownerId === ownerId) {
        out.latestJobId = j.id;
        out.latestError = j.error || null;
        out.latestState = j.state;
        break;
      }
    }
    return out;
  }

  async function handle(request, { ownerId } = { ownerId: 0 }) {
    const checked = validateSessionRequest(request);
    if (checked) return checked;
    if (request.action === "overview") return overview(ownerId);
    if (request.action === "prepare") return runPrepare(request, ownerId);
    if (request.action === "start") return start(request, ownerId);
    if (request.action === "status") return status(request, ownerId);
    if (request.action === "cancel") return cancel(request, ownerId);
    return fail("invalid_action");
  }

  function cancelOwned(ownerId) {
    for (const job of jobs.values()) {
      if (job.ownerId === ownerId && (job.state === "running" || job.state === "cancelling")) {
        sendCancel(job, false);
      }
    }
  }

  function isIdle() {
    if (active) return false;
    for (const job of jobs.values()) {
      if (job.state === "running" || job.state === "cancelling") return false;
    }
    return true;
  }

  function shutdown() {
    for (const job of jobs.values()) {
      if (job.state === "running" || job.state === "cancelling") sendCancel(job, false);
    }
    const deadline = Date.now() + gracefulMs + killWatchMs + 200;
    return new Promise((resolve) => {
      const tick = () => {
        if (isIdle() || Date.now() >= deadline) {
          if (active?.child) killChild(active.child);
          for (const t of timers) clearTimeout(t);
          timers.clear();
          active = null;
          resolve();
          return;
        }
        setTimeout(tick, 40);
      };
      tick();
    });
  }

  return { handle, cancelOwned, shutdown, isIdle, _jobs: jobs };
}

export function backendReadyPath(repoRoot) {
  return path.join(repoRoot, ".local", "x431-re", "2026-09-27-preconnect", "backend", "ready.json");
}

export { toBackend };
