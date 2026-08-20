import { describe, expect, it } from "vitest";
import {
  isPetkaPriceVerified,
  markPetkaPriceVerified,
  needsPetkaPriceVerify,
  stripPetkaPendingMarkers,
} from "./price-verify.js";

describe("needsPetkaPriceVerify", () => {
  it("flags Design911 / 非PETKA / 待本机复核 as pending", () => {
    expect(
      needsPetkaPriceVerify("非PETKA价，待本机复核", "currency=GBP"),
    ).toBe(true);
    expect(
      needsPetkaPriceVerify("Design911 OEM / Mann", "currency=GBP"),
    ).toBe(true);
    expect(
      needsPetkaPriceVerify(null, "currency=GBP; design911=yes"),
    ).toBe(true);
  });

  it("returns false when marked as PETKA-verified", () => {
    expect(
      needsPetkaPriceVerify("PETKA价；本机复核", "currency=GBP"),
    ).toBe(false);
    expect(
      needsPetkaPriceVerify("来源PETKA", "Design911 历史备注"),
    ).toBe(false);
    expect(needsPetkaPriceVerify("PETKA 抄 GBP", null)).toBe(false);
  });

  it("ignores bare PETKA mentions that are not price-verify markers", () => {
    expect(
      needsPetkaPriceVerify("OEM 以 PETKA/手册终核", null),
    ).toBe(false);
    expect(isPetkaPriceVerified("OEM 以 PETKA/手册终核", null)).toBe(false);
    expect(needsPetkaPriceVerify(null, "currency=GBP")).toBe(false);
  });

  it("PETKA inbox alone does not verify", () => {
    expect(isPetkaPriceVerified("PETKA inbox", null)).toBe(false);
    expect(
      needsPetkaPriceVerify("Design911 £24.58；非PETKA价，待本机复核；PETKA inbox", null),
    ).toBe(true);
  });
});

describe("stripPetkaPendingMarkers", () => {
  it("removes 非PETKA价 / 待本机复核 / PETKA inbox, keeps Design911 URL", () => {
    const src =
      "Design911 OEM £24.58 https://www.design911.co.uk/p/oil-filter/ ；Mann副厂£14.95；非PETKA价，待本机复核；PETKA inbox";
    const out = stripPetkaPendingMarkers(src);
    expect(out).toContain("https://www.design911.co.uk/p/oil-filter/");
    expect(out).toContain("Mann副厂£14.95");
    expect(out).not.toMatch(/非\s*PETKA\s*价/);
    expect(out).not.toContain("待本机复核");
    expect(out).not.toMatch(/PETKA\s*inbox/i);
  });

  it("tidies leftover separators", () => {
    expect(stripPetkaPendingMarkers("；非PETKA价，待本机复核；")).toBe("");
    expect(stripPetkaPendingMarkers("keep；PETKA inbox")).toBe("keep");
  });
});

describe("markPetkaPriceVerified", () => {
  it("clears pending and adds PETKA价 so verify heuristics flip", () => {
    const src =
      "Design911 OEM £24.58 https://example.com/x ；非PETKA价，待本机复核；PETKA inbox";
    const out = markPetkaPriceVerified(src);
    expect(isPetkaPriceVerified(out, null)).toBe(true);
    expect(needsPetkaPriceVerify(out, null)).toBe(false);
    expect(out).toContain("PETKA价");
    expect(out).toContain("https://example.com/x");
    expect(out).not.toMatch(/非\s*PETKA\s*价/);
    expect(out).not.toContain("待本机复核");
    expect(out).not.toMatch(/PETKA\s*inbox/i);
  });

  it("merges stripped append without duplicating verified marker", () => {
    const out = markPetkaPriceVerified("来源PETKA", "手抄；PETKA inbox");
    expect(out).toContain("来源PETKA");
    expect(out).toContain("手抄");
    expect(out).not.toMatch(/PETKA\s*inbox/i);
    expect(isPetkaPriceVerified(out, null)).toBe(true);
  });

  it("empty notes become PETKA价", () => {
    expect(markPetkaPriceVerified(null)).toBe("PETKA价");
    expect(markPetkaPriceVerified("")).toBe("PETKA价");
  });
});
