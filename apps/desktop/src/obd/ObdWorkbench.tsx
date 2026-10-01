import { useEffect, useMemo, useState } from "react";
import type { ObdRun, ObdRuntimeState, ObdScenario } from "@porsche981/domain";
import { api } from "../api";

const INITIAL: ObdRuntimeState = { mode: "offline", phase: "idle", run: null, elapsedMs: 0, observations: [], error: null };
const SCENARIOS: Record<ObdScenario, string> = { normal: "正常连接 · 有故障码", "no-codes": "正常连接 · 无码", "no-response": "车辆没有响应", disconnect: "采集中途掉线", malformed: "收到不完整数据" };
const STATUS: Record<string, string> = { running: "采集中", completed: "已结束并保存", partial: "部分完成并保存", cancelled: "已停止并保存", interrupted: "采集中断" };
const OUTCOME: Record<string, string> = { ok: "有效响应", "no-data": "暂无数据", timeout: "响应超时", invalid: "数据格式异常", disconnected: "连接中断", cancelled: "已取消" };
const TASKS: Record<string, string> = { "0100": "支持能力", "03": "已存储故障码", "07": "待定故障码", "0101": "MIL 与监控原始信息", "020200": "冻结帧关联码", "020C00": "冻结帧转速", "020500": "冻结帧水温", "0902": "车辆识别号", "0A": "永久故障码" };
const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

function Curve({ points, unit }: { points: { t: number; value: number }[]; unit: string }) {
  if (points.length < 2) return <p className="muted">累计两次采样后显示趋势</p>;
  const values = points.map((p) => p.value), low = Math.min(...values), high = Math.max(...values);
  const x0 = points[0].t, x1 = points.at(-1)!.t;
  const pad = Math.max((high - low) * .15, 1), min = low - pad, max = high + pad;
  const plot = points.map((p, i) => `${i && p.t - points[i - 1].t < 10000 ? "L" : "M"}${42 + 260 * (p.t - x0) / Math.max(1, x1 - x0)},${70 - 55 * (p.value - min) / (max - min)}`).join(" ");
  return <svg viewBox="0 0 330 106" role="img" aria-label={`采样趋势，纵轴 ${unit}，横轴会话秒数`} className="obd-curve">
    <path d="M42 10V76H310" fill="none" stroke="currentColor" opacity=".25" />
    <path d={plot} fill="none" stroke="var(--accent)" strokeWidth="2" />
    <text x="0" y="18">{max.toFixed(0)}</text><text x="0" y="74">{min.toFixed(0)}</text>
    <text x="42" y="94">{(x0 / 1000).toFixed(1)}</text><text x="273" y="94">{(x1 / 1000).toFixed(1)} s</text>
    <text x="5" y="103">{unit}</text>
  </svg>;
}

export function ObdWorkbench({ tab }: { tab: string }) {
  const [inner, setInner] = useState<"connection" | "live" | "faults" | "monitors">("connection");
  const [state, setState] = useState<ObdRuntimeState>(INITIAL);
  const [runs, setRuns] = useState<ObdRun[]>([]);
  const [scenario, setScenario] = useState<ObdScenario>("normal");
  const [budget, setBudget] = useState(30000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [clock, setClock] = useState(Date.now());
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    let live = true, received = false;
    const off = api().onObdState((next) => { if (live) { received = true; setState(next); } });
    api().obdGetState().then((next) => { if (live && !received) setState(next); }).catch((e) => { if (live) setError(String(e)); });
    const timer = window.setInterval(() => setClock(Date.now()), 500);
    return () => { live = false; off(); clearInterval(timer); };
  }, []);
  useEffect(() => {
    let live = true;
    api().obdListRuns().then((rows) => { if (live) setRuns(rows); }).catch((e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [state.run?.sessionId, state.phase]);
  useEffect(() => { setCursor(0); setPlaying(false); }, [state.mode, state.run?.sessionId]);
  useEffect(() => {
    if (!playing || state.mode !== "replay") return;
    const timer = window.setInterval(() => setCursor((prev) => Math.min(state.elapsedMs, prev + 250)), 250);
    return () => clearInterval(timer);
  }, [playing, state.mode, state.elapsedMs]);
  useEffect(() => { if (cursor >= state.elapsedMs) setPlaying(false); }, [cursor, state.elapsedMs]);
  const active = state.phase === "running" || state.phase === "saving";
  const at = state.mode === "replay" ? cursor : active && state.run ? Math.max(state.elapsedMs, clock - Date.parse(state.run.startedAt)) : state.elapsedMs;
  const observations = useMemo(() => state.mode === "replay" ? state.observations.filter((o) => o.tMs <= cursor) : state.observations, [state.observations, state.mode, cursor]);
  const liveSamples = observations.flatMap((o) => o.samples.filter((s) => s.context === "live").map((s) => ({ ...s, t: o.tMs })));
  const groups = [...new Set(liveSamples.map((s) => s.ecu + "/" + s.signal))].map((key) => {
    const values = liveSamples.filter((s) => s.ecu + "/" + s.signal === key); return { key, values, last: values.at(-1)! };
  });
  const dtcs = observations.flatMap((o) => o.dtcs.map((d) => ({ ...d, at: o.tMs })));
  const freeze = observations.flatMap((o) => o.samples.filter((s) => s.context === "freeze"));
  const monitors = observations.flatMap((o) => o.monitors);
  const vins = observations.flatMap((o) => o.vin);
  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError(null); setMessage("");
    try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  if (tab !== "dev") return null;
  return <div className="obd-workbench">
    <div className="obd-status-bar panel" aria-live="polite">
      <span className="obd-status-dot" aria-hidden />
      <strong>{state.mode === "replay" ? "回放 · 模拟记录" : active ? "离线模拟中" : "模拟未运行"}</strong>
      <span className="obd-source">全部数据为模拟，非实车</span>
    </div>
    <nav className="chip-row" aria-label="开发与验证内页">
      {([["connection", "离线演练"], ["live", "实时数据"], ["faults", "采集结果"], ["monitors", "就绪监控"]] as const).map(([id, label]) => (
        <button key={id} type="button" className={`chip${inner === id ? " active" : ""}`} onClick={() => setInner(id)}>{label}</button>
      ))}
    </nav>
    {(error || state.error) && <p className="error" role="alert">{error || state.error}</p>}
    {message && <p role="status">{message}</p>}
    {active && <div className="obd-acquisition-strip" aria-live="polite">
      <span>{state.phase === "saving" ? "正在确认保存…" : "限时采集"} · {seconds(at)} / {seconds(state.run!.budgetMs)}</span>
      <span>已落盘 {observations.length} 次请求结果</span>
      <button disabled={busy || state.phase === "saving"} onClick={() => action(() => api().obdStop())}>停止并保存</button>
      <progress value={Math.min(at, state.run!.budgetMs)} max={state.run!.budgetMs} aria-label="采集预算进度" />
    </div>}
    {!active && state.run && <p className="muted" role="status">模拟会话 #{state.run.sessionId} · {STATUS[state.run.status]} · {state.run.reason}</p>}
    {state.mode === "replay" && <section className="panel obd-replay">
      <h2>会话回放</h2><p className="muted">仅读取已保存的模拟记录，不连接任何设备。</p>
      <div className="row"><button onClick={() => { if (cursor >= state.elapsedMs) setCursor(0); setPlaying(!playing); }}>{playing ? "暂停回放" : "播放回放"}</button>
      <input aria-label="回放位置" type="range" min={0} max={Math.max(1, state.elapsedMs)} step={1} value={cursor} onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }} />
      <span>{seconds(cursor)} / {seconds(state.elapsedMs)}</span></div>
    </section>}
    {inner === "connection" && <>
      <section className="panel">
        <h2>先在电脑上演练</h2>
        <p>无需接车。模拟连接、读取、故障与自动保存，确认现场操作流程。</p>
        <div className="row">
          <label>演练场景<select value={scenario} disabled={active || busy} onChange={(e) => setScenario(e.target.value as ObdScenario)}>{Object.entries(SCENARIOS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
          <label>采集时间上限<select value={budget} disabled={active || busy} onChange={(e) => setBudget(Number(e.target.value))}><option value={15000}>15 秒 · 快速演练</option><option value={30000}>30 秒 · 完整演练</option><option value={180000}>3 分钟 · 预算演练</option></select></label>
          <button className="btn" disabled={active || busy} onClick={() => action(async () => {
            await api().obdDisconnect().catch(() => {});
            await api().obdStartSimulation({ scenario, budgetMs: budget });
          })}>开始模拟采集</button>
        </div>
        <p className="muted">模拟工况：暖机怠速、停车。优先读取故障与冻结帧，再采实时值，预算内提前预留收尾时间。</p>
        <details><summary>现场准备状态</summary><ul><li>当前：离线演练与记录回放</li><li>下一步：vLinker / OBDLink MX+ 蓝牙验证，再进行短时实车读取</li><li>使用 MX+ 连接项目时，先断开 RaceChrono 等应用；离线工作台不访问硬件</li></ul></details>
      </section>
      <section className="panel">
        <h2>已保存的模拟会话</h2>
        {!runs.length ? <p className="muted">开始一次演练后，记录会自动保存在本机。</p> : <ul className="plain-list obd-run-list">{runs.map((run) => <li key={run.sessionId}>
          <div><strong>#{run.sessionId} · {SCENARIOS[run.scenario]}</strong><div className="muted">{new Date(run.startedAt).toLocaleString()} · {STATUS[run.status]}</div></div>
          <button disabled={active || busy} onClick={() => action(() => api().obdReplay(run.sessionId))}>打开回放</button>
          <button disabled={busy} onClick={() => action(async () => { const result = await api().obdExport(run.sessionId); if (result.saved) setMessage("记录已导出，文件内保留模拟来源和原始响应。"); })}>导出记录</button>
        </li>)}</ul>}
      </section>
    </>}
    {inner === "live" && <section className="panel"><h2>模拟实时数据</h2>
      <p className="muted">横轴为会话时间；停止后保留最后一次记录，不代表当前车辆状态。</p>
      {!groups.length ? <p>先在“连接”开始演练，或推进回放位置。</p> : <div className="obd-metrics">{groups.map(({ key, values, last }) => <article className="obd-metric" key={key}>
        <h3>{last.label}</h3><div className="obd-value">{last.value.toFixed(last.signal === "rpm" || last.signal === "speed" ? 0 : 1)} <small>{last.unit}</small></div>
        <p className="muted">ECU {last.ecu} · {seconds(last.t)} · {at - last.t > 5000 ? "值已过期" : active ? "模拟采样" : "记录值"}</p>
        <Curve points={values.slice(-60)} unit={last.unit} />
      </article>)}</div>}
    </section>}
    {inner === "faults" && <section className="panel"><h2>采集结果 · 模拟</h2>
      {!observations.some((o) => o.command === "03" && o.outcome === "ok") ? <p>尚未取得有效读码响应，不能判定是否无码。</p> : !dtcs.length ? <p>本次模拟扫描返回无码；这不是实车健康结论。</p> : <ul>{dtcs.map((d, i) => <li key={i}><strong>{d.code}</strong> · ECU {d.ecu} · {({ stored: "已存储", pending: "待定", permanent: "永久", freeze: "冻结帧关联" })[d.status]} · {seconds(d.at)}</li>)}</ul>}
      {!!freeze.length && <><h3>故障发生时的模拟数据</h3><ul>{freeze.map((s, i) => <li key={i}>{s.label}：{s.value} {s.unit} · ECU {s.ecu}</li>)}</ul><p className="muted">冻结帧与当前实时数据分开保存。</p></>}
    </section>}
    {inner === "monitors" && <section className="panel"><h2>监控信息 · 模拟</h2>
      {!monitors.length ? <p>尚未收到有效监控响应。</p> : monitors.map((m, i) => <div key={i}><p>ECU {m.ecu} · 排放故障灯（MIL）：{m.mil ? "亮" : "灭"} · 上报码数：{m.count}</p><p className="muted">监控原始字节：{m.raw.map((b) => b.toString(16).padStart(2, "0").toUpperCase()).join(" ")}。各项就绪位解释尚未启用，不推断通过或失败。</p></div>)}
    </section>}
    {inner === "connection" && <section className="panel"><h2>采集身份 · 模拟</h2>{vins.length ? vins.map((v, i) => <p key={i}>演示 VIN：{v.value} · ECU {v.ecu}</p>) : <p>尚未取得模拟车辆身份。</p>}<p className="muted">不会覆盖生产车辆身份表。</p></section>}
    {!!observations.length && ["connection", "faults"].includes(inner) && <details className="panel obd-evidence"><summary>采集证据与任务完成情况（{observations.length} 条）</summary>
      <ul className="plain-list">{observations.map((o) => <li key={o.seq}><strong>{seconds(o.tMs)} · {TASKS[o.command] ?? `实时请求 ${o.command}`}</strong> · {OUTCOME[o.outcome]}
        <details><summary>查看原始响应{o.detail ? ` · ${o.detail}` : ""}</summary><pre>{o.raw || "未收到响应字节"}</pre></details>
      </li>)}</ul>
    </details>}
  </div>;
}
