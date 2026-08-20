import { useCallback, useEffect, useState } from "react";
import {
  DEFAULT_INTERIOR_OPTION,
  DEFAULT_PAINT_NAME,
  DEFAULT_TOP_OPTION,
  formatSpecLabel,
  INTERIOR_OPTIONS_SPEC,
  PAINT_OPTIONS,
  resolveInteriorCode,
  resolvePaintOemCode,
  resolveTopCode,
  TOP_OPTIONS_SPEC,
  type InteriorOption,
  type TopOption,
} from "@porsche981/domain";
import { api } from "../api";

/**
 * 一级：车辆设置
 * 981 厂册漆 / PET 内饰（人造革·真皮）/ 软顶。
 */
export function VehicleSettingsPage() {
  const [paint, setPaint] = useState<string>(DEFAULT_PAINT_NAME);
  const [interior, setInterior] =
    useState<InteriorOption>(DEFAULT_INTERIOR_OPTION);
  const [top, setTop] = useState<TopOption>(DEFAULT_TOP_OPTION);
  const [paintCode, setPaintCode] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    const v = await api().getVehicle();
    setPaint(v.paint_name ?? DEFAULT_PAINT_NAME);
    setPaintCode(v.paint_code ?? "");
    setInterior(resolveInteriorCode(v.interior));
    setTop(resolveTopCode(v.top));
  }, []);

  useEffect(() => {
    load()
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [load]);

  async function save() {
    setMsg(null);
    setError(null);
    try {
      await api().setVehicleSettings({
        paint_name: paint,
        paint_code: paintCode.trim() || null,
        interior,
        top,
      });
      setMsg("已落盘；部件定位 X-ray 将跟车漆/软顶/内饰色。");
    } catch (e) {
      setError(String(e));
    }
  }

  const solid = PAINT_OPTIONS.filter((p) => p.group === "solid");
  const metallic = PAINT_OPTIONS.filter((p) => p.group === "metallic");
  const special = PAINT_OPTIONS.filter((p) => p.group === "special");
  const intLeatherette = INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leatherette");
  const intLeatheretteTwo = INTERIOR_OPTIONS_SPEC.filter(
    (i) => i.group === "leatherette_two_tone",
  );
  const intLeather = INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leather");
  const intLeatherTwo = INTERIOR_OPTIONS_SPEC.filter((i) => i.group === "leather_two_tone");

  return (
    <div className="vehicle-settings" data-page="vehicle-settings">
      <header className="page-head">
        <h1>车辆设置</h1>
      </header>

      <section className="panel">
        <h2>车漆</h2>
        <p className="muted">素色</p>
        <div className="chip-row">
          {solid.map((p) => (
            <button
              key={p.code}
              type="button"
              className={paint === p.en ? "chip active" : "chip"}
              title={formatSpecLabel(p)}
              onClick={() => {
                setPaint(p.en);
                setPaintCode(resolvePaintOemCode(p.en) ?? p.code);
              }}
              disabled={loading}
            >
              {formatSpecLabel(p)}
            </button>
          ))}
        </div>
        <p className="muted">金属漆</p>
        <div className="chip-row">
          {metallic.map((p) => (
            <button
              key={p.code}
              type="button"
              className={paint === p.en ? "chip active" : "chip"}
              title={formatSpecLabel(p)}
              onClick={() => {
                setPaint(p.en);
                setPaintCode(resolvePaintOemCode(p.en) ?? p.code);
              }}
              disabled={loading}
            >
              {formatSpecLabel(p)}
            </button>
          ))}
        </div>
        <p className="muted">特别色</p>
        <div className="chip-row">
          {special.map((p) => (
            <button
              key={p.code}
              type="button"
              className={paint === p.en ? "chip active" : "chip"}
              title={formatSpecLabel(p)}
              onClick={() => {
                setPaint(p.en);
                setPaintCode(resolvePaintOemCode(p.en) ?? p.code);
              }}
              disabled={loading}
            >
              {formatSpecLabel(p)}
            </button>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>内饰</h2>
        <p className="muted">人造革</p>
        <div className="chip-row">
          {intLeatherette.map((opt) => (
            <button
              key={opt.code}
              type="button"
              className={interior === opt.code ? "chip active" : "chip"}
              onClick={() => setInterior(opt.code)}
              disabled={loading}
            >
              {formatSpecLabel(opt)}
            </button>
          ))}
        </div>
        <p className="muted">人造革/真皮套装 · 双色</p>
        <div className="chip-row">
          {intLeatheretteTwo.map((opt) => (
            <button
              key={opt.code}
              type="button"
              className={interior === opt.code ? "chip active" : "chip"}
              onClick={() => setInterior(opt.code)}
              disabled={loading}
            >
              {formatSpecLabel(opt)}
            </button>
          ))}
        </div>
        <p className="muted">真皮</p>
        <div className="chip-row">
          {intLeather.map((opt) => (
            <button
              key={opt.code}
              type="button"
              className={interior === opt.code ? "chip active" : "chip"}
              onClick={() => setInterior(opt.code)}
              disabled={loading}
            >
              {formatSpecLabel(opt)}
            </button>
          ))}
        </div>
        <p className="muted">真皮双色</p>
        <div className="chip-row">
          {intLeatherTwo.map((opt) => (
            <button
              key={opt.code}
              type="button"
              className={interior === opt.code ? "chip active" : "chip"}
              onClick={() => setInterior(opt.code)}
              disabled={loading}
            >
              {formatSpecLabel(opt)}
            </button>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>篷（软顶）</h2>
        <div className="chip-row">
          {TOP_OPTIONS_SPEC.map((opt) => (
            <button
              key={opt.code}
              type="button"
              className={top === opt.code ? "chip active" : "chip"}
              onClick={() => setTop(opt.code)}
              disabled={loading}
            >
              {formatSpecLabel(opt)}
            </button>
          ))}
        </div>
      </section>

      <button type="button" className="btn" onClick={() => void save()} disabled={loading}>
        保存
      </button>
      {msg ? <p className="muted">{msg}</p> : null}
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}
