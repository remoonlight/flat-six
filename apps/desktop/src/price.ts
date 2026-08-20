/** Price display + PETKA verify helpers for the desktop UI. */
import {
  DEFAULT_FX_TABLE,
  formatMoneyAsCny,
  type FxTable,
} from "@porsche981/domain";

export {
  DEFAULT_FX_TABLE,
  formatCny,
  formatMoneyAsCny,
  isPetkaPriceVerified,
  needsPetkaPriceVerify,
  rateToCny,
  toCny,
  type FxTable,
} from "@porsche981/domain";

/** currency is folded into price_note by PETKA CSV ingest as `currency=GBP`. */
export function currencyFromPriceNote(
  priceNote: string | null | undefined,
): string | null {
  const m = priceNote?.match(/currency=([A-Z]{3})/);
  return m?.[1] ?? null;
}

/**
 * Display money as CNY (manual FX). Source amount stays in DB/CSV currency.
 * Returns primary `¥…` string for table cells.
 */
export function formatMoney(
  amount: number | null | undefined,
  priceNote: string | null | undefined,
  fx: FxTable = DEFAULT_FX_TABLE,
): string {
  return formatMoneyDisplay(amount, priceNote, fx).primary;
}

/** Primary CNY + optional muted detail (`24.58 GBP × 9.2 → CNY`). */
export function formatMoneyDisplay(
  amount: number | null | undefined,
  priceNote: string | null | undefined,
  fx: FxTable = DEFAULT_FX_TABLE,
) {
  return formatMoneyAsCny(amount, currencyFromPriceNote(priceNote), fx);
}

export function needsVinPetkaConfirm(sku: string, notes: string | null): boolean {
  if (sku === "fuel-filter") return true;
  return /须\s*VIN|必须.*PETKA|VIN\s*\/\s*PETKA|VIN\+PETKA/i.test(notes ?? "");
}
