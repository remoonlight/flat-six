/** Manual FX table: source currency → CNY. No live crawl. */

export type FxTable = {
  display_currency: "CNY";
  as_of: string;
  note?: string;
  rates_to_cny: Record<string, number>;
};

/** Mirrors data/seed/fx.json — keep in sync when editing the seed. */
export const DEFAULT_FX_TABLE: FxTable = {
  display_currency: "CNY",
  as_of: "2026-07-31",
  note: "手工汇率；不自动爬价",
  rates_to_cny: {
    CNY: 1,
    GBP: 9.2,
    EUR: 7.9,
    USD: 7.2,
  },
};

export function rateToCny(
  currency: string | null | undefined,
  fx: FxTable = DEFAULT_FX_TABLE,
): number | null {
  if (!currency) return null;
  const r = fx.rates_to_cny[currency.toUpperCase()];
  return typeof r === "number" && Number.isFinite(r) && r > 0 ? r : null;
}

/** Convert source-currency amount to CNY. null if amount/currency/rate missing. */
export function toCny(
  amount: number | null | undefined,
  currency: string | null | undefined,
  fx: FxTable = DEFAULT_FX_TABLE,
): number | null {
  if (amount == null || !Number.isFinite(amount)) return null;
  const rate = rateToCny(currency, fx);
  if (rate == null) return null;
  return Math.round(amount * rate * 100) / 100;
}

/** e.g. `¥226.14` */
export function formatCny(amountCny: number | null | undefined): string {
  if (amountCny == null || !Number.isFinite(amountCny)) return "—";
  return `¥${amountCny.toFixed(2)}`;
}

/**
 * Primary display: CNY. When source ≠ CNY, optional detail for muted secondary line.
 */
export function formatMoneyAsCny(
  amount: number | null | undefined,
  currency: string | null | undefined,
  fx: FxTable = DEFAULT_FX_TABLE,
): { primary: string; detail: string | null; cny: number | null } {
  if (amount == null) {
    return { primary: "—", detail: null, cny: null };
  }
  const cur = currency?.toUpperCase() ?? null;
  const cny = toCny(amount, cur, fx);
  if (cny == null) {
    return {
      primary: cur ? `${amount} ${cur}` : String(amount),
      detail: "无汇率，未折算 CNY",
      cny: null,
    };
  }
  const rate = rateToCny(cur, fx)!;
  const detail =
    cur && cur !== "CNY"
      ? `${amount} ${cur} × ${rate} → CNY（汇率 ${fx.as_of}）`
      : null;
  return { primary: formatCny(cny), detail, cny };
}
