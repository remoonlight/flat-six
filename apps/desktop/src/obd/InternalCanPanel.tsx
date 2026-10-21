import { useEffect, useState } from "react";
import type { ObdConnectionRequest, ObdConnectionResult } from "../api";
import { obdFunctionLabel } from "./function-label";
import { internalReceiveFailure, parameterSaveFailure } from "./diagnostic-messages";
import { LiveParameterCharts } from "./LiveParameterCharts";
import { mainlandUnit, mainlandValueText } from "./live-unit-logic.mjs";
import "../engine-session.css";

export function InternalCanPanel({ status, busy, invoke }: {
  status: ObdConnectionResult | null;
  busy: boolean;
  invoke: (request: ObdConnectionRequest) => Promise<ObdConnectionResult | undefined>;
}) {
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [display, setDisplay] = useState<"text" | "graph">("text");
  const [selectionOpen, setSelectionOpen] = useState(true);
  const data = status?.internal;
  const parameters = data?.parameters || [];
  const visible = parameters.filter(parameter => selected.includes(parameter.id));
  useEffect(() => { setSelected([]); setDisplay("text"); setMessage(""); setSelectionOpen(true); }, [status?.canNetwork, status?.selectedDeviceId]);
  const receiveState = data?.receiveState || "idle";
  const canStart = receiveState === "idle" || receiveState === "standby";
  const interrupted = ["starting", "receiving"].includes(receiveState) && !status?.connected;
  const reconnecting = interrupted && ["connecting", "reconnecting"].includes(status?.linkState || "");
  async function action(request: ObdConnectionRequest) {
    if (request.action === "new-batch") { setDisplay("text"); setSelectionOpen(false); setMessage(""); }
    const result = await invoke(request);
    const failure = (error: unknown) => request.action === "save-result" ? parameterSaveFailure(error).message : internalReceiveFailure(error);
    if (!result) { setMessage(failure(null)); return; }
    const saved = result.saved as { ok?: boolean; saved?: boolean; filePath?: string; error?: string } | undefined;
    setMessage(result.ok === false ? failure(result.error) : saved?.ok === false ? failure(saved.error) :
      saved?.saved ? `已保存参数：${saved.filePath}` : result.recording?.error ? failure(result.recording.error) : "");
  }
  return <section className="panel eng" data-testid="internal-can-panel">
    <h3>{status?.canNetwork ? ({ drive: "驱动 CAN", chassis: "底盘 CAN", comfort: "舒适性 CAN", crash: "碰撞 CAN", adas: "ADAS CAN" })[status.canNetwork] : "未选择 CAN"} · {obdFunctionLabel("持续接收", status?.internalSupported === true)}</h3>
    <p className="muted">数据来自所选接线。单次最多十分钟；切换页面或重连不重置期限，断线期间不补造数据。</p>
    {!status?.internalSupported ? <p className="muted">此 CAN 与诊断头尚无已核实的原始监听配置，连接功能不可执行。</p> : null}
    {receiveState === "idle" ? <p role="status">诊断头已连接后，点击开始接收本批数据。</p> : receiveState === "stopping" ? <p role="status">正在停止接收，等待诊断头确认。</p> : null}
    {interrupted ? <p role="status" data-testid="internal-reconnect-status">{reconnecting ? "连接已中断，正在自动重连；已有数据保留，计时继续。" : "接收已中断，请停止本批后检查连接；已有数据保留。"}</p> : receiveState === "starting" ? <p role="status">正在开始接收。</p> : receiveState === "receiving" ? <p role="status">正在接收本批数据。</p> : null}
    {data?.stopReason === "time-limit" ? <p role="status" data-testid="internal-time-limit">本批已到十分钟，{data.receiveState === "standby" ? status?.connected ? "已停止接收并保留诊断头连接" : "已停止接收，诊断头未连接" : "正在停止接收"}。已采结果保留，下一批须手动开始。</p> : data?.batchClosed ? <p role="status">本批接收已结束，已有结果保留；下一批须手动开始。</p> : null}
    <h4>{obdFunctionLabel("广播车辆数值", !!data?.verifiedSignalCount)}</h4>
    {!data?.verifiedSignalCount ? <p className="muted">{data?.signalDefinitionError ? "车辆参数资料暂不可用，请检查资料后重新开始。" : "当前接线尚无已核实的车辆参数定义，暂不能显示或保存车辆参数。"}</p> : <>
      <details open={selectionOpen} onToggle={event => setSelectionOpen(event.currentTarget.open)} data-testid="internal-selection">
        <summary>选择显示参数（已选 {visible.length} 项）</summary>
        <div className="eng-parameters">{parameters.map(parameter => <label className="eng-parameter" key={parameter.id}>
          <input type="checkbox" data-testid={`internal-select-${parameter.id}`} checked={selected.includes(parameter.id)}
            onChange={event => setSelected(previous => event.target.checked ? [...previous, parameter.id] : previous.filter(id => id !== parameter.id))} />
          <span>{parameter.name}{parameter.unit ? `（${mainlandUnit(parameter.unit).unit}）` : ""}</span>
        </label>)}</div>
      </details>
      <div className="eng-display-modes" aria-label="内网数据视图">
        <button type="button" data-testid="internal-display-text" aria-pressed={display === "text"} onClick={() => setDisplay("text")}>数值</button>
        <button type="button" data-testid="internal-display-graph" aria-pressed={display === "graph"} onClick={() => setDisplay("graph")}>图形</button>
      </div>
      {!visible.length ? <p className="muted" data-testid="internal-no-selection">尚未选择显示参数</p> : display === "text" ?
        <dl className="eng-pids" data-testid="internal-values">{visible.map(parameter => {
          const converted = mainlandValueText(parameter.value, parameter.unit);
          const unit = parameter.value === null ? mainlandUnit(parameter.unit).unit : converted.unit;
          const stale = parameter.invalid || interrupted || parameter.lastElapsedMs !== null && (data.plotElapsedMs ?? 0) - parameter.lastElapsedMs > 60000;
          return <div className="eng-pid" key={parameter.id}><dt>{parameter.name}</dt><dd data-parameter-id={parameter.id}>
            {parameter.value === null ? "--" : converted.text}{unit ? ` ${unit}` : ""}{stale && parameter.value !== null ? " · 未更新" : ""}
          </dd></div>;
        })}</dl> : <LiveParameterCharts currentElapsedMs={data.plotElapsedMs} series={visible.map(parameter => ({
          id: parameter.id, name: parameter.name, unit: parameter.unit,
          latest: parameter.value === null ? "--" : mainlandValueText(parameter.value, parameter.unit).text,
          latestValue: parameter.value ?? undefined,
          latestSuffix: parameter.value !== null && (parameter.invalid || interrupted || parameter.lastElapsedMs !== null && (data.plotElapsedMs ?? 0) - parameter.lastElapsedMs > 60000) ? " · 未更新" : "",
          segments: parameter.segments,
        }))} />}
      <p className="muted" data-testid="internal-parameter-count">本批已保留 {data.parameterSampleCount || 0} 条参数数据，保存包含全部已识别参数。</p>
    </>}
    <div className="row">
      <button type="button" data-testid="internal-save" disabled={busy || receiveState !== "standby" || !data?.batchClosed || !data?.parameterSampleCount}
        onClick={() => void action({ action: "save-result" })}>{obdFunctionLabel("保存记录", !!data?.verifiedSignalCount)}</button>
      <button type="button" data-testid="internal-new-batch" disabled={busy || !status?.connected || !canStart} onClick={() => void action({ action: "new-batch" })}>{data?.batchClosed ? "重新开始" : "开始"}</button>
      <button type="button" data-testid="internal-stop" disabled={busy || !["starting", "receiving"].includes(receiveState)} onClick={() => void action({ action: "receive-stop" })}>停止</button>
    </div>
    {message ? <p role="status" data-testid="internal-action-message">{message}</p> : null}
    {data?.interruptions?.length ? <p className="muted" data-testid="internal-gaps">本批发生 {data.interruptions.length} 次中断，已保留中断记录，不补造数值。</p> : null}

  </section>;
}
