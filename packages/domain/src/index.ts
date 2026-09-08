export type IntervalStatus = "ok" | "dueSoon" | "overdue" | "no_baseline";

export type IntervalInput = {
  nowKm: number;
  /** Last replacement odometer; omit / null → no_baseline (do not invent delivery date). */
  replacedAtKm?: number | null;
  /** Last replacement ISO date YYYY-MM-DD; omit / null → no_baseline. */
  replacedAtDate?: string | null;
  intervalKm?: number | null;
  intervalMonths?: number | null;
  avgKmPerDay?: number | null;
  /** days before due to flag dueSoon (default 30) */
  soonDays?: number;
  /** km before due to flag dueSoon (default 500) */
  soonKm?: number;
  nowDate?: string; // ISO, defaults to today UTC date
};

export type IntervalResult = {
  nextDueKm: number | null;
  nextDueDate: string | null;
  remainingKm: number | null;
  remainingDays: number | null;
  status: IntervalStatus;
};

/** True when both last-service km and date are present. */
export function hasIntervalBaseline(
  replacedAtKm: number | null | undefined,
  replacedAtDate: string | null | undefined,
): boolean {
  return (
    replacedAtKm != null &&
    Number.isFinite(replacedAtKm) &&
    typeof replacedAtDate === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(replacedAtDate)
  );
}

const NO_BASELINE_RESULT: IntervalResult = {
  nextDueKm: null,
  nextDueDate: null,
  remainingKm: null,
  remainingDays: null,
  status: "no_baseline",
};

function parseDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addMonths(iso: string, months: number): string {
  const d = parseDate(iso);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth() + months;
  const day = d.getUTCDate();
  const target = new Date(Date.UTC(y, m, 1));
  const lastDay = new Date(
    Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0),
  ).getUTCDate();
  target.setUTCDate(Math.min(day, lastDay));
  return formatDate(target);
}

function daysBetween(fromIso: string, toIso: string): number {
  const a = parseDate(fromIso).getTime();
  const b = parseDate(toIso).getTime();
  return Math.floor((b - a) / 86_400_000);
}

/**
 * Dual-constraint interval: next due is the earlier of km-based and time-based limits.
 * Without a real last-service baseline, returns status `no_baseline` and null remainings
 * (never invent vehicle delivery / purchase date).
 */
export function computeInterval(input: IntervalInput): IntervalResult {
  if (!hasIntervalBaseline(input.replacedAtKm, input.replacedAtDate)) {
    return { ...NO_BASELINE_RESULT };
  }
  const replacedAtKm = input.replacedAtKm as number;
  const replacedAtDate = input.replacedAtDate as string;

  const nowDate = input.nowDate ?? new Date().toISOString().slice(0, 10);
  const soonDays = input.soonDays ?? 30;
  const soonKm = input.soonKm ?? 500;

  let nextDueKm: number | null = null;
  if (input.intervalKm != null && input.intervalKm > 0) {
    nextDueKm = replacedAtKm + input.intervalKm;
  }

  let nextDueDate: string | null = null;
  if (input.intervalMonths != null && input.intervalMonths > 0) {
    nextDueDate = addMonths(replacedAtDate, input.intervalMonths);
  }

  // If only km interval and we have avgKmPerDay, project a date
  if (
    nextDueDate == null &&
    nextDueKm != null &&
    input.avgKmPerDay != null &&
    input.avgKmPerDay > 0
  ) {
    const remaining = nextDueKm - input.nowKm;
    const days = Math.ceil(remaining / input.avgKmPerDay);
    const projected = new Date(parseDate(nowDate).getTime() + days * 86_400_000);
    nextDueDate = formatDate(projected);
  }

  // If both exist, the binding constraint is whichever comes first in "urgency"
  // remainingKm / remainingDays computed independently; status uses both.

  let remainingKm: number | null =
    nextDueKm != null ? nextDueKm - input.nowKm : null;
  let remainingDays: number | null =
    nextDueDate != null ? daysBetween(nowDate, nextDueDate) : null;

  // When both constraints exist, status / "effective" remaining is the worse one
  let status: IntervalStatus = "ok";
  const kmOver = remainingKm != null && remainingKm <= 0;
  const dayOver = remainingDays != null && remainingDays <= 0;
  const kmSoon =
    remainingKm != null && remainingKm > 0 && remainingKm <= soonKm;
  const daySoon =
    remainingDays != null && remainingDays > 0 && remainingDays <= soonDays;

  if (kmOver || dayOver) status = "overdue";
  else if (kmSoon || daySoon) status = "dueSoon";

  return {
    nextDueKm,
    nextDueDate,
    remainingKm,
    remainingDays,
    status,
  };
}

export type MileageApplyResult =
  | { ok: true; currentKm: number }
  | { ok: false; reason: "invalid"; currentKm: number };

/** Set odometer to nextKm (increase or decrease). Rejects negative / non-finite. */
export function applyMileageSet(nextKm: number): MileageApplyResult {
  if (!Number.isFinite(nextKm) || nextKm < 0) {
    return { ok: false, reason: "invalid", currentKm: nextKm };
  }
  return { ok: true, currentKm: nextKm };
}

export type CodingSnapshotDraft = {
  system: string;
  functionName: string;
  subFunction?: string | null;
  beforeValue: string;
  afterValue: string;
  note?: string;
  odometerKm: number;
  recordedAt: string;
};

export function validateCodingSnapshot(
  draft: CodingSnapshotDraft,
): { ok: true } | { ok: false; errors: string[] } {
  const errors: string[] = [];
  if (!draft.system.trim()) errors.push("system required");
  if (!draft.functionName.trim()) errors.push("functionName required");
  if (draft.odometerKm < 0) errors.push("odometerKm invalid");
  if (!draft.recordedAt) errors.push("recordedAt required");
  return errors.length ? { ok: false, errors } : { ok: true };
}

export {
  PETKA_CSV_HEADERS,
  PETKA_CSV_OPTIONAL_HEADERS,
  bulkPartSku,
  bulkRowsToPetkaCsv,
  normalizeOemForSku,
  parseAftermarketQuotesCompact,
  parsePetkaBulkText,
  parsePetkaPartsCsv,
  type AftermarketQuote,
  type BulkCsvOptions,
  type PetkaBulkRow,
  type PetkaCsvError,
  type PetkaCsvSkip,
  type PetkaPartDraft,
  type ParsePetkaCsvResult,
} from "./petka-csv.js";

export {
  isPetkaPriceVerified,
  markPetkaPriceVerified,
  needsPetkaPriceVerify,
  stripPetkaPendingMarkers,
} from "./price-verify.js";

export {
  DEFAULT_FX_TABLE,
  formatCny,
  formatMoneyAsCny,
  rateToCny,
  toCny,
  type FxTable,
} from "./fx.js";

export {
  collectSkusForModelSelection,
  emptyModelOemLinkFile,
  findModelsForSku,
  findModelOemLink,
  findSkuByOemNumber,
  formatModelChildCaption,
  formatModelOemPointer,
  formatOemWithName,
  formatPetkaPrLabel,
  isOemPlaceholderName,
  migrateModelOemLinkIds,
  modelOemLinkId,
  primarySkuOfLink,
  resolveModelOemSku,
  resolveModelOemSkus,
  resolveOemPartLabel,
  suggestOemByInput,
  type ModelChildCaption,
  type ModelOemKind,
  type ModelOemLink,
  type ModelOemLinkFile,
  type OemLabelPart,
  type OemLookupPart,
  type OemSuggestHit,
} from "./model-oem-links.js";

export {
  buildGarageConsumablesList,
  buildGaragePartsList,
  filterPartsByGarageView,
  generationDisplayLabel,
  hotspotsForGarageView,
  isConsumablePart,
  isPartFor981Car,
  mergeGenerationLabel,
  mergePartsByOem,
  resolveConsumableSlotSku,
  EXTERIOR_ZONE_HOTSPOTS,
  GARAGE_CONSUMABLE_SLOTS,
  INTERIOR_ZONE_HOTSPOTS,
  XRAY_FLOW_HOTSPOTS,
  XRAY_MECHANICAL_HOTSPOTS,
  type ExteriorZoneId as GarageExteriorZoneId,
  type GarageOemListOpts,
  type GaragePartRef,
  type GaragePartsMode,
  type GarageStructureId as GaragePartsStructureId,
  type InteriorZoneId as GarageInteriorZoneId,
  type MergedOemPartRow,
} from "./garage-parts-list.js";

export {
  resolveSkuToLocator,
  skuToHotspot,
  type LocatorPartRef,
  type LocatorResolveResult,
  type LocatorZoneRef,
} from "./locator-resolve.js";

export {
  applyIntervalKindMarker,
  parseIntervalKind,
  type IntervalKind,
} from "./interval-kind.js";

export {
  DEFAULT_PAINT_NAME,
  DEFAULT_TOP_OPTION,
  DEFAULT_INTERIOR_OPTION,
  PAINT_OPTIONS,
  TOP_OPTIONS,
  TOP_OPTIONS_SPEC,
  INTERIOR_OPTIONS,
  INTERIOR_OPTIONS_SPEC,
  formatSpecLabel,
  resolveBodyPaintFinish,
  resolveBodyPaintHex,
  resolveBodyPaintPbr,
  resolveInteriorCode,
  resolveInteriorHex,
  resolvePaintCode,
  resolvePaintOemCode,
  resolveSoftTopHex,
  resolveTopCode,
  type BodyPaintFinish,
  type BodyPaintPbr,
  type PaintName,
  type SpecColor,
  type TopOption,
  type InteriorOption,
} from "./vehicle-paint.js";

