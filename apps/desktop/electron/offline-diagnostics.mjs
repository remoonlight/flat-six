/**
 * Bounded offline diagnostics bridge. Renderer may only pass a typed request.
 * Spawns `python -m scripts.diagnostics.workbench` with JSON stdin. No serial.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const OFFLINE_CHANNEL = "diagnostics:offline";
export const ACTIONS = Object.freeze([
  "summary",
  "plan",
  "variants",
  "records",
  "match",
  "decode",
  "preview",
  "replay",
]);

const ACTION_SET = new Set(ACTIONS);
const CATEGORIES = new Set(["identity", "measurement", "coding", "dtc", "routine"]);
const FORBIDDEN = new Set([
  "send",
  "path",
  "out",
  "outPath",
  "formula",
  "serialPort",
  "executable",
  "args",
  "inputFile",
  "variantsPath",
  "command",
]);

const MAX_JSON = 256 * 1024;
const MAX_STDOUT = 2 * 1024 * 1024;
const MAX_STDERR = 32 * 1024;
const MAX_LIMIT = 100;
const MAX_OFFSET = 1_000_000;
const MAX_SEARCH = 80;
const MAX_HEX = 4096;
const MAX_PROFILE = 240;
const MAX_IDENTITY = 16 * 1024;
const DEFAULT_TIMEOUT_MS = 120_000;

const FLAGS = {
  executionEnabled: false,
  liveVerified: false,
  writePayload: null,
};

export function fail(error, extra = {}) {
  return { ok: false, error, ...FLAGS, ...extra };
}

function isInt(n) {
  return typeof n === "number" && Number.isInteger(n);
}

export function validateRequest(req) {
  if (req == null || typeof req !== "object" || Array.isArray(req)) {
    return fail("malformed_request");
  }
  const bad = Object.keys(req).filter((k) => FORBIDDEN.has(k));
  if (bad.length) return fail("forbidden_field", { fields: bad });
  if (!ACTION_SET.has(req.action)) return fail("invalid_action", { action: req.action });
  if (req.generation != null && req.generation !== "981" && req.generation !== "982") {
    return fail("wrong_generation", { generation: req.generation });
  }
  if (req.ecuId != null && !isInt(req.ecuId)) return fail("malformed_ecu_id");
  if (req.profileId != null) {
    if (typeof req.profileId !== "string" || !req.profileId || req.profileId.length > MAX_PROFILE) {
      return fail("malformed_profile_id");
    }
  }
  if (req.category != null && !CATEGORIES.has(req.category)) {
    return fail("invalid_category", { category: req.category });
  }
  for (const [key, lo, hi] of [
    ["offset", 0, MAX_OFFSET],
    ["limit", 1, MAX_LIMIT],
    ["recordAt", 0, 2 ** 31 - 1],
  ]) {
    const val = req[key];
    if (val == null) continue;
    if (!isInt(val) || val < lo || val > hi) return fail("cap_limit", { field: key });
  }
  if (req.search != null && (typeof req.search !== "string" || req.search.length > MAX_SEARCH)) {
    return fail("cap_limit", { field: "search" });
  }
  if (req.dataHex != null && (typeof req.dataHex !== "string" || req.dataHex.length > MAX_HEX)) {
    return fail("cap_limit", { field: "dataHex" });
  }
  if (req.responseMode != null && req.responseMode !== "data" && req.responseMode !== "pdu") {
    return fail("invalid_response_mode", { responseMode: req.responseMode });
  }
  if (req.rawValue != null && !isInt(req.rawValue)) return fail("malformed_raw_value");
  if (req.identity != null) {
    if (typeof req.identity !== "object" || Array.isArray(req.identity)) {
      return fail("malformed_identity");
    }
    if (Buffer.byteLength(JSON.stringify(req.identity), "utf8") > MAX_IDENTITY) {
      return fail("cap_limit", { field: "identity" });
    }
  }
  const encoded = Buffer.byteLength(JSON.stringify(req), "utf8");
  if (encoded > MAX_JSON) return fail("request_too_large");
  return null;
}

export function resolvePython(env = process.env) {
  const pinned = (env.PORSCHE981_PYTHON || "").trim();
  if (pinned) return { exe: pinned, prefix: [] };
  if (process.platform === "win32") return { exe: "py", prefix: ["-3"] };
  return { exe: "python3", prefix: [] };
}

export function resolvePythonCandidates(env = process.env) {
  const pinned = (env.PORSCHE981_PYTHON || "").trim();
  if (pinned) return [{ exe: pinned, prefix: [] }];
  if (process.platform === "win32") {
    return [
      { exe: "py", prefix: ["-3"] },
      { exe: "python", prefix: [] },
      { exe: "python3", prefix: [] },
    ];
  }
  return [
    { exe: "python3", prefix: [] },
    { exe: "python", prefix: [] },
  ];
}

function workbenchMissing(repoRoot) {
  const p = path.join(repoRoot, "scripts", "diagnostics", "workbench.py");
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

/**
 * @param {object} request
 * @param {{
 *   repoRoot: string,
 *   variantsPath?: string,
 *   timeoutMs?: number,
 *   maxStdout?: number,
 *   spawnFn?: typeof spawn,
 *   env?: NodeJS.ProcessEnv,
 * }} opts
 */
export function runWorkbench(request, opts) {
  const checked = validateRequest(request);
  if (checked) return Promise.resolve(checked);

  const repoRoot = opts.repoRoot;
  const missing = workbenchMissing(repoRoot);
  if (missing) {
    return Promise.resolve(fail("workbench_source_missing", { path: missing }));
  }

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxStdout = opts.maxStdout ?? MAX_STDOUT;
  const spawnFn = opts.spawnFn ?? spawn;
  const env = { ...(opts.env || process.env), PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  if (opts.variantsPath) env.PORSCHE981_VARIANTS = opts.variantsPath;

  const payload = JSON.stringify(request);
  const candidates = resolvePythonCandidates(env);

  return new Promise((resolve) => {
    let idx = 0;
    let promiseSettled = false;
    const settle = (result) => {
      if (promiseSettled) return;
      promiseSettled = true;
      resolve(result);
    };

    const tryOne = () => {
      if (promiseSettled) return;
      if (idx >= candidates.length) {
        settle(
          fail("python_runtime_missing", {
            detail: "Set PORSCHE981_PYTHON to a CPython 3 interpreter",
            tried: candidates.map((c) => c.exe),
          }),
        );
        return;
      }
      const { exe, prefix } = candidates[idx++];
      const args = [...prefix, "-m", "scripts.diagnostics.workbench"];
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

      const attempt = { abandoned: false, finished: false, child };
      let stdout = Buffer.alloc(0);
      let stderr = "";
      let timedOut = false;
      let capped = false;
      let watchdog = null;

      const finishAttempt = (result) => {
        if (attempt.abandoned || attempt.finished || promiseSettled) return;
        attempt.finished = true;
        clearTimeout(timer);
        if (watchdog) clearTimeout(watchdog);
        killChild(child);
        settle(result);
      };

      const abandonAndFallback = () => {
        attempt.abandoned = true;
        clearTimeout(timer);
        if (watchdog) clearTimeout(watchdog);
        killChild(child);
        tryOne();
      };

      const timer = setTimeout(() => {
        timedOut = true;
        killChild(child);
        watchdog = setTimeout(() => finishAttempt(fail("timeout")), 250);
      }, timeoutMs);

      child.on("error", (err) => {
        if (attempt.abandoned || attempt.finished) return;
        if (err && (err.code === "ENOENT" || err.code === "EINVAL")) {
          abandonAndFallback();
          return;
        }
        finishAttempt(fail("spawn_failed", { detail: String(err && err.message) }));
      });

      child.stdout?.on("data", (chunk) => {
        if (attempt.abandoned || capped) return;
        const room = maxStdout + 1 - stdout.length;
        stdout = Buffer.concat([stdout, chunk.subarray(0, Math.max(0, room))]);
        if (stdout.length > maxStdout) {
          capped = true;
          killChild(child);
          watchdog = setTimeout(() => finishAttempt(fail("output_cap")), 250);
        }
      });
      child.stderr?.on("data", (chunk) => {
        if (attempt.abandoned) return;
        stderr += chunk.toString("utf8");
        if (stderr.length > MAX_STDERR) stderr = stderr.slice(0, MAX_STDERR);
      });

      child.stdin?.on("error", (err) => {
        if (attempt.abandoned || attempt.finished) return;
        if (err && err.code === "EPIPE") finishAttempt(fail("stdin_closed"));
      });

      child.on("close", (code) => {
        if (attempt.abandoned || attempt.finished) return;
        if (timedOut) {
          finishAttempt(fail("timeout"));
          return;
        }
        if (capped) {
          finishAttempt(fail("output_cap"));
          return;
        }
        const text = stdout.toString("utf8").trim();
        if (!text) {
          finishAttempt(
            fail("empty_output", {
              exitCode: code,
              stderr: stderr.slice(0, 400),
            }),
          );
          return;
        }
        let doc;
        try {
          doc = JSON.parse(text);
        } catch (e) {
          finishAttempt(fail("malformed_output", { detail: String(e), exitCode: code }));
          return;
        }
        if (typeof doc !== "object" || doc == null) {
          finishAttempt(fail("malformed_output", { exitCode: code }));
          return;
        }
        finishAttempt({
          ...FLAGS,
          ...doc,
          executionEnabled: false,
          liveVerified: false,
          writePayload: null,
        });
      });

      try {
        child.stdin.write(payload, "utf8");
        child.stdin.end();
      } catch {
        finishAttempt(fail("stdin_closed"));
      }
    };
    tryOne();
  });
}

export async function handleOfflineDiagnostics(request, opts) {
  return runWorkbench(request, opts);
}
