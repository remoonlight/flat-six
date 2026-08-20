/** Community soft reminder vs OEM/hard maintenance interval. */
export type IntervalKind = "soft" | "hard";

const KIND_RE = /\binterval_kind=(soft|hard)\b/i;

/** Parse `interval_kind=soft|hard` from parts.notes; omit → hard. */
export function parseIntervalKind(
  notes: string | null | undefined,
): IntervalKind {
  const m = notes?.match(KIND_RE);
  if (m?.[1]?.toLowerCase() === "soft") return "soft";
  return "hard";
}

/**
 * Upsert or strip the notes marker. soft → `interval_kind=soft`; hard → remove marker.
 * Preserves the rest of the notes text.
 */
export function applyIntervalKindMarker(
  notes: string | null | undefined,
  kind: IntervalKind,
): string | null {
  const cleaned = (notes ?? "")
    .replace(/\s*;?\s*interval_kind=(soft|hard)\b/gi, "")
    .replace(/^;\s*/, "")
    .replace(/;\s*;/g, ";")
    .trim();
  if (kind === "soft") {
    return cleaned ? `interval_kind=soft; ${cleaned}` : "interval_kind=soft";
  }
  return cleaned || null;
}
