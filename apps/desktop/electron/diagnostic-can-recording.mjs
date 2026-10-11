import { createCanPcapWriter, validCanFrame } from "./internal-can-data.mjs";
import { createDiagnosticPduWriter, validDiagnosticPdu } from "./diagnostic-pdu-data.mjs";
import { CAPTURE_CLOCK, captureNowUs } from "./capture-clock.mjs";
const RECORDING_DURATION_MS = 10 * 60 * 1000;
export function createDiagnosticCanRecorder({ chooseFile, isLiveReady = () => false,
  nowFn = Date.now, elapsedNowFn = () => performance.now(), captureNowUsFn = captureNowUs, setTimeoutFn = setTimeout, clearTimeoutFn = clearTimeout }) {
  let writer = null, state = null, startUs = 0, startMonotonicUs = 0, deadline = 0, timer = null, serial = Promise.resolve(), disposed = false, epoch = 0;
  function finish(reason) {
    if (!writer) return;
    if (timer !== null) { clearTimeoutFn(timer); timer = null; }
    const current = writer; writer = null;
    state = { ...state, active: false, frameCount: current.count, endedAt: new Date(nowFn()).toISOString(), reason };
    try { current.finish(reason); } catch (error) { state.error = String(error.code || error.message || error); }
  }
  // Wall time belongs to packet metadata; clock corrections cannot change the duration.
  function expire() { if (writer && elapsedNowFn() >= deadline) finish("time-limit"); }
  function snapshot() { expire(); return { ok: true, recording: writer ? { ...state, frameCount: writer.count } : state }; }
  function inside(value) {
    if (value.monotonicClock !== undefined) {
      if (value.monotonicClock !== CAPTURE_CLOCK) { finish("capture-clock-mismatch"); return false; }
      return (value.startedMonotonicTimestampUs ?? value.monotonicTimestampUs) >= startMonotonicUs;
    }
    return (value.startedTimestampUs ?? value.timestampUs) >= startUs;
  }
  async function act(request) {
    if (disposed) return { ok: false, error: "recording-shutdown" };
    if (!request || Object.keys(request).some((key) => !["action", "simulation", "layer"].includes(key)) || !["status", "start", "stop"].includes(request.action)
      || (request.layer !== undefined && !["can", "application"].includes(request.layer))) return { ok: false, error: "recording-request-invalid" };
    if (request.action === "status") return snapshot();
    expire();
    if (request.action === "stop") { finish("user-stopped"); return snapshot(); }
    if (typeof request.simulation !== "boolean") return { ok: false, error: "recording-source-required" };
    if (writer) return { ...snapshot(), ok: false, error: "already-recording" };
    const layer = request.layer || "can";
    if (!request.simulation && !isLiveReady(layer)) return { ok: false, error: layer === "application" ? "diagnostic_pdu_unavailable" : "raw_can_frames_unavailable" };
    const requestedEpoch = epoch;
    const file = await chooseFile(request.simulation, layer); if (!file) return { ...snapshot(), canceled: true };
    if (disposed || requestedEpoch !== epoch) return { ok: false, error: "recording-start-interrupted" };
    if (!request.simulation && !isLiveReady(layer)) return { ok: false, error: layer === "application" ? "diagnostic_pdu_unavailable" : "raw_can_frames_unavailable" };
    try {
      writer = layer === "application" ? createDiagnosticPduWriter(file, request.simulation) : createCanPcapWriter(file, request.simulation ? "SIMULATED diagnostic RX" : "diagnostic RX", {
        description: request.simulation ? "SYNTHETIC simulator CAN frames, captured only during explicit recording. Not vehicle evidence."
          : "Adapter-reported received CAN frames only during explicit recording. No inferred TX or reconstructed PDU. Host arrival timestamps, not hardware bus timing." });
      const started = nowFn(); startUs = started * 1000; deadline = elapsedNowFn() + RECORDING_DURATION_MS;
      startMonotonicUs = captureNowUsFn();
      state = { active: true, simulation: request.simulation, layer, startedAt: new Date(started).toISOString(), file, frameCount: 0 };
      const current = writer;
      timer = setTimeoutFn(() => { if (writer === current) finish("time-limit"); }, RECORDING_DURATION_MS);
      timer?.unref?.();
      return snapshot();
    } catch (error) { return { ok: false, error: String(error.code || error.message || error) }; }
  }
  return {
    handle(request) { const next = serial.then(() => act(request), () => act(request)); serial = next.catch(() => {}); return next; },
    onFrame(event) {
      expire();
      if (!writer || state.layer !== "can") return;
      if (!validCanFrame(event.frame)) { finish("invalid-frame"); state.error = "invalid-frame"; return; }
      if (event.simulation !== state.simulation) { finish("source-changed"); return; }
      if (!inside(event.frame)) return;
      try { writer.append([{ ...event.frame, captureContext: { profileId: event.profileId, runId: event.runId, simulation: event.simulation } }]); }
      catch (error) { finish("recording-write-failed"); state.error = String(error.code || error.message || error); }
    },
    onPdu(event) {
      expire();
      if (!writer || state.layer !== "application") return;
      if (!validDiagnosticPdu(event.pdu)) { finish("invalid-pdu"); state.error = "invalid-pdu"; return; }
      if (event.simulation !== state.simulation) { finish("source-changed"); return; }
      if (!inside(event.pdu)) return;
      try { writer.append(event.pdu, { targetProfileId: event.profileId, runId: event.runId, simulation: event.simulation }); }
      catch (error) { finish("recording-write-failed"); state.error = String(error.code || error.message || error); }
    },
    stop(reason) { epoch++; finish(reason); },
    shutdown() { disposed = true; epoch++; finish("application-closed"); }, snapshot,
  };
}
