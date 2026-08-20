import { describe, expect, it } from "vitest";
import {
  applyIntervalKindMarker,
  parseIntervalKind,
} from "./interval-kind.js";

describe("parseIntervalKind", () => {
  it("defaults to hard when notes omit kind", () => {
    expect(parseIntervalKind(null)).toBe("hard");
    expect(parseIntervalKind("厂方周期")).toBe("hard");
    expect(parseIntervalKind("interval_kind=hard")).toBe("hard");
  });

  it("detects soft marker", () => {
    expect(parseIntervalKind("interval_kind=soft")).toBe("soft");
    expect(parseIntervalKind("interval_kind=soft; 按磨损")).toBe("soft");
  });
});

describe("applyIntervalKindMarker", () => {
  it("prefixes soft without wiping notes", () => {
    expect(applyIntervalKindMarker("按磨损更换", "soft")).toBe(
      "interval_kind=soft; 按磨损更换",
    );
    expect(applyIntervalKindMarker(null, "soft")).toBe("interval_kind=soft");
  });

  it("strips soft when hard", () => {
    expect(applyIntervalKindMarker("interval_kind=soft; 按磨损", "hard")).toBe(
      "按磨损",
    );
    expect(applyIntervalKindMarker("interval_kind=soft", "hard")).toBeNull();
  });

  it("is idempotent for soft", () => {
    const once = applyIntervalKindMarker("VIN 终核", "soft");
    expect(applyIntervalKindMarker(once, "soft")).toBe(once);
  });
});
