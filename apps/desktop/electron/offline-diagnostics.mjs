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
  "coding-options",
  "replay",
  "ready-units", "ready-parameters", "ready-plan", "ready-replay", "ready-acquire",
  "catalog-units", "catalog-parameters", "catalog-plan", "catalog-replay", "catalog-coding-plan",
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
  if (req.action === "ready-acquire" && Object.keys(req).some((key) =>
    !["action", "generation", "ecuId", "profileId", "parameterIds"].includes(key))) return fail("forbidden_field");
  if (req.groupId != null && (typeof req.groupId !== "string" || !/^(?:[0-9A-F]{8}|ungrouped)$/.test(req.groupId))) return fail("invalid_group_id");
  if (req.parameterIds != null && (!Array.isArray(req.parameterIds) || req.parameterIds.length > 12
    || req.parameterIds.some((id) => typeof id !== "string" || !/^[a-f0-9]{64}$/.test(id))
    || new Set(req.parameterIds).size !== req.parameterIds.length)) return fail("invalid_parameter_selection");
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
  if (req.expectedReadRequestHex != null && (typeof req.expectedReadRequestHex !== "string"
    || !/^(?:21[0-9A-F]{2}|22[0-9A-F]{4})$/.test(req.expectedReadRequestHex))) return fail("coding_block_request_invalid");
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
  if (pinned) return { exe: pinned, prefix: ["-X", "utf8"] };
  if (process.platform === "win32") return { exe: "py", prefix: ["-3"] };
  return { exe: "python3", prefix: [] };
}

export function resolvePythonCandidates(env = process.env) {
  const pinned = (env.PORSCHE981_PYTHON || "").trim();
  // An isolated bundled Python ignores PYTHONUTF8/PYTHONIOENCODING.
  // Use the interpreter flag so Chinese JSON works on non-UTF8 Windows too.
  if (pinned) return [{ exe: pinned, prefix: ["-X", "utf8"] }];
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
    let cancelAttempt = null;
    const onAbort = () => {
      if (cancelAttempt) cancelAttempt();
      else settle(fail("cancelled"));
    };
    const settle = (result) => {
      if (promiseSettled) return;
      promiseSettled = true;
      opts.signal?.removeEventListener("abort", onAbort);
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
      cancelAttempt = () => finishAttempt(fail("cancelled"));

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
    opts.signal?.addEventListener("abort", onAbort, { once: true });
    if (opts.signal?.aborted) onAbort(); else tryOne();
  });
}

export async function handleOfflineDiagnostics(request, opts) {
  return runWorkbench(request, opts);
}

/** Cancellation is scoped to the originating window and a single preparation. */
export function createOfflineOperations(opts) {
  const operations = new Map();
  const validId = (id) => typeof id === "string" && /^[a-zA-Z0-9-]{1,80}$/.test(id);
  return {
    async run(request, ownerId, operationId) {
      if (operationId == null) return handleOfflineDiagnostics(request, opts);
      if (!validId(operationId) || !["ready-plan", "ready-replay", "ready-acquire", "catalog-plan", "catalog-replay"].includes(request?.action)) return fail("invalid_operation");
      const key = `${ownerId}:${operationId}`;
      if (operations.has(key)) return fail("operation_exists");
      const controller = new AbortController();
      operations.set(key, controller);
      try { return await handleOfflineDiagnostics(request, { ...opts, signal: controller.signal }); }
      finally { operations.delete(key); }
    },
    cancel(ownerId, operationId) {
      if (!validId(operationId)) return fail("invalid_operation");
      const controller = operations.get(`${ownerId}:${operationId}`);
      if (controller) controller.abort();
      return { ok: true, cancelled: Boolean(controller), ...FLAGS };
    },
    cancelOwned(ownerId) {
      for (const [key, controller] of operations) if (key.startsWith(`${ownerId}:`)) controller.abort();
    },
  };
}
