import { useCallback, useEffect, useMemo, useState } from "react";
import { resolveSkuToLocator } from "@porsche981/domain";
import {
  api,
  type DtcEntry,
  type LocatorMap,
  type ObdDtc,
  type ObdSession,
  type Part,
  type Vehicle,
} from "../api";
import type { LocatorFocus } from "../locator-focus";
import { CodingPage } from "./CodingPage";
import { DiagnosticsPage } from "./DiagnosticsPage";

export type ObdPageProps = {
  onLocate?: (focus: LocatorFocus) => void;
};

type ObdTab =
  | "connection"
  | "live"
  | "faults"
  | "monitors"
  | "insights"
  | "vehicle"
  | "coding";

const TABS: { id: ObdTab; label: string }[] = [
  { id: "connection", label: "连接" },
  { id: "live", label: "实时数据" },
  { id: "faults", label: "故障码" },
  { id: "monitors", label: "就绪监控" },
  { id: "insights", label: "分析洞察" },
  { id: "vehicle", label: "车辆信息" },
  { id: "coding", label: "设码" },
];

async function faultLogFromObdDtc(
  session: ObdSession,
  dtc: ObdDtc,
  fallbackKm: number,
) {
  const kb = await api().getDtc(dtc.code);
  const symptom = kb ? `${kb.code} — ${kb.title_zh}` : dtc.code;
  return api().addFaultLog({
    logged_at: session.started_at.slice(0, 10),
    odometer_km: session.odometer_km ?? fallbackKm,
    symptom,
    area_hypothesis: kb?.likely_causes ?? null,
    action: kb?.checks ?? `OBD 会话 #${session.id} 手工码`,
    result: `来自 OBD 会话 #${session.id}（${dtc.status}）`,
    related_part_sku: kb?.related_part_sku ?? null,
  });
}

export function ObdPage({ onLocate }: ObdPageProps) {
  const [tab, setTab] = useState<ObdTab>("faults");
  const [codeInput, setCodeInput] = useState("");
  const [active, setActive] = useState<DtcEntry | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [locatorMap, setLocatorMap] = useState<LocatorMap | null>(null);
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [sessions, setSessions] = useState<ObdSession[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [sessionDtcs, setSessionDtcs] = useState<ObdDtc[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [logBusy, setLogBusy] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    const [p, v, map, sess] = await Promise.all([
      api().listParts(),
      api().getVehicle(),
      api().locatorMap(),
      api().listObdSessions(),
    ]);
    setParts(p);
    setVehicle(v);
    setLocatorMap(map);
    setSessions(sess);
  }, []);

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  useEffect(() => {
    if (expandedId == null) {
      setSessionDtcs([]);
      return;
    }
    api()
      .listObdDtcs(expandedId)
      .then(setSessionDtcs)
      .catch((e) => setError(String(e)));
  }, [expandedId]);

  const normalized = codeInput.trim().toUpperCase();

  async function lookup() {
    setError(null);
    const q = normalized;
    if (!q) {
      setActive(null);
      return;
    }
    try {
      const hit = await api().getDtc(q);
      if (hit) {
        setActive(hit);
        return;
      }
      const matches = await api().searchDtc(q);
      setActive(matches[0] ?? null);
      if (!matches.length) {
        setError(`码库无 ${q}；仍可记入会话作手工记录`);
      }
    } catch (e) {
      setError(String(e));
    }
  }

  const related = useMemo(() => {
    if (!active?.related_part_sku) return null;
    return parts.find((p) => p.sku === active.related_part_sku) ?? null;
  }, [active, parts]);

  const locateTarget = useMemo(() => {
    if (!active?.related_part_sku || !locatorMap) return null;
    return resolveSkuToLocator(
      active.related_part_sku,
      parts,
      locatorMap.zones,
    );
  }, [active, parts, locatorMap]);

  async function recordSession() {
    setError(null);
    const code = normalized;
    if (!code) {
      setError("请输入故障码");
      return;
    }
    setBusy(true);
    try {
      const session = await api().createObdSession({
        odometer_km: vehicle?.current_km ?? null,
        note: note.trim() || (active ? null : `手工码 ${code}`),
      });
      await api().addObdDtc({
        session_id: session.id,
        code,
        status: active ? "manual_kb" : "manual_unknown",
      });
      setNote("");
      await refresh();
      setExpandedId(session.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSession(s: ObdSession) {
    setError(null);
    setExpandedId((prev) => (prev === s.id ? null : s.id));
  }

  async function logDtc(session: ObdSession, dtc: ObdDtc) {
    setError(null);
    setLogBusy(dtc.id);
    try {
      await faultLogFromObdDtc(session, dtc, vehicle?.current_km ?? 0);
    } catch (e) {
      setError(String(e));
    } finally {
      setLogBusy(null);
    }
  }

  const expandedSession = sessions.find((s) => s.id === expandedId) ?? null;

  return (
    <div className="obd-page" data-page="obd">
      <header className="page-head">
        <p className="muted obd-kicker">诊断</p>
        <h1>
          实时 OBD <span className="obd-beta">试运行</span>
        </h1>
      </header>

      <nav className="obd-tabs chip-row" aria-label="OBD 分区">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`chip${tab === t.id ? " active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab !== "coding" ? (
        <div className="obd-status-bar panel">
          <span className="obd-status-dot" aria-hidden />
          <strong>未连接</strong>
          <span className="muted">ELM327 · 第二阶段适配器</span>
          <span className="obd-live-poll muted">实时轮询 · 关</span>
        </div>
      ) : null}

      {error && tab === "faults" ? <p className="error">{error}</p> : null}

      {tab === "connection" && (
        <section className="panel">
          <h2>连接</h2>
          <div className="obd-platform callout">
            <p>
              <strong>平台支持 · Windows</strong>
            </p>
            <p className="muted">
              第一阶段：内置码库查码 + 手工会话。第二阶段接 ELM327（USB /
              蓝牙）只读故障码；不清码、不写 ECU（见 ADR 001）。
            </p>
          </div>
          <div className="row">
            <button type="button" className="chip active" disabled>
              Web 串口（USB）
            </button>
            <button type="button" className="chip" disabled>
              本机桥接
            </button>
          </div>
          <button type="button" className="btn" disabled>
            连接 USB ELM
          </button>
        </section>
      )}

      {tab === "live" && (
        <section className="panel">
          <h2>实时数据</h2>
          <button type="button" disabled>
            刷新实时数据
          </button>
          <p className="muted">连接后可见实时 PID（第三阶段）。</p>
        </section>
      )}

      {tab === "faults" && (
        <section className="panel">
          <h2>故障码</h2>
          <p className="muted">
            输入 OBD-II 故障码查内置说明；可记入手工会话。硬件扫码见第二阶段。
          </p>
          <div className="row">
            <button type="button" disabled>
              刷新故障码
            </button>
            <button type="button" disabled title="ADR 001：不做清码">
              清除故障码
            </button>
            <button type="button" disabled>
              保存扫描
            </button>
          </div>
          <div className="row">
            <label>
              故障码
              <input
                value={codeInput}
                onChange={(e) => setCodeInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && lookup()}
                placeholder="P0300"
              />
            </label>
            <button type="button" onClick={() => lookup()}>
              查询
            </button>
          </div>

          {active && (
            <div className="callout" style={{ marginTop: 12 }}>
              <h3>
                {active.code} — {active.title_zh}
              </h3>
              <h4>可能原因</h4>
              <p>{active.likely_causes}</p>
              <h4>检查步骤</h4>
              <p>{active.checks}</p>
              {(related || active.related_part_sku) && (
                <div
                  style={{
                    display: "flex",
                    gap: 12,
                    alignItems: "center",
                    flexWrap: "wrap",
                    marginTop: 8,
                  }}
                >
                  <span>
                    {related
                      ? `相关零件：${related.name_zh}（${related.sku}）`
                      : `相关 SKU：${active.related_part_sku}（零件未入库）`}
                  </span>
                  <button
                    type="button"
                    className="ghost"
                    disabled={!locateTarget || !onLocate}
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
                </div>
              )}
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <label>
              会话备注（可选）
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="例如：X431 读码截图"
              />
            </label>
            <button type="button" disabled={busy} onClick={() => recordSession()}>
              记入会话
            </button>
          </div>

          {sessions.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <h3>最近会话</h3>
              <ul className="plain-list">
                {sessions.slice(0, 5).map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => toggleSession(s)}
                      aria-expanded={expandedId === s.id}
                    >
                      {expandedId === s.id ? "▾" : "▸"} #{s.id} ·{" "}
                      {s.started_at.slice(0, 16)}
                      {s.odometer_km != null ? ` · ${s.odometer_km} km` : ""}
                      {s.note ? ` · ${s.note}` : ""}
                    </button>
                    {expandedId === s.id && expandedSession && (
                      <ul
                        className="plain-list"
                        style={{ marginLeft: 16, marginTop: 4 }}
                      >
                        {sessionDtcs.length === 0 ? (
                          <li className="muted">无故障码</li>
                        ) : (
                          sessionDtcs.map((d) => (
                            <li
                              key={d.id}
                              style={{
                                display: "flex",
                                gap: 8,
                                alignItems: "center",
                                flexWrap: "wrap",
                              }}
                            >
                              <span>
                                <strong>{d.code}</strong> · {d.status}
                              </span>
                              <button
                                type="button"
                                className="ghost"
                                disabled={logBusy === d.id}
                                onClick={() => logDtc(expandedSession, d)}
                              >
                                记入故障台账
                              </button>
                            </li>
                          ))
                        )}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {tab === "monitors" && (
        <section className="panel">
          <h2>就绪监控</h2>
          <p className="muted">就绪度监控占位（第三阶段及以后）。</p>
        </section>
      )}

      {tab === "insights" && (
        <section className="panel obd-legacy-faults" aria-label="故障台账过渡">
          <h2>分析洞察</h2>
          <p className="muted">故障台账（过渡）；硬件落地前继续用手工记录。</p>
          <DiagnosticsPage onLocate={onLocate} />
        </section>
      )}

      {tab === "vehicle" && (
        <section className="panel">
          <h2>车辆信息</h2>
          {vehicle ? (
            <ul className="plain-list">
              <li>
                {vehicle.year} {vehicle.model} {vehicle.trim} · {vehicle.chassis}
              </li>
              <li>里程：{vehicle.current_km} km</li>
              <li>VIN：{vehicle.vin ?? "—"}</li>
              <li>
                漆：{vehicle.paint_name ?? "—"}
                {vehicle.paint_code ? ` (${vehicle.paint_code})` : ""}
              </li>
              <li>内饰：{vehicle.interior ?? "—"}</li>
              <li>篷：{vehicle.top ?? "—"}</li>
            </ul>
          ) : (
            <p className="muted">加载车辆档案…</p>
          )}
        </section>
      )}

      {tab === "coding" ? <CodingPage /> : null}
    </div>
  );
}
