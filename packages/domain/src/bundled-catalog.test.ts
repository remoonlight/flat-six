import { describe, expect, it } from "vitest";
import {
  assembleBundledCatalog,
  harmonizeZoneDraft,
  isShippedCatalogPriceNote,
} from "./bundled-catalog.js";
import { DEFAULT_FX_TABLE } from "./fx.js";
import { assertPetkaEpcFixtures, parsePetkaEpcCsv } from "./petka-epc.js";
import { parsePetkaPartsCsv } from "./petka-csv.js";

const ZONE_HEADER =
  "sku,name_zh,oem_number,system,zone,oem_price,currency,price_as_of,aftermarket_price,applicability_note,design911_checked,interval_km,interval_months,locator_hotspot,notes";

describe("harmonizeZoneDraft", () => {
  it("converts GBP aftermarket onto EUR OEM and keeps orig note", () => {
    const csv = [
      ZONE_HEADER,
      'oil-filter,机油滤清器,9A1.107.224.00,发动机,engine-bay,28.61,EUR,2026-07-31,14.95,981,partial,10000,12,engine-bay,teile.com OEM €28.61；非PETKA价；Design911 aftermarket_price=14.95 GBP 保留',
    ].join("\n");
    const parsed = parsePetkaPartsCsv(csv);
    expect(parsed.errors).toEqual([]);
    const h = harmonizeZoneDraft(parsed.drafts[0]!, DEFAULT_FX_TABLE);
    expect(h.oem_price).toBe(28.61);
    expect(h.aftermarket_price).toBe(17.41);
    expect(h.price_note).toMatch(/currency=EUR/);
    expect(h.price_note).toMatch(/petka_verified=0/);
    expect(h.price_note).toMatch(/source=teile\/Design911/);
    expect(h.price_note).toMatch(/aftermarket_orig=14\.95 GBP/);
    expect(h.aftermarket_quotes).toEqual([{ brand: "Design911", price: 17.41 }]);
  });
});

describe("parsePetkaEpcCsv", () => {
  it("picks cleaned bilingual names", () => {
    const csv = `record_type,vehicle_model,vehicle_year,hg,drawing,pos,part_number,name_zh,name_en,note,qty,model_codes,model_meaning
part,981,2016,104,x,1,9A1 107 224 00,机油滤芯 配合使用: foo,Oil filter insert also use: bar,左侧,1,,
part,981,2016,103,x,1,997 111 107 31,密封件,Seal,,1,,
part,981,2016,0,x,1,WHT 008 240,组合螺栓,Bolt with washer,,1,,
`;
    const { byCompact } = parsePetkaEpcCsv(csv);
    assertPetkaEpcFixtures(byCompact);
  });
});

describe("assembleBundledCatalog", () => {
  it("overlays zone prices and EPC names onto bootstrap sku", () => {
    const bootstrap = JSON.stringify({
      parts: [
        {
          sku: "oil-filter",
          name_zh: "机油滤清器",
          oem_number: "9A1.107.224.00",
          system: "发动机",
          interval_km: 10000,
          interval_months: 12,
          oem_price: null,
          aftermarket_price: null,
          price_note: null,
          price_as_of: null,
          locator_hotspot: "engine-bay",
          notes: "随机油更换",
        },
      ],
    });
    const zone = [
      ZONE_HEADER,
      "oil-filter,机油滤清器,9A1.107.224.00,发动机,engine-bay,28.61,EUR,2026-07-31,14.95,981,partial,10000,12,engine-bay,teile.com；Design911 aftermarket_price=14.95 GBP 保留",
    ].join("\n");
    const bulk = [
      ZONE_HEADER + ",generation",
      "981-9A110722400,9A110722400,9A1.107.224.00,bulk,bulk,28.61,EUR,2026-08-02,,,no,,,bulk,非PETKA价，teile.com 快照,981",
    ].join("\n");
    const epc = `record_type,vehicle_model,vehicle_year,hg,drawing,pos,part_number,name_zh,name_en,note,qty,model_codes,model_meaning
part,981,2016,104,x,1,9A1 107 224 00,机油滤芯,Oil filter insert,,1,,
`;
    const out = assembleBundledCatalog({
      bootstrapJson: bootstrap,
      zoneCsvs: [zone],
      bulkCsvs: [bulk],
      epcCsv: epc,
    });
    const oil = out.rows.find((r) => r.sku === "oil-filter")!;
    expect(oil.name_zh).toBe("机油滤芯");
    expect(oil.name_en).toBe("Oil filter insert");
    expect(oil.oem_price).toBe(28.61);
    expect(oil.aftermarket_price).toBe(17.41);
    expect(oil.interval_km).toBe(10000);
    expect(oil.seedNameZh).toBe("机油滤清器");
    const bulkOil = out.rows.find((r) => r.sku === "981-9A110722400")!;
    expect(bulkOil.generation).toBe("981");
    expect(bulkOil.name_zh).toBe("机油滤芯");
    const withoutBulkQuote = assembleBundledCatalog({
      bootstrapJson: bootstrap,
      zoneCsvs: [zone],
      bulkCsvs: [],
      epcCsv: epc,
    }).rows.find((r) => r.sku === "981-9A110722400")!;
    expect(withoutBulkQuote.oem_price).toBe(28.61);
    expect(withoutBulkQuote.aftermarket_price).toBeNull();
    expect(withoutBulkQuote.price_as_of).toBe("2026-07-31");
    expect(withoutBulkQuote.price_note).toContain("quote_sku=oil-filter");
  });

  it("still inserts 981 EPC sku when only a 982 row shares the OEM", () => {
    const bootstrap = JSON.stringify({ parts: [] });
    const bulk = [
      ZONE_HEADER + ",generation",
      "982-WHT008240,WHT008240,WHT008240,bulk,bulk,1,EUR,2026-08-02,,,no,,,bulk,非PETKA价,982",
    ].join("\n");
    const epc = `record_type,vehicle_model,vehicle_year,hg,drawing,pos,part_number,name_zh,name_en,note,qty,model_codes,model_meaning
part,981,2016,0,x,1,WHT 008 240,组合螺栓,Bolt with washer,,1,,
`;
    const out = assembleBundledCatalog({
      bootstrapJson: bootstrap,
      zoneCsvs: [],
      bulkCsvs: [bulk],
      epcCsv: epc,
    });
    expect(out.rows.find((r) => r.sku === "982-WHT008240")?.generation).toBe(
      "982",
    );
    const p981 = out.rows.find((r) => r.sku === "981-WHT008240");
    expect(p981?.generation).toBe("981");
    expect(p981?.name_zh).toBe("组合螺栓");
  });

  it("fills missing OEM prices from exact teile hits without creating unknown SKUs", () => {
    const bootstrap = JSON.stringify({ parts: [] });
    const epc = `record_type,vehicle_model,vehicle_year,hg,drawing,pos,part_number,name_zh,name_en,note,qty,model_codes,model_meaning
part,981,2016,4,x,1,991 341 158 06,车轮支架,Wheel carrier,,1,,
`;
    const exact = JSON.stringify({
      hits: [
        {
          compact: "99134115806",
          priceNumber: "99134115806",
          price: 784.02,
          as_of: "2026-08-18",
        },
        {
          compact: "NOMATCH999",
          priceNumber: "NOMATCH999",
          price: 9,
          as_of: "2026-08-18",
        },
        {
          compact: "BAD",
          priceNumber: "OTHER",
          price: 1,
          as_of: "2026-08-18",
        },
      ],
    });
    const out = assembleBundledCatalog({
      bootstrapJson: bootstrap,
      zoneCsvs: [],
      bulkCsvs: [],
      epcCsv: epc,
      exactOemJson: exact,
    });
    const wheel = out.rows.find((r) => r.sku === "981-99134115806")!;
    expect(wheel.name_zh).toBe("车轮支架");
    expect(wheel.name_en).toBe("Wheel carrier");
    expect(wheel.oem_price).toBe(784.02);
    expect(wheel.price_note).toMatch(/source=teile\.com/);
    expect(out.rows.find((r) => r.sku === "981-NOMATCH999")).toBeUndefined();
  });
});

describe("isShippedCatalogPriceNote", () => {
  it("allows empty and bundled placeholders, rejects custom metadata", () => {
    expect(isShippedCatalogPriceNote(null)).toBe(true);
    expect(
      isShippedCatalogPriceNote(
        "currency=EUR; petka_verified=0; source=teile.com",
      ),
    ).toBe(true);
    expect(isShippedCatalogPriceNote("currency=EUR; shop=local")).toBe(false);
    expect(isShippedCatalogPriceNote("currency=GBP; leftover")).toBe(false);
  });
});
