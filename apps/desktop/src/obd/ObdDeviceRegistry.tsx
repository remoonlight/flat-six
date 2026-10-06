import type { ObdConnectionDevice, ObdRegisteredDevice } from "../api";
import "./device-registry.css";

const HIDDEN_FAMILIES = new Set(["X431", "X431-tablet", "Espressif"]);

export function ObdDeviceRegistry({ devices, detectedDevices, busy, selectedId, connectionState, onSelect }: {
  devices: ObdRegisteredDevice[];
  detectedDevices: ObdConnectionDevice[];
  busy: boolean;
  selectedId: string | null;
  connectionState: string;
  onSelect: (id: string) => void;
}) {
  const rows = new Map<string, { id: string; family: string; name?: string | null; state: string; serial?: string; comPort?: string | null; selectable: boolean }>();
  for (const d of detectedDevices) {
    if (!d.available) continue;
    const family = d.brand || devices.find((known) => known.id === d.id)?.family || "未识别型号";
    if (!["vLinker", "OBDLink MX+", "VNCI", "PT3G", "unresolved"].includes(family)) continue;
    if (HIDDEN_FAMILIES.has(family)) continue;
    rows.set(d.id, { id: d.id, family, name: d.name, serial: d.serial, comPort: d.comPort,
      state: d.available ? d.transport === "d-pdu-usb" ? "USB 已接入" : d.paired ? "已配对" : "可连接" : d.paired ? "已配对 · 无可用串口" : "本次未发现",
      selectable: !!d.available });
  }
  if (!rows.size) return <p className="muted" data-testid="obd-conn-empty">本次没有发现可用诊断头，请接入设备后刷新。</p>;
  return <div className="obd-device-registry" data-testid="obd-device-registry" aria-label="本次可用设备">
    <ul data-testid="obd-conn-list">
      {[...rows.values()].map((d) => <li key={d.id} data-testid={`obd-registered-${d.family}`}>
        <label className="obd-conn-item">
          <input type="radio" name="obd-device" data-testid={`obd-device-${d.id}`} checked={selectedId === d.id}
            disabled={busy || !d.selectable} onChange={() => onSelect(d.id)} />
          <span>
            <strong>{d.family === "unresolved" ? "未识别型号" : d.family}</strong>
            {d.name && d.name !== d.family ? ` · ${d.name}` : ""}
            {d.serial ? ` · 序列号 ${d.serial}` : d.comPort ? ` · ${d.comPort}` : ""}
            <span className="muted obd-device-state" data-testid={selectedId === d.id ? "obd-conn-state" : undefined}>
              {selectedId === d.id ? connectionState : d.state}
            </span>
          </span>
        </label>
      </li>)}
    </ul>
  </div>;
}
