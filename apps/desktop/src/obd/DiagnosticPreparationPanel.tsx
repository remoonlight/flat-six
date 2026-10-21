import { obdFunctionLabel } from "./function-label";
import { useEffect, useRef, useState } from "react";
import type { FirmwareVersionResult, FirmwareExecutionResult, FirmwareExecutionRequest } from "../api";

const failureMessage = (error?: string) => ({
  "vehicle-busy": "另一项车辆操作尚未结束，请等待后重新核对版本。",
  "firmware-current-changed": "车辆、诊断头或单元版本已变化，请重新核对版本。",
  "firmware-file-changed": "原厂文件已变化或缺失，请核对资料后重新选择版本。",
  "firmware-source-changed": "原厂作业或适用资料已变化，请重新核对版本。",
  "firmware-selection-stale": "本次版本选择已失效，请重新核对版本。",
  "firmware-current-unavailable": "无法取得本次完整车辆与有线连接信息，请检查连接和资料后重试。",
  "firmware-client-unavailable": "当前原厂作业尚未绑定完整执行和恢复客户端，不能开始刷写。",
  "firmware-app-closing": "项目正在处理退出，请完成退出选择后再操作。",
  "workshop-not-running": "本次作业已结束，请查看最新结果。",
  "workshop-not-waiting-manual": "当前步骤已变化，请按最新页面继续。",
  "workshop-job-unknown": "本次作业已变化，请返回当前单元后重新核对。",
  "workshop-owner-mismatch": "本次作业属于另一窗口，请回原窗口继续。",
} as Record<string, string>)[error || ""] || "暂时无法核对原厂版本，请检查连接和资料后重试。";

export function DiagnosticPreparationPanel({ ecu, programming = true, onFlowVisible }: { ecu: string; programming?: boolean; onFlowVisible?: (visible: boolean) => void }) {
  const [listing, setListing] = useState<FirmwareVersionResult | null>(null);
  const [review, setReview] = useState<FirmwareVersionResult | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);
  const selectionRevision = useRef(0);
  const [execution, setExecution] = useState<FirmwareExecutionResult | null>(null);
  const [acting, setActing] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const executionAction = useRef(false);
  const executionRevision = useRef(0);
  const job = execution?.job?.ecu === ecu ? execution.job : null;
  useEffect(() => { onFlowVisible?.(!!job); }, [job?.id, onFlowVisible]);
  useEffect(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (executionAction.current) { timer = setTimeout(() => void poll(), 400); return; }
      const revision = ++executionRevision.current;
      try {
        const state = await window.porsche981?.firmwareExecution?.({ action: "status" });
        if (!disposed && revision === executionRevision.current && state?.ok) setExecution(state);
      } catch { /* An action error is shown below; a poll never dispatches work. */ }
      if (!disposed) timer = setTimeout(() => void poll(), 400);
    }
    void poll();
    return () => { disposed = true; executionRevision.current++; clearTimeout(timer); onFlowVisible?.(false); };
  }, [ecu, onFlowVisible]);
  async function execute(request: FirmwareExecutionRequest) {
    if (acting) return;
    const revision = ++executionRevision.current;
    executionAction.current = true; setActing(true); setActionMessage("");
    try {
      const state = await window.porsche981?.firmwareExecution?.(request);
      if (revision !== executionRevision.current) return;
      if (state?.ok) {
        setExecution(state);
        if (request.action === "dismiss") { setReview(null); setSelected(""); setReload(value => value + 1); }
      } else setActionMessage(failureMessage(state?.error));
    } catch { if (revision === executionRevision.current) setActionMessage(failureMessage()); }
    finally { executionAction.current = false; setActing(false); }
  }
  useEffect(() => {
    let disposed = false, listId: string | undefined;
    selectionRevision.current++;
    setListing(null); setReview(null); setSelected(""); setMessage("");
    if (!programming) return;
    setBusy(true);
    void (async () => {
      try {
        const result = await window.porsche981?.firmwareVersions?.({ action: "list", ecu });
        listId = result?.listId;
        if (disposed) {
          if (listId) void window.porsche981?.firmwareVersions?.({ action: "clear", listId }).catch(() => {});
          return;
        }
        if (result?.ok) setListing(result); else setMessage(failureMessage(result?.error));
      } catch { if (!disposed) setMessage(failureMessage()); }
      finally { if (!disposed) setBusy(false); }
    })();
    return () => {
      disposed = true; selectionRevision.current++;
      if (listId) void window.porsche981?.firmwareVersions?.({ action: "clear", listId }).catch(() => {});
    };
  }, [ecu, programming, reload]);
  async function choose(targetId: string) {
    const revision = ++selectionRevision.current;
    setSelected(targetId); setReview(null); setMessage("");
    if (!targetId || !listing?.listId) return;
    setBusy(true);
    try {
      const result = await window.porsche981?.firmwareVersions?.({ action: "review", ecu, listId: listing.listId, targetId });
      if (revision !== selectionRevision.current) return;
      if (result?.ok) setReview(result);
      else { setSelected(""); setMessage(failureMessage(result?.error)); }
    } catch { if (revision === selectionRevision.current) { setSelected(""); setMessage(failureMessage()); } }
    finally { if (revision === selectionRevision.current) setBusy(false); }
  }
  if (!programming) return null;
  const choice = listing?.versions?.find(item => item.id === selected);
  const unitLabels = { waiting: "等待", flashing: "刷写中", checking: "核对中", completed: "已完成", failed: "失败", unconfirmed: "结果未确认", unexecuted: "未执行" };
  const currentStep = job?.steps[job.currentIndex];
  return <section className="panel" data-testid="diagnostic-preparation" data-programming-ecu={ecu}>
    <h3>{obdFunctionLabel("原厂固件刷写", review?.canStart === true || !!job)}</h3>
    {job ? <section data-testid="firmware-execution">
      <p role="status" data-testid="firmware-execution-state">{!job.released
        ? job.cancelRequested ? "等待安全停止" : job.phase === "release-pending" ? "等待诊断头释放确认" : job.phase === "waiting-manual" ? "请完成当前人工操作" : job.phase === "preparing" ? "正在核对执行条件" : "正在执行原厂作业"
        : job.phase === "completed" ? "刷写及完整版本核对完成" : job.phase === "cancelled" ? "本次作业已停止" : "本次作业未完成"}</p>
      <ul data-testid={job.released ? "firmware-execution-result" : "firmware-execution-progress"}>
        {job.units.map(unit => <li key={unit.ecu} data-firmware-unit={unit.ecu} data-firmware-unit-state={unit.status}>
          <strong>{unit.label}</strong>：{unit.originalVersion} → {unit.targetVersion} · {unitLabels[unit.status]}
          {job.released && <span> · 实际读回：{unit.actualVersion ?? "未确认"}</span>}
        </li>)}
      </ul>
      {!job.released && currentStep && <p data-testid="firmware-current-step">{currentStep.label}</p>}
      {job.phase === "waiting-manual" && <button type="button" data-testid="firmware-manual-continue" disabled={acting}
        onClick={() => void execute({ action: "continue", jobId: job.id, stepId: currentStep!.id })}>已完成，继续</button>}
      {!job.released && <button type="button" data-testid="firmware-cancel" disabled={acting || job.cancelRequested || job.phase === "release-pending"}
        onClick={() => void execute({ action: "cancel", jobId: job.id })}>{job.cancelRequested ? "等待安全停止" : "取消执行"}</button>}
      {job.cancelNotApplied && <p>本次作业已完成，取消未能提前生效。</p>}
      {(job.releaseFailure || job.failure) && <p role="alert" data-testid="firmware-execution-failure">{(job.releaseFailure || job.failure)!.cause} {(job.releaseFailure || job.failure)!.remedy}</p>}
      {job.connectionWarning && <p role="status">{job.connectionWarning}</p>}
      {job.released && <button type="button" data-testid="firmware-return" disabled={acting} onClick={() => void execute({ action: "dismiss", jobId: job.id })}>返回当前单元</button>}
    </section> : <>
    <p>用于原厂升级、同版本重刷，以及原厂明确允许的版本回退。目标版本由你选择，联动更新需核对全部参与单元。</p>
    <p>需要核对本车当前单元的身份与版本、原厂文件和完整作业，以及 VNCI／PT3G 有线连接。</p>
    {busy && <p role="status" data-testid="firmware-version-checking">正在核对原厂版本及完整联动范围</p>}
    {!listing?.versions?.length && !busy && !message && <p role="status" data-testid="firmware-version-unfinished">
      {listing?.catalogueState === "no-match" ? "当前没有核对通过的匹配版本。" : "已核实匹配的原厂版本列表尚未完成。"} {listing?.reason}
    </p>}
    {!!listing?.versions?.length && <label>目标原厂版本
      <select data-testid="firmware-target-version" value={selected} disabled={busy || acting || execution?.busy} onChange={event => void choose(event.target.value)}>
        <option value="">请选择目标版本</option>
        {listing.versions.map(item => <option key={item.id} value={item.id}>{item.version}</option>)}
      </select>
    </label>}
    {choice?.notes && <details data-testid="firmware-release-notes"><summary>原厂更新说明</summary><p>{choice.notes}</p></details>}
    {review?.rows && <section data-testid="firmware-version-review">
      <h4>本次刷写范围</h4>
      <ul>{review.rows.map(unit => <li key={unit.ecu} data-firmware-review-ecu={unit.ecu}>
        {unit.label}：{unit.currentVersion} → {unit.targetVersion}
      </li>)}</ul>
      <p role="status">{review.reason}</p>
    </section>}
    {message && <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>重新核对版本</button>}
    <button type="button" data-testid="firmware-confirm" disabled={busy || acting || execution?.busy || !review?.canStart || !review.reviewId || !review.listId || !review.targetId}
      onClick={() => review?.reviewId && review.listId && review.targetId && void execute({ action: "start", ecu, reviewId: review.reviewId, listId: review.listId, targetId: review.targetId })}>
      {obdFunctionLabel("确认刷写", review?.canStart === true)}</button>
    </>}
    {!job && message && <p role="alert" data-testid="firmware-version-error">{message}</p>}
    {actionMessage && <p role="alert" data-testid="firmware-action-error">{actionMessage}</p>}
  </section>;
}
