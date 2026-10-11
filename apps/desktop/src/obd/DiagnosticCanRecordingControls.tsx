import { useEffect, useRef, useState } from "react";
import { api, hasDesktopApi, type ObdConnectionResult } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { obdFunctionLabel } from "./function-label";

type Snapshot = Awaited<ReturnType<NonNullable<ReturnType<typeof api>["diagnosticCanRecording"]>>>;

/** Mounted by App, outside page lifetimes. Starting a recording never starts vehicle IO. */
export function ObdRecordingControls() {
  const [status, setStatus] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<ObdConnectionResult | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [statusError, setStatusError] = useState(false);
  const [simulation, setSimulation] = useState(false);
  const [layer, setLayer] = useState<"can" | "application">("application");
  const generation = useRef(0), acting = useRef(false);
  const available = hasDesktopApi() && !!api().diagnosticCanRecording;
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const current = generation.current;
      try {
        const [diagnostic, head] = await Promise.all([
          available ? api().diagnosticCanRecording?.({ action: "status" }) : null,
          available ? api().obdConnection?.({ action: "status" }) : null,
        ]);
        if (active && current === generation.current && !acting.current) {
          setStatusError(!diagnostic?.ok || !head?.ok);
          if (diagnostic?.ok) setStatus(diagnostic);
          if (head?.ok) setConnection(head);
        }
      } catch { if (active) setStatusError(true); }
      finally { if (active) timer = setTimeout(() => void poll(), 500); }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [available]);

  async function act(source: "diagnostic" | "internal", action: "start" | "stop") {
    if (acting.current) return;
    acting.current = true; generation.current++; setBusy(true); setMessage("");
    try {
      if (source === "diagnostic") {
        const result = await api().diagnosticCanRecording?.(action === "start" ? { action, simulation, layer } : { action });
        if (result) {
          if ("recording" in result) setStatus(result);
          if (!result.ok) setMessage(diagnosticMessage(result.error));
        }
      } else {
        const result = await api().obdConnection?.({ action: action === "start" ? "record-start" : "record-stop" });
        if (result) {
          setConnection(result);
          if (!result.ok) setMessage(diagnosticMessage(result.error));
        }
      }
    } catch (error) { setMessage(diagnosticMessage(error)); }
    finally { generation.current++; acting.current = false; setBusy(false); }
  }

  if (!available) return null;
  const diagnostic = status?.recording, internal = connection?.recording;
  const anyActive = !!diagnostic?.active || !!internal?.active;
  const internalPurpose = connection?.purpose === "internal";
  const showDiagnostic = !internalPurpose || !!diagnostic?.active;
  const showInternal = internalPurpose || !!internal?.active;
  const selectedSimulation = diagnostic?.active ? diagnostic.simulation : simulation;
  const selectedLayer = diagnostic?.active ? diagnostic.layer || "can" : layer;
  const layerImplemented = (value: "can" | "application") => selectedSimulation || connection?.model == null ||
    (value === "application" ? ["vLinker", "OBDLink MX+", "VNCI"] : ["vLinker", "OBDLink MX+"]).includes(connection.model);
  return <section className="obd-recording-bar" data-testid="obd-recording-controls" aria-label="原始记录">
    <strong>原始记录</strong>
    {showDiagnostic && <div className="obd-recording-group" data-testid="diagnostic-can-recording">
      <label>来源 <select aria-label="诊断记录来源" value={selectedSimulation ? "simulation" : "live"} disabled={busy || anyActive}
        onChange={event => setSimulation(event.target.value === "simulation")}>
        <option value="live">车辆诊断</option><option value="simulation">模拟诊断</option>
      </select></label>
      <label>内容 <select aria-label="诊断报文记录内容" value={selectedLayer} disabled={busy || anyActive}
        onChange={event => setLayer(event.target.value as "can" | "application")}>
        <option value="application">{obdFunctionLabel("完整诊断应用响应", layerImplemented("application"))}</option><option value="can">{obdFunctionLabel("原始 CAN 接收帧", layerImplemented("can"))}</option>
      </select></label>
      <button type="button" disabled={busy || anyActive} onClick={() => void act("diagnostic", "start")}>{obdFunctionLabel(`开始记录${selectedSimulation ? "模拟" : "车辆"}${selectedLayer === "can" ? "原始接收帧" : "完整诊断响应"}`, layerImplemented(selectedLayer))}</button>
      <button type="button" disabled={busy || !diagnostic?.active} onClick={() => void act("diagnostic", "stop")}>结束{diagnostic?.layer === "application" ? "诊断响应" : "原始帧"}记录</button>
      <span role="status" data-testid="diagnostic-record-state">{diagnostic ? `${diagnostic.simulation ? "模拟" : "车辆"}${diagnostic.layer === "application" ? "应用响应" : "CAN帧"}记录${diagnostic.active ? "进行中" : "已结束"}：${diagnostic.frameCount} 条${diagnostic.reason === "time-limit" ? " · 已到十分钟" : ""}` : "默认关闭"}</span>
    </div>}
    {showInternal && <div className="obd-recording-group" data-testid="internal-recording-controls">
      <span>内网 CAN · {connection?.canNetwork ? ({ drive: "驱动", chassis: "底盘", comfort: "舒适性", crash: "碰撞", adas: "ADAS" })[connection.canNetwork] : "未选择网络"}</span>
      <button type="button" data-testid="internal-record-start" disabled={busy || !connection?.connected || connection.internal?.receiveState !== "receiving" || anyActive} onClick={() => void act("internal", "start")}>{obdFunctionLabel("开始记录原始报文", connection?.internalSupported === true)}</button>
      <button type="button" data-testid="internal-record-stop" disabled={busy || !internal?.active} onClick={() => void act("internal", "stop")}>结束内网记录</button>
      <span role="status" data-testid="internal-record-state">{internal ? `${internal.active ? "正在记录" : "记录已结束"}：${internal.frameCount} 帧${internal.reason === "time-limit" ? " · 已到十分钟" : ""}` : "默认关闭"}</span>
    </div>}
    {message && <p role="alert">{message}</p>}
    {statusError && <p role="alert">记录状态暂时无法核对，当前显示上次结果。可重试结束记录；确认文件已关闭后再开始下一份。</p>}
    {diagnostic?.error && <p role="alert">诊断记录未完整结束：{diagnosticMessage(diagnostic.error)}</p>}
    {internal?.error && <p role="alert">内网记录未完整结束：{diagnosticMessage(internal.error)}</p>}
    {showDiagnostic && diagnostic?.reason === "source-changed" && <p role="status">数据来源发生改变，已结束上一份记录。请按新的来源重新开始记录。</p>}
    <details className="obd-recording-info"><summary>记录说明与文件</summary>
      <p>记录默认关闭，显式开始后只保存开启期间实际收到的内容；单次最多十分钟，切页不重置期限。记录不会启动诊断或接收，停止记录不停止诊断。车辆与模拟来源须分别选择，来源变化结束当前记录。</p>
      <p>原始 CAN 接收帧仅保留诊断头报告的 RX；完整诊断应用响应保留重组或 D-PDU 字节，包含身份、否定及未通过解码的响应，不还原为 CAN 帧。时间为电脑观察时间。内网记录在接收停止时同时结束。</p>
      {diagnostic?.file && <p>诊断记录文件：{diagnostic.file}</p>}
      {internal?.file && <p>内网记录文件：{internal.file}</p>}
    </details>
  </section>;
}
