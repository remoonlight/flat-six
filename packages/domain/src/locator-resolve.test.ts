import { describe, expect, it } from "vitest";
import { resolveSkuToLocator, skuToHotspot } from "./locator-resolve.js";

const parts = [
  { sku: "coil-pack", locator_hotspot: "engine-bay" },
  { sku: "brake-fluid", locator_hotspot: "front-left" },
  { sku: "cabin-filter", locator_hotspot: "int-cabin-filter" },
  { sku: "battery", locator_hotspot: "front-trunk" },
  { sku: "pdk-fluid", locator_hotspot: "underbody" },
  { sku: "wiper-blades", locator_hotspot: "wipers" },
  { sku: "no-spot", locator_hotspot: null },
];

const zones = [
  { id: "engine-bay", hotspots: [{ id: "engine-bay" }] },
  {
    id: "brakes",
    hotspots: [{ id: "front-left" }, { id: "rear-left" }],
  },
  {
    id: "chassis",
    hotspots: [{ id: "underbody" }, { id: "front-left" }],
  },
  {
    id: "interior",
    hotspots: [
      { id: "int-details-int" },
      { id: "int-cabin-filter" },
    ],
  },
  {
    id: "front-trunk",
    hotspots: [{ id: "front-trunk" }, { id: "wipers" }],
  },
];

describe("skuToHotspot", () => {
  it("maps seed skus to parts.locator_hotspot", () => {
    expect(skuToHotspot("coil-pack", parts)).toBe("engine-bay");
    expect(skuToHotspot("brake-fluid", parts)).toBe("front-left");
    expect(skuToHotspot("cabin-filter", parts)).toBe("int-cabin-filter");
    expect(skuToHotspot("missing", parts)).toBeNull();
  });
});

describe("resolveSkuToLocator", () => {
  it("resolves mapped hotspots (front-left prefers brakes; cabin-filter → interior; battery/wiper → front-trunk)", () => {
    expect(resolveSkuToLocator("coil-pack", parts, zones)).toEqual({
      sku: "coil-pack",
      hotspotId: "engine-bay",
      zoneId: "engine-bay",
    });
    expect(resolveSkuToLocator("brake-fluid", parts, zones)).toEqual({
      sku: "brake-fluid",
      hotspotId: "front-left",
      zoneId: "brakes",
    });
    expect(resolveSkuToLocator("pdk-fluid", parts, zones)).toEqual({
      sku: "pdk-fluid",
      hotspotId: "underbody",
      zoneId: "chassis",
    });
    expect(resolveSkuToLocator("cabin-filter", parts, zones)).toEqual({
      sku: "cabin-filter",
      hotspotId: "int-cabin-filter",
      zoneId: "interior",
    });
    expect(resolveSkuToLocator("battery", parts, zones)).toEqual({
      sku: "battery",
      hotspotId: "front-trunk",
      zoneId: "front-trunk",
    });
    expect(resolveSkuToLocator("wiper-blades", parts, zones)).toEqual({
      sku: "wiper-blades",
      hotspotId: "wipers",
      zoneId: "front-trunk",
    });
  });

  it("returns null when hotspot is off the map", () => {
    expect(resolveSkuToLocator("no-spot", parts, zones)).toBeNull();
  });
});
