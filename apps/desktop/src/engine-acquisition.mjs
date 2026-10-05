// Every child session is finite, independently qualified and actually closed.
// Repeat only completed reads; a failed or cancelled task never auto-resumes.
export function createEngineAcquisition({ limit = 3000, byteLimit = 6 * 1024 * 1024 } = {}) {
  let samples = [], runs = [], offset = 0, firstTime = null, bytes = 0;
  const startedAt = new Date().toISOString();
  return {
    append(final) {
      if (!final || !Array.isArray(final.engine?.samples) || !final.runId || runs.some((run) => run.runId === final.runId)) return false;
      if (samples.length + final.engine.samples.length > limit) throw new Error("acquisition-batch-limit");
      bytes += new TextEncoder().encode(JSON.stringify(final)).length;
      for (const sample of final.engine.samples) {
        const stamp = Date.parse(sample.capturedUtc);
        if (firstTime === null && Number.isFinite(stamp)) firstTime = stamp;
        samples.push({ ...sample, sourceRunId: final.runId, cycle: sample.cycle + offset,
          elapsedMs: Number.isFinite(stamp) && firstTime !== null ? stamp - firstTime : sample.elapsedMs });
      }
      offset = Math.max(offset, ...samples.map((sample) => sample.cycle));
      runs.push(final);
      return true;
    },
    get samples() { return samples; },
    get full() { return samples.length >= limit; },
    canFit(count) { return Number.isInteger(count) && count > 0 && samples.length + count <= limit && bytes + 2 * 1024 * 1024 + 65536 <= byteLimit; },
    get count() { return samples.length; },
    hasRun(id) { return runs.some((run) => run.runId === id); },
    export() { return { kind: "engine-acquisition-batch", startedAt, endedAt: new Date().toISOString(),
      sampleLimit: limit, byteLimit, samples, runs, retentionComplete: true, batchComplete: runs.every((run) => run.status === "completed"),
      simulation: runs.every((run) => run.simulation === true), liveVerified: false }; },
  };
}
