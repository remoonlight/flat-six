import { useEffect, useRef, useState } from "react";
import { api, callOfflineDiagnostics, type OfflineDiagnosticsRequest, type OfflineDiagnosticsResult } from "../api";

type Variant = { profileId: string; name: string; status: string; parameterCount: number; replayParameterCount: number; identityCount: number; identityDecoderCount: number };
type Unit = { systemId: string; ecuId: number; label: string; variants: Variant[] };
type Category = { id: string; label: string; count: number };
type Parameter = { id: string; name: string; nameResolved: boolean; unit: string | null; requestHex: string | null;
  decoderReady: boolean; decoderIssue: string | null; status: string; decodedSampleCount: number; byteOffset: number; bitOffset: number };

export function OfflineRealtimePanel({ systemId, locked }: { systemId: string; locked: boolean }) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [present, setPresent] = useState<boolean | null>(null);
  const [profileId, setProfileId] = useState("");
  const [search, setSearch] = useState("");
  const [categories, setCategories] = useState<Category[]>([]);
  const [groupId, setGroupId] = useState("");
  const [offset, setOffset] = useState(0);
  const [parameters, setParameters] = useState<Parameter[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<Parameter[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<OfflineDiagnosticsResult | null>(null);
  const [startedAt, setStartedAt] = useState("");
  const [finishedAt, setFinishedAt] = useState("");
  const [stopped, setStopped] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");
  const sequence = useRef(0);
  const opSequence = useRef(0);
  const mounted = useRef(true);
  const operation = useRef<string | null>(null);
  const unit = units.find((u) => u.systemId === systemId);
  const variant = unit?.variants.find((v) => v.profileId === profileId);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    void callOfflineDiagnostics({ action: "ready-units", generation: "981" }).then((doc) => {
      if (!active) return;
      if (!doc.ok) { setError(doc.error || "离线清单读取失败"); setPresent(false); return; }
      const list = (doc.units || []) as Unit[];
      setUnits(list); setPresent(Boolean(doc.present));
      const found = list.find((u) => u.systemId === systemId);
      const matched = found?.variants.filter((v) => v.status === "identity-matched" && v.parameterCount > 0) || [];
      setProfileId(matched.length === 1 ? matched[0].profileId : "");
    }).catch((e) => { if (active) { setPresent(false); setError(String(e)); } });
    return () => {
      active = false; mounted.current = false; ++sequence.current; ++opSequence.current;
      if (operation.current) void api().cancelOfflineDiagnostics?.(operation.current).catch(() => {});
    };
  }, [systemId]);

  useEffect(() => {
    const id = ++sequence.current;
    setParameters([]); setTotal(0);
    if (!unit || !profileId) { setLoading(false); return; }
    setLoading(true); setError("");
    const timer = setTimeout(() => {
      void callOfflineDiagnostics({ action: "ready-parameters", generation: "981", ecuId: unit.ecuId,
        profileId, search, offset, limit: 40, groupId: groupId || undefined }).then((doc) => {
        if (id !== sequence.current || !mounted.current) return;
        if (!doc.ok) setError(doc.error || "参数读取失败");
        else { setParameters((doc.items || []) as Parameter[]); setTotal(Number(doc.total || 0)); setCategories((doc.categories || []) as Category[]); }
      }).catch((e) => { if (id === sequence.current && mounted.current) setError(String(e)); })
        .finally(() => { if (id === sequence.current && mounted.current) setLoading(false); });
    }, 180);
    return () => { clearTimeout(timer); ++sequence.current; };
  }, [unit, profileId, search, offset, groupId]);

  function resetResults() {
    ++opSequence.current; setPlan(null); setError(""); setStartedAt(""); setFinishedAt("");
    setStopped(false); setSavedMessage("");
  }
  async function start() {
    if (!unit || !profileId || !selected.length || operation.current || busy || locked || loading || saving) return;
    const id = ++opSequence.current;
    const operationId = crypto.randomUUID(); operation.current = operationId;
    setBusy(true); setError(""); setPlan(null); setStopped(false); setSavedMessage("");
    setStartedAt(new Date().toISOString()); setFinishedAt("");
    const req: OfflineDiagnosticsRequest = { action: "ready-plan", generation: "981", ecuId: unit.ecuId, profileId, parameterIds: selected.map((p) => p.id) };
    try {
      const doc = await callOfflineDiagnostics(req, operationId);
      if (!mounted.current || id !== opSequence.current) return;
      if (doc.error === "cancelled") setStopped(true);
      else if (!doc.ok) setError(doc.error || "离线处理失败");
      else setPlan(doc);
    } catch (e) { if (mounted.current && id === opSequence.current) setError(String(e)); }
    finally {
      if (operation.current === operationId) operation.current = null;
      if (mounted.current && id === opSequence.current) { setBusy(false); setStopping(false); setFinishedAt(new Date().toISOString()); }
    }
  }
  async function stop() {
    if (!operation.current || stopping) return;
    setStopping(true);
    try {
      const result = await api().cancelOfflineDiagnostics?.(operation.current);
      if (result && !result.ok) throw new Error(result.error || "停止失败");
    } catch (e) { if (mounted.current) { setError(String(e)); setStopping(false); } }
  }
  async function save() {
    if (!plan || busy || saving || locked || !unit || !variant) return;
    const saveFile = api().saveDiagnosticRecording;
    if (!saveFile) { setError("需要桌面端另存为功能。"); return; }
    const id = opSequence.current; setSaving(true); setSavedMessage(""); setError("");
    try {
      const result = await saveFile({ fileName: `obd-${systemId}-${startedAt.replace(/[^0-9]/g, "")}.json`,
        recording: { kind: "x431-offline-plan", vehicleDataCollected: false, startedAt, finishedAt,
          systemId, ecuId: unit.ecuId, profileId, version: variant.name, parameters: selected, result: plan } });
      if (!mounted.current || id !== opSequence.current) return;
      if (!result.ok) setError(result.error || "保存失败");
      else if (result.saved) setSavedMessage(`已保存：${result.filePath}`);
    } catch (e) { if (mounted.current && id === opSequence.current) setError(String(e)); }
    finally { if (mounted.current) setSaving(false); }
  }
  const disabled = locked || busy || saving;
  const planBody = plan?.plan as { requestCountPerCycle: number; groups: { requestHex: string; parameterIds: string[]; minimumDataBytes: number; spanKind: string }[]; blocked: unknown[] } | undefined;
  return <div className="offline-realtime" data-testid="offline-realtime">
    {present === null ? <p className="muted">正在读取本地清单…</p> : !present ? <p className="muted">本机尚未生成离线数据清单。</p> : !unit ? <p className="muted">此节点没有独立的 X431 参数定义。</p> : <>
      {variant ? <>
        <p className="muted" data-testid="offline-version">{variant.name}</p>
        <div className="eng-section-head offline-filter-row">
          <label>分类 <select data-testid="offline-category" value={groupId} disabled={disabled || loading} onChange={(e) => { setGroupId(e.target.value); setOffset(0); }}>
            <option value="">全部数据（{variant.parameterCount}）</option>{categories.filter((c) => c.count > 0 && c.label !== "VIRTUAL_CURRENTDATA").map((c) => <option key={c.id} value={c.id}>{c.label}（{c.count}）</option>)}
          </select></label>
          <label>搜索参数 <input data-testid="offline-search" value={search} disabled={disabled} onChange={(e) => { setSearch(e.target.value.slice(0, 80)); setOffset(0); }} /></label><span data-testid="offline-selection-count">已选 {selected.length} / 12 · 共 {total} 项</span>
        </div>
        <div className="eng-parameters" data-testid="offline-parameters" aria-busy={loading}>
          {parameters.map((p) => <label key={p.id} className="eng-parameter" title={`${p.name} · ${p.requestHex || "请求未解析"} · 字节 ${p.byteOffset} / 位 ${p.bitOffset}`}>
            <input type="checkbox" data-testid={`offline-select-${p.id}`} checked={selected.some((s) => s.id === p.id)} disabled={disabled || !p.decoderReady || (!selected.some((s) => s.id === p.id) && selected.length >= 12)}
              onChange={(e) => { resetResults(); setSelected(e.target.checked ? [...selected, p] : selected.filter((s) => s.id !== p.id)); }} />
            <span className="offline-parameter-name">{p.name}</span><small className="offline-parameter-unit">{p.unit || "—"}</small>
            <small className="offline-parameter-status">{p.decoderReady ? p.decodedSampleCount ? "有历史响应" : "待采集响应" : "定义待补"}</small>
          </label>)}
        </div>
        {loading ? <p className="muted">正在载入参数…</p> : !parameters.length ? <p className="muted">没有符合条件的参数。</p> : null}
        <div className="eng-selection-actions"><button disabled={disabled || loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 40))}>上一页</button><span>{total ? offset + 1 : 0}–{Math.min(offset + 40, total)} / {total}</span><button disabled={disabled || loading || offset + 40 >= total} onClick={() => setOffset(offset + 40)}>下一页</button></div>
        {selected.length ? <div className="offline-selected" data-testid="offline-selected">{selected.map((p) => <button key={p.id} disabled={disabled} onClick={() => { resetResults(); setSelected(selected.filter((s) => s.id !== p.id)); }}>{p.name} ×</button>)}</div> : null}
        <div className="eng-selection-actions">
          <button data-testid="offline-plan" disabled={disabled || loading || !selected.length} onClick={() => void start()}>数据采集</button>
          {startedAt ? <>
            <button data-testid="offline-stop" disabled={!busy || stopping} onClick={() => void stop()}>{stopping ? "正在停止…" : "停止"}</button>
            <button data-testid="offline-save" disabled={disabled || !plan} onClick={() => void save()}>{saving ? "正在保存…" : "保存此次采集"}</button>
          </> : null}
        </div>
        {startedAt ? <p className="muted" data-testid="offline-capture-state">{busy ? "正在准备读取计划…" : stopped ? "已停止。" : plan ? "读取计划已准备；尚未采集实车数据。" : "未完成读取计划。"}</p> : null}
        {savedMessage ? <p role="status" data-testid="offline-saved">{savedMessage}</p> : null}
        {planBody ? <div data-testid="offline-plan-result"><p>已选 {selected.length} 项，合并为每轮 {planBody.requestCountPerCycle} 个读取请求。</p><ul>{planBody.groups.map((g) => <li key={g.requestHex}>{g.requestHex} · {g.parameterIds.length} 项 · 至少 {g.minimumDataBytes} 字节{g.spanKind !== "exact" ? "（长度下限）" : ""}</li>)}</ul><p className="muted">计划已包含身份读取清单；车辆读取和周期需要验收。</p></div> : null}
      </> : <p className="muted" data-testid="offline-version-unmatched">尚未匹配到本车的唯一版本。</p>}
    </>}
    {error ? <p role="alert" data-testid="offline-error">{error}</p> : null}
  </div>;
}
