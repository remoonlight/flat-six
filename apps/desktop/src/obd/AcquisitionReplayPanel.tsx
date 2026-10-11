import { useEffect, useRef, useState } from "react";
import { api, type ReplayConnectionEvent, type ReplayResult, type ReplaySeries, type ReplaySummary } from "../api";

const seconds = (ms: number) => `${(ms / 1000).toFixed(3)} s`;
const errors: Record<string, string> = { replay_unsupported_format: "请选择本项目保存的标准、厂商或内网采集记录。",
  replay_no_samples: "记录中没有采集结果。", replay_count_mismatch: "文件中的结果数量不一致。",
  replay_invalid_json: "文件不是完整的 JSON，可能尚未写完或已损坏。",
  replay_invalid_sample: "记录中存在无效数值或时间，无法完整回放。",
  replay_invalid_connection_events: "记录中的通信事件数量、来源或顺序不一致，无法完整回放。",
  replay_invalid_timestamp: "记录中的采样时间缺失或无效。", replay_parameter_conflict: "同一参数的名称或单位存在冲突。" };
function connectionLabel(event: ReplayConnectionEvent) {
  switch (event.phase) {
    case "interrupted": return "通信中断";
    case "retrying": return `第 ${event.attempt}/3 次重连`;
    case "qualified": return "身份一致，等待有效回答";
    case "recovered": return "已收到有效回答，通信恢复";
    case "retry-failed": return `第 ${event.attempt}/3 次重连失败`;
    case "failed": return "恢复终止";
  }
}

function ReplayGraph({ series, duration }: { series: ReplaySeries; duration: number }) {
  if (!series.points.length) return <p className="muted">当前位置之前没有数值曲线；文本值见下方。</p>;
  const values = series.points.map(p => Number(p.value)), low = Math.min(...values), high = Math.max(...values);
  const scale = Math.max(1, Math.abs(low), Math.abs(high)), span = high / scale - low / scale;
  const pad = span ? span * .08 : .05;
  return <svg viewBox="0 0 480 170" role="img" aria-label={`${series.name}历史采样点，${series.unit}`} className="eng-chart">
    <line x1="54" x2="460" y1="138" y2="138" className="eng-chart-grid" />
    <text x="50" y="23" textAnchor="end">{high.toFixed(2)}</text><text x="50" y="138" textAnchor="end">{low.toFixed(2)}</text>
    {series.points.map(p => <circle key={p.index} cx={54 + (duration ? p.timeMs / duration * 406 : 0)}
      cy={138 - (Number(p.value) / scale - low / scale + pad) / (span + 2 * pad) * 120} r="2.5" className="eng-chart-point">
      <title>{p.display} {p.unit} · {seconds(p.timeMs)} · {p.timestamp}</title></circle>)}
    <text x="54" y="161">0 s</text><text x="460" y="161" textAnchor="end">{seconds(duration)}</text>
  </svg>;
}

export function AcquisitionReplayPanel({ locked }: { locked: boolean }) {
  const [id, setId] = useState(""), [summary, setSummary] = useState<ReplaySummary | null>(null);
  const [selected, setSelected] = useState<string[]>([]), [position, setPosition] = useState(0), [offset, setOffset] = useState(0);
  const [view, setView] = useState<ReplayResult | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [playing, setPlaying] = useState(false), [speed, setSpeed] = useState(1);
  const [filter, setFilter] = useState("");
  const [seriesOffset, setSeriesOffset] = useState(0);
  const [connectionOffset, setConnectionOffset] = useState(0);
  const owned = useRef(""), mounted = useRef(true), previousOffsets = useRef<number[]>([]);
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false;
    if (owned.current) void api().acquisitionReplay?.({ action: "discard", recordingId: owned.current });
  }; }, []);
  useEffect(() => {
    if (!id || busy) return;
    let active = true;
    void api().acquisitionReplay?.({ action: "view", recordingId: id, parameterIds: selected, positionMs: position, offset, seriesOffset, connectionOffset }).then(result => {
      if (!active || owned.current !== id) return;
      if (!result.ok) { setError(errors[result.error || ""] || `无法回放：${result.error || "未知错误"}`); setPlaying(false); }
      else setView(result);
    }).catch(() => { if (active && owned.current === id) { setError("无法读取回放结果，请重新打开记录。"); setPlaying(false); } });
    return () => { active = false; };
  }, [id, selected, position, offset, seriesOffset, connectionOffset, busy]);
  useEffect(() => {
    if (!playing || !summary) return;
    let last = performance.now();
    const timer = setInterval(() => {
      const now = performance.now(), delta = (now - last) * speed; last = now;
      setPosition(prior => Math.min(summary.durationMs, prior + delta));
    }, 100);
    return () => clearInterval(timer);
  }, [playing, summary, speed]);
  useEffect(() => { if (summary && position >= summary.durationMs) setPlaying(false); }, [position, summary]);
  async function open() {
    const fn = api().acquisitionReplay; if (!fn || busy || locked) return;
    setPlaying(false); setBusy(true); setError("");
    try {
      const result = await fn({ action: "open" });
      if (!mounted.current) { if (result.recordingId) await fn({ action: "discard", recordingId: result.recordingId }); return; }
      if (result.canceled) return;
      if (!result.ok || !result.summary || !result.recordingId) { setError(errors[result.error || ""] || `打开失败：${result.error || "未知错误"}`); return; }
      owned.current = result.recordingId; setId(result.recordingId); setSummary(result.summary);
      setError("");
      setSelected(result.summary.parameters.map(p => p.id)); setPosition(0); setOffset(0); setSeriesOffset(0); setConnectionOffset(0); setView(null);
      setFilter("");
      previousOffsets.current = [];
    } catch { if (mounted.current) setError("打开记录失败，请重试。"); }
    finally { if (mounted.current) setBusy(false); }
  }
  function close() {
    if (id) void api().acquisitionReplay?.({ action: "discard", recordingId: id });
    owned.current = ""; setId(""); setSummary(null); setView(null); setPlaying(false); setError("");
  }
  return <section className="panel acquisition-replay" data-testid="acquisition-replay">
    <div className="eng-section-head"><h3>采集记录回放</h3><div className="row">
      <button type="button" data-testid="replay-open" disabled={busy || locked || !api().acquisitionReplay} onClick={() => void open()}>{busy ? "正在打开…" : "打开采集记录"}</button>
      {summary ? <button type="button" disabled={busy} onClick={close}>关闭回放</button> : null}
    </div></div>
    {error ? <p role="alert" data-testid="replay-error">{error}</p> : null}
    {summary ? <>
      <p data-testid="replay-summary">历史采集 · {summary.fileName} · {summary.sampleCount} 条 · {summary.connectionEventCount} 个通信事件 · {seconds(summary.durationMs)} · {summary.simulation ? "模拟来源" : summary.vehicleDataCollected ? "文件声明车辆采集，未经本次核验" : "文件来源待核对"}</p>
      <p className="muted">{summary.profileId} {summary.sourceKind} · 原结束原因：{summary.stopReason || "未记录"}{summary.error ? ` · ${summary.error}` : ""}</p>
      {!summary.retentionComplete ? <p role="status">该文件未声明完整保留，回放仅覆盖文件内已有结果。</p> : null}
      {summary.timeRegressed ? <p role="status">记录的时间曾倒退；明细保留原顺序，时间轴按记录时间排列。</p> : null}
      <details><summary>选择回放参数（已选 {selected.length} 项；完整目录 {summary.parameters.length} 项）</summary>
        <label>查找参数 <input aria-label="查找回放参数" value={filter} onChange={e => setFilter(e.target.value)} /></label>
        <div className="row">{["全选", "取消全选"].map((label, i) => <button key={label} type="button" onClick={() => {
          setSelected(i ? [] : summary.parameters.map(p => p.id)); setOffset(0); setSeriesOffset(0); previousOffsets.current = []; setView(null);
        }}>{label}</button>)}</div>
        <div className="eng-parameters" data-testid="replay-parameters">{summary.parameters.filter(p => `${p.name} ${p.id}`.toLowerCase().includes(filter.toLowerCase())).map(p => <label key={p.id} className="eng-parameter">
          <input type="checkbox" checked={selected.includes(p.id)} onChange={event => {
            setSelected(event.target.checked ? [...selected, p.id] : selected.filter(x => x !== p.id));
            setOffset(0); setSeriesOffset(0); previousOffsets.current = []; setView(null);
          }} /><span>{p.name}</span><small>{p.unit} · {p.count} 条</small>
        </label>)}</div>
      </details>
      <div className="eng-toolbar">
        <button type="button" data-testid="replay-play" disabled={(!selected.length && !summary.connectionEventCount) || !summary.durationMs} onClick={() => {
          if (!playing && position >= summary.durationMs) setPosition(0); setPlaying(!playing);
        }}>{playing ? "暂停回放" : "播放"}</button>
        <label>速度 <select aria-label="回放速度" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value="1">1×</option><option value="2">2×</option><option value="5">5×</option></select></label>
        <label className="replay-time">时间 <input type="range" data-testid="replay-position" aria-label="回放时间" min="0" max={summary.durationMs || 1} step="any" value={position}
          onChange={e => { setPlaying(false); setPosition(Number(e.target.value)); }} /></label>
        <output data-testid="replay-time">{seconds(position)} / {seconds(summary.durationMs)}</output>
      </div>
      <p className="muted">时间轴从文件最早的数值或通信事件开始，保留实际间隔与空档，不插值。大数据曲线显示分时段极值概览；完整原值见全程明细。厂商时间按文件保留主机响应观察或旧版处理时间，内网为电脑接收时间，均不代表 ECU 内部采样周期。</p>
      {summary.connectionEventCount ? <section data-testid="replay-connections">
        <h4>全程通信事件</h4>
        <p data-testid="replay-connection-current" role="status">当前位置的历史状态：{view?.currentConnectionEvent
          ? `${connectionLabel(view.currentConnectionEvent)} · 连接批次 ${view.currentConnectionEvent.connectionEpoch}${view.currentConnectionEvent.reason ? ` · ${view.currentConnectionEvent.reason}` : ""}` : "尚无通信事件"}
          {position >= summary.durationMs && view?.currentConnectionEvent && !["recovered", "failed"].includes(view.currentConnectionEvent.phase) ? " · 本批结束时恢复结果未确认" : ""}</p>
        {!summary.sampleCount ? <p data-testid="replay-events-only">本文件没有车辆数值，仅保留通信事件和结束原因。</p> : null}
        <div className="row"><span data-testid="replay-connection-page">{view?.connectionTotal ? `${connectionOffset + 1}–${view.nextConnectionOffset} / ${view.connectionTotal}` : "0 条"}</span>
          <button type="button" data-testid="replay-connection-prev" disabled={!connectionOffset || !view} onClick={() => { setConnectionOffset(Math.max(0, connectionOffset - 100)); setView(null); }}>上一页事件</button>
          <button type="button" data-testid="replay-connection-next" disabled={!view || (view.nextConnectionOffset || 0) >= (view.connectionTotal || 0)} onClick={() => { setConnectionOffset(view!.nextConnectionOffset!); setView(null); }}>下一页事件</button>
        </div>
        <div className="replay-table"><table><thead><tr><th>原顺序</th><th>时间</th><th>事件</th><th>连接批次</th><th>原因</th><th>原经过时间</th><th>原时间戳</th></tr></thead>
          <tbody data-testid="replay-connection-rows">{view?.connectionEvents?.map(event => <tr key={event.sequence}>
            <td>{event.sequence}</td><td>{seconds(event.timeMs)}</td><td>{connectionLabel(event)}</td><td>{event.connectionEpoch}</td><td>{event.reason || "—"}</td><td>{seconds(event.elapsedMs)}</td><td>{event.observedUtc}</td>
          </tr>)}</tbody>
        </table></div>
      </section> : null}
      {selected.length > 12 ? <div className="row"><span>参数显示 {seriesOffset + 1}–{Math.min(seriesOffset + 12, selected.length)} / {selected.length}</span>
        <button type="button" data-testid="replay-series-prev" disabled={!seriesOffset} onClick={() => setSeriesOffset(Math.max(0, seriesOffset - 12))}>上一组参数</button>
        <button type="button" data-testid="replay-series-next" disabled={seriesOffset + 12 >= selected.length} onClick={() => setSeriesOffset(seriesOffset + 12)}>下一组参数</button>
      </div> : null}
      <div className="replay-series">{view?.series?.map(series => <article key={series.id} data-testid="replay-series">
        <h4>{series.name} · {series.current ? `${series.current.display} ${series.unit}` : "尚无样本"}</h4>
        <p className="muted">{series.current ? `${seconds(series.current.timeMs)} · ${series.current.timestamp}` : ""} · {series.numericCount} 个数值 / {series.plottedCount} 个概览点</p>
        <ReplayGraph series={series} duration={summary.durationMs} />
      </article>)}</div>
      <h4>全程明细</h4>
      <div className="row"><span data-testid="replay-page">{view?.total ? `${offset + 1}–${view.nextOffset} / ${view.total}` : "0 条"}</span>
        <button type="button" data-testid="replay-prev" disabled={!view || !previousOffsets.current.length} onClick={() => { setOffset(previousOffsets.current.pop() || 0); setView(null); }}>上一页</button>
        <button type="button" data-testid="replay-next" disabled={!view || (view.nextOffset || 0) >= (view.total || 0)} onClick={() => {
          previousOffsets.current.push(offset); setOffset(view!.nextOffset!); setView(null);
        }}>下一页</button></div>
      <div className="replay-table"><table><thead><tr><th>原顺序</th><th>时间</th><th>参数</th><th>原显示值</th><th>轮次</th><th>原时间戳</th><th>连接批次</th></tr></thead>
        <tbody data-testid="replay-rows">{view?.rows?.map(row => <tr key={row.index}><td>{row.index + 1}</td><td title={row.sourceElapsedMs === null ? undefined : `原经过时间 ${seconds(row.sourceElapsedMs)}`}>{seconds(row.timeMs)}</td><td>{row.name}</td><td>{row.display} {row.unit}</td><td>{row.cycle ?? "—"}</td><td>{row.timestamp}{row.timestampUs !== undefined ? ` · ${row.timestampUs} µs` : ""}</td><td>{row.connectionEpoch ?? "—"}</td></tr>)}</tbody>
      </table></div>
    </> : <p className="muted">打开已保存的标准数据、厂商参数或内网接收 JSON，选择参数后按原时间播放。</p>}
  </section>;
}
