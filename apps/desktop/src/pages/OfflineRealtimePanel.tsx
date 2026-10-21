import { mainlandUnit } from "../obd/live-unit-logic.mjs";
import { useCallback, useEffect, useRef, useState } from "react";
import { callOfflineDiagnostics } from "../api";
import { ManufacturerRehearsalPanel } from "../obd/ManufacturerRehearsalPanel";
import { obdFunctionLabel } from "../obd/function-label";
import { diagnosticSourceFailure } from "../obd/diagnostic-messages";

type Variant = { profileId: string; name: string; status: string; parameterCount: number; replayParameterCount: number; identityCount: number; identityDecoderCount: number };
type Unit = { systemId: string; ecuId: number; label: string; variants: Variant[] };
type Category = { id: string; label: string; count: number };
type Parameter = { id: string; name: string; nameResolved: boolean; unit: string | null; requestHex: string | null;
  decoderReady: boolean; decoderIssue: string | null; status: string; decodedSampleCount: number; byteOffset: number; bitOffset: number; definitionOffset?: number | null };
import { profileFitsGeneration } from "../obd/manufacturer-profiles.mjs";
export function OfflineRealtimePanel({ systemId, generation = "981", locked, onBusyChange, adapterModel = null, diagnosticConnectionKey = "", autoIdentifyAllowed = false, vehicleIdentified = true, vehicleIdentityPending = false }: { systemId: string; generation?: "981" | "982"; locked: boolean; onBusyChange?: (busy: boolean) => void; adapterModel?: string | null; diagnosticConnectionKey?: string; autoIdentifyAllowed?: boolean; vehicleIdentified?: boolean; vehicleIdentityPending?: boolean }) {
  const availableProfile = (id: string) => profileFitsGeneration(id, generation);
  const [manufacturerBusy, setManufacturerBusy] = useState(false);
  useEffect(() => { onBusyChange?.(manufacturerBusy); }, [manufacturerBusy, onBusyChange]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [present, setPresent] = useState<boolean | null>(null);
  const [profileId, setProfileId] = useState("");
  const [identifiedProfileId, setIdentifiedProfileId] = useState<string | null>(null);
  const matchedProfile = useRef("");
  const [search, setSearch] = useState("");
  const [categories, setCategories] = useState<Category[]>([]);
  const [groupId, setGroupId] = useState("");
  const [offset, setOffset] = useState(0);
  const [parameters, setParameters] = useState<Parameter[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Parameter[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const mounted = useRef(true);
  const unit = units.find((u) => u.systemId === systemId);
  const variant = unit?.variants.find((v) => v.profileId === profileId);
  const versionIdentified = vehicleIdentified && !vehicleIdentityPending && !!identifiedProfileId && identifiedProfileId === profileId && !!variant;

  useEffect(() => {
    mounted.current = true;
    let active = true;
    setPresent(null); setProfileId(""); setIdentifiedProfileId(null); matchedProfile.current = ""; setUnits([]); setSelected([]); resetResults(); setOffset(0); setGroupId("");
    void callOfflineDiagnostics({ action: "ready-units", generation }).then((doc) => {
      if (!active) return;
      if (!doc.ok) { setError(diagnosticSourceFailure(doc.error).message); setPresent(false); return; }
      const list = (doc.units || []) as Unit[];
      setUnits(list); setPresent(Boolean(doc.present));
    }).catch((e) => { if (active) { setPresent(false); setError(diagnosticSourceFailure(e).message); } });
    return () => {
      active = false; mounted.current = false; ++sequence.current;
    };
  }, [systemId, generation]);

  useEffect(() => {
    const id = ++sequence.current;
    setParameters([]); setTotal(0);
    if (!unit || !versionIdentified) { setLoading(false); return; }
    setLoading(true); setError("");
    const timer = setTimeout(() => {
      void callOfflineDiagnostics({ action: "ready-parameters", generation, ecuId: unit.ecuId,
        profileId, search, offset, limit: 40, groupId: groupId || undefined }).then((doc) => {
        if (id !== sequence.current || !mounted.current) return;
        if (!doc.ok) setError(diagnosticSourceFailure(doc.error).message);
        else { setParameters((doc.items || []) as Parameter[]); setTotal(Number(doc.total || 0)); setCategories((doc.categories || []) as Category[]); }
      }).catch((e) => { if (id === sequence.current && mounted.current) setError(diagnosticSourceFailure(e).message); })
        .finally(() => { if (id === sequence.current && mounted.current) setLoading(false); });
    }, 180);
    return () => { clearTimeout(timer); ++sequence.current; };
  }, [unit, profileId, versionIdentified, search, offset, groupId, generation]);

  function resetResults() { setError(""); }
  const onIdentityResult = useCallback((id: string | null) => {
    setIdentifiedProfileId(id);
    if (!id) return;
    setProfileId(id);
    if (matchedProfile.current !== id) { setSelected([]); setSearch(""); setGroupId(""); setOffset(0); }
    matchedProfile.current = id; setError("");
  }, []);
  const disabled = locked || manufacturerBusy;
  const unitHasClient = !!unit?.variants.some(v => availableProfile(v.profileId));
  const identityRequired = !!diagnosticConnectionKey && unitHasClient && (adapterModel === null || ["vLinker", "OBDLink MX+", "ELM327", "OBDKey", "OBDLink", "VNCI"].includes(adapterModel));
  const identityPending = !versionIdentified;
  const profileImplemented = availableProfile(profileId);
  const liveImplemented = profileImplemented && (adapterModel === null || ["vLinker", "OBDLink MX+", "ELM327", "OBDKey", "OBDLink", "VNCI"].includes(adapterModel));
  return <div className="offline-realtime" data-testid="offline-realtime" data-profile-id={versionIdentified ? profileId : undefined}>
    {present === null ? <p className="muted">正在读取本地清单…</p> : !present ? <p className="muted">本机尚未准备参数目录。</p> : !unit ? <p className="muted">此控制单元尚无可用参数目录。</p> : <>
      {versionIdentified && variant ? <>
        {!profileImplemented ? <p className="muted" data-testid="offline-version-unmatched">厂商实时读取(未完成)：当前版本的车辆读取尚未接通。</p> : null}
        <div data-testid="offline-selection">
        <div className="eng-section-head offline-filter-row">
          <label>分类 <select data-testid="offline-category" value={groupId} disabled={disabled || loading} onChange={(e) => { resetResults(); setGroupId(e.target.value); setOffset(0); }}>
            <option value="">全部数据（{variant.parameterCount}）</option>{categories.filter((c) => c.count > 0 && c.label !== "VIRTUAL_CURRENTDATA").map((c) => <option key={c.id} value={c.id}>{c.label}（{c.count}）</option>)}
          </select></label>
          <label>搜索参数 <input data-testid="offline-search" value={search} disabled={disabled} onChange={(e) => { resetResults(); setSearch(e.target.value.slice(0, 80)); setOffset(0); }} /></label><span data-testid="offline-selection-count">已选 {selected.length} / 12 · 共 {total} 项</span>
        </div>
        <div className="eng-parameters" data-testid="offline-parameters" aria-busy={loading}>
          {parameters.map((p) => <label key={p.id} className="eng-parameter" title={p.nameResolved ? p.name : "参数名称尚未核实，暂不能采集。"}>
            <input type="checkbox" data-testid={`offline-select-${p.id}`} checked={selected.some((s) => s.id === p.id)} disabled={disabled || identityPending || !liveImplemented || !p.nameResolved || !p.decoderReady || (!selected.some((s) => s.id === p.id) && selected.length >= 12)}
              onChange={(e) => { resetResults(); setSelected(e.target.checked ? [...selected, p].sort((a, b) =>
                (a.definitionOffset ?? Number.MAX_SAFE_INTEGER) - (b.definitionOffset ?? Number.MAX_SAFE_INTEGER) || a.id.localeCompare(b.id))
                : selected.filter((s) => s.id !== p.id)); }} />
            <span className="offline-parameter-name">{obdFunctionLabel(p.nameResolved ? p.name : "名称待核实", liveImplemented && p.nameResolved && p.decoderReady && !!p.requestHex)}</span><small className="offline-parameter-unit">{mainlandUnit(p.unit).unit || "—"}</small>
          </label>)}
        </div>
        {loading ? <p className="muted">正在载入参数…</p> : !parameters.length ? <p className="muted">没有符合条件的参数。</p> : null}
        <div className="eng-selection-actions"><button disabled={disabled || loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 40))}>上一页</button><span>{total ? offset + 1 : 0}–{Math.min(offset + 40, total)} / {total}</span><button disabled={disabled || loading || offset + 40 >= total} onClick={() => setOffset(offset + 40)}>下一页</button></div>
        {selected.length ? <div className="offline-selected" data-testid="offline-selected">{selected.map((p) => <button key={p.id} disabled={disabled} onClick={() => { resetResults(); setSelected(selected.filter((s) => s.id !== p.id)); }}>{p.name} ×</button>)}</div> : null}
        </div>
      </> : null}
        {(unitHasClient || identityRequired) &&
          <ManufacturerRehearsalPanel generation={generation} profileId={profileId} parameters={selected} canReadVehicle={vehicleIdentified && versionIdentified}
            locked={locked} onBusyChange={setManufacturerBusy} liveImplemented={liveImplemented}
            identityEcuId={unit.ecuId} identityConnectionKey={identityRequired ? `${generation}:${diagnosticConnectionKey}` : ""} canIdentify={autoIdentifyAllowed} identifiedProfileId={identifiedProfileId} onIdentityResult={onIdentityResult} />}

      {!versionIdentified && !manufacturerBusy ? <p className="muted" data-testid="offline-identity-state">识别控制单元版本后显示可选参数。</p> : null}
    </>}
    {error ? <p role="alert" data-testid="offline-error">{error}</p> : null}
  </div>;
}
