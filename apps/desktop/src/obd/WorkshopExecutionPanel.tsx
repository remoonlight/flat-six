import { useEffect, useState } from "react";
import type { WorkshopExecutionRequest, WorkshopExecutionResult } from "../api";
import { obdFunctionLabel } from "./function-label";

const labels = { pending: "未执行", running: "执行中", "waiting-manual": "等待人工操作", completed: "已完成", failed: "未完成", unconfirmed: "结果未确认", incomplete: "未完成" };
const errorMessage = (code?: string) => ({
  "vehicle-busy": "当前车辆操作尚未结束，请等待后再执行。",
  "workshop-recipe-unavailable": "此功能尚无已核实的完整作业与车辆客户端，当前不能执行。",
  "workshop-recipe-invalid": "当前功能的作业定义未通过核对，请检查资料后重试。",
  "workshop-not-waiting-manual": "当前步骤已变化，请按页面上的最新步骤继续。",
  "workshop-resume-unavailable": "当前流程不能从中断处继续，请查看结果及车辆条件。",
  "workshop-app-closing": "项目正在处理退出，请完成退出选择后再操作。",
} as Record<string, string>)[code || ""] || "暂时无法确认流程状态，请等待页面更新后再操作。";

export function WorkshopExecutionPanel({ functionId, generation, persistence, onReady, onVisibleChange }: {
  functionId: string; generation: "981" | "982"; persistence?: "ignition-cycle" | "persistent";
  onReady: (ready: boolean) => void; onVisibleChange: (visible: boolean) => void;
}) {
  const [capability, setCapability] = useState<WorkshopExecutionResult | null>(null);
  const [state, setState] = useState<WorkshopExecutionResult | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const job = state?.job?.functionId === functionId && state.job.generation === generation ? state.job : null;
  const active = !!job && !job.released;
  const terminal = !!job && job.released;
  const step = job?.steps[job.currentIndex];
  const api = () => window.porsche981?.workshopExecution;
  const context = () => ({ functionId, generation, ...(functionId === "psm-roller" ? { persistence } : {}) });

  useEffect(() => { onVisibleChange(!!job); }, [job?.id, onVisibleChange]);
  useEffect(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    setCapability(null); setState(null); setMessage(""); onReady(false);
    async function poll() {
      try {
        const invoke = api();
        if (!invoke) { if (!disposed) setMessage("维护执行接口尚未就绪。"); return; }
        const cap = await invoke({ action: "capability", ...context() });
        const result = await invoke({ action: "status" });
        if (disposed) return;
        setCapability(cap); onReady(cap.ok && cap.ready === true);
        if (result.ok) setState(result); else setMessage(errorMessage(result.error));
      } catch { if (!disposed) setMessage(errorMessage()); }
      if (!disposed) timer = setTimeout(() => void poll(), 400);
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); onVisibleChange(false); };
  }, [functionId, generation, persistence, onReady, onVisibleChange]);

  async function action(request: WorkshopExecutionRequest) {
    const invoke = api(); if (!invoke || pending) return;
    setPending(true); setMessage("");
    try {
      const result = await invoke(request);
      if (result.ok) setState(result); else setMessage(errorMessage(result.error));
    } catch { setMessage(errorMessage()); }
    finally { setPending(false); }
  }
  return <section className="workshop-execution" data-testid="workshop-execution">
    {!job ? <>
      {!capability?.ready && <p className="muted" data-testid="workshop-execution-unavailable">{capability?.reason || "此功能执行尚未完成。"}</p>}
      {capability?.ready && !capability.connectedForDiagnostics && <p className="muted">请先连接诊断头并选择诊断用途。</p>}
      <button type="button" data-testid="workshop-execution-start" disabled={pending || !capability?.ready || !capability.connectedForDiagnostics || capability.busy || state?.busy}
        onClick={() => void action({ action: "start", ...context() })}>{obdFunctionLabel("开始执行", capability?.ready === true)}</button>
    </> : <>
      {active ? <>
        <p role="status" data-testid="workshop-execution-state">{job.cancelRequested ? "等待安全停止" : ({
          preparing: "正在核对执行条件", running: "正在执行", "waiting-manual": "请完成当前人工操作", stopping: "正在安全停止",
          releasing: "正在完成任务收尾", "release-pending": "等待诊断头释放确认", "checking-resume": "正在核对续执行条件",
        } as Record<string, string>)[job.phase] || "正在处理"}</p>
        {step && !["releasing", "release-pending", "checking-resume"].includes(job.phase) && <div data-testid="workshop-current-step">
          <h4>第 {job.currentIndex + 1} 步／共 {job.steps.length} 步</h4><p>{step.label}</p>
        </div>}
        {job.phase === "waiting-manual" && <>
          {job.cancelRequested && <p className="muted">仍需完成当前人工步骤，才能进入已核实的安全停止位置。</p>}
          <button type="button" data-testid="workshop-manual-continue" disabled={pending}
            onClick={() => void action({ action: "continue", jobId: job.id, stepId: step?.id })}>已完成，继续</button>
        </>}
        <button type="button" data-testid="workshop-cancel" disabled={pending || job.cancelRequested || job.phase === "release-pending"}
          onClick={() => void action({ action: "cancel", jobId: job.id })}>{job.cancelRequested ? "等待安全停止" : "取消执行"}</button>
      </> : terminal && <div data-testid="workshop-execution-result">
        <h4>{job.phase === "completed" ? "操作完成" : job.phase === "cancelled" ? "本次流程已停止" : job.phase === "resume-ready" ? "续执行条件已通过" : "操作未完成"}</h4>
        <ol>{job.steps.map(item => <li key={item.id} data-step-id={item.id} data-step-state={item.status}>
          {item.label} · {labels[item.status]}
        </li>)}</ol>
        {job.cancelNotApplied && <p>本次流程已完成，取消未能提前生效。</p>}
        {job.phase === "failed" && job.resumeSupported && <button type="button" data-testid="workshop-check-resume" disabled={pending}
          onClick={() => void action({ action: "check-resume", jobId: job.id })}>从中断处继续</button>}
        {job.phase === "resume-ready" && <button type="button" data-testid="workshop-resume" disabled={pending}
          onClick={() => void action({ action: "resume", jobId: job.id })}>继续执行</button>}
        <button type="button" data-testid="workshop-return" disabled={pending} onClick={() => void action({ action: "dismiss", jobId: job.id })}>返回当前功能</button>
      </div>}
      {(job.releaseFailure || job.failure) && <p role="alert" data-testid="workshop-execution-failure">
        {job.phase === "resume-ready" ? "上次失败：" : ""}{(job.releaseFailure || job.failure)!.cause} {job.phase === "resume-ready" ? "" : (job.releaseFailure || job.failure)!.remedy}
      </p>}
      {job.resumeFailure && <p role="alert" data-testid="workshop-resume-failure">{job.resumeFailure.cause} {job.resumeFailure.remedy}</p>}
      {job.connectionWarning && <p role="status">{job.connectionWarning}</p>}
    </>}
    {message && <p role="alert" data-testid="workshop-execution-message">{message}</p>}
  </section>;
}
