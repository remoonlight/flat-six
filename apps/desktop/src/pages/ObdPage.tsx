import { useCallback, useEffect, useMemo, useState } from "react";
import { resolveSkuToLocator } from "@porsche981/domain";
import {
  api,
  hasDesktopApi,
  topologyFixtureEnabled,
  type DtcEntry,
  type LocatorMap,
  type ObdDtc,
  type ObdConnectionDevice,
  type ObdRegisteredDevice,
  type ObdSession,
  type Part,
  type PorscheApi,
  type Vehicle,
} from "../api";
import { headerTaskLabel, headerTaskState } from "../can-topology-logic.mjs";
import { formatHeaderVoltage } from "../obd-connection-logic.mjs";
import type { LocatorFocus } from "../locator-focus";
import { CodingPage } from "./CodingPage";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { GuidedTroubleshootPanel, type GuideSeed } from "./GuidedTroubleshootPanel";
import { RepairComparePanel } from "./RepairComparePanel";
import { OfflineDiagnosticsPage } from "./OfflineDiagnosticsPage";
import { ReadOnlySessionPage } from "./ReadOnlySessionPage";
import { TopologyPage } from "./TopologyPage";
import { EngineDataPage } from "./EngineDataPage";
import { CanCapturePage } from "./CanCapturePage";
import { ObdDeviceRegistry } from "../obd/ObdDeviceRegistry";

export type ObdPageProps = {
  onLocate?: (focus: LocatorFocus) => void;
};

type ObdTab =
  | "topology"
  | "connection"
  | "live"
  | "broadcast"
  | "faults"
  | "guide"
  | "compare"
  | "monitors"
  | "insights"
  | "vehicle"
  | "coding"
  | "offline"
  | "session";

const TABS: { id: ObdTab; label: string }[] = [
  { id: "topology", label: "系统拓扑" },
  { id: "connection", label: "连接设置" },
  { id: "live", label: "实时数据" },
  { id: "broadcast", label: "广播记录" },
  { id: "faults", label: "故障码" },
  { id: "guide", label: "引导排障" },
  { id: "compare", label: "维修对比" },
  { id: "monitors", label: "就绪监控" },
  { id: "insights", label: "分析洞察" },
  { id: "vehicle", label: "车辆信息" },
  { id: "coding", label: "设码" },
  { id: "offline", label: "离线工作台" },
  { id: "session", label: "只读采集" },
];

function vnciConnectionMessage(error: unknown): string | null {
  if (error === "vnci-obd-unpowered-or-invalid-voltage") return "VNCI 已识别，但 OBD 供电异常。请接入本车 OBD 接口并确认供电后重试。";
  if (error === "vnci-device-in-use") return "VNCI 被其他软件占用。请退出 ODIS、PIWIS 等诊断软件后重试。";
  if (error === "vnci-firmware-mismatch-no-auto-update") return "VNCI 固件与已接入的驱动版本不一致，连接已停止。请核对驱动版本；项目不会自动升级诊断头。";
  if (error === "vnci-firmware-check-failed") return "无法核对 VNCI 固件。请检查 USB 连接后重试。";
  if (error === "vnci-driver-version-not-qualified") return "VNCI 驱动文件已变化，需重新核对版本后才能连接。";
  return null;
}

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
  const [tab, setTab] = useState<ObdTab>("topology");
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
  const [topoBusy, setTopoBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [engineBusy, setEngineBusy] = useState(false);
  const [captureBusy, setCaptureBusy] = useState(false);
  const [overview, setOverview] = useState<{
    ok?: boolean;
    taskState?: string;
    voltageVolts?: number | null;
    voltageLabel?: string;
  } | null>(null);
  const [commLost, setCommLost] = useState(false);
  const [devices, setDevices] = useState<ObdConnectionDevice[]>([]);
  const [deviceRegistry, setDeviceRegistry] = useState<ObdRegisteredDevice[]>([]);
  const [listErrors, setListErrors] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [connBusy, setConnBusy] = useState(false);
  const [connNote, setConnNote] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [linkState, setLinkState] = useState("idle");
  const [modelPick, setModelPick] = useState<"vLinker" | "OBDLink MX+" | "VNCI" | "">("");
  const [guideSeed, setGuideSeed] = useState<GuideSeed | null>(null);
  const [guideEcu, setGuideEcu] = useState<"dme" | "gateway" | "unknown">("unknown");

  const apiAvailable = hasDesktopApi() || topologyFixtureEnabled();
  const taskState = headerTaskState({
    apiAvailable,
    overviewOk: overview?.ok === true,
    overviewState: overview?.taskState,
    localRunning: topoBusy || sessionBusy || engineBusy || captureBusy,
    commLost,
  });

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
    let stop = false;
    let latest = 0;
    const tick = async () => {
      const seq = ++latest;
      const fn = typeof window !== "undefined" ? window.porsche981?.readOnlySession : undefined;
      if (!fn) {
        if (!stop && seq === latest) setOverview(null);
        return;
      }
      try {
        const doc = await fn({ action: "overview" } as never);
        if (stop || seq !== latest) return;
        setCommLost(false);
        if (doc && doc.ok === true) setOverview(doc);
        else setOverview({ ok: false });
        const connFn = window.porsche981?.obdConnection || window.__FAKE_CONNECTION__;
        if (connFn) {
          const st = await connFn({ action: "status" });
          if (stop || seq !== latest) return;
          setConnected(!!st.connected);
          const vnciNote = vnciConnectionMessage(st.error || st.connectionError);
          if (vnciNote) setConnNote(vnciNote);
          if (typeof st.linkState === "string") setLinkState(st.linkState);
          if (typeof st.voltageVolts === "number") {
            setOverview((o) => ({ ...(o || {}), ok: o?.ok, taskState: o?.taskState, voltageVolts: st.voltageVolts }));
          } else {
            setOverview((o) => ({ ...(o || {}), ok: o?.ok, taskState: o?.taskState, voltageVolts: null }));
          }
        }
      } catch {
        if (stop || seq !== latest) return;
        setCommLost(true);
        setConnected(false);
        setOverview({ ok: false });
      }
    };
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      await tick();
      if (!stop) timer = setTimeout(() => void poll(), 800);
    };
    void poll();
    return () => {
      stop = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);

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

  const tabsLocked = topoBusy || sessionBusy || engineBusy || captureBusy;
  const sessionLocked = tabsLocked || overview?.taskState === "running";
  const selectedDev = devices.find((d) => d.id === selectedId);
  const needsModel = selectedDev?.brand === "unresolved" && !modelPick;
  const canConnect = Boolean(
    selectedId && selectedDev?.available && !needsModel && !connBusy && !sessionLocked && linkState !== "connecting" && !connected,
  );
  const expandedSession = sessions.find((s) => s.id === expandedId) ?? null;

  function applyConn(doc: {
    ok?: boolean;
    error?: string | null;
    devices?: ObdConnectionDevice[];
    deviceRegistry?: ObdRegisteredDevice[];
    listErrors?: string[];
    selectedDeviceId?: string | null;
    model?: string | null;
    connected?: boolean;
    linkState?: string;
    voltageVolts?: number | null;
    guidance?: string;
  }) {
    if (doc.devices) setDevices(doc.devices);
    if (doc.deviceRegistry) setDeviceRegistry(doc.deviceRegistry);
    if (doc.listErrors) setListErrors(doc.listErrors);
    if ("selectedDeviceId" in doc) setSelectedId(doc.selectedDeviceId ?? null);
    if ("model" in doc) setModelPick(doc.model === "vLinker" || doc.model === "OBDLink MX+" || doc.model === "VNCI" ? doc.model : "");
    if (typeof doc.voltageVolts === "number") {
      setOverview((o) => ({ ...(o || {}), voltageVolts: doc.voltageVolts }));
    } else if (connected && doc.connected === false) {
      setOverview((o) => ({ ...(o || {}), voltageVolts: null }));
    }
    if ("connected" in doc) setConnected(!!doc.connected);
    if (typeof doc.linkState === "string") setLinkState(doc.linkState);
    const hit = (doc.devices || devices).find((d) => d.id === (doc.selectedDeviceId ?? selectedId));
    const err = doc.error;
    const note =
      vnciConnectionMessage(err) || (err === "device_port_unavailable" || err === "busy"
        ? hit?.guidance || (err === "busy" ? "诊断任务占用链路" : "已配对但没有可用串口")
        : err || hit?.guidance || null);
    setConnNote(note);
  }

  async function connectionCall(req: Parameters<NonNullable<PorscheApi["obdConnection"]>>[0]) {
    const fn = window.porsche981?.obdConnection || window.__FAKE_CONNECTION__;
    if (!fn) {
      applyConn({ ok: false, error: "desktop_required", devices: [], voltageVolts: null, connected: false });
      return;
    }
    setConnBusy(true);
    try {
      const result = await fn(req);
      applyConn(result);
      return result;
    } catch (e) {
      applyConn({ ok: false, error: String(e), connected: false, voltageVolts: null });
    } finally {
      setConnBusy(false);
    }
  }

  useEffect(() => {
    if (tab !== "connection") return;
    void connectionCall({ action: "list" });
  }, [tab]);

  return (
    <div className="obd-page" data-page="obd">
      <header className="page-head">
        <div>
          <p className="muted obd-kicker">诊断</p>
          <h1>
            实时 OBD <span className="obd-beta">试运行</span>
          </h1>
        </div>
        <div className="obd-head-meta">
          <p className="obd-header-voltage" data-testid="obd-header-voltage">
            {formatHeaderVoltage(overview?.voltageVolts ?? null)}
          </p>
          <p className="obd-task-status" data-testid="obd-task-status" data-state={taskState}>
            <span className="obd-status-dot" data-state={taskState} aria-hidden />
            {headerTaskLabel(taskState)}
          </p>
        </div>
      </header>

      <nav className="obd-tabs chip-row" aria-label="OBD 分区">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-obd-tab={t.id}
            className={`chip${tab === t.id ? " active" : ""}`}
            disabled={tabsLocked && tab !== t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {error && tab === "faults" ? <p className="error">{error}</p> : null}

      {tab === "connection" && (
        <section className="panel" data-testid="obd-connection">
          <h2>连接设置</h2>
          <p className="muted">
            选择 vLinker / OBDLink MX+ 蓝牙设备或 VNCI USB 诊断头，点击「连接设备」检查连接与供电电压。
          </p>
          <details data-testid="obd-mx-pairing">
            <summary>OBDLink MX+ 蓝牙连接步骤</summary>
            <ol>
              <li>保持 MX+ 现有 Drive CAN 分接口接线并通电，按设备配对按钮。</li>
              <li>在 Windows 蓝牙设置中添加「OBDLink MX+」，按系统提示完成配对。</li>
              <li>确认已建立 Bluetooth SPP 串口，然后点击「刷新」并选择 MX+。</li>
              <li>先断开 RaceChrono、OBDwiz 等应用对 MX+ 的连接，再点击「连接设备」。</li>
            </ol>
            <p className="muted">Windows 显示「已配对」不代表适配器正在通信；连接成功后本页会显示电压。诊断读取需在相应页面单独启动。</p>
            <p className="muted">本车 MX+ 接车内 Drive CAN；连接设置检查握手与电压，「广播记录」可采集和回放。已有采集出现 CAN ERROR，信号解码与诊断口读取仍待验证。</p>
          </details>
          <details data-testid="obd-vnci-connection">
            <summary>VNCI USB 连接步骤</summary>
            <ol>
              <li>将 VNCI 接入本车 OBD 接口并通电，再用 USB 连接电脑。</li>
              <li>退出 ODIS、PIWIS、X431 等主动诊断软件，点击「刷新」并按序列号选择 VNCI。</li>
              <li>点击「连接设备」检查 OBD 供电，再在对应页面启动只读采集。</li>
            </ol>
            <p className="muted">当前支持 VAS6154A USB 接口。仅接 USB 时可能没有 OBD 供电；设码需按具体功能完成备份、回读与恢复验证。</p>
          </details>
          {listErrors.length > 0 ? (
            <p className="muted" data-testid="obd-conn-errors">
              {listErrors.join("；")}
            </p>
          ) : null}
          {devices.length === 0 ? (
            <p className="muted" data-testid="obd-conn-empty">
              未发现适配器。请检查蓝牙配对或 VNCI USB 连接，确认设备驱动已安装后刷新。
            </p>
          ) : (
            <ul className="plain-list" data-testid="obd-conn-list">
              {devices.map((d) => (
                <li key={d.id}>
                  <label className="obd-conn-item">
                    <input
                      type="radio"
                      name="obd-device"
                      data-testid={`obd-device-${d.id}`}
                      checked={selectedId === d.id}
                      disabled={connBusy || sessionLocked}
                      onChange={() => {
                        setSelectedId(d.id);
                        setModelPick("");
                        void connectionCall({
                          action: "select",
                          deviceId: d.id,
                        });
                      }}
                    />
                    <span>
                      <strong>{d.brand === "unresolved" ? "未识别型号" : d.brand}</strong>
                      {d.name ? ` · ${d.name}` : ""}
                      {d.transport === "d-pdu-usb" ? ` · 序列号 ${d.serial}` : d.comPort ? ` · ${d.comPort}` : " · 无 COM"}
                      {d.available ? "" : " · 不可用"}
                      {!d.available && d.guidance ? ` · ${d.guidance}` : d.paired && !d.available ? " · 已配对无串口" : ""}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {devices.some((d) => d.id === selectedId && d.brand === "unresolved") ? (
            <label className="field">
              型号（系统名称无法识别）
              <select
                data-testid="obd-model-pick"
                value={modelPick}
                onChange={(e) => {
                  const model = e.target.value as typeof modelPick;
                  setModelPick(model);
                  if (model && selectedId) void connectionCall({ action: "select", deviceId: selectedId, model });
                }}
                disabled={connBusy || sessionLocked}
              >
                <option value="">请选择实际型号</option>
                <option value="vLinker">vLinker</option>
                <option value="OBDLink MX+">OBDLink MX+</option>
              </select>
            </label>
          ) : null}
          <div className="row">
            <button
              type="button"
              data-testid="obd-conn-bluetooth"
              className="ghost"
              disabled={!window.porsche981?.obdOpenBluetooth}
              onClick={() => void window.porsche981?.obdOpenBluetooth().catch((e: unknown) => setConnNote(String(e)))}
            >
              打开 Windows 蓝牙设置
            </button>
            <button
              type="button"
              data-testid="obd-conn-refresh"
              className="btn"
              disabled={connBusy}
              onClick={() => void connectionCall({ action: "list" })}
            >
              刷新
            </button>
            <button
              type="button"
              data-testid="obd-conn-connect"
              className="btn"
              disabled={!canConnect}
              onClick={() =>
                void connectionCall({
                  action: "connect",
                  deviceId: selectedId || undefined,
                  model: selectedDev?.brand === "unresolved" && modelPick ? modelPick : undefined,
                })
              }
            >
              连接设备
            </button>
            <button
              type="button"
              className="ghost"
              data-testid="obd-conn-disconnect"
              disabled={connBusy || sessionLocked}
              onClick={() => void connectionCall({ action: "disconnect" })}
            >
              断开
            </button>
            <button
              type="button"
              className="ghost"
              data-testid="obd-conn-clear"
              disabled={connBusy || sessionLocked}
              onClick={() => void connectionCall({ action: "clear" })}
            >
              清除选择
            </button>
          </div>
          <p className="muted" data-testid="obd-conn-state">
            {sessionLocked || linkState === "diagnostic"
              ? "诊断占用"
              : linkState === "connecting"
                ? "连接中"
                : linkState === "reconnecting"
                  ? "等待重连"
                  : connected || linkState === "connected"
                    ? "已连接"
                    : "未建立适配器通信"}
          </p>
          {connNote ? (
            <p className="muted" data-testid="obd-conn-note">
              {connNote}
            </p>
          ) : null}
          <ObdDeviceRegistry devices={deviceRegistry} busy={connBusy || sessionLocked} selectedId={selectedId}
            onSelect={(deviceId) => void connectionCall({ action: "select", deviceId })}
            onNavigate={async (route, deviceId) => {
              if (deviceId && !(await connectionCall({ action: "select", deviceId }))?.ok) return;
              setTab(route);
            }} />
        </section>
      )}

      {tab === "live" ? (
        <EngineDataPage
          onBusyChange={setEngineBusy}
          peerBusy={topoBusy || sessionBusy || captureBusy || (overview?.taskState === "running" && !engineBusy)}
        />
      ) : null}

      {tab === "faults" && (
        <section className="panel">
          <h2>故障码</h2>
          <p className="muted">
            输入 OBD-II 故障码查内置说明；可记入手工会话。硬件扫码见「系统拓扑」读取；清故障码请到「系统拓扑」。
          </p>
          <div className="row">
            <button type="button" disabled>
              刷新故障码
            </button>
            <button type="button" className="ghost" onClick={() => setTab("topology")}>
              前往系统拓扑清码
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
            <label>
              控制单元语境
              <select data-testid="obd-guide-ecu" value={guideEcu} onChange={(e) => setGuideEcu(e.target.value as typeof guideEcu)}>
                <option value="unknown">未声明（不套用 DME 检查）</option>
                <option value="dme">已知 DME（981 发动机档案）</option>
                <option value="gateway">已知网关</option>
              </select>
            </label>
            <button
              type="button"
              className="ghost"
              data-testid="obd-fault-open-guide"
              disabled={!normalized}
              onClick={() => {
                if (!normalized) return;
                setGuideSeed({
                  code: normalized,
                  moduleKey: guideEcu === "dme" ? "porsche-981-2014-dme" : guideEcu === "gateway" ? "porsche-981-2014-gateway" : "unknown",
                  ecuContext: guideEcu,
                  source: "manual",
                  identityKind: "unknown",
                });
                setTab("guide");
              }}
            >
              引导排障
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

      <div hidden={tab !== "guide"}>
        <GuidedTroubleshootPanel
          seed={guideSeed}
          vehicleKm={vehicle?.current_km ?? 0}
          onLocate={onLocate}
          onOpenEngine={() => setTab("live")}
        />
      </div>
      {tab === "compare" ? <RepairComparePanel vehicleKm={vehicle?.current_km ?? 0} /> : null}

      {tab === "insights" && (
        <section className="panel obd-legacy-faults" aria-label="故障台账过渡">
          <h2>分析洞察</h2>
          <p className="muted">故障台账（过渡）；硬件落地前继续用手工记录。</p>
          <DiagnosticsPage
            onLocate={onLocate}
            onOpenGuide={(seed) => {
              setGuideSeed(seed);
              setTab("guide");
            }}
          />
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

      <div hidden={tab !== "topology"}>
        <TopologyPage
          onBusyChange={setTopoBusy}
          peerBusy={sessionBusy || engineBusy || captureBusy || (overview?.taskState === "running" && !topoBusy)}
          onOpenGuide={(seed) => {
            setGuideSeed({
              ...seed,
              ecuContext:
                seed.ecuContext ||
                (seed.moduleKey === "porsche-981-2014-dme" ? "dme" : seed.moduleKey === "porsche-981-2014-gateway" ? "gateway" : "unknown"),
            });
            setTab("guide");
          }}
        />
      </div>
      <div hidden={tab !== "broadcast"}>
        <CanCapturePage peerBusy={topoBusy || sessionBusy || engineBusy} onBusyChange={setCaptureBusy} />
      </div>
      {tab === "coding" ? <CodingPage /> : null}
      {tab === "offline" ? <OfflineDiagnosticsPage /> : null}
      <div hidden={tab !== "session"}>
        <ReadOnlySessionPage
          onBusyChange={setSessionBusy}
          peerBusy={topoBusy || engineBusy || captureBusy || (overview?.taskState === "running" && !sessionBusy)}
        />
      </div>
    </div>
  );
}
