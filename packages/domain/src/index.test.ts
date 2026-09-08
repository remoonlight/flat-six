import { describe, expect, it } from "vitest";
import {
  applyMileageSet,
  computeInterval,
  hasIntervalBaseline,
  validateCodingSnapshot,
} from "./index.js";

describe("computeInterval", () => {
  it("computes km and month dual constraint", () => {
    const r = computeInterval({
      nowKm: 45_000,
      replacedAtKm: 40_000,
      replacedAtDate: "2025-01-01",
      intervalKm: 10_000,
      intervalMonths: 12,
      nowDate: "2025-07-01",
    });
    expect(r.nextDueKm).toBe(50_000);
    expect(r.remainingKm).toBe(5_000);
    expect(r.nextDueDate).toBe("2026-01-01");
    expect(r.remainingDays).toBeGreaterThan(100);
    expect(r.status).toBe("ok");
  });

  it("marks overdue when km exceeded", () => {
    const r = computeInterval({
      nowKm: 51_000,
      replacedAtKm: 40_000,
      replacedAtDate: "2025-01-01",
      intervalKm: 10_000,
      nowDate: "2025-07-01",
    });
    expect(r.status).toBe("overdue");
    expect(r.remainingKm).toBe(-1_000);
  });

  it("marks dueSoon near limit", () => {
    const r = computeInterval({
      nowKm: 49_700,
      replacedAtKm: 40_000,
      replacedAtDate: "2025-01-01",
      intervalKm: 10_000,
      nowDate: "2025-07-01",
    });
    expect(r.status).toBe("dueSoon");
  });

  it("returns no_baseline without last service (does not invent delivery date)", () => {
    const r = computeInterval({
      nowKm: 45_000,
      intervalKm: 15_000,
      intervalMonths: 12,
      nowDate: "2025-07-01",
    });
    expect(r.status).toBe("no_baseline");
    expect(r.nextDueKm).toBeNull();
    expect(r.nextDueDate).toBeNull();
    expect(r.remainingKm).toBeNull();
    expect(r.remainingDays).toBeNull();
  });

  it("returns no_baseline when date missing even if km present", () => {
    const r = computeInterval({
      nowKm: 45_000,
      replacedAtKm: 40_000,
      replacedAtDate: null,
      intervalKm: 15_000,
      nowDate: "2025-07-01",
    });
    expect(r.status).toBe("no_baseline");
    expect(r.remainingKm).toBeNull();
  });
});

describe("hasIntervalBaseline", () => {
  it("requires both km and YYYY-MM-DD date", () => {
    expect(hasIntervalBaseline(40_000, "2025-01-01")).toBe(true);
    expect(hasIntervalBaseline(null, "2025-01-01")).toBe(false);
    expect(hasIntervalBaseline(40_000, null)).toBe(false);
    expect(hasIntervalBaseline(40_000, "2025/01/01")).toBe(false);
  });
});

describe("applyMileageSet", () => {
  it("accepts decrease and increase", () => {
    const down = applyMileageSet(9_000);
    expect(down.ok).toBe(true);
    if (down.ok) expect(down.currentKm).toBe(9_000);
    const up = applyMileageSet(10_500);
    expect(up.ok).toBe(true);
    if (up.ok) expect(up.currentKm).toBe(10_500);
  });

  it("rejects negative", () => {
    const r = applyMileageSet(-1);
    expect(r.ok).toBe(false);
  });
});

describe("validateCodingSnapshot", () => {
  it("requires system and function", () => {
    const r = validateCodingSnapshot({
      system: "",
      functionName: "设码",
      beforeValue: "a",
      afterValue: "b",
      odometerKm: 1,
      recordedAt: "2025-01-01T00:00:00Z",
    });
    expect(r.ok).toBe(false);
  });
});
