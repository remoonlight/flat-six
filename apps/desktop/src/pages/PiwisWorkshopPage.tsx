import { useEffect, useMemo, useState } from "react";
import {
  WORKSHOP_FUNCTIONS, buildWorkshopPreview,
  type RollerPersistence, type WorkshopFlashResult, type WorkshopGeneration,
  type WorkshopPreviewInput, type WorkshopSection,
} from "@porsche981/domain";
import { api, hasDesktopApi } from "../api";
import "../piwis-workshop.css";

const FIELDS: Record<string, string> = { PRODUKTSCHLUESSEL: "产品键", AUSSTATTUNG: "配置码条件", MODELLJAHR: "年款条件（原文）", PTNR: "当前软件号条件", HWTNR: "当前硬件号条件", SWVERSION: "当前软件版本条件" };
const EVIDENCE = { "model-rules": "已找到车系规则依据", "platform-menu": "已找到共享平台菜单", "platform-rules": "已找到共享平台规则，车系待核实", "service-bulletin": "已找到厂方更新资料，条件待核实" };
const ruleTargets = (r: NonNullable<ReturnType<typeof buildWorkshopPreview>["flashRule"]>) => r.kind === "blocked" ? "禁止刷写分支" : r.targets.map((t) => t.softwarePartNumber ?? t.session).join(" / ");

export function PiwisWorkshopPage({ section: selectedSection, embedded = false, functionId, combined = false }: {
  section?: WorkshopSection; embedded?: boolean; functionId?: string; combined?: boolean;
} = {}) {
  const [generation, setGeneration] = useState<WorkshopGeneration>("981");
  const [localSection, setSection] = useState<WorkshopSection>("maintenance");
  const section = selectedSection ?? localSection;
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState("psm-roller");
  const [persistence, setPersistence] = useState<RollerPersistence>("ignition-cycle");
  const [flash, setFlash] = useState<WorkshopFlashResult | null>(null);
  const [ruleId, setRuleId] = useState("");
  const [ruleQuery, setRuleQuery] = useState("");
  const [preview, setPreview] = useState<ReturnType<typeof buildWorkshopPreview> | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!selectedSection) return;
    setQuery(""); setActiveId(selectedSection === "maintenance" ? "psm-roller" : "program-dme");
    setRuleId(""); setRuleQuery(""); setPreview(null); setMessage(""); setError("");
  }, [selectedSection]);

  useEffect(() => {
    let live = true;
    const read = hasDesktopApi() ? api().workshopFlashIndex : undefined;
    if (!read) { setFlash({ status: "missing", index: null }); return; }
    read().then((v) => { if (live) setFlash(v); }).catch(() => { if (live) setFlash({ status: "invalid", index: null }); });
    return () => { live = false; };
  }, []);

  const functions = useMemo(() => WORKSHOP_FUNCTIONS.filter((f) => f.section === section &&
    (!functionId || f.id === functionId) &&
    `${f.name} ${f.german} ${f.ecu} ${f.summary}`.toLowerCase().includes(query.trim().toLowerCase())), [section, query, functionId]);
  const active = functions.find((f) => f.id === activeId) ?? functions[0];
  const rules = useMemo(() => (flash?.index?.rules ?? []).filter((r) => (combined || r.generation === generation) && r.ecu === active?.ecu &&
    `${r.description} ${r.conditions.flatMap((c) => c.values).join(" ")} ${r.currentEcus?.flatMap((e) => e.conditions.flatMap((c) => c.values)).join(" ") ?? ""} ${r.targets.map((t) => `${t.softwarePartNumber ?? ""} ${t.session ?? ""}`).join(" ")}`.toLowerCase().includes(ruleQuery.trim().toLowerCase())), [flash, generation, active?.ecu, ruleQuery, combined]);
  const rule = rules.find((r) => r.id === ruleId);
  const input: WorkshopPreviewInput | null = active ? { functionId: active.id, generation,
    ...(active.id === "psm-roller" ? { rollerPersistence: persistence } : {}),
    ...(rule ? { flashRuleId: rule.id } : {}),
  } : null;
  const currentPreview = input ? buildWorkshopPreview(input, rule) : null;

  function clearPreview() { setPreview(null); setMessage(""); setError(""); }
  function changeSection(next: WorkshopSection) {
    setSection(next); setQuery(""); setActiveId(next === "maintenance" ? "psm-roller" : "program-dme");
    setRuleId(""); setRuleQuery(""); clearPreview();
  }
  async function exportPreview() {
    if (!input || !preview) return;
    const save = hasDesktopApi() ? api().workshopExportPreview : undefined;
    if (!save) { setError("请在桌面应用中保存准备清单。"); return; }
    setBusy(true); setError(""); setMessage("");
    try { const result = await save(input); setMessage(result.saved ? "准备清单已保存，包含适用性待核实状态。" : "已取消保存。"); }
    catch { setError("准备清单保存失败，请核对本机规则数据后重试。"); }
    finally { setBusy(false); }
  }

  return <div className="workshop" data-testid="piwis-workshop">
    {!functionId && <header className="workshop-heading">
      <div><p className="muted">981 / 982 · PIWIS 功能参考</p>{!embedded && <h2>维护与编程</h2>}
        <p>查看维护流程、滚筒模式和控制单元编程条件，生成操作前准备清单。</p></div>
      <label>研究车系<select aria-label="研究车系" value={generation} disabled={busy} onChange={(e) => {
        setGeneration(e.target.value as WorkshopGeneration); setRuleId(""); setRuleQuery(""); clearPreview();
      }}><option value="981">981 · Boxster / Cayman</option><option value="982">982 · 718 Boxster / Cayman</option></select></label>
    </header>}
    <p className="workshop-status" role="status">目录与流程预览 · 目标 ECU 适用性待核实 · 实车执行尚未启用</p>
    {!functionId && <p className="muted">研究车系用于筛选目录，车辆档案与当前连接不随此选择改变。共享目录中的功能仍需匹配实际 ECU 版本。</p>}
    {!embedded && <nav className="chip-row" aria-label="维护与编程分区">
      <button className={`chip${section === "maintenance" ? " active" : ""}`} disabled={busy} onClick={() => changeSection("maintenance")}>维护／特殊功能</button>
      <button className={`chip${section === "programming" ? " active" : ""}`} disabled={busy} onClick={() => changeSection("programming")}>控制单元编程</button>
    </nav>}
    {!functionId && <label className="workshop-search">搜索功能<input type="search" placeholder="名称、德文菜单或控制单元" value={query} disabled={busy} onChange={(e) => { setQuery(e.target.value); setRuleId(""); clearPreview(); }} /></label>}
    <div className={functionId ? "workshop-focused" : "workshop-layout"}>
      {!functionId && <section className="panel workshop-catalog" aria-label="功能目录">
        <p className="muted">{generation} · {functions.length} 项 · 版本适用性待核实</p>
        {!functions.length && <p>没有匹配的功能，请调整搜索条件。</p>}
        {functions.map((fn) => <button key={fn.id} className={`workshop-function${active?.id === fn.id ? " active" : ""}`}
          aria-pressed={active?.id === fn.id} disabled={busy} data-function-id={fn.id} onClick={() => {
            setActiveId(fn.id); setRuleId(""); setRuleQuery(""); clearPreview();
          }}><strong>{fn.name}</strong><span>{fn.ecu} · {fn.german}</span></button>)}
      </section>}
      {active && <section className="panel workshop-detail" data-testid="workshop-detail">
        <span className="workshop-module">{active.ecu} · {combined ? "981 / 982" : generation}</span><h3>{active.name}</h3>
        <p className="muted">{active.german} · {EVIDENCE[active.evidence]}</p>
        <p>{active.summary}</p>
        {combined ? (["981", "982"] as const).map((family) => active.familyNotes?.[family] && <p key={family} className="workshop-status" data-testid="workshop-family-note">{family}：{active.familyNotes[family]}</p>) : active.familyNotes?.[generation] && <p className="workshop-status" data-testid="workshop-family-note">{active.familyNotes[generation]}</p>}
        {combined && <label className="workshop-preparation-family">准备清单车系<select aria-label="准备清单车系" value={generation} disabled={busy} onChange={(e) => {
          setGeneration(e.target.value as WorkshopGeneration); setRuleId(""); clearPreview();
        }}><option value="981">981</option><option value="982">982</option></select><span className="muted">仅指定离线清单，功能目录同时展示两车系；不改变车辆档案或连接。</span></label>}
        {active.id === "psm-roller" && <div className="workshop-roller">
          <p><strong>当前滚筒状态：</strong>未读取</p>
          <label>保持方式<select aria-label="滚筒保持方式" value={persistence} disabled={busy} onChange={(e) => { setPersistence(e.target.value as RollerPersistence); clearPreview(); }}>
            <option value="ignition-cycle">下次点火周期／软件复位／退出客户诊断时结束</option><option value="persistent">跨点火周期保持</option>
          </select></label>
          <p className="muted">保持方式来自 PSM 配置；启用前需核实当前版本支持，结束后需读取并确认退出状态。</p>
          <div className="row"><button disabled>启用滚筒模式</button><button disabled>读取滚筒状态</button><button disabled>退出滚筒模式</button></div>
        </div>}
        {!!active.dependencies.length && <div className="workshop-dependencies"><strong>流程依赖</strong><ul>{active.dependencies.map((s) => <li key={s}>{s}</li>)}</ul></div>}
        {section === "programming" && <div className="workshop-flash" data-testid="workshop-flash">
          <h4>本机刷写规则预览</h4><p>当前 ECU 软件：未读取。所选规则仅用于研究，尚未判定适用于当前车辆。</p>
          {!flash ? <p role="status">正在读取本机规则…</p> : flash.status !== "loaded" ? <p role="status">{flash.status === "invalid" ? "本机规则数据无效，无法展示目标软件。" : "尚未导入本机 PIWIS 规则，可先查看准备流程。"}</p> : <>
            <label>搜索刷写规则<input type="search" aria-label="搜索刷写规则" value={ruleQuery} disabled={busy} placeholder="型号、产品键、配置码、目标软件号" onChange={(e) => { setRuleQuery(e.target.value); setRuleId(""); clearPreview(); }} /></label>
            <label>研究规则（{rules.length} 条）<select aria-label="研究规则" value={rule?.id ?? ""} disabled={busy} onChange={(e) => {
              setRuleId(e.target.value); const selected = rules.find((r) => r.id === e.target.value);
              if (selected) setGeneration(selected.generation); clearPreview();
            }}>
              <option value="">请选择研究规则</option>{rules.map((r) => <option key={r.id} value={r.id}>{combined ? `${r.generation} · ` : ""}{r.kind === "blocked" ? "禁止刷写 · " : r.kind === "dataset" ? "数据集 · " : ""}{r.description}</option>)}
            </select></label>
            {!rules.length && <p>没有匹配的规则，请调整搜索条件。</p>}
            {rule && <div data-testid="workshop-rule"><h4>{rule.description}</h4>
              {rule.kind === "blocked" && <p className="error" data-testid="workshop-no-flash">禁止刷写分支（NO FLASH） · 需要核实是否命中该限制；此分支没有固件目标。</p>}
              {rule.familyEvidence === "shared-platform" && <p className="workshop-status">共享平台规则 · 981／982 具体适用性待核实，不能根据当前筛选推定支持。</p>}
              <dl>{rule.conditions.map((c, i) => <div key={i}><dt>{FIELDS[c.field] ?? c.field}</dt><dd>{c.values.join(" / ") || "原配置未限定"}</dd></div>)}</dl>
              {!!rule.currentEcus?.length && <div data-testid="workshop-current-conditions"><h4>当前 ECU 身份条件</h4>
                {rule.currentEcus.map((e, i) => <div key={i}><strong>{e.logicalLink}</strong><dl>{e.conditions.map((c, j) => <div key={j}><dt>{FIELDS[c.field] ?? c.field}</dt><dd>{c.values.join(" / ")}</dd></div>)}</dl></div>)}
              </div>}
              <p className="muted">条件保留原文；尚未解析配置表达式或转换年款范围。</p>
              {rule.kind !== "blocked" && <ul>{rule.targets.map((t, i) => <li key={i}>{rule.kind === "dataset" ? "目标数据集会话：" : "目标软件号（规则）："}<strong>{rule.kind === "dataset" ? t.session : t.softwarePartNumber}</strong> · {t.logicalLink}{rule.kind !== "dataset" && t.session ? ` · ${t.session}` : ""}</li>)}</ul>}
              <p className="muted">实际刷写还需核实完整容器、授权及关联模块；此单条规则未展开全部流程依赖。</p>
            </div>}
          </>}
        </div>}
        <h4>执行前需要核实</h4><ul>{active.prerequisites.map((s) => <li key={s}>{s}</li>)}</ul>
        <h4>{rule?.kind === "blocked" ? "限制核验流程" : "待验证流程"}</h4><ol>{currentPreview?.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        <div className="row"><button disabled={busy} onClick={() => { if (currentPreview) { setPreview(currentPreview); setError(""); setMessage(""); } }}>生成准备清单</button>
          {section === "programming" && <button disabled>执行控制单元编程</button>}</div>
        <details className="workshop-source"><summary>来源与证据范围</summary>
          <p>{active.source}</p><p>来源：用户本机 PIWIS 安装数据，2026-10-01 只读研究。菜单与规则证据尚未通过独立实车验证。</p>
          {rule && flash?.index?.sources.filter((s) => s.file === rule.source).map((s) => <p key={s.file}>{s.file}<br />SHA-256：{s.sha256}</p>)}
        </details>
      </section>}
    </div>
    {preview && <section className="panel workshop-preview" data-testid="workshop-preview">
      <h3>{preview.generation} · {preview.name} · 准备清单</h3>
      <p>离线草稿 · 未核实车辆身份与适用性 · 不包含可执行请求</p>
      {preview.rollerPersistence && <p>保持方式：{preview.rollerPersistence === "persistent" ? "跨点火周期保持" : "下次点火周期／软件复位／退出客户诊断时结束"}</p>}
      {preview.familyNote && <p>{preview.familyNote}</p>}
      {preview.flashRule && <p>研究规则：{preview.flashRule.description} · {preview.flashRule.kind === "blocked" ? "限制" : preview.flashRule.kind === "dataset" ? "数据集" : "目标软件号"}：{ruleTargets(preview.flashRule)}</p>}
      <ol>{preview.steps.map((s) => <li key={s}>{s}</li>)}</ol>
      {!!preview.dependencies.length && <p>依赖：{preview.dependencies.join("；")}</p>}
      <button disabled={busy} onClick={() => void exportPreview()}>保存准备清单</button>
    </section>}
    {message && <p role="status">{message}</p>}{error && <p className="error" role="alert">{error}</p>}
  </div>;
}
