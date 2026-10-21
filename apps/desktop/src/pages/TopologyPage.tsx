import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { api, hasDesktopApi, topologyFixtureEnabled } from "../api";
import { topologySeed } from "../can-topology-data";
import {
  branchAppearances,
  canTransmit,
  combinedGeneration,
  completionFeedback,
  createScanQueue,
  dtcBadgeCount,
  dtcDetail,
  dtcLabel,
  orderDtcRecords,
  emptyStatusMap,
  flattenNodes,
  failureReasonZh,
  isAdaptedProfile,
  mergeStatusAfterJob,
  prepareScanRetry,
  statusText,
} from "../can-topology-logic.mjs";
import { topologyCapability } from "../can-topology-capabilities.mjs";
import { createSourceFaultInvoker, withSourceFaultRoutes } from "../obd/topology-source.mjs";
import { obdFunctionLabel } from "../obd/function-label";
import "../can-topology.css";

type NodeT = {
  id: string;
  short: string;
  label: string;
  branchId: string;
  branchLabel: string;
  branchColor?: string;
  isGateway?: boolean;
  profileId?: string;
  connectionType?: string;
  sourcePages?: number[];
  notes?: string;
  sourceGenerations?: string[];
  secondary?: boolean;
  additionalBranches?: Array<{ branchId: string; sourcePages?: number[]; notes?: string }>;
  gatewayPins?: { high?: string | null; low?: string | null };
};

function harnessScenario() {
  if (typeof window === "undefined") return "success";
  return window.__TOPO_SCENARIO__ || "success";
}

export function TopologyPage({
  onBusyChange,
  peerBusy = false,
  adapterModel,
  adapterDeviceId,
  active = false,
  diagnosticReady = false,
  onNavigate,
}: {
  onBusyChange?: (busy: boolean) => void;
  peerBusy?: boolean;
  adapterModel?: string | null;
  adapterDeviceId?: string | null;
  active?: boolean;
  diagnosticReady?: boolean;
  onNavigate?: (tab: "connection" | "live" | "coding", systemId?: string) => void;
}) {
  const seed = topologySeed();
  const fixture = topologyFixtureEnabled();
  const canHardware = hasDesktopApi() || fixture;
  const gen = useMemo(
    () =>
      (fixture ? combinedGeneration(seed) : withSourceFaultRoutes(combinedGeneration(seed))) as {
        diagnostic?: {
          label?: string;
          high?: string | null;
          low?: string | null;
          gatewayHigh?: string | null;
          gatewayLow?: string | null;
        };
        branches?: Array<{
          id: string;
          label: string;
          color: string;
          gatewayPins?: { high?: string | null; low?: string | null };
          nodes: unknown[];
        }>;
      } | null,
    [seed, fixture],
  );

  const [view, setView] = useState<"diagram" | "list">("diagram");
  const [selectedId, setSelectedId] = useState<string>("gateway");
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [statuses, setStatuses] = useState<Record<string, Record<string, unknown>>>(() => emptyStatusMap(gen));
  const [busy, setBusy] = useState(false);
  const [wholeReadRunning, setWholeReadRunning] = useState(false);
  const [readStopRequested, setReadStopRequested] = useState(false);
  const [releasePending, setReleasePending] = useState(false);
  const releaseTermination = useRef(false);
  const [recovery, setRecovery] = useState<{ jobId: string | null; remaining: number; canRetry: boolean; readback?: boolean;
    resolve: (choice: "retry" | "terminate") => void; deviceId?: string | null; model?: string | null } | null>(null);
  const [retryBusy, setRetryBusy] = useState(false), [retryError, setRetryError] = useState("");
  const retryLock = useRef(false), retryCancelled = useRef(false);
  const queueRef = useRef<ReturnType<typeof createScanQueue> | null>(null);
  const taskInvoke = useRef<((request: Record<string, unknown>) => Promise<any>) | null>(null);
  const startLock = useRef(false);
  const mounted = useRef(true);
  const runToken = useRef(0);
  const statusesRef = useRef(statuses);
  statusesRef.current = statuses;

  const nodes = useMemo(() => flattenNodes(gen) as NodeT[], [gen]);
  const rails = useMemo(
    () => branchAppearances(gen) as Array<{ id: string; label: string; color: string; appearances: NodeT[] }>,
    [gen],
  );
  const selected = nodes.find((n) => n.id === selectedId) || nodes[0];
  const stSel = selected ? statuses[selected.id] : null;
  const locked = busy || peerBusy;
  const supported = nodes.filter((node) => canTransmit(node) && topologyCapability(node, adapterModel, adapterDeviceId).readable);
  const capability = topologyCapability(selected, adapterModel, adapterDeviceId);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      void queueRef.current?.cancel();
    };
  }, []);

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  function invoke(req: Record<string, unknown>) {
    const fn = api().readOnlySession;
    if (!fn) return Promise.reject(new Error("desktop_required"));
    return fn(req as never);
  }

  async function runTask(
    task: "read" | "clear",
    list: NodeT[],
    liveFlags: { x431Inactive: boolean; confirmedReadOnly?: boolean; confirmedClearDtc?: boolean },
    lockedStart = false,
    wholeCar = false,
  ) {
    if (!lockedStart && (startLock.current || busy || peerBusy)) return;
    const targets = list.filter((n) => canTransmit(n) && topologyCapability(n, adapterModel, adapterDeviceId).readable);
    if (!list.length || targets.length !== list.length || (task === "clear" && list.some((n) => !topologyCapability(n, adapterModel, adapterDeviceId).clearable))) {
      if (lockedStart) startLock.current = false;
      return;
    }
    if (!canHardware) {
      setError("需要桌面端。");
      if (lockedStart) startLock.current = false;
      return;
    }
    startLock.current = true;
    const token = ++runToken.current;
    const deviceId = adapterDeviceId, model = adapterModel;
    const stream=api().manufacturerStream, connection=api().obdConnection;
    const readInvoke = !fixture && stream && connection ? createSourceFaultInvoker({stream: req=>stream(req), legacy: invoke,
      connection:req=>connection(req),deviceId:deviceId ?? null,model:model ?? null}) : invoke;
    const invokeTask = async (req: Record<string, unknown>) => {
      if (req.action === "start" && req.mode === "live") {
        const connection = api().obdConnection;
        if (!connection || !deviceId || !model) return { ok: false, error: "scan-device-unconfirmed" };
        const status = await connection({ action: "status" });
        if (status.selectedDeviceId !== deviceId || status.model !== model) return { ok: false, error: "scan-device-changed" };
        if (status.purpose !== "diagnostic") return { ok: false, error: "diagnostic_purpose_required" };
      }
      return readInvoke(req);
    };
    taskInvoke.current=invokeTask;
    const q = createScanQueue({ invoke: invokeTask });
    queueRef.current = q;
    setBusy(true);
    setWholeReadRunning(task === "read" && wholeCar); setReadStopRequested(false);
    setReleasePending(false); releaseTermination.current = false;
    if (task === "read") {
      const empty = emptyStatusMap(gen);
      const next = wholeCar ? empty : {
        ...statusesRef.current,
        ...Object.fromEntries(targets.map(node => [node.id, empty[node.id]])),
      };
      for (const node of targets) next[node.id] = { ...next[node.id], queued: true };
      statusesRef.current = next;
      setStatuses(statusesRef.current);
    }
    setError(null);
    setRecovery(null); setRetryError("");
    setProgress(task === "clear" ? "清除中…" : "读取中…");
    const ctx = {
      mode: fixture ? "simulation" as const : "live" as const,
      sessionTask: task,
      ...(fixture ? { scenario: harnessScenario() } : {}),
      ...liveFlags,
    };
    try {
      const out = await q.run({
        nodes: targets,
        ctx,
        hooks: {
          statusOf: (id: string) => statusesRef.current[id],
          onStatus: (doc: { cleanupPending?: boolean; error?: string | null }) => {
            if (!mounted.current || token !== runToken.current) return;
            setReleasePending(doc.cleanupPending === true);
            if (!doc.cleanupPending) return;
            setProgress(releaseTermination.current ? "正在终止并等待诊断头释放…" : q.stopRequested ? "正在完成当前单元读取并等待诊断头释放，随后停止…"
              : doc.error ? "当前操作未完成，正在等待诊断头释放…" : "正在等待诊断头释放…");
          },
          onKind: (id: string, classified: Record<string, unknown>) => {
            if (!mounted.current || token !== runToken.current) return;
            const next = { ...statusesRef.current, [id]: mergeStatusAfterJob(statusesRef.current[id], classified, task, { final: classified }) };
            statusesRef.current = next; setStatuses(next);
          },
          onInterrupted: checkpoint => new Promise(resolve => {
            if (!mounted.current || token !== runToken.current) { resolve("terminate"); return; }
            setProgress(checkpoint.readback ? "清除结果未确认，重新连接后将只读取原范围核对。"
              : `检查中断，尚有 ${checkpoint.remaining.length} 个单元未完成。已读结果保留。`);
            setRetryError("");
            setRecovery({ jobId: checkpoint.row.jobId || null, remaining: checkpoint.remaining.length,
              canRetry: checkpoint.canRetry, readback: checkpoint.readback, resolve, deviceId, model });
          }),
        },
      });
      if (!mounted.current || token !== runToken.current) return;
      if (out.stopped) {
        setProgress("读取已停止，已读结果保留。可重新开始检查。");
        if (out.error) setError(failureReasonZh(out.error));
      }
      else if (out.error === "cancelled") setProgress("检查已终止，已读结果保留。可重新开始检查。");
      else if (out.error === "busy") setError("已有任务在进行");
      else if (task === "read" && !out.error) setProgress("读取结束。");
      else setProgress(`${out.clearOutcomeUnconfirmed ? out.readbackStarted ? out.error ? "清除响应未确认；重新读取核对未完成。" : "清除响应未确认；已重新读取核对。" : "清除结果未确认。" : ""}${completionFeedback({ results: out.results, adaptedQueued: targets.length, totalNodes: nodes.length })}`);
    } catch (e) {
      if (mounted.current && token === runToken.current) setError(failureReasonZh(e));
    } finally {
      startLock.current = false;
      if (mounted.current && token === runToken.current) {
        // A stopped/failed queue leaves unvisited units unread, not waiting
        // forever after the task has released its owner.
        if (Object.values(statusesRef.current).some(status => status.queued === true)) {
          const next = Object.fromEntries(Object.entries(statusesRef.current).map(([id, status]) => {
            const { queued: _queued, ...unvisited } = status;
            return [id, unvisited];
          }));
          statusesRef.current = next; setStatuses(next);
        }
        setBusy(false); setReleasePending(false); setWholeReadRunning(false); setReadStopRequested(false);
        setRecovery(null); setRetryBusy(false);
      }
      queueRef.current = null;
    }
  }

  async function retryScan() {
    if (!recovery || !recovery.canRetry || retryLock.current) return;
    retryLock.current = true; retryCancelled.current = false;
    const token = runToken.current, checkpoint = recovery;
    setRetryBusy(true); setRetryError("");
    try {
      const connection = api().obdConnection;
      const result = await prepareScanRetry({ invoke: taskInvoke.current || invoke, connection: connection ? req => connection(req as never) : undefined,
        jobId: checkpoint.jobId, mode: fixture ? "simulation" : "live", deviceId: checkpoint.deviceId, model: checkpoint.model,
        cancelled: () => retryCancelled.current || !mounted.current || token !== runToken.current });
      if (!mounted.current || token !== runToken.current) return;
      if (retryCancelled.current) checkpoint.resolve("terminate");
      else if (result.ok) { setRecovery(null); setProgress(checkpoint.readback ? "正在重新读取原清除范围…" : "继续读取未完成单元…"); checkpoint.resolve("retry"); }
      else setRetryError(result.error || "scan-connection-unconfirmed");
    } finally {
      retryLock.current = false;
      if (mounted.current && token === runToken.current) setRetryBusy(false);
    }
  }

  function terminateReleaseWait() {
    if (releaseTermination.current) return;
    releaseTermination.current = true;
    setProgress("正在终止并等待诊断头释放…");
    void queueRef.current?.cancel();
  }

  function terminateScan() {
    retryCancelled.current = true;
    if (!retryLock.current) recovery?.resolve("terminate");
  }

  function stopReadAfterCurrent() {
    if (!queueRef.current?.stopAfterCurrent()) return;
    setReadStopRequested(true);
    setProgress("正在完成当前单元读取，随后停止…");
  }

  function clearEligible(node: NodeT) {
    const status = statusesRef.current[node.id];
    const identity=status?.identity as {sourceCatalogId?: string;generation?: string;variantLink?: {profile_id?: string}} | undefined;
    if(identity?.sourceCatalogId && (identity.generation!=="981" || identity.variantLink?.profile_id!==
      ({"porsche-981-2014-dme":"9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
        "porsche-981-2014-gateway":"9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1"} as Record<string,string>)[node.profileId || ""]))return false;
    return status?.kind === "dtc-present" && status.stale !== true &&
      (status.identity as { observedProfileMatch?: boolean } | undefined)?.observedProfileMatch === true &&
      topologyCapability(node, adapterModel, adapterDeviceId).clearable;
  }

  function requestTask(task: "read" | "clear", list: NodeT[], wholeCar = false) {
    if (startLock.current || busy || peerBusy) return;
    if (!list.length || list.some((n) => !canTransmit(n)) || (task === "clear" && list.some((n) => !clearEligible(n)))) return;
    startLock.current = true;
    void runTask(task, list, {
      x431Inactive: true,
      confirmedReadOnly: task === "read" ? true : undefined,
      confirmedClearDtc: task === "clear" ? true : undefined,
    }, true, wholeCar);
  }

  // Browsing other pages preserves this scan, its interruption choice and results.
  useEffect(() => {
    if (!diagnosticReady && !busy && !fixture) setStatuses((current) => {
      const next = Object.fromEntries(Object.entries(current).map(([id, status]) => [id,
        status.kind === "dtc-present" || status.kind === "no-dtc" ? { ...status, stale: true } : status]));
      statusesRef.current = next;
      return next;
    });
  }, [diagnosticReady, busy, fixture]);

  const selectedAdapted = selected && isAdaptedProfile(selected.profileId);
  const actionsOff = locked || !canHardware || (!fixture && !diagnosticReady);
  const selectedOff = actionsOff || !selectedAdapted || !capability.readable;
  const clearTargets = supported.filter(clearEligible);
  const canClearAll = clearTargets.length > 0;

  function chip(n: NodeT, key: string, index: number) {
    const st = statuses[n.id] || { kind: "unscanned", simulated: false };
    const kind = String(st.kind);
    const badge = dtcBadgeCount(st);
    return (
      <button
        key={key}
        type="button"
        className="topo-chip"
        style={{
          "--node-column": Math.floor(index / 2) + 1,
          "--node-row": index % 2 === 0 ? 1 : 3,
          "--compact-column": Math.floor((index % 4) / 2) + 1,
          "--compact-row": Math.floor(index / 4) * 4 + (index % 2 === 0 ? 1 : 3),
        } as CSSProperties}
        title={`${n.label} · ${topologyCapability(n, adapterModel, adapterDeviceId).diagnostic} · ${statusText(st)}`}
        data-testid={`topo-node-${n.id}`}
        data-kind={kind}
        data-stale={st.stale ? "1" : undefined}
        data-secondary={n.secondary ? "1" : undefined}
        data-supported={canTransmit(n) ? "1" : "0"}
        data-reference={topologyCapability(n).referenceOnly ? "1" : undefined}
        aria-pressed={selectedId === n.id}
        aria-label={`${n.short} ${n.label} ${statusText(st)}`}
        onClick={() => setSelectedId(n.id)}
      >
        {n.short}
        <span className="sub">{kind === "pending-adapt" || kind === "assembly-unconfirmed" ? "待适配" : statusText(st)}</span>
        {badge != null ? <span className="badge">{String(badge)}</span> : null}
      </button>
    );
  }

  const switchEl = (
    <div className="topo-switch" data-testid="topo-view" role="tablist" aria-label="视图">
      <button
        type="button"
        role="tab"
        data-testid="topo-view-diagram"
        aria-selected={view === "diagram"}
        className={view === "diagram" ? "on" : undefined}
        onClick={() => setView("diagram")}
      >
        拓扑图
      </button>
      <button
        type="button"
        role="tab"
        data-testid="topo-view-list"
        aria-selected={view === "list"}
        className={view === "list" ? "on" : undefined}
        onClick={() => setView("list")}
      >
        列表
      </button>
    </div>
  );

  return (
    <div className="topo" data-page="topology">
      <div className="topo-layout">
        <section className="panel topo-map-panel">
          <div className="topo-map-heading">
            <h3>控制单元网络</h3>
            <div className="topo-legend" aria-label="节点说明">
              <span><i data-legend="supported" />诊断已接入</span>
              <span><i data-legend="dtc" />有故障码</span>
              <span><i data-legend="no-dtc" />无故障码</span>
              <span><i data-legend="failed" />读取未完成</span>
            </div>
          </div>
          {view === "list" ? (
            <table className="topo-list" data-testid="topo-list">
              <thead>
                <tr>
                  <th>模块</th>
                  <th>网络</th>
                  <th>诊断能力</th>
                  <th>读取结果</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => {
                  const st = statuses[n.id] || { kind: "unscanned" };
                  const badge = dtcBadgeCount(st);
                  return (
                    <tr key={n.id} data-kind={String(st.kind)} data-stale={st.stale ? "1" : undefined}
                      data-selected={selectedId === n.id ? "1" : undefined}>
                      <td>
                        <button type="button" className="btn" aria-pressed={selectedId === n.id} onClick={() => setSelectedId(n.id)}>
                          {n.short} {n.label}
                        </button>
                      </td>
                      <td>{n.branchLabel}</td>
                      <td>{topologyCapability(n).diagnostic}</td>
                      <td>
                        {statusText(st)}
                        {badge != null ? ` · ${badge}` : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <><div className="topo-map" data-testid="topo-diagram">
              <div className="topo-spine">
                {nodes
                  .filter((n) => n.isGateway)
                  .map((n) => (
                    <button
                      key={n.id}
                      type="button"
                      className="topo-chip topo-gw"
                      data-testid={`topo-node-${n.id}`}
                      data-kind={String((statuses[n.id] || {}).kind || "unscanned")}
                      data-stale={statuses[n.id]?.stale ? "1" : undefined}
                      data-supported={canTransmit(n) ? "1" : "0"}
                      aria-pressed={selectedId === n.id}
                      onClick={() => setSelectedId(n.id)}
                    >
                      {n.short}
                      <span className="sub">{statusText(statuses[n.id] || { kind: "unscanned" })}</span>
                      {dtcBadgeCount(statuses[n.id]) != null ? (
                        <span className="badge">{String(dtcBadgeCount(statuses[n.id]))}</span>
                      ) : null}
                    </button>
                  ))}
                <div className="topo-spine-v" aria-hidden />
              </div>
              <div className="topo-vbus" data-testid="topo-trunk" aria-hidden />
              <div className="topo-buses">
                {rails.map((b) => {
                  const kids = b.appearances || [];
                  return (
                    <div key={b.id} className="topo-bus" data-branch={b.id} style={{ ["--bus" as string]: b.color }}>
                      <div className="topo-bus-name">
                        <span>{b.label}</span>
                      </div>
                      <div
                        className="topo-bus-track"
                        data-testid={`topo-rail-${b.id}`}
                        style={{
                          "--node-columns": Math.max(1, Math.ceil(kids.length / 2)),
                          "--compact-groups": Math.max(1, Math.ceil(kids.length / 4)),
                        } as CSSProperties}
                      >
                        {kids.map((n, i) => chip(n, `${n.id}-${b.id}`, i))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            {nodes.some(node=>node.branchId==='unplaced') ? <section className="topo-unplaced" data-testid="topo-unplaced">
              <h4>连接位置未核实</h4>
              <div>{nodes.filter(node=>node.branchId==='unplaced').map((node,index)=>chip(node,node.id,index))}</div>
            </section> : null}</>
          )}
        </section>

        <div className="topo-detail-col" data-testid="topo-detail-col">
          {switchEl}
          <aside className="panel topo-detail" data-testid="topo-detail">
            {progress ? (
              <p className="muted" data-testid="topo-progress" role="status">
                {progress}
              </p>
            ) : null}
            {error ? (
              <p className="error" data-testid="topo-error" role="alert">
                {error}
              </p>
            ) : null}
            {wholeReadRunning && busy && !recovery ? <button type="button" className="btn" data-testid="topo-stop-read"
              disabled={readStopRequested} onClick={stopReadAfterCurrent}>{readStopRequested ? "正在停止读取…" : "停止读取"}</button> : null}
            {releasePending && busy && <div data-testid="topo-release-wait">
              <button type="button" className="btn" data-testid="topo-release-terminate"
                disabled={releaseTermination.current} onClick={terminateReleaseWait}>终止</button>
            </div>}
            {recovery ? <div role="alertdialog" aria-labelledby="topo-recovery-title" data-testid="topo-recovery">
              <h3 id="topo-recovery-title">{recovery.readback ? "清除结果未确认" : "检查中断"}</h3>
              <p>{recovery.readback ? `重试连接后自动读取本次原范围的 ${recovery.remaining} 个控制单元核对，不再次清除；终止后保留已读结果。`
                : `重试会继续尚未完成的 ${recovery.remaining} 个控制单元；终止后保留本页已读结果。`}</p>
              {!recovery.canRetry ? <p role="alert">已读结果缺少可核对的车辆身份，请终止并重新检查。</p> : null}
              {retryError ? <div role="alert" data-testid="topo-retry-error"><p>{failureReasonZh(retryError)}</p>
                </div> : null}
              <button type="button" className="btn" data-testid="topo-retry" disabled={retryBusy || !recovery.canRetry}
                onClick={() => void retryScan()}>{retryBusy ? "正在核对并连接…" : recovery.readback ? "重试连接并核对" : "重试继续"}</button>
              <button type="button" className="ghost" data-testid="topo-terminate" onClick={terminateScan}>终止</button>
            </div> : null}

            {!fixture && !diagnosticReady && !busy ? <p className="muted">请先在连接设置中连接诊断头，并选择诊断 CAN 用途。</p> : null}
            {selected ? <h3>{selected.short} · {selected.label}</h3> : null}
            {selected ? <section data-testid="topo-fault-info">
              {stSel?.error ? <p className="error">{failureReasonZh(stSel.error)}</p> : null}
              {typeof stSel?.dtcCount === "number" && stSel.kind !== "unscanned" && stSel.kind !== "pending-adapt" ? <>
                <p>{(stSel.dtcSimulated ?? stSel.simulated) ? "模拟数据" : "车辆读取"}{stSel.stale ? " · 上次读取，当前结果未确认" : ""}</p>
                <h3>故障码：{String(stSel.dtcCount)} 条{stSel.stale ? "（上次读取）" : ""}</h3>
                {Array.isArray(stSel.records) ? (orderDtcRecords(stSel.records) as Array<Record<string, string>>).map((r, i) =>
                  <div key={i}>{dtcLabel(r)} {dtcDetail(r, selected.profileId)}</div>) : null}
              </> : <p className="muted">{stSel?.queued === true ? "等待读取故障信息…" : stSel?.kind === "scanning" ? "正在读取故障信息…" : "尚未读取故障信息"}</p>}
            </section> : null}
            {selected?.isGateway && (
              <>
                <div className="topo-secondary">
                  <button type="button" className="btn" data-testid="topo-read-all" disabled={actionsOff || !supported.length}
                    onClick={() => requestTask("read", supported, true)}>{obdFunctionLabel("整车检查", nodes.every(n => canTransmit(n) && topologyCapability(n, adapterModel, adapterDeviceId).readable))}</button>
                  <button type="button" className="btn" data-testid="topo-clear-all" disabled={actionsOff || !canClearAll}
                    onClick={() => requestTask("clear", clearTargets)}>{obdFunctionLabel("清除所有单元故障码", nodes.every(n => canTransmit(n) && topologyCapability(n, adapterModel, adapterDeviceId).clearable))}</button>
                </div>
              </>
            )}
            {selected ? (
              <>
                <div className="topo-secondary">
                  {onNavigate ? <button type="button" className="ghost" data-testid="topo-open-live" disabled={locked} onClick={() => onNavigate("live", selected.id)}>实时数据</button> : null}
                  <button
                    type="button"
                    className="btn"
                    data-testid="topo-read-selected"
                    disabled={selectedOff}
                    onClick={() => selected && requestTask("read", [selected])}
                  >
                    {obdFunctionLabel("读取故障码", capability.readable)}
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-testid="topo-clear-selected"
                    disabled={selectedOff || !selected || !clearEligible(selected)}
                    onClick={() => selected && requestTask("clear", [selected])}
                  >
                    {obdFunctionLabel("清除故障码", capability.clearable)}
                  </button>
                </div>
              </>
            ) : (
              <p className="muted">选择模块</p>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
