/** Join notes + price_note for price-source heuristics. */
function priceText(
  notes: string | null | undefined,
  priceNote: string | null | undefined,
): string {
  return [notes, priceNote].filter(Boolean).join(" ");
}

/**
 * True when text looks like a local PETKA price confirmation
 * (e.g. 「PETKA价」「来源PETKA」「PETKA 抄」).
 * Strips 「非PETKA价」 first so the negative phrase does not count as verified.
 */
export function isPetkaPriceVerified(
  notes: string | null | undefined,
  priceNote: string | null | undefined,
): boolean {
  const text = priceText(notes, priceNote);
  if (!text) return false;
  const withoutNeg = text.replace(/非\s*PETKA\s*价/g, "");
  return /PETKA\s*价|来源\s*PETKA|PETKA\s*抄|已\s*PETKA\s*复核/.test(withoutNeg);
}

/**
 * True when notes/price_note mark a non-PETKA / Design911 price that still
 * needs local PETKA verify. Verified markers win over pending markers.
 */
export function needsPetkaPriceVerify(
  notes: string | null | undefined,
  priceNote: string | null | undefined,
): boolean {
  const text = priceText(notes, priceNote);
  if (!text) return false;
  if (isPetkaPriceVerified(notes, priceNote)) return false;
  return /非\s*PETKA|待本机复核|Design911/i.test(text);
}

/** Normalize Chinese/ASCII separators and drop empty segments. */
function tidyNoteSegments(notes: string): string {
  return notes
    .split(/[；;]/)
    .map((s) => s.replace(/^[，,、\s]+|[，,、\s]+$/g, "").trim())
    .filter(Boolean)
    .join("；");
}

/**
 * Remove pending / false-verified markers from notes.
 * Keeps useful Design911 URL history; strips 「非PETKA价」「待本机复核」
 * and the meaningless 「PETKA inbox」 tag.
 */
export function stripPetkaPendingMarkers(
  notes: string | null | undefined,
): string {
  let s = (notes ?? "").trim();
  if (!s) return "";
  // Combined pending phrase first
  s = s.replace(/非\s*PETKA\s*价\s*[，,、]?\s*待本机复核/g, "");
  s = s.replace(/非\s*PETKA\s*价/g, "");
  s = s.replace(/待本机复核/g, "");
  // Misleading inbox tag (does not satisfy isPetkaPriceVerified)
  s = s.replace(/PETKA\s*inbox/gi, "");
  return tidyNoteSegments(s);
}

/**
 * After writing a real PETKA oem_price: clear pending markers and ensure a
 * formal verified marker so isPetkaPriceVerified=true / needsPetkaPriceVerify=false.
 * Optional append (e.g. TSV notes) is stripped then merged.
 */
export function markPetkaPriceVerified(
  notes: string | null | undefined,
  append?: string | null,
): string {
  let s = stripPetkaPendingMarkers(notes);
  const extra = stripPetkaPendingMarkers(append);
  if (extra) {
    for (const part of extra.split(/[；;]/)) {
      const p = part.trim();
      if (!p) continue;
      if (!s.includes(p)) s = s ? `${s}；${p}` : p;
    }
  }
  if (!isPetkaPriceVerified(s, null)) {
    s = s ? `${s}；PETKA价` : "PETKA价";
  }
  return s;
}
