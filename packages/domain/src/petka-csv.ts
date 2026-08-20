/** PETKA zone CSV → part draft (matches data/petka/_template/parts.csv). */

export const PETKA_CSV_HEADERS = [
  "sku",
  "name_zh",
  "oem_number",
  "system",
  "zone",
  "oem_price",
  "currency",
  "price_as_of",
  "aftermarket_price",
  "applicability_note",
  "design911_checked",
  "interval_km",
  "interval_months",
  "locator_hotspot",
  "notes",
] as const;

/** Optional columns — old zone CSVs without these still ingest. */
export const PETKA_CSV_OPTIONAL_HEADERS = [
  "aftermarket_quotes",
  "generation",
] as const;

export type PetkaCsvHeader = (typeof PETKA_CSV_HEADERS)[number];

export type AftermarketQuote = {
  brand: string;
  price: number;
};

/** Shape stored in SQLite `parts` after mapping CSV extras. */
export type PetkaPartDraft = {
  sku: string;
  name_zh: string;
  oem_number: string | null;
  system: string;
  /** Present when CSV has `generation` column. */
  generation?: string | null;
  interval_km: number | null;
  interval_months: number | null;
  oem_price: number | null;
  aftermarket_price: number | null;
  /** Set when CSV has `aftermarket_quotes` column; omitted otherwise. */
  aftermarket_quotes?: AftermarketQuote[] | null;
  price_note: string | null;
  price_as_of: string | null;
  locator_hotspot: string | null;
  notes: string | null;
};

export type PetkaCsvSkip = {
  line: number;
  sku: string;
  reason: string;
};

export type PetkaCsvError = {
  line: number;
  sku?: string;
  message: string;
};

export type ParsePetkaCsvResult = {
  drafts: PetkaPartDraft[];
  skipped: PetkaCsvSkip[];
  errors: PetkaCsvError[];
};

function parseCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      continue;
    }
    if (ch === ",") {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out;
}

function emptyToNull(s: string): string | null {
  const t = s.trim();
  return t === "" ? null : t;
}

function parseOptionalNumber(raw: string, field: string, line: number): number | null {
  const t = raw.trim();
  if (t === "") return null;
  const n = Number(t);
  if (!Number.isFinite(n)) {
    throw new Error(`line ${line}: ${field} not a number: ${t}`);
  }
  return n;
}

/** Compact CSV form: `Design911:12.3|BrandB:9` */
export function parseAftermarketQuotesCompact(
  raw: string,
  line: number,
): AftermarketQuote[] | null {
  const t = raw.trim();
  if (!t) return null;
  const out: AftermarketQuote[] = [];
  for (const segment of t.split("|")) {
    const part = segment.trim();
    if (!part) continue;
    const colon = part.indexOf(":");
    if (colon < 1) {
      throw new Error(`line ${line}: invalid aftermarket_quotes segment: ${part}`);
    }
    const brand = part.slice(0, colon).trim();
    const price = Number(part.slice(colon + 1).trim());
    if (!brand || !Number.isFinite(price)) {
      throw new Error(`line ${line}: invalid aftermarket_quotes: ${part}`);
    }
    out.push({ brand, price });
  }
  return out.length ? out : null;
}

function deriveAftermarketPriceFromQuotes(
  quotes: AftermarketQuote[] | null,
): number | null {
  if (!quotes?.length) return null;
  let min = quotes[0]!.price;
  for (const q of quotes) {
    if (q.price < min) min = q.price;
  }
  return min;
}

function shouldSkipExample(sku: string, nameZh: string): string | null {
  if (sku.endsWith("-example")) return "sku ends with -example";
  if (/（示例）|\(示例\)/.test(nameZh)) return "name marked 示例";
  return null;
}

function mapRow(
  cells: Record<string, string>,
  line: number,
  hasQuotesColumn: boolean,
  hasGenerationColumn: boolean,
):
  | { ok: true; draft: PetkaPartDraft }
  | { ok: false; skip?: PetkaCsvSkip; error?: PetkaCsvError } {
  const sku = (cells.sku ?? "").trim();
  const name_zh = (cells.name_zh ?? "").trim();
  const system = (cells.system ?? "").trim();
  const currency = (cells.currency ?? "").trim();

  if (!sku && !name_zh) {
    return { ok: false, skip: { line, sku: "", reason: "empty row" } };
  }

  const skipReason = shouldSkipExample(sku, name_zh);
  if (skipReason) {
    return { ok: false, skip: { line, sku, reason: skipReason } };
  }

  if (!sku) {
    return { ok: false, error: { line, message: "sku required" } };
  }
  if (!name_zh) {
    return { ok: false, error: { line, sku, message: "name_zh required" } };
  }
  if (!system) {
    return { ok: false, error: { line, sku, message: "system required" } };
  }
  if (!currency) {
    return { ok: false, error: { line, sku, message: "currency required" } };
  }

  let oem_price: number | null;
  let aftermarket_price: number | null;
  let aftermarket_quotes: AftermarketQuote[] | null | undefined;
  let interval_km: number | null;
  let interval_months: number | null;
  try {
    oem_price = parseOptionalNumber(cells.oem_price ?? "", "oem_price", line);
    aftermarket_price = parseOptionalNumber(
      cells.aftermarket_price ?? "",
      "aftermarket_price",
      line,
    );
    if (hasQuotesColumn) {
      aftermarket_quotes = parseAftermarketQuotesCompact(
        cells.aftermarket_quotes ?? "",
        line,
      );
      if (aftermarket_quotes?.length) {
        aftermarket_price = deriveAftermarketPriceFromQuotes(aftermarket_quotes);
      }
    }
    interval_km = parseOptionalNumber(cells.interval_km ?? "", "interval_km", line);
    interval_months = parseOptionalNumber(
      cells.interval_months ?? "",
      "interval_months",
      line,
    );
  } catch (e) {
    return {
      ok: false,
      error: {
        line,
        sku,
        message: e instanceof Error ? e.message : String(e),
      },
    };
  }

  const zone = emptyToNull(cells.zone ?? "");
  const locator = emptyToNull(cells.locator_hotspot ?? "") ?? zone;
  const design911 = emptyToNull(cells.design911_checked ?? "");
  const applicability = emptyToNull(cells.applicability_note ?? "");
  const extraNotes = emptyToNull(cells.notes ?? "");

  const priceBits = [`currency=${currency}`];
  if (design911) priceBits.push(`design911=${design911}`);
  const price_note = priceBits.join("; ");

  const noteBits: string[] = [];
  if (applicability) noteBits.push(applicability);
  if (extraNotes) noteBits.push(extraNotes);
  const notes = noteBits.length ? noteBits.join(" | ") : null;

  let generation: string | null | undefined;
  if (hasGenerationColumn) {
    const g = (cells.generation ?? "").trim();
    if (g && g !== "981" && g !== "982") {
      return {
        ok: false,
        error: {
          line,
          sku,
          message: `generation must be 981|982 (got ${g})`,
        },
      };
    }
    generation = g || null;
  }

  const draft: PetkaPartDraft = {
    sku,
    name_zh,
    oem_number: emptyToNull(cells.oem_number ?? ""),
    system,
    interval_km,
    interval_months,
    oem_price,
    aftermarket_price,
    price_note,
    price_as_of: emptyToNull(cells.price_as_of ?? ""),
    locator_hotspot: locator,
    notes,
  };
  if (hasQuotesColumn) draft.aftermarket_quotes = aftermarket_quotes ?? null;
  if (hasGenerationColumn) draft.generation = generation ?? null;

  return { ok: true, draft };
}

/**
 * Parse UTF-8 PETKA parts CSV text into upsert drafts.
 * Skips template example rows (`*-example` / 示例).
 */
export function parsePetkaPartsCsv(text: string): ParsePetkaCsvResult {
  const lines = text
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((l, i, arr) => !(l.trim() === "" && i === arr.length - 1));

  const drafts: PetkaPartDraft[] = [];
  const skipped: PetkaCsvSkip[] = [];
  const errors: PetkaCsvError[] = [];

  if (lines.length === 0) {
    errors.push({ line: 0, message: "empty file" });
    return { drafts, skipped, errors };
  }

  const headerCells = parseCsvLine(lines[0]).map((h) => h.trim());
  const missing = PETKA_CSV_HEADERS.filter((h) => !headerCells.includes(h));
  if (missing.length) {
    errors.push({
      line: 1,
      message: `missing headers: ${missing.join(", ")}`,
    });
    return { drafts, skipped, errors };
  }

  const index = new Map(headerCells.map((h, i) => [h, i]));
  const hasQuotesColumn = headerCells.includes("aftermarket_quotes");
  const hasGenerationColumn = headerCells.includes("generation");
  const allHeaders = [
    ...PETKA_CSV_HEADERS,
    ...PETKA_CSV_OPTIONAL_HEADERS.filter((h) => headerCells.includes(h)),
  ];

  for (let i = 1; i < lines.length; i++) {
    const lineNo = i + 1;
    const raw = lines[i];
    if (!raw.trim()) {
      skipped.push({ line: lineNo, sku: "", reason: "empty row" });
      continue;
    }
    const cols = parseCsvLine(raw);
    const cells: Record<string, string> = {};
    for (const h of allHeaders) {
      const idx = index.get(h);
      cells[h] = idx == null ? "" : (cols[idx] ?? "");
    }
    const mapped = mapRow(cells, lineNo, hasQuotesColumn, hasGenerationColumn);
    if (!mapped.ok) {
      if (mapped.skip) skipped.push(mapped.skip);
      if (mapped.error) errors.push(mapped.error);
      continue;
    }
    drafts.push(mapped.draft);
  }

  return { drafts, skipped, errors };
}

/** Normalize OEM for bulk sku: strip dots/spaces, uppercase. */
export function normalizeOemForSku(oem: string): string {
  return oem.replace(/[.\s]/g, "").toUpperCase();
}

/** Bulk catalog sku: `{gen}-{oemNormalized}` — never collide with maintenance skus. */
export function bulkPartSku(generation: "981" | "982", oem: string): string {
  return `${generation}-${normalizeOemForSku(oem)}`;
}

/** Porsche OEM-ish token (dotted or compact). */
const OEM_RE =
  /\b([0-9A-Z]{2,4}(?:\.[0-9A-Z]{2,4}){2,5}|[0-9A-Z]{9,14})\b/i;

/** GBP / plain decimal price near end of line. */
const PRICE_RE = /(?:£\s*)?(\d{1,5}(?:[.,]\d{2})?)\s*(?:GBP|£)?\s*$/i;

export type PetkaBulkRow = {
  oem_number: string;
  name: string;
  oem_price: number | null;
};

/**
 * Parse PETKA clipboard / printed parts-list text into OEM rows.
 * Tolerates tab or multi-space columns; skips headers / empty lines.
 */
export function parsePetkaBulkText(text: string): PetkaBulkRow[] {
  const rows: PetkaBulkRow[] = [];
  const seen = new Set<string>();
  for (const raw of text.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (/^(pos|tnr|teil|part|nr\.?|menge|qty|preis|price|beschreibung|description)\b/i.test(line)) {
      continue;
    }
    const oemMatch = line.match(OEM_RE);
    if (!oemMatch) continue;
    const oem_number = oemMatch[1]!.toUpperCase().replace(
      /^([0-9A-Z]{9,14})$/,
      (compact) => {
        // leave compact as-is; dotted already fine
        return compact;
      },
    );
    // Prefer dotted form when input had dots
    const oemCanonical = oemMatch[1]!.includes(".")
      ? oemMatch[1]!.toUpperCase()
      : oemMatch[1]!.toUpperCase();

    let rest = line.replace(oemMatch[0], " ").replace(/\s+/g, " ").trim();
    let oem_price: number | null = null;
    const priceMatch = rest.match(PRICE_RE);
    if (priceMatch) {
      const n = Number(priceMatch[1]!.replace(",", "."));
      if (Number.isFinite(n) && n > 0 && n < 100_000) {
        oem_price = n;
        rest = rest.slice(0, priceMatch.index).trim();
      }
    }
    // strip leading pos numbers
    rest = rest.replace(/^\d{1,4}\s+/, "").trim();
    const name = rest || oemCanonical;
    const key = normalizeOemForSku(oemCanonical);
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ oem_number: oemCanonical, name, oem_price });
  }
  return rows;
}

export type BulkCsvOptions = {
  generation: "981" | "982";
  system?: string;
  zone?: string;
  asOf?: string;
  nameLang?: "de" | "en" | "zh" | "unknown";
};

/** Build zone-template CSV text from bulk rows (for ingest:petka --file). */
export function bulkRowsToPetkaCsv(
  rows: PetkaBulkRow[],
  opts: BulkCsvOptions,
): string {
  const asOf = opts.asOf ?? new Date().toISOString().slice(0, 10);
  const system = opts.system ?? "bulk";
  const zone = opts.zone ?? "bulk";
  const lang = opts.nameLang ?? "unknown";
  const header = [
    ...PETKA_CSV_HEADERS,
    "generation",
  ].join(",");
  const lines = [header];
  for (const r of rows) {
    const sku = bulkPartSku(opts.generation, r.oem_number);
    const notes = `PETKA bulk; PETKA价; name_lang=${lang}; as_of=${asOf}`;
    const cells = [
      sku,
      csvEscape(r.name),
      r.oem_number,
      system,
      zone,
      r.oem_price == null ? "" : String(r.oem_price),
      "GBP",
      asOf,
      "",
      "",
      "no",
      "",
      "",
      zone,
      csvEscape(notes),
      opts.generation,
    ];
    lines.push(cells.join(","));
  }
  return lines.join("\n") + "\n";
}

function csvEscape(s: string): string {
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}
