import { describe, expect, it } from "vitest";
import {
  buildGarageConsumablesList,
  buildGaragePartsList,
  filterPartsByGarageView,
  generationDisplayLabel,
  hotspotsForGarageView,
  isConsumablePart,
  isPartFor981Car,
  mergeGenerationLabel,
  mergePartsByOem,
  type GaragePartRef,
} from "./garage-parts-list.js";

const sample: GaragePartRef[] = [
  {
    sku: "oil-filter",
    name_zh: "机油滤清器",
    oem_number: "9A1.107.224.00",
    system: "发动机",
    generation: "981",
    interval_km: 15000,
    interval_months: 12,
    locator_hotspot: "engine-bay",
  },
  {
    sku: "982-9A110722400",
    name_zh: "机油滤清器(718)",
    oem_number: "9A1.107.224.00",
    system: "发动机",
    generation: "982",
    interval_km: 15000,
    interval_months: 12,
    locator_hotspot: "engine-bay",
  },
  {
    sku: "battery",
    name_zh: "蓄电池",
    oem_number: "999.611.053.00",
    system: "电气",
    generation: "981",
    interval_km: null,
    interval_months: null,
    locator_hotspot: "front-trunk",
  },
  {
    sku: "cabin-filter",
    name_zh: "空调滤芯",
    oem_number: "970.572.219.00",
    system: "空调",
    generation: null,
    interval_km: 30000,
    interval_months: 24,
    locator_hotspot: "int-cabin-filter",
  },
  {
    sku: "front-brake-pads",
    name_zh: "前刹车片",
    oem_number: "981.351.939.04",
    system: "制动",
    generation: "981",
    interval_km: 40000,
    interval_months: null,
    locator_hotspot: "front-left",
  },
];

describe("generation labels", () => {
  it("maps 982 → 718 and merges", () => {
    expect(generationDisplayLabel("982")).toBe("718");
    expect(mergeGenerationLabel(["981", "982"])).toBe("981+718");
    expect(mergeGenerationLabel([null, null])).toBe("—");
  });
});

describe("isConsumablePart / isPartFor981Car", () => {
  it("consumable + 981 car filter", () => {
    expect(isConsumablePart(sample[0]!)).toBe(true);
    expect(isConsumablePart(sample[2]!)).toBe(false);
    expect(isPartFor981Car(sample[0]!)).toBe(true);
    expect(isPartFor981Car(sample[1]!)).toBe(false);
    expect(isPartFor981Car(sample[3]!)).toBe(true);
  });
});

describe("hotspotsForGarageView", () => {
  it("front-trunk and rear-trunk map to expected hotspots", () => {
    const ft = hotspotsForGarageView({
      mode: "exterior",
      exteriorZone: "front-trunk",
      interiorZone: "all",
      xrayStructure: "all",
    });
    expect(ft).toBeInstanceOf(Set);
    expect([...(ft as Set<string>)]).toEqual(
      expect.arrayContaining(["front-trunk", "wipers"]),
    );

    const rt = hotspotsForGarageView({
      mode: "exterior",
      exteriorZone: "rear-trunk",
      interiorZone: "all",
      xrayStructure: "all",
    }) as Set<string>;
    expect(rt.has("engine-bay")).toBe(true);
  });
});

describe("mergePartsByOem", () => {
  it("dedupes OEM and sorts", () => {
    const rows = mergePartsByOem(sample);
    const oil = rows.find((r) => r.oem_number === "9A1.107.224.00");
    expect(oil?.generationLabel).toBe("981+718");
    expect(oil?.skus).toHaveLength(2);
  });
});

describe("buildGaragePartsList / consumables", () => {
  it("981 car hides 718 OEM; zone list keeps consumables", () => {
    const rear = buildGaragePartsList(
      sample,
      {
        mode: "exterior",
        exteriorZone: "rear-trunk",
        interiorZone: "all",
        xrayStructure: "all",
      },
      { carGeneration: "981" },
    );
    const oil = rear.find((r) => r.oem_number === "9A1.107.224.00");
    expect(oil?.generationLabel).toBe("981");
    expect(oil?.skus).toEqual(["oil-filter"]);

    const front = buildGaragePartsList(
      sample,
      {
        mode: "exterior",
        exteriorZone: "front-trunk",
        interiorZone: "all",
        xrayStructure: "all",
      },
      { carGeneration: "981" },
    );
    expect(front.map((r) => r.skus[0])).toContain("battery");
    expect(front.every((r) => !r.skus.includes("oil-filter"))).toBe(true);

    const cons = buildGarageConsumablesList(sample, { carGeneration: "981" });
    expect(cons.map((r) => r.name_zh)).toEqual([
      "机油滤清器",
      "空调过滤器",
      "前刹车片",
      "蓄电池",
    ]);
    expect(cons.some((r) => r.skus.includes("982-9A110722400"))).toBe(false);
  });

  it("consumables allowlist excludes belt/fuel-filter; alias tire-fl → 前轮胎", () => {
    const more: GaragePartRef[] = [
      ...sample,
      {
        sku: "serpentine-belt",
        name_zh: "驱动皮带",
        oem_number: "9A1.102.218.00",
        system: "发动机",
        generation: "981",
        interval_km: 90000,
        interval_months: 72,
        locator_hotspot: "engine-bay",
      },
      {
        sku: "tire-fl",
        name_zh: "左前轮胎",
        oem_number: null,
        system: "底盘",
        generation: "981",
        interval_km: 40000,
        interval_months: 72,
        locator_hotspot: "front-left",
      },
      {
        sku: "coil-pack",
        name_zh: "点火线圈",
        oem_number: "9A1.602.104.07",
        system: "发动机",
        generation: "981",
        interval_km: null,
        interval_months: null,
        locator_hotspot: "engine-bay",
      },
    ];
    const cons = buildGarageConsumablesList(more, { carGeneration: "981" });
    expect(cons.some((r) => r.skus.includes("serpentine-belt"))).toBe(false);
    expect(cons.find((r) => r.name_zh === "前轮胎")?.skus).toEqual(["tire-fl"]);
    expect(cons.find((r) => r.name_zh === "点火线圈")?.skus).toEqual([
      "coil-pack",
    ]);
  });

  it("empty hotspot zone stays empty", () => {
    const bumper = buildGaragePartsList(sample, {
      mode: "exterior",
      exteriorZone: "front-bumper",
      interiorZone: "all",
      xrayStructure: "all",
    });
    expect(bumper).toEqual([]);
  });

  it("does not strip consumables from zone list", () => {
    const rear = filterPartsByGarageView(
      sample,
      hotspotsForGarageView({
        mode: "exterior",
        exteriorZone: "rear-trunk",
        interiorZone: "all",
        xrayStructure: "all",
      }),
    );
    expect(rear.some((p) => p.sku === "oil-filter")).toBe(true);
  });
});
