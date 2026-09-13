import { decodeDtc, parseCanReplies, type ObdCode, type ObdReply } from "./obd.js";
import { ANALYSIS_PARAMETERS, analysisCommand, decodeAnalysisPid } from './obd-analysis.js';

export const OBD_PRODUCTION_COMMANDS = [
  "0100", "0120", "0140", ...ANALYSIS_PARAMETERS.map(p => analysisCommand(p.pid)),
  "03", "07", "0A", "04",
  "0902", "0904", "0906",
] as const;
export const AT_HANDSHAKE = ["ATZ", "ATE0", "ATL0", "ATH1", "ATS1", "ATD0", "ATSP6", "ATCAF1", "ATCFC1", "ATCSM0", "ATCEA", "ATCRA", "ATSH7DF", "ATAL"] as const;
export const TRUSTED_AT_COMMANDS = [...AT_HANDSHAKE, "ATRV"] as const;
export type ObdProductionCommand = typeof OBD_PRODUCTION_COMMANDS[number];

const TRUSTED = new Set<string>([...OBD_PRODUCTION_COMMANDS, ...TRUSTED_AT_COMMANDS]);
export function assertTrustedCommand(command: string): string {
  const cmd = command.trim().toUpperCase();
  if (/[\r\n]/.test(cmd)) throw new Error("obd_production_command_not_allowed");
  if (!TRUSTED.has(cmd)) throw new Error("obd_production_command_not_allowed");
  return cmd;
}

export type ElmFraming = "pci";
export type ProductionOutcome =
  | "ok" | "no-data" | "timeout" | "invalid" | "disconnected" | "cancelled"
  | "at-error" | "bus-error" | "unable" | "unknown";

export type AdapterIdentity = {
  port: string;
  friendlyName: string;
  pnpId: string | null;
  occupied: boolean;
  occupiedReason: string | null;
  preferred: boolean;
};

export type VoltageReading = { volts: number; source: "ATRV" | "PID0142"; observedAt: string };
export type IgnitionState = "running" | "unknown";
export type LiveHeader = {
  adapterConnected: boolean;
  vehicleCommunicating: boolean;
  ignition: IgnitionState;
  voltage: VoltageReading | null;
  stale: boolean;
  observedAt: string | null;
  adapter: AdapterIdentity | null;
  mock: boolean;
};

export type ModuleCapability = {
  key: string;
  name: string;
  order: number;
  orderEvidence: string | null;
  orderUnknown: boolean;
  support: "standard-obd" | "unsupported";
  ops: readonly string[];
  clearScope: "functional-mode04" | "none";
};

export type ModuleScanStatus = "success" | "none" | "failed" | "not-supported";
export type SourcePage = { model?: string; page?: number; file?: string; kind?: string };
export type ManualHit = {
  code: string;
  title: string;
  description: string;
  checks: string[];
  source: string | null;
  sourcePages: SourcePage[];
  verification: string | null;
  moduleHint: string | null;
  applicability?: string;
  sourceModels?: string[];
};

export type ScannedDtc = ObdCode & {
  moduleKey: string;
  ecu: string;
  manual: ManualHit | null;
  manualFallback: string | null;
};

export type ModuleScan = {
  moduleKey: string;
  name: string;
  status: ModuleScanStatus;
  ecu: string | null;
  orderUnknown: boolean;
  dtcs: ScannedDtc[];
  stored: string[];
  pending: string[];
  permanent: string[];
  modes: Record<string, ProductionOutcome | "missing">;
  raw: string[];
  detail: string | null;
};

export type ScanSnapshot = {
  id: number | null;
  at: string;
  vehicleKey: string;
  adapter: AdapterIdentity | null;
  modules: ModuleScan[];
};

export type ClearModuleOutcome = {
  moduleKey: string;
  name: string;
  result: "cleared" | "remaining" | "failed" | "unknown" | "not-attempted";
  ecuPositive: boolean;
  detail: string | null;
};

export type ClearReport = {
  preClearId?: number;
  pre?: ScanSnapshot;
  at: string;
  transportOk: boolean;
  ecuPositive: boolean;
  raw: string;
  outcome: ProductionOutcome;
  scope: string;
  modules: ClearModuleOutcome[];
  readback: ScanSnapshot | null;
};

export type EcuIdentity = {
  vehicleKey: string;
  moduleKey: string;
  name: string;
  ecuAddress: string | null;
  hardwareId: string | null;
  serial: string | null;
  softwareId: string | null;
  calibrationId: string | null;
  cvn: string | null;
  codingFingerprint: string | null;
  lastSuccessAt: string | null;
};

export type EcuChange = {
  vehicleKey: string;
  moduleKey: string;
  field: string;
  previous: string | null;
  next: string | null;
  observedAt: string;
};

export type Porsche981Reference = {
  schemaVersion: 1;
  moduleOrderComplete?: boolean;
  modules: Array<{ key: string; name: string; order: number; orderEvidence?: string | null }>;
  manualEntries: Array<{
    code: string;
    title?: string | null;
    description?: string | null;
    checks?: string[] | string | null;
    source?: string | null;
    sourcePages?: SourcePage[] | null;
    verification?: string | null;
    moduleHint?: string | null;
    applicability?: string;
    sourceModels?: string[];
  }>;
};

export type CodesJson = {
  codes?: Array<{
    code: string;
    models?: string[];
    title_zh?: string;
    context_zh?: string;
    verify?: string | null;
    pages?: SourcePage[];
  }>;
};

export const PENDING_UNSUPPORTED: ModuleCapability[] = [
  { key: "psm", name: "PSM", order: 1000, orderEvidence: null, orderUnknown: true, support: "unsupported", ops: [], clearScope: "none" },
  { key: "front-end", name: "前端电子装置", order: 1001, orderEvidence: null, orderUnknown: true, support: "unsupported", ops: [], clearScope: "none" },
  { key: "rear-end", name: "后端电子装置", order: 1002, orderEvidence: null, orderUnknown: true, support: "unsupported", ops: [], clearScope: "none" },
  { key: "pcm", name: "PCM", order: 1003, orderEvidence: null, orderUnknown: true, support: "unsupported", ops: [], clearScope: "none" },
  { key: "hvac", name: "空调", order: 1004, orderEvidence: null, orderUnknown: true, support: "unsupported", ops: [], clearScope: "none" },
  { key: "park", name: "驻车辅助", order: 1005, orderEvidence: null, orderUnknown: true, support: "unsupported", ops: [], clearScope: "none" },
];

export const MANUAL_FALLBACK = "未找到该模块的对应手册检查项";
const MX_RE = /mx\+|racechrono/i;
const VLINKER_RE = /vlinker/i;
const OBD_IDS = new Set(["7E8", "7E9", "7EA", "7EB", "7EC", "7ED", "7EE", "7EF"]);

export function isObdResponder(ecu: string): boolean {
  return OBD_IDS.has(ecu.toUpperCase());
}

export function classifyAdapter(port: string, friendlyName: string, pnpId: string | null): AdapterIdentity {
  const blob = `${friendlyName}\n${pnpId ?? ""}`;
  const occupied = MX_RE.test(blob);
  return {
    port,
    friendlyName,
    pnpId,
    occupied,
    occupiedReason: occupied ? "MX+ 由 RaceChrono 占用，不自动选择" : null,
    preferred: VLINKER_RE.test(friendlyName) && !occupied,
  };
}

export function classifyElmBody(body: string): string {
  const t = body.replace(/\0/g, "").trim().toUpperCase();
  if (!t) return "prompt";
  if (t === "OK") return "ok";
  if (t === "NO DATA") return "no-data";
  if (t === "STOPPED") return "stopped";
  if (t === "?") return "unknown-at";
  if (t.includes("UNABLE TO CONNECT") || t === "UNABLETOCONNECT") return "unable";
  if (t.includes("BUS ERROR") || t.includes("CAN ERROR")) return "bus-error";
  if (t === "ERROR") return "at-error";
  return "data";
}

export function parseAtrv(raw: string): number | null {
  const body = raw.replace(/>/g, "").split(/[\r\n]+/).map(s => s.trim()).filter(s => s && s.toUpperCase() !== "ATRV");
  const m = body.length === 1 ? /^(\d+(?:\.\d+)?)\s*V$/i.exec(body[0]) : null;
  if (!m) return null;
  const v = Number(m[1]);
  return Number.isFinite(v) && v >= 0 && v < 30 ? v : null;
}

export function assertAtzBanner(raw: string): void {
  const t = raw.replace(/>/g, "").toUpperCase();
  if (t.includes("BUS ERROR") || t.includes("CAN ERROR") || t.includes("UNABLE TO CONNECT") || t.includes("?")) {
    throw new Error("obd_at_failed:ATZ");
  }
  if (!raw.trimEnd().endsWith(">") || !/(?:ELM327|STN\d*|VLINKER)\s+V?\d/i.test(t)) throw new Error("obd_at_failed:ATZ");
}

export function assertAtOk(command: string, raw: string): void {
  const lines = raw.replace(/>/g, "").split(/[\r\n]+/).map((l) => l.trim()).filter(Boolean);
  const kinds = lines.map(classifyElmBody);
  if (kinds.includes("unknown-at") || kinds.includes("bus-error") || kinds.includes("at-error") || kinds.includes("unable") || kinds.includes("stopped")) {
    throw new Error("obd_at_failed:" + command);
  }
  if (!kinds.includes("ok")) throw new Error("obd_at_failed:" + command);
  if (!raw.trimEnd().endsWith(">") || lines.some(l => l.toUpperCase() !== "OK" && l.replace(/\s/g, "").toUpperCase() !== command)) throw new Error("obd_at_failed:" + command);
}

export type PerEcuResult = { ecu: string; ok: boolean; detail: string | null; dtcs: ObdCode[]; vin?: string | null; calibrationId?: string | null; cvn?: string | null; rpm?: number | null; voltage?: number | null; value?: number };

export function parsePciObdReplies(raw: string, command: string): { replies: ObdReply[]; errors: Record<string, string> } {
  if (!raw.trimEnd().endsWith(">")) throw new Error("incomplete_prompt");
  const errors: Record<string, string> = {};
  const grouped = new Map<string, string[]>();
  for (const original of raw.replace(/>/g, "").split(/[\r\n]+/)) {
    const line = original.trim();
    if (!line || line.replace(/\s/g, "").toUpperCase() === command || line === "SEARCHING...") continue;
    const kind = classifyElmBody(line);
    if (kind !== "data") continue;
    const match = /^([0-9A-F]{3}|[0-9A-F]{8})\s+((?:[0-9A-F]{2}\s*)+)$/i.exec(line);
    if (!match) { errors["?"] = "headerless_or_invalid"; continue; }
    const ecu = match[1].toUpperCase();
    if (!isObdResponder(ecu)) { errors[ecu] = "unexpected_can_id"; continue; }
    const list = grouped.get(ecu) ?? [];
    list.push(line);
    grouped.set(ecu, list);
  }
  const replies: ObdReply[] = [];
  for (const [ecu, lines] of grouped) {
    try {
      const parsed = parseCanReplies(lines.join("\r") + "\r>", command);
      for (const r of parsed) {
        if (!isObdResponder(r.ecu)) { errors[r.ecu] = "unexpected_can_id"; continue; }
        replies.push(r);
      }
    } catch (e) {
      errors[ecu] = e instanceof Error ? e.message : "invalid";
    }
  }
  return { replies, errors };
}

function dtcBlock(ecu: string, payload: number[], service: number): { dtcs: ObdCode[]; error: string | null } {
  if (payload[0] !== service + 0x40) return { dtcs: [], error: "unexpected_service" };
  if (payload.length < 2) return { dtcs: [], error: "short_dtc" };
  const count = payload[1];
  const rest = payload.slice(2);
  if (count * 2 > rest.length) return { dtcs: [], error: "dtc_count_mismatch" };
  if (rest.slice(count * 2).some(b => b !== 0)) return { dtcs: [], error: "dtc_trailing_data" };
  const dtcs: ObdCode[] = [];
  for (let i = 0; i < count; i++) {
    const a = rest[i * 2], b = rest[i * 2 + 1];
    if (!a && !b) return { dtcs: [], error: "dtc_zero_inside_count" };
    if (a || b) dtcs.push({ ecu, code: decodeDtc(a, b), status: service === 3 ? "stored" : service === 7 ? "pending" : "permanent" });
  }
  return { dtcs, error: null };
}

function mode09Text(payload: number[], pid: number): string | null {
  if (payload[0] !== 0x49 || payload[1] !== pid) return null;
  const count = payload[2];
  if (!count || count > 255) return null;
  const bytes = payload.slice(3);
  if (pid === 2) {
    const value = String.fromCharCode(...payload.slice(3));
    return count === 1 && /^[A-HJ-NPR-Z0-9]{17}$/.test(value) ? value : null;
  }
  if (pid === 4) {
    if (bytes.length !== count * 16 || bytes.some(b => b !== 0 && (b < 32 || b >= 127))) return null;
    const records = Array.from({length: count}, (_, i) => String.fromCharCode(...bytes.slice(i * 16, i * 16 + 16)).replace(/\0/g, "").trim());
    return records.every(Boolean) ? records.join(" | ") : null;
  }
  if (pid === 6 && bytes.length === count * 4) return Array.from({length: count}, (_, i) => bytes.slice(i * 4, i * 4 + 4).map(b => b.toString(16).padStart(2, "0")).join("").toUpperCase()).join(" | ");
  return null;
}

export type ProductionDecode = {
  command: string;
  raw: string;
  outcome: ProductionOutcome;
  detail: string | null;
  replies: ObdReply[];
  dtcs: ObdCode[];
  perEcu: PerEcuResult[];
  rpm: number | null;
  speed: number | null;
  voltage: number | null;
  voltageSource: "PID0142" | null;
  vin: string | null;
  calibrationId: string | null;
  cvn: string | null;
  supported: Record<string, number[]>;
};

export function decodeProductionObservation(command: string, raw: string, _framing: ElmFraming = "pci"): ProductionDecode {
  assertTrustedCommand(command);
  const result: ProductionDecode = {
    command, raw, outcome: "ok", detail: null, replies: [], dtcs: [], perEcu: [],
    rpm: null, speed: null, voltage: null, voltageSource: null,
    vin: null, calibrationId: null, cvn: null, supported: {},
  };
  const body = raw.replace(/>/g, "");
  const lines = body.split(/[\r\n]+/).map((l) => l.trim()).filter(Boolean);
  const kinds = lines.map(classifyElmBody);
  if (kinds.includes("bus-error")) { result.outcome = "bus-error"; result.detail = "BUS ERROR"; return result; }
  if (kinds.includes("at-error")) { result.outcome = "at-error"; result.detail = "ERROR"; return result; }
  if (kinds.includes("unable")) { result.outcome = "unable"; result.detail = "UNABLE TO CONNECT"; return result; }
  if (kinds.includes("stopped")) { result.outcome = "unknown"; result.detail = "STOPPED"; return result; }
  if (raw.trimEnd().endsWith(">") && kinds.includes("no-data") && kinds.every((k) => k === "no-data" || k === "prompt")) {
    result.outcome = "no-data"; result.detail = "NO DATA"; return result;
  }
  let parsed: ReturnType<typeof parsePciObdReplies>;
  try { parsed = parsePciObdReplies(raw, command); }
  catch (e) { return {...result, outcome:"invalid", detail: e instanceof Error ? e.message : "invalid"}; }
  const { replies, errors } = parsed;
  result.replies = replies;
  const service = command.startsWith("AT") ? -1 : parseInt(command.slice(0, 2), 16);
  if (command === "04") {
    result.perEcu = replies.map((r) => ({ ecu: r.ecu, ok: r.payload.length === 1 && r.payload[0] === 0x44, detail: r.payload.length === 1 && r.payload[0] === 0x44 ? null : "clear_not_positive", dtcs: [] }));
    for (const [ecu, err] of Object.entries(errors)) result.perEcu.push({ ecu, ok: false, detail: err, dtcs: [] });
    result.outcome = result.perEcu.some((p) => p.ok) ? "ok" : replies.length ? "invalid" : Object.keys(errors).length ? "invalid" : "invalid";
    return result;
  }
  let anyOk = false;
  for (const { ecu, payload } of replies) {
    const one: PerEcuResult = { ecu, ok: false, detail: null, dtcs: [] };
    try {
      if ([3, 7, 10].includes(service)) {
        const block = dtcBlock(ecu, payload, service);
        if (block.error) { one.detail = block.error; }
        else { one.ok = true; one.dtcs = block.dtcs; result.dtcs.push(...block.dtcs); anyOk = true; }
      } else if (service === 1) {
        const pid = parseInt(command.slice(2, 4), 16);
        if (payload[0] !== 0x41 || payload[1] !== pid) throw new Error("unexpected_pid");
        const bytes = payload.slice(2);
        if ([0, 0x20, 0x40].includes(pid)) {
          if (bytes.length !== 4) throw new Error("invalid_support_length");
          result.supported[ecu] = Array.from({ length: 32 }, (_, i) => i + 1).filter((p) => (bytes[(p - 1) >> 3] & (1 << (7 - ((p - 1) % 8)))) !== 0).map(p => p + pid);
        } else {
          const value = decodeAnalysisPid(pid, bytes);
          if (value == null) throw new Error('invalid_pid_length');
          one.value = value;
          if (pid === 0x0c) { result.rpm = value; one.rpm = value; }
          if (pid === 0x0d) result.speed = value;
          if (pid === 0x42) { result.voltage = value; result.voltageSource = 'PID0142'; one.voltage = value; }
        }
        one.ok = true; anyOk = true;
      } else if (service === 9) {
        const pid = parseInt(command.slice(2, 4), 16);
        const text = mode09Text(payload, pid);
        if (text == null) throw new Error("invalid_identity");
        if (pid === 2) { result.vin = text ?? result.vin; one.vin = text; }
        if (pid === 4) { result.calibrationId = text ?? result.calibrationId; one.calibrationId = text; }
        if (pid === 6) { result.cvn = text ?? result.cvn; one.cvn = text; }
        one.ok = true; anyOk = true;
      } else throw new Error("unexpected_service");
    } catch (e) {
      one.detail = e instanceof Error ? e.message : "invalid";
    }
    result.perEcu.push(one);
  }
  for (const [ecu, err] of Object.entries(errors)) {
    if (!result.perEcu.some((p) => p.ecu === ecu)) result.perEcu.push({ ecu, ok: false, detail: err, dtcs: [] });
  }
  if (!anyOk) {
    result.outcome = kinds.includes("no-data") ? "no-data" : "invalid";
    result.detail = result.perEcu[0]?.detail ?? "missing_ecu_response";
  }
  if (service === 9 && command === "0902") {
    const vins = new Set(result.perEcu.filter(p => p.ok && p.vin).map(p => p.vin));
    if (vins.size > 1) { result.vin = null; result.detail = "conflicting_vins"; }
  }
  return result;
}

export function ignitionFromRpm(rpm: number | null): IgnitionState {
  return rpm != null && rpm > 0 ? "running" : "unknown";
}
export function vehicleFromVoltage(_voltage: number | null): boolean { return false; }

export function responderKey(ecu: string): string { return `obd-can:${ecu}`; }
export function responderName(ecu: string): string { return `排放诊断单元 ${ecu}`; }

export function buildCapabilityRegistry(reference: Porsche981Reference | null): ModuleCapability[] {
  const refMods = (reference?.modules ?? []).map((m) => ({
    key: m.key,
    name: m.name,
    order: m.order,
    orderEvidence: m.orderEvidence ?? null,
    orderUnknown: false,
    support: "unsupported" as const,
    ops: [],
    clearScope: "none" as const,
  }));
  const have = new Set(refMods.map((m) => m.key));
  const tail = PENDING_UNSUPPORTED.filter((p) => !have.has(p.key));
  return [...refMods, ...tail];
}

export function formatSourcePages(pages: SourcePage[] | null | undefined): string {
  const p981 = (pages ?? []).filter((p) => !p.model || p.model === "981");
  if (!p981.length) return "";
  const file = p981[0]?.file ?? "";
  const nums = p981.map((p) => p.page).filter((n) => typeof n === "number");
  return `${file} 第 ${nums.join("、")} 页`;
}

function asChecks(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean);
  if (typeof v === "string" && v.trim()) return [v.trim()];
  return [];
}

export function filter981CodesJson(doc: CodesJson, code: string): ManualHit | null {
  const row = doc.codes?.find((c) => c.code.toUpperCase() === code.toUpperCase());
  if (!row) return null;
  if (!(row.models ?? []).map(String).some(m => m === '981' || m === '982')) return null;
  const pages = (row.pages ?? []).filter((p) => p.model === "981" || p.model === "982");
  if (!pages.length) return null;
  return {
    code: row.code.toUpperCase(),
    title: row.title_zh ?? "",
    description: row.context_zh ?? "",
    checks: [],
    source: pages[0]?.file ?? null,
    sourcePages: pages,
    verification: row.verify ?? null,
    moduleHint: null,
    sourceModels: (row.models ?? []).filter(m => m === '981' || m === '982'),
    applicability: (row.models ?? []).includes('981') ? '具体控制单元、故障子类型及本车适用条件需核实' : '扩充收录；本车适用性待核实',
  };
}

export function lookup981Manual(code: string, reference: Porsche981Reference | null, codes?: CodesJson | null): ManualHit | null {
  const fromRef = reference?.manualEntries?.find((e) => e.code.toUpperCase() === code.toUpperCase());
  if (fromRef) {
    return {
      code: fromRef.code.toUpperCase(),
      title: fromRef.title ?? "",
      description: fromRef.description ?? "",
      checks: asChecks(fromRef.checks),
      source: fromRef.source ?? null,
      sourcePages: (fromRef.sourcePages ?? []).filter((p) => !p.model || p.model === "981" || p.model === "982"),
      verification: fromRef.verification ?? null,
      moduleHint: fromRef.moduleHint ?? null,
      applicability: fromRef.applicability ?? '具体控制单元、故障子类型及本车适用条件需核实',
      sourceModels: fromRef.sourceModels ?? [...new Set((fromRef.sourcePages ?? []).map(p => p.model).filter((m): m is string => Boolean(m)))],
    };
  }
  if (codes) return filter981CodesJson(codes, code);
  return null;
}

export function attachManual(dtc: ObdCode, moduleKey: string, reference: Porsche981Reference | null, codes?: CodesJson | null): ScannedDtc {
  const manual = lookup981Manual(dtc.code, reference, codes);
  const hasChecks = Boolean(manual?.checks.length);
  return { ...dtc, moduleKey, manual, manualFallback: hasChecks ? null : MANUAL_FALLBACK };
}

const IDENTITY_FIELDS = ["hardwareId", "serial", "softwareId", "calibrationId", "cvn", "codingFingerprint"] as const;

export function applyEcuUpsert(existing: EcuIdentity | null, incoming: Partial<EcuIdentity> & Pick<EcuIdentity, "vehicleKey" | "moduleKey" | "name">, now: string): { row: EcuIdentity; changes: EcuChange[] } {
  if (existing && (existing.vehicleKey !== incoming.vehicleKey || existing.moduleKey !== incoming.moduleKey)) throw new Error("ecu_identity_scope_mismatch");
  const empty = (): EcuIdentity => ({
    vehicleKey: incoming.vehicleKey, moduleKey: incoming.moduleKey, name: incoming.name,
    ecuAddress: incoming.ecuAddress ?? null,
    hardwareId: null, serial: null, softwareId: null, calibrationId: null, cvn: null, codingFingerprint: null,
    lastSuccessAt: null,
  });
  const base = existing ?? empty();
  const row: EcuIdentity = { ...base, name: incoming.name || base.name, ecuAddress: incoming.ecuAddress ?? base.ecuAddress };
  const changes: EcuChange[] = [];
  for (const field of IDENTITY_FIELDS) {
    const next = incoming[field];
    if (next == null || next === "") continue;
    const previous = base[field];
    if (previous != null && previous !== next) {
      changes.push({ vehicleKey: row.vehicleKey, moduleKey: row.moduleKey, field, previous, next, observedAt: now });
    }
    row[field] = next;
  }
  if (incoming.lastSuccessAt) row.lastSuccessAt = incoming.lastSuccessAt;
  else if (IDENTITY_FIELDS.some((f) => incoming[f])) row.lastSuccessAt = now;
  return { row, changes };
}

export function mergeEcuRows(into: EcuIdentity, from: EcuIdentity, now: string): { row: EcuIdentity; changes: EcuChange[] } {
  const newer = Date.parse(from.lastSuccessAt ?? "") > Date.parse(into.lastSuccessAt ?? "");
  const incoming = { ...from, vehicleKey: into.vehicleKey, moduleKey: into.moduleKey };
  if (!newer && into.lastSuccessAt) {
    for (const field of IDENTITY_FIELDS) incoming[field] = into[field] ?? from[field];
    incoming.lastSuccessAt = into.lastSuccessAt;
    incoming.ecuAddress = into.ecuAddress ?? from.ecuAddress;
  }
  return applyEcuUpsert(into, incoming, now);
}

export function resolveVehicleKey(opts: { observedVin: string | null; knownVins: string[]; currentKey: string | null; sessionNonce: string }): { vehicleKey: string; reboundFrom: string | null } {
  const vin = opts.observedVin;
  if (vin) {
    const from = opts.currentKey?.startsWith("unknown:") ? opts.currentKey : null;
    return { vehicleKey: vin, reboundFrom: from && from !== vin ? from : null };
  }
  if (opts.currentKey?.startsWith("unknown:")) return { vehicleKey: opts.currentKey, reboundFrom: null };
  return { vehicleKey: `unknown:${opts.sessionNonce}`, reboundFrom: null };
}

export function headerIdle(prev: LiveHeader | null, extra: Partial<LiveHeader>): LiveHeader {
  return {
    adapterConnected: false,
    vehicleCommunicating: false,
    ignition: "unknown",
    voltage: null,
    stale: false,
    observedAt: null,
    adapter: prev?.adapter ?? null,
    mock: prev?.mock ?? false,
    ...extra,
  };
}

export function headerFresh(header: LiveHeader, now: number, maxAgeMs: number): boolean {
  if (!header.adapterConnected || !header.vehicleCommunicating || header.stale) return false;
  if (!header.observedAt) return false;
  const t = Date.parse(header.observedAt);
  return Number.isFinite(t) && now >= t && now - t <= maxAgeMs;
}
