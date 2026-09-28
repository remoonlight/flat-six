/** Shared one-owner transport gate for session jobs vs ATRV monitor. */
export function createTransportGate() {
  let owner = null;
  return {
    tryAcquire(who) {
      if (!who || owner) return false;
      owner = who;
      return true;
    },
    release(who) {
      if (owner === who) owner = null;
    },
    owner() {
      return owner;
    },
  };
}

export function parseVoltageVolts(text) {
  if (typeof text !== "string") return null;
  if (/-\s*\d/.test(text)) return null;
  if (/ERROR|UNABLE|NO DATA|\?/i.test(text)) return null;
  const lines = text.replaceAll(">", "\n").split(/[\r\n]+/).map((line) => line.trim()).filter((line) => line && line.toUpperCase() !== "ATRV");
  const hits = lines.map((line) => line.match(/^(\d+(?:\.\d+)?)\s*V$/i));
  if (hits.some((hit) => !hit)) return null;
  if (hits.length !== 1) return null;
  const v = Number(hits[0][1]);
  if (!Number.isFinite(v) || v <= 0 || v < 6 || v > 20) return null;
  return v;
}

export function formatHeaderVoltage(volts) {
  if (typeof volts !== "number" || !Number.isFinite(volts) || volts < 6 || volts > 20) return "电压 -- V";
  return `电压 ${volts.toFixed(1)} V`;
}

export function voltageFresh(at, now, ttlMs) {
  if (typeof at !== "number" || !Number.isFinite(at) || typeof now !== "number") return false;
  if (at > now) return false;
  return now - at <= ttlMs;
}

// Observed X431 (local HCI): Bluetooth ACL retained minutes–hours. Not a voltage sample period.
// Product design defaults — NOT measured X431 ATRV cadence:
export const ATRV_INTERVAL_MS = 2000;
export const VOLTAGE_FRESH_MS = 6000;
export const RETRY_BACKOFF_MS = Object.freeze([5000, 10000, 20000, 30000]);
export const MONITOR_AT_TIMEOUT_MS = 4000;
export const MONITOR_HANDSHAKE_MS = 30_000;
export const MONITOR_LIVENESS_MS = ATRV_INTERVAL_MS + MONITOR_AT_TIMEOUT_MS;
export const MONITOR_GRACEFUL_MS = 1500;
export const MONITOR_KILL_WAIT_MS = 400;

export function nextRetryDelayMs(failCount) {
  const n = Number(failCount);
  if (!Number.isFinite(n) || n < 1) return RETRY_BACKOFF_MS[0];
  return RETRY_BACKOFF_MS[Math.min(n - 1, RETRY_BACKOFF_MS.length - 1)];
}

export function isTransportFault(error) {
  const s = String(error || "");
  if (/adapter-identity-mismatch|device-id-invalid|live-probe-disabled|invalid-kind|atrv-unparsed|malformed_request|malformed_device/i.test(s)) {
    return false;
  }
  return /port-io|timeout|spawn_failed|disconnect|python_runtime_missing|prompt-timeout|output_cap|stdin_closed|device-port-unavailable|device-identity-missing|device-port-not-unique/i.test(s);
}

function isPlain(v) {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

export function parseSampleAt(raw, now) {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const t = Date.parse(raw);
    if (Number.isFinite(t)) return t;
  }
  return null;
}

export function acceptMonitorReading(doc, { deviceId, now, freshMs }) {
  if (!isPlain(doc) || doc.simulation === true) return { reject: "synthetic" };
  if (doc.type === "stopped") return { ignore: true };
  if (doc.ok === false || doc.type === "error") {
    return { fatal: true, error: String(doc.error || "probe_failed") };
  }
  if (doc.ok !== true || doc.simulation !== false) return { reject: "malformed" };
  if (doc.type !== "handshake" && doc.type !== "reading") return { reject: "malformed" };
  if (doc.voltageSource !== "atrv") return { reject: "malformed" };
  if (doc.deviceId !== deviceId) return { reject: "device_mismatch" };
  if (typeof doc.volts !== "number" || !Number.isFinite(doc.volts) || doc.volts < 6 || doc.volts > 20) {
    return { reject: "malformed" };
  }
  const at = parseSampleAt(doc.at, now);
  if (at == null || at > now || now - at > freshMs) return { reject: "stale" };
  return { volts: doc.volts, at, source: "atrv" };
}
