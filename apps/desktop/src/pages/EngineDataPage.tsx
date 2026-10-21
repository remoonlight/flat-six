import { useCallback, useEffect, useRef, useState } from "react";
import { engineSessionFixtureEnabled, hasDesktopApi } from "../api";
import { ManufacturerRehearsalPanel } from "../obd/ManufacturerRehearsalPanel";
import { topologySeed } from "../can-topology-data";
import { combinedGeneration, flattenNodes } from "../can-topology-logic.mjs";
import { OfflineRealtimePanel } from "./OfflineRealtimePanel";
import "../engine-session.css";

const CONTROL_UNITS = [...flattenNodes(combinedGeneration(topologySeed())),
  { id: "ecu-75", short: "TV", label: "电视调谐器" },
  { id: "ecu-65", short: "AMP", label: "外部放大器" },
  { id: "ecu-165", short: "ERA", label: "紧急呼叫系统" },
] as Array<{ id: string; short: string; label: string }>;

export function EngineDataPage({ onBusyChange, peerBusy = false, initialSystemId = "",
  adapterModel = null, diagnosticConnectionKey = "", diagnosticReady = false, active = true }: {
  onBusyChange?: (busy: boolean) => void; peerBusy?: boolean; initialSystemId?: string;
  adapterModel?: string | null; diagnosticConnectionKey?: string; diagnosticReady?: boolean; active?: boolean;
}) {
  const [systemId, setSystemId] = useState(() => CONTROL_UNITS.some(unit => unit.id === initialSystemId) ? initialSystemId : "");
  const [generation, setGeneration] = useState<"981" | "982" | null>(null);
  const [vehicleIdentified, setVehicleIdentified] = useState(false);
  const [vehicleEpoch, setVehicleEpoch] = useState(0);
  const [vehicleIdentityBusy, setVehicleIdentityBusy] = useState(false);
  const [manufacturerBusy, setManufacturerBusy] = useState(false);
  const vehicleIdentity = useRef<{ generation: "981" | "982"; sha256: string } | null>(null);
  const locked = peerBusy || manufacturerBusy || vehicleIdentityBusy;
  const system = CONTROL_UNITS.find(unit => unit.id === systemId);
  const onVehicleIdentity = useCallback((detected: "981" | "982" | null, sha256?: string) => {
    setVehicleIdentified(!!detected);
    if (!detected) return;
    if (vehicleIdentity.current?.generation === detected && vehicleIdentity.current.sha256 === sha256) return;
    vehicleIdentity.current = { generation: detected, sha256: sha256 || "" };
    setGeneration(detected); setVehicleEpoch(epoch => epoch + 1);
  }, []);
  useEffect(() => {
    if (initialSystemId && !locked && CONTROL_UNITS.some(unit => unit.id === initialSystemId)) setSystemId(initialSystemId);
  }, [initialSystemId]);
  useEffect(() => { onBusyChange?.(manufacturerBusy || vehicleIdentityBusy); }, [manufacturerBusy, vehicleIdentityBusy, onBusyChange]);

  if (!hasDesktopApi() && !engineSessionFixtureEnabled()) return <section className="panel" data-page="engine-session">
    <h2>实时数据</h2><p className="callout">需要桌面端。浏览器不会连接车辆。</p>
  </section>;
  return <div className="eng" data-page="engine-session"><section className="panel">
    <ManufacturerRehearsalPanel profileId="" parameters={[]} vehicleIdentification vehicleGeneration={vehicleIdentified ? generation : null}
      identityEcuId={9} identityConnectionKey={diagnosticConnectionKey} canIdentify={active && diagnosticReady}
      locked={peerBusy || manufacturerBusy} onBusyChange={setVehicleIdentityBusy} onVehicleIdentity={onVehicleIdentity} />
    <label className="eng-unit-select"><select aria-label="控制单元" data-testid="eng-system" value={systemId} disabled={locked}
      onChange={event => setSystemId(event.target.value)}>
      <option value="">请选择控制单元</option>
      {CONTROL_UNITS.map(unit => <option key={unit.id} value={unit.id}>{unit.short} · {unit.label}</option>)}
    </select></label>
    {!system ? <p className="muted" data-testid="eng-select-unit">先选择控制单元，再选择需要采集的数据。</p> :
      <OfflineRealtimePanel key={`${generation}:${vehicleEpoch}:${systemId}`} generation={generation || "981"} systemId={systemId}
        locked={peerBusy || vehicleIdentityBusy} onBusyChange={setManufacturerBusy} adapterModel={adapterModel}
        diagnosticConnectionKey={vehicleIdentified ? diagnosticConnectionKey : ""}
        autoIdentifyAllowed={vehicleIdentified && active && diagnosticReady} vehicleIdentified={vehicleIdentified}
        vehicleIdentityPending={!!diagnosticConnectionKey && !vehicleIdentified} />}
  </section></div>;
}
