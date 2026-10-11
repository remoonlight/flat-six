import { useEffect, useState } from "react";
import { api, callOfflineDiagnostics } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";

type Catalog = { sourceCatalogId: string; profileFiles: number; generatorCodeMatchesCurrent: boolean };
type Unit = { ecuId: number; label: string; variants: { profileId: string; name: string }[] };
type Reference = { variant: string; module: string; ecuId: number; counts: Record<string, number>;
  diagnosticBindingStatus: string; versionBindingStatus: string; selectorBindingStatus: string;
  codingPreviewSummary?: { namedOptionFields: number; numericCandidateFields: number; blockedFields: number } | null;
  generatorCodeMatchesCurrent: boolean; selectorGroups: { requestHex: string; sourceOccurrences: number;
    postprocessingCompleteOccurrences: number; missingReason: string | null; loaderRequiredSymbolCount: number }[] };

const status = (value: string) => ({ "exact-source-name-match": "名称精确对应", "exact-source-version-rule-match": "版本规则名称精确对应",
  "source-name-missing": "缺少同名诊断来源", "source-link-ambiguous": "诊断来源链接有歧义", "source-route-or-version-ambiguous": "版本路由或规则有歧义",
  "exact-selector-read-source-coverage": "选择字段读取源齐全", "selector-read-source-coverage-incomplete": "选择字段读取源缺项",
  "source-binding-missing-or-ambiguous": "来源绑定缺失或有歧义", "source-version-name-missing": "缺少同名版本规则",
  "read-command-only-in-other-ECUs": "只在其他单元找到请求来源", "current-exact-source-name-missing": "缺少当前精确版本名称" }[value] || "来源尚未核实");

export function SourceReferencePanel({ locked = false }: { locked?: boolean }) {
  const [catalogs, setCatalogs] = useState<Catalog[]>([]), [catalogId, setCatalogId] = useState("");
  const [generation, setGeneration] = useState<"981" | "982">("981"), [units, setUnits] = useState<Unit[]>([]);
  const [ecu, setEcu] = useState(""), [profileId, setProfileId] = useState("");
  const [reference, setReference] = useState<Reference>(), [revision, setRevision] = useState(0);
  const [error, setError] = useState(""), [importing, setImporting] = useState(false);
  useEffect(() => {
    let active = true; setCatalogs([]); setCatalogId(""); setError("");
    void callOfflineDiagnostics({ action: "source-catalogs" }).then(doc => {
      if (!active) return; if (!doc.ok) throw new Error(diagnosticMessage(doc.error));
      const rows = doc.catalogs as Catalog[]; setCatalogs(rows);
      if (rows.length === 1) setCatalogId(rows[0].sourceCatalogId);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    let active = true; setUnits([]); setEcu(""); setProfileId(""); setReference(undefined); setError("");
    if (catalogId) void callOfflineDiagnostics({ action: "source-units", generation, sourceCatalogId: catalogId }).then(doc => {
      if (!active) return; if (!doc.ok) throw new Error(diagnosticMessage(doc.error)); setUnits(doc.units as Unit[]);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [catalogId, generation]);
  useEffect(() => {
    let active = true; setReference(undefined); setError("");
    if (catalogId && ecu && profileId) void callOfflineDiagnostics({ action: "source-profile", generation,
      sourceCatalogId: catalogId, ecuId: Number(ecu), profileId }).then(doc => {
      if (!active) return; if (!doc.ok) throw new Error(diagnosticMessage(doc.error)); setReference(doc as unknown as Reference);
    }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [catalogId, generation, ecu, profileId]);
  async function importDefinitions() {
    if (locked || importing) return; setImporting(true); setError("");
    try {
      const fn = api().diagnosticDefinitionBundle;
      if (!fn) throw new Error("需要桌面端资料导入功能");
      const result = await fn({ action: "import" });
      if (!result.ok) throw new Error(diagnosticMessage(result.error));
      if (!result.canceled) setRevision(value => value+1);
    } catch (e) { setError(String(e)); } finally { setImporting(false); }
  }
  const unit = units.find(row => String(row.ecuId) === ecu);
  return <section data-testid="source-reference">
    <h4>接车前来源与缺项</h4>
    <p>按车型、控制单元和目录版本查阅来源资料。来源齐全仍需核对实际安装、身份、会话与完整响应；实车诊断、实时读取及设码资格尚未通过。</p>
    <button disabled={locked || importing} onClick={() => void importDefinitions()} data-testid="source-import">导入接车来源资料</button>
    <select aria-label="接车来源资料版本" data-testid="source-catalog" value={catalogId} disabled={locked || importing}
      onChange={e => { setReference(undefined); setCatalogId(e.target.value); }}>
      <option value="">{catalogs.length ? "请选择资料版本" : "尚未导入来源资料"}</option>
      {catalogs.map(row => <option key={row.sourceCatalogId} value={row.sourceCatalogId}>{row.sourceCatalogId.slice(0,12)} · {row.profileFiles} 个版本来源</option>)}
    </select>
    <select aria-label="来源资料车型" data-testid="source-generation" value={generation} disabled={locked || importing}
      onChange={e => { setReference(undefined); setGeneration(e.target.value as "981" | "982"); }}><option>981</option><option>982</option></select>
    <select aria-label="来源控制单元" data-testid="source-ecu" value={ecu} disabled={locked || importing || !units.length}
      onChange={e => { setReference(undefined); setProfileId(""); setEcu(e.target.value); }}>
      <option value="">请选择控制单元</option>{units.map(row => <option key={row.ecuId} value={row.ecuId}>{row.label} · {row.ecuId}</option>)}
    </select>
    <select aria-label="来源目录版本" data-testid="source-profile" value={profileId} disabled={locked || importing || !unit}
      onChange={e => { setReference(undefined); setProfileId(e.target.value); }}>
      <option value="">请选择目录版本</option>{unit?.variants.map(row => <option key={row.profileId} value={row.profileId}>{row.name}</option>)}
    </select>
    {reference && <div data-testid="source-reference-result">
      <p>{generation} · 单元 {reference.ecuId} · {reference.variant}</p>
      {!reference.generatorCodeMatchesCurrent && <p role="alert">资料生成版本与当前程序不一致，请核对资料版本；仍只显示来源参考。</p>}
      <p>身份读取 {reference.counts.identityReadGroups} 组 · 实时定义 {reference.counts.measurementDefinitions} 项／{reference.counts.measurementReadGroups} 组 · 设码定义 {reference.counts.codingFields} 项／{reference.counts.codingReadGroups} 组</p>
      {reference.codingPreviewSummary && <p data-testid="source-coding-preview">设码离线预览：枚举 {reference.codingPreviewSummary.namedOptionFields} 项 · 有界数值候选 {reference.codingPreviewSummary.numericCandidateFields} 项 · 待补定义 {reference.codingPreviewSummary.blockedFields} 项。原生编辑及本车可写资格仍待验收。</p>}
      <p>诊断来源：{status(reference.diagnosticBindingStatus)}。版本规则：{status(reference.versionBindingStatus)}。选择字段：{status(reference.selectorBindingStatus)}。</p>
      <ul>{reference.selectorGroups.map((row,index) => <li key={index}>{row.requestHex}：{row.sourceOccurrences} 处来源；{row.postprocessingCompleteOccurrences} 处后处理有静态证据。
        {row.missingReason && `缺项：${status(row.missingReason)}。`}</li>)}</ul>
      <p>本车版本、诊断头通信、实际动态绑定、设码和恢复均待验收。</p>
    </div>}
    {error && <p role="alert" data-testid="source-reference-error">{error}</p>}
  </section>;
}
