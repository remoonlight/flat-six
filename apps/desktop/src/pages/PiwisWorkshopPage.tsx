import { useCallback, useEffect, useMemo, useState } from "react";
import {
  WORKSHOP_FUNCTIONS,
  type RollerPersistence, type WorkshopGeneration, type WorkshopSection,
} from "@porsche981/domain";
import { obdFunctionLabel } from "../obd/function-label";
import { WorkshopExecutionPanel } from "../obd/WorkshopExecutionPanel";
import "../piwis-workshop.css";

const EVIDENCE = { "model-rules": "已找到车系规则依据", "platform-menu": "已找到共享平台菜单", "platform-rules": "已找到共享平台规则，车系待核实", "service-bulletin": "已找到厂方更新资料，条件待核实" };

export function PiwisWorkshopPage({ section: selectedSection, embedded = false, functionId, combined = false }: {
  section?: WorkshopSection; embedded?: boolean; functionId?: string; combined?: boolean;
} = {}) {
  const [generation, setGeneration] = useState<WorkshopGeneration>("981");
  const [localSection, setSection] = useState<WorkshopSection>("maintenance");
  const section = selectedSection ?? localSection;
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState("psm-roller");
  const [persistence, setPersistence] = useState<RollerPersistence>("ignition-cycle");
  const [flowVisible, setFlowVisible] = useState(false);
  const [readyFunctionId, setReadyFunctionId] = useState("");

  useEffect(() => {
    if (!selectedSection) return;
    setQuery(""); setActiveId(selectedSection === "maintenance" ? "psm-roller" : "program-dme");
  }, [selectedSection]);

  const functions = useMemo(() => WORKSHOP_FUNCTIONS.filter((f) => f.section === section &&
    (!functionId || f.id === functionId) &&
    `${f.name} ${f.german} ${f.ecu} ${f.summary}`.toLowerCase().includes(query.trim().toLowerCase())), [section, query, functionId]);
  const active = functions.find((f) => f.id === activeId) ?? functions[0];
  const executionReady = readyFunctionId === active?.id;
  const setExecutionReady = useCallback((ready: boolean) => setReadyFunctionId(ready ? active?.id || "" : ""), [active?.id]);

  function changeSection(next: WorkshopSection) {
    setSection(next); setQuery(""); setActiveId(next === "maintenance" ? "psm-roller" : "program-dme");
  }

  return <div className="workshop" data-testid="piwis-workshop">
    {!functionId && <header className="workshop-heading">
      <div><p className="muted">981 / 982 · PIWIS 功能参考</p>{!embedded && <h2>维护与编程</h2>}
        <p>查看功能用途、操作条件及步骤。</p></div>
      <label>车系<select aria-label="车系" value={generation} onChange={(e) => setGeneration(e.target.value as WorkshopGeneration)}>
        <option value="981">981 · Boxster / Cayman</option><option value="982">982 · 718 Boxster / Cayman</option>
      </select></label>
    </header>}
    {!flowVisible && <p className="workshop-status" role="status">功能说明 · 目标 ECU 适用性待核实 · 车辆执行尚未完成</p>}
    {!embedded && <nav className="chip-row" aria-label="维护与编程分区">
      <button className={`chip${section === "maintenance" ? " active" : ""}`} onClick={() => changeSection("maintenance")}>维护／特殊功能</button>
      <button className={`chip${section === "programming" ? " active" : ""}`} onClick={() => changeSection("programming")}>控制单元编程</button>
    </nav>}
    {!functionId && <label className="workshop-search">搜索功能<input type="search" placeholder="名称、德文菜单或控制单元" value={query} onChange={(e) => setQuery(e.target.value)} /></label>}
    <div className={functionId ? "workshop-focused" : "workshop-layout"}>
      {!functionId && <section className="panel workshop-catalog" aria-label="功能目录">
        <p className="muted">{generation} · {functions.length} 项 · 版本适用性待核实</p>
        {!functions.length && <p>没有匹配的功能，请调整搜索条件。</p>}
        {functions.map((fn) => <button key={fn.id} className={`workshop-function${active?.id === fn.id ? " active" : ""}`}
          aria-pressed={active?.id === fn.id} data-function-id={fn.id} onClick={() => setActiveId(fn.id)}>
          <strong>{obdFunctionLabel(fn.name, false)}</strong><span>{fn.ecu} · {fn.german}</span>
        </button>)}
      </section>}
      {active && <section className="panel workshop-detail" data-testid="workshop-detail">
        <span className="workshop-module">{active.ecu} · {combined ? "981 / 982" : generation}</span><h3>{obdFunctionLabel(active.name, active.section === "maintenance" && executionReady)}</h3>
        {active.section === "maintenance" && <WorkshopExecutionPanel key={`${active.id}:${generation}`} functionId={active.id} generation={generation}
          persistence={active.id === "psm-roller" ? persistence : undefined} onReady={setExecutionReady} onVisibleChange={setFlowVisible} />}
        {!flowVisible && <>
        <p className="muted">{active.german} · {EVIDENCE[active.evidence]}</p>
        <p>{active.summary}</p>
        {combined ? (["981", "982"] as const).map((family) => active.familyNotes?.[family] && <p key={family} className="workshop-status" data-testid="workshop-family-note">{family}：{active.familyNotes[family]}</p>) : active.familyNotes?.[generation] && <p className="workshop-status" data-testid="workshop-family-note">{active.familyNotes[generation]}</p>}
        {active.id === "psm-roller" && <div className="workshop-roller">
          <p><strong>当前滚筒状态：</strong>未读取</p>
          <label>保持方式<select aria-label="滚筒保持方式" value={persistence} onChange={(e) => setPersistence(e.target.value as RollerPersistence)}>
            <option value="ignition-cycle">下次点火周期／软件复位／退出客户诊断时结束</option><option value="persistent">跨点火周期保持</option>
          </select></label>
          <p className="muted">保持方式来自 PSM 配置；启用前需核实当前版本支持，结束后需读取并确认退出状态。</p>
          {!executionReady && <div className="row">{["启用滚筒模式", "读取滚筒状态", "退出滚筒模式"].map(name => <button key={name} disabled>{obdFunctionLabel(name, false)}</button>)}</div>}
        </div>}
        {!!active.dependencies.length && <div className="workshop-dependencies"><strong>流程依赖</strong><ul>{active.dependencies.map((s) => <li key={s}>{s}</li>)}</ul></div>}
        <h4>执行前需要核实</h4><ul>{active.prerequisites.map((s) => <li key={s}>{s}</li>)}</ul>
        <h4>操作步骤</h4><ol>{active.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        <details className="workshop-source"><summary>来源与证据范围</summary>
          <p>{active.source}</p><p>来源：用户本机 PIWIS 安装数据，2026-10-01 只读研究。菜单与规则证据尚未通过独立实车验证。</p>
        </details>
        </>}
      </section>}
    </div>
  </div>;
}
