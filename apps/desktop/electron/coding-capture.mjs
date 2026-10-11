import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { resolvePython } from "./offline-diagnostics.mjs";

const fail = error => ({ ok: false, error, executionEnabled: false, baselineCreated: false });
const validId = value => typeof value === "string" && /^[A-Za-z0-9-]{1,80}$/.test(value);
export function startCodingCaptureWorker(request, { repoRoot, env = process.env, spawnFn = spawn, timeoutMs = 120000, closeWaitMs = 5000 }) {
  const python = resolvePython(env);
  const child = spawnFn(python.exe, [...python.prefix, "-m", "scripts.diagnostics.coding_capture_bridge"], {
    cwd: repoRoot, env: { ...env, PYTHONDONTWRITEBYTECODE: "1", PYTHONIOENCODING: "utf-8" },
    stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false,
  });
  let reason = null, terminal = false, stdout = Buffer.alloc(0), resolveResult, resolveClosed, watchdog;
  const result = new Promise(resolve => { resolveResult = resolve; });
  const closed = new Promise(resolve => { resolveClosed = resolve; });
  const timer = setTimeout(() => cancel("capture-timeout"), timeoutMs);
  function cancel(error = "capture-cancelled") {
    if (terminal || reason) return;
    reason = error; try { child.kill(); } catch {}
    if (terminal) return;
    watchdog = setTimeout(() => resolveResult(fail("capture-worker-close-timeout")), closeWaitMs);
  }
  child.stdout.on("data", chunk => {
    if (reason) return;
    stdout = Buffer.concat([stdout, chunk.subarray(0, Math.max(0, 2 * 1024 * 1024 + 1 - stdout.length))]);
    if (stdout.length > 2 * 1024 * 1024) { stdout = Buffer.alloc(0); cancel("capture-output-limit"); }
  });
  child.stderr.on("data", () => {});
  child.on("error", () => cancel("capture-worker-start-failed"));
  child.stdin.on("error", () => cancel("capture-worker-input-failed"));
  child.once("close", code => {
    terminal = true; clearTimeout(timer); clearTimeout(watchdog);
    let doc;
    try {
      doc = JSON.parse(stdout.toString("utf8"));
      if (code !== 0 || typeof doc.ok !== "boolean" || doc.executionEnabled !== false || doc.liveVerified !== false
        || doc.writePayload !== null || doc.baselineCreated !== false || doc.completeOriginalBackup !== false
        || doc.freshReadProven !== false) throw new Error();
    } catch { doc = fail("capture-worker-output-invalid"); }
    resolveResult(reason ? fail(reason) : doc); resolveClosed();
  });
  try { child.stdin.end(JSON.stringify(request), "utf8"); } catch { cancel("capture-worker-input-failed"); }
  return { result, closed, cancel, get terminal() { return terminal; } };
}

/** Native chooser paths and private artifacts stay owned by the originating window. */
export function createCodingCapture({ repoRoot, env = process.env, chooseOpen, chooseSave, isVehicleBusy = () => false,
  startWorker = startCodingCaptureWorker, temporaryRoot = os.tmpdir() }) {
  const states = new Map(), jobs = new Map(), operations = new Set(); let disposed = false;
  async function clean(state) {
    if (state.cleanup) return state.cleanup;
    const workspace = state.workspace; state.workspace = null;
    if (workspace) {
      const pending = fs.rm(workspace, { recursive: true, force: true }).finally(() => { if (state.cleanup === pending) state.cleanup = null; });
      state.cleanup = pending; return pending;
    }
  }
  function discard(owner, clientId) {
    const state = states.get(owner);
    if (!state || (clientId !== undefined && state.clientId !== clientId)) return Promise.resolve();
    state.disposed = true;
    const job = jobs.get(owner);
    if (job) job.cancel();
    const cleanup = async () => { await clean(state); if (states.get(owner) === state) states.delete(owner); };
    return job ? job.closed.then(cleanup) : cleanup();
  }
  async function worker(owner, state, request, epoch = state.cancelEpoch) {
    if (state.disposed || states.get(owner) !== state || epoch !== state.cancelEpoch) return fail("capture-cancelled");
    let job;
      try { job = startWorker({ ...request, kind: state.kind }, { repoRoot, env }); } catch { return fail("capture-worker-start-failed"); }
    jobs.set(owner, job);
    job.closed.then(() => { if (jobs.get(owner) === job) jobs.delete(owner); }).catch(() => {});
    const doc = await job.result;
    // A timeout notification is not actual close. Preserve the private files
    // and the busy owner until the same child emits close.
    if (!job.terminal) {
      job.closed.then(() => clean(state)).catch(() => {});
      return doc;
    }
    if (jobs.get(owner) === job) jobs.delete(owner);
    if (state.disposed || states.get(owner) !== state) return fail("capture-cancelled");
    return doc;
  }
  function get(owner, request, kind) {
    const state = states.get(owner);
    if (!state || state.disposed || state.clientId !== request.clientId || state.kind !== kind) throw new Error("capture-selection-expired");
    return state;
  }
  async function perform(owner, request, kind) {
    const shapes = { open: ["action", "clientId"], analyze: ["action", "clientId", "captureId", "generation", "profileId", "runId"],
      page: ["action", "clientId", "analysisId", "cursor"], save: ["action", "clientId", "analysisId"],
      cancel: ["action", "clientId"], discard: ["action", "clientId"], clear: ["action", "clientId"],
      scope: ["action", "clientId", "generation", "profileId", "sourceCatalogId", "ecuId"],
      units: ["action", "clientId", "generation", "sourceCatalogId", "systemId"] };
    const keys = request && shapes[request.action];
    if (kind === "identity" && ["scope", "analyze"].includes(request?.action)) keys.splice(keys.indexOf("profileId"), 1);
    if (request?.action === "page" && (kind !== "coding" || Object.hasOwn(request, "stream"))) keys.push("stream");
    const codingContext = kind === "coding" && (request?.action === "scope" || (request?.action === "analyze"
      && ["sourceCatalogId", "ecuId", "captureTargetProfileId"].some(key => Object.hasOwn(request, key))));
    if (codingContext && request.action === "analyze") keys.push("sourceCatalogId", "ecuId", "captureTargetProfileId");
    if (kind === "dtc" && request?.action === "analyze") keys.push("sourceCatalogId", "ecuId", "linkIndex", "captureTargetProfileId");
    if (kind === "identity" && request?.action === "analyze") keys.push("sourceCatalogId", "ecuId", "captureTargetProfileId");
    if (!keys || Object.keys(request).length !== keys.length || keys.some(key => !Object.hasOwn(request, key))
      || !validId(request.clientId)) return fail("capture-request-invalid");
    if (request.action === "units" && (kind !== "coding" || !["981", "982"].includes(request.generation)
      || !/^[a-f0-9]{64}$/.test(request.sourceCatalogId || "") || typeof request.systemId !== "string"
      || !/^[a-z0-9-]{1,80}$/.test(request.systemId))) return fail("capture-request-invalid");
    if (request.action === "scope" && !["coding", "dtc", "identity"].includes(kind)) return fail("capture-request-invalid");
    if ((["dtc", "identity"].includes(kind) || codingContext) && ["scope", "analyze"].includes(request.action) &&
      (!/^[a-f0-9]{64}$/.test(request.sourceCatalogId || "") || !Number.isSafeInteger(request.ecuId) || request.ecuId < 0
        || !["981", "982"].includes(request.generation) || (kind !== "identity" && (typeof request.profileId !== "string" || !request.profileId.length || request.profileId.length > 240))
        || (request.action === "analyze" && ((kind === "dtc" && (!Number.isSafeInteger(request.linkIndex) || request.linkIndex < 0))
          || typeof request.captureTargetProfileId !== "string" || !request.captureTargetProfileId.length || request.captureTargetProfileId.length > 240)))) return fail("capture-request-invalid");
    if (request.action === "discard") {
      const state = states.get(owner);
      if (state?.clientId === request.clientId && state.kind !== kind) return fail("capture-selection-expired");
      void discard(owner, request.clientId).catch(() => {}); return { ok: true };
    }
    if (disposed) return fail("capture-shutdown");
    if (request.action === "cancel") { const state = get(owner, request, kind); state.cancelEpoch++; jobs.get(owner)?.cancel(); return { ok: true, cancelling: !!jobs.get(owner), clientId: state.clientId }; }
    if (jobs.has(owner) || states.get(owner)?.dialog) return fail("capture-busy");
    if (request.action === "clear") {
      const state = get(owner, request, kind); await clean(state); state.analysisId = null; state.report = null; return { ok: true };
    }
    if (isVehicleBusy()) return fail("capture-finish-vehicle-first");
    if (["open", "scope", "units"].includes(request.action)) {
      let state = states.get(owner);
      if (!state || state.disposed || state.clientId !== request.clientId || state.kind !== kind) {
        if (state) await discard(owner);
        state = { clientId: request.clientId, kind, disposed: false, workspace: null, cancelEpoch: 0 }; states.set(owner, state);
      }
      const epoch = state.cancelEpoch;
      if (["scope", "units"].includes(request.action)) {
        const { clientId, ...scopeRequest } = request;
        return worker(owner, state, scopeRequest, epoch);
      }
      state.dialog = true;
      let file;
      try { file = await chooseOpen(owner); } finally { state.dialog = false; }
      if (state.disposed || states.get(owner) !== state || epoch !== state.cancelEpoch) return fail("capture-cancelled");
      if (!file) return { ok: true, canceled: true };
      if (isVehicleBusy()) return fail("capture-finish-vehicle-first");
      await clean(state); state.analysisId = null; state.report = null; state.capture = null;
      const doc = await worker(owner, state, { action: "inventory", capture: file }, epoch);
      if (!doc.ok) return doc;
      state.capture = { id: randomUUID(), file, ...doc };
      return { ...doc, captureId: state.capture.id, fileName: path.basename(file) };
    }
    const state = get(owner, request, kind);
    const epoch = state.cancelEpoch;
    if (request.action === "analyze") {
      if (!state.capture || request.captureId !== state.capture.id || !["981", "982"].includes(request.generation)
        || (kind !== "identity" && (typeof request.profileId !== "string" || request.profileId.length > 240))
        || !state.capture.sessions.some(s => s.targetProfileId === (["dtc", "identity"].includes(kind) || codingContext ? request.captureTargetProfileId : request.profileId) && s.runId === request.runId)) return fail("capture-session-mismatch");
      await clean(state); state.analysisId = null; state.report = null;
      state.workspace = await fs.mkdtemp(path.join(temporaryRoot, `flat-six-${kind}-capture-`));
      const output = path.join(state.workspace, "result");
      const doc = await worker(owner, state, { action: "analyze", capture: state.capture.file,
        captureSha256: state.capture.capture.sha256, output, generation: request.generation, runId: request.runId,
        ...(kind !== "identity" ? { profileId: request.profileId } : {}),
        ...(["dtc", "identity"].includes(kind) || codingContext ? { sourceCatalogId: request.sourceCatalogId, ecuId: request.ecuId, captureTargetProfileId: request.captureTargetProfileId } : {}),
        ...(kind === "dtc" ? { linkIndex: request.linkIndex } : {}) }, epoch);
      if (!doc.ok) { if (!jobs.has(owner)) await clean(state); return doc; }
      state.analysisId = randomUUID(); state.artifact = doc.artifact; state.output = output; state.report = doc.report;
      return { ok: true, analysisId: state.analysisId, report: state.report };
    }
    if (!state.analysisId || request.analysisId !== state.analysisId) return fail("capture-analysis-expired");
    const artifact = { output: state.output, artifact: state.artifact };
    if (request.action === "page") {
      if (!Number.isSafeInteger(request.cursor) || request.cursor < 0) return fail("capture-cursor-invalid");
      if (kind === "coding" && request.stream !== undefined && !["responses", "blocks"].includes(request.stream)) return fail("capture-stream-invalid");
      if (["measurement", "identity"].includes(kind) && !["responses", "values"].includes(request.stream)) return fail("capture-stream-invalid");
      if (kind === "dtc" && !["responses", "faults"].includes(request.stream)) return fail("capture-stream-invalid");
      return worker(owner, state, { action: "page", ...artifact, cursor: request.cursor, ...(request.stream !== undefined ? { stream: request.stream } : {}) });
    }
    if (request.action === "save") {
      state.dialog = true; let destination;
      try { destination = await chooseSave(owner); } finally { state.dialog = false; }
      if (state.disposed || states.get(owner) !== state || epoch !== state.cancelEpoch) return fail("capture-cancelled");
      if (!destination) return { ok: true, canceled: true };
      if (isVehicleBusy()) return fail("capture-finish-vehicle-first");
      const sourceStat = await fs.stat(state.capture.file);
      const destinationStat = await fs.stat(destination).catch(error => { if (error.code === "ENOENT") return null; throw error; });
      if (destinationStat && sourceStat.dev === destinationStat.dev && sourceStat.ino === destinationStat.ino)
        return fail("capture-export-overwrites-source");
      const doc = await worker(owner, state, { action: "export", ...artifact, destination }, epoch);
      return doc.ok ? { ...doc, filePath: destination } : doc;
    }
    return fail("capture-request-invalid");
  }
  return {
    async handle(request, owner, kind = "coding") {
      if (!["coding", "measurement", "dtc", "identity"].includes(kind)) return fail("capture-request-invalid");
      const interrupt = ["cancel", "discard"].includes(request?.action);
      if (!interrupt && operations.has(owner)) return fail("capture-busy");
      if (!interrupt) operations.add(owner);
      try { return await perform(owner, request, kind); } catch (error) { return fail(String(error.code || error.message)); }
      finally { if (!interrupt) operations.delete(owner); }
    },
    isIdle() { return !jobs.size && !operations.size; },
    cancelOwned(owner) { return discard(owner); },
    async shutdown() { disposed = true; const pending = [...states.keys()].map(owner => discard(owner));
      // Do not remove a still-live child's files to satisfy a shutdown timeout.
      await Promise.allSettled(pending.map(p => Promise.race([p, new Promise(resolve => { const timer = setTimeout(resolve, 6000); timer.unref?.(); })]))); },
  };
}
