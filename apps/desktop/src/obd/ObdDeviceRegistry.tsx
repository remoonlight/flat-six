import type { ObdRegisteredDevice } from "../api";
import "./device-registry.css";

type Route = ObdRegisteredDevice["routes"][number];
const ROUTES: Record<Route, string> = { session: "只读采集", live: "实时数据", broadcast: "广播记录", offline: "离线工作台", coding: "设码记录" };
const DEVICE_ROUTES = new Set<Route>(["session", "live", "broadcast"]);
const HIDDEN_FAMILIES = new Set(["X431", "X431-tablet", "Espressif"]);

export function ObdDeviceRegistry({ devices, busy, selectedId, onSelect, onNavigate }: {
  devices: ObdRegisteredDevice[];
  busy: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNavigate: (route: Route, deviceId?: string) => void;
}) {
  const visibleDevices = devices.filter((device) => !HIDDEN_FAMILIES.has(device.family));
  if (!visibleDevices.length) return null;
  return <section className="obd-device-registry" data-testid="obd-device-registry" aria-label="项目关联设备">
    <h3>项目关联设备 · {visibleDevices.length}</h3>
    <p className="muted">设备离线仍保留档案。当前状态由本次刷新得到；功能入口用于选择任务，打开页面不会自动通信。</p>
    <ul className="obd-registry-grid">
      {visibleDevices.map((d) => <li className="obd-registry-card" key={d.id} data-testid={`obd-registered-${d.family}`}>
        <div className="obd-registry-heading"><strong>{d.name}</strong><span className="muted">{d.state}</span></div>
        <p>{d.connection}{d.comPort ? ` · ${d.comPort}` : ""}</p>
        {d.serial || d.mac ? <p className="muted">{d.serial ? `序列号 ${d.serial}` : `蓝牙 ${d.mac}`}</p> : null}
        <p className="muted">{d.driverInstalled === true ? `驱动/接口已安装${d.driverVersion ? ` · ${d.driverVersion}` : ""}` : d.driverInstalled === false ? "本次未检测到所需驱动/工具" : "驱动/接口状态未确认"}{d.serviceState ? ` · 服务 ${d.serviceState}` : ""}</p>
        {d.firmware ? <p className="muted">最近识别固件：{d.firmware}</p> : null}
        <p>{d.features.join(" · ")}</p>
        <p className="muted">{d.note}</p>
        {d.lastSeenAt ? <p className="muted">最近识别：<time dateTime={d.lastSeenAt}>{new Date(d.lastSeenAt).toLocaleString("zh-CN")}</time></p> : null}
        <div className="row">
          {d.connectable ? <button type="button" className="ghost" disabled={busy || selectedId === d.id} onClick={() => onSelect(d.id)}>{selectedId === d.id ? "已选择" : "选择此设备"}</button> : null}
          {d.routes.map((route) => <button type="button" className="ghost" key={route}
            disabled={busy || (!d.connectable && (route === "session" || route === "live"))}
            onClick={() => onNavigate(route, DEVICE_ROUTES.has(route) && d.connectable ? d.id : undefined)}>{!d.connectable && route === "broadcast" ? "广播回放" : ROUTES[route]}</button>)}
        </div>
      </li>)}
    </ul>
  </section>;
}
