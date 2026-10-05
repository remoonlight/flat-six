import { createCanPcapWriter, validCanFrame } from "./internal-can-data.mjs";
export function createDiagnosticCanRecorder({ chooseFile, isLiveReady = () => false }) {
  let writer = null, state = null, startUs = 0, serial = Promise.resolve(), disposed = false, epoch = 0;
  function finish(reason) {
    if (!writer) return;
    const current = writer; writer = null;
    state = { ...state, active: false, frameCount: current.count, endedAt: new Date().toISOString(), reason };
    try { current.finish(reason); } catch (error) { state.error = String(error.code || error.message || error); }
  }
  function snapshot() { return { ok: true, recording: writer ? { ...state, frameCount: writer.count } : state }; }
  async function act(request) {
    if (disposed) return { ok: false, error: "recording-shutdown" };
    if (!request || Object.keys(request).some((key) => !["action", "simulation"].includes(key)) || !["status", "start", "stop"].includes(request.action)) return { ok: false, error: "recording-request-invalid" };
    if (request.action === "status") return snapshot();
    if (request.action === "stop") { finish("user-stopped"); return snapshot(); }
    if (typeof request.simulation !== "boolean") return { ok: false, error: "recording-source-required" };
    if (writer) return { ...snapshot(), ok: false, error: "already-recording" };
    if (!request.simulation && !isLiveReady()) return { ok: false, error: "raw_can_frames_unavailable" };
    const requestedEpoch = epoch;
    const file = await chooseFile(request.simulation); if (!file) return { ...snapshot(), canceled: true };
    if (disposed || requestedEpoch !== epoch) return { ok: false, error: "recording-start-interrupted" };
    if (!request.simulation && !isLiveReady()) return { ok: false, error: "raw_can_frames_unavailable" };
    try {
      writer = createCanPcapWriter(file, request.simulation ? "SIMULATED diagnostic RX" : "diagnostic RX", {
        description: request.simulation ? "SYNTHETIC simulator CAN frames, captured only during explicit recording. Not vehicle evidence."
          : "Adapter-reported received CAN frames only during explicit recording. No inferred TX or reconstructed PDU. Host arrival timestamps, not hardware bus timing." });
      startUs = Date.now() * 1000;
      state = { active: true, simulation: request.simulation, startedAt: new Date().toISOString(), file, frameCount: 0 };
      return snapshot();
    } catch (error) { return { ok: false, error: String(error.code || error.message || error) }; }
  }
  return {
    handle(request) { const next = serial.then(() => act(request), () => act(request)); serial = next.catch(() => {}); return next; },
    onFrame(event) {
      if (!writer) return;
      if (!validCanFrame(event.frame)) { finish("invalid-frame"); state.error = "invalid-frame"; return; }
      if (event.simulation !== state.simulation) { finish("source-changed"); return; }
      if (event.frame.timestampUs < startUs) return;
      try { writer.append([{ ...event.frame, captureContext: { profileId: event.profileId, runId: event.runId, simulation: event.simulation } }]); }
      catch (error) { finish("recording-write-failed"); state.error = String(error.code || error.message || error); }
    },
    stop(reason) { epoch++; finish(reason); },
    shutdown() { disposed = true; epoch++; finish("application-closed"); }, snapshot,
  };
}
