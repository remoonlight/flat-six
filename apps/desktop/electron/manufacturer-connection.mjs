/** Shared read-only connection-event contract for workers and saved recording replay. */
const CONNECTION_REASONS = new Set(["transport-disconnected", "cancelled", "deadline-expired", "identity-mismatch",
  "recovery-close-failed", "manufacturer-close-failed", "recovery-exhausted", "identity-decode-failed",
  "identity-request-failed", "session-subfunction-mismatch", "response-rejected", "recovery-failed"]);
export function validConnectionEvent(doc, job) {
  if (!doc || typeof doc !== "object" || Array.isArray(doc) || doc.liveVerified !== false || doc.writePayload !== null) return false;
  const prior = job.connectionEvents.at(-1);
  if (Object.keys(doc).some(k => !["sequence", "episode", "phase", "attempt", "connectionEpoch", "elapsedMs", "observedUtc", "reason",
    "sourceKind", "simulation", "executionEnabled", "liveVerified", "writePayload"].includes(k))
    || doc.executionEnabled !== job.live || doc.simulation !== !job.live
    || doc.sourceKind !== (job.live ? "independent-manufacturer-read" : "historical-fixture-rehearsal")
    || doc.sequence !== job.connectionEvents.length + 1 || !Number.isSafeInteger(doc.episode) || doc.episode < 1
    || !Number.isSafeInteger(doc.attempt) || doc.attempt < 0 || doc.attempt > 3
    || !Number.isSafeInteger(doc.connectionEpoch) || doc.connectionEpoch < 1
    || !Number.isFinite(doc.elapsedMs) || doc.elapsedMs < (prior?.elapsedMs ?? 0)
    || typeof doc.observedUtc !== "string" || doc.observedUtc.length > 40 || !Number.isFinite(Date.parse(doc.observedUtc))) return false;
  if (doc.phase === "interrupted") return doc.attempt === 0 && doc.reason === "transport-disconnected"
    && doc.episode === (prior?.episode ?? 0) + 1 && (!prior || prior.phase === "recovered")
    && (!prior || doc.connectionEpoch === prior.connectionEpoch);
  if (!prior || doc.episode !== prior.episode || ["recovered", "failed"].includes(prior.phase)) return false;
  if (doc.phase === "retrying") return doc.reason === null && ["interrupted", "retry-failed"].includes(prior.phase)
    && doc.attempt === prior.attempt + 1 && doc.connectionEpoch === prior.connectionEpoch;
  if (doc.attempt !== prior.attempt) return false;
  if (doc.phase === "qualified") return doc.reason === null && prior.phase === "retrying" && doc.connectionEpoch === prior.connectionEpoch + 1;
  if (doc.connectionEpoch !== prior.connectionEpoch) return false;
  if (doc.phase === "recovered") return doc.reason === null && prior.phase === "qualified";
  if (doc.phase === "retry-failed") return ["retrying", "qualified"].includes(prior.phase) && doc.reason === "transport-disconnected";
  return doc.phase === "failed" && CONNECTION_REASONS.has(doc.reason);
}
