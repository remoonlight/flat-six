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
  emptyStatusMap,
  flattenNodes,
  failureReasonZh,
  isAdaptedProfile,
  mergeStatusAfterJob,
  statusText,
} from "../can-topology-logic.mjs";
import { topologyCapability } from "../can-topology-capabilities.mjs";
import { persistTopologySnapshot } from "../obd-diag-persist";
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

function readTime(value: unknown) {
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? "时间未确认" : date.toLocaleString("zh-CN", { hour12: false });
}

function harnessScenario() {
  if (typeof window === "undefined") return "success";
  return window.__TOPO_SCENARIO__ || "success";
}

export function TopologyPage({
  onBusyChange,
  peerBusy = false,
  adapterModel,
  active = false,
  diagnosticReady = false,
  onNavigate,
}: {
  onBusyChange?: (busy: boolean) => void;
  peerBusy?: boolean;
  adapterModel?: string | null;
  active?: boolean;
  diagnosticReady?: boolean;
  onNavigate?: (tab: "connection" | "live" | "coding", systemId?: string) => void;
}) {
  const seed = topologySeed();
  const fixture = topologyFixtureEnabled();
  const canHardware = hasDesktopApi() || fixture;
  const gen = useMemo(
    () =>
      combinedGeneration(seed) as {
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
    [seed],
  );

  const [view, setView] = useState<"diagram" | "list">("diagram");
  const [selectedId, setSelectedId] = useState<string>("gateway");
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [statuses, setStatuses] = useState<Record<string, Record<string, unknown>>>(() => emptyStatusMap(gen));
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState<Parameters<typeof persistTopologySnapshot>[1] | null>(null);
  const [saving, setSaving] = useState(false);
  const autoChecked = useRef(false);
  const queueRef = useRef<ReturnType<typeof createScanQueue> | null>(null);
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
  const supported = nodes.filter(canTransmit);
  const capability = topologyCapability(selected, adapterModel);

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
  ) {
    if (!lockedStart && (startLock.current || busy || peerBusy)) return;
    const targets = list.filter((n) => canTransmit(n));
    if (!list.length || targets.length !== list.length || (task === "clear" && list.some((n) => !topologyCapability(n, adapterModel).clearable))) {
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
    const q = createScanQueue({ invoke });
    queueRef.current = q;
    setBusy(true);
    setBatch(null);
    if (task === "read") {
      statusesRef.current = emptyStatusMap(gen);
      setStatuses(statusesRef.current);
    }
    setError(null);
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
          onKind: (id: string, classified: Record<string, unknown>) => {
            if (!mounted.current || token !== runToken.current) return;
            setStatuses((prev) => ({
              ...prev,
              [id]: mergeStatusAfterJob(prev[id], classified, task, { final: classified }),
            }));
          },
        },
      });
      if (!mounted.current || token !== runToken.current) return;
      if (out.error === "cancelled") setProgress("已取消");
      else if (out.error === "busy") setError("已有任务在进行");
      else setProgress(completionFeedback({ results: out.results, adaptedQueued: targets.length, totalNodes: nodes.length }));
      if (out.error !== "busy" && task === "read" && (out.results || []).some((row) => !!(row as { doc?: { jobId?: string } }).doc?.jobId)) {
        setBatch({ task, source: ctx.mode,
          results: (out.results || []) as Parameters<typeof persistTopologySnapshot>[1]["results"] });
      }
    } catch (e) {
      if (mounted.current && token === runToken.current) setError(String(e));
    } finally {
      startLock.current = false;
      if (mounted.current && token === runToken.current) {
        setBusy(false);
      }
      queueRef.current = null;
    }
  }

  function clearEligible(node: NodeT) {
    const status = statusesRef.current[node.id];
    return status?.kind === "dtc-present" && status.stale !== true &&
      (status.identity as { observedProfileMatch?: boolean } | undefined)?.observedProfileMatch === true &&
      topologyCapability(node, adapterModel).clearable;
  }

  function requestTask(task: "read" | "clear", list: NodeT[]) {
    if (startLock.current || busy || peerBusy) return;
    if (!list.length || list.some((n) => !canTransmit(n)) || (task === "clear" && list.some((n) => !clearEligible(n)))) return;
    startLock.current = true;
    void runTask(task, list, {
      x431Inactive: true,
      confirmedReadOnly: task === "read" ? true : undefined,
      confirmedClearDtc: task === "clear" ? true : undefined,
    }, true);
  }

  useEffect(() => {
    if (!active) { autoChecked.current = false; return; }
    if (fixture || autoChecked.current || !diagnosticReady || busy || peerBusy || !supported.length) return;
    autoChecked.current = true;
    requestTask("read", supported);
  }, [active, diagnosticReady, busy, peerBusy, fixture, supported]);

  useEffect(() => {
    if (!active && !busy && !fixture) { setBatch(null); setStatuses(emptyStatusMap(gen)); setProgress(null); }
  }, [active, busy, fixture, gen]);
  useEffect(() => {
    if (!diagnosticReady && !busy && !fixture) setStatuses((current) => {
      const next = Object.fromEntries(Object.entries(current).map(([id, status]) => [id,
        status.kind === "dtc-present" || status.kind === "no-dtc" ? { ...status, stale: true } : status]));
      statusesRef.current = next;
      return next;
    });
  }, [diagnosticReady, busy, fixture]);

  async function saveBatch() {
    if (!batch || saving || busy) return;
    setSaving(true);
    try {
      const saved = await persistTopologySnapshot(api(), batch);
      if (!saved) throw new Error("本批没有可保存的实际读取结果");
      setProgress("本批读取结果已保存");
    } catch (e) { setError(`保存失败：${String(e)}`); }
    finally { setSaving(false); }
  }

  const selectedAdapted = selected && isAdaptedProfile(selected.profileId);
  const actionsOff = locked || !canHardware || (!fixture && !diagnosticReady);
  const selectedOff = actionsOff || !selectedAdapted;
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
        title={`${n.label} · ${topologyCapability(n, adapterModel).diagnostic} · ${statusText(st)}`}
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
                    <tr key={n.id} data-selected={selectedId === n.id ? "1" : undefined}>
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
            <div className="topo-map" data-testid="topo-diagram">
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

            <button type="button" className="btn" data-testid="topo-save-result" disabled={!batch || busy || saving}
              onClick={() => void saveBatch()}>{saving ? "正在保存…" : "保存结果"}</button>
            {!fixture && !diagnosticReady && !busy ? <p className="muted">请先在连接设置中连接诊断头，并选择诊断 CAN 用途。</p> : null}
            {selected?.isGateway ? (
              <>
                <h3>{selected.short} · {selected.label}</h3>
                <div className="topo-secondary">
                  <button type="button" className="btn" data-testid="topo-read-all" disabled={actionsOff || !supported.length}
                    onClick={() => requestTask("read", supported)}>读取所有单元故障码</button>
                  <button type="button" className="btn" data-testid="topo-clear-all" disabled={actionsOff || !canClearAll}
                    onClick={() => requestTask("clear", clearTargets)}>清除所有单元故障码</button>
                </div>
              </>
            ) : selected ? (
              <>
                <dl>
                  <dt>模块</dt>
                  <dd>
                    {selected.short} {selected.label}
                    {selected.secondary ? "（该网上为交叉接口）" : ""}
                  </dd>
                  {stSel?.error ? <><dt>未完成原因</dt><dd>{failureReasonZh(stSel.error)}</dd></> : null}
                  {stSel?.capturedUtc ? <><dt>{stSel.stale ? "上次读取时间" : "读取时间"}</dt><dd>{readTime(stSel.capturedUtc)}</dd></> : null}
                  {typeof stSel?.dtcCount === "number" ? <><dt>结果来源</dt><dd>{(stSel.dtcSimulated ?? stSel.simulated) ? "模拟数据" : "车辆读取"}{stSel.stale ? " · 上次读取，当前结果未确认" : ""}</dd></> : null}
                  {typeof stSel?.dtcCount === "number" && stSel.kind !== "unscanned" && stSel.kind !== "pending-adapt" ? (
                    <>
                      <dt>故障码</dt>
                      <dd>
                        {String(stSel.dtcCount)} 条{stSel.stale ? "（上次读取）" : ""}
                        {Array.isArray(stSel.records)
                          ? (stSel.records as Array<Record<string, string>>).map((r, i) => (
                              <div key={i}>
                                {dtcLabel(r)} {dtcDetail(r)}{" "}
                              </div>
                            ))
                          : null}
                      </dd>
                    </>
                  ) : null}
                </dl>
                <div className="topo-secondary">
                  {onNavigate ? <button type="button" className="ghost" data-testid="topo-open-live" disabled={locked} onClick={() => onNavigate("live", selected.id)}>实时数据</button> : null}
                  <button
                    type="button"
                    className="btn"
                    data-testid="topo-read-selected"
                    disabled={selectedOff}
                    onClick={() => selected && requestTask("read", [selected])}
                  >
                    读取故障码
                  </button>
                  <button
                    type="button"
                    className="btn"
                    data-testid="topo-clear-selected"
                    disabled={selectedOff || !selected || !clearEligible(selected)}
                    onClick={() => selected && requestTask("clear", [selected])}
                  >
                    清除故障码
                  </button>
                </div>
                {selectedAdapted ? <p className="muted topo-note" data-testid="topo-clear-scope">{capability.clearable ? "受限清码仅用于当前 DME / Gateway，实车清码与复读待验收。" : "当前设备未开放清故障码；仍可进行具名只读诊断。"}</p> : null}
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
