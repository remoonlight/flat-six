import { useEffect, useMemo, useState } from "react";
import {
  parseIntervalKind,
  resolveSkuToLocator,
  type FxTable,
} from "@porsche981/domain";
import { api, type AftermarketQuote, type LocatorMap, type Part } from "../api";
import type { LocatorFocus } from "../locator-focus";
import {
  DEFAULT_FX_TABLE,
  currencyFromPriceNote,
  formatMoney,
  formatMoneyDisplay,
  isPetkaPriceVerified,
  needsPetkaPriceVerify,
  needsVinPetkaConfirm,
} from "../price";

export type CatalogPageProps = {
  onLocate?: (focus: LocatorFocus) => void;
  /** When set, only parts whose `system` is in this list (empty = none). */
  systemFilter?: readonly string[] | null;
};

type AmQuoteRow = { brand: string; price: string };

function quotesFromPart(p: Part): AmQuoteRow[] {
  if (p.aftermarket_quotes?.length) {
    return p.aftermarket_quotes.map((q) => ({
      brand: q.brand,
      price: String(q.price),
    }));
  }
  if (p.aftermarket_price != null) {
    return [{ brand: "Design911", price: String(p.aftermarket_price) }];
  }
  return [{ brand: "Design911", price: "" }];
}

function parseQuoteRows(rows: AmQuoteRow[]): AftermarketQuote[] | null {
  const out: AftermarketQuote[] = [];
  for (const row of rows) {
    const brand = row.brand.trim();
    const priceStr = row.price.trim();
    if (!brand && !priceStr) continue;
    if (!brand || priceStr === "") continue;
    const price = Number(priceStr);
    if (!Number.isFinite(price)) continue;
    out.push({ brand, price });
  }
  return out.length ? out : null;
}

function isFxTable(v: unknown): v is FxTable {
  if (!v || typeof v !== "object") return false;
  const o = v as FxTable;
  return (
    o.display_currency === "CNY" &&
    !!o.rates_to_cny &&
    typeof o.as_of === "string"
  );
}

export function CatalogPage({
  onLocate,
  systemFilter = null,
}: CatalogPageProps = {}) {
  const [parts, setParts] = useState<Part[]>([]);
  const [locatorMap, setLocatorMap] = useState<LocatorMap | null>(null);
  const [selected, setSelected] = useState<Part | null>(null);
  const [oem, setOem] = useState("");
  const [amQuotes, setAmQuotes] = useState<AmQuoteRow[]>([
    { brand: "Design911", price: "" },
  ]);
  const [note, setNote] = useState("");
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [q, setQ] = useState("");
  const [generationFilter, setGenerationFilter] = useState<
    "all" | "maint" | "981" | "982"
  >("all");
  const [fx, setFx] = useState<FxTable>(DEFAULT_FX_TABLE);

  async function refresh() {
    const list = await api().listParts();
    setParts(list);
    if (selected) {
      const fresh = list.find((p) => p.id === selected.id) ?? null;
      setSelected(fresh);
      if (fresh) fillForm(fresh);
    }
  }

  function fillForm(p: Part) {
    setOem(p.oem_price == null ? "" : String(p.oem_price));
    setAmQuotes(quotesFromPart(p));
    setNote(p.price_note ?? "");
    setAsOf(p.price_as_of ?? new Date().toISOString().slice(0, 10));
  }

  useEffect(() => {
    refresh();
    void api()
      .locatorMap()
      .then(setLocatorMap)
      .catch(() => setLocatorMap(null));
    void api()
      .getFx?.()
      .then((t) => {
        if (isFxTable(t)) setFx(t);
      })
      .catch(() => {
        /* keep DEFAULT_FX_TABLE */
      });
  }, []);

  const locateTarget = useMemo(() => {
    if (!selected || !locatorMap) return null;
    return resolveSkuToLocator(selected.sku, parts, locatorMap.zones);
  }, [selected, parts, locatorMap]);

  const filtered = parts.filter((p) => {
    if (systemFilter && !systemFilter.includes(p.system)) return false;
    if (generationFilter === "maint" && p.generation != null) return false;
    if (generationFilter === "981" && p.generation !== "981") return false;
    if (generationFilter === "982" && p.generation !== "982") return false;
    const s = q.trim().toLowerCase();
    if (!s) return true;
    return (
      p.name_zh.includes(q) ||
      (p.oem_number ?? "").toLowerCase().includes(s) ||
      p.sku.toLowerCase().includes(s) ||
      p.system.includes(q) ||
      (p.generation ?? "").includes(s)
    );
  });

  async function savePrices() {
    if (!selected || selected.sku === "fuel-filter") return;
    const parsedQuotes = parseQuoteRows(amQuotes);
    await api().updatePartPrices({
      id: selected.id,
      oem_price: oem === "" ? null : Number(oem),
      aftermarket_price: null,
      aftermarket_quotes: parsedQuotes,
      price_note: note || null,
      price_as_of: asOf || null,
    });
    await refresh();
  }

  const selectedCurrency = selected
    ? currencyFromPriceNote(selected.price_note)
    : null;
  const selectedNeedsVin = selected
    ? needsVinPetkaConfirm(selected.sku, selected.notes)
    : false;
  const selectedNeedsPriceVerify = selected
    ? needsPetkaPriceVerify(selected.notes, selected.price_note)
    : false;
  const selectedPetkaVerified = selected
    ? isPetkaPriceVerified(selected.notes, selected.price_note)
    : false;
  const selectedOemDisp = selected
    ? formatMoneyDisplay(selected.oem_price, selected.price_note, fx)
    : null;
  const selectedAmDisp = selected
    ? formatMoneyDisplay(selected.aftermarket_price, selected.price_note, fx)
    : null;

  return (
    <div>
      <h2>零件目录 / 报价</h2>
      <p className="muted">
        OEM 号为参考；报价为手工快照，非实时行情。入库多为源币种（如{" "}
        <strong>GBP</strong>），界面<strong>一律折算 CNY</strong>展示（手工汇率{" "}
        {fx.as_of}，见 <code>data/seed/fx.json</code>
        ；不自动爬价）。「待 PETKA」仍标未本机复核。
      </p>
      <div className="row" style={{ marginTop: 12, gap: 12, flexWrap: "wrap" }}>
        <label>
          搜索
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="名称 / OEM / 系统"
          />
        </label>
        <label>
          世代
          <select
            value={generationFilter}
            onChange={(e) =>
              setGenerationFilter(
                e.target.value as "all" | "maint" | "981" | "982",
              )
            }
          >
            <option value="all">全部</option>
            <option value="maint">保养子集</option>
            <option value="981">981 全量</option>
            <option value="982">982 全量</option>
          </select>
        </label>
      </div>

      <div
        className="panel"
        style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}
      >
        <div>
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>世代</th>
                <th>OEM</th>
                <th>OEM价</th>
                <th>副厂价</th>
                <th>展示</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => {
                const vinWarn = needsVinPetkaConfirm(p.sku, p.notes);
                const pricePending = needsPetkaPriceVerify(p.notes, p.price_note);
                const pricePetka = isPetkaPriceVerified(p.notes, p.price_note);
                return (
                  <tr
                    key={p.id}
                    style={{
                      cursor: "pointer",
                      background:
                        selected?.id === p.id ? "#f7f0ea" : undefined,
                    }}
                    onClick={() => {
                      setSelected(p);
                      fillForm(p);
                    }}
                  >
                    <td>
                      {p.name_zh}
                      {vinWarn ? (
                        <span className="pill warn" style={{ marginLeft: 6 }}>
                          须 VIN
                        </span>
                      ) : null}
                      {pricePending ? (
                        <span className="pill warn" style={{ marginLeft: 6 }}>
                          待 PETKA
                        </span>
                      ) : pricePetka ? (
                        <span className="pill ok" style={{ marginLeft: 6 }}>
                          PETKA
                        </span>
                      ) : null}
                      <div className="muted">{p.system}</div>
                    </td>
                    <td>{p.generation ?? "—"}</td>
                    <td>{p.oem_number ?? "—"}</td>
                    <td>{formatMoney(p.oem_price, p.price_note, fx)}</td>
                    <td>{formatMoney(p.aftermarket_price, p.price_note, fx)}</td>
                    <td>
                      <span className="currency-badge">CNY</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div>
          {selected ? (
            <>
              <h2>
                {selected.name_zh}
                {selectedNeedsPriceVerify ? (
                  <span className="pill warn" style={{ marginLeft: 8 }}>
                    待 PETKA
                  </span>
                ) : selectedPetkaVerified ? (
                  <span className="pill ok" style={{ marginLeft: 8 }}>
                    PETKA
                  </span>
                ) : null}
              </h2>
              <p className="muted">
                SKU {selected.sku}
                {selected.generation ? ` · ${selected.generation}` : ""} · 定位锚点{" "}
                {selected.locator_hotspot ?? "—"}
                {selectedCurrency
                  ? ` · 源币种 ${selectedCurrency} → 展示 CNY`
                  : " · 展示 CNY"}
              </p>
              {onLocate ? (
                <p style={{ marginBottom: 8 }}>
                  <button
                    type="button"
                    className="ghost"
                    disabled={!locateTarget}
                    title={
                      locateTarget
                        ? `跳到 ${locateTarget.zoneId} / ${locateTarget.hotspotId}`
                        : "该 SKU 锚点不在定位图上"
                    }
                    onClick={() => {
                      if (!locateTarget || !onLocate) return;
                      onLocate({
                        zoneId: locateTarget.zoneId,
                        hotspotId: locateTarget.hotspotId,
                        sku: locateTarget.sku,
                      });
                    }}
                  >
                    定位
                  </button>
                </p>
              ) : null}
              {(selectedOemDisp?.detail || selectedAmDisp?.detail) && (
                <p className="muted" style={{ fontSize: 12 }}>
                  {selectedOemDisp?.detail
                    ? `OEM ${selectedOemDisp.detail}`
                    : null}
                  {selectedOemDisp?.detail && selectedAmDisp?.detail
                    ? "；"
                    : null}
                  {selectedAmDisp?.detail
                    ? `副厂 ${selectedAmDisp.detail}`
                    : null}
                </p>
              )}
              <p>
                间隔：{selected.interval_km ?? "—"} km /{" "}
                {selected.interval_months ?? "—"} 月
                {parseIntervalKind(selected.notes) === "soft" ? (
                  <span className="muted" style={{ marginLeft: 8 }}>
                    软提醒
                  </span>
                ) : null}
              </p>
              {selectedNeedsVin && (
                <div className="callout warn">
                  须按本车 VIN 在 PETKA 终核 OEM；当前价故意留空，勿填臆造 OEM
                  价。公开目录号仅供参考。
                </div>
              )}
              {selected.notes && <div className="callout">{selected.notes}</div>}
              <p className="muted" style={{ marginBottom: 8 }}>
                价：PETKA 复制文本存 <code>.local/petka-inbox/*.txt</code>
                （watch 自动吃）；fuel-filter 仍禁填。编辑框填
                <strong>源币种</strong>金额。
              </p>
              <div className="row">
                <label>
                  OEM 报价{selectedCurrency ? ` (${selectedCurrency})` : ""}
                  <input
                    value={oem}
                    onChange={(e) => setOem(e.target.value)}
                    disabled={selected.sku === "fuel-filter"}
                    placeholder={
                      selected.sku === "fuel-filter"
                        ? "留空：须 VIN/PETKA"
                        : undefined
                    }
                  />
                </label>
                <label style={{ display: "block", width: "100%" }}>
                  副厂报价{selectedCurrency ? ` (${selectedCurrency})` : ""}
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {amQuotes.map((row, i) => (
                      <div key={i} className="row" style={{ margin: 0 }}>
                        <input
                          value={row.brand}
                          onChange={(e) => {
                            const next = [...amQuotes];
                            next[i] = { ...row, brand: e.target.value };
                            setAmQuotes(next);
                          }}
                          placeholder={i === 0 ? "Design911" : "可追加品牌"}
                          disabled={selected.sku === "fuel-filter"}
                          style={{ flex: 1 }}
                        />
                        <input
                          value={row.price}
                          onChange={(e) => {
                            const next = [...amQuotes];
                            next[i] = { ...row, price: e.target.value };
                            setAmQuotes(next);
                          }}
                          placeholder="价格"
                          disabled={selected.sku === "fuel-filter"}
                          style={{ width: 96 }}
                        />
                        <button
                          type="button"
                          className="ghost"
                          disabled={
                            selected.sku === "fuel-filter" || amQuotes.length <= 1
                          }
                          onClick={() =>
                            setAmQuotes(amQuotes.filter((_, j) => j !== i))
                          }
                        >
                          删
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      className="ghost"
                      disabled={selected.sku === "fuel-filter"}
                      onClick={() =>
                        setAmQuotes([...amQuotes, { brand: "", price: "" }])
                      }
                    >
                      + 追加品牌
                    </button>
                  </div>
                </label>
                <label>
                  报价日期
                  <input
                    type="date"
                    value={asOf}
                    onChange={(e) => setAsOf(e.target.value)}
                  />
                </label>
                <label>
                  备注来源（含 currency=XXX）
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
                <button
                  className="primary"
                  type="button"
                  onClick={savePrices}
                  disabled={selected.sku === "fuel-filter"}
                >
                  保存报价快照
                </button>
              </div>
              {selected.sku === "fuel-filter" && (
                <p className="muted" style={{ marginTop: 8 }}>
                  fuel-filter 禁止在 App 内填价；请先 PETKA/VIN 确认后再改 CSV 并
                  ingest。
                </p>
              )}
            </>
          ) : (
            <p className="muted">选择左侧零件编辑报价</p>
          )}
        </div>
      </div>
    </div>
  );
}
