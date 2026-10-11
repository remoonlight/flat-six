import { useEffect, useRef, useState } from "react";
import { api, type OfflineDiagnosticsResult, type ManufacturerConnectionEvent } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { obdFunctionLabel } from "./function-label";
import { manufacturerTiming, manufacturerConnectionMessage } from "./manufacturer-timing.mjs";

type Parameter = { id: string; name: string; unit: string | null; decodedSampleCount: number; decoderReady?: boolean };
type Sample = { parameterId: string; display: string | number; value: string | number; numeric: boolean;
  processedUtc: string; elapsedMs: number; synthetic: boolean; cycle: number; connectionEpoch?: number };

export function ManufacturerRehearsalPanel({ profileId, parameters, locked, onBusyChange, liveImplemented = false }: {
  profileId: string; parameters: Parameter[]; locked: boolean; onBusyChange: (busy: boolean) => void; liveImplemented?: boolean;
}) {
  const [busy, setBusy] = useState(false), [saving, setSaving] = useState(false);
  const [error, setError] = useState(""), [saved, setSaved] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]), [cycles, setCycles] = useState(0);
  const [state, setState] = useState("尚未开始"), [continuous, setContinuous] = useState(true);
  const [vehicle, setVehicle] = useState(false);
  const [connectionEvents, setConnectionEvents] = useState<ManufacturerConnectionEvent[]>([]);
  const [x431Inactive, setX431Inactive] = useState(false), [confirmedReadOnly, setConfirmedReadOnly] = useState(false);
  const active = useRef(false), mounted = useRef(true), operation = useRef<string | null>(null);
  const retained = useRef<Sample[]>([]), savedSession = useRef<string | null>(null);
  const selection = parameters.map((p) => p.id).join(",");
  useEffect(() => {
    const previous = savedSession.current; savedSession.current = null;
    if (previous) void api().manufacturerStream?.({ action: "discard", sessionId: previous }).catch(() => {});
    retained.current = [];
    setSamples([]); setCycles(0); setConnectionEvents([]); setError(""); setSaved(""); setState("尚未开始");
  }, [profileId, selection]);
  useEffect(() => { onBusyChange(busy || saving); }, [busy, saving, onBusyChange]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; active.current = false;
      if (operation.current) void api().manufacturerStream?.({ action: "stop", sessionId: operation.current }).catch(() => {});
      onBusyChange(false);
    };
  }, [onBusyChange]);

  async function start(fromVehicle = false) {
    if (active.current || busy || saving || locked || !parameters.length) return;
    if (fromVehicle && (!x431Inactive || !confirmedReadOnly)) return;
    active.current = true; setVehicle(fromVehicle); setBusy(true); setError(""); setSaved(""); setState(fromVehicle ? "正在核对身份并读取车辆…" : "正在演练");
    retained.current = []; savedSession.current = null;
    setSamples([]); setCycles(0); setConnectionEvents([]);
    try {
      const stream = api().manufacturerStream;
      if (!stream) throw new Error("需要桌面端持续演练功能。");
      const started = await stream({ action: fromVehicle ? "start-read" : "start", profileId, parameterIds: parameters.map(p => p.id), continuous,
        ...(fromVehicle ? { confirmedReadOnly, x431Inactive } : {}) });
      if (!started.ok || typeof started.sessionId !== "string") throw new Error(diagnosticMessage(started.error || "启动失败"));
      operation.current = started.sessionId;
      savedSession.current = started.sessionId;
      let cursor = 0, completed = 0, connectionCursor = 0;
      // A stop/unmount during the start IPC also cancels the newly returned job.
      if (!active.current || !mounted.current) await stream({ action: "stop", sessionId: operation.current });
      while (mounted.current) {
        const doc = await stream({ action: "status", sessionId: operation.current, cursor, connectionCursor });
        if (!mounted.current) return;
        if (!doc.ok || !Array.isArray(doc.events) || !Number.isInteger(doc.cursor))
          throw new Error(diagnosticMessage(doc.error || "演练结果无效"));
        for (const event of doc.events as OfflineDiagnosticsResult[]) {
          const incoming = event.samples as Sample[];
          retained.current.push(...incoming);
          completed += Number(event.completedCycles || 0);
        }
        cursor = Number(doc.cursor);
        if (doc.connectionEvents?.length) setConnectionEvents(prior => [...prior, ...doc.connectionEvents!]);
        connectionCursor = doc.connectionCursor ?? connectionCursor;
        setSamples([...retained.current]); setCycles(completed);
        if (doc.state === "finished" && !doc.hasMore && !doc.hasMoreConnectionEvents) {
          const result = doc.result as OfflineDiagnosticsResult | null;
          if (doc.error || (!result?.ok && result?.error !== "cancelled"))
            throw new Error(diagnosticMessage(String(doc.error || result?.error || "演练进程异常结束")));
          setState((doc.stopReason || result?.stopReason) === "time-limit" ? "本批已到十分钟，已停止并保留结果，可保存后开始下一批"
            : active.current ? "本轮已完成" : "已停止");
          break;
        }
        if (!doc.hasMore && !doc.hasMoreConnectionEvents) await new Promise(resolve => setTimeout(resolve, 150));
      }
    } catch (e) {
      if (mounted.current) { setError(String(e)); setState("已停止，保留本批已处理结果"); }
    } finally {
      if (operation.current) void api().manufacturerStream?.({ action: "stop", sessionId: operation.current }).catch(() => {});
      active.current = false; operation.current = null;
      if (mounted.current) setBusy(false);
    }
  }
  async function stop() {
    active.current = false; setState("正在停止…");
    if (operation.current) {
      try { await api().manufacturerStream?.({ action: "stop", sessionId: operation.current }); }
      catch (e) { if (mounted.current) setError(String(e)); }
    }
  }
  async function save() {
    if (busy || saving || locked || (!retained.current.length && !connectionEvents.length) || !savedSession.current) return;
    setSaving(true); setSaved(""); setError("");
    try {
      const fn = api().manufacturerStream;
      if (!fn) throw new Error("需要桌面端保存功能。");
      const result = await fn({ action: "save", sessionId: savedSession.current });
      if (!mounted.current) return;
      if (!result.ok) throw new Error(diagnosticMessage(result.error || "保存失败，已保留本批结果。"));
      if (result.saved) setSaved(`已保存：${result.filePath}`);
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setSaving(false); }
  }
  const eligible = parameters.length > 0 && parameters.every((p) => p.decodedSampleCount > 0);
  return <section data-testid="manufacturer-rehearsal">
    <p>厂商参数采集演练：先在模拟串口核对会话和完整身份，再通过诊断传输解析历史响应，每个请求组每轮读取一次。不会连接车辆；重复演练不会产生新的车辆数值。</p>
    <p>车辆读取使用当前所选诊断头，先核对完整DME或Gateway身份。开始前须退出X431通信；当前接入具名981 DME和Gateway A7.1的vLinker／OBDLink串口及VNCI读取软件，诊断头和本车验证仍待完成。VNCI返回诊断PDU，可显式选择“完整诊断应用响应”记录；这是应用层记录，不能代替原始CAN帧。没有历史响应不影响已定义参数的独立读取；其他版本与单元继续待适配。</p>
    <label><input data-testid="manufacturer-x431-inactive" type="checkbox" checked={x431Inactive} disabled={busy || saving || locked} onChange={event => setX431Inactive(event.target.checked)} />X431 已退出诊断会话</label>
    <label><input data-testid="manufacturer-readonly" type="checkbox" checked={confirmedReadOnly} disabled={busy || saving || locked} onChange={event => setConfirmedReadOnly(event.target.checked)} />仅执行已定义的厂商参数只读采集</label>
    <label><input type="checkbox" checked={continuous} disabled={locked || busy || saving}
      onChange={(event) => setContinuous(event.target.checked)} />连续读取／演练（单次最多十分钟，同一会话逐轮返回，轮间等待1秒；这是暂定调度，停止后保留本批结果）</label>
    <button data-testid="manufacturer-start" disabled={locked || busy || saving || !eligible} onClick={() => void start()}>开始厂商参数演练</button>
    <button data-testid="manufacturer-read" disabled={locked || busy || saving || !x431Inactive || !confirmedReadOnly || !parameters.length || parameters.some(p => !p.decoderReady)} onClick={() => void start(true)}>{obdFunctionLabel("从车辆读取厂商参数", liveImplemented)}</button>
    <button data-testid="manufacturer-stop" disabled={!busy} onClick={() => void stop()}>{vehicle ? "停止读取" : "停止演练"}</button>
    <button data-testid="manufacturer-save" disabled={locked || busy || saving || (!samples.length && !connectionEvents.length)} onClick={() => void save()}>{vehicle ? "保存本批车辆数值与中断记录" : "保存演练结果"}</button>
    <p data-testid="manufacturer-state">{state} · {cycles} 轮 · {samples.length} 个{vehicle ? "车辆读取值" : "演练值"}</p>
    {connectionEvents.length > 0 && <p role="status" aria-live="polite" data-testid="manufacturer-connection-state">{manufacturerConnectionMessage(connectionEvents.at(-1)!, !busy)} · 已记录 {connectionEvents.length} 项通信事件</p>}
    <p className="muted">更新间隔按主机观察到完整响应后的实际经过时间计算，不代表ECU采样周期；重连并重新核对身份后曲线分段，不补值。演练时间仅表示本机处理历史响应的时间。</p>
    {parameters.map((p) => {
      const series = samples.filter((s) => s.parameterId === p.id), latest = series.at(-1);
      const { points, segments, intervalMs, recovered, interruptions } = manufacturerTiming(series);
      const low = Math.min(...points.map((s) => Number(s.value))), high = Math.max(...points.map((s) => Number(s.value)));
      const start = points[0]?.elapsedMs ?? 0, span = (points.at(-1)?.elapsedMs ?? start) - start;
      return <div key={p.id}><p>{p.name}：{latest?.display ?? "--"} {p.unit}（{vehicle ? "本次车辆读取" : "历史报文演练"}）</p>
        <p data-testid="manufacturer-timing">{recovered ? "通信恢复后首个值，更新间隔待下一次返回" : intervalMs === null ? "更新间隔：等待下一次返回" : `最近更新间隔：${intervalMs.toFixed(1)} 毫秒`}{interruptions ? ` · 通信中断分段 ${interruptions} 处` : ""}</p>
        {points.length > 0 && <svg viewBox="0 0 480 120" role="img" aria-label={`${p.name}${vehicle ? "读取" : "演练"}曲线`} className="eng-chart">
          {segments.map((segment, i) => <polyline key={i} fill="none" stroke="currentColor" strokeWidth="2" points={segment.map((s) =>
            `${40 + (s.elapsedMs - start) / (span || 1) * 420},${100 - (Number(s.value) - low) / (high - low || 1) * 80}`).join(" ")} />)}
          {segments.filter(segment => segment.length === 1).map((segment, i) => <circle key={i} r="2.5" fill="currentColor"
            cx={40 + (segment[0].elapsedMs - start) / (span || 1) * 420} cy={100 - (Number(segment[0].value) - low) / (high - low || 1) * 80} />)}
          <text x="0" y="15">{high}</text><text x="0" y="104">{low}</text><text x="350" y="117">{(span / 1000).toFixed(1)} s {vehicle ? "响应观察时间" : "演练时间"}</text>
        </svg>}</div>;
    })}
    {error && <p role="alert" data-testid="manufacturer-error">{error}</p>}
    {saved && <p role="status" data-testid="manufacturer-saved">{saved}</p>}
  </section>;
}
