import { useEffect, useRef, useState } from "react";
import { api, callOfflineDiagnostics } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { CodingCapturePanel } from "./CodingCapturePanel";

type Variant = { profileId: string; name: string };
type Scope = { profileId: string; definitionFieldCount: number; definitionCoverageComplete: boolean;
  requestGroups: { requestHex: string; identifierKind: string; identifierHex: string; minimumKnownBytes: number;
    expectedTotalBytes: number | null; fields: unknown[] }[];
  missing: { reason: string; sourceOffset: number }[]; blockers: string[] };

export function CodingReadScopePanel({ ecu }: { ecu: string }) {
  const client = useRef(crypto.randomUUID());
  const [catalogs, setCatalogs] = useState<{ sourceCatalogId: string }[]>([]), [catalogId, setCatalogId] = useState("");
  const [generation, setGeneration] = useState<"981" | "982">("981");
  const [variants, setVariants] = useState<Variant[]>([]), [profileId, setProfileId] = useState("");
  const [ecuId, setEcuId] = useState<number>();
  const [scope, setScope] = useState<Scope>(), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    void callOfflineDiagnostics({ action: "source-catalogs" }).then(doc => {
      if (!active) return; if (!doc.ok) throw new Error(diagnosticMessage(doc.error));
      const rows = doc.catalogs as { sourceCatalogId: string }[]; setCatalogs(rows);
      if (rows.length === 1) setCatalogId(rows[0].sourceCatalogId);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; void api().codingCapture?.({ action: "discard", clientId: client.current }); };
  }, []);
  useEffect(() => {
    let active = true;
    setVariants([]); setProfileId(""); setEcuId(undefined); setScope(undefined); setError("");
    if (!catalogId) return;
    setBusy(true);
    void api().codingCapture?.({ action: "units", clientId: client.current, generation, sourceCatalogId: catalogId, systemId: ecu }).then(doc => {
      if (!active) return; if (!doc?.ok) throw new Error(diagnosticMessage(doc?.error));
      setVariants(doc.unit?.variants || []); setEcuId(doc.unit?.ecuId);
    }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [ecu, generation, catalogId]);
  useEffect(() => {
    let active = true;
    setScope(undefined); setError("");
    if (!profileId || ecuId == null || !catalogId) return;
    setBusy(true);
    void api().codingCapture?.({ action: "scope", clientId: client.current, generation, sourceCatalogId: catalogId, ecuId, profileId }).then(doc => {
      if (!active) return; if (!doc?.ok) throw new Error(diagnosticMessage(doc?.error));
      setScope(doc.plan as Scope);
    }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [profileId, ecuId, generation, catalogId]);
  async function save() {
    if (!scope || busy || saving) return;
    setSaving(true); setError("");
    try {
      const out = await api().saveDiagnosticRecording?.({ fileName: "current-ecu-coding-read-plan.json", recording: scope });
      if (!out?.ok) throw new Error(diagnosticMessage(out?.error || "保存失败"));
    } catch (e) { setError(String(e)); } finally { setSaving(false); }
  }
  return <section data-testid="coding-read-scope">
    <h4>当前控制单元原码读取范围</h4>
    <p>手动选择资料版本以检查读取范围。选择版本不代表已经识别本车；这里不会读取车辆或创建原码基线。</p>
    <label>来源目录 <select aria-label="原码范围来源目录" value={catalogId} disabled={busy || saving} onChange={e => setCatalogId(e.target.value)}>
      <option value="">{catalogs.length ? "请选择来源目录" : "请先导入接车来源资料"}</option>
      {catalogs.map(c => <option key={c.sourceCatalogId} value={c.sourceCatalogId}>{c.sourceCatalogId.slice(0,12)}</option>)}
    </select></label>
    <label>车型 <select aria-label="原码范围车型" value={generation} disabled={busy || saving}
      onChange={(e) => setGeneration(e.target.value as "981" | "982")}><option>981</option><option>982</option></select></label>
    <label>资料版本 <select aria-label="原码范围版本" value={profileId} disabled={busy || saving}
      onChange={(e) => setProfileId(e.target.value)}><option value="">请选择控制单元版本</option>
      {variants.map((v) => <option key={v.profileId} value={v.profileId}>{v.name}</option>)}</select></label>
    {!busy && variants.length === 0 && <p>此控制单元没有可匹配的版本资料。</p>}
    {scope && <div data-testid="coding-read-scope-result">
      <p>{scope.definitionFieldCount} 个来源字段，合并为 {scope.requestGroups.length} 个读取请求；{scope.missing.length} 项来源字段布局缺口。</p>
      <details><summary>查看全部 {scope.requestGroups.length} 个编码读取组</summary>
        {scope.requestGroups.map((g) => <p key={g.requestHex}>{g.identifierKind} {g.identifierHex}：已知字段至少需要 {g.minimumKnownBytes} 字节，完整块长度尚未确认；覆盖 {g.fields.length} 个字段。</p>)}
      </details>
      <p>各编码块的完整长度和整个车辆编码范围尚未确认，不能据此生成“车辆完整原码”或开放修改、恢复。</p>
      {scope.blockers.map((b) => <p key={b}>{b}</p>)}
      <button disabled={busy || saving} onClick={() => void save()}>保存原码读取准备</button>
      {ecuId != null && <CodingCapturePanel key={`${catalogId}:${generation}:${profileId}`} generation={generation} profileId={profileId} ecuId={ecuId} sourceCatalogId={catalogId} />}
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
