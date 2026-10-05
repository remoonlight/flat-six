import { useEffect, useState } from "react";
import { api, hasDesktopApi } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
type Snapshot = Awaited<ReturnType<NonNullable<ReturnType<typeof api>["diagnosticCanRecording"]>>>;
export function DiagnosticCanRecordingControls({ simulation }: { simulation: boolean }) {
  const [status, setStatus] = useState<Snapshot | null>(null), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const available = hasDesktopApi() && !!api().diagnosticCanRecording;
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const result = available ? await api().diagnosticCanRecording?.({ action: "status" }) : null; if (active && result) setStatus(result); }
      finally { if (active) timer = setTimeout(() => void poll().catch(() => {}), 500); }
    };
    void poll().catch(() => {});
    return () => { active = false; clearTimeout(timer); };
  }, []);
  async function act(action: "start" | "stop") {
    if (busy || !api().diagnosticCanRecording) return;
    setBusy(true); setMessage("");
    try { const result = await api().diagnosticCanRecording!({ action, simulation }); setStatus(result); if (!result.ok) setMessage(diagnosticMessage(result.error)); }
    catch (error) { setMessage(diagnosticMessage(error)); } finally { setBusy(false); }
  }
  return <div data-testid="diagnostic-can-recording">
    <button type="button" disabled={busy || !!status?.recording?.active || !available} onClick={() => void act("start")}>开始记录{simulation ? "模拟" : "车辆"}原始接收帧</button>{" "}
    <button type="button" disabled={busy || !status?.recording?.active} onClick={() => void act("stop")}>结束原始帧记录</button>
    <p role="status">{status?.recording ? `${status.recording.simulation ? "模拟" : "车辆"}记录${status.recording.active ? "进行中" : "已结束"}：${status.recording.frameCount} 帧 · ${status.recording.file}` : "原始帧记录默认关闭。"}</p>
    <p className="muted">只流式保存开始与结束之间，诊断头实际报告的完整 CAN 接收帧。不能用诊断 PDU 或推测的发送帧生成抓包；时间来自电脑接收时刻。</p>
    {message && <p role="alert">{message}</p>}{status?.recording?.error && <p role="alert">记录未完整结束：{diagnosticMessage(status.recording.error)}</p>}
    {status?.recording?.reason === "source-changed" && <p role="status">数据来源发生改变，已结束上一份记录。请按新的来源重新开始记录。</p>}
  </div>;
}
