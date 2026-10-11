import { useEffect, useState } from "react";
import { api, type DiagnosticPreparationResult } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";

const phases: Record<string, string> = {
  "backed-up": "已备份，尚未发布写入意图", "write-intent": "已发布写入意图，结果待核对",
  "write-returned": "写入调用已返回，回读待核对", "block-verified": "部分块已回读，完整范围待核对",
  "completed": "完整回读通过", "outcome-unknown": "结果未知，需核对", "failed-before-write": "写入前失败",
};

export function CodingOperationHistoryPanel({ ecu, revision }: { ecu: string; revision?: string | null }) {
  const [offset, setOffset] = useState(0), [refresh, setRefresh] = useState(0);
  const [doc, setDoc] = useState<DiagnosticPreparationResult | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  useEffect(() => { setOffset(0); }, [ecu, revision]);
  useEffect(() => {
    let active = true;
    setDoc(null); setError(""); setLoading(true);
    const fn = api().diagnosticPreparation;
    if (!fn) { setError("桌面操作记录接口不可用。"); setLoading(false); return; }
    void fn({ action: "operation-history", ecu, offset }).then(result => {
      if (!active) return;
      if (result.ok && result.history && result.operations) setDoc(result);
      else setError(diagnosticMessage(result.error || "preparation-history-invalid"));
    }).catch(failure => { if (active) setError(diagnosticMessage(failure)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [ecu, revision, offset, refresh]);
  return <section data-testid="coding-operation-history" aria-label="持久操作状态">
    <h4>持久操作状态</h4>
    <p className="muted">按当前控制单元名称归档，重启后保留；记录本身不证明当前车辆身份或允许继续写入。模拟操作与车辆操作分别标明，未知结果不自动重放。</p>
    <button type="button" disabled={loading} onClick={() => setRefresh(value => value + 1)}>刷新操作状态</button>
    {loading && <p role="status">正在核对操作记录…</p>}
    {error && <p role="alert">操作记录读取失败：{error}；不能据此确认没有未结束操作。</p>}
    {doc?.history && <>
      <p>本单元 {doc.history.currentEcuTotal} 项，归属未确认 {doc.history.unassignedTotal} 项；需核对 {doc.history.requiresReconciliation} 项。</p>
      {doc.history.unassignedTotal > 0 && <p role="alert">部分记录损坏或不完整，无法确认控制单元归属；它们不会被忽略或归到当前单元。</p>}
      {doc.history.total === 0 && <p>暂无操作记录。</p>}
      <ul>{doc.operations?.map(row => <li key={row.operationId} data-operation-id={row.operationId}>
        <strong>{row.scope === "unassigned" ? "归属未确认" : row.mode === "simulation" ? "模拟操作" : "车辆操作"} · {row.integrityError ? "记录完整性核对失败" : phases[row.phase || ""] || "状态未确认"}</strong>
        <p>操作编号：<code>{row.operationId}</code></p>
        {row.createdUtc && <p>开始 {row.createdUtc} · 最后事件 {row.updatedUtc}</p>}
        {row.targetBlocks !== undefined && <p>完整范围 {row.targetBlocks} 块 · 改变 {row.changedBlocks} 块 · 写入意图 {row.intendedBlocks} 块 · 已回读 {row.verifiedBlocks} 块</p>}
        {row.requiresReconciliation && <p role="alert">{row.mode === "simulation" ? "模拟结果尚未闭合；此记录不表示发生车辆写入。" : "执行结果或记录完整性尚未确认，需核对；不能据此重发或恢复写入。"}</p>}
      </li>)}</ul>
      <div className="chip-row">
        <button type="button" disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 40))}>上一页操作</button>
        <button type="button" disabled={loading || offset + 40 >= doc.history.total} onClick={() => setOffset(value => value + 40)}>下一页操作</button>
      </div>
    </>}
  </section>;
}
