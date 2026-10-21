import { createRef, useEffect, useRef, useState } from "react";
import { WORKSHOP_FUNCTIONS } from "@porsche981/domain";
import {
  CODING_CATEGORIES, CODING_SYSTEMS, WORKSPACE_ENTRIES, maintenancePurpose,
  type CodingCategory,
} from "../coding-workspace";
import { CodingGuidePanel } from "./CodingGuidePanel";
import { CodingMenuDetail } from "./CodingMenuDetail";
import { PiwisWorkshopPage } from "./PiwisWorkshopPage";
import { DiagnosticPreparationPanel } from "../obd/DiagnosticPreparationPanel";
import { obdFunctionLabel } from "../obd/function-label";
import { CodingWorkspacePanel, type CodingWorkspaceHandle } from "../obd/CodingWorkspacePanel";
import "../coding-workspace.css";

const systems = CODING_SYSTEMS.filter((system) => WORKSPACE_ENTRIES.some((entry) => entry.systemIds.includes(system.id)));

export function CodingPage({ initialSystemId = "", onDraftChange, discardVersion = 0 }: { initialSystemId?: string; onDraftChange?: (dirty: boolean) => void; discardVersion?: number }) {
  const [systemId, setSystemId] = useState(() => systems.some((s) => s.id === initialSystemId) ? initialSystemId : "");
  const [category, setCategory] = useState<CodingCategory>(initialSystemId ? "coding" : "maintenance");
  const [activeId, setActiveId] = useState("");
  const activeSystem = useRef(systemId); activeSystem.current = systemId;
  const [visitedUnits, setVisitedUnits] = useState<string[]>([]);
  const [dirtyByUnit, setDirtyByUnit] = useState<Record<string,boolean>>({});
  const [readyByUnit, setReadyByUnit] = useState<Record<string,{read:boolean;backup:boolean}>>({});
  const [visibleByUnit, setVisibleByUnit] = useState<Record<string,boolean>>({});
  const bindings = useRef(new Map<string,{ref:React.RefObject<CodingWorkspaceHandle | null>;dirty:(value:boolean)=>void;ready:(value:{read:boolean;backup:boolean})=>void;visible:(value:boolean)=>void;loaded:()=>void}>());
  function unitBinding(ecu:string) {
    let value = bindings.current.get(ecu);
    if (!value) {
      value = {ref:createRef<CodingWorkspaceHandle>(),
        dirty:dirty=>setDirtyByUnit(previous=>previous[ecu]===dirty?previous:{...previous,[ecu]:dirty}),
        ready:ready=>setReadyByUnit(previous=>previous[ecu]?.read===ready.read&&previous[ecu]?.backup===ready.backup?previous:{...previous,[ecu]:ready}),
        visible:visible=>setVisibleByUnit(previous=>previous[ecu]===visible?previous:{...previous,[ecu]:visible}),
        loaded:()=>{if(activeSystem.current===ecu){setCategory("coding");setActiveId("");}},
      }; bindings.current.set(ecu,value);
    }
    return value;
  }
  useEffect(()=>{if(systemId)setVisitedUnits(previous=>previous.includes(systemId)?previous:[...previous,systemId]);},[systemId]);
  const codingReady = readyByUnit[systemId] || {read:false,backup:false};
  const liveCodingVisible = visibleByUnit[systemId] === true;
  const [functionSearch, setFunctionSearch] = useState("");
  const draftDirty = dirtyByUnit[systemId] === true;
  const anyDraftDirty = Object.values(dirtyByUnit).some(Boolean);
  const [programmingFlowVisible, setProgrammingFlowVisible] = useState(false);
  const [pendingChange, setPendingChange] = useState<{ system?: string; category?: CodingCategory } | null>(null);
  useEffect(() => { onDraftChange?.(anyDraftDirty); }, [anyDraftDirty, onDraftChange]);
  useEffect(() => { setDirtyByUnit({}); setPendingChange(null); }, [discardVersion]);
  const system = systems.find((s) => s.id === systemId);
  const systemEntries = WORKSPACE_ENTRIES.filter((e) => e.systemIds.includes(systemId));
  const functions = systemEntries.filter((e) => e.category === category);
  const maintenanceView = category === "maintenance" || category === "special";
  const query = maintenanceView ? functionSearch.trim().toLocaleLowerCase("zh-CN") : "";
  const visibleFunctions = functions.filter(f => !query || f.name.toLocaleLowerCase("zh-CN").includes(query));
  const groups = maintenanceView ? [...new Set(visibleFunctions.map(maintenancePurpose))]
    .map(label => ({ label, entries: visibleFunctions.filter(f => maintenancePurpose(f) === label) }))
    : [{ label: "", entries: visibleFunctions }];
  const active = visibleFunctions.find((f) => f.id === activeId);
  const workshop = WORKSHOP_FUNCTIONS.find((f) => f.id === active?.workshopId);
  function applyChange(change: { system?: string; category?: CodingCategory }) {
    bindings.current.get(systemId)?.ref.current?.discard();
    setDirtyByUnit(previous=>({...previous,[systemId]:false}));
    if (change.system !== undefined) setSystemId(change.system);
    if (change.category !== undefined) setCategory(change.category);
    setActiveId(""); setFunctionSearch(""); setPendingChange(null);
  }
  function chooseSystem(id: string) { if (id === systemId) return; if (draftDirty) setPendingChange({ system: id }); else applyChange({ system: id }); }
  useEffect(() => {
    if (!initialSystemId || !systems.some(s => s.id === initialSystemId)) return;
    if (draftDirty) setPendingChange({ system: initialSystemId, category: "coding" });
    else applyChange({ system: initialSystemId, category: "coding" });
  }, [initialSystemId]);
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
        {pendingChange && <section className="panel" role="alert" data-testid="coding-switch-review"><p>尚有未写入的调整，是否丢弃后切换？</p>
          <button type="button" onClick={() => setPendingChange(null)}>继续修改</button>
          <button type="button" onClick={() => applyChange(pendingChange)}>丢弃后切换</button>
        </section>}
        {!system ? <section className="panel coding-system-empty"><h3>请选择系统</h3><p className="muted">从左侧系统拓扑清单选择控制单元，查看其四类功能。</p></section> : <>
          <header className="coding-selected-system"><h3>{system.short} · {system.label}</h3><p className="muted">981 / 982 内容统一展示，来源菜单与具体适用性分别标注。</p></header>
          <nav className="chip-row" aria-label="系统功能类别">
            {CODING_CATEGORIES.map((c) => <button type="button" key={c.id} data-coding-category={c.id} className={`chip${category === c.id ? " active" : ""}`}
              aria-pressed={category === c.id} onClick={() => { if (c.id === category) return; if (draftDirty) setPendingChange({ category: c.id }); else applyChange({ category: c.id }); }}>{c.label}（{systemEntries.filter((e) => e.category === c.id).length}）</button>)}
            <button type="button" className="chip" data-testid="coding-backup-original" onClick={() => bindings.current.get(systemId)?.ref.current?.backup()}>{obdFunctionLabel("备份原车设码", codingReady.backup)}</button>
            <button type="button" className="chip" data-testid="coding-read-original" onClick={() => bindings.current.get(systemId)?.ref.current?.read()}>{obdFunctionLabel("读取原车设码", codingReady.read)}</button>
          </nav>
          {!functions.length && <section className="panel" data-testid="coding-empty-category"><h3>暂无内容</h3>
            <p className="muted">{system.label}的{CODING_CATEGORIES.find((c) => c.id === category)?.label}类别尚无资料。</p></section>}
        </>}
        {[...new Set([...visitedUnits,...(systemId?[systemId]:[])])].map(ecu=>{
          const binding=unitBinding(ecu);
          return <div key={ecu} hidden={systemId!==ecu} data-coding-workspace-ecu={ecu}><CodingWorkspacePanel ref={binding.ref} ecu={ecu} selected={systemId===ecu} active={systemId===ecu&&category === "coding"}
            onDirty={binding.dirty} onReady={binding.ready} onLoaded={binding.loaded} onVisible={binding.visible} discardVersion={discardVersion} /></div>;
        })}
        <div hidden={category === "coding" && liveCodingVisible} className={system && functions.length ? "coding-functions-layout" : ""}>
          {system && functions.length > 0 && <section className="panel coding-function-list" aria-label="系统功能列表">
            {maintenanceView && <label className="coding-function-search">搜索功能
              <input data-testid="maintenance-search" value={functionSearch} maxLength={80} onChange={event => setFunctionSearch(event.target.value)} />
            </label>}
            {groups.map(group => <div key={group.label} data-maintenance-purpose={group.label || undefined}>
              {group.label && <h4 className="coding-purpose-heading">{group.label}</h4>}
              {group.entries.map(f => <button type="button" key={f.id} data-coding-function={f.id} data-coding-source-ids={(f.sourceIds ?? [f.id]).join(" ")} aria-pressed={active?.id === f.id}
                className={`coding-function${active?.id === f.id ? " active" : ""}`} onClick={() => setActiveId(f.id)}><strong>{obdFunctionLabel(f.name, false)}</strong></button>)}
            </div>)}
            {!visibleFunctions.length && <p className="muted" data-testid="maintenance-no-matches">没有符合条件的功能。</p>}
          </section>}
          <div className="coding-function-detail">
            {active && category === "programming" && <DiagnosticPreparationPanel key={`${systemId}-${active.id}`} ecu={systemId} programming onFlowVisible={setProgrammingFlowVisible} />}
            {system && visibleFunctions.length > 0 && !active && <p className="muted" data-testid="coding-function-unselected">请选择功能，查看说明和当前缺项。</p>}
            {!programmingFlowVisible && active?.menu && <CodingMenuDetail key={`menu-${active.id}`} entry={active.menu} archive={active.archive} />}
            {!programmingFlowVisible && workshop && <PiwisWorkshopPage key={`workshop-${active?.id}`} section={workshop.section} functionId={workshop.id} embedded combined />}
            {!programmingFlowVisible && active?.guideId != null && <CodingGuidePanel key={`guide-${active.id}`} focusId={active.guideId} />}
          </div>
        </div>
      </section>
    </div>
  </div>;
}
