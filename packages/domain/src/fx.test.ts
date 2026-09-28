import { describe, expect, it } from "vitest";
import {
  DEFAULT_FX_TABLE,
  convertViaCny,
  formatCny,
  formatMoneyAsCny,
  rateToCny,
  toCny,
} from "./fx.js";

describe("toCny", () => {
  it("converts GBP with default manual rate", () => {
    expect(toCny(24.58, "GBP")).toBe(226.14);
    expect(rateToCny("GBP")).toBe(9.2);
  });

  it("passes CNY through", () => {
    expect(toCny(100, "CNY")).toBe(100);
  });

  it("returns null when currency/rate missing", () => {
    expect(toCny(10, null)).toBeNull();
    expect(toCny(10, "JPY")).toBeNull();
    expect(toCny(null, "GBP")).toBeNull();
  });
});

describe("convertViaCny", () => {
  it("converts GBP aftermarket into declared EUR", () => {
    expect(convertViaCny(14.95, "GBP", "EUR")).toBe(17.41);
  });

  it("is identity for same currency", () => {
    expect(convertViaCny(28.61, "EUR", "EUR")).toBe(28.61);
  });
});

describe("formatMoneyAsCny", () => {
  it("shows ¥ primary and GBP detail", () => {
    const r = formatMoneyAsCny(24.58, "GBP", DEFAULT_FX_TABLE);
    expect(r.primary).toBe("¥226.14");
    expect(r.detail).toMatch(/24\.58 GBP/);
    expect(formatCny(r.cny)).toBe("¥226.14");
  });

  it("dash when amount empty", () => {
    expect(formatMoneyAsCny(null, "GBP").primary).toBe("—");
  });
});
