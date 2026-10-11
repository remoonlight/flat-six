import { useEffect, useState } from "react";
import { callOfflineDiagnostics } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { ApplicationCapturePanel } from "./CodingCapturePanel";

type Unit = { systemId: string; label: string; variants: { profileId: string; name: string }[] };
export function MeasurementCapturePanel() {
  const [generation, setGeneration] = useState<"981" | "982">("981");
  const [units, setUnits] = useState<Unit[]>([]), [systemId, setSystemId] = useState("");
  const [profileId, setProfileId] = useState(""), [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true; setUnits([]); setSystemId(""); setProfileId(""); setError(""); setLoading(true);
    void callOfflineDiagnostics({ action: "catalog-units", generation }).then(doc => {
      if (!active) return;
      if (!doc.ok) throw new Error(diagnosticMessage(doc.error));
      setUnits((doc.units || []) as Unit[]);
    }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [generation]);
  const unit = units.find(u => u.systemId === systemId);
  return <details data-testid="measurement-capture-selection">
    <summary>断车后：完整实时数据核对</summary>
    <p>按采集时的资料版本复解来源中的全部字段。手动选择仅用于离线核对，不识别车辆或控制单元，也不发出读取请求。</p>
    <label>车型视图 <select aria-label="实时复解车型" value={generation} disabled={loading} onChange={e => {
      setProfileId(""); setSystemId(""); setGeneration(e.target.value as "981" | "982");
    }}><option>981</option><option>982</option></select></label>
    <label>控制单元 <select aria-label="实时复解控制单元" value={systemId} disabled={loading} onChange={e => { setSystemId(e.target.value); setProfileId(""); }}>
      <option value="">请选择控制单元</option>{units.map(u => <option key={u.systemId} value={u.systemId}>{u.label}</option>)}
    </select></label>
    <label>资料版本 <select aria-label="实时复解资料版本" value={profileId} disabled={loading || !unit} onChange={e => setProfileId(e.target.value)}>
      <option value="">请选择来源版本</option>{unit?.variants.map(v => <option key={v.profileId} value={v.profileId}>{v.name}</option>)}
    </select></label>
    {loading && <p>正在读取来源目录…</p>}
    {profileId && <ApplicationCapturePanel key={`${generation}:${profileId}`} generation={generation} profileId={profileId} kind="measurement" />}
    {error && <p role="alert">{error}</p>}
  </details>;
}
