import { useState } from "react";
import { WORKSHOP_FUNCTIONS } from "@porsche981/domain";
import {
  CODING_CATEGORIES, CODING_SYSTEMS, WORKSPACE_ENTRIES,
  type CodingCategory,
} from "../coding-workspace";
import { CodingGuidePanel } from "./CodingGuidePanel";
import { CodingMenuDetail } from "./CodingMenuDetail";
import { PiwisWorkshopPage } from "./PiwisWorkshopPage";
import { DiagnosticPreparationPanel } from "../obd/DiagnosticPreparationPanel";
import "../coding-workspace.css";

export function CodingPage({ initialSystemId = "" }: { initialSystemId?: string }) {
  const [systemId, setSystemId] = useState(() => CODING_SYSTEMS.some((s) => s.id === initialSystemId) ? initialSystemId : "");
  const [category, setCategory] = useState<CodingCategory>(initialSystemId ? "coding" : "maintenance");
  const [activeId, setActiveId] = useState("");
  const system = CODING_SYSTEMS.find((s) => s.id === systemId);
  const systems = CODING_SYSTEMS;
  const systemEntries = WORKSPACE_ENTRIES.filter((e) => e.systemIds.includes(systemId));
  const functions = systemEntries.filter((e) => e.category === category);
  const active = functions.find((f) => f.id === activeId) ?? functions[0];
  const workshop = WORKSHOP_FUNCTIONS.find((f) => f.id === active?.workshopId);
  function chooseSystem(id: string) { setSystemId(id); setActiveId(""); }
  return <div className="coding-workspace" data-page="coding">
    <div className="coding-system-workspace">
      <aside className="panel coding-systems" aria-label="系统拓扑清单">
        <div className="coding-system-list">
          {systems.filter((s) => !s.supplemental).map((s) => <button type="button" key={s.id} data-coding-system={s.id} aria-pressed={systemId === s.id}
            className={`coding-system${systemId === s.id ? " active" : ""}`} onClick={() => chooseSystem(s.id)}><strong>{s.short} · {s.label}</strong><span>{s.branchLabel}</span></button>)}
          {systems.some((s) => s.supplemental) && <p className="muted coding-supplement-label">整车与补充资料系统</p>}
          {systems.filter((s) => s.supplemental).map((s) => <button type="button" key={s.id} data-coding-system={s.id} aria-pressed={systemId === s.id}
            className={`coding-system${systemId === s.id ? " active" : ""}`} onClick={() => chooseSystem(s.id)}><strong>{s.label}</strong><span>{s.id === "whole-vehicle" ? "多系统流程" : "来源系统，未纳入拓扑"}</span></button>)}
        </div>
      </aside>
      <section className="coding-system-content" aria-label="系统功能与详情">
        {!system ? <section className="panel coding-system-empty"><h3>请选择系统</h3><p className="muted">从左侧系统拓扑清单选择控制单元，查看其四类功能。</p></section> : <>
          <header className="coding-selected-system"><h3>{system.short} · {system.label}</h3><p className="muted">981 / 982 内容统一展示，来源菜单与具体适用性分别标注。</p></header>
          <nav className="chip-row" aria-label="系统功能类别">
            {CODING_CATEGORIES.map((c) => <button type="button" key={c.id} data-coding-category={c.id} className={`chip${category === c.id ? " active" : ""}`}
              aria-pressed={category === c.id} onClick={() => { setCategory(c.id); setActiveId(""); }}>{c.label}（{systemEntries.filter((e) => e.category === c.id).length}）</button>)}
          </nav>
          {(category === "coding" || category === "programming") && <DiagnosticPreparationPanel key={`${systemId}-${category}`} ecu={systemId} programming={category === "programming"} />}
          {!functions.length && <section className="panel" data-testid="coding-empty-category"><h3>暂无内容</h3>
            <p className="muted">{system.label}的{CODING_CATEGORIES.find((c) => c.id === category)?.label}类别尚无资料。</p></section>}
        </>}
        <div className={system && functions.length ? "coding-functions-layout" : ""}>
          {system && functions.length > 0 && <section className="panel coding-function-list" aria-label="系统功能列表">
            {functions.map((f) => <button type="button" key={f.id} data-coding-function={f.id} data-coding-source-ids={(f.sourceIds ?? [f.id]).join(" ")} aria-pressed={active?.id === f.id}
              className={`coding-function${active?.id === f.id ? " active" : ""}`} onClick={() => setActiveId(f.id)}><strong>{f.name}</strong><span>{f.families}</span><small>{f.source}{f.systemIds.length > 1 ? " · 跨系统完整流程" : ""}</small></button>)}
          </section>}
          <div className="coding-function-detail">
            {active?.menu && <CodingMenuDetail key={`menu-${active.id}`} entry={active.menu} archive={active.archive} />}
            {workshop && <PiwisWorkshopPage key={`workshop-${active?.id}`} section={workshop.section} functionId={workshop.id} embedded combined />}
            {active?.guideId != null && <CodingGuidePanel key={`guide-${active.id}`} focusId={active.guideId} />}
          </div>
        </div>
      </section>
    </div>
  </div>;
}
