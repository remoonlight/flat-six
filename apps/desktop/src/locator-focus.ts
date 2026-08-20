/** R5: deep-link into Locator from fault KB / wiring index (zone + hotspot + optional sku). */
export type LocatorFocus = {
  zoneId: string;
  hotspotId: string | null;
  sku?: string | null;
};
