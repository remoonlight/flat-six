import { validConnectionEvent } from "./manufacturer-connection.mjs";
import { validateManufacturerEvidence, validManufacturerDtcResult, validManufacturerIdentityInfo } from "./manufacturer-evidence.mjs";
import { spawn } from "node:child_process";
import { randomUUID, createHash } from "node:crypto";
import { fail, validateRequest, resolvePythonCandidates } from "./offline-diagnostics.mjs";
import { liveBlocked } from "./read-only-session.mjs";
import { validCanFrame } from "./internal-can-data.mjs";
import { validDiagnosticPdu } from "./diagnostic-pdu-data.mjs";
import { validObdDeviceId } from "../src/obd-connection-logic.mjs";
import { createPlotClock } from "./capture-clock.mjs";
import { validateCompleteCodingRead, validateCodingVehicleAnchor } from './coding-read-evidence.mjs';
import { UDS_CODING_CONTEXTS } from './uds-coding-sources.mjs';

const FLAGS = { executionEnabled: false, liveVerified: false, writePayload: null };
const MAX_FRAME = 2 * 1024 * 1024;
const ACQUISITION_DURATION_MS = 10 * 60 * 1000;
import { READ_PROFILES, LIVE_READ_PROFILES, liveReadTargets, profileFitsGeneration, faultReadTargets, faultProfileFitsGeneration } from "../src/obd/manufacturer-profiles.mjs";
/** Typed manufacturer workers; physical reads share the selected-device lease. */
export function createManufacturerStream(opts) {
  const jobs = new Map();
  const now = opts.nowFn || (() => performance.now()), setT = opts.setTimeoutFn || setTimeout, clearT = opts.clearTimeoutFn || clearTimeout;
  let disposed = false;
  const releaseComplete = job => job.released && (!job.identifying || job.resumeDone === true);
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
    const ready = releaseComplete(job);
    return { ok: true, ...FLAGS, mode: job.live ? "live" : "simulation", released: ready, releaseVerified: ready && job.releaseVerified === true, sessionId: job.id,
    state: job.identifying && job.released && !ready ? "closing" : job.state, events: job.events.slice(cursor, end), cursor: end, hasMore: end < job.events.length,
    connectionEvents: job.connectionEvents.slice(connectionCursor, connectionEnd), connectionCursor: connectionEnd,
    hasMoreConnectionEvents: connectionEnd < job.connectionEvents.length,
    unavailableParameters: [...(job.evidence?.unavailableParameters?.values() || [])],
    invalidParameters: [...(job.evidence?.invalidParameters?.values() || [])],
    awaitingRetry: job.awaitingRetry === true,
    plotElapsedMs: job.plotClock.read(), stopReason: job.stopReason, result: job.result, error: job.error };
  };
  function expire(job) {
    if (!job.released && now() >= job.deadline) stop(job, "time-limit");
  }
  function armDeadline(job) {
    if (job.released || !["preparing", "running"].includes(job.state)) return;
    clearT(job.durationTimer);
    job.durationTimer = setT(() => {
      expire(job);
      // Timers can fire before the acquisition clock reaches its deadline.
      // Keep the same deadline and rearm only while this batch is active.
      armDeadline(job);
    }, Math.max(1, Math.ceil(job.deadline - now())));
    job.durationTimer?.unref?.();
  }
  function stop(job, reason = "user-stopped") {
    if (!["preparing", "running"].includes(job.state)) return;
    job.plotClock.freeze(reason === "time-limit" ? job.deadline : now());
    job.stopReason = reason;
    job.awaitingRetry = false; job.retryRequested = false;
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
    job.plotClock.freeze();
    clearT(job.timer); clearT(job.stopTimer); clearT(job.durationTimer);
    job.state = job.released ? "finished" : "closing";
  }
  function released(job) {
    if (job.released) return;
    job.released = true;
    job.awaitingRetry = false; job.retryRequested = false;
    job.finishedUtc = new Date().toISOString();
    if (job.lease) { opts.gate.release("session"); job.lease = false; }
    if (job.reserved) {
      job.reserved = false;
      // Serial/TCP workers also need an explicit close report. Process exit or
      // a kill request alone cannot establish that cleanup and release worked.
      // A poisoned ELM link may close without ATPC; that known transport fault
      // is separate from a failed adapter cleanup/port close.
      const closeErrors = job.result?.transport?.closeErrors;
      const openAttempted = job.result?.transport?.openAttempted;
      const neverOpened = openAttempted === false && job.childCloseCode === 1
        && job.result?.ok === false && job.result.executionEnabled === false
        && job.result.vehicleDataCollected === false && job.result.requestCount === 0
        && job.result.completedCycles === 0 && job.result.transport.closed === false
        && Array.isArray(closeErrors) && closeErrors.length === 0
        && !job.workerActivitySeen && job.events.length === 0 && job.connectionEvents.length === 0;
      const nativeReleaseVerified = job.live && job.workerStarted
        ? !job.error && !job.aborting && [0, 1].includes(job.childCloseCode)
          && (neverOpened || openAttempted !== false && job.result?.transport?.closed === true
            && Array.isArray(closeErrors)
            && closeErrors.every(error => !job.native && typeof error === "string" && error.startsWith("poisoned:")))
        : undefined;
      if (nativeReleaseVerified === false) job.error = "adapter_release_not_verified";
      job.releaseVerified = nativeReleaseVerified === true;
      job.resuming = Promise.resolve(opts.connection.resumeAfterSession({ nativeReleaseVerified }))
        .then(result => { if (job.identifying && result?.ok === false) job.error ||= result.error || "monitor_resume_failed"; })
        .catch(() => { job.error ||= "monitor_resume_failed"; })
        .finally(() => { job.resumeDone = true; if (job.orphaned && jobs.get(job.owner) === job) jobs.delete(job.owner); });
    } else job.resumeDone = true;
    finish(job);
    job.resolveClosed?.();
    if (job.orphaned && releaseComplete(job)) jobs.delete(job.owner);
  }
  function launch(job, request) {
    job.profileId = request.profileId;
    job.parameterIds = job.identifying ? [] : request.parameterIds.slice();
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
      job.workerStarted = true;
      let buffer = Buffer.alloc(0), spawned = false, finalSeen = false, abandoned = false;
      const heartbeat = () => {
        expire(job);
        clearT(job.timer);
        if (!job.awaitingRetry) job.timer = setT(() => abort(job, "stream_timeout"), opts.timeoutMs ?? 45000);
      };
      heartbeat();
      child.on("spawn", () => { spawned = true; });
      child.on("error", (error) => {
        if (abandoned || job.state === "finished") return;
        if (!spawned && ["ENOENT", "EINVAL"].includes(error.code)) {
          abandoned = true; job.workerStarted = false; clearT(job.timer); attempt();
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
            if (frame.event !== "finished") job.workerActivitySeen = true;
            heartbeat();
            if (frame.event === "diagnostic-pdu") {
              if (!job.live || doc.executionEnabled !== true || doc.simulation !== false
                || doc.sourceKind !== "independent-manufacturer-read" || !validDiagnosticPdu(doc.pdu)) throw new Error("stream_source_invalid");
              // The recorder owns explicit opt-in and writes before decoding can
              // reject a response. Never send raw bytes into ordinary value batches.
              // Discovery has not matched a version yet. Preserve explicit raw
              // captures without labeling them as the nominal directory version.
              if ((job.dtcReading || job.codingReading) && !doc.pdu.duringCleanup && typeof doc.pdu.requestHex==='string') {
                job.identityPdus ||= new Map();
                job.identityPdus.set(doc.pdu.requestHex,createHash('sha256').update(Buffer.from(doc.pdu.pduHex,'hex')).digest('hex'));
                if (job.codingReading) {
                  const rows = job.codingPdus.get(doc.pdu.requestHex) || [];
                  if (rows.length >= 16 || job.codingPdus.size >= 300) throw new Error('stream_coding_evidence_limit');
                  rows.push(doc.pdu.pduHex); job.codingPdus.set(doc.pdu.requestHex, rows);
                }
              }
              opts.onDiagnosticPdu?.({ pdu: doc.pdu, simulation: false, profileId: job.identifying ? null : request.profileId, runId: job.id });
            } else if (frame.event === 'coding-vehicle-anchor') {
              if (!job.live || !job.codingReading || job.codingAnchor) throw new Error('stream_coding_anchor_invalid');
              job.codingAnchor = validateCodingVehicleAnchor(doc, job.codingPdus, job.generation, job.expectedVehicleIdentitySha256);
              // Same DID numbers on two ECUs are independent observations.
              // Keep the verified gateway token private; target evidence starts here.
              job.codingPdus = new Map();
              job.identityPdus = new Map();
            } else if (frame.event === "can-frame") {
              if (!job.live || doc.executionEnabled !== true || doc.simulation !== false
                || doc.sourceKind !== "independent-manufacturer-read" || !validCanFrame(doc.frame)) throw new Error("stream_source_invalid");
              opts.onCanFrame?.({ frame: doc.frame, simulation: false, profileId: job.identifying ? null : request.profileId, runId: job.id });
            } else if (frame.event === "connection-state") {
              if (!validConnectionEvent(doc, job)) throw new Error("stream_connection_state_invalid");
              job.connectionEvents.push(doc);
              job.awaitingRetry = doc.phase === "awaiting-retry";
              if (job.awaitingRetry) clearT(job.timer);
              if (doc.phase === "retrying") job.retryRequested = false;
              if (!job.awaitingRetry) heartbeat();
              if (doc.phase === "qualified") job.qualifiedEpochs.add(doc.connectionEpoch);
            } else if (frame.event === "cycle") {
              if (job.identifying) throw new Error("stream_identity_values_forbidden");
              if (typeof doc.ok !== "boolean" || doc.completedCycles !== Number(doc.ok)
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
              if ([...evidence.unavailableParameters.keys(), ...evidence.invalidParameters.keys()].some(id => !request.parameterIds.includes(id))
                || doc.ok && doc.samples.length + evidence.unavailableParameters.size + (doc.invalidParameters?.length || 0) !== request.parameterIds.length)
                throw new Error("stream_evidence_invalid");
              job.evidence = evidence;
              for (const sample of doc.samples) job.plotClock.observe(sample.elapsedMs);
              // Ordinary value batches never persist raw PDUs by default.
              if (job.live) doc.transactions = doc.transactions.map(({ pduHex: _raw, ...entry }) => entry);
              job.events.push(doc);
              job.eventBytes.push(Buffer.byteLength(JSON.stringify(doc)));
              job.sampleCount += doc.samples.length;
              job.completedCycles += doc.completedCycles;
              job.requestCount += doc.transactions.length;
              job.vehicleDataCollected ||= doc.vehicleDataCollected === true;
            } else if (frame.event === "finished") {
              if (job.identifying && (doc.completedCycles !== 0 || doc.requestCount !== 0 || (job.dtcReading || job.codingReading ? typeof doc.vehicleDataCollected !== "boolean" : doc.vehicleDataCollected !== false)
                || doc.samples !== undefined && (!Array.isArray(doc.samples) || doc.samples.length !== 0)
                || doc.transactions !== undefined && (!Array.isArray(doc.transactions) || doc.transactions.length !== 0)
                || doc.ok && (doc.identityOnly !== !(job.dtcReading || job.codingReading) || !(job.dtcReading || job.vehicleIdentifying ? faultReadTargets : liveReadTargets)(doc.identifiedProfileId).includes(job.identifyEcuId)
                  || doc.readContract?.profileId !== doc.identifiedProfileId || doc.readContract?.ecuId !== job.identifyEcuId
                  || doc.readContract?.generation !== job.generation || doc.transport?.qualification?.generation !== job.generation
                  || !(job.dtcReading || job.vehicleIdentifying ? faultProfileFitsGeneration : profileFitsGeneration)(doc.identifiedProfileId,job.generation) || doc.transport?.qualification?.observedProfileMatch !== true
                  || !Array.isArray(doc.readContract?.groups) || doc.readContract.groups.length !== 0
                  || doc.transport?.qualification?.freshRead !== true
                  || doc.transport?.qualification?.variantLink?.profile_id !== doc.identifiedProfileId)))
                throw new Error("stream_identity_invalid");
              if (job.dtcReading && (doc.ok || doc.identityInfo!==undefined)
                && !validManufacturerIdentityInfo(doc,job.identityPdus || new Map())) throw new Error("stream_identity_details_invalid");
              if (job.dtcReading && doc.ok && (!validManufacturerDtcResult(doc)
                || job.expectedVehicleIdentitySha256 && doc.vehicleIdentitySha256 !== job.expectedVehicleIdentitySha256)) throw new Error("stream_dtc_invalid");
              if (job.codingReading && doc.ok) {
                job.codingIdentity = validateCompleteCodingRead(doc, job.codingPdus, job.codingAnchor);
                if (doc.vehicleIdentitySha256 !== job.expectedVehicleIdentitySha256) throw new Error('stream_coding_vehicle_changed');
              }
              if (job.vehicleIdentifying && doc.ok && (!['981','982'].includes(doc.vehicleGeneration)
                || !/^[a-f0-9]{64}$/.test(doc.vehicleIdentitySha256 || '')))
                throw new Error("stream_identity_invalid");
              if (doc.invalidParameters !== undefined || job.evidence?.invalidParameters?.size) {
                const expected = [...(job.evidence?.invalidParameters?.values() || [])];
                if (JSON.stringify(doc.invalidParameters) !== JSON.stringify(expected)) throw new Error("stream_summary_mismatch");
              }
              if (doc.unavailableParameters !== undefined || job.evidence?.unavailableParameters?.size) {
                const expected = [...(job.evidence?.unavailableParameters?.values() || [])];
                if (JSON.stringify(doc.unavailableParameters) !== JSON.stringify(expected)) throw new Error("stream_summary_mismatch");
              }
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
        job.childCloseCode = code;
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
    const job = { id: randomUUID(), owner, live, requireManualRetry: live, state: live ? "preparing" : "running", events: [], eventBytes: [], connectionEvents: [],
      sampleCount: 0, completedCycles: 0, requestCount: 0, vehicleDataCollected: false, workerActivitySeen: false,
      startedUtc: new Date().toISOString(), finishedUtc: "", saving: false, result: null, error: null, released: false,
      deadline: now() + ACQUISITION_DURATION_MS, plotClock: createPlotClock({ now }), stopReason: null, qualifiedEpochs: new Set([1]), evidence: null };
    job.closed = new Promise(resolve => { job.resolveClosed = resolve; });
    armDeadline(job);
    jobs.set(owner, job); return job;
  }
  async function startRead(request, owner, identifying = false) {
    const vehicleIdentifying = request.action === 'identify-vehicle';
    const dtcReading = request.action === 'read-dtc';
    const codingReading = request.action === 'read-coding';
    if (vehicleIdentifying) {
      if (Object.keys(request).some(k => !['action','confirmedReadOnly','x431Inactive'].includes(k)))
        return fail('manufacturer-read-preconditions-required');
      request = {...request, ecuId: 9};
    }
    const fields = identifying ? ["action", "generation", "ecuId", "confirmedReadOnly", "x431Inactive", ...(dtcReading || codingReading ? ["expectedVehicleIdentitySha256"] : [])] : ["action", "generation", "ecuId", "profileId", "parameterIds", "continuous", "confirmedReadOnly", "x431Inactive"];
    if (Object.keys(request).some(k => !fields.includes(k))
      || (!identifying && typeof request.continuous !== "boolean") || request.confirmedReadOnly !== true || request.x431Inactive !== true)
      return fail("manufacturer-read-preconditions-required");
    if ((dtcReading || codingReading) && request.expectedVehicleIdentitySha256 !== undefined
      && (typeof request.expectedVehicleIdentitySha256 !== "string" || !/^[a-f0-9]{64}$/.test(request.expectedVehicleIdentitySha256))) return fail("manufacturer-vehicle-binding-invalid");
    const generation = request.generation === undefined ? "981" : request.generation;
    if (!["981","982"].includes(generation)) return fail("manufacturer-profile-unqualified");
    if (identifying) {
      const nominal = Number.isInteger(request.ecuId) && [...LIVE_READ_PROFILES.keys()].find(pid => liveReadTargets(pid).includes(request.ecuId) && profileFitsGeneration(pid,generation));
      if (!nominal) return fail("manufacturer-profile-unqualified");
      request = { ...request, profileId: nominal, parameterIds: [], continuous: false };
    }
    if (!profileFitsGeneration(request.profileId,generation)) return fail("manufacturer-profile-unqualified");
    const targets = liveReadTargets(request.profileId);
    const ecuId = request.ecuId ?? (targets.length === 1 ? targets[0] : null);
    if (!Number.isInteger(ecuId) || !targets.includes(ecuId) || (!identifying && !request.parameterIds?.length)) return fail("manufacturer-profile-unqualified");
    const invalid = identifying ? null : validateRequest({ action: "ready-plan", generation, ecuId, profileId: request.profileId, parameterIds: request.parameterIds });
    if (invalid) return invalid;
    const blocked = liveBlocked(opts, opts.env || process.env);
    if (blocked) return fail("live_not_enabled", { reason: blocked });
    const conn = opts.connection;
    if (!conn || !opts.gate) return fail("manufacturer-live-handoff-unavailable");
    const prior = jobs.get(owner);
    if (prior && (!releaseComplete(prior) || prior.saving)) return fail("stream_busy");
    if (conn.snapshot().purpose !== "diagnostic") return fail("diagnostic_purpose_required");
    const deviceId = conn.selectedId();
    if (!deviceId) return fail("device_not_selected");
    if (deviceId.startsWith("pt3g:")) return fail("pt3g-vehicle-transport-not-qualified");
    if (!validObdDeviceId(deviceId)) return fail("device-id-invalid");
    const adapterModel = conn.snapshot().model;
    if (adapterModel === "CANtieCAR") return fail("adapter-mode-not-qualified");
    const serialBaud = conn.snapshot().serialBaud ?? null;
    if (!conn.reserveDispatch()) return fail("busy");
    const job = newJob(owner, true); job.reserved = true; job.native = deviceId.startsWith("vnci:");
    job.identifying = identifying; job.vehicleIdentifying = vehicleIdentifying; job.dtcReading = dtcReading; job.identifyEcuId = ecuId; job.generation = generation;
    job.codingReading = codingReading; if (codingReading) job.codingPdus = new Map();
    job.expectedVehicleIdentitySha256 = request.expectedVehicleIdentitySha256;
    try {
      const handoff = await conn.suspendForSession();
      if (handoff?.ok === false) {
        job.error = handoff.error || "port_release_pending"; job.reserved = false;
        conn.releaseDispatch(); released(job); return fail(job.error);
      }
      expire(job);
      if (disposed || job.state !== "preparing") { job.error = "cancelled"; released(job); return snapshot(job); }
      if (conn.selectedId() !== deviceId || conn.snapshot().model !== adapterModel || (conn.snapshot().serialBaud ?? null) !== serialBaud || conn.snapshot().purpose !== "diagnostic") { job.error = "device_mismatch"; released(job); return fail(job.error); }
      if (!opts.gate.tryAcquire("session")) { job.error = "busy"; released(job); return fail(job.error); }
      job.lease = true; job.state = "running";
      const payload = { action: codingReading ? 'manufacturer-read-coding' : dtcReading ? "manufacturer-read-dtc" : identifying ? "manufacturer-identify" : "manufacturer-read", generation, ecuId, profileId: request.profileId,
        ...(codingReading ? {completeOriginalRequired:true} : {}),
        ...((dtcReading || codingReading) && request.expectedVehicleIdentitySha256 ? { expectedVehicleIdentitySha256: request.expectedVehicleIdentitySha256 } : {}),
        parameterIds: request.parameterIds, deviceId, ...([1, 9].includes(ecuId) ? { sourceBound: true } : {}), ...(adapterModel ? { adapterModel } : {}), ...(serialBaud == null ? {} : { serialBaud }), confirmedReadOnly: true, x431Inactive: true,
        cycleCount: request.continuous ? null : 1 };
      if (vehicleIdentifying) {
        for (const key of ['generation','ecuId','profileId','parameterIds','sourceBound','cycleCount']) delete payload[key];
        payload.action = 'manufacturer-identify-vehicle';
      }
      launch(job, payload);
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
    const ended = await Promise.race([Promise.all(targets.map(async j => { await j.closed; await j.resuming; })).then(() => true),
      new Promise(resolve => { timer = setT(() => resolve(false), opts.closeWaitMs ?? 25000); })]);
    clearT(timer);
    if (ended && targets.some(j => j.error === "adapter_release_not_verified")) return fail("adapter_release_not_verified");
    return ended ? { ok: true } : fail("port_release_pending");
  }
  async function save(job) {
    if (!releaseComplete(job) || job.saving) return fail("stream_busy");
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
    // Private main-process client: renderer handle() never accepts read-coding.
    async readCoding(ecuId, owner) {
      if (![1, 2, 9, 47, 48].includes(ecuId)
        && !UDS_CODING_CONTEXTS.some(row => row.ecuId === ecuId)) return fail('coding-client-unavailable');
      const device = opts.connection?.selectedId();
      async function completed(request) {
        const start = await startRead(request, owner, true);
        if (!start.ok) throw new Error(start.error);
        const job = jobs.get(owner);
        await job.closed; await job.resuming;
        if (job.error || !releaseComplete(job) || job.releaseVerified !== true || !job.result?.ok || job.orphaned
          || opts.connection?.selectedId() !== device) throw new Error(job.error || job.result?.error || 'coding-read-failed');
        return job;
      }
      try {
        const anchor = await completed({ action:'identify-vehicle', confirmedReadOnly:true, x431Inactive:true });
        const job = await completed({ action:'read-coding', ecuId, generation:anchor.result.vehicleGeneration,
          expectedVehicleIdentitySha256:anchor.result.vehicleIdentitySha256, confirmedReadOnly:true, x431Inactive:true });
        return { ok:true, result:job.result, identity:job.codingIdentity, releaseVerified:true };
      } catch (error) { return fail(error.message || 'coding-read-failed'); }
      finally { const job = jobs.get(owner); if (job && releaseComplete(job)) jobs.delete(owner); }
    },
    handle(request, owner) {
      if (!request || typeof request !== "object" || Array.isArray(request)) return fail("malformed_request");
      // A quit timeout leaves the window alive. Existing data and release
      // status must remain accessible, while no new acquisition can start.
      if (disposed && !["status", "stop", "save", "discard"].includes(request.action)) return fail("stream_shutdown");
      if (["save", "discard"].includes(request.action)) {
        if (Object.keys(request).some(key => !["action", "sessionId"].includes(key))) return fail("forbidden_field");
        const job = jobs.get(owner);
        if (!job || job.id !== request.sessionId) return fail("unknown_session");
        if (!releaseComplete(job) || job.saving) return fail("stream_busy");
        if (request.action === "discard") { jobs.delete(owner); return { ok: true, ...FLAGS }; }
        return save(job);
      }
      if (request.action === "start-read") return startRead(request, owner);
      if (request.action === "identify") return startRead(request, owner, true);
      if (request.action === "read-dtc") return startRead(request, owner, true);
      if (request.action === "identify-vehicle") return startRead(request, owner, true);
      if (request.action === "retry") {
        if (Object.keys(request).some(key => !["action", "sessionId", "episode", "attempt"].includes(key))) return fail("forbidden_field");
        const job = jobs.get(owner);
        if (!job || job.id !== request.sessionId) return fail("unknown_session");
        expire(job);
        const checkpoint = job.connectionEvents.at(-1);
        if (!job.live || job.state !== "running" || !job.awaitingRetry || job.retryRequested
          || checkpoint?.phase !== "awaiting-retry" || checkpoint.linkReleased !== true
          || request.episode !== checkpoint.episode || request.attempt !== checkpoint.attempt)
          return fail("stream_retry_not_available");
        job.retryRequested = true; job.awaitingRetry = false;
        clearT(job.timer); job.timer = setT(() => abort(job, "stream_timeout"), opts.timeoutMs ?? 45000);
        try { job.child.stdin.write(JSON.stringify({ action: "retry", episode: checkpoint.episode, attempt: checkpoint.attempt }) + "\n"); }
        catch { abort(job, "stdin_closed"); return fail("stdin_closed"); }
        return snapshot(job);
      }
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
        if (prior && (prior.state !== "finished" || !releaseComplete(prior) || prior.saving)) return fail("stream_busy");
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
    cancelOwned(owner) { const job = jobs.get(owner); if (job) { job.orphaned = true; stop(job); if (releaseComplete(job)) jobs.delete(owner); } },
    cancelActiveAndWait() { return cancelAndWait([...jobs.values()].filter(j => j.live && !releaseComplete(j))); },
    shutdown() { disposed = true; return cancelAndWait([...jobs.values()].filter(j => !releaseComplete(j))); },
    isIdle() { return [...jobs.values()].every(releaseComplete); },
  };
}
