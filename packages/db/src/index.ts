import { DatabaseSync } from "node:sqlite";
import fs from "node:fs";
import path from "node:path";
import {
  applyMileageSet,
  computeInterval,
  type IntervalResult,
} from "@porsche981/domain";

export type Vehicle = {
  id: number;
  year: number;
  model: string;
  trim: string;
  chassis: string;
  vin: string | null;
  current_km: number;
  avg_km_per_day: number | null;
  paint_name: string | null;
  paint_code: string | null;
  interior: string | null;
  top: string | null;
  updated_at: string;
};

export type VehicleSettings = {
  paint_name: string | null;
  paint_code: string | null;
  interior: string | null;
  top: string | null;
};

export type AftermarketQuote = {
  brand: string;
  price: number;
};

export type Part = {
  id: number;
  sku: string;
  name_zh: string;
  /** Design911 / 英文原文；可空 */
  name_en: string | null;
  oem_number: string | null;
  system: string;
  /** `981` | `982` for bulk catalog; null = maintenance subset. */
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
  /** PETKA 图注（多为左/右侧） */
  petka_note: string | null;
  /** PETKA PR 代号+解释，如 `447 紧急备用轮胎` */
  pr_label: string | null;
};

function normalizeAftermarketQuotes(parsed: unknown): AftermarketQuote[] | null {
  if (!Array.isArray(parsed) || parsed.length === 0) return null;
  const out: AftermarketQuote[] = [];
  for (const item of parsed) {
    if (!item || typeof item !== "object") continue;
    const brand = String((item as { brand?: unknown }).brand ?? "").trim();
    const price = Number((item as { price?: unknown }).price);
    if (!brand || !Number.isFinite(price)) continue;
    out.push({ brand, price });
  }
  return out.length ? out : null;
}

export function parseAftermarketQuotes(raw: unknown): AftermarketQuote[] | null {
  if (raw == null || raw === "") return null;
  if (typeof raw !== "string") return null;
  try {
    return normalizeAftermarketQuotes(JSON.parse(raw));
  } catch {
    return null;
  }
}

function serializeAftermarketQuotes(
  quotes: AftermarketQuote[] | null | undefined,
): string | null {
  if (!quotes?.length) return null;
  return JSON.stringify(quotes);
}

/** Cheapest alternate quote — mirrors legacy single `aftermarket_price`. */
export function deriveAftermarketPrice(
  quotes: AftermarketQuote[] | null | undefined,
): number | null {
  if (!quotes?.length) return null;
  let min = quotes[0]!.price;
  for (const q of quotes) {
    if (q.price < min) min = q.price;
  }
  return min;
}

function mapPartRow(row: Record<string, unknown>): Part {
  return {
    ...(row as Omit<
      Part,
      "aftermarket_quotes" | "generation" | "name_en" | "petka_note" | "pr_label"
    >),
    name_en:
      row.name_en == null || row.name_en === ""
        ? null
        : String(row.name_en),
    petka_note:
      row.petka_note == null || row.petka_note === ""
        ? null
        : String(row.petka_note),
    pr_label:
      row.pr_label == null || row.pr_label === ""
        ? null
        : String(row.pr_label),
    generation:
      row.generation == null || row.generation === ""
        ? null
        : String(row.generation),
    aftermarket_quotes: parseAftermarketQuotes(row.aftermarket_quotes),
  };
}

export type ServiceRecord = {
  id: number;
  part_id: number | null;
  title: string;
  replaced_at: string;
  odometer_km: number;
  brand: string | null;
  cost: number | null;
  notes: string | null;
};

export type CodingSnapshot = {
  id: number;
  system: string;
  function_name: string;
  sub_function: string | null;
  before_value: string;
  after_value: string;
  note: string | null;
  odometer_km: number;
  recorded_at: string;
};

export type FaultEntry = {
  id: number;
  symptom: string;
  likely_causes: string;
  checks: string;
  related_part_sku: string | null;
};

/** Long-term fault tracking (R5) — separate from read-only fault_kb. */
export type FaultLog = {
  id: number;
  logged_at: string;
  odometer_km: number;
  symptom: string;
  area_hypothesis: string | null;
  action: string | null;
  result: string | null;
  closed: number;
  related_part_sku: string | null;
  coding_snapshot_id: number | null;
};

/** OBD-II DTC knowledge base (Phase 1 — read-only lookup). */
export type DtcEntry = {
  code: string;
  title_zh: string;
  likely_causes: string;
  checks: string;
  related_part_sku: string | null;
};

/** Manual OBD session (Phase 1 — no adapter). */
export type ObdSession = {
  id: number;
  started_at: string;
  odometer_km: number | null;
  adapter: string | null;
  note: string | null;
};

export type ObdDtc = {
  id: number;
  session_id: number;
  code: string;
  status: string;
  raw_json: string | null;
};

/** Mileage change audit — setMileage records every write (up or down). */
export type MileageAudit = {
  id: number;
  previous_km: number;
  new_km: number;
  reason: string;
  created_at: string;
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS vehicle (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  year INTEGER NOT NULL,
  model TEXT NOT NULL,
  trim TEXT NOT NULL,
  chassis TEXT NOT NULL,
  vin TEXT,
  current_km INTEGER NOT NULL DEFAULT 0,
  avg_km_per_day REAL,
  paint_name TEXT,
  paint_code TEXT,
  interior TEXT,
  top TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mileage_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  previous_km INTEGER NOT NULL,
  new_km INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sku TEXT NOT NULL UNIQUE,
  name_zh TEXT NOT NULL,
  oem_number TEXT,
  system TEXT NOT NULL,
  generation TEXT,
  interval_km INTEGER,
  interval_months INTEGER,
  oem_price REAL,
  aftermarket_price REAL,
  price_note TEXT,
  price_as_of TEXT,
  locator_hotspot TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS service_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_id INTEGER REFERENCES parts(id),
  title TEXT NOT NULL,
  replaced_at TEXT NOT NULL,
  odometer_km INTEGER NOT NULL,
  brand TEXT,
  cost REAL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS coding_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  system TEXT NOT NULL,
  function_name TEXT NOT NULL,
  sub_function TEXT,
  before_value TEXT NOT NULL,
  after_value TEXT NOT NULL,
  note TEXT,
  odometer_km INTEGER NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fault_kb (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symptom TEXT NOT NULL,
  likely_causes TEXT NOT NULL,
  checks TEXT NOT NULL,
  related_part_sku TEXT
);

CREATE TABLE IF NOT EXISTS fault_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  logged_at TEXT NOT NULL,
  odometer_km INTEGER NOT NULL,
  symptom TEXT NOT NULL,
  area_hypothesis TEXT,
  action TEXT,
  result TEXT,
  closed INTEGER NOT NULL DEFAULT 0,
  related_part_sku TEXT,
  coding_snapshot_id INTEGER REFERENCES coding_snapshots(id)
);

CREATE TABLE IF NOT EXISTS dtc_kb (
  code TEXT PRIMARY KEY,
  title_zh TEXT NOT NULL,
  likely_causes TEXT NOT NULL,
  checks TEXT NOT NULL,
  related_part_sku TEXT
);

CREATE TABLE IF NOT EXISTS obd_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  odometer_km INTEGER,
  adapter TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS obd_dtcs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES obd_sessions(id),
  code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'manual',
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
`;

export type SeedPayload = {
  parts: Array<
    Omit<
      Part,
      | "id"
      | "generation"
      | "aftermarket_quotes"
      | "name_en"
      | "petka_note"
      | "pr_label"
    > & {
      generation?: string | null;
      aftermarket_quotes?: AftermarketQuote[] | null;
      name_en?: string | null;
      petka_note?: string | null;
      pr_label?: string | null;
    }
  >;
  faults: Array<Omit<FaultEntry, "id">>;
  dtcs?: DtcEntry[];
};

export class GarageDb {
  readonly db: DatabaseSync;

  constructor(dbPath: string) {
    const dir = path.dirname(dbPath);
    fs.mkdirSync(dir, { recursive: true });
    this.db = new DatabaseSync(dbPath);
    this.db.exec(SCHEMA);
    this.ensureVehicleColumns();
    this.ensurePartsColumns();
    this.migrateAftermarketQuotes();
    this.ensureVehicle();
  }

  private ensurePartsColumns() {
    const cols = this.db.prepare("PRAGMA table_info(parts)").all() as Array<{
      name: string;
    }>;
    const names = new Set(cols.map((c) => c.name));
    if (!names.has("aftermarket_quotes")) {
      this.db.exec(`ALTER TABLE parts ADD COLUMN aftermarket_quotes TEXT`);
    }
    if (!names.has("generation")) {
      this.db.exec(`ALTER TABLE parts ADD COLUMN generation TEXT`);
    }
    if (!names.has("name_en")) {
      this.db.exec(`ALTER TABLE parts ADD COLUMN name_en TEXT`);
    }
    if (!names.has("petka_note")) {
      this.db.exec(`ALTER TABLE parts ADD COLUMN petka_note TEXT`);
    }
    if (!names.has("pr_label")) {
      this.db.exec(`ALTER TABLE parts ADD COLUMN pr_label TEXT`);
    }
  }

  /** Seed quotes from legacy `aftermarket_price` (Design911). */
  private migrateAftermarketQuotes() {
    const rows = this.db
      .prepare(
        `SELECT id, aftermarket_price, aftermarket_quotes FROM parts WHERE aftermarket_price IS NOT NULL`,
      )
      .all() as Array<{
      id: number;
      aftermarket_price: number;
      aftermarket_quotes: string | null;
    }>;
    const update = this.db.prepare(
      `UPDATE parts SET aftermarket_quotes = ? WHERE id = ?`,
    );
    for (const row of rows) {
      if (parseAftermarketQuotes(row.aftermarket_quotes)?.length) continue;
      update.run(
        JSON.stringify([{ brand: "Design911", price: row.aftermarket_price }]),
        row.id,
      );
    }
  }

  /** ALTER existing DBs that predate paint/interior/top columns. */
  private ensureVehicleColumns() {
    const cols = this.db.prepare("PRAGMA table_info(vehicle)").all() as Array<{
      name: string;
    }>;
    const names = new Set(cols.map((c) => c.name));
    for (const col of ["paint_name", "paint_code", "interior", "top"] as const) {
      if (!names.has(col)) {
        this.db.exec(`ALTER TABLE vehicle ADD COLUMN ${col} TEXT`);
      }
    }
  }

  close() {
    this.db.close();
  }

  private ensureVehicle() {
    const row = this.db.prepare("SELECT id FROM vehicle WHERE id = 1").get();
    if (!row) {
      this.db
        .prepare(
          `INSERT INTO vehicle (id, year, model, trim, chassis, vin, current_km, avg_km_per_day, updated_at)
           VALUES (1, 2014, 'Boxster', 'S', '981', NULL, 0, NULL, ?)`,
        )
        .run(new Date().toISOString());
    }
  }

  getVehicle(): Vehicle {
    return this.db.prepare("SELECT * FROM vehicle WHERE id = 1").get() as Vehicle;
  }

  setMileage(nextKm: number, reason = "user_update"): Vehicle {
    const v = this.getVehicle();
    const applied = applyMileageSet(nextKm);
    if (!applied.ok) {
      throw new Error(`mileage_invalid:${nextKm}`);
    }
    const now = new Date().toISOString();
    this.db
      .prepare(
        `INSERT INTO mileage_audit (previous_km, new_km, reason, created_at) VALUES (?, ?, ?, ?)`,
      )
      .run(v.current_km, nextKm, reason, now);
    this.db
      .prepare(`UPDATE vehicle SET current_km = ?, updated_at = ? WHERE id = 1`)
      .run(nextKm, now);
    return this.getVehicle();
  }

  setAvgKmPerDay(avg: number | null): Vehicle {
    this.db
      .prepare(`UPDATE vehicle SET avg_km_per_day = ?, updated_at = ? WHERE id = 1`)
      .run(avg, new Date().toISOString());
    return this.getVehicle();
  }

  setVin(vin: string | null): Vehicle {
    this.db
      .prepare(`UPDATE vehicle SET vin = ?, updated_at = ? WHERE id = 1`)
      .run(vin, new Date().toISOString());
    return this.getVehicle();
  }

  setVehicleSettings(settings: VehicleSettings): Vehicle {
    const paint_name = settings.paint_name?.trim() || null;
    const paint_code = settings.paint_code?.trim() || null;
    const interior = settings.interior?.trim() || null;
    const top = settings.top?.trim() || null;
    this.db
      .prepare(
        `UPDATE vehicle SET paint_name = ?, paint_code = ?, interior = ?, top = ?, updated_at = ? WHERE id = 1`,
      )
      .run(paint_name, paint_code, interior, top, new Date().toISOString());
    return this.getVehicle();
  }

  listParts(): Part[] {
    return (
      this.db.prepare("SELECT * FROM parts ORDER BY system, name_zh").all() as Array<
        Record<string, unknown>
      >
    ).map(mapPartRow);
  }

  getPart(id: number): Part | undefined {
    const row = this.db
      .prepare("SELECT * FROM parts WHERE id = ?")
      .get(id) as Record<string, unknown> | undefined;
    return row ? mapPartRow(row) : undefined;
  }

  getPartBySku(sku: string): Part | undefined {
    const row = this.db
      .prepare("SELECT * FROM parts WHERE sku = ?")
      .get(sku) as Record<string, unknown> | undefined;
    return row ? mapPartRow(row) : undefined;
  }

  /** 更新名称/OEM 列；undefined 表示不改该列。 */
  updatePartNames(
    sku: string,
    patch: {
      name_en?: string | null;
      name_zh?: string | null;
      oem_number?: string | null;
      petka_note?: string | null;
      pr_label?: string | null;
    },
  ): Part | undefined {
    const existing = this.getPartBySku(sku);
    if (!existing) return undefined;
    const name_en =
      patch.name_en !== undefined
        ? patch.name_en?.trim() || null
        : existing.name_en;
    const name_zh =
      patch.name_zh !== undefined
        ? (patch.name_zh?.trim() || existing.name_zh)
        : existing.name_zh;
    const oem_number =
      patch.oem_number !== undefined
        ? patch.oem_number?.trim() || null
        : existing.oem_number;
    const petka_note =
      patch.petka_note !== undefined
        ? patch.petka_note?.trim() || null
        : existing.petka_note;
    const pr_label =
      patch.pr_label !== undefined
        ? patch.pr_label?.trim() || null
        : existing.pr_label;
    this.db
      .prepare(
        `UPDATE parts SET name_en = ?, name_zh = ?, oem_number = ?, petka_note = ?, pr_label = ? WHERE sku = ?`,
      )
      .run(name_en, name_zh, oem_number, petka_note, pr_label, sku);
    return this.getPartBySku(sku);
  }

  /** Insert or update by sku. Returns whether the row was newly inserted.
   * Omit `aftermarket_quotes` (undefined) to leave existing quotes untouched (old CSV path).
   * Omit `generation` (undefined) to leave existing generation untouched. */
  upsertPart(
    draft: Omit<
      Part,
      | "id"
      | "aftermarket_quotes"
      | "generation"
      | "name_en"
      | "petka_note"
      | "pr_label"
    > & {
      aftermarket_quotes?: AftermarketQuote[] | null;
      generation?: string | null;
      name_en?: string | null;
      petka_note?: string | null;
      pr_label?: string | null;
    },
  ): { sku: string; inserted: boolean } {
    const existing = this.getPartBySku(draft.sku);
    const hasQuotes = draft.aftermarket_quotes !== undefined;
    const hasGeneration = draft.generation !== undefined;
    const generation = hasGeneration
      ? draft.generation?.trim() || null
      : (existing?.generation ?? null);
    if (hasQuotes) {
      const normalized = normalizeAftermarketQuotes(draft.aftermarket_quotes);
      const amPrice =
        deriveAftermarketPrice(normalized) ?? draft.aftermarket_price;
      this.db
        .prepare(
          `INSERT INTO parts (
          sku, name_zh, oem_number, system, generation, interval_km, interval_months,
          oem_price, aftermarket_price, aftermarket_quotes, price_note, price_as_of, locator_hotspot, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sku) DO UPDATE SET
          name_zh = excluded.name_zh,
          oem_number = excluded.oem_number,
          system = excluded.system,
          generation = excluded.generation,
          interval_km = excluded.interval_km,
          interval_months = excluded.interval_months,
          oem_price = excluded.oem_price,
          aftermarket_price = excluded.aftermarket_price,
          aftermarket_quotes = excluded.aftermarket_quotes,
          price_note = excluded.price_note,
          price_as_of = excluded.price_as_of,
          locator_hotspot = excluded.locator_hotspot,
          notes = excluded.notes`,
        )
        .run(
          draft.sku,
          draft.name_zh,
          draft.oem_number,
          draft.system,
          generation,
          draft.interval_km,
          draft.interval_months,
          draft.oem_price,
          amPrice,
          serializeAftermarketQuotes(normalized),
          draft.price_note,
          draft.price_as_of,
          draft.locator_hotspot,
          draft.notes,
        );
    } else {
      this.db
        .prepare(
          `INSERT INTO parts (
          sku, name_zh, oem_number, system, generation, interval_km, interval_months,
          oem_price, aftermarket_price, price_note, price_as_of, locator_hotspot, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sku) DO UPDATE SET
          name_zh = excluded.name_zh,
          oem_number = excluded.oem_number,
          system = excluded.system,
          generation = excluded.generation,
          interval_km = excluded.interval_km,
          interval_months = excluded.interval_months,
          oem_price = excluded.oem_price,
          aftermarket_price = excluded.aftermarket_price,
          price_note = excluded.price_note,
          price_as_of = excluded.price_as_of,
          locator_hotspot = excluded.locator_hotspot,
          notes = excluded.notes`,
        )
        .run(
          draft.sku,
          draft.name_zh,
          draft.oem_number,
          draft.system,
          generation,
          draft.interval_km,
          draft.interval_months,
          draft.oem_price,
          draft.aftermarket_price,
          draft.price_note,
          draft.price_as_of,
          draft.locator_hotspot,
          draft.notes,
        );
    }
    return { sku: draft.sku, inserted: !existing };
  }

  upsertParts(
    drafts: Array<
      Omit<
        Part,
        | "id"
        | "aftermarket_quotes"
        | "generation"
        | "name_en"
        | "petka_note"
        | "pr_label"
      > & {
        aftermarket_quotes?: AftermarketQuote[] | null;
        generation?: string | null;
        name_en?: string | null;
        petka_note?: string | null;
        pr_label?: string | null;
      }
    >,
  ): {
    inserted: number;
    updated: number;
  } {
    let inserted = 0;
    let updated = 0;
    this.db.exec("BEGIN");
    try {
      for (const d of drafts) {
        const r = this.upsertPart(d);
        if (r.inserted) inserted++;
        else updated++;
      }
      this.db.exec("COMMIT");
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
    return { inserted, updated };
  }

  updatePartPrices(
    id: number,
    oem_price: number | null,
    aftermarket_price: number | null,
    price_note: string | null,
    price_as_of: string | null,
    aftermarket_quotes?: AftermarketQuote[] | null,
  ): Part {
    if (aftermarket_quotes !== undefined) {
      const normalized = normalizeAftermarketQuotes(aftermarket_quotes);
      const amPrice = deriveAftermarketPrice(normalized);
      this.db
        .prepare(
          `UPDATE parts SET oem_price = ?, aftermarket_price = ?, aftermarket_quotes = ?, price_note = ?, price_as_of = ? WHERE id = ?`,
        )
        .run(
          oem_price,
          amPrice,
          serializeAftermarketQuotes(normalized),
          price_note,
          price_as_of,
          id,
        );
    } else {
      this.db
        .prepare(
          `UPDATE parts SET oem_price = ?, aftermarket_price = ?, price_note = ?, price_as_of = ? WHERE id = ?`,
        )
        .run(oem_price, aftermarket_price, price_note, price_as_of, id);
    }
    return this.getPart(id)!;
  }

  addServiceRecord(input: {
    part_id?: number | null;
    title: string;
    replaced_at: string;
    odometer_km: number;
    brand?: string | null;
    cost?: number | null;
    notes?: string | null;
  }): ServiceRecord {
    const info = this.db
      .prepare(
        `INSERT INTO service_records (part_id, title, replaced_at, odometer_km, brand, cost, notes)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        input.part_id ?? null,
        input.title,
        input.replaced_at,
        input.odometer_km,
        input.brand ?? null,
        input.cost ?? null,
        input.notes ?? null,
      );
    return this.db
      .prepare("SELECT * FROM service_records WHERE id = ?")
      .get(Number(info.lastInsertRowid)) as ServiceRecord;
  }

  listServiceRecords(): ServiceRecord[] {
    return this.db
      .prepare("SELECT * FROM service_records ORDER BY replaced_at DESC, id DESC")
      .all() as ServiceRecord[];
  }

  deleteServiceRecord(id: number): { ok: true } {
    const n = Number(id);
    if (!Number.isInteger(n) || n <= 0) {
      throw new Error(`service_record_invalid:${id}`);
    }
    const info = this.db
      .prepare("DELETE FROM service_records WHERE id = ?")
      .run(n);
    if (!info.changes) throw new Error(`service_record_not_found:${n}`);
    return { ok: true };
  }

  latestServiceForPart(partId: number): ServiceRecord | undefined {
    return this.db
      .prepare(
        `SELECT * FROM service_records WHERE part_id = ? ORDER BY replaced_at DESC, id DESC LIMIT 1`,
      )
      .get(partId) as ServiceRecord | undefined;
  }

  partIntervalStatus(partId: number): IntervalResult | null {
    const part = this.getPart(partId);
    if (!part) return null;
    const last = this.latestServiceForPart(partId);
    const v = this.getVehicle();
    // No service_record → no_baseline (never invent delivery / purchase date).
    return computeInterval({
      nowKm: v.current_km,
      replacedAtKm: last?.odometer_km ?? null,
      replacedAtDate: last?.replaced_at ?? null,
      intervalKm: part.interval_km,
      intervalMonths: part.interval_months,
      avgKmPerDay: v.avg_km_per_day,
    });
  }

  seedIfEmpty(payload: SeedPayload) {
    const count = this.db.prepare("SELECT COUNT(*) AS c FROM parts").get() as {
      c: number;
    };
    if (count.c > 0) return { seeded: false };

    const insertPart = this.db.prepare(
      `INSERT INTO parts (sku, name_zh, oem_number, system, interval_km, interval_months,
        oem_price, aftermarket_price, price_note, price_as_of, locator_hotspot, notes)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );
    const insertFault = this.db.prepare(
      `INSERT INTO fault_kb (symptom, likely_causes, checks, related_part_sku)
       VALUES (?, ?, ?, ?)`,
    );

    this.db.exec("BEGIN");
    try {
      for (const p of payload.parts) {
        insertPart.run(
          p.sku,
          p.name_zh,
          p.oem_number,
          p.system,
          p.interval_km,
          p.interval_months,
          p.oem_price,
          p.aftermarket_price,
          p.price_note,
          p.price_as_of,
          p.locator_hotspot,
          p.notes,
        );
      }
      for (const f of payload.faults) {
        insertFault.run(
          f.symptom,
          f.likely_causes,
          f.checks,
          f.related_part_sku,
        );
      }
      if (payload.dtcs?.length) {
        this.insertDtcs(payload.dtcs);
      }
      this.db.exec("COMMIT");
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
    return { seeded: true, parts: payload.parts.length, faults: payload.faults.length };
  }

  private insertDtcs(dtcs: DtcEntry[]) {
    const insert = this.db.prepare(
      `INSERT INTO dtc_kb (code, title_zh, likely_causes, checks, related_part_sku)
       VALUES (?, ?, ?, ?, ?)`,
    );
    for (const d of dtcs) {
      insert.run(
        d.code.toUpperCase(),
        d.title_zh,
        d.likely_causes,
        d.checks,
        d.related_part_sku,
      );
    }
  }

  /** Seed dtc_kb when empty (existing DBs that already have parts). */
  seedDtcIfEmpty(dtcs: DtcEntry[]) {
    const count = this.db.prepare("SELECT COUNT(*) AS c FROM dtc_kb").get() as {
      c: number;
    };
    if (count.c > 0) return { seeded: false };
    this.db.exec("BEGIN");
    try {
      this.insertDtcs(dtcs);
      this.db.exec("COMMIT");
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
    return { seeded: true, dtcs: dtcs.length };
  }

  /** Insert bootstrap codes missing from dtc_kb (existing DBs after seed grow). */
  seedDtcMissing(dtcs: DtcEntry[]) {
    const existing = new Set(
      (
        this.db.prepare("SELECT code FROM dtc_kb").all() as { code: string }[]
      ).map((r) => r.code.toUpperCase()),
    );
    const missing = dtcs.filter((d) => !existing.has(d.code.toUpperCase()));
    if (!missing.length) return { inserted: 0 };
    this.db.exec("BEGIN");
    try {
      this.insertDtcs(missing);
      this.db.exec("COMMIT");
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
    return { inserted: missing.length };
  }

  searchDtc(prefix: string): DtcEntry[] {
    const p = String(prefix ?? "").trim().toUpperCase();
    if (!p) return this.listDtcs();
    return this.db
      .prepare(
        `SELECT code, title_zh, likely_causes, checks, related_part_sku
         FROM dtc_kb WHERE code LIKE ? || '%' ORDER BY code`,
      )
      .all(p) as DtcEntry[];
  }

  getDtcByCode(code: string): DtcEntry | undefined {
    const c = String(code ?? "").trim().toUpperCase();
    if (!c) return undefined;
    return this.db
      .prepare(
        `SELECT code, title_zh, likely_causes, checks, related_part_sku
         FROM dtc_kb WHERE code = ?`,
      )
      .get(c) as DtcEntry | undefined;
  }

  listDtcs(): DtcEntry[] {
    return this.db
      .prepare(
        `SELECT code, title_zh, likely_causes, checks, related_part_sku
         FROM dtc_kb ORDER BY code`,
      )
      .all() as DtcEntry[];
  }

  createObdSession(input: {
    started_at?: string;
    odometer_km?: number | null;
    adapter?: string | null;
    note?: string | null;
  }): ObdSession {
    const info = this.db
      .prepare(
        `INSERT INTO obd_sessions (started_at, odometer_km, adapter, note)
         VALUES (?, ?, ?, ?)`,
      )
      .run(
        input.started_at ?? new Date().toISOString(),
        input.odometer_km ?? null,
        input.adapter ?? null,
        input.note ?? null,
      );
    return this.db
      .prepare("SELECT * FROM obd_sessions WHERE id = ?")
      .get(Number(info.lastInsertRowid)) as ObdSession;
  }

  addObdDtc(input: {
    session_id: number;
    code: string;
    status?: string;
    raw_json?: string | null;
  }): ObdDtc {
    const sessionId = Number(input.session_id);
    const session = this.db
      .prepare("SELECT id FROM obd_sessions WHERE id = ?")
      .get(sessionId) as { id: number } | undefined;
    if (!session) throw new Error(`obd_session_not_found:${sessionId}`);
    const code = String(input.code ?? "").trim().toUpperCase();
    if (!code) throw new Error("obd_dtc_code_required");
    const info = this.db
      .prepare(
        `INSERT INTO obd_dtcs (session_id, code, status, raw_json)
         VALUES (?, ?, ?, ?)`,
      )
      .run(
        sessionId,
        code,
        input.status ?? "manual",
        input.raw_json ?? null,
      );
    return this.db
      .prepare("SELECT * FROM obd_dtcs WHERE id = ?")
      .get(Number(info.lastInsertRowid)) as ObdDtc;
  }

  listObdSessions(): ObdSession[] {
    return this.db
      .prepare("SELECT * FROM obd_sessions ORDER BY started_at DESC, id DESC")
      .all() as ObdSession[];
  }

  listObdDtcs(sessionId: number): ObdDtc[] {
    const sid = Number(sessionId);
    return this.db
      .prepare(
        "SELECT * FROM obd_dtcs WHERE session_id = ? ORDER BY id ASC",
      )
      .all(sid) as ObdDtc[];
  }

  listFaults(): FaultEntry[] {
    return this.db.prepare("SELECT * FROM fault_kb ORDER BY id").all() as FaultEntry[];
  }

  addFaultLog(input: {
    logged_at: string;
    odometer_km: number;
    symptom: string;
    area_hypothesis?: string | null;
    action?: string | null;
    result?: string | null;
    closed?: boolean | number;
    related_part_sku?: string | null;
    coding_snapshot_id?: number | null;
  }): FaultLog {
    const closed = input.closed ? 1 : 0;
    let codingId: number | null = null;
    if (input.coding_snapshot_id != null) {
      const n = Number(input.coding_snapshot_id);
      if (!Number.isInteger(n) || n <= 0) {
        throw new Error(`coding_snapshot_invalid:${input.coding_snapshot_id}`);
      }
      const snap = this.db
        .prepare("SELECT id FROM coding_snapshots WHERE id = ?")
        .get(n) as { id: number } | undefined;
      if (!snap) throw new Error(`coding_snapshot_not_found:${n}`);
      codingId = n;
    }
    const info = this.db
      .prepare(
        `INSERT INTO fault_logs
          (logged_at, odometer_km, symptom, area_hypothesis, action, result,
           closed, related_part_sku, coding_snapshot_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        input.logged_at,
        input.odometer_km,
        input.symptom,
        input.area_hypothesis ?? null,
        input.action ?? null,
        input.result ?? null,
        closed,
        input.related_part_sku ?? null,
        codingId,
      );
    return this.db
      .prepare("SELECT * FROM fault_logs WHERE id = ?")
      .get(Number(info.lastInsertRowid)) as FaultLog;
  }

  listFaultLogs(): FaultLog[] {
    return this.db
      .prepare(
        `SELECT * FROM fault_logs
         ORDER BY closed ASC, logged_at DESC, id DESC`,
      )
      .all() as FaultLog[];
  }

  closeFaultLog(id: number): FaultLog {
    const row = this.db
      .prepare("SELECT * FROM fault_logs WHERE id = ?")
      .get(id) as FaultLog | undefined;
    if (!row) throw new Error(`fault_log_not_found:${id}`);
    this.db.prepare("UPDATE fault_logs SET closed = 1 WHERE id = ?").run(id);
    return this.db
      .prepare("SELECT * FROM fault_logs WHERE id = ?")
      .get(id) as FaultLog;
  }

  addCodingSnapshot(input: {
    system: string;
    function_name: string;
    sub_function?: string | null;
    before_value: string;
    after_value: string;
    note?: string | null;
    odometer_km: number;
    recorded_at: string;
  }): CodingSnapshot {
    const info = this.db
      .prepare(
        `INSERT INTO coding_snapshots
          (system, function_name, sub_function, before_value, after_value, note, odometer_km, recorded_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        input.system,
        input.function_name,
        input.sub_function ?? null,
        input.before_value,
        input.after_value,
        input.note ?? null,
        input.odometer_km,
        input.recorded_at,
      );
    return this.db
      .prepare("SELECT * FROM coding_snapshots WHERE id = ?")
      .get(Number(info.lastInsertRowid)) as CodingSnapshot;
  }

  listCodingSnapshots(): CodingSnapshot[] {
    return this.db
      .prepare("SELECT * FROM coding_snapshots ORDER BY recorded_at DESC, id DESC")
      .all() as CodingSnapshot[];
  }
}

export function defaultDbPath(): string {
  const appData =
    process.env.APPDATA ||
    process.env.HOME ||
    process.env.USERPROFILE ||
    ".";
  return path.join(appData, "porsche981", "garage.db");
}
