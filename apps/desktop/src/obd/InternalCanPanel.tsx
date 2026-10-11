import { useState } from "react";
import type { ObdConnectionRequest, ObdConnectionResult } from "../api";
import { obdFunctionLabel } from "./function-label";

export function InternalCanPanel({ status, busy, invoke }: {
  status: ObdConnectionResult | null;
  busy: boolean;
  invoke: (request: ObdConnectionRequest) => Promise<ObdConnectionResult | undefined>;
}) {
  const [message, setMessage] = useState("");
  const data = status?.internal;
  async function action(request: ObdConnectionRequest) {
    const result = await invoke(request);
    if (!result) return;
    const saved = result.saved as { ok?: boolean; saved?: boolean; filePath?: string; error?: string } | undefined;
    setMessage(result.ok === false ? String(result.error || "操作失败") : saved?.ok === false ? `保存失败：${saved.error}` :
      saved?.saved ? `已保存本批结果：${saved.filePath}` : result.recording?.error ? `记录未完整结束：${result.recording.error}` : "");
  }
  return <section className="panel" data-testid="internal-can-panel">
    <h3>{status?.canNetwork ? ({ drive: "驱动 CAN", chassis: "底盘 CAN", comfort: "舒适性 CAN", crash: "碰撞 CAN", adas: "ADAS CAN" })[status.canNetwork] : "未选择 CAN"} · {obdFunctionLabel("持续接收", status?.internalSupported === true)}</h3>
    <p className="muted">数据来自所选接线。单次最多十分钟；切换页面或重连不重置期限，断线期间不补造数据。</p>
    {!status?.internalSupported ? <p className="muted">此 CAN 与诊断头尚无已核实的原始监听配置，连接功能不可执行。</p> : null}
    <p data-testid="internal-can-count">已接收 {data?.frameCount || 0} 帧；本批完整保留 {data?.retainedFrames || 0} 帧，点击保存保留本次全过程。</p>
    {data?.stopReason === "time-limit" ? <p role="status" data-testid="internal-time-limit">本批已到十分钟，{data.receiveState === "standby" ? "已停止接收并保留诊断头连接" : "正在停止接收"}。已采结果保留，下一批须手动开始。</p> : data?.batchClosed ? <p role="status">本批接收已结束，已有结果仍可完整保存；下一批须手动开始。</p> : null}
    <h4>{obdFunctionLabel("广播车辆数值", false)}</h4>
    <p className="muted">当前没有通过独立参考验证的广播数值定义，仅显示实际收到的报文。接收时间为电脑收到数据的时间。</p>
    <div className="row">
      <button type="button" data-testid="internal-save" disabled={busy || !data?.retainedFrames} onClick={() => void action({ action: "save-result" })}>保存结果</button>
      <button type="button" data-testid="internal-new-batch" disabled={busy || !status?.connected} onClick={() => void action({ action: "new-batch" })}>开始下一批</button>
    </div>
    {message ? <p role="status">{message}</p> : null}
    {data?.interruptions?.length ? <p className="muted" data-testid="internal-gaps">本批发生 {data.interruptions.length} 次中断，导出保留中断时间与原因。</p> : null}
    <ul data-testid="internal-frames" className="eng-pid-list">
      {(data?.latest || []).slice(-80).map((frame) => <li key={`${frame.canId}-${frame.extended}`}>
        <strong>0x{frame.canId.toString(16).toUpperCase().padStart(frame.extended ? 8 : 3, "0")}</strong>
        <code>{frame.dataHex.match(/.{2}/g)?.join(" ")}</code>
        <span>{new Date(frame.timestampUs / 1000).toLocaleTimeString("zh-CN", { hour12: false })}</span>
      </li>)}
    </ul>
  </section>;
}
