import { useEffect, useState } from "react";
import { callOfflineDiagnostics } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { ApplicationCapturePanel } from "./CodingCapturePanel";

type Catalog = { sourceCatalogId: string; profileFiles: number };
type Unit = { ecuId: number; label: string; variants: { profileId: string; name: string }[] };
export function DtcCapturePanel({ kind = "dtc" }: { kind?: "dtc" | "identity" }) {
  const identity = kind === "identity", label = identity ? "身份核对" : "故障核对";
  const [catalogs, setCatalogs] = useState<Catalog[]>([]), [catalogId, setCatalogId] = useState("");
  const [generation, setGeneration] = useState<"981" | "982">("981");
  const [units, setUnits] = useState<Unit[]>([]), [ecuId, setEcuId] = useState(""), [profileId, setProfileId] = useState("");
  const [error, setError] = useState(""), [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    void callOfflineDiagnostics({ action: "source-catalogs" }).then(doc => {
      if (!active) return; if (!doc.ok) throw new Error(diagnosticMessage(doc.error));
      const rows = doc.catalogs as Catalog[]; setCatalogs(rows);
      if (rows.length === 1) setCatalogId(rows[0].sourceCatalogId);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true; setUnits([]); setEcuId(""); setProfileId(""); setError("");
    if (!catalogId) return () => { active = false; };
    setLoading(true);
    void callOfflineDiagnostics({ action: "source-units", generation, sourceCatalogId: catalogId }).then(doc => {
      if (!active) return; if (!doc.ok) throw new Error(diagnosticMessage(doc.error)); setUnits(doc.units as Unit[]);
    }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [catalogId, generation]);
  const unit = units.find(u => String(u.ecuId) === ecuId);
  return <details data-testid={`${kind}-capture-selection`}>
    <summary>{identity ? "断车后：全候选身份与版本核对" : "断车后：完整故障码核对"}</summary>
    <p>{identity ? "选择已导入来源资料和控制单元，对照该单元的全部候选版本，保留缺回复、解码失败和冲突。不会自动确定实际车辆版本，不读取车辆。" : "选择已导入的来源资料、控制单元及用于比较的来源版本，再显式选择捕获目标和本次采集。来源版本选择不证明车辆实际版本。仅核对已有完整应用响应，不识别车辆、不读取或清除车辆故障。"}</p>
    <label>来源资料 <select aria-label={`${label}来源资料`} value={catalogId} disabled={loading} onChange={e => { setProfileId(""); setEcuId(""); setCatalogId(e.target.value); }}>
      <option value="">{catalogs.length ? "请选择来源目录" : "请先导入接车来源资料"}</option>{catalogs.map(c => <option key={c.sourceCatalogId} value={c.sourceCatalogId}>{c.sourceCatalogId.slice(0, 12)} · {c.profileFiles} 个版本</option>)}
    </select></label>
    <label>车型视图 <select aria-label={`${label}车型`} value={generation} disabled={loading} onChange={e => { setProfileId(""); setEcuId(""); setGeneration(e.target.value as "981" | "982"); }}><option>981</option><option>982</option></select></label>
    <label>控制单元 <select aria-label={`${label}控制单元`} value={ecuId} disabled={loading} onChange={e => { setProfileId(""); setEcuId(e.target.value); }}>
      <option value="">请选择控制单元</option>{units.map(u => <option key={u.ecuId} value={u.ecuId}>{u.label} · {u.ecuId}</option>)}
    </select></label>
    {!identity && <label>资料版本 <select aria-label="故障核对资料版本" value={profileId} disabled={loading || !unit} onChange={e => setProfileId(e.target.value)}>
      <option value="">请选择来源版本</option>{unit?.variants.map(v => <option key={v.profileId} value={v.profileId}>{v.name}</option>)}
    </select></label>}
    {profileId && ecuId && catalogId && <ApplicationCapturePanel key={`${catalogId}:${generation}:${ecuId}:${profileId}`} generation={generation} profileId={profileId} kind="dtc" dtcContext={{ sourceCatalogId: catalogId, ecuId: Number(ecuId) }} />}
    {identity && unit && catalogId && <ApplicationCapturePanel key={`${catalogId}:${generation}:${ecuId}`} generation={generation} profileId="" kind="identity" identityContext={{ sourceCatalogId: catalogId, ecuId: Number(ecuId) }} />}
    {error && <p role="alert">{error}</p>}
  </details>;
}
