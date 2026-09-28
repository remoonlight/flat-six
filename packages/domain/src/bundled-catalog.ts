import { DEFAULT_FX_TABLE, convertViaCny, type FxTable } from "./fx.js";
import {
  parsePetkaEpcCsv,
  compactOem,
  type PetkaEpcRecord,
} from "./petka-epc.js";
import {
  parsePetkaPartsCsv,
  type AftermarketQuote,
  type PetkaPartDraft,
} from "./petka-csv.js";

export const BUNDLED_CATALOG_ALGO = "bundled-catalog-v3";

export type BundledPart = {
  sku: string;
  name_zh: string;
  name_en: string | null;
  oem_number: string | null;
  system: string;
  generation: string | null;
  interval_km: number | null;
  interval_months: number | null;
  oem_price: number | null;
  aftermarket_price: number | null;
  aftermarket_quotes: AftermarketQuote[] | null;
  price_note: string | null;
  price_as_of: string | null;
  locator_hotspot: string | null;
  notes: string | null;
  petka_note: string | null;
  pr_label: string | null;
  /** Bootstrap (or bulk placeholder) name_zh, for safe in-place upgrades. */
  seedNameZh: string | null;
};

export type CatalogFileSet = {
  bootstrapJson: string;
  zoneCsvs: string[];
  bulkCsvs: string[];
  epcCsv: string;
  fxJson?: string;
  exactOemJson?: string;
};

export type AssembleBundledCatalogResult = {
  rows: BundledPart[];
  zoneDrafts: number;
  bulkDrafts: number;
  epcUnique: number;
  exactOemHits: number;
};

export type ExactOemHit = {
  compact: string;
  price: number;
  as_of: string;
};

export function parseExactTeileOemHits(text: string): ExactOemHit[] {
  const raw = JSON.parse(text) as {
    hits?: Array<{
      compact?: unknown;
      priceNumber?: unknown;
      price?: unknown;
      as_of?: unknown;
      err?: unknown;
    }>;
  };
  const out: ExactOemHit[] = [];
  for (const h of raw.hits || []) {
    if (!h || h.err) continue;
    const compact = compactOem(String(h.compact || ""));
    const pn = compactOem(String(h.priceNumber || h.compact || ""));
    const price = Number(h.price);
    const as_of = String(h.as_of || "").slice(0, 10);
    if (!compact || compact.length < 5) continue;
    if (pn !== compact) continue;
    if (!Number.isFinite(price) || price <= 0) continue;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(as_of)) continue;
    out.push({ compact, price, as_of });
  }
  return out;
}

function parseCurrency(priceNote: string | null | undefined): string | null {
  const m = /(?:^|;\s*)currency=([A-Za-z]{3})\b/i.exec(priceNote || "");
  return m ? m[1]!.toUpperCase() : null;
}

function appendNoteBits(note: string | null, bits: string[]): string | null {
  let out = (note || "").trim();
  for (const bit of bits) {
    const key = bit.split("=")[0];
    if (!key) continue;
    if (new RegExp(`(?:^|;\\s*)${key}=`, "i").test(out)) continue;
    out = out ? `${out}; ${bit}` : bit;
  }
  return out || null;
}

function loadFx(raw?: string): FxTable {
  if (!raw) return DEFAULT_FX_TABLE;
  try {
    return parseFxTable(JSON.parse(raw));
  } catch {
    return DEFAULT_FX_TABLE;
  }
}

export function parseFxTable(raw: unknown): FxTable {
  if (!raw || typeof raw !== "object") return DEFAULT_FX_TABLE;
  const o = raw as {
    display_currency?: unknown;
    as_of?: unknown;
    note?: unknown;
    rates_to_cny?: unknown;
  };
  const rates =
    o.rates_to_cny && typeof o.rates_to_cny === "object"
      ? (o.rates_to_cny as Record<string, number>)
      : DEFAULT_FX_TABLE.rates_to_cny;
  return {
    display_currency: "CNY",
    as_of: typeof o.as_of === "string" ? o.as_of : DEFAULT_FX_TABLE.as_of,
    note: typeof o.note === "string" ? o.note : DEFAULT_FX_TABLE.note,
    rates_to_cny: { ...DEFAULT_FX_TABLE.rates_to_cny, ...rates },
  };
}

function aftermarketLooksGbp(text: string): boolean {
  return /aftermarket_price\s*=\s*[\d.]+(?:\s*)GBP|Design911 aftermarket_price=[\d.]+ GBP|GBP 保留/i.test(
    text,
  );
}

function sourceBits(text: string): string {
  const src: string[] = [];
  if (/teile/i.test(text)) src.push("teile");
  if (/Design911/i.test(text)) src.push("Design911");
  return src.length ? `source=${src.join("/")}` : "source=teile/Design911";
}

export function harmonizeZoneDraft(
  draft: PetkaPartDraft,
  fx: FxTable,
): PetkaPartDraft {
  const declared = parseCurrency(draft.price_note) || "EUR";
  const blob = [draft.notes, draft.price_note].filter(Boolean).join(" ");
  let aftermarket_price = draft.aftermarket_price;
  let aftermarket_quotes = draft.aftermarket_quotes;
  const extraBits = [`petka_verified=0`, sourceBits(blob)];

  if (
    aftermarket_price != null &&
    declared !== "GBP" &&
    aftermarketLooksGbp(blob)
  ) {
    const converted = convertViaCny(aftermarket_price, "GBP", declared, fx);
    extraBits.push(`aftermarket_orig=${aftermarket_price} GBP`);
    if (converted != null) {
      aftermarket_price = converted;
      if (aftermarket_quotes?.length) {
        aftermarket_quotes = aftermarket_quotes.map((q) => {
          const p = convertViaCny(q.price, "GBP", declared, fx);
          return { brand: q.brand, price: p ?? q.price };
        });
      } else if (/Design911/i.test(blob)) {
        aftermarket_quotes = [{ brand: "Design911", price: converted }];
      }
    }
  }

  return {
    ...draft,
    aftermarket_price,
    aftermarket_quotes:
      aftermarket_quotes !== undefined ? aftermarket_quotes : draft.aftermarket_quotes,
    price_note: appendNoteBits(draft.price_note, extraBits),
  };
}

function fromDraft(
  d: PetkaPartDraft,
  extras?: Partial<BundledPart>,
): BundledPart {
  return {
    sku: d.sku,
    name_zh: d.name_zh,
    name_en: extras?.name_en ?? null,
    oem_number: d.oem_number,
    system: d.system,
    generation: d.generation ?? null,
    interval_km: d.interval_km,
    interval_months: d.interval_months,
    oem_price: d.oem_price,
    aftermarket_price: d.aftermarket_price,
    aftermarket_quotes: d.aftermarket_quotes ?? null,
    price_note: d.price_note,
    price_as_of: d.price_as_of,
    locator_hotspot: d.locator_hotspot,
    notes: d.notes,
    petka_note: extras?.petka_note ?? null,
    pr_label: extras?.pr_label ?? null,
    seedNameZh: extras?.seedNameZh ?? d.name_zh,
  };
}

function overlayDraft(base: BundledPart, d: PetkaPartDraft): BundledPart {
  return {
    ...base,
    name_zh: d.name_zh || base.name_zh,
    oem_number: d.oem_number ?? base.oem_number,
    system: d.system || base.system,
    generation: d.generation !== undefined ? d.generation : base.generation,
    interval_km: d.interval_km ?? base.interval_km,
    interval_months: d.interval_months ?? base.interval_months,
    oem_price: d.oem_price,
    aftermarket_price: d.aftermarket_price,
    aftermarket_quotes:
      d.aftermarket_quotes !== undefined
        ? d.aftermarket_quotes
        : base.aftermarket_quotes,
    price_note: d.price_note,
    price_as_of: d.price_as_of,
    locator_hotspot: d.locator_hotspot ?? base.locator_hotspot,
    notes: d.notes ?? base.notes,
  };
}

function applyExactOemPrices(
  bySku: Map<string, BundledPart>,
  hits: ExactOemHit[],
  fx: FxTable,
): number {
  const byCompact = new Map(hits.map((h) => [h.compact, h]));
  let applied = 0;
  for (const [sku, row] of bySku) {
    if (row.generation === "982") continue;
    if (row.oem_price != null) continue;
    const c = compactOem(row.oem_number);
    if (!c) continue;
    const hit = byCompact.get(c);
    if (!hit) continue;
    const declared = parseCurrency(row.price_note) || "EUR";
    const oem =
      convertViaCny(hit.price, "EUR", declared, fx) ?? hit.price;
    const note = appendNoteBits(
      String(row.price_note || "")
        .split(";")
        .map((s) => s.trim())
        .filter((s) => s && !/^source=petka-epc$/i.test(s))
        .join("; ") || `currency=${declared}`,
      [`currency=${declared}`, "petka_verified=0", "source=teile.com"],
    );
    bySku.set(sku, {
      ...row,
      oem_price: oem,
      price_as_of: row.price_as_of || hit.as_of,
      price_note: note,
    });
    applied++;
  }
  return applied;
}

function applyEpc(row: BundledPart, rec: PetkaEpcRecord): BundledPart {
  return {
    ...row,
    oem_number: rec.oem || row.oem_number,
    name_zh: rec.name_zh || row.name_zh,
    name_en: rec.name_en || row.name_en,
    petka_note: rec.petka_note || row.petka_note,
    pr_label: rec.pr_label || row.pr_label,
  };
}

export function assembleBundledCatalog(
  files: CatalogFileSet,
): AssembleBundledCatalogResult {
  const fx = loadFx(files.fxJson);
  const bootstrap = JSON.parse(files.bootstrapJson) as {
    parts?: Array<{
      sku: string;
      name_zh: string;
      oem_number: string | null;
      system: string;
      interval_km: number | null;
      interval_months: number | null;
      oem_price: number | null;
      aftermarket_price: number | null;
      price_note: string | null;
      price_as_of: string | null;
      locator_hotspot: string | null;
      notes: string | null;
    }>;
  };
  const bySku = new Map<string, BundledPart>();
  for (const p of bootstrap.parts || []) {
    bySku.set(p.sku, {
      sku: p.sku,
      name_zh: p.name_zh,
      name_en: null,
      oem_number: p.oem_number,
      system: p.system,
      generation: null,
      interval_km: p.interval_km,
      interval_months: p.interval_months,
      oem_price: p.oem_price,
      aftermarket_price: p.aftermarket_price,
      aftermarket_quotes: null,
      price_note: p.price_note,
      price_as_of: p.price_as_of,
      locator_hotspot: p.locator_hotspot,
      notes: p.notes,
      petka_note: null,
      pr_label: null,
      seedNameZh: p.name_zh,
    });
  }

  let zoneDrafts = 0;
  for (const text of files.zoneCsvs) {
    const parsed = parsePetkaPartsCsv(text);
    if (parsed.errors.length) {
      throw new Error(
        `zone csv: ${parsed.errors.map((e) => e.message).join("; ")}`,
      );
    }
    for (const raw of parsed.drafts) {
      const d = harmonizeZoneDraft(raw, fx);
      zoneDrafts++;
      const existing = bySku.get(d.sku);
      bySku.set(d.sku, existing ? overlayDraft(existing, d) : fromDraft(d));
    }
  }

  let bulkDrafts = 0;
  for (const text of files.bulkCsvs) {
    const parsed = parsePetkaPartsCsv(text);
    if (parsed.errors.length) {
      throw new Error(
        `bulk csv: ${parsed.errors.map((e) => e.message).join("; ")}`,
      );
    }
    for (const raw of parsed.drafts) {
      const d = harmonizeZoneDraft(raw, fx);
      bulkDrafts++;
      if (bySku.has(d.sku)) continue;
      bySku.set(d.sku, fromDraft(d, { seedNameZh: d.name_zh }));
    }
  }

  const epc = parsePetkaEpcCsv(files.epcCsv);
  for (const row of bySku.values()) {
    const c = compactOem(row.oem_number);
    if (!c) continue;
    const rec = epc.byCompact.get(c);
    if (rec) bySku.set(row.sku, applyEpc(row, rec));
  }
  for (const [compact, rec] of epc.byCompact) {
    const sku = `981-${compact}`;
    const existing = bySku.get(sku);
    if (existing) {
      bySku.set(sku, applyEpc(existing, rec));
      continue;
    }
    bySku.set(sku, {
      sku,
      name_zh: rec.name_zh || rec.name_en || sku,
      name_en: rec.name_en || null,
      oem_number: rec.oem,
      system: rec.hg ? `HG-${rec.hg}` : "PETKA",
      generation: "981",
      interval_km: null,
      interval_months: null,
      oem_price: null,
      aftermarket_price: null,
      aftermarket_quotes: null,
      price_note: "currency=EUR; petka_verified=0; source=petka-epc",
      price_as_of: null,
      locator_hotspot: null,
      notes: "source=petka-epc",
      petka_note: rec.petka_note || null,
      pr_label: rec.pr_label || null,
      seedNameZh: compact,
    });
  }

  const exactHits = files.exactOemJson
    ? parseExactTeileOemHits(files.exactOemJson)
    : [];
  const exactOemHits = applyExactOemPrices(bySku, exactHits, fx);

  // Maintenance and catalog SKUs may represent the same OEM. Share only the
  // known OEM quote, never aftermarket offers or vehicle applicability.
  const maintenancePrices = new Map(
    [...bySku.values()]
      .filter((r) => r.generation == null && r.oem_number && r.oem_price != null)
      .map((r) => [compactOem(r.oem_number), r]),
  );
  for (const row of bySku.values()) {
    if (row.generation !== "981" || row.oem_price != null) continue;
    const donor = maintenancePrices.get(compactOem(row.oem_number));
    if (!donor) continue;
    const currency = parseCurrency(row.price_note) || "EUR";
    const amount = convertViaCny(donor.oem_price, parseCurrency(donor.price_note), currency, fx);
    if (amount == null) continue;
    row.oem_price = amount;
    row.price_as_of = donor.price_as_of;
    const source = /(?:^|;\s*)source=([^;]+)/i.exec(donor.price_note || "")?.[1];
    row.price_note = `currency=${currency}; petka_verified=0; source=${source || "teile/Design911"}; quote_sku=${donor.sku}`;
  }

  return {
    rows: [...bySku.values()],
    zoneDrafts,
    bulkDrafts,
    epcUnique: epc.byCompact.size,
    exactOemHits,
  };
}

export function isShippedCatalogPriceNote(
  note: string | null | undefined,
): boolean {
  const t = (note || "").trim();
  if (!t) return true;
  const bits = t.split(";").map((s) => s.trim()).filter(Boolean);
  const keyOk =
    /^(currency|design911|petka_verified|source|aftermarket_orig)=/i;
  if (!bits.every((b) => keyOk.test(b))) return false;
  for (const b of bits) {
    if (!/^source=/i.test(b)) continue;
    const v = b.slice(b.indexOf("=") + 1).trim();
    if (
      !/^(petka-epc|teile\.com|teile(?:\/Design911)?|Design911)$/i.test(v)
    ) {
      return false;
    }
  }
  return true;
}

/** @deprecated use isShippedCatalogPriceNote — custom currency/source notes are not replaceable. */
export function catalogPriceNoteLooksSafe(
  note: string | null | undefined,
): boolean {
  return isShippedCatalogPriceNote(note);
}
