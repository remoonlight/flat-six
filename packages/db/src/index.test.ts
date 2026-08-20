import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it } from "vitest";
import { GarageDb } from "./index.js";

const tempDbs: string[] = [];

function openTempDb(): GarageDb {
  const dbPath = path.join(
    os.tmpdir(),
    `porsche981-db-test-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}.db`,
  );
  tempDbs.push(dbPath);
  return new GarageDb(dbPath);
}

afterEach(() => {
  while (tempDbs.length) {
    const p = tempDbs.pop()!;
    try {
      fs.unlinkSync(p);
    } catch {
      /* */
    }
  }
});

describe("setVehicleSettings", () => {
  it("persists paint/interior/top and round-trips via getVehicle", () => {
    const db = openTempDb();
    const v = db.setVehicleSettings({
      paint_name: "Guards Red",
      paint_code: "L84A",
      interior: "A11",
      top: "A27",
    });
    expect(v.paint_name).toBe("Guards Red");
    expect(v.paint_code).toBe("L84A");
    expect(v.interior).toBe("A11");
    expect(v.top).toBe("A27");
    const again = db.getVehicle();
    expect(again.paint_name).toBe("Guards Red");
    db.close();
  });

  it("migrates legacy vehicle table without paint columns", () => {
    const dbPath = path.join(
      os.tmpdir(),
      `porsche981-db-legacy-${process.pid}-${Date.now()}.db`,
    );
    tempDbs.push(dbPath);
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE vehicle (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        year INTEGER NOT NULL,
        model TEXT NOT NULL,
        trim TEXT NOT NULL,
        chassis TEXT NOT NULL,
        vin TEXT,
        current_km INTEGER NOT NULL DEFAULT 0,
        avg_km_per_day REAL,
        updated_at TEXT NOT NULL
      );
      INSERT INTO vehicle (id, year, model, trim, chassis, vin, current_km, updated_at)
      VALUES (1, 2014, 'Boxster', 'S', '981', NULL, 0, '2026-01-01T00:00:00.000Z');
    `);
    legacy.close();
    const db = new GarageDb(dbPath);
    db.setVehicleSettings({
      paint_name: "Mahogany Metallic",
      paint_code: null,
      interior: "7J0",
      top: "A27",
    });
    expect(db.getVehicle().paint_name).toBe("Mahogany Metallic");
    db.close();
  });
});

describe("setMileage", () => {
  it("allows increase and same km", () => {
    const db = openTempDb();
    expect(db.setMileage(45_000).current_km).toBe(45_000);
    expect(db.setMileage(45_000).current_km).toBe(45_000);
    expect(db.setMileage(50_000).current_km).toBe(50_000);
    db.close();
  });

  it("rejects decrease", () => {
    const db = openTempDb();
    db.setMileage(45_000);
    expect(() => db.setMileage(40_000)).toThrow(/mileage_decrease_not_allowed/);
    expect(db.getVehicle().current_km).toBe(45_000);
    db.close();
  });
});

describe("addFaultLog", () => {
  it("rejects invalid coding_snapshot_id", () => {
    const db = openTempDb();
    const base = {
      logged_at: "2026-07-31",
      odometer_km: 50_000,
      symptom: "test",
    };
    expect(() =>
      db.addFaultLog({ ...base, coding_snapshot_id: 0 }),
    ).toThrow(/coding_snapshot_invalid/);
    expect(() =>
      db.addFaultLog({ ...base, coding_snapshot_id: -1 }),
    ).toThrow(/coding_snapshot_invalid/);
    expect(() =>
      db.addFaultLog({ ...base, coding_snapshot_id: 999 }),
    ).toThrow(/coding_snapshot_not_found/);
    db.close();
  });

  it("accepts null coding_snapshot_id", () => {
    const db = openTempDb();
    const row = db.addFaultLog({
      logged_at: "2026-07-31",
      odometer_km: 50_000,
      symptom: "noise",
      coding_snapshot_id: null,
    });
    expect(row.coding_snapshot_id).toBeNull();
    db.close();
  });
});

describe("partIntervalStatus no_baseline", () => {
  it("returns no_baseline when part has no service_record", () => {
    const db = openTempDb();
    db.seedIfEmpty({
      parts: [
        {
          sku: "engine-oil",
          name_zh: "机油",
          oem_number: null,
          system: "engine",
          interval_km: 15_000,
          interval_months: 12,
          oem_price: null,
          aftermarket_price: null,
          aftermarket_quotes: null,
          price_note: null,
          price_as_of: null,
          locator_hotspot: null,
          notes: null,
          generation: null,
        },
      ],
      faults: [],
    });
    db.setMileage(45_000);
    const oil = db.listParts().find((p) => p.sku === "engine-oil");
    expect(oil).toBeTruthy();
    const status = db.partIntervalStatus(oil!.id);
    expect(status?.status).toBe("no_baseline");
    expect(status?.remainingKm).toBeNull();
    expect(status?.remainingDays).toBeNull();
    expect(status?.nextDueKm).toBeNull();
    expect(status?.nextDueDate).toBeNull();
    db.close();
  });
});

describe("aftermarket_quotes", () => {
  it("migrates legacy aftermarket_price into Design911 quote", () => {
    const dbPath = path.join(
      os.tmpdir(),
      `porsche981-db-am-migrate-${process.pid}-${Date.now()}.db`,
    );
    tempDbs.push(dbPath);
    const legacy = new DatabaseSync(dbPath);
    legacy.exec(`
      CREATE TABLE parts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL UNIQUE,
        name_zh TEXT NOT NULL,
        oem_number TEXT,
        system TEXT NOT NULL,
        interval_km INTEGER,
        interval_months INTEGER,
        oem_price REAL,
        aftermarket_price REAL,
        price_note TEXT,
        price_as_of TEXT,
        locator_hotspot TEXT,
        notes TEXT
      );
      INSERT INTO parts (sku, name_zh, system, aftermarket_price)
      VALUES ('brake-pad', '刹车片', 'brakes', 45.5);
    `);
    legacy.close();
    const db = new GarageDb(dbPath);
    const part = db.getPartBySku("brake-pad");
    expect(part?.aftermarket_quotes).toEqual([
      { brand: "Design911", price: 45.5 },
    ]);
    expect(part?.aftermarket_price).toBe(45.5);
    db.close();
  });

  it("round-trips quotes and syncs aftermarket_price from Design911", () => {
    const db = openTempDb();
    db.seedIfEmpty({
      parts: [
        {
          sku: "oil-filter",
          name_zh: "机滤",
          oem_number: null,
          system: "engine",
          interval_km: null,
          interval_months: null,
          oem_price: 10,
          aftermarket_price: null,
          aftermarket_quotes: null,
          price_note: "currency=GBP",
          price_as_of: "2026-01-01",
          locator_hotspot: null,
          notes: null,
        },
      ],
      faults: [],
    });
    const part = db.getPartBySku("oil-filter")!;
    const updated = db.updatePartPrices(
      part.id,
      10,
      null,
      "currency=GBP",
      "2026-01-01",
      [
        { brand: "Design911", price: 12.3 },
        { brand: "Uro", price: 8.5 },
      ],
    );
    expect(updated.aftermarket_quotes).toEqual([
      { brand: "Design911", price: 12.3 },
      { brand: "Uro", price: 8.5 },
    ]);
    expect(updated.aftermarket_price).toBe(12.3);
    const again = db.getPart(part.id)!;
    expect(again.aftermarket_quotes).toHaveLength(2);
    db.close();
  });

  it("upsertPart writes aftermarket_quotes from CSV draft", () => {
    const db = openTempDb();
    const { inserted } = db.upsertPart({
      sku: "front-pad",
      name_zh: "前刹车片",
      oem_number: null,
      system: "brakes",
      interval_km: null,
      interval_months: null,
      oem_price: 200,
      aftermarket_price: 99,
      aftermarket_quotes: [
        { brand: "Design911", price: 99 },
        { brand: "Brembo", price: 88 },
      ],
      price_note: "currency=GBP",
      price_as_of: "2026-08-02",
      locator_hotspot: "brakes",
      notes: null,
    });
    expect(inserted).toBe(true);
    const part = db.getPartBySku("front-pad")!;
    expect(part.aftermarket_quotes).toEqual([
      { brand: "Design911", price: 99 },
      { brand: "Brembo", price: 88 },
    ]);
    expect(part.aftermarket_price).toBe(99);
    db.upsertPart({
      sku: "front-pad",
      name_zh: "前刹车片",
      oem_number: null,
      system: "brakes",
      interval_km: null,
      interval_months: null,
      oem_price: 210,
      aftermarket_price: 12,
      price_note: "currency=GBP",
      price_as_of: "2026-08-02",
      locator_hotspot: "brakes",
      notes: null,
    });
    const kept = db.getPartBySku("front-pad")!;
    expect(kept.aftermarket_quotes).toHaveLength(2);
    expect(kept.oem_price).toBe(210);
    db.close();
  });

  it("updatePartNames can set oem_number without touching prices", () => {
    const db = openTempDb();
    db.upsertPart({
      sku: "oil-filter",
      name_zh: "机油滤芯",
      oem_number: "9A110722400",
      system: "engine",
      interval_km: null,
      interval_months: null,
      oem_price: 28.61,
      aftermarket_price: null,
      price_note: "currency=EUR",
      price_as_of: "2026-08-02",
      locator_hotspot: null,
      notes: null,
    });
    const updated = db.updatePartNames("oil-filter", {
      name_zh: "机油滤芯 滤芯",
      name_en: "Oil filter insert",
      oem_number: "9A1.107.224.00",
    });
    expect(updated?.oem_number).toBe("9A1.107.224.00");
    expect(updated?.name_zh).toBe("机油滤芯 滤芯");
    expect(updated?.name_en).toBe("Oil filter insert");
    expect(updated?.oem_price).toBe(28.61);
    db.close();
  });

  it("updatePartNames can set petka_note and pr_label", () => {
    const db = openTempDb();
    db.upsertPart({
      sku: "hose",
      name_zh: "冷却水管",
      oem_number: "991.106.131.03",
      system: "engine",
      interval_km: null,
      interval_months: null,
      oem_price: null,
      aftermarket_price: null,
      price_note: null,
      price_as_of: null,
      locator_hotspot: null,
      notes: null,
    });
    const updated = db.updatePartNames("hose", {
      petka_note: "左侧",
      pr_label: "447 紧急备用轮胎",
    });
    expect(updated?.petka_note).toBe("左侧");
    expect(updated?.pr_label).toBe("447 紧急备用轮胎");
    expect(updated?.name_zh).toBe("冷却水管");
    db.close();
  });

  it("upsertPart persists generation without touching quotes", () => {
    const db = openTempDb();
    db.upsertPart({
      sku: "981-9A110722400",
      name_zh: "Oil filter",
      oem_number: "9A1.107.224.00",
      system: "bulk",
      generation: "981",
      interval_km: null,
      interval_months: null,
      oem_price: 28.61,
      aftermarket_price: null,
      aftermarket_quotes: [{ brand: "Design911", price: 14 }],
      price_note: "currency=GBP",
      price_as_of: "2026-08-02",
      locator_hotspot: "bulk",
      notes: "PETKA bulk",
    });
    const part = db.getPartBySku("981-9A110722400")!;
    expect(part.generation).toBe("981");
    expect(part.aftermarket_quotes).toHaveLength(1);
    db.upsertPart({
      sku: "981-9A110722400",
      name_zh: "Oil filter",
      oem_number: "9A1.107.224.00",
      system: "bulk",
      generation: "981",
      interval_km: null,
      interval_months: null,
      oem_price: 29,
      aftermarket_price: null,
      price_note: "currency=GBP",
      price_as_of: "2026-08-02",
      locator_hotspot: "bulk",
      notes: "PETKA bulk",
    });
    const kept = db.getPartBySku("981-9A110722400")!;
    expect(kept.generation).toBe("981");
    expect(kept.oem_price).toBe(29);
    expect(kept.aftermarket_quotes).toHaveLength(1);
    db.close();
  });
});

describe("OBD session DTCs", () => {
  const sampleDtcs = [
    {
      code: "P0300",
      title_zh: "失火",
      likely_causes: "a",
      checks: "b",
      related_part_sku: null,
    },
    {
      code: "P0562",
      title_zh: "电压低",
      likely_causes: "c",
      checks: "d",
      related_part_sku: "battery",
    },
  ];

  it("listObdDtcs returns codes for session", () => {
    const db = openTempDb();
    db.seedIfEmpty({ parts: [], faults: [], dtcs: sampleDtcs });
    const session = db.createObdSession({ odometer_km: 10_000 });
    db.addObdDtc({ session_id: session.id, code: "P0300" });
    db.addObdDtc({ session_id: session.id, code: "P0562" });
    const rows = db.listObdDtcs(session.id);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.code)).toEqual(["P0300", "P0562"]);
    db.close();
  });

  it("seedDtcMissing inserts only new codes", () => {
    const db = openTempDb();
    db.seedIfEmpty({ parts: [], faults: [], dtcs: [sampleDtcs[0]] });
    expect(db.listDtcs()).toHaveLength(1);
    const res = db.seedDtcMissing(sampleDtcs);
    expect(res.inserted).toBe(1);
    expect(db.listDtcs()).toHaveLength(2);
    expect(db.seedDtcMissing(sampleDtcs).inserted).toBe(0);
    db.close();
  });
});
