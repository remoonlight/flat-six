// Every child session is finite, independently qualified and actually closed.
// Repeat only completed reads; a failed or cancelled task never auto-resumes.
export function createEngineAcquisition() {
  const samples = [], runs = [], runIds = new Set();
  let offset = 0, firstTime = null, stopReason = null;
  const startedAt = new Date().toISOString();
  return {
    append(final) {
      if (!final || !Array.isArray(final.engine?.samples) || !final.runId || runIds.has(final.runId)) return false;
      let nextOffset = offset;
      for (const sample of final.engine.samples) {
        const stamp = Date.parse(sample.capturedUtc);
        if (firstTime === null && Number.isFinite(stamp)) firstTime = stamp;
        const cycle = sample.cycle + offset;
        nextOffset = Math.max(nextOffset, cycle);
        samples.push({ ...sample, sourceRunId: final.runId, cycle,
          elapsedMs: Number.isFinite(stamp) && firstTime !== null ? stamp - firstTime : sample.elapsedMs });
      }
      offset = nextOffset;
      runs.push(final);
      runIds.add(final.runId);
      return true;
    },
    get samples() { return samples; },
    get count() { return samples.length; },
    hasRun(id) { return runIds.has(id); },
    stop(reason) { stopReason = reason; },
    export() { return { kind: "engine-acquisition-batch", startedAt, endedAt: new Date().toISOString(),
      stopReason,
      sampleLimit: null, byteLimit: null, samples: samples.slice(), runs: runs.slice(), retentionComplete: true,
      batchComplete: runs.every((run) => (run.sessionState || run.status) === "completed"),
      simulation: runs.every((run) => run.simulation === true), liveVerified: false }; },
  };
}
