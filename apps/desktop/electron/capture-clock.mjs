// Shared host clocks used by libuv and the Python capture producer. Wall time
// stays in PCAP packet timestamps; this clock only determines record membership.
export const CAPTURE_CLOCK = process.platform === "win32" ? "windows-qpc"
  : process.platform === "darwin" ? "darwin-continuous" : "posix-monotonic";
export const captureNowUs = () => Number(process.hrtime.bigint() / 1000n);
const clocks = new Set(["windows-qpc", "darwin-continuous", "posix-monotonic"]);
const positive = n => Number.isSafeInteger(n) && n > 0;
export function validCaptureClock(value) {
  const keys = ["monotonicClock", "monotonicTimestampUs", "startedMonotonicTimestampUs"];
  if (keys.every(key => value[key] === undefined)) return true; // Retained legacy captures.
  return clocks.has(value.monotonicClock) && positive(value.monotonicTimestampUs)
    && (value.startedMonotonicTimestampUs === undefined || (positive(value.startedMonotonicTimestampUs)
      && value.startedMonotonicTimestampUs <= value.monotonicTimestampUs));
}

// Presentation time only: advance a graph through silent intervals without
// manufacturing samples or changing their recorded timestamps.
export function createPlotClock({ now = () => performance.now() } = {}) {
  let elapsed = null, observedAt = now(), running = true;
  const read = (at = now()) => elapsed === null ? null
    : elapsed + (running ? Math.max(0, at - observedAt) : 0);
  return {
    read,
    observe(value) {
      if (!Number.isFinite(value) || value < 0) return;
      const at = now();
      elapsed = Math.max(value, read(at) ?? value); observedAt = at;
    },
    freeze(at = now()) { elapsed = read(at); observedAt = at; running = false; },
    // Continuing the same batch includes time spent waiting for user retry.
    resume() { running = true; },
  };
}
