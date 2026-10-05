import { useEffect, useRef, useState } from "react";
import { api, callOfflineDiagnostics, type OfflineDiagnosticsResult } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";

type Parameter = { id: string; name: string; unit: string | null; decodedSampleCount: number };
type Sample = { parameterId: string; display: string | number; value: string | number; numeric: boolean;
  processedUtc: string; elapsedMs: number; synthetic: true; cycle: number };

export function ManufacturerRehearsalPanel({ profileId, parameters, locked, onBusyChange }: {
  profileId: string; parameters: Parameter[]; locked: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const [busy, setBusy] = useState(false), [saving, setSaving] = useState(false);
  const [error, setError] = useState(""), [saved, setSaved] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]), [cycles, setCycles] = useState(0);
  const [state, setState] = useState("尚未开始"), [continuous, setContinuous] = useState(true);
  const active = useRef(false), mounted = useRef(true), operation = useRef<string | null>(null);
  const batch = useRef<{ startedUtc: string; finishedUtc: string; cycles: OfflineDiagnosticsResult[]; samples: Sample[] }>(
    { startedUtc: "", finishedUtc: "", cycles: [], samples: [] });
  const selection = parameters.map((p) => p.id).join(",");
  useEffect(() => {
    batch.current = { startedUtc: "", finishedUtc: "", cycles: [], samples: [] };
    setSamples([]); setCycles(0); setError(""); setSaved(""); setState("尚未开始");
  }, [profileId, selection]);
  useEffect(() => { onBusyChange(busy || saving); }, [busy, saving, onBusyChange]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; active.current = false;
      if (operation.current) void api().cancelOfflineDiagnostics?.(operation.current).catch(() => {});
      onBusyChange(false);
    };
  }, [onBusyChange]);

  async function start() {
    if (active.current || busy || saving || locked || !parameters.length) return;
    active.current = true; setBusy(true); setError(""); setSaved(""); setState("正在演练");
    batch.current = { startedUtc: new Date().toISOString(), finishedUtc: "", cycles: [], samples: [] };
    setSamples([]); setCycles(0);
    try {
      do {
        if (batch.current.samples.length + parameters.length > 3000
          || new TextEncoder().encode(JSON.stringify(batch.current)).length > 6 * 1024 * 1024 - 2 * 1024 * 1024 - 65536) {
          throw new Error("本批已达到完整保存上限，请保存后开始新一批。");
        }
        const id = crypto.randomUUID(); operation.current = id;
        const doc = await callOfflineDiagnostics({ action: "ready-acquire", generation: "981", ecuId: 1,
          profileId, parameterIds: parameters.map((p) => p.id) }, id);
        operation.current = null;
        if (!mounted.current) return;
        if (doc.error === "cancelled" && !active.current) break;
        if (!doc.ok && doc.sourceKind == null) throw new Error(diagnosticMessage(doc.error || "演练资料校验失败"));
        const cycle = batch.current.cycles.length + 1;
        const incoming = (doc.samples || []) as Sample[];
        // The 2 MiB child-output cap + 64 KiB metadata reserve keeps the full
        // next cycle exportable. End the batch instead of silently dropping it.
        if (doc.sourceKind !== "historical-fixture-rehearsal" || doc.vehicleDataCollected !== false
          || doc.simulation !== true || incoming.some((sample) => sample.synthetic !== true
            || !parameters.some((p) => p.id === sample.parameterId))) throw new Error("演练结果来源不符，已停止。");
        const elapsedMs = Date.now() - Date.parse(batch.current.startedUtc);
        batch.current.cycles.push(doc);
        batch.current.samples.push(...incoming.map((sample) => ({ ...sample, elapsedMs, cycle })));
        setSamples([...batch.current.samples]); setCycles(cycle);
        if (!doc.ok) throw new Error(diagnosticMessage(doc.error || "厂商参数演练失败"));
        if (!continuous || !active.current) break;
        // Wait after the completed cycle; this is not a claim of X431 cadence.
        await new Promise((resolve) => setTimeout(resolve, 1000));
      } while (active.current);
      if (mounted.current) setState(active.current ? "本轮已完成" : "已停止");
    } catch (e) {
      if (mounted.current) { setError(String(e)); setState("已停止，保留本批已处理结果"); }
    } finally {
      active.current = false; operation.current = null;
      batch.current.finishedUtc = new Date().toISOString();
      if (mounted.current) setBusy(false);
    }
  }
  async function stop() {
    active.current = false; setState("正在停止…");
    if (operation.current) {
      try { await api().cancelOfflineDiagnostics?.(operation.current); }
      catch (e) { if (mounted.current) setError(String(e)); }
    }
  }
  async function save() {
    if (busy || saving || locked || !batch.current.cycles.length) return;
    setSaving(true); setSaved(""); setError("");
    try {
      const fn = api().saveDiagnosticRecording;
      if (!fn) throw new Error("需要桌面端保存功能。");
      const result = await fn({ fileName: `manufacturer-rehearsal-${Date.now()}.json`, recording: {
        kind: "manufacturer-acquisition-rehearsal", sourceKind: "historical-fixture-rehearsal",
        vehicleDataCollected: false, simulation: true, liveVerified: false,
        profileId, parameters, ...batch.current } });
      if (!mounted.current) return;
      if (!result.ok) throw new Error(diagnosticMessage(result.error || "保存失败，已保留本批结果。"));
      if (result.saved) setSaved(`已保存：${result.filePath}`);
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setSaving(false); }
  }
  const eligible = parameters.length > 0 && parameters.every((p) => p.decodedSampleCount > 0);
  return <section data-testid="manufacturer-rehearsal">
    <p>厂商参数采集演练：先在模拟串口核对会话和完整身份，再通过诊断传输解析历史响应，每个请求组每轮读取一次。不会连接车辆；重复演练不会产生新的车辆数值。</p>
    <p>当前实车读取未开放：还需核对独立诊断头的会话、长响应和完整版本。没有历史响应的参数需先补采。</p>
    <label><input type="checkbox" checked={continuous} disabled={locked || busy || saving}
      onChange={(event) => setContinuous(event.target.checked)} />连续演练（每轮结束后等待 1 秒，实际周期含处理耗时）</label>
    <button data-testid="manufacturer-start" disabled={locked || busy || saving || !eligible} onClick={() => void start()}>开始厂商参数演练</button>
    <button data-testid="manufacturer-stop" disabled={!busy} onClick={() => void stop()}>停止演练</button>
    <button data-testid="manufacturer-save" disabled={locked || busy || saving || cycles === 0} onClick={() => void save()}>保存演练结果</button>
    <p data-testid="manufacturer-state">{state} · {cycles} 轮 · {samples.length} 个演练值</p>
    {parameters.map((p) => {
      const series = samples.filter((s) => s.parameterId === p.id), latest = series.at(-1);
      const points = series.filter((s) => s.numeric && Number.isFinite(Number(s.value)));
      const low = Math.min(...points.map((s) => Number(s.value))), high = Math.max(...points.map((s) => Number(s.value)));
      const start = points[0]?.elapsedMs ?? 0, span = (points.at(-1)?.elapsedMs ?? start) - start;
      return <div key={p.id}><p>{p.name}：{latest?.display ?? "--"} {p.unit}（历史报文演练）</p>
        {points.length > 0 && <svg viewBox="0 0 480 120" role="img" aria-label={`${p.name}演练曲线`} className="eng-chart">
          <polyline fill="none" stroke="currentColor" strokeWidth="2" points={points.map((s) =>
            `${40 + (s.elapsedMs - start) / (span || 1) * 420},${100 - (Number(s.value) - low) / (high - low || 1) * 80}`).join(" ")} />
          <text x="0" y="15">{high}</text><text x="0" y="104">{low}</text><text x="350" y="117">{(span / 1000).toFixed(1)} s 演练时间</text>
        </svg>}</div>;
    })}
    {error && <p role="alert" data-testid="manufacturer-error">{error}</p>}
    {saved && <p role="status" data-testid="manufacturer-saved">{saved}</p>}
  </section>;
}
