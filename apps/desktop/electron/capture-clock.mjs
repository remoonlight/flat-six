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
