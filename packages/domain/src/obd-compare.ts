const VIN_RE = /^[A-HJ-NPR-Z0-9]{17}$/;
const GENERIC_UNKNOWN = /^(unknown:|unbound:|anon:)/i;
const MAX_JSON = 200_000;
const MAX_DTCS = 200;
const COVERED = new Set(["success-dtc", "success-none"]);
export const LOCAL_GARAGE_BINDING = "local:garage-981";

export const DIAG_SOURCES = ["simulation", "live", "manual", "legacy-unknown"] as const;
export type DiagSource = (typeof DIAG_SOURCES)[number];
export const IDENTITY_KINDS = ["vin", "unbound", "unknown", "user-declared"] as const;
export type IdentityKind = (typeof IDENTITY_KINDS)[number];
export const MODULE_COVERAGES = [
  "success-dtc",
  "success-none",
  "failed",
  "unsupported",
  "incomplete",
  "not-scanned",
] as const;
export type ModuleCoverage = (typeof MODULE_COVERAGES)[number];
export type CompareKind = "new" | "still" | "gone" | "status-changed" | "not-comparable";

export type SnapshotDtc = {
  /** Raw DTC identity (dtcHex). Display SAE code is separate. */
  code: string;
  dtcHex: string;
  statusHex: string | null;
  subtype: string | null;
  status: string | null;
  display: string | null;
};

export type SnapshotModule = {
  moduleKey: string;
  name: string;
  ecuVariant: string | null;
  coverage: ModuleCoverage;
  dtcs: SnapshotDtc[];
  rawRef: string | null;
};

export type DiagSnapshot = {
  id: number | null;
  at: string;
  source: DiagSource;
  identityKind: IdentityKind;
  vehicleKey: string | null;
  identityLabel: string;
  completeness: "complete" | "partial" | "failed";
  modules: SnapshotModule[];
  fingerprint: string;
  captureEventId: string;
  boundAt?: string | null;
  boundNote?: string | null;
};

export type CompareRow = {
  kind: CompareKind;
  moduleKey: string;
  code: string;
  dtcHex?: string;
  subtype: string | null;
  beforeStatus: string | null;
  afterStatus: string | null;
  note: string;
};

export type CompareOk = {
  ok: true;
  beforeId: number;
  afterId: number;
  rows: CompareRow[];
  disclaimer: string;
  context?: string;
};

export type CompareFail = { ok: false; error: string; reason: string };

const KIND_TO_COVERAGE: Record<string, ModuleCoverage> = {
  dtc: "success-dtc",
  "dtc-present": "success-dtc",
  noDtc: "success-none",
  "no-dtc": "success-none",
  commFail: "failed",
  "comm-fail": "failed",
  identity: "failed",
  "identity-mismatch": "failed",
  cancelled: "incomplete",
  decodeError: "incomplete",
  "decode-error": "incomplete",
  partial: "incomplete",
  pendingAdapt: "unsupported",
  "pending-adapt": "unsupported",
  notInstalled: "unsupported",
  "not-installed": "unsupported",
  rejected: "failed",
  scanning: "incomplete",
  unscanned: "not-scanned",
};

export const SOURCE_ZH: Record<DiagSource, string> = {
  simulation: "模拟",
  live: "实车",
  manual: "手工",
  "legacy-unknown": "遗留（来源不明）",
};

export const COVERAGE_ZH: Record<ModuleCoverage, string> = {
  "success-dtc": "合格覆盖·有码",
  "success-none": "合格覆盖·无码",
  failed: "失败",
  unsupported: "不支持",
  incomplete: "不完整",
  "not-scanned": "未扫描",
};

export const IDENTITY_ZH: Record<IdentityKind, string> = {
  vin: "实测车架号",
  unbound: "未绑定",
  unknown: "身份不明",
  "user-declared": "用户声明绑定本车库（非实测 VIN）",
};

export function isValidVin(v: unknown): v is string {
  return typeof v === "string" && VIN_RE.test(v);
}

export function parseJsonBounded(text: string, max = MAX_JSON): unknown {
  if (typeof text !== "string" || text.length > max) throw new Error("diag_json_too_large");
  return JSON.parse(text, (k, v) => {
    if (k === "__proto__" || k === "constructor" || k === "prototype") return undefined;
    return v;
  });
}

function str(v: unknown, max = 400): string {
  if (v != null && typeof v !== "string" && typeof v !== "number") throw new Error("diag_invalid_field");
  const text = String(v ?? "");
  if (text.length > max || text.includes("\0")) throw new Error("diag_invalid_field");
  return text;
}

function dtcKey(moduleKey: string, d: SnapshotDtc): string {
  return `${moduleKey}\0${d.dtcHex || d.code}`;
}

function variantKey(m: SnapshotModule): string {
  return `${m.moduleKey}\0${m.ecuVariant ?? ""}`;
}

export function fingerprintSnapshot(s: Omit<DiagSnapshot, "id" | "fingerprint"> & { fingerprint?: string }): string {
  return `event:${s.captureEventId || ""}`;
}

export function normalizeRecordDtc(rec: unknown): SnapshotDtc | null {
  if (!rec || typeof rec !== "object") return null;
  const r = rec as Record<string, unknown>;
  const dtcHex = str(r.dtcHex || r.rawHex || "", 32).toUpperCase();
  const display = str(r.displayCode || r.code || dtcHex, 32).toUpperCase();
  const identity = dtcHex || display;
  if (!identity || identity === "?") return null;
  const statusHex = r.statusHex != null && String(r.statusHex) !== "" ? str(r.statusHex, 16).toUpperCase() : null;
  const status =
    r.observedStatusText != null
      ? str(r.observedStatusText, 80)
      : r.status != null
        ? str(r.status, 80)
        : statusHex;
  return {
    code: identity,
    dtcHex: identity,
    statusHex,
    subtype: r.subtype == null ? null : str(r.subtype, 32),
    status: status || null,
    display: display || identity,
  };
}

function observedVin(final: Record<string, unknown> | null | undefined): string | null {
  if (!final) return null;
  if (isValidVin(final.vin)) return String(final.vin);
  if (isValidVin(final.vehicleKey) && !GENERIC_UNKNOWN.test(String(final.vehicleKey))) return String(final.vehicleKey);
  return null;
}

export function identityFieldsFromFinal(final: Record<string, unknown> | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  const rows = Array.isArray(final?.results) ? final!.results : [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const r = row as Record<string, unknown>;
    if (r.role !== "identity" || r.ok !== true || !r.field) continue;
    const dec = r.decoded && typeof r.decoded === "object" ? (r.decoded as Record<string, unknown>) : null;
    const text = dec && (dec.text ?? dec.value);
    if (text != null && String(text).trim()) out[String(r.field)] = String(text).trim();
  }
  return out;
}

export function variantFromIdentityFields(fields: Record<string, string>): string | null {
  const keys = ["hardware", "software", "dsn", "porschePart", "hardwarePart", "system", "identification", "dataRecord"];
  const parts = keys.filter((k) => fields[k]).map((k) => `${k}=${fields[k]}`);
  return parts.length ? parts.join("|") : null;
}

export function identityFromCapture(final: Record<string, unknown> | null | undefined): {
  identityKind: IdentityKind;
  vehicleKey: string | null;
  identityLabel: string;
} {
  const vin = observedVin(final ?? undefined);
  if (vin) return { identityKind: "vin", vehicleKey: vin, identityLabel: vin };
  if (final && typeof final === "object") {
    return {
      identityKind: "unbound",
      vehicleKey: null,
      identityLabel: "未绑定车架号（本次读取未给出 VIN，未推断；需用户声明绑定后才能对比）",
    };
  }
  return { identityKind: "unknown", vehicleKey: null, identityLabel: "身份不明（未推断 VIN）" };
}

export function coverageFromKind(kind: unknown): ModuleCoverage {
  return KIND_TO_COVERAGE[String(kind || "")] || "incomplete";
}

export function eventIdFromResults(
  results: Array<{ doc?: { jobId?: string; final?: { runId?: unknown } }; classified?: { runId?: unknown } }>,
): string {
  const parts = results
    .map((r) => String(r.doc?.jobId || r.doc?.final?.runId || r.classified?.runId || ""))
    .filter(Boolean);
  if (!parts.length) throw new Error("diag_capture_event_required");
  return str(parts.join("|"), 240);
}

function applyRecords(coverage: ModuleCoverage, recs: unknown[]): SnapshotDtc[] {
  const dtcs = recs.map((r) => {
    const d = normalizeRecordDtc(r);
    if (!d) throw new Error("diag_invalid_dtc");
    return d;
  });
  if (new Set(dtcs.map((d) => d.dtcHex)).size !== dtcs.length) throw new Error("diag_duplicate_dtc");
  if (dtcs.length > MAX_DTCS) throw new Error("diag_too_many_dtcs");
  if (coverage === "success-none" && dtcs.length) throw new Error("diag_none_with_dtcs");
  if (coverage === "success-dtc" && !dtcs.length) throw new Error("diag_dtc_without_codes");
  return dtcs;
}

export function normalizeTopologyCapture(input: {
  at?: string;
  source: DiagSource;
  captureEventId?: string;
  results: Array<{
    nodeId: string;
    classified?: Record<string, unknown>;
    doc?: { jobId?: string; final?: Record<string, unknown> };
  }>;
  nodes: Array<{ id: string; label?: string; profileId?: string }>;
}): Omit<DiagSnapshot, "id"> {
  if (input.source === "live" && input.results.some((r) => r.classified?.simulated === true)) {
    throw new Error("diag_source_inconsistent");
  }
  const captureEventId = input.captureEventId || eventIdFromResults(input.results);
  const nodeById = new Map(input.nodes.map((n) => [n.id, n]));
  const firstFinal = input.results.find((r) => r.doc?.final)?.doc?.final as Record<string, unknown> | undefined;
  const ident2 = identityFromCapture(firstFinal ?? null);
  const modules: SnapshotModule[] = [];
  const seen = new Set<string>();
  const moduleKeys = new Set<string>();
  for (const row of input.results) {
    const node = nodeById.get(row.nodeId);
    const c = row.classified || {};
    const moduleKey = str(node?.profileId || row.nodeId, 80);
    if (moduleKeys.has(moduleKey)) throw new Error("diag_duplicate_module");
    moduleKeys.add(moduleKey);
    seen.add(row.nodeId);
    const recs = Array.isArray(c.records) ? c.records : [];
    const fields = identityFieldsFromFinal(row.doc?.final);
    const variant = variantFromIdentityFields(fields);
    const coverage = coverageFromKind(c.kind);
    modules.push({
      moduleKey,
      name: str(node?.label || moduleKey, 80),
      ecuVariant: variant,
      coverage,
      dtcs: applyRecords(coverage, recs),
      rawRef: str(c.runId || row.doc?.final?.runId || row.doc?.jobId || null, 80) || null,
    });
  }
  for (const n of input.nodes) {
    if (seen.has(n.id) || !n.profileId) continue;
    const moduleKey = str(n.profileId, 80);
    if (moduleKeys.has(moduleKey)) continue;
    modules.push({
      moduleKey,
      name: str(n.label || n.id, 80),
      ecuVariant: null,
      coverage: "not-scanned",
      dtcs: [],
      rawRef: null,
    });
  }
  const completeness: DiagSnapshot["completeness"] = modules.every((m) => COVERED.has(m.coverage))
    ? "complete"
    : modules.every((m) => m.coverage === "failed" || m.coverage === "unsupported")
      ? "failed"
      : "partial";
  const at = input.at && !Number.isNaN(Date.parse(input.at)) ? input.at : new Date().toISOString();
  const base = {
    at,
    source: input.source,
    ...ident2,
    completeness,
    modules,
    captureEventId,
  };
  return { ...base, fingerprint: fingerprintSnapshot(base) };
}

export function normalizeSessionFinal(input: {
  at?: string;
  source: DiagSource;
  profileId: string;
  name?: string;
  final: Record<string, unknown> | null;
  classifiedKind?: string;
  records?: unknown[];
  captureEventId?: string;
}): Omit<DiagSnapshot, "id"> {
  const ident = identityFromCapture(input.final);
  const kind = input.classifiedKind || (input.final?.status === "completed" ? "partial" : "failed");
  const recs = input.records ?? [];
  const fields = identityFieldsFromFinal(input.final);
  const variant = variantFromIdentityFields(fields);
  const coverage = coverageFromKind(kind);
  const runId = str(input.final?.runId, 80);
  const captureEventId = input.captureEventId || runId;
  if (!captureEventId) throw new Error("diag_capture_event_required");
  const modules: SnapshotModule[] = [
    {
      moduleKey: str(input.profileId, 80),
      name: str(input.name || input.profileId, 80),
      ecuVariant: variant,
      coverage,
      dtcs: applyRecords(coverage, recs),
      rawRef: runId || null,
    },
  ];
  const completeness: DiagSnapshot["completeness"] = COVERED.has(coverage) ? "complete" : coverage === "failed" ? "failed" : "partial";
  const at = input.at && !Number.isNaN(Date.parse(input.at)) ? input.at : new Date().toISOString();
  const base = { at, source: input.source, ...ident, completeness, modules, captureEventId };
  return { ...base, fingerprint: fingerprintSnapshot(base) };
}

function assertSnapshot(s: DiagSnapshot, label: string): void {
  if (!s || typeof s !== "object") throw new Error(`${label}_missing`);
  if (!Number.isSafeInteger(s.id) || (s.id as number) < 1) throw new Error(`${label}_id`);
  if (!s.at || Number.isNaN(Date.parse(s.at))) throw new Error(`${label}_time`);
  if (!(DIAG_SOURCES as readonly string[]).includes(s.source)) throw new Error(`${label}_source`);
  if (!(IDENTITY_KINDS as readonly string[]).includes(s.identityKind)) throw new Error(`${label}_identity`);
}

export function compareSnapshots(before: DiagSnapshot, after: DiagSnapshot): CompareOk | CompareFail {
  try {
    assertSnapshot(before, "before");
    assertSnapshot(after, "after");
  } catch (e) {
    return { ok: false, error: String(e), reason: String(e) };
  }
  if (before.id === after.id) {
    return { ok: false, error: "不能选择同一条记录", reason: "same-snapshot" };
  }
  const t0 = Date.parse(before.at);
  const t1 = Date.parse(after.at);
  if (!(t0 < t1)) {
    return { ok: false, error: "时间顺序无效：维修后记录必须晚于维修前", reason: "chronology" };
  }
  if (before.source !== after.source) {
    return { ok: false, error: `来源不一致（前 ${SOURCE_ZH[before.source]} / 后 ${SOURCE_ZH[after.source]}），模拟与实车记录不可混比`, reason: "source-mismatch" };
  }
  if (before.source === "legacy-unknown" || after.source === "legacy-unknown") {
    return { ok: false, error: "来源不明的遗留记录不能参与对比（未假定为实车）", reason: "provenance-unknown" };
  }
  const qualified = new Set<IdentityKind>(["vin", "user-declared"]);
  if (!qualified.has(before.identityKind) || !qualified.has(after.identityKind)) {
    return { ok: false, error: "未限定为同一车辆：请先把记录用户声明绑定到本车库车辆（非实测 VIN），未绑定/不明身份不能对比", reason: "identity-unqualified" };
  }
  if (before.identityKind !== after.identityKind) {
    return { ok: false, error: "车辆身份类型不一致", reason: "identity-mismatch" };
  }
  if (before.identityKind === "vin") {
    if (!isValidVin(before.vehicleKey) || !isValidVin(after.vehicleKey) || before.vehicleKey !== after.vehicleKey) {
      return { ok: false, error: "车架号不一致或无效", reason: "vin-mismatch" };
    }
  } else {
    const a = String(before.vehicleKey || "");
    const b = String(after.vehicleKey || "");
    if (!a || !b || a !== b || GENERIC_UNKNOWN.test(a)) {
      return { ok: false, error: "用户声明绑定键不一致或无效", reason: "identity-mismatch" };
    }
  }

  const beforeMods = new Map(before.modules.map((m) => [m.moduleKey, m]));
  const afterMods = new Map(after.modules.map((m) => [m.moduleKey, m]));
  const rows: CompareRow[] = [];
  const keys = new Set([...beforeMods.keys(), ...afterMods.keys()]);

  for (const moduleKey of keys) {
    const b = beforeMods.get(moduleKey);
    const a = afterMods.get(moduleKey);
    if (b && a && (b.ecuVariant || a.ecuVariant) && variantKey(b) !== variantKey(a)) {
      rows.push({
        kind: "not-comparable",
        moduleKey,
        code: "*",
        subtype: null,
        beforeStatus: null,
        afterStatus: null,
        note: "控制单元变体身份不同或一侧缺失变体证据，不作为同一单元对比",
      });
      continue;
    }
    const bOk = !!b && COVERED.has(b.coverage);
    const aOk = !!a && COVERED.has(a.coverage);
    const bMap = new Map((b?.dtcs || []).map((d) => [dtcKey(moduleKey, d), d]));
    const aMap = new Map((a?.dtcs || []).map((d) => [dtcKey(moduleKey, d), d]));
    const codes = new Set([...bMap.keys(), ...aMap.keys()]);
    if (!bOk || !aOk) {
      if (!codes.size) {
        rows.push({
          kind: "not-comparable",
          moduleKey,
          code: "—",
          subtype: null,
          beforeStatus: b?.coverage ?? "absent",
          afterStatus: a?.coverage ?? "absent",
          note:
            !aOk
              ? "后一次该单元读取失败、不完整、不支持或未覆盖，不能把故障码视为消失"
              : "前一次该单元无合格覆盖，不能对比",
        });
      }
      for (const k of codes) {
        const bd = bMap.get(k);
        const ad = aMap.get(k);
        rows.push({
          kind: "not-comparable",
          moduleKey,
          code: (ad || bd)!.display || (ad || bd)!.code,
          dtcHex: (ad || bd)!.dtcHex,
          subtype: (ad || bd)!.subtype,
          beforeStatus: bd?.statusHex ?? bd?.status ?? b?.coverage ?? null,
          afterStatus: ad?.statusHex ?? ad?.status ?? a?.coverage ?? null,
          note: "该单元两侧并非都合格覆盖（含失败/缺失/不支持/不完整），不可比",
        });
      }
      continue;
    }
    for (const k of codes) {
      const bd = bMap.get(k);
      const ad = aMap.get(k);
      if (bd && ad) {
        const statusChanged = (bd.statusHex || bd.status || "") !== (ad.statusHex || ad.status || "");
        rows.push({
          kind: statusChanged ? "status-changed" : "still",
          moduleKey,
          code: bd.display || bd.code,
          dtcHex: bd.dtcHex,
          subtype: ad.subtype ?? bd.subtype,
          beforeStatus: bd.statusHex ?? bd.status,
          afterStatus: ad.statusHex ?? ad.status,
          note: statusChanged ? "仍观察到，状态字节有变化" : "两次都观察到",
        });
      } else if (ad && !bd) {
        rows.push({
          kind: "new",
          moduleKey,
          code: ad.display || ad.code,
          dtcHex: ad.dtcHex,
          subtype: ad.subtype,
          beforeStatus: null,
          afterStatus: ad.statusHex ?? ad.status,
          note: "后一次新出现（合格覆盖范围内）",
        });
      } else if (bd && !ad) {
        rows.push({
          kind: "gone",
          moduleKey,
          code: bd.display || bd.code,
          dtcHex: bd.dtcHex,
          subtype: bd.subtype,
          beforeStatus: bd.statusHex ?? bd.status,
          afterStatus: null,
          note: "后一次该单元合格覆盖下未再观察到。这只是两次观察之差，清码或间歇故障也会消失，不能称为已修好。",
        });
      }
    }
  }

  return {
    ok: true,
    beforeId: before.id as number,
    afterId: after.id as number,
    rows,
    context: `${SOURCE_ZH[before.source]} · ${IDENTITY_ZH[before.identityKind]} · ${before.vehicleKey} · ${before.at} → ${after.at}`,
    disclaimer:
      "对比描述两次诊断观察，不把故障码消失称为已修复。清码、间歇故障或未覆盖的读取都会造成表面消失。",
  };
}

export function formatCompareReport(result: CompareOk, note: string): string {
  const lines = [
    `诊断对比 #${result.beforeId} → #${result.afterId}`,
    result.disclaimer,
    result.context || "",
    note ? `维修/操作备注：${note}` : "",
    ...result.rows.map((r) => {
      const label = {
        new: "新出现",
        still: "仍存在",
        gone: "未再观察到",
        "status-changed": "状态变化",
        "not-comparable": "不可比",
      }[r.kind];
      const st = `${r.beforeStatus || "—"} → ${r.afterStatus || "—"}`;
      return `${label} · ${r.moduleKey} · ${r.code}${r.dtcHex ? ` [${r.dtcHex}]` : ""}${r.subtype ? ` 子码 ${r.subtype}` : ""}（${st}） · ${r.note}`;
    }),
  ];
  return lines.filter(Boolean).join("\n");
}

export function declaredBindingPatch(note: string, bindingKey = LOCAL_GARAGE_BINDING): {
  identityKind: "user-declared";
  vehicleKey: string;
  identityLabel: string;
  boundAt: string;
  boundNote: string;
} {
  const n = String(note || "").trim().slice(0, 500);
  if (!n) throw new Error("diag_bind_note_required");
  if (!bindingKey || GENERIC_UNKNOWN.test(bindingKey)) throw new Error("diag_bind_key_invalid");
  return {
    identityKind: "user-declared",
    vehicleKey: bindingKey,
    identityLabel: IDENTITY_ZH["user-declared"],
    boundAt: new Date().toISOString(),
    boundNote: n,
  };
}
