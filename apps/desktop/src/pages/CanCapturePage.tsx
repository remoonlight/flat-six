import { useEffect, useRef, useState } from "react";
import { api, type CanCaptureResult } from "../api";
import "../can-capture.css";

export function CanCapturePage({ peerBusy, onBusyChange }: { peerBusy: boolean; onBusyChange: (busy: boolean) => void }) {
  const [runs, setRuns] = useState<NonNullable<CanCaptureResult["runs"]>>([]);
  const [doc, setDoc] = useState<CanCaptureResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [seconds, setSeconds] = useState(20);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const active = useRef<string | null>(null);
  const starting = useRef(false);
  const mounted = useRef(true);
  const fn = api().canCapture;
  const running = busy || Boolean(jobId);
  useEffect(() => { onBusyChange(running); }, [running, onBusyChange]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; if (active.current) void fn?.({ action: "cancel", jobId: active.current }); };
  }, [fn]);
  async function refresh() {
    const out = await fn?.({ action: "list" });
    if (out?.ok && mounted.current) setRuns(out.runs || []);
  }
  useEffect(() => { void refresh().catch((e) => setError(String(e))); }, [fn]);
  useEffect(() => {
    if (!jobId || !fn) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const out = await fn({ action: "status", jobId });
        if (stop) return;
        if (!out.ok) throw new Error(out.error || "读取采集状态失败");
        setDoc(out);
        if (["completed", "failed", "cancelled"].includes(out.state || "")) {
          active.current = null; setJobId(null); void refresh();
          return;
        }
      } catch (e) {
        await fn({ action: "cancel", jobId }).catch(() => {});
        if (stop) return;
        setError(String(e)); active.current = null; setJobId(null);
        return;
      }
      timer = setTimeout(poll, 500);
    };
    void poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [jobId, fn]);
  async function start() {
    if (!fn || starting.current || running || peerBusy) return;
    starting.current = true; setBusy(true); setError(null); setDoc(null);
    try {
      const out = await fn({ action: "start", seconds, confirmedReadOnly: confirmed, x431Inactive: confirmed });
      if (!out.ok || !out.jobId) throw new Error(out.error || "启动失败");
      active.current = out.jobId;
      if (!mounted.current) { await fn({ action: "cancel", jobId: out.jobId }); return; }
      setJobId(out.jobId);
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { starting.current = false; if (mounted.current) setBusy(false); }
  }
  async function replay(runId: string) {
    if (!fn || running || peerBusy) return;
    setBusy(true); setError(null);
    try {
      const out = await fn({ action: "replay", runId }); setDoc(out);
      if (!out.ok) setError(out.error || "回放失败");
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  const result = doc?.final || doc;
  const capture = result?.capture;
  return <section className="panel" data-page="can-capture">
    <h2>CAN 广播记录</h2>
    <p className="muted">选择 MX+ 后，记录已有 981 候选 ID。结果保存在本机，可离车后回放。信号定义尚未独立验证。</p>
    {!fn ? <p>采集与本地回放需要桌面端。</p> : <>
      <div className="can-capture-controls"><label>采集秒数 <input aria-label="采集秒数" type="number" min={1} max={60} value={seconds} disabled={running || peerBusy}
        onChange={(e) => setSeconds(Number(e.target.value))} /></label></div>
      <label className="can-capture-confirm"><input type="checkbox" checked={confirmed} disabled={running || peerBusy} onChange={(e) => setConfirmed(e.target.checked)} />
        车辆已停稳，允许只读记录；X431 和其他连接 MX+ 的应用已退出。</label>
      <button disabled={!confirmed || running || peerBusy || !Number.isInteger(seconds) || seconds < 1 || seconds > 60} onClick={() => void start()}>开始记录</button>{" "}
      <button disabled={!jobId} onClick={() => { if (jobId) void fn({ action: "cancel", jobId }); }}>停止并保存</button>{" "}
      <button disabled={running} onClick={() => void refresh()}>刷新历史记录</button>
      {jobId ? <p>采集中 · {doc?.latest?.frame_count ?? "—"} 帧</p> : null}
      {error || doc?.error ? <p role="alert">{error || doc?.error}</p> : null}
      {capture ? <div className="can-capture-result">
        <p><strong>{result?.integrityVerified ? "本地回放，文件完整性已核对" : "历史采集结果"}</strong> · {capture.frame_count} 帧 ·
          {capture.ok ? "记录质量检查通过" : "记录存在异常"}</p>
        {capture.error || capture.cleanup_error ? <p role="alert">{capture.error || capture.cleanup_error}</p> : null}
        <p>异常：{Object.entries(capture.adapter_notices || {}).filter(([k]) => !["STOPPED", "STM"].includes(k)).map(([k, v]) => `${k} × ${v}`).join("；") || "未记录异常提示"} · 尾部未完成字节 {capture.trailing_partial_bytes}</p>
        <p className="muted">时间来自电脑接收时刻；不能据此计算蓝牙无线丢包率。</p>
        <table><thead><tr><th>CAN ID</th><th>帧格式</th><th>DLC</th><th>帧数</th></tr></thead><tbody>
          {capture.partitions.map((p) => <tr key={`${p.id_hex}:${p.extended}:${p.dlc}`}><td>{p.id_hex}</td><td>{p.extended ? "扩展" : "标准"}</td><td>{p.dlc}</td><td>{p.count}</td></tr>)}
        </tbody></table>
        <p>目录：{result?.directory}</p>
      </div> : null}
      <h3>本地记录</h3>
      <ul className="plain-list">{runs.map((run) => <li key={run.id}>{run.id} · {run.frames} 帧 · {run.qualityOk ? "质量检查通过" : "异常或未完成"}{" "}
        <button disabled={running || peerBusy} onClick={() => void replay(run.id)}>核对并回放</button></li>)}</ul>
    </>}
  </section>;
}
