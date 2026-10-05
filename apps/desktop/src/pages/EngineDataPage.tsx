import { useEffect, useRef, useState } from "react";
import {
  api,
  engineSessionFixtureEnabled,
  hasDesktopApi,
  type ReadOnlySessionRequest,
  type ReadOnlySessionResult,
} from "../api";
import {
  afterStartJobAssigned,
  afterStatusIpcFailure,
  buildEnginePrepareRequest,
  buildEngineStartRequest,
  canOperate,
  clampEngineOptions,
  ENGINE_PID_META,
  LIVE_DATA_SELECTION_LIMIT,
  engineFreshness,
  enginePlanView,
  formatEngineValue,
  formatSampleAge,
  interpretStatusDoc,
  latestCycleOf,
  liveReady,
  pickEngineView,
  sampleCurrency,
} from "../read-only-session-logic.mjs";
import { topologySeed } from "../can-topology-data";
import { combinedGeneration, flattenNodes } from "../can-topology-logic.mjs";
import "../engine-session.css";
import { OfflineRealtimePanel } from "./OfflineRealtimePanel";
import { createEngineAcquisition } from "../engine-acquisition.mjs";
import { DiagnosticCanRecordingControls } from "../obd/DiagnosticCanRecordingControls";

type Mode = "simulation" | "live";
type Scenario =
  | "success"
  | "identity-mismatch"
  | "negative"
  | "pending-timeout"
  | "disconnect"
  | "slow";

const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: "success", label: "成功" },
  { id: "identity-mismatch", label: "身份不匹配" },
  { id: "negative", label: "否定响应" },
  { id: "pending-timeout", label: "等待超时" },
  { id: "disconnect", label: "断开" },
  { id: "slow", label: "缓慢（用于取消）" },
];

type Sample = {
  pid: string;
  label?: string;
  value?: number;
  unit?: string;
  capturedUtc?: string;
  elapsedMs?: number;
  cycle?: number;
  synthetic?: boolean;
};

function latestByPid(samples: Sample[] | undefined) {
  const map = new Map<string, Sample>();
  for (const s of samples || []) {
    if (!s?.pid) continue;
    const prev = map.get(s.pid);
    if (!prev || (s.cycle || 0) >= (prev.cycle || 0)) map.set(s.pid, s);
  }
  return map;
}

const CONTROL_UNITS = [...flattenNodes(combinedGeneration(topologySeed())),
  { id: "ecu-75", short: "TV", label: "电视调谐器" },
  { id: "ecu-65", short: "AMP", label: "外部放大器" },
  { id: "ecu-165", short: "ERA", label: "紧急呼叫系统" },
] as Array<{ id: string; short: string; label: string }>;
const PID_IDS = Object.keys(ENGINE_PID_META).sort((a, b) => parseInt(a, 16) - parseInt(b, 16));
type DisplayMode = "text" | "graph" | "both";

function ParameterGraph({ pid, samples, unavailable }: { pid: string; samples: Sample[]; unavailable: boolean }) {
  const meta = ENGINE_PID_META[pid as keyof typeof ENGINE_PID_META];
  const points = samples.filter((s) => s.pid === pid && typeof s.value === "number" && Number.isFinite(s.value)
    && typeof s.elapsedMs === "number" && Number.isFinite(s.elapsedMs)).sort((a, b) => a.elapsedMs! - b.elapsedMs!);
  if (unavailable || !points.length) return <p className="muted eng-chart-empty">{unavailable ? "曲线已中断" : "等待采样"}</p>;
  const values = points.map((s) => s.value!);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const padding = high === low ? Math.max(1, Math.abs(high) * .05) : (high - low) * .1;
  const min = low - padding;
  const max = high + padding;
  const start = points[0].elapsedMs!;
  const duration = (points[points.length - 1].elapsedMs! - start) / 1000;
  const coords = points.map((s) => ({ x: 76 + (duration ? (s.elapsedMs! - start) / (duration * 1000) * 374 : 0),
    y: 142 - (s.value! - min) / (max - min) * 120 }));
  return <svg className="eng-chart" viewBox="0 0 480 180" role="img" aria-label={`${meta.label}随时间变化，单位${meta.unit}`} data-testid={`eng-chart-${pid}`}>
    {[0, .5, 1].map((fraction) => <g key={fraction}>
      <line x1="76" x2="450" y1={142 - fraction * 120} y2={142 - fraction * 120} className="eng-chart-grid" />
      <text x="68" y={146 - fraction * 120} textAnchor="end">{formatEngineValue(min + fraction * (max - min), "")}</text>
    </g>)}
    <polyline className="eng-chart-line" points={coords.map((p) => `${p.x},${p.y}`).join(" ")} />
    {coords.map((p, i) => <circle key={i} cx={p.x} cy={p.y} r="3" className="eng-chart-point"><title>{formatEngineValue(points[i].value, meta.unit)} · {points[i].capturedUtc}</title></circle>)}
    <text x="76" y="169">0 s</text><text x="450" y="169" textAnchor="end">{duration.toFixed(1)} s</text>
    <text x="76" y="12">{meta.unit}</text>
  </svg>;
}

export function EngineDataPage({
  onBusyChange,
  peerBusy = false,
  initialSystemId = "",
}: {
  onBusyChange?: (busy: boolean) => void;
  peerBusy?: boolean;
  initialSystemId?: string;
}) {
  const desktop = hasDesktopApi() || engineSessionFixtureEnabled();
  const jobIdRef = useRef<string | null>(null);
  const startLock = useRef(false);
  const mountedRef = useRef(true);
  const pendingCancelRef = useRef(false);
  const continuousRef = useRef(false);
  const acquisition = useRef(createEngineAcquisition());
  const [continuous, setContinuous] = useState(true);
  const [continuousActive, setContinuousActive] = useState(false);
  const [collected, setCollected] = useState<Sample[]>([]);
  const jobModeRef = useRef<Mode>("simulation");
  const [mode, setMode] = useState<Mode>("simulation");
  const [scenario, setScenario] = useState<Scenario>("success");
  const [x431Off, setX431Off] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [sampleCycles, setSampleCycles] = useState(5);
  const [intervalMs, setIntervalMs] = useState(1000);
  const [planDoc, setPlanDoc] = useState<ReadOnlySessionResult | null>(null);
  const [status, setStatus] = useState<ReadOnlySessionResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyPlan, setBusyPlan] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");
  const [systemId, setSystemId] = useState(() => CONTROL_UNITS.some((s) => s.id === initialSystemId) ? initialSystemId : "");
  const [selectedPids, setSelectedPids] = useState<string[]>([]);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("both");
  const [dataSource, setDataSource] = useState("standard");
  const [manufacturerBusy, setManufacturerBusy] = useState(false);
  const system = CONTROL_UNITS.find((s) => s.id === systemId);
  const availablePids = systemId === "dme" ? PID_IDS : [];

  const running = continuousActive || status?.state === "running" || status?.state === "cancelling" || starting || Boolean(jobId);
  const locked = running || peerBusy || saving || manufacturerBusy;
  const liveOk = liveReady(x431Off, readOnly);
  const ops = canOperate(mode, locked || busyPlan || systemId !== "dme" || !selectedPids.length, null, null, null, liveOk);

  function resetSelection() {
    acquisition.current = createEngineAcquisition(); setCollected([]);
    setPlanDoc(null);
    setStatus(null);
    setError(null);
    setSavedMessage("");
  }

  function selectParameters(pids: string[]) {
    if (locked || busyPlan || pids.length > LIVE_DATA_SELECTION_LIMIT) return;
    setSelectedPids(PID_IDS.filter((pid) => pids.includes(pid)));
    resetSelection();
  }

  useEffect(() => {
    onBusyChange?.(running || manufacturerBusy);
  }, [running, manufacturerBusy, onBusyChange]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      continuousRef.current = false;
      const id = jobIdRef.current;
      if (id) void api().readOnlySession?.({ action: "cancel", jobId: id }).catch(() => {});
    };
  }, []);

  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const token = jobId;
    const tick = async () => {
      if (stop) return;
      const fn = api().readOnlySession;
      if (!fn) return;
      try {
        const doc = await fn({ action: "status", jobId: token });
        if (stop) return;
        const interpreted = await interpretStatusDoc(doc, token, fn);
        if (jobIdRef.current !== token && !interpreted.stop) return;
        if (!mountedRef.current) {
          if (interpreted.stop) {
            jobIdRef.current = null;
            startLock.current = false;
          }
          return;
        }
        setStatus(interpreted.doc || doc);
        if (interpreted.error) setError(interpreted.error);
        if (interpreted.stop) {
          const final = interpreted.doc?.final || doc.final;
          if (final) {
            acquisition.current.append(final);
            setCollected([...acquisition.current.samples]);
          }
          jobIdRef.current = null;
          pendingCancelRef.current = false;
          setJobId(null);
          startLock.current = false;
          setStarting(false);
          const fitsNext = acquisition.current.canFit(selectedPids.length * clampEngineOptions(sampleCycles, intervalMs).sampleCycles);
          if (continuousRef.current && !pendingCancelRef.current && (interpreted.doc?.state || doc.state) === "completed" && final?.ok && fitsNext) {
            setTimeout(() => { if (mountedRef.current && continuousRef.current) void start(true); }, 100);
          } else {
            continuousRef.current = false; setContinuousActive(false);
            if (!fitsNext) setSavedMessage(`本批已保留 ${acquisition.current.count} 条样本；样本数或文件大小上限不足以容纳下一完整小批次，已停止。可保存结果，再开始下一批。`);
          }
          return;
        }
      } catch (e) {
        if (stop) return;
        await afterStatusIpcFailure(token, fn);
        if (!mountedRef.current) {
          jobIdRef.current = null;
          startLock.current = false;
          return;
        }
        setError(String(e));
        continuousRef.current = false; setContinuousActive(false);
        jobIdRef.current = null;
        pendingCancelRef.current = false;
        setJobId(null);
        startLock.current = false;
        setStarting(false);
        setStatus(null);
        return;
      }
      if (!stop) setTimeout(tick, 400);
    };
    void tick();
    return () => {
      stop = true;
    };
  }, [jobId]);

  async function loadPlan() {
    if (locked || busyPlan || systemId !== "dme" || !selectedPids.length) return;
    setError(null);
    setBusyPlan(true);
    try {
      const doc = await api().readOnlySession!(buildEnginePrepareRequest({ sampleCycles, intervalMs, selectedPids }));
      if (!mountedRef.current) return;
      setPlanDoc(doc);
      if (!doc.ok) setError(doc.error || "计划失败");
    } catch (e) {
      if (mountedRef.current) setError(String(e));
    } finally {
      setBusyPlan(false);
    }
  }

  async function start(continuation = false) {
    if (startLock.current || jobIdRef.current || peerBusy || busyPlan || systemId !== "dme" || !selectedPids.length) return;
    startLock.current = true;
    if (!continuation) { acquisition.current = createEngineAcquisition(); setCollected([]); continuousRef.current = continuous; }
    setContinuousActive(continuousRef.current);
    pendingCancelRef.current = false;
    jobModeRef.current = mode;
    setStarting(true);
    setError(null);
    setStatus(null);
    const fn = api().readOnlySession;
    if (!fn) {
      startLock.current = false;
      setStarting(false);
      continuousRef.current = false; setContinuousActive(false);
      return;
    }
    try {
      const opts = clampEngineOptions(sampleCycles, intervalMs);
      const req = buildEngineStartRequest({
        mode,
        scenario,
        confirmedReadOnly: readOnly,
        x431Inactive: x431Off,
        sampleCycles: opts.sampleCycles,
        intervalMs: opts.intervalMs,
        selectedPids,
      });
      if ("error" in req && req.error) {
        setError(req.error);
        startLock.current = false;
        setStarting(false);
        continuousRef.current = false; setContinuousActive(false);
        return;
      }
      const doc = await fn(req as ReadOnlySessionRequest);
      const next = afterStartJobAssigned(doc.jobId, pendingCancelRef.current, mountedRef.current);
      if (next.action === "fail" || !doc.ok) {
        if (mountedRef.current) {
          setError(doc.error || "无法开始");
          startLock.current = false;
          setStarting(false);
          continuousRef.current = false; setContinuousActive(false);
        }
        return;
      }
      jobIdRef.current = next.jobId!;
      if (next.action === "cancel-unmount") {
        await fn({ action: "cancel", jobId: next.jobId }).catch(() => {});
        return;
      }
      if (next.action === "cancel-pending") {
        setStatus({ ...doc, state: "cancelling", final: null, latest: null });
        setJobId(next.jobId!);
        await fn({ action: "cancel", jobId: next.jobId }).catch((e) => setError(String(e)));
        return;
      }
      setStatus({ ...doc, state: "running", final: null, latest: null });
      setJobId(next.jobId!);
    } catch (e) {
      if (mountedRef.current) {
        setError(String(e));
        startLock.current = false;
        setStarting(false);
        continuousRef.current = false; setContinuousActive(false);
      }
    }
  }

  async function cancel() {
    continuousRef.current = false; setContinuousActive(false);
    pendingCancelRef.current = true;
    const id = jobIdRef.current;
    if (!id) return;
    try {
      await api().readOnlySession?.({ action: "cancel", jobId: id });
    } catch (e) {
      setError(String(e));
    }
  }

  async function exportJson() {
    const payload = acquisition.current.count ? acquisition.current.export() : status?.final;
    if (!payload || running || saving) return;
    const saveFile = api().saveDiagnosticRecording;
    if (!saveFile) { setError("需要桌面端另存为功能。"); return; }
    setSaving(true); setSavedMessage(""); setError(null);
    const name = "runId" in payload && typeof payload.runId === "string" ? payload.runId : "engine-session";
    try {
      const result = await saveFile({ fileName: `${name.replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 120)}.json`, recording: payload });
      if (!mountedRef.current) return;
      if (!result.ok) setError(result.error || "保存失败");
      else if (result.saved) setSavedMessage(`已保存：${result.filePath}`);
    } catch (e) { if (mountedRef.current) setError(String(e)); }
    finally { if (mountedRef.current) setSaving(false); }
  }

  if (!desktop) {
    return (
      <section className="panel" data-page="engine-session">
        <h2>实时数据</h2>
        <p className="callout">需要桌面端。浏览器不会连接车辆。</p>
      </section>
    );
  }

  const view = pickEngineView(status);
  const currentEngine = view.engine as {
    supportedPids?: string[];
    unsupportedPids?: string[];
    samples?: Sample[];
    completedCycles?: number;
    sampleCycles?: number;
    intervalMs?: number;
  } | null;
  const offset = collected.length ? Math.max(...collected.map((sample) => sample.cycle || 0)) : 0;
  const firstTime = collected.length ? Date.parse(collected[0].capturedUtc || "") : null;
  const currentSamples = acquisition.current.hasRun(status?.final?.runId) ? [] : (currentEngine?.samples || []).map((sample) => ({ ...sample,
    cycle: (sample.cycle || 0) + offset, elapsedMs: firstTime !== null ? Date.parse(sample.capturedUtc || "") - firstTime : sample.elapsedMs }));
  const engine = currentEngine ? { ...currentEngine, samples: [...collected, ...currentSamples] } : collected.length ? { samples: collected, completedCycles: undefined, sampleCycles: undefined, supportedPids: undefined, unsupportedPids: undefined } : null;
  const freshness = engineFreshness(status?.state, status?.final, status?.error || error, jobModeRef.current);
  const liveDisplay = freshness === "sampling";
  const byPid = latestByPid(engine?.samples);
  const plan = planDoc?.plan && typeof planDoc.plan === "object" ? (planDoc.plan as Record<string, unknown>) : null;
  const planView = enginePlanView(plan);
  const nowMs = Date.now();
  const latestCycle = latestCycleOf(engine?.samples);
  const waiting =
    (freshness === "sampling" || freshness === "sampling-sim") &&
    Number.isInteger(engine?.completedCycles) &&
    Number.isInteger(engine?.sampleCycles) &&
    (engine?.completedCycles || 0) < (engine?.sampleCycles || 0);
  const progress = status?.latest?.type === "progress" ? status.latest : null;
  const stateLabel: Record<string, string> = {
    running: "采样中",
    cancelling: "正在取消",
    completed: "已完成",
    cancelled: "已取消",
    failed: "未完成",
    identity: "核对身份",
    engine: "读取参数",
  };
  const progressText = progress
    ? `${stateLabel[String(progress.stage)] || "进行中"} · 步骤 ${String(progress.completed)} / ${String(progress.total)}`
    : stateLabel[status?.state || ""] || (starting ? "正在启动…" : running ? "采样中…" : "尚未开始");
  const kindText =
    freshness === "sampling"
      ? "正在实车采样（非缓存回放）"
      : freshness === "sampling-sim"
        ? "正在模拟采样，不是实车数据"
        : freshness === "cancelling"
          ? "正在取消，数值已中断"
          : freshness === "simulated"
            ? "模拟结果，不是实车已验证读取"
            : freshness === "historical"
              ? "历史结果，不是正在更新的实车数据"
              : freshness === "stale"
                ? "已失效或已中断，不是实时数据"
                : "尚无结果";

  return (
    <div className="eng" data-page="engine-session">
      <section className="panel">
        <label className="eng-unit-select">
          <select aria-label="控制单元" data-testid="eng-system" value={systemId} disabled={locked || busyPlan} onChange={(e) => {
            setSystemId(e.target.value); setSelectedPids([]); resetSelection();
          }}>
            <option value="">请选择控制单元</option>
            {CONTROL_UNITS.map((s) => <option key={s.id} value={s.id}>{s.short} · {s.label}</option>)}
          </select>
        </label>
        {!system ? <p className="muted" data-testid="eng-select-unit">先选择控制单元，再选择需要采集的数据。</p> : null}
        {systemId === "dme" ? <label className="eng-unit-select">数据清单 <select data-testid="eng-data-source" value={dataSource} disabled={locked || busyPlan} onChange={(e) => { setDataSource(e.target.value); resetSelection(); }}><option value="standard">标准 OBD 数据</option><option value="x431">X431 数据清单</option></select></label> : null}
        {system && (systemId !== "dme" || dataSource === "x431") ? <OfflineRealtimePanel key={systemId} systemId={systemId} locked={running || peerBusy || saving || busyPlan} onBusyChange={setManufacturerBusy} /> : null}
      </section>
      {availablePids.length > 0 && dataSource === "standard" ? <>
      <section className="panel">
        <div className="eng-section-head"><h3>可采集数据</h3><span data-testid="eng-selection-count">已选 {selectedPids.length} 项 · 最多 {LIVE_DATA_SELECTION_LIMIT} 项</span></div>
        <div className="eng-parameters" data-testid="eng-parameters">
          {availablePids.map((pid) => {
            const meta = ENGINE_PID_META[pid as keyof typeof ENGINE_PID_META];
            return <label key={pid} className="eng-parameter">
              <input type="checkbox" data-testid={`eng-select-${pid}`} checked={selectedPids.includes(pid)}
                disabled={locked || busyPlan || (!selectedPids.includes(pid) && selectedPids.length >= LIVE_DATA_SELECTION_LIMIT)}
                onChange={(e) => selectParameters(e.target.checked ? [...selectedPids, pid] : selectedPids.filter((p) => p !== pid))} />
              <span>{meta.label}</span><small>{meta.unit}</small>
            </label>;
          })}
        </div>
        <div className="eng-selection-actions">
          <button type="button" data-testid="eng-select-all" disabled={locked || busyPlan} onClick={() => selectParameters(availablePids.slice(0, LIVE_DATA_SELECTION_LIMIT))}>全选</button>
          <button type="button" disabled={locked || busyPlan || !selectedPids.length} onClick={() => selectParameters([])}>取消全选</button>
        </div>
        <p className="muted">DME 标准 OBD 参数；车辆支持项在采集时核对。默认模拟，桌面实车采集待验收。</p>
      </section>
      {selectedPids.length > 0 ? <>
      <section className="panel">
        <div className="eng-toolbar">
          <label><input data-testid="eng-continuous" type="checkbox" checked={continuous} disabled={locked || busyPlan}
            onChange={(event) => { setContinuous(event.target.checked); resetSelection(); }} />持续采集，直到停止或本批样本满 3000 条</label>
          <label>
            方式
            <select data-testid="eng-mode" value={mode} disabled={locked || busyPlan} onChange={(e) => { setMode(e.target.value as Mode); resetSelection(); }}>
              <option value="simulation">模拟</option>
              <option value="live">实车</option>
            </select>
          </label>
          {mode === "simulation" ? (
            <label>
              模拟情景
              <select
                data-testid="eng-scenario"
                value={scenario}
                disabled={locked || busyPlan}
                onChange={(e) => { setScenario(e.target.value as Scenario); resetSelection(); }}
              >
                {SCENARIOS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            采样轮次
            <input
              data-testid="eng-cycles"
              type="number"
              min={1}
              max={10}
              value={sampleCycles}
              disabled={locked || busyPlan}
              onChange={(e) => {
                setSampleCycles(Number(e.target.value));
                resetSelection();
              }}
            />
          </label>
          <label>
            间隔（毫秒）
            <input
              data-testid="eng-interval"
              type="number"
              min={500}
              max={5000}
              step={100}
              value={intervalMs}
              disabled={locked || busyPlan}
              onChange={(e) => {
                setIntervalMs(Number(e.target.value));
                resetSelection();
              }}
            />
          </label>
          <button type="button" data-testid="eng-prepare" disabled={busyPlan || locked} onClick={() => loadPlan()}>
            加载计划
          </button>
          <button type="button" data-testid="eng-start" disabled={!ops.start} onClick={() => start()}>
            {mode === "live" ? "开始采集" : "开始模拟"}
          </button>
          {starting || status ? <button type="button" data-testid="eng-cancel" disabled={!jobId && !starting && !continuousActive} onClick={() => cancel()}>
            停止
          </button> : null}
          <button type="button" data-testid="eng-restart" disabled={!ops.start || running} onClick={() => start()}>
            重新采样
          </button>
          {starting || status || collected.length ? <button type="button" data-testid="eng-export" disabled={running || saving || (!status?.final && !collected.length)} onClick={() => void exportJson()}>
            {saving ? "正在保存…" : "保存此次采集"}
          </button> : null}
        </div>
        <DiagnosticCanRecordingControls simulation={mode === "simulation"} />
        {mode === "live" ? (
          <div className="eng-checks">
            <label>
              <input data-testid="eng-x431" type="checkbox" checked={x431Off} onChange={(e) => setX431Off(e.target.checked)} />
              X431 已退出诊断会话
            </label>
            <label>
              <input
                data-testid="eng-readonly"
                type="checkbox"
                checked={readOnly}
                onChange={(e) => setReadOnly(e.target.checked)}
              />
              仅执行已定义的只读发动机采样
            </label>
          </div>
        ) : null}
        {peerBusy ? <p className="muted" data-testid="eng-peer-busy">已有其它诊断任务在进行</p> : null}
        {error ? <p className="error" data-testid="eng-error">{error}</p> : null}
        {savedMessage ? <p role="status" data-testid="eng-saved">{savedMessage}</p> : null}
      </section>

      <section className="panel">
        <h3>计划</h3>
        <p className="muted" data-testid="eng-plan">
          {planView.text}
        </p>
      </section>

      <section className="panel">
        <h3>进度</h3>
        <p className="eng-progress" data-testid="eng-progress">
          {progressText}
        </p>
        <p className="muted" data-testid="eng-batch-count">本批保留 {engine?.samples?.length || 0} 条实际返回的样本；上限 3000 条。{mode === "simulation" ? "当前为模拟数据。" : ""}</p>
        <p className="eng-kind" data-testid="eng-kind" data-freshness={freshness}>
          {kindText}
        </p>
      </section>

      <section className="panel">
        <div className="eng-section-head">
          <h3>{system?.short} · 数据显示</h3>
          <div className="eng-display-modes" role="group" aria-label="数据显示方式">
            {([['text', '文字'], ['graph', '图形'], ['both', '文字与图形']] as const).map(([id, label]) =>
              <button type="button" key={id} data-testid={`eng-display-${id}`} aria-pressed={displayMode === id} onClick={() => setDisplayMode(id)}>{label}</button>)}
          </div>
        </div>
        <p className="muted" data-testid="eng-support">
          支持：{(engine?.supportedPids || []).join(", ") || "—"}；不支持：{(engine?.unsupportedPids || []).join(", ") || "—"}
          {engine?.completedCycles != null ? ` · 第 ${engine.completedCycles}/${engine.sampleCycles} 轮` : ""}
        </p>
        <ul className={`eng-pids eng-view-${displayMode}`} data-testid="eng-pids" data-live={liveDisplay ? "1" : "0"}>
          {selectedPids.map((pid) => {
            const meta = ENGINE_PID_META[pid as keyof typeof ENGINE_PID_META];
            const sample = byPid.get(pid);
            const unsupported = (engine?.unsupportedPids || []).includes(pid);
            const currency = sampleCurrency(sample, {
              freshness,
              latestCycle,
              nowMs,
              intervalMs: engine?.intervalMs ?? intervalMs,
              waiting,
            });
            const unit = sample?.unit || meta.unit;
            const valueText = unsupported
              ? "不支持"
              : sample && freshness === "stale"
                ? "已失效"
                : sample && currency.show
                  ? `${formatEngineValue(sample.value, unit)}${currency.stale ? "（等待更新）" : ""}`
                  : "—";
            const age = sample?.capturedUtc && liveDisplay ? formatSampleAge(sample.capturedUtc, nowMs) : "";
            return (
              <li
                key={pid}
                className="eng-pid"
                data-pid={pid}
                data-unsupported={unsupported ? "1" : undefined}
                data-stale={currency.stale ? "1" : undefined}
              >
                <dl>
                  <dt>{meta.label}</dt>
                  {displayMode !== "graph" ? <dd>{valueText}</dd> : null}
                  {displayMode !== "graph" && sample?.capturedUtc && (liveDisplay || freshness === "simulated" || freshness === "historical" || freshness === "sampling-sim") ? (
                    <time dateTime={sample.capturedUtc}>
                      {liveDisplay ? `上次更新 ${age}` : sample.capturedUtc}
                    </time>
                  ) : null}
                </dl>
                {displayMode !== "text" ? <ParameterGraph pid={pid} samples={engine?.samples || []} unavailable={freshness === "stale" || freshness === "cancelling"} /> : null}
              </li>
            );
          })}
        </ul>
      </section>
      </> : <section className="panel"><p className="muted" data-testid="eng-select-data">请勾选需要采集的数据。</p></section>}
      </> : null}
    </div>
  );
}
