import { describe, expect, it } from "vitest";
import {
  DEFAULT_PAINT_NAME,
  DEFAULT_TOP_OPTION,
  DEFAULT_INTERIOR_OPTION,
  INTERIOR_OPTIONS_SPEC,
  PAINT_OPTIONS,
  resolveBodyPaintFinish,
  resolveBodyPaintHex,
  resolveBodyPaintPbr,
  resolveInteriorCode,
  resolveInteriorHex,
  resolvePaintCode,
  resolvePaintOemCode,
  resolveSoftTopHex,
  TOP_OPTIONS_SPEC,
  formatSpecLabel,
  resolveTopCode,
} from "./vehicle-paint.js";

describe("vehicle-paint", () => {
  it("brochure paint + PET interior counts", () => {
    expect(PAINT_OPTIONS.filter((p) => p.group === "solid")).toHaveLength(4);
    expect(PAINT_OPTIONS.filter((p) => p.group === "metallic")).toHaveLength(8);
    expect(PAINT_OPTIONS.filter((p) => p.group === "special")).toHaveLength(3);
    expect(TOP_OPTIONS_SPEC).toHaveLength(4);
    expect(INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leatherette")).toHaveLength(4);
    expect(
      INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leatherette_two_tone"),
    ).toHaveLength(3);
    expect(INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leather")).toHaveLength(8);
    expect(
      INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leather_two_tone"),
    ).toHaveLength(6);
  });

  it("covers every PAINT_OPTIONS entry", () => {
    for (const p of PAINT_OPTIONS) {
      expect(resolveBodyPaintHex(p.en)).toBe(p.hex);
      expect(resolveBodyPaintHex(p.code)).toBe(p.hex);
      expect(resolvePaintCode(p.en)).toBe(p.code);
    }
  });

  it("covers every TOP_OPTIONS_SPEC entry", () => {
    for (const t of TOP_OPTIONS_SPEC) {
      expect(resolveSoftTopHex(t.code)).toBe(t.hex);
    }
  });

  it("covers every INTERIOR_OPTIONS_SPEC entry", () => {
    for (const i of INTERIOR_OPTIONS_SPEC) {
      expect(resolveInteriorHex(i.code)).toBe(i.hex);
      expect(resolveInteriorCode(i.code)).toBe(i.code);
    }
  });

  it("falls back + build-sheet synonyms", () => {
    expect(resolveBodyPaintHex(null)).toBe(resolveBodyPaintHex(DEFAULT_PAINT_NAME));
    expect(resolveSoftTopHex(null)).toBe(resolveSoftTopHex(DEFAULT_TOP_OPTION));
    expect(resolveSoftTopHex("4V")).toBe(resolveSoftTopHex("A27"));
    expect(resolveTopCode("BLUE")).toBe("G25");
    expect(resolveTopCode("6V")).toBe("R25");
    expect(resolveTopCode("RED")).toBe("L25");
    expect(resolveInteriorHex(null)).toBe(resolveInteriorHex(DEFAULT_INTERIOR_OPTION));
    expect(resolveInteriorCode(null)).toBe(DEFAULT_INTERIOR_OPTION);
  });

  it("migrates prior interior codes", () => {
    expect(resolveInteriorCode("AJ")).toBe("A11");
    expect(resolveInteriorCode("UD")).toBe("7J0");
    expect(resolveInteriorCode("970-BX")).toBe("GNG");
    expect(resolveInteriorCode("PX")).toBe("GNG");
    expect(resolveInteriorCode("AJ-RS")).toBe("DJS");
    expect(resolveInteriorHex("AJ")).toBe(resolveInteriorHex("A11"));
  });

  it("maps finish → 3D PBR", () => {
    expect(resolveBodyPaintFinish("Guards Red")).toBe("solid");
    expect(resolveBodyPaintFinish("Agate Grey Metallic")).toBe("metallic");
    expect(resolveBodyPaintFinish("Carmine Red")).toBe("special");
    expect(resolveBodyPaintPbr("Guards Red")).toEqual({
      metalness: 0.08,
      roughness: 0.42,
    });
    expect(resolveBodyPaintPbr("Agate Grey Metallic").metalness).toBeGreaterThan(
      0.5,
    );
    expect(resolveBodyPaintPbr("Lime Gold Metallic").metalness).toBeGreaterThan(
      0.5,
    );
    expect(resolveBodyPaintPbr("Carmine Red").metalness).toBeLessThan(0.3);
  });

  it("resolves known samples", () => {
    expect(resolveBodyPaintHex("Guards Red")).toBe("#A01818");
    expect(resolveSoftTopHex("G25")).toBe("#1E3A5A");
    expect(resolveSoftTopHex("BLUE")).toBe("#1E3A5A");
    const r25 = TOP_OPTIONS_SPEC.find((t) => t.code === "R25");
    expect(r25?.order).toBe("6V");
    expect(formatSpecLabel(r25!)).toBe("R25/6V · brown · 棕色");
    expect(resolveInteriorHex("DK4")).toBe("#1A1A1C");
    expect(resolveInteriorHex("DSP")).toBe("#141416");
    expect(resolveInteriorHex("DSN")).toBe("#141416");
    expect(formatSpecLabel(PAINT_OPTIONS[0]!)).toContain("041");
    expect(resolvePaintOemCode("Mahogany Metallic")).toBe("LM8Y");
    expect(DEFAULT_INTERIOR_OPTION).toBe("A11");
    const gng = INTERIOR_OPTIONS_SPEC.find((i) => i.code === "GNG");
    expect(gng?.order).toBe("PX");
    expect(formatSpecLabel(gng!)).toBe(
      "GNG/PX · Agate Gray/Amber · 玛瑙灰/琥珀色",
    );
    const djs = INTERIOR_OPTIONS_SPEC.find((i) => i.code === "DJS");
    expect(djs?.en).toBe("Alcantara Black/Rhodium Silver");
    expect(djs?.zh).toBe("Alcantara 黑色/铑银色");
    const dsn = INTERIOR_OPTIONS_SPEC.find((i) => i.code === "DSN");
    expect(dsn?.en).toBe("Alcantara Black/Carmine Red");
    expect(formatSpecLabel(dsn!)).toContain("Alcantara Black/Carmine Red");
  });
});
