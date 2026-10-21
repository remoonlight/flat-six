import { useEffect, useRef, useState } from "react";
import { api, type OfflineDiagnosticsResult, type ManufacturerConnectionEvent } from "../api";
import { diagnosticMessage, parameterSaveFailure, standardSessionFailure as sessionFailure } from "./diagnostic-messages";
import { obdFunctionLabel } from "./function-label";
import { LiveParameterCharts } from "./LiveParameterCharts";
import { mainlandValueText } from "./live-unit-logic.mjs";
import { manufacturerTiming, manufacturerConnectionMessage } from "./manufacturer-timing.mjs";

type Parameter = { id: string; name: string; unit: string | null; decodedSampleCount: number; decoderReady?: boolean };
type Sample = { parameterId: string; display: string | number; value: string | number; numeric: boolean;
  processedUtc: string; elapsedMs: number; synthetic: boolean; cycle: number; connectionEpoch?: number };

export function ManufacturerRehearsalPanel({ profileId, generation = "981", parameters, locked, onBusyChange, liveImplemented = false,
  identityEcuId, identityConnectionKey = "", canIdentify = false, identifiedProfileId, onIdentityResult,
  vehicleIdentification = false, vehicleGeneration, onVehicleIdentity, canReadVehicle = true }: {
  profileId: string; generation?: "981" | "982"; parameters: Parameter[]; locked: boolean; onBusyChange: (busy: boolean) => void; liveImplemented?: boolean;
  identityEcuId?: number; identityConnectionKey?: string; canIdentify?: boolean; identifiedProfileId?: string | null; onIdentityResult?: (id: string | null) => void;
  vehicleIdentification?: boolean; vehicleGeneration?: "981" | "982" | null; onVehicleIdentity?: (generation: "981" | "982" | null, identitySha256?: string) => void;
  canReadVehicle?: boolean;
}) {
  const [busy, setBusy] = useState(false), [saving, setSaving] = useState(false);
  const [error, setError] = useState<ReturnType<typeof sessionFailure> | null>(null), [saved, setSaved] = useState("");
  const [saveId, setSaveId] = useState("");
  const [saveFailure, setSaveFailure] = useState<ReturnType<typeof parameterSaveFailure> | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]), [cycles, setCycles] = useState(0);
  const [plotElapsedMs, setPlotElapsedMs] = useState<number | null>(null);
  const [unavailable, setUnavailable] = useState<Set<string>>(new Set());
  const [invalid, setInvalid] = useState<Set<string>>(new Set());
  const [state, setState] = useState(""), [displayMode, setDisplayMode] = useState<"text" | "graph" | "both">("text");
  const [vehicle, setVehicle] = useState(false);
  const [connectionEvents, setConnectionEvents] = useState<ManufacturerConnectionEvent[]>([]);
  const [retryCheckpoint, setRetryCheckpoint] = useState<ManufacturerConnectionEvent | null>(null);
  const [retryBusy, setRetryBusy] = useState(false);
  // Read-only requests use the same internal IPC preconditions as the topology page.
  // This flag does not detect or verify another diagnostic application.
  const x431Inactive = true;
  const confirmedReadOnly = true;
  const [identifying, setIdentifying] = useState(false);
  const identityContext = useRef(""), identityAttempt = useRef("");
  const identityRequired = !!identityConnectionKey && !!identityEcuId;
  const active = useRef(false), mounted = useRef(true), operation = useRef<string | null>(null);
  const stopRequested = useRef(false);
  const retained = useRef<Sample[]>([]), savedSession = useRef<string | null>(null);
  const selection = parameters.map((p) => p.id).join(",");
  useEffect(() => {
    const previous = savedSession.current; savedSession.current = null;
    if (previous) void api().manufacturerStream?.({ action: "discard", sessionId: previous }).catch(() => {});
    retained.current = [];
    setSamples([]); setCycles(0); setPlotElapsedMs(null); setUnavailable(new Set()); setInvalid(new Set()); setConnectionEvents([]); setRetryCheckpoint(null); setError(null); setSaved(""); setSaveId(""); setSaveFailure(null); setState("");
  }, [profileId, selection, generation]);
  useEffect(() => { onBusyChange(busy || saving); }, [busy, saving, onBusyChange]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; active.current = false;
      if (operation.current && !stopRequested.current) {
        stopRequested.current = true;
        void api().manufacturerStream?.({ action: "stop", sessionId: operation.current }).catch(() => {});
      }
      const previous = savedSession.current; savedSession.current = null;
      if (previous && previous !== operation.current) void api().manufacturerStream?.({ action: "discard", sessionId: previous }).catch(() => {});
      onBusyChange(false);
    };
  }, [onBusyChange]);

  useEffect(() => {
    if (!busy && (!vehicleIdentification || !locked) && identityContext.current !== identityConnectionKey) {
      identityContext.current = identityConnectionKey; identityAttempt.current = "";
      onIdentityResult?.(null);
      onVehicleIdentity?.(null);
      if (vehicleIdentification) setState("");
    }
  }, [identityConnectionKey, busy, locked, vehicleIdentification, onIdentityResult, onVehicleIdentity]);
  useEffect(() => {
    if (identityRequired && canIdentify && x431Inactive && confirmedReadOnly && !locked && !busy && !saving
      && !samples.length && identityAttempt.current !== identityConnectionKey) void identify();
  }, [identityConnectionKey, identityRequired, canIdentify, x431Inactive, confirmedReadOnly, locked, busy, saving, samples.length]);

  async function identify() {
    if (!identityRequired || !canIdentify || !x431Inactive || !confirmedReadOnly || locked || active.current || busy || saving) return;
    identityAttempt.current = identityConnectionKey; onIdentityResult?.(null); onVehicleIdentity?.(null);
    active.current = true; stopRequested.current = false; setIdentifying(true); setBusy(true); setError(null);
    retained.current = []; savedSession.current = null; setSamples([]); setCycles(0); setSaved(""); setSaveId("");
    setUnavailable(new Set()); setInvalid(new Set()); setPlotElapsedMs(null); setConnectionEvents([]); setRetryCheckpoint(null);
    setState(vehicleIdentification ? "正在识别车型…" : "正在识别当前控制单元版本…");
    let releaseObserved = false;
    try {
      const stream = api().manufacturerStream;
      if (!stream) throw new Error("需要桌面端数据采集功能。");
      const started = await stream(vehicleIdentification ? { action: "identify-vehicle", confirmedReadOnly, x431Inactive }
        : { action: "identify", ...(generation === "982" ? { generation } : {}), ecuId: identityEcuId, confirmedReadOnly, x431Inactive });
      if (!started.ok || typeof started.sessionId !== "string") throw new Error(started.error || "stream_worker_failed");
      operation.current = started.sessionId;
      if (!active.current || !mounted.current) {
        stopRequested.current = true;
        void stream({ action: "stop", sessionId: operation.current }).catch(() => {});
      }
      while (mounted.current) {
        let doc: Awaited<ReturnType<typeof stream>>;
        try {
          doc = await stream({ action: "status", sessionId: operation.current, cursor: 0, connectionCursor: 0 });
          if (!doc.ok || !Array.isArray(doc.events) || doc.events.length !== 0 || doc.cursor !== 0
            || !["preparing", "running", "stopping", "closing", "finished"].includes(doc.state || ""))
            throw new Error(doc.error || "status_failed");
        } catch (cause) {
          if (!mounted.current) return;
          active.current = false; setError(sessionFailure(cause)); setState("正在终止识别并等待诊断头释放…");
          if (!stopRequested.current) {
            stopRequested.current = true;
            void stream({ action: "stop", sessionId: operation.current }).catch(() => {});
          }
          await new Promise(resolve => setTimeout(resolve, 150)); continue;
        }
        if (!mounted.current) return;
        if (doc.state === "finished" && doc.released === true) {
          releaseObserved = true;
          if (!active.current) { setState("已终止识别"); break; }
          const result = doc.result, id = result?.identifiedProfileId;
          if (doc.error || !result?.ok || result.identityOnly !== true || typeof id !== "string")
            throw new Error(String(doc.error || result?.error || "stream_worker_failed"));
          if (vehicleIdentification) {
            const detected = result.vehicleGeneration;
            if ((detected !== "981" && detected !== "982") || !/^[a-f0-9]{64}$/.test(String(result.vehicleIdentitySha256 || "")))
              throw new Error("vehicle-model-unidentified");
            onVehicleIdentity?.(detected, String(result.vehicleIdentitySha256)); setState(`车型已识别：${detected}`);
          } else { onIdentityResult?.(id); setState("版本已识别，请选择参数后开始"); }
          break;
        }
        if (["stopping", "closing"].includes(doc.state || "") || !active.current)
          setState("正在等待诊断头释放并恢复待机…");
        await new Promise(resolve => setTimeout(resolve, 150));
      }
    } catch (cause) {
      if (mounted.current) { setError(sessionFailure(cause)); setState("识别未完成，可重试"); }
    } finally {
      if (operation.current && !releaseObserved && !stopRequested.current) {
        stopRequested.current = true;
        void api().manufacturerStream?.({ action: "stop", sessionId: operation.current }).catch(() => {});
      }
      active.current = false; operation.current = null;
      if (mounted.current) { setIdentifying(false); setBusy(false); }
    }
  }

  async function start(fromVehicle = false) {
    if (active.current || busy || saving || locked || !parameters.length) return;
    if (fromVehicle && (!canReadVehicle || !x431Inactive || !confirmedReadOnly)) return;
    setDisplayMode("text"); active.current = true; setVehicle(fromVehicle); setBusy(true); setError(null); setSaved(""); setSaveId(""); setSaveFailure(null); setState(fromVehicle ? "正在核对身份并读取车辆…" : "正在处理测试数据");
    retained.current = []; savedSession.current = null;
    setSamples([]); setCycles(0); setPlotElapsedMs(null); setUnavailable(new Set()); setInvalid(new Set()); setConnectionEvents([]); setRetryCheckpoint(null);
    stopRequested.current = false;
    let releaseObserved = false;
    try {
      const stream = api().manufacturerStream;
      if (!stream) throw new Error("需要桌面端数据采集功能。");
      const started = await stream({ action: fromVehicle ? "start-read" : "start", profileId, parameterIds: parameters.map(p => p.id), continuous: true,
        ...(fromVehicle ? { ecuId: identityEcuId, ...(generation === "982" ? { generation } : {}), confirmedReadOnly, x431Inactive } : {}) });
      if (!started.ok || typeof started.sessionId !== "string") throw new Error(started.error || "stream_worker_failed");
      operation.current = started.sessionId;
      savedSession.current = started.sessionId;
      let cursor = 0, completed = 0, connectionCursor = 0;
      // A stop/unmount during the start IPC also cancels the newly returned job.
      if (!active.current || !mounted.current) {
        stopRequested.current = true;
        void stream({ action: "stop", sessionId: operation.current }).catch(cause => {
          if (mounted.current) setError(sessionFailure(cause));
        });
      }
      while (mounted.current) {
        let doc: Awaited<ReturnType<typeof stream>>;
        try {
          doc = await stream({ action: "status", sessionId: operation.current, cursor, connectionCursor });
          if (!doc.ok || !Array.isArray(doc.events) || !Number.isInteger(doc.cursor)
            || doc.cursor !== cursor + doc.events.length || doc.events.some(event => !Array.isArray(event.samples))
            || !["preparing", "running", "stopping", "closing", "finished"].includes(doc.state || ""))
            throw new Error(doc.error || "status_failed");
        } catch (cause) {
          if (!mounted.current) return;
          active.current = false; setRetryCheckpoint(null); setError(sessionFailure(cause));
          setState("正在停止并等待诊断头释放，已有数据保留…");
          if (!stopRequested.current) {
            stopRequested.current = true;
            void stream({ action: "stop", sessionId: operation.current }).catch(cause => {
              if (mounted.current) setError(sessionFailure(cause));
            });
          }
          await new Promise(resolve => setTimeout(resolve, 150));
          continue;
        }
        if (!mounted.current) return;
        if (typeof doc.plotElapsedMs === "number" && Number.isFinite(doc.plotElapsedMs)) setPlotElapsedMs(doc.plotElapsedMs);
        for (const event of doc.events as OfflineDiagnosticsResult[]) {
          const incoming = event.samples as Sample[];
          retained.current.push(...incoming);
          completed += Number(event.completedCycles || 0);
        }
        cursor = Number(doc.cursor);
        if (doc.connectionEvents?.length) {
          setConnectionEvents(prior => [...prior, ...doc.connectionEvents!]);
          const latest = doc.connectionEvents.at(-1)!;
          setRetryCheckpoint(active.current && doc.awaitingRetry && latest.phase === "awaiting-retry" ? latest : null);
        } else if (doc.awaitingRetry === false) setRetryCheckpoint(null);
        connectionCursor = doc.connectionCursor ?? connectionCursor;
        setSamples([...retained.current]); setCycles(completed);
        if (doc.state !== "finished" && active.current)
          setState(doc.awaitingRetry ? "连接中断，等待选择重试继续或终止"
            : completed > 0 ? (fromVehicle ? "正在读取车辆参数…" : "正在处理测试数据") : "正在读取…");
        if (["stopping", "closing"].includes(doc.state || "") || !active.current)
          setState("正在停止并等待诊断头释放，已有数据保留…");
        if (Array.isArray(doc.unavailableParameters))
          setUnavailable(new Set(doc.unavailableParameters.map(entry => entry.parameterId)));
        if (Array.isArray(doc.invalidParameters))
          setInvalid(new Set(doc.invalidParameters.map(entry => entry.parameterId)));
        if (doc.state === "finished" && !doc.hasMore && !doc.hasMoreConnectionEvents) {
          if (doc.released !== true) {
            setState("正在停止并等待诊断头释放，已有数据保留…");
            await new Promise(resolve => setTimeout(resolve, 150));
            continue;
          }
          releaseObserved = true;
          const result = doc.result as OfflineDiagnosticsResult | null;
          if (doc.error || (!result?.ok && result?.error !== "cancelled" && result?.stopReason !== "time-limit"))
            throw new Error(String(doc.error || result?.error || "stream_worker_failed"));
          setState((doc.stopReason || result?.stopReason) === "time-limit" ? "本批已到十分钟，已停止并保留结果，可保存后开始下一批"
            : result?.stopReason === "all-parameters-unavailable" ? "所选参数本批均不可用，已停止并保留已有数据"
            : active.current ? "本轮已完成" : "已停止");
          break;
        }
        if (!doc.hasMore && !doc.hasMoreConnectionEvents) await new Promise(resolve => setTimeout(resolve, 150));
      }
    } catch (e) {
      if (fromVehicle && String(e instanceof Error ? e.message : e).includes("identity-mismatch")) onIdentityResult?.(null);
      if (mounted.current) { setError(sessionFailure(e)); setState("已停止，保留本批已处理结果"); }
    } finally {
      if (operation.current && !releaseObserved && !stopRequested.current) {
        stopRequested.current = true;
        void api().manufacturerStream?.({ action: "stop", sessionId: operation.current }).catch(() => {});
      }
      active.current = false; operation.current = null;
      if (mounted.current) { setBusy(false); setRetryCheckpoint(null); }
    }
  }
  async function stop() {
    active.current = false; setRetryCheckpoint(null); setState("正在停止…");
    if (operation.current && !stopRequested.current) {
      stopRequested.current = true;
      try { await api().manufacturerStream?.({ action: "stop", sessionId: operation.current }); }
      catch (e) { if (mounted.current) setError(sessionFailure(e)); }
    }
  }
  async function retry() {
    const checkpoint = retryCheckpoint, sessionId = operation.current;
    if (!checkpoint || !sessionId || retryBusy || !active.current) return;
    setRetryBusy(true); setError(null);
    try {
      const result = await api().manufacturerStream?.({ action: "retry", sessionId, episode: checkpoint.episode, attempt: checkpoint.attempt });
      if (!mounted.current) return;
      if (!result?.ok) setError(sessionFailure(result?.error || "stream_retry_not_available"));
      else { setRetryCheckpoint(null); setState("正在重连并核对控制单元身份…"); }
    } catch (e) { if (mounted.current) setError(sessionFailure(e)); }
    finally { if (mounted.current) setRetryBusy(false); }
  }
  async function save() {
    if (busy || saving || locked || !retained.current.length || !savedSession.current) return;
    setSaving(true); setSaved(""); setSaveId(""); setSaveFailure(null);
    try {
      const fn = api().manufacturerStream;
      if (!fn) throw new Error("parameter_recording_save_failed");
      const result = await fn({ action: "save", sessionId: savedSession.current });
      if (!mounted.current) return;
      if (!result.ok) setSaveFailure(parameterSaveFailure(result.error));
      if (result.saved) { setSaved(`已保存：${result.filePath}`); setSaveId(result.saveId || ""); }
    } catch (e) { if (mounted.current) setSaveFailure(parameterSaveFailure(e)); }
    finally { if (mounted.current) setSaving(false); }
  }
  async function revealSaved() {
    if (!saveId) return;
    try { const result = await api().revealParameterRecording?.({ saveId });
      if (!result?.ok) setError(sessionFailure(result?.error || "parameter_recording_reveal_failed"));
    } catch { setError(sessionFailure("parameter_recording_reveal_failed")); }
  }
  if (vehicleIdentification) return <section data-testid="vehicle-identification">
    {!vehicleGeneration && <>
      {!busy && identityAttempt.current === identityConnectionKey && !!identityConnectionKey && <button data-testid="vehicle-identify-retry" disabled={locked || !canIdentify || !x431Inactive || !confirmedReadOnly} onClick={() => void identify()}>重试</button>}
    </>}
    {busy && <button data-testid="vehicle-identify-stop" disabled={!active.current} onClick={stop}>终止</button>}
    {state && <p role="status" data-testid="vehicle-identify-state">{state}</p>}
    {error && <p role="alert">{error.message}</p>}
  </section>;
  return <section data-testid="manufacturer-rehearsal">
    {identityRequired && !busy && identifiedProfileId !== profileId && (identityAttempt.current === identityConnectionKey || samples.length > 0) && <button data-testid="manufacturer-identify-retry" disabled={locked || saving || !canIdentify || !x431Inactive || !confirmedReadOnly} onClick={() => void identify()}>重新识别</button>}
    <button data-testid="manufacturer-read" disabled={!liveImplemented || !canReadVehicle || locked || busy || saving || !x431Inactive || !confirmedReadOnly || identityRequired && identifiedProfileId !== profileId || !parameters.length || parameters.some(p => !p.decoderReady)} onClick={() => void start(true)}>{obdFunctionLabel(samples.length ? "重新开始" : "开始", liveImplemented)}</button>
    <button data-testid="manufacturer-stop" disabled={!busy || !active.current} onClick={() => void stop()}>{identifying ? "终止" : "停止"}</button>
    <button data-testid="manufacturer-save" disabled={locked || busy || saving || !samples.length} onClick={() => void save()}>{saving ? "正在保存…" : "保存记录"}</button>
    {state && <p data-testid="manufacturer-state">{state} · {cycles} 轮 · {samples.length} 个数值</p>}
    {retryCheckpoint && <div role="alertdialog" aria-label="实时数据连接中断" data-testid="manufacturer-recovery">
      <p>连接中断，已保留本批数据。重试会核对原控制单元后继续；仍按本批原十分钟截止。中断数据不补值。</p>
      <button type="button" data-testid="manufacturer-retry" disabled={retryBusy || !busy} onClick={() => void retry()}>重试继续</button>
      <button type="button" data-testid="manufacturer-terminate" disabled={!busy} onClick={() => void stop()}>终止</button>
    </div>}
    {connectionEvents.length > 0 && <p role="status" aria-live="polite" data-testid="manufacturer-connection-state">{manufacturerConnectionMessage(connectionEvents.at(-1)!, !busy)} · 已记录 {connectionEvents.length} 项通信事件</p>}
    <div className="eng-display-modes" role="group" aria-label="厂商数据显示方式">{([["text", "数值"], ["graph", "图形"], ["both", "数值与图形"]] as const).map(([id, label]) => <button key={id} type="button" data-testid={`manufacturer-display-${id}`} aria-pressed={displayMode === id} onClick={() => setDisplayMode(id)}>{label}</button>)}</div>
    {displayMode !== "graph" && parameters.map((p) => {
      const series = samples.filter((s) => s.parameterId === p.id), latest = series.at(-1);
      const disabled = unavailable.has(p.id);
      const connection = connectionEvents.at(-1);
      const stale = invalid.has(p.id) || latest && connection && (connection.phase !== "recovered"
        || (latest.connectionEpoch ?? 1) < connection.connectionEpoch);
      const display = mainlandValueText(latest?.numeric ? Number(latest.value) : latest?.display, p.unit);
      return <div key={p.id} data-parameter-id={p.id} data-unavailable={disabled ? "1" : undefined}><p>{p.name}：{disabled ? "不可用（本批）" : `${display.text} ${display.unit || ""}${stale ? "（未更新）" : ""}`}（本次车辆读取）</p>
        {(disabled || stale) && latest && <p>上次值：{display.text} {display.unit}</p>}

        </div>;
    })}
    {displayMode !== "text" && <LiveParameterCharts currentElapsedMs={plotElapsedMs} series={parameters.map(p => {
      const series = samples.filter(s => s.parameterId === p.id), latest = series.at(-1);
      const { segments } = manufacturerTiming(series);
      const connection = connectionEvents.at(-1);
      const stale = invalid.has(p.id) || latest && connection && (connection.phase !== "recovered"
        || (latest.connectionEpoch ?? 1) < connection.connectionEpoch);
      return { id: p.id, name: p.name, unit: p.unit,
        latestValue: !unavailable.has(p.id) && latest?.numeric ? Number(latest.value) : undefined,
        latestSuffix: stale ? "（未更新）" : "",
        latest: unavailable.has(p.id) ? "不可用（本批）" : `${latest?.display ?? "—"}${stale ? "（未更新）" : ""}`, timestamp: latest?.processedUtc,
        segments: segments.map(segment => segment.map(s => ({ elapsedMs: s.elapsedMs,
          value: s.numeric ? Number(s.value) : NaN, timestamp: s.processedUtc }))),
      };
    })} />}
    {error && <div role="alert"><p data-testid="manufacturer-error">{error.message}</p>
    </div>}
    {saveFailure && <div role="alert">
      <p data-testid="manufacturer-error">{saveFailure.message}</p>
    </div>}
    {saved && <p role="status" data-testid="manufacturer-saved">{saved}</p>}
    {saveId && <button type="button" data-testid="manufacturer-open-folder" onClick={() => void revealSaved()}>打开文件夹</button>}
  </section>;
}
