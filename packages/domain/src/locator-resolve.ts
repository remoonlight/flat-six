/** sku → parts.locator_hotspot → zones.json hotspot (R5 优先链到三区定位). */

export type LocatorPartRef = {
  sku: string;
  locator_hotspot: string | null;
};

export type LocatorZoneRef = {
  id: string;
  hotspots: { id: string }[];
};

export type LocatorResolveResult = {
  sku: string;
  hotspotId: string;
  zoneId: string;
};

/**
 * Resolve a part SKU to a locator zone + hotspot.
 * Hotspot id comes from `parts.locator_hotspot`; zone is the first map zone
 * that declares that hotspot (shared ids like `front-left` prefer earlier zones).
 * Returns null if the part is missing, has no hotspot, or the hotspot is not
 * on any map zone (e.g. legacy cabin if still unmapped).
 */
export function resolveSkuToLocator(
  sku: string,
  parts: LocatorPartRef[],
  zones: LocatorZoneRef[],
): LocatorResolveResult | null {
  const part = parts.find((p) => p.sku === sku);
  if (!part?.locator_hotspot) return null;
  const hotspotId = part.locator_hotspot;
  const zone = zones.find((z) => z.hotspots.some((h) => h.id === hotspotId));
  if (!zone) return null;
  return { sku, hotspotId, zoneId: zone.id };
}

/** sku → locator_hotspot only (does not require presence on the three-zone map). */
export function skuToHotspot(
  sku: string,
  parts: LocatorPartRef[],
): string | null {
  const part = parts.find((p) => p.sku === sku);
  return part?.locator_hotspot ?? null;
}
