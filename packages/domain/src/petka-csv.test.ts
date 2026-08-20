import { describe, expect, it } from "vitest";
import {
  bulkPartSku,
  parseAftermarketQuotesCompact,
  parsePetkaBulkText,
  parsePetkaPartsCsv,
} from "./petka-csv.js";

const HEADER =
  "sku,name_zh,oem_number,system,zone,oem_price,currency,price_as_of,aftermarket_price,applicability_note,design911_checked,interval_km,interval_months,locator_hotspot,notes";

const HEADER_WITH_QUOTES =
  "sku,name_zh,oem_number,system,zone,oem_price,currency,price_as_of,aftermarket_price,aftermarket_quotes,applicability_note,design911_checked,interval_km,interval_months,locator_hotspot,notes";

describe("parsePetkaPartsCsv", () => {
  it("maps a valid row and folds currency into price_note", () => {
    const csv = [
      HEADER,
      "oil-filter,机油滤清器,9A1.107.201.00,发动机,engine-bay,12.5,GBP,2026-07-30,,适用 Boxster S,yes,15000,12,engine-bay,随机油",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.drafts).toHaveLength(1);
    expect(r.drafts[0]).toMatchObject({
      sku: "oil-filter",
      name_zh: "机油滤清器",
      oem_number: "9A1.107.201.00",
      system: "发动机",
      oem_price: 12.5,
      price_note: "currency=GBP; design911=yes",
      price_as_of: "2026-07-30",
      locator_hotspot: "engine-bay",
      interval_km: 15000,
      interval_months: 12,
      notes: "适用 Boxster S | 随机油",
    });
  });

  it("skips template example rows", () => {
    const csv = [
      HEADER,
      "oil-filter-example,机油滤清器（示例）,9A1.107.201.00,发动机,engine-bay,,GBP,,,,no,15000,12,engine-bay,删掉",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.drafts).toHaveLength(0);
    expect(r.skipped[0]?.reason).toMatch(/example|示例/);
  });

  it("errors when currency missing", () => {
    const csv = [
      HEADER,
      "pad,刹车片,970.xxx,制动,brakes,10,,,20,,,30000,24,brakes,",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.drafts).toHaveLength(0);
    expect(r.errors[0]?.message).toMatch(/currency/);
  });

  it("uses zone as locator_hotspot fallback and keeps quoted commas", () => {
    const csv = [
      HEADER,
      'arm,控制臂,"9A1,000.00",底盘,chassis,,GBP,,,,,,,,',
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.drafts[0]?.oem_number).toBe("9A1,000.00");
    expect(r.drafts[0]?.locator_hotspot).toBe("chassis");
  });

  it("parses compact aftermarket_quotes and derives min aftermarket_price", () => {
    const csv = [
      HEADER_WITH_QUOTES,
      "oil-filter,机油滤清器,9A1.107.201.00,发动机,engine-bay,,GBP,,,Design911:14.95|Mann:9.50,,yes,15000,12,engine-bay,",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.drafts[0]?.aftermarket_quotes).toEqual([
      { brand: "Design911", price: 14.95 },
      { brand: "Mann", price: 9.5 },
    ]);
    // aftermarket_price = lowest quote; full list stays in aftermarket_quotes
    expect(r.drafts[0]?.aftermarket_price).toBe(9.5);
  });

  it("derives min when Design911 absent", () => {
    const csv = [
      HEADER_WITH_QUOTES,
      "pad,刹车片,970.xxx,制动,brakes,,GBP,,,Brembo:99|Textar:55,,,,,,brakes,",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.drafts[0]?.aftermarket_price).toBe(55);
  });

  it("old CSV without aftermarket_quotes column still works", () => {
    const csv = [
      HEADER,
      "oil-filter,机油滤清器,9A1.107.201.00,发动机,engine-bay,,GBP,,12.5,,yes,15000,12,engine-bay,",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.drafts[0]?.aftermarket_price).toBe(12.5);
    expect(r.drafts[0]).not.toHaveProperty("aftermarket_quotes");
  });

  it("maps optional generation column", () => {
    const csv = [
      HEADER + ",generation",
      "981-9A110722400,Oil filter,9A1.107.224.00,bulk,bulk,28.61,GBP,2026-08-02,,,no,,,bulk,PETKA bulk,981",
    ].join("\n");
    const r = parsePetkaPartsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.drafts[0]).toMatchObject({
      sku: "981-9A110722400",
      generation: "981",
      oem_price: 28.61,
    });
  });

  it("parsePetkaBulkText extracts OEM and price", () => {
    const text = [
      "Pos\tPart No.\tDescription\tPrice GBP",
      "001\t9A1.107.224.00\tOil filter\t28.61",
      "002\t000.043.206.69\tEngine oil\t14.50",
    ].join("\n");
    const rows = parsePetkaBulkText(text);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      oem_number: "9A1.107.224.00",
      oem_price: 28.61,
    });
    expect(bulkPartSku("981", "9A1.107.224.00")).toBe("981-9A110722400");
  });
});
