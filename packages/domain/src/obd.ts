/** Offline OBD contracts. No hardware or executable coding commands. */
export const OBD_DECODER_VERSION = "standard-v1";
export const OBD_SCENARIOS = ["normal", "no-codes", "no-response", "disconnect", "malformed"] as const;
export type ObdScenario = typeof OBD_SCENARIOS[number];
export type ObdOutcome = "ok" | "no-data" | "timeout" | "invalid" | "disconnected" | "cancelled";
export type ObdSample = { ecu: string; signal: string; label: string; value: number; unit: string; context: "live" | "freeze" };
export type ObdCode = { ecu: string; code: string; status: "stored" | "pending" | "permanent" | "freeze" };
export type ObdReply = { ecu: string; payload: number[] };
export type ObdObservation = {
  seq: number; tMs: number; command: string; raw: string; outcome: ObdOutcome;
  detail: string | null; replies: ObdReply[]; samples: ObdSample[]; dtcs: ObdCode[];
  supported: Record<string, number[]>;
  vin: { ecu: string; value: string }[];
  monitors: { ecu: string; mil: boolean; count: number; raw: number[] }[];
};
export type ObdRun = {
  sessionId: number; source: "simulation"; scenario: ObdScenario; budgetMs: number;
  status: "running" | "completed" | "partial" | "cancelled" | "interrupted";
  startedAt: string; endedAt: string | null; reason: string | null; decoderVersion: string;
};
export type ObdRecording = { version: 1; run: ObdRun; observations: ObdObservation[] };
export type ObdRuntimeState = {
  mode: "offline" | "simulation" | "replay";
  phase: "idle" | "running" | "saving" | "finished" | "error";
  run: ObdRun | null; elapsedMs: number; observations: ObdObservation[]; error: string | null;
};
export type ObdPlan = { scenario: ObdScenario; budgetMs: number; reserveMs: number; commands: readonly string[] };
export const OBD_BASE_COMMANDS = ["0100", "03", "07", "0101", "020200", "020C00", "020500", "0902", "0A"] as const;
export const OBD_LIVE_COMMANDS = ["010C", "0105", "010D", "0104", "010F", "0110"] as const;
const allowed = new Set<string>([...OBD_BASE_COMMANDS, ...OBD_LIVE_COMMANDS]);
export function assertReadCommand(command: string): void {
  if (!allowed.has(command)) throw new Error("obd_command_not_allowed");
}
export function createObdPlan(input: { scenario?: unknown; budgetMs?: unknown }): ObdPlan {
  const scenario = input?.scenario ?? "normal";
  const budgetMs = input?.budgetMs ?? 30_000;
  if (!OBD_SCENARIOS.includes(scenario as ObdScenario)) throw new Error("obd_invalid_scenario");
  if (typeof budgetMs !== "number" || !Number.isInteger(budgetMs) || budgetMs < 5_000 || budgetMs > 180_000) throw new Error("obd_invalid_budget");
  return { scenario: scenario as ObdScenario, budgetMs, reserveMs: Math.min(2_000, budgetMs / 5), commands: OBD_BASE_COMMANDS };
}

/** Prompt-delimited ELM input, independent of serial event boundaries. */
export class ElmPromptBuffer {
  private pending = "";
  push(chunk: string): string[] {
    this.pending += chunk;
    if (this.pending.length > 65_536) { this.pending = ""; throw new Error("obd_response_too_large"); }
    const parts = this.pending.split(">");
    this.pending = parts.pop()!;
    return parts.map((p) => p + ">");
  }
  get incomplete(): boolean { return this.pending.trim().length > 0; }
}

/** Expected capture format: headers on, spaces on, CAN PCI present, no DLC column.
 * This is a fixture/import contract, not a claim about an untested vLinker firmware.
 */
export function parseCanReplies(raw: string, command: string): ObdReply[] {
  if (!raw.trimEnd().endsWith(">")) throw new Error("incomplete_prompt");
  const completed: ObdReply[] = [];
  const streams = new Map<string, { size: number; next: number; bytes: number[] }>();
  for (const original of raw.replace(/>/g, "").split(/[\r\n]+/)) {
    const line = original.trim();
    if (!line || line.replace(/\s/g, "").toUpperCase() === command || line === "SEARCHING...") continue;
    const match = /^([0-9A-F]{3}|[0-9A-F]{8})\s+((?:[0-9A-F]{2}\s*)+)$/i.exec(line);
    if (!match) throw new Error("invalid_can_line");
    const ecu = match[1].toUpperCase();
    if (parseInt(ecu, 16) > (ecu.length === 3 ? 0x7ff : 0x1fffffff)) throw new Error("invalid_can_address");
    const bytes = match[2].trim().split(/\s+/).map((v) => parseInt(v, 16));
    if (bytes.length > 8) throw new Error("invalid_can_length");
    const kind = bytes[0] >> 4;
    if (kind === 0) {
      const size = bytes[0] & 15;
      if (!size || size > 7 || bytes.length < size + 1 || streams.has(ecu)) throw new Error("invalid_single_frame");
      completed.push({ ecu, payload: bytes.slice(1, size + 1) });
    } else if (kind === 1) {
      const size = ((bytes[0] & 15) << 8) | bytes[1];
      if (bytes.length !== 8 || size <= 7 || size > 4095 || streams.has(ecu)) throw new Error("invalid_first_frame");
      streams.set(ecu, { size, next: 1, bytes: bytes.slice(2) });
    } else if (kind === 2) {
      const stream = streams.get(ecu);
      if (!stream || (bytes[0] & 15) !== stream.next || bytes.length < 2) throw new Error("invalid_sequence");
      const remaining = stream.size - stream.bytes.length;
      if (bytes.length - 1 < Math.min(7, remaining)) throw new Error("truncated_consecutive_frame");
      stream.bytes.push(...bytes.slice(1));
      stream.next = (stream.next + 1) & 15;
      if (stream.bytes.length >= stream.size) {
        completed.push({ ecu, payload: stream.bytes.slice(0, stream.size) });
        streams.delete(ecu);
      }
    } else throw new Error("unexpected_frame_type");
  }
  if (streams.size) throw new Error("incomplete_multiframe");
  if (!completed.length) throw new Error("missing_ecu_response");
  return completed;
}

export function decodeDtc(a: number, b: number): string {
  return "PCBU"[a >> 6] + ((a >> 4) & 3) + (a & 15).toString(16).toUpperCase() + b.toString(16).padStart(2, "0").toUpperCase();
}
function decodeValue(ecu: string, pid: number, bytes: number[], context: "live" | "freeze"): ObdSample[] {
  const defs: Record<number, [string, string, string, number, (a: number, b: number) => number]> = {
    0x0c: ["rpm", "转速", "rpm", 2, (a, b) => (a * 256 + b) / 4],
    0x05: ["coolant", "冷却液温度", "°C", 1, (a) => a - 40],
    0x0d: ["speed", "车速", "km/h", 1, (a) => a],
    0x04: ["load", "发动机负荷", "%", 1, (a) => a * 100 / 255],
    0x0f: ["intake", "进气温度", "°C", 1, (a) => a - 40],
    0x10: ["maf", "空气流量", "g/s", 2, (a, b) => (a * 256 + b) / 100],
  };
  const def = defs[pid];
  if (!def) return [];
  if (bytes.length !== def[3]) throw new Error("invalid_pid_length");
  return [{ ecu, signal: def[0], label: def[1], unit: def[2], value: def[4](bytes[0], bytes[1]), context }];
}
export function decodeObdObservation(command: string, raw: string, seq: number, tMs: number): ObdObservation {
  assertReadCommand(command);
  const result: ObdObservation = { seq, tMs, command, raw, outcome: "ok", detail: null, replies: [], samples: [], dtcs: [], supported: {}, vin: [], monitors: [] };
  const lines = raw.trim().replace(/>$/, "").split(/[\r\n]+/).map((line) => line.trim()).filter((line) => line && line.replace(/\s/g, "").toUpperCase() !== command && line !== "SEARCHING...");
  if (raw.trimEnd().endsWith(">") && lines.length === 1 && lines[0] === "NO DATA") { result.outcome = "no-data"; result.detail = "未收到有效数据，不等于无码或不支持"; return result; }
  try {
    const replies = parseCanReplies(raw, command);
    const service = parseInt(command.slice(0, 2), 16);
    for (const { ecu, payload } of replies) {
      if (payload[0] !== service + 0x40) throw new Error("unexpected_service");
      if ([3, 7, 10].includes(service)) {
        if ((payload.length - 1) % 2) throw new Error("invalid_dtc_length");
        for (let i = 1; i < payload.length; i += 2) {
          if (payload[i] || payload[i + 1]) result.dtcs.push({ ecu, code: decodeDtc(payload[i], payload[i + 1]), status: service === 3 ? "stored" : service === 7 ? "pending" : "permanent" });
        }
      } else if (service === 1 || service === 2) {
        const pid = parseInt(command.slice(2, 4), 16);
        if (payload[1] !== pid) throw new Error("unexpected_pid");
        if (service === 2 && payload[2] !== 0) throw new Error("unexpected_freeze_frame");
        const bytes = payload.slice(service === 2 ? 3 : 2);
        if (pid === 0 && service === 1) {
          if (bytes.length !== 4) throw new Error("invalid_support_length");
          result.supported[ecu] = Array.from({ length: 32 }, (_, i) => i + 1).filter((p) => (bytes[(p - 1) >> 3] & (1 << (7 - ((p - 1) % 8)))) !== 0);
        } else if (pid === 1 && service === 1) {
          if (bytes.length !== 4) throw new Error("invalid_monitor_length");
          result.monitors.push({ ecu, mil: !!(bytes[0] & 128), count: bytes[0] & 127, raw: bytes });
        } else if (pid === 2 && service === 2) {
          if (bytes.length !== 2) throw new Error("invalid_freeze_dtc");
          if (bytes[0] || bytes[1]) result.dtcs.push({ ecu, code: decodeDtc(bytes[0], bytes[1]), status: "freeze" });
        } else result.samples.push(...decodeValue(ecu, pid, bytes, service === 2 ? "freeze" : "live"));
      } else if (service === 9) {
        if (payload.length !== 20 || payload[1] !== 2 || payload[2] !== 1) throw new Error("invalid_vin_response");
        const value = String.fromCharCode(...payload.slice(3));
        if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(value)) throw new Error("invalid_vin");
        result.vin.push({ ecu, value });
      }
    }
    result.replies = replies;
  } catch (e) {
    result.outcome = "invalid";
    result.detail = e instanceof Error ? e.message : "invalid_response";
    result.replies = []; result.samples = []; result.dtcs = []; result.supported = {}; result.vin = []; result.monitors = [];
  }
  return result;
}

/** Replays always re-decode raw evidence; imported derived values are never trusted. */
export function validateObdRecording(value: unknown): ObdRecording {
  const data = value as ObdRecording;
  if (!data || data.version !== 1 || data.run?.source !== "simulation" || !Array.isArray(data.observations) || data.observations.length > 3000) throw new Error("obd_invalid_recording");
  createObdPlan(data.run);
  if (!Number.isSafeInteger(data.run.sessionId) || data.run.sessionId < 1 || !Number.isFinite(Date.parse(data.run.startedAt))) throw new Error("obd_invalid_recording_identity");
  if (!["running", "completed", "partial", "cancelled", "interrupted"].includes(data.run.status)) throw new Error("obd_invalid_recording_status");
  let previous = -1;
  const observations = data.observations.map((o, i) => {
    if (!o || o.seq !== i + 1 || !Number.isInteger(o.tMs) || o.tMs < 0 || o.tMs < previous || o.tMs > data.run.budgetMs + 10_000 || typeof o.raw !== "string" || o.raw.length > 65_536) throw new Error("obd_invalid_observation");
    previous = o.tMs;
    const decoded = decodeObdObservation(o.command, o.raw, o.seq, o.tMs);
    if (["timeout", "disconnected", "cancelled"].includes(o.outcome) && !o.raw) { decoded.outcome = o.outcome; decoded.detail = o.outcome; }
    return decoded;
  });
  return { version: 1, run: data.run, observations };
}
