/**
 * Bounded OBD connection IPC: persistent ATRV monitor + list/select/disconnect.
 * Renderer never supplies a COM path. Live sessions read selected identity from here.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { resolvePythonCandidates } from "./offline-diagnostics.mjs";
import { liveBlocked, validateSessionRequest } from "./read-only-session.mjs";
import {
  ATRV_INTERVAL_MS,
  MONITOR_GRACEFUL_MS,
  MONITOR_HANDSHAKE_MS,
  MONITOR_KILL_WAIT_MS,
  MONITOR_LIVENESS_MS,
  VOLTAGE_FRESH_MS,
  acceptMonitorReading,
  createTransportGate,
  formatHeaderVoltage,
  isTransportFault,
  nextRetryDelayMs,
  parseVoltageVolts,
  voltageFresh,
} from "../src/obd-connection-logic.mjs";

export const CONNECTION_CHANNEL = "diagnostics:connection";
export {
  ATRV_INTERVAL_MS,
  VOLTAGE_FRESH_MS,
  acceptMonitorReading,
  createTransportGate,
  formatHeaderVoltage,
  nextRetryDelayMs,
  parseVoltageVolts,
};

const ACTIONS = new Set(["list", "select", "connect", "voltage", "disconnect", "clear", "status"]);
const DEVICE_RE = /^bt:[0-9A-F]{12}$/;
const MODELS = new Set(["vLinker", "OBDLink MX+"]);
const MAX_JSON = 16 * 1024;
const LIST_TIMEOUT_MS = 20_000;
const MONITOR_LINE_CAP = 64 * 1024;
const FLAGS = Object.freeze({ executionEnabled: false, liveVerified: false, writePayload: null });

function isPlain(v) {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

export function fail(error, extra = {}) {
  return { ok: false, error, ...FLAGS, ...extra };
}

export function validateConnectionRequest(req) {
  if (!isPlain(req)) return fail("malformed_request");
  if (!ACTIONS.has(req.action)) return fail("invalid_action");
  const allowed =
    req.action === "select" || req.action === "connect" || req.action === "voltage"
      ? ["action", "deviceId", "model"]
      : ["action"];
  const extra = Object.keys(req).filter((k) => !allowed.includes(k));
  if (extra.length) return fail("forbidden_field", { fields: extra });
  if (req.deviceId != null && (typeof req.deviceId !== "string" || !DEVICE_RE.test(req.deviceId))) {
    return fail("malformed_device_id");
  }
  if (req.model != null && (typeof req.model !== "string" || !MODELS.has(req.model))) {
    return fail("malformed_model");
  }
  try {
    if (Buffer.byteLength(JSON.stringify(req), "utf8") > MAX_JSON) return fail("request_too_large");
  } catch {
    return fail("malformed_request");
  }
  return null;
}

function persistPath(repoRoot, env) {
  if (env.PORSCHE981_CONNECTION_STATE) return env.PORSCHE981_CONNECTION_STATE;
  return path.join(repoRoot, ".local", "obd-connection.json");
}

function loadState(file) {
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!isPlain(raw)) return { deviceId: null, model: null };
    const deviceId = typeof raw.deviceId === "string" && DEVICE_RE.test(raw.deviceId) ? raw.deviceId : null;
    const model = MODELS.has(raw.model) ? raw.model : null;
    return { deviceId, model };
  } catch {
    return { deviceId: null, model: null };
  }
}

function saveState(file, state) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ deviceId: state.deviceId, model: state.model || null }, null, 2), "utf8");
}

function spawnPython(opts, args, payload, timeoutMs) {
  const env = { ...opts.envIn, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  delete env.PORSCHE981_SERIAL;
  delete env.PORSCHE981_SERIAL_PORT;
  const candidates = opts.spawnFn ? [{ exe: "stub", prefix: [] }] : resolvePythonCandidates(env);
  const spawnFn = opts.spawnFn || spawn;
  return new Promise((resolve) => {
    const tryOne = (idx) => {
      if (idx >= candidates.length) {
        resolve({ ok: false, error: "python_runtime_missing", ...FLAGS });
        return;
      }
      const { exe, prefix } = candidates[idx];
      let child;
      try {
        child = spawnFn(exe, opts.spawnFn ? args : [...prefix, ...args], {
          cwd: opts.repoRoot,
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          shell: false,
        });
      } catch {
        tryOne(idx + 1);
        return;
      }
      let stdout = "";
      let stderr = "";
      let settled = false;
      let failure = null;
      let killWatchdog;
      opts.children?.add(child);
      const done = (doc) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        clearTimeout(killWatchdog);
        resolve(doc);
      };
      const terminate = (error) => {
        if (failure) return;
        failure = error;
        try {
          child.kill();
        } catch {
          /* */
        }
        killWatchdog = setTimeout(() => done(fail(error)), 1500);
      };
      const timer = setTimeout(() => terminate("timeout"), timeoutMs);
      child.on("error", (err) => {
        if (err && (err.code === "ENOENT" || err.code === "EINVAL") && !opts.spawnFn) {
          opts.children?.delete(child);
          settled = true;
          clearTimeout(timer);
          tryOne(idx + 1);
          return;
        }
        terminate("spawn_failed");
      });
      child.stdout?.on("data", (c) => {
        stdout += c.toString("utf8");
        if (stdout.length > 512 * 1024) terminate("output_cap");
      });
      child.stderr?.on("data", (c) => {
        stderr = (stderr + c.toString("utf8")).slice(-32 * 1024);
      });
      child.on("close", (code) => {
        opts.children?.delete(child);
        if (failure) {
          done(fail(failure));
          return;
        }
        const line = stdout.trim().split("\n").filter(Boolean).pop() || "";
        let doc;
        try {
          doc = JSON.parse(line);
        } catch {
          done(fail("malformed_output", { code, stderr: stderr.slice(0, 200) }));
          return;
        }
        if (!isPlain(doc)) {
          done(fail("protocol_error"));
          return;
        }
        done({ ...doc, ok: code === 0 && doc.ok === true, ...FLAGS, liveVerified: false, writePayload: null });
      });
      try {
        child.stdin.write(payload, "utf8");
      } catch {
        terminate("stdin_closed");
      }
    };
    tryOne(0);
  });
}

function acceptMonitorLine(doc, ctx) {
  return acceptMonitorReading(doc, ctx);
}

export function createObdConnectionManager(opts) {
  const envIn = opts.env || process.env;
  const file = persistPath(opts.repoRoot, envIn);
  const gate = opts.gate || createTransportGate();
  const listFn = opts.listFn;
  const nowFn = opts.now || Date.now;
  const freshMs = opts.freshMs ?? VOLTAGE_FRESH_MS;
  const setT = opts.setTimeoutFn || setTimeout;
  const clearT = opts.clearTimeoutFn || clearTimeout;
  const children = new Set();
  const spawnOpts = { ...opts, envIn, spawnFn: opts.spawnFn, children };

  let selected = loadState(file);
  let connected = false;
  let wantConnected = false;
  let lastList = { devices: [], errors: [] };
  let reading = null;
  let stopped = false;
  let monitorChild = null;
  let monitorToken = null;
  let ingestGen = 0;
  let linkState = "idle";
  let retryTimer = null;
  let retryFails = 0;
  let lastFatal = null;
  let dispatchReserved = false;
  let pausedForSession = false;
  let watchTimer = null;
  let opChain = Promise.resolve();
  let pendingMutations = 0;
  const handshakeMs = opts.handshakeMs ?? MONITOR_HANDSHAKE_MS;
  const livenessMs = opts.livenessMs ?? MONITOR_LIVENESS_MS;
  const gracefulMs = opts.gracefulMs ?? MONITOR_GRACEFUL_MS;
  const killWaitMs = opts.killWaitMs ?? MONITOR_KILL_WAIT_MS;

  function selectedId() {
    return selected.deviceId;
  }

  function clearReading() {
    reading = null;
  }

  function applyReading(volts, source, at) {
    if (source === "simulation" || source !== "atrv") {
      clearReading();
      return;
    }
    if (typeof volts !== "number" || !Number.isFinite(volts) || volts < 6 || volts > 20) {
      clearReading();
      return;
    }
    reading = { volts, source: "atrv", at: at ?? nowFn() };
  }

  function voltageView() {
    const now = nowFn();
    if (!reading || !voltageFresh(reading.at, now, freshMs)) {
      if (reading) reading = null;
      return { voltageVolts: null, voltageSource: null, voltageAt: null };
    }
    return { voltageVolts: reading.volts, voltageSource: reading.source, voltageAt: reading.at };
  }

  function snapshot(extra = {}) {
    const v = voltageView();
    const diagnostic = pausedForSession || gate.owner() === "session";
    const state = diagnostic ? "diagnostic" : linkState;
    return {
      ok: true,
      ...FLAGS,
      selectedDeviceId: selected.deviceId,
      model: selected.model,
      connected,
      linkState: state,
      pairingOk: lastList.devices.some((d) => d.id === selected.deviceId && d.paired),
      commOk: connected,
      devices: lastList.devices,
      listErrors: lastList.errors || [],
      voltageLabel: formatHeaderVoltage(v.voltageVolts),
      atrvIntervalMs: ATRV_INTERVAL_MS,
      voltageFreshMs: freshMs,
      ...v,
      ...extra,
    };
  }

  function persist() {
    saveState(file, selected);
  }

  function enqueue(fn) {
    pendingMutations += 1;
    const execute = async () => {
      try {
        if (stopped) return fail("connection_closed");
        if (sessionBusy()) return fail("busy");
        return await fn();
      } finally {
        pendingMutations -= 1;
      }
    };
    const run = opChain.then(execute, execute);
    opChain = run.then(
      () => {},
      () => {},
    );
    return run;
  }

  function clearWatch() {
    if (watchTimer) clearT(watchTimer);
    watchTimer = null;
  }

  function cancelRetry() {
    if (retryTimer) clearT(retryTimer);
    retryTimer = null;
  }

  function sessionBusy() {
    return gate.owner() === "session" || dispatchReserved;
  }

  function noteLiveAtrv(adapter, mode) {
    if (mode !== "live") return;
    const volts = parseVoltageVolts(adapter?.atrv);
    if (volts == null) return;
    applyReading(volts, "atrv");
  }

  async function listDevices() {
    try {
      const doc = listFn
        ? await listFn()
        : envIn.PORSCHE981_CONNECTION_FIXTURE
          ? JSON.parse(fs.readFileSync(envIn.PORSCHE981_CONNECTION_FIXTURE, "utf8"))
          : await spawnPython(spawnOpts, ["-m", "scripts.diagnostics.connection"], JSON.stringify({ action: "list" }) + "\n", LIST_TIMEOUT_MS);
      const devices = Array.isArray(doc.devices) ? doc.devices : [];
      lastList = { devices, errors: Array.isArray(doc.errors) ? doc.errors : doc.error ? [doc.error] : [] };
      if (!devices.some((d) => d.id === selected.deviceId && d.available) && !wantConnected) {
        connected = false;
        clearReading();
      }
      return snapshot({ ok: true });
    } catch {
      lastList = { devices: [], errors: ["list-failed"] };
      if (!wantConnected) clearReading();
      return fail("list_failed", snapshot({ ok: false }));
    }
  }

  function waitUntilClosed(child, maxMs) {
    return new Promise((resolve) => {
      if (!child || child.exitCode != null || child._exited) {
        resolve(true);
        return;
      }
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        clearT(timer);
        resolve(ok);
      };
      const onClose = () => finish(true);
      child.once("close", onClose);
      const timer = setT(() => {
        child.removeListener("close", onClose);
        finish(false);
      }, maxMs);
    });
  }

  async function stopMonitorProcess() {
    const child = monitorChild;
    if (!child) return { ok: true };
    ingestGen += 1;
    clearWatch();
    try {
      child.stdin.write(JSON.stringify({ action: "stop" }) + "\n", "utf8");
    } catch {
      /* */
    }
    if (await waitUntilClosed(child, gracefulMs)) return { ok: true };
    try {
      child.kill();
    } catch {
      /* */
    }
    if (await waitUntilClosed(child, killWaitMs)) return { ok: true };
    return { ok: false, error: "monitor_close_timeout" };
  }

  function armWatch(ms, reason) {
    clearWatch();
    watchTimer = setT(() => {
      watchTimer = null;
      if (stopped || !monitorChild) return;
      lastFatal = reason;
      connected = false;
      clearReading();
      try {
        monitorChild.kill();
      } catch {
        /* retain until close */
      }
    }, ms);
  }

  function scheduleRetry(error) {
    if (stopped || !wantConnected || pausedForSession) return;
    if (!isTransportFault(error)) {
      wantConnected = false;
      connected = false;
      linkState = "idle";
      clearReading();
      return;
    }
    connected = false;
    clearReading();
    linkState = "reconnecting";
    retryFails += 1;
    const delay = opts.retryDelayMs ? opts.retryDelayMs(retryFails) : nextRetryDelayMs(retryFails);
    cancelRetry();
    retryTimer = setT(() => {
      retryTimer = null;
      if (stopped || !wantConnected || pausedForSession) return;
      void startMonitor();
    }, delay);
  }

  function ingestLine(doc, gen, child) {
    if (stopped || gen !== ingestGen) return;
    const acc = acceptMonitorLine(doc, { deviceId: selected.deviceId, now: nowFn(), freshMs });
    if (acc.ignore) return;
    if (acc.reject) return;
    if (acc.fatal) {
      lastFatal = acc.error;
      connected = false;
      clearReading();
      if (!isTransportFault(acc.error)) {
        wantConnected = false;
        linkState = "idle";
      }
      try {
        child.kill();
      } catch {
        /* */
      }
      return;
    }
    applyReading(acc.volts, "atrv", acc.at);
    connected = true;
    linkState = "connected";
    retryFails = 0;
    lastFatal = null;
    armWatch(livenessMs, "timeout");
  }

  function bindMonitorChild(child, gen, token, id) {
    monitorChild = child;
    monitorToken = token;
    children.add(child);
    lastFatal = null;
    let buf = "";
    child.stdout?.on("data", (c) => {
      if (gen !== ingestGen) return;
      buf += c.toString("utf8");
      if (buf.length > MONITOR_LINE_CAP) {
        buf = "";
        lastFatal = "output_cap";
        connected = false;
        clearReading();
        try {
          child.kill();
        } catch {
          /* retain lock until close */
        }
        return;
      }
      const parts = buf.split("\n");
      buf = parts.pop() || "";
      for (const line of parts) {
        if (!line.trim()) continue;
        try {
          ingestLine(JSON.parse(line), gen, child);
        } catch {
          /* reject malformed */
        }
      }
    });
    child.on("error", (err) => {
      if (child._fallback || gen !== ingestGen) return;
      if (err && (err.code === "ENOENT" || err.code === "EINVAL")) return;
      lastFatal = "spawn_failed";
      connected = false;
      clearReading();
      try {
        child.kill();
      } catch {
        /* */
      }
    });
    child.on("close", () => {
      child._exited = true;
      children.delete(child);
      if (child._fallback) return;
      if (monitorChild === child) {
        monitorChild = null;
        monitorToken = null;
      }
      clearWatch();
      if (gate.owner() === token) gate.release(token);
      if (stopped || pausedForSession) return;
      if (gen !== ingestGen) return;
      if (wantConnected) scheduleRetry(lastFatal || "port-io");
    });
    try {
      child.stdin.write(JSON.stringify({ action: "monitor", deviceId: id }) + "\n", "utf8");
    } catch {
      lastFatal = "stdin_closed";
      try {
        child.kill();
      } catch {
        /* */
      }
    }
    armWatch(handshakeMs, "timeout");
  }

  async function startMonitor() {
    if (stopped || !wantConnected || pausedForSession || monitorChild) return;
    const id = selected.deviceId;
    if (!id) return;
    if (gate.owner() && !String(gate.owner()).startsWith("monitor")) return;
    const gen = ++ingestGen;
    const token = `monitor:${gen}`;
    if (!gate.tryAcquire(token)) return;
    linkState = "connecting";
    const env = { ...envIn, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
    delete env.PORSCHE981_SERIAL;
    delete env.PORSCHE981_SERIAL_PORT;
    const spawnFn = opts.monitorSpawnFn || opts.spawnFn || spawn;
    const injected = Boolean(opts.monitorSpawnFn || opts.spawnFn);
    const candidates = opts.pythonCandidates || (injected ? [{ exe: "stub", prefix: [] }] : resolvePythonCandidates(env));

    const tryOne = (idx) => {
      if (stopped || gen !== ingestGen) {
        if (gate.owner() === token) gate.release(token);
        return;
      }
      if (idx >= candidates.length) {
        if (gate.owner() === token) gate.release(token);
        lastFatal = "python_runtime_missing";
        scheduleRetry("python_runtime_missing");
        return;
      }
      const { exe, prefix } = candidates[idx];
      let child;
      try {
        child = spawnFn(exe, injected ? ["-m", "scripts.diagnostics.connection"] : [...prefix, "-m", "scripts.diagnostics.connection"], {
          cwd: opts.repoRoot,
          env,
          stdio: ["pipe", "pipe", "pipe"],
          windowsHide: true,
          shell: false,
        });
      } catch {
        tryOne(idx + 1);
        return;
      }
      child.once("error", (err) => {
        if (!(err && (err.code === "ENOENT" || err.code === "EINVAL"))) return;
        if (idx + 1 >= candidates.length) return;
        child._fallback = true;
        if (monitorChild === child) {
          children.delete(child);
          monitorChild = null;
          monitorToken = null;
          clearWatch();
        }
        tryOne(idx + 1);
      });
      bindMonitorChild(child, gen, token, id);
    };
    tryOne(0);
  }

  async function beginConnect() {
    if (stopped) return fail("connection_closed");
    if (sessionBusy()) return fail("busy");
    const id = selected.deviceId;
    if (!id) return fail("device_not_selected");
    wantConnected = true;
    retryFails = 0;
    cancelRetry();
    if (!monitorChild) await startMonitor();
    return snapshot({ ok: true });
  }

  function reserveDispatch() {
    if (stopped) return false;
    if (pendingMutations || dispatchReserved || gate.owner() === "session") return false;
    dispatchReserved = true;
    return true;
  }

  function releaseDispatch() {
    dispatchReserved = false;
  }

  async function suspendForSession() {
    pausedForSession = true;
    cancelRetry();
    connected = false;
    clearReading();
    linkState = "diagnostic";
    const stoppedMon = await stopMonitorProcess();
    if (!stoppedMon.ok) {
      pausedForSession = false;
      return fail(stoppedMon.error || "monitor_close_timeout", snapshot({ ok: false }));
    }
    return { ok: true, ...FLAGS };
  }

  async function resumeAfterSession() {
    pausedForSession = false;
    releaseDispatch();
    if (stopped || !wantConnected) {
      if (!wantConnected) linkState = "idle";
      return;
    }
    await startMonitor();
  }

  async function userStop(clearSelection) {
    wantConnected = false;
    cancelRetry();
    connected = false;
    clearReading();
    linkState = "idle";
    const result = await stopMonitorProcess();
    if (!result.ok) return fail(result.error, snapshot({ ok: false }));
    if (clearSelection) {
      selected = { deviceId: null, model: null };
      persist();
    }
    return snapshot();
  }

  async function doSelect(request) {
    if (!request.deviceId) return fail("malformed_device_id");
    const hit = lastList.devices.find((d) => d.id === request.deviceId);
    if (!hit) return fail("unknown_device");
    if (!hit.available) {
      cancelRetry();
      wantConnected = false;
      connected = false;
      clearReading();
      const result = await stopMonitorProcess();
      if (!result.ok) return fail(result.error, snapshot({ ok: false }));
      selected = { deviceId: request.deviceId, model: request.model || selected.model };
      persist();
      return fail("device_port_unavailable", snapshot({ ok: false }));
    }
    if (request.deviceId !== selected.deviceId) {
      cancelRetry();
      wantConnected = false;
      connected = false;
      clearReading();
      const result = await stopMonitorProcess();
      if (!result.ok) return fail(result.error, snapshot({ ok: false }));
    }
    selected = { deviceId: request.deviceId, model: request.model || (hit.brand !== "unresolved" ? hit.brand : selected.model) || null };
    persist();
    return snapshot();
  }

  async function handle(request) {
    const bad = validateConnectionRequest(request);
    if (bad) return bad;
    if (stopped) return fail("connection_closed");
    if (request.action === "list") return listDevices();
    if (request.action === "status") return snapshot();
    if (request.action === "voltage") {
      if (!connected) return fail("not_connected", snapshot({ ok: false }));
      return snapshot();
    }
    if (sessionBusy() && request.action !== "status") return fail("busy");
    if (request.action === "select") return enqueue(() => doSelect(request));
    if (request.action === "clear") return enqueue(() => userStop(true));
    if (request.action === "disconnect") return enqueue(() => userStop(false));
    const id = selected.deviceId;
    if (!id) return fail("device_not_selected");
    if (request.deviceId && request.deviceId !== id) return fail("device_mismatch");
    if (request.action === "connect") return enqueue(() => {
      if (request.deviceId && request.deviceId !== selected.deviceId) return fail("device_mismatch");
      return beginConnect();
    });
    return fail("invalid_action");
  }

  async function shutdown() {
    stopped = true;
    wantConnected = false;
    cancelRetry();
    clearWatch();
    connected = false;
    clearReading();
    linkState = "idle";
    await stopMonitorProcess();
    ingestGen += 1;
  }

  return {
    handle,
    selectedId,
    voltageView,
    snapshot,
    noteLiveAtrv,
    shutdown,
    gate,
    reserveDispatch,
    releaseDispatch,
    suspendForSession,
    resumeAfterSession,
    _state: () => ({ selected, connected, reading, wantConnected, linkState, pausedForSession, dispatchReserved, monitorToken }),
  };
}

export function attachSessionHandoff(conn, sessionMgr, hooks = {}) {
  const orig = sessionMgr.handle.bind(sessionMgr);
  sessionMgr.handle = async (request, ctx) => {
    if (request?.action !== "prepare" && request?.action !== "start") return orig(request, ctx);
    const invalid = validateSessionRequest(request);
    if (invalid) return invalid;
    if (request.action === "start" && (request.mode || "simulation") === "live") {
      const blocked = liveBlocked({ spawnFn: hooks.spawnFn, allowInjectedLive: hooks.allowInjectedLive }, hooks.env || process.env);
      if (blocked) return fail("live_not_enabled", { reason: blocked });
      if (!conn.selectedId()) return fail("device_not_selected");
    }
    if (!conn.reserveDispatch()) return fail("busy");
    try {
      const sus = await conn.suspendForSession();
      if (sus && sus.ok === false) {
        conn.releaseDispatch();
        return sus;
      }
      const out = await orig(request, ctx);
      if (request.action === "prepare") await conn.resumeAfterSession();
      else if (!out?.ok && !out?.jobId) await conn.resumeAfterSession();
      return out;
    } catch (err) {
      await conn.resumeAfterSession();
      throw err;
    }
  };
  return sessionMgr;
}

