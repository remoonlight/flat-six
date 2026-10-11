import { validConnectionEvent } from "./manufacturer-connection.mjs";
import { validateManufacturerEvidence } from "./manufacturer-evidence.mjs";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fail, validateRequest, resolvePythonCandidates } from "./offline-diagnostics.mjs";
import { liveBlocked } from "./read-only-session.mjs";
import { validCanFrame } from "./internal-can-data.mjs";
import { validDiagnosticPdu } from "./diagnostic-pdu-data.mjs";

const FLAGS = { executionEnabled: false, liveVerified: false, writePayload: null };
const MAX_FRAME = 2 * 1024 * 1024;
const ACQUISITION_DURATION_MS = 10 * 60 * 1000;
const READ_PROFILES = new Map([
  ["9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", 1],
  ["9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1", 9],
]);
/** Typed manufacturer workers; physical reads share the selected-device lease. */
export function createManufacturerStream(opts) {
  const jobs = new Map();
  const now = opts.nowFn || (() => performance.now()), setT = opts.setTimeoutFn || setTimeout, clearT = opts.clearTimeoutFn || clearTimeout;
  let disposed = false;
  const snapshot = (job, cursor = 0, connectionCursor = 0) => {
    expire(job);
    let end = cursor, bytes = 0;
    // Bound each IPC page, not retention. A finished worker can still have
    // undisplayed pages; the consumer must drain them before showing completion.
    while (end < job.events.length && end < cursor + 32) {
      const size = job.eventBytes[end];
      if (end > cursor && bytes + size > 1024 * 1024) break;
      bytes += size; end++;
    }
    const connectionEnd = Math.min(job.connectionEvents.length, connectionCursor + 32);
    return { ok: true, ...FLAGS, mode: job.live ? "live" : "simulation", released: job.released, sessionId: job.id,
    state: job.state, events: job.events.slice(cursor, end), cursor: end, hasMore: end < job.events.length,
    connectionEvents: job.connectionEvents.slice(connectionCursor, connectionEnd), connectionCursor: connectionEnd,
    hasMoreConnectionEvents: connectionEnd < job.connectionEvents.length,
    stopReason: job.stopReason, result: job.result, error: job.error };
  };
  function expire(job) {
    if (!job.released && now() >= job.deadline) stop(job, "time-limit");
  }
  function stop(job, reason = "user-stopped") {
    if (!["preparing", "running"].includes(job.state)) return;
    job.stopReason = reason;
    clearT(job.durationTimer);
    if (job.state === "preparing") { job.state = "stopping"; return; }
    job.state = "stopping";
    try { job.child?.stdin.write('{"action":"stop"}\n'); } catch { abort(job, "stdin_closed"); }
    if (job.state !== "finished") job.stopTimer = setT(() => abort(job, "stream_stop_timeout"), opts.stopTimeoutMs ?? 20000);
  }
  function abort(job, error) {
    job.error = error;
    job.aborting = true;
    job.child?.kill();
    finish(job);
  }
  function finish(job) {
    clearT(job.timer); clearT(job.stopTimer); clearT(job.durationTimer);
    job.state = job.released ? "finished" : "closing";
  }
  function released(job) {
    if (job.released) return;
    job.released = true;
    job.finishedUtc = new Date().toISOString();
    if (job.lease) { opts.gate.release("session"); job.lease = false; }
    if (job.reserved) {
      job.reserved = false;
      const nativeReleaseVerified = job.native && job.nativeWorkerStarted
        ? !job.error && !job.aborting && job.result?.transport?.closed === true
          && Array.isArray(job.result.transport.closeErrors) && job.result.transport.closeErrors.length === 0
        : undefined;
      if (nativeReleaseVerified === false) job.error = "adapter_release_not_verified";
      job.resuming = Promise.resolve(opts.connection.resumeAfterSession({ nativeReleaseVerified }))
        .catch(() => { job.error ||= "monitor_resume_failed"; });
    }
    finish(job);
    job.resolveClosed?.();
    if (job.orphaned) jobs.delete(job.owner);
  }
  function launch(job, request) {
    job.profileId = request.profileId;
    job.parameterIds = request.parameterIds.slice();
    const env = { ...(opts.env || process.env), PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
    const candidates = resolvePythonCandidates(env);
    let index = 0;
    function attempt() {
      if (job.state === "finished") return;
      if (index >= candidates.length) { job.error = "python_runtime_missing"; released(job); return; }
      const { exe, prefix } = candidates[index++];
      let child;
      try {
        child = (opts.spawnFn || spawn)(exe, [...prefix, "-B", "-m", job.live ? "scripts.diagnostics.manufacturer_read" : "scripts.diagnostics.manufacturer_stream"],
          { cwd: opts.repoRoot, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false });
      } catch { attempt(); return; }
      job.child = child;
      job.nativeWorkerStarted = job.native;
      let buffer = Buffer.alloc(0), spawned = false, finalSeen = false, abandoned = false;
      const heartbeat = () => {
        expire(job);
        clearT(job.timer);
        job.timer = setT(() => abort(job, "stream_timeout"), opts.timeoutMs ?? 45000);
      };
      heartbeat();
      child.on("spawn", () => { spawned = true; });
      child.on("error", (error) => {
        if (abandoned || job.state === "finished") return;
        if (!spawned && ["ENOENT", "EINVAL"].includes(error.code)) {
          abandoned = true; job.nativeWorkerStarted = false; clearT(job.timer); attempt();
        } else abort(job, "spawn_failed");
      });
      child.stdin.on("error", () => { if (!abandoned && job.state !== "finished") abort(job, "stdin_closed"); });
      child.stderr.on("data", () => {}); // Consume, do not expose private source paths.
      child.stdout.on("data", (chunk) => {
        if (abandoned || job.state === "finished" || job.aborting) return;
        buffer = Buffer.concat([buffer, chunk]);
        try {
          let newline;
          while ((newline = buffer.indexOf(10)) >= 0) {
            if (newline > MAX_FRAME || finalSeen) throw new Error("stream_output_invalid");
            const frame = JSON.parse(buffer.subarray(0, newline).toString("utf8"));
            buffer = buffer.subarray(newline + 1);
            const doc = frame.result;
            if (!doc || typeof doc.executionEnabled !== "boolean" || (!job.live && doc.executionEnabled !== false)
              || doc.liveVerified !== false || doc.writePayload !== null)
              throw new Error("stream_source_invalid");
            heartbeat();
            if (frame.event === "diagnostic-pdu") {
              if (!job.live || doc.executionEnabled !== true || doc.simulation !== false
                || doc.sourceKind !== "independent-manufacturer-read" || !validDiagnosticPdu(doc.pdu)) throw new Error("stream_source_invalid");
              // The recorder owns explicit opt-in and writes before decoding can
              // reject a response. Never send raw bytes into ordinary value batches.
              opts.onDiagnosticPdu?.({ pdu: doc.pdu, simulation: false, profileId: request.profileId, runId: job.id });
            } else if (frame.event === "can-frame") {
              if (!job.live || doc.executionEnabled !== true || doc.simulation !== false
                || doc.sourceKind !== "independent-manufacturer-read" || !validCanFrame(doc.frame)) throw new Error("stream_source_invalid");
              opts.onCanFrame?.({ frame: doc.frame, simulation: false, profileId: request.profileId, runId: job.id });
            } else if (frame.event === "connection-state") {
              if (!validConnectionEvent(doc, job)) throw new Error("stream_connection_state_invalid");
              job.connectionEvents.push(doc);
              if (doc.phase === "qualified") job.qualifiedEpochs.add(doc.connectionEpoch);
            } else if (frame.event === "cycle") {
              if (typeof doc.ok !== "boolean" || doc.completedCycles !== Number(doc.ok)
                || (doc.ok && (!Array.isArray(doc.samples) || doc.samples.length !== request.parameterIds.length))
                || doc.sourceKind !== (job.live ? "independent-manufacturer-read" : "historical-fixture-rehearsal")
                || doc.simulation !== !job.live || (job.live ? typeof doc.vehicleDataCollected !== "boolean" : doc.vehicleDataCollected !== false)
                || !Array.isArray(doc.samples) || (job.live && (doc.executionEnabled !== true || (doc.samples.length && doc.vehicleDataCollected !== true)))
                || doc.samples.length > request.parameterIds.length
                || new Set(doc.samples.map(s => s.parameterId)).size !== doc.samples.length
                || !Array.isArray(doc.transactions) || !Number.isInteger(doc.cycle) || doc.cycle !== job.events.length + 1
                || doc.samples.some(s => s.synthetic !== !job.live || s.cycle !== doc.cycle
                  || s.connectionEpoch !== undefined && (!Number.isSafeInteger(s.connectionEpoch) || s.connectionEpoch < 1)
                  || !request.parameterIds.includes(s.parameterId) || !Number.isFinite(s.elapsedMs) || s.elapsedMs < 0))
                throw new Error("stream_source_invalid");
              const evidence = validateManufacturerEvidence(doc, { ...job.evidence, qualifiedEpochs: job.qualifiedEpochs });
              if (!evidence) throw new Error("stream_evidence_invalid");
              job.evidence = evidence;
              // Ordinary value batches never persist raw PDUs by default.
              if (job.live) doc.transactions = doc.transactions.map(({ pduHex: _raw, ...entry }) => entry);
              job.events.push(doc);
              job.eventBytes.push(Buffer.byteLength(JSON.stringify(doc)));
              job.sampleCount += doc.samples.length;
              job.completedCycles += doc.completedCycles;
              job.requestCount += doc.transactions.length;
              job.vehicleDataCollected ||= doc.vehicleDataCollected === true;
            } else if (frame.event === "finished") {
              if ((doc.connectionEventCount !== undefined || job.connectionEvents.length)
                && doc.connectionEventCount !== job.connectionEvents.length) throw new Error("stream_summary_mismatch");
              if ((doc.ok || job.events.length || job.live) && (doc.sourceKind !== (job.live ? "independent-manufacturer-read" : "historical-fixture-rehearsal")
                || doc.simulation !== !job.live || (job.live ? typeof doc.vehicleDataCollected !== "boolean" : doc.vehicleDataCollected !== false)))
                throw new Error("stream_source_invalid");
              if (job.events.length && (doc.completedCycles !== job.completedCycles
                || doc.requestCount !== job.requestCount
                || (job.live && (doc.executionEnabled !== true || doc.vehicleDataCollected !== job.vehicleDataCollected))))
                throw new Error("stream_summary_mismatch");
              if (job.live && doc.transport) { const { rawLog: _raw, ...transport } = doc.transport; doc.transport = transport; }
              finalSeen = true; job.result = doc;
            } else throw new Error("stream_output_invalid");
          }
          if (buffer.length > MAX_FRAME) throw new Error("stream_output_limit");
        } catch (error) { abort(job, error.message || "stream_output_invalid"); }
      });
      child.on("close", (code) => {
        if (abandoned) return;
        if (job.state !== "finished" && (!finalSeen || buffer.length || (code !== 0 && !(job.live && code === 1 && job.result?.ok === false)))) job.error ||= "stream_worker_failed";
        released(job);
      });
      try {
        child.stdin.write(JSON.stringify(request) + "\n");
        if (job.state === "stopping") child.stdin.write('{"action":"stop"}\n');
      } catch { abort(job, "stdin_closed"); }
    }
    attempt();
  }
  function newJob(owner, live = false) {
    const job = { id: randomUUID(), owner, live, state: live ? "preparing" : "running", events: [], eventBytes: [], connectionEvents: [],
      sampleCount: 0, completedCycles: 0, requestCount: 0, vehicleDataCollected: false,
      startedUtc: new Date().toISOString(), finishedUtc: "", saving: false, result: null, error: null, released: false,
      deadline: now() + ACQUISITION_DURATION_MS, stopReason: null, qualifiedEpochs: new Set([1]), evidence: null };
    job.closed = new Promise(resolve => { job.resolveClosed = resolve; });
    job.durationTimer = setT(() => stop(job, "time-limit"), ACQUISITION_DURATION_MS);
    job.durationTimer?.unref?.();
    jobs.set(owner, job); return job;
  }
  async function startRead(request, owner) {
    if (Object.keys(request).some(k => !["action", "profileId", "parameterIds", "continuous", "confirmedReadOnly", "x431Inactive"].includes(k))
      || typeof request.continuous !== "boolean" || request.confirmedReadOnly !== true || request.x431Inactive !== true)
      return fail("manufacturer-read-preconditions-required");
    const ecuId = READ_PROFILES.get(request.profileId);
    if (!ecuId || !request.parameterIds?.length) return fail("manufacturer-profile-unqualified");
    const invalid = validateRequest({ action: "ready-plan", generation: "981", ecuId, profileId: request.profileId, parameterIds: request.parameterIds });
    if (invalid) return invalid;
    const blocked = liveBlocked(opts, opts.env || process.env);
    if (blocked) return fail("live_not_enabled", { reason: blocked });
    const conn = opts.connection;
    if (!conn || !opts.gate) return fail("manufacturer-live-handoff-unavailable");
    const prior = jobs.get(owner);
    if (prior && (!prior.released || prior.saving)) return fail("stream_busy");
    if (conn.snapshot().purpose !== "diagnostic") return fail("diagnostic_purpose_required");
    const deviceId = conn.selectedId();
    if (!deviceId) return fail("device_not_selected");
    if (deviceId.startsWith("pt3g:")) return fail("pt3g-vehicle-transport-not-qualified");
    if (!/^(?:bt:[0-9A-F]{12}|usb:[0-9a-f]{64}|vnci:[0-9]{1,16})$/.test(deviceId)) return fail("device-id-invalid");
    const adapterModel = conn.snapshot().model;
    if (!conn.reserveDispatch()) return fail("busy");
    const job = newJob(owner, true); job.reserved = true; job.native = deviceId.startsWith("vnci:");
    try {
      const handoff = await conn.suspendForSession();
      if (handoff?.ok === false) {
        job.error = handoff.error || "port_release_pending"; job.reserved = false;
        conn.releaseDispatch(); released(job); return fail(job.error);
      }
      expire(job);
      if (disposed || job.state !== "preparing") { job.error = "cancelled"; released(job); return snapshot(job); }
      if (conn.selectedId() !== deviceId || conn.snapshot().model !== adapterModel || conn.snapshot().purpose !== "diagnostic") { job.error = "device_mismatch"; released(job); return fail(job.error); }
      if (!opts.gate.tryAcquire("session")) { job.error = "busy"; released(job); return fail(job.error); }
      job.lease = true; job.state = "running";
      launch(job, { action: "manufacturer-read", generation: "981", ecuId, profileId: request.profileId,
        parameterIds: request.parameterIds, deviceId, ...(adapterModel ? { adapterModel } : {}), confirmedReadOnly: true, x431Inactive: true,
        cycleCount: request.continuous ? null : 1 });
      return snapshot(job);
    } catch {
      job.error = "manufacturer-handoff-failed";
      if (job.child) abort(job, job.error); else released(job);
      return fail(job.error);
    }
  }
  async function cancelAndWait(targets) {
    for (const job of targets) stop(job);
    let timer;
    const ended = await Promise.race([Promise.all(targets.map(j => j.closed)).then(() => true),
      new Promise(resolve => { timer = setT(() => resolve(false), opts.closeWaitMs ?? 25000); })]);
    clearT(timer);
    if (ended) await Promise.all(targets.map(j => j.resuming));
    if (ended && targets.some(j => j.error === "adapter_release_not_verified")) return fail("adapter_release_not_verified");
    return ended ? { ok: true } : fail("port_release_pending");
  }
  async function save(job) {
    if (!job.released || job.saving) return fail("stream_busy");
    if ((!job.sampleCount && !job.connectionEvents.length) || typeof opts.saveRecording !== "function") return fail("manufacturer_recording_unavailable");
    job.saving = true;
    try {
      const recording = {
        kind: job.live ? "manufacturer-acquisition-read" : "manufacturer-acquisition-rehearsal",
        sourceKind: job.live ? "independent-manufacturer-read" : "historical-fixture-rehearsal",
        vehicleDataCollected: job.live && job.vehicleDataCollected, simulation: !job.live, liveVerified: false,
        profileId: job.profileId, parameterIds: job.parameterIds.slice(),
        startedUtc: job.startedUtc, finishedUtc: job.finishedUtc, sampleCount: job.sampleCount,
        cycleCount: job.events.length, retentionComplete: true,
        connectionEventCount: job.connectionEvents.length,
        connectionEvents: { *[Symbol.iterator]() { yield* job.connectionEvents; } },
        termination: { error: job.error || job.result?.error || null,
          stopReason: job.stopReason || job.result?.stopReason || (job.result?.ok ? "completed" : "failed"), finalReceived: job.result !== null },
        cycles: job.result ? [job.result] : [],
        samples: { *[Symbol.iterator]() { for (const event of job.events) yield* event.samples; } },
        cycleResults: { *[Symbol.iterator]() { for (const { samples: _samples, ...metadata } of job.events) yield metadata; } },
      };
      return { ...FLAGS, ...await opts.saveRecording(recording, job.owner) };
    } catch (error) { return fail(error?.code || "recording_save_failed"); }
    finally { job.saving = false; }
  }
  return {
    handle(request, owner) {
      if (!request || typeof request !== "object" || Array.isArray(request)) return fail("malformed_request");
      if (disposed) return fail("stream_shutdown");
      if (["save", "discard"].includes(request.action)) {
        if (Object.keys(request).some(key => !["action", "sessionId"].includes(key))) return fail("forbidden_field");
        const job = jobs.get(owner);
        if (!job || job.id !== request.sessionId) return fail("unknown_session");
        if (!job.released || job.saving) return fail("stream_busy");
        if (request.action === "discard") { jobs.delete(owner); return { ok: true, ...FLAGS }; }
        return save(job);
      }
      if (request.action === "start-read") return startRead(request, owner);
      if (request.action === "start") {
        if (Object.keys(request).some(k => !["action", "profileId", "parameterIds", "continuous"].includes(k))
          || typeof request.continuous !== "boolean") return fail("forbidden_field");
        const ecuId = READ_PROFILES.get(request.profileId);
        if (!ecuId) return fail("manufacturer-profile-unqualified");
        const payload = { action: "ready-acquire", generation: "981", ecuId,
          profileId: request.profileId, parameterIds: request.parameterIds, cycleCount: request.continuous ? 3 : 1 };
        const invalid = validateRequest(payload);
        if (invalid) return invalid;
        if (!payload.profileId || !payload.parameterIds?.length) return fail("invalid_parameter_selection");
        const prior = jobs.get(owner);
        if (prior && (prior.state !== "finished" || !prior.released || prior.saving)) return fail("stream_busy");
        const job = newJob(owner); launch(job, payload); return snapshot(job);
      }
      if (!["status", "stop"].includes(request.action) || Object.keys(request).some(k => !["action", "sessionId", "cursor", "connectionCursor"].includes(k)))
        return fail("invalid_action");
      const job = jobs.get(owner);
      if (!job || job.id !== request.sessionId) return fail("unknown_session");
      const cursor = request.cursor ?? 0;
      if (!Number.isInteger(cursor) || cursor < 0 || cursor > job.events.length) return fail("invalid_cursor");
      const connectionCursor = request.connectionCursor ?? 0;
      if (!Number.isInteger(connectionCursor) || connectionCursor < 0 || connectionCursor > job.connectionEvents.length) return fail("invalid_cursor");
      if (request.action === "stop") stop(job);
      return snapshot(job, cursor, connectionCursor);
    },
    cancelOwned(owner) { const job = jobs.get(owner); if (job) { job.orphaned = true; stop(job); if (job.released) jobs.delete(owner); } },
    cancelActiveAndWait() { return cancelAndWait([...jobs.values()].filter(j => j.live && !j.released)); },
    shutdown() { disposed = true; return cancelAndWait([...jobs.values()].filter(j => !j.released)); },
    isIdle() { return [...jobs.values()].every(j => j.released); },
  };
}
