import { useEffect, useState } from "react";
import {
  hasDesktopApi,
  topologyFixtureEnabled,
  type ObdConnectionDevice,
  type ObdCanNetwork,
  type ObdConnectionResult,
  type ObdRegisteredDevice,
  type PorscheApi,
} from "../api";
import { headerTaskLabel, headerTaskState } from "../can-topology-logic.mjs";
import { formatHeaderVoltage } from "../obd-connection-logic.mjs";
import { CodingPage } from "./CodingPage";
import { TopologyPage } from "./TopologyPage";
import { EngineDataPage } from "./EngineDataPage";
import { ObdDeviceRegistry } from "../obd/ObdDeviceRegistry";
import { InternalCanPanel } from "../obd/InternalCanPanel";

type ObdTab =
  | "topology"
  | "connection"
  | "live"
  | "coding";

const TABS: { id: ObdTab; label: string }[] = [
  { id: "connection", label: "连接设置" },
  { id: "topology", label: "系统拓扑" },
  { id: "live", label: "实时数据" },
  { id: "coding", label: "设码与编程" },
];

const CONNECTION_PURPOSES = [
  { value: "diagnostic", label: "诊断" },
  { value: "drive", label: "内网-驱动can" },
  { value: "chassis", label: "内网-底盘can" },
  { value: "comfort", label: "内网-舒适性can" },
  { value: "crash", label: "内网-碰撞can" },
] as const;

function vnciConnectionMessage(error: unknown): string | null {
  const messages: Record<string, string> = {
    device_port_unavailable: "上次选择的诊断头本次不可用。请接入该设备后刷新，或手动选择其他设备。",
    can_network_required: "请按实际接线选择内网用途。",
    internal_receive_not_integrated: "内网持续接收尚未接入桌面，本次不会打开诊断链路。已保存所选 CAN。",
    internal_profile_not_qualified: "此诊断头与 CAN 尚无已核实的原始监听配置，连接已停止；不会发送诊断请求。",
    diagnostic_purpose_required: "当前用途为内网读取。请断开设备、手动改接诊断 CAN，再选择诊断用途。",
    disconnect_before_configure: "请先断开设备并等待端口释放，再更改用途或 CAN。",
    monitor_close_timeout: "关闭未完成，正在等待诊断头端口释放。请勿重复连接。",
    session_close_timeout: "诊断任务关闭未完成，正在等待端口释放。请勿重复连接。",
    "pt3g-vehicle-transport-not-qualified": "PT3G 已支持诊断头连接与供电监测；车辆读取尚未核实，当前不能启动诊断。",
    "pt3g-device-in-use": "PT3G 被其他软件占用，请退出 PIWIS 等诊断软件后重试。",
    "pt3g-device-identity-missing-or-ambiguous": "未找到唯一匹配的 PT3G，请核对 USB 连接并刷新。",
    "pt3g-cleanup-failed": "PT3G 释放失败，请等待连接关闭后重试。",
    adapter_release_not_verified: "诊断头原生释放未确认，已停止重连及诊断。请重新接入该头 USB，再重启应用并检查连接。",
    disconnect_before_refresh: "请先断开诊断头并等待释放，再刷新设备列表。",
  };
  if (typeof error === "string" && messages[error]) return messages[error];
  if (typeof error === "string" && error.startsWith("pt3g-driver-support-")) return "PT3G 驱动配套资料缺失或版本已变化，请核对完整 E70 驱动目录。";
  if (error === "vnci-device-in-use") return "VNCI 被其他软件占用。请退出 ODIS、PIWIS 等诊断软件后重试。";
  if (error === "vnci-firmware-mismatch-no-auto-update") return "VNCI 固件与已接入的驱动版本不一致，连接已停止。请核对驱动版本；项目不会自动升级诊断头。";
  if (error === "vnci-firmware-check-failed") return "无法核对 VNCI 固件。请检查 USB 连接后重试。";
  if (typeof error === "string" && error.startsWith("vnci-driver-support-missing:")) return "VNCI 驱动配套资料缺失。请补齐与当前版本匹配的完整驱动目录后重试。";
  if (typeof error === "string" && error.startsWith("vnci-driver-support-not-qualified:")) return "VNCI 驱动配套资料与已核对版本不一致，连接已停止。请核对完整驱动目录。";
  if (error === "vnci-driver-version-not-qualified") return "VNCI 驱动文件已变化，需重新核对版本后才能连接。";
  return null;
}

export function ObdPage() {
  const [tab, setTab] = useState<ObdTab>("topology");
  const [topoBusy, setTopoBusy] = useState(false);
  const [engineBusy, setEngineBusy] = useState(false);
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
  const [purpose, setPurpose] = useState<"diagnostic" | "internal">("diagnostic");
  const [canNetwork, setCanNetwork] = useState<ObdCanNetwork | "">("");
  const [connectionStatus, setConnectionStatus] = useState<ObdConnectionResult | null>(null);
  const [modelPick, setModelPick] = useState<"vLinker" | "OBDLink MX+" | "VNCI" | "PT3G" | "">("");
  const [topologyAdapterModel, setTopologyAdapterModel] = useState<string | null>(null);
  const [codingTargetSystem, setCodingTargetSystem] = useState("");
  const [liveTargetSystem, setLiveTargetSystem] = useState("");

  const apiAvailable = hasDesktopApi() || topologyFixtureEnabled();
  const taskState = headerTaskState({
    apiAvailable,
    overviewOk: overview?.ok === true,
    overviewState: overview?.taskState,
    localRunning: topoBusy || engineBusy,
    commLost,
  });

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
          applyConn(st);
          setConnected(!!st.connected);
          setTopologyAdapterModel(typeof st.model === "string" ? st.model : null);
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

  const tabsLocked = topoBusy || engineBusy;
  const sessionLocked = tabsLocked || overview?.taskState === "running";
  const selectedDev = devices.find((d) => d.id === selectedId);
  const needsModel = selectedDev?.brand === "unresolved" && !modelPick;
  const canConnect = Boolean(
    selectedId && selectedDev?.available && !needsModel && !connBusy && !sessionLocked && linkState === "idle" && !connected && (purpose !== "internal" || !!canNetwork),
  );

  function applyConn(doc: {
    purpose?: ObdConnectionResult["purpose"];
    canNetwork?: ObdConnectionResult["canNetwork"];
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
    setConnectionStatus(doc as ObdConnectionResult);
    if (doc.purpose) setPurpose(doc.purpose);
    if ("canNetwork" in doc) setCanNetwork(doc.canNetwork || "");
    if (doc.devices) setDevices(doc.devices);
    if (doc.deviceRegistry) setDeviceRegistry(doc.deviceRegistry);
    if (doc.listErrors) setListErrors(doc.listErrors);
    if ("selectedDeviceId" in doc) setSelectedId(doc.selectedDeviceId ?? null);
    if ("model" in doc) setModelPick(doc.model === "vLinker" || doc.model === "OBDLink MX+" || doc.model === "VNCI" || doc.model === "PT3G" ? doc.model : "");
    if ("model" in doc) setTopologyAdapterModel(doc.model || null);
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
        : err || null);
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
          <p className="obd-header-device" data-testid="obd-header-device">
            已连接设备：{connected ? topologyAdapterModel || "型号未知" : "无"}
          </p>
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
            disabled={tabsLocked && tab !== t.id && t.id !== "connection"}
            onClick={() => {
              if (t.id === "coding") setCodingTargetSystem("");
              if (t.id === "live") setLiveTargetSystem("");
              setTab(t.id);
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "connection" && (
        <section className="panel" data-testid="obd-connection">
          <fieldset className="obd-purpose-options" data-testid="obd-purpose" disabled={connBusy || sessionLocked || linkState !== "idle"}>
            <legend>连接用途</legend>
            {CONNECTION_PURPOSES.map((option) => <label key={option.value}>
              <input type="radio" name="obd-purpose" data-testid={`obd-purpose-${option.value}`} value={option.value}
                checked={(purpose === "diagnostic" ? "diagnostic" : canNetwork) === option.value}
                onChange={() => void connectionCall(option.value === "diagnostic" ? { action: "configure", purpose: "diagnostic" } :
                  { action: "configure", purpose: "internal", canNetwork: option.value })} />
              <span>{option.label}</span>
            </label>)}
          </fieldset>
          {purpose === "internal" ? <p className="muted">请手动接到所选 CAN。项目不自动识别网络或切换接线；当前仅 MX+ 驱动 CAN 有已核实的监听配置。</p> : null}
          {purpose === "internal" && canNetwork === "adas" ? <p className="muted">上次保存的接线为 ADAS CAN，请按实际接线重新选择连接用途。</p> : null}
          <p className="muted">支持 vLinker FS BT、OBDLink MX+、VNCI VAS6154A 与 PT3G / E70（USB）。PT3G 当前仅支持诊断头连接与供电监测。</p>
          <ObdDeviceRegistry devices={deviceRegistry} detectedDevices={devices} busy={connBusy || sessionLocked} selectedId={selectedId}
            connectionState={linkState === "disconnecting" ? "正在断开" : linkState === "close_failed" ? "关闭失败 · 等待释放" : sessionLocked || linkState === "diagnostic" ? "诊断占用" : linkState === "connecting" ? "连接中" : linkState === "reconnecting" ? "等待重连" : connected ? "已连接" : "已选择"}
            onSelect={(deviceId) => {
              setSelectedId(deviceId);
              setModelPick("");
              void connectionCall({ action: "select", deviceId });
            }} />
          {devices.some((d) => d.id === selectedId && d.brand === "unresolved") ? (
            <label className="field">
              设备型号
              <select data-testid="obd-model-pick" value={modelPick} disabled={connBusy || sessionLocked}
                onChange={(e) => {
                  const model = e.target.value as typeof modelPick;
                  setModelPick(model);
                  if (model && selectedId) void connectionCall({ action: "select", deviceId: selectedId, model });
                }}>
                <option value="">请选择实际型号</option>
                <option value="vLinker">vLinker</option>
                <option value="OBDLink MX+">OBDLink MX+</option>
              </select>
            </label>
          ) : null}
          <div className="row">
            <button type="button" data-testid="obd-conn-refresh" className="btn" disabled={connBusy}
              onClick={() => void connectionCall({ action: "list" })}>刷新</button>
            <button type="button" data-testid="obd-conn-connect" className="btn" disabled={!canConnect}
              onClick={() => void connectionCall({ action: "connect", deviceId: selectedId || undefined,
                model: selectedDev?.brand === "unresolved" && modelPick ? modelPick : undefined })}>连接设备</button>
            <button type="button" data-testid="obd-conn-disconnect" className="ghost" disabled={connBusy || linkState === "disconnecting"}
              onClick={() => void connectionCall({ action: "disconnect" })}>断开设备</button>
          </div>
          {listErrors.length > 0 ? <p className="error" data-testid="obd-conn-errors">{listErrors.join("；")}</p> : null}
          {connNote ? <p className="error" data-testid="obd-conn-note">{connNote}</p> : null}
        </section>
      )}

      {tab === "live" ? (
        purpose === "internal" ? <InternalCanPanel status={connectionStatus} busy={connBusy} invoke={connectionCall} /> : <EngineDataPage
          initialSystemId={liveTargetSystem}
          onBusyChange={setEngineBusy}
          peerBusy={topoBusy || (overview?.taskState === "running" && !engineBusy)}
        />
      ) : null}

      <div hidden={tab !== "topology"}>
        <TopologyPage
          onBusyChange={setTopoBusy}
          peerBusy={engineBusy || (overview?.taskState === "running" && !topoBusy)}
          adapterModel={topologyAdapterModel}
          active={tab === "topology"}
          diagnosticReady={purpose === "diagnostic" && connected}
          onNavigate={(target, systemId) => {
            if (target === "coding") setCodingTargetSystem(systemId || "");
            if (target === "live") setLiveTargetSystem(systemId || "");
            setTab(target);
          }}
        />
      </div>
      {tab === "coding" ? <CodingPage initialSystemId={codingTargetSystem} /> : null}
    </div>
  );
}
