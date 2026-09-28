import { useEffect, useMemo, useRef, useState } from "react";
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
  isAdaptedProfile,
  mergeStatusAfterJob,
  statusText,
} from "../can-topology-logic.mjs";
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
  secondary?: boolean;
  additionalBranches?: Array<{ branchId: string; sourcePages?: number[]; notes?: string }>;
  gatewayPins?: { high?: string | null; low?: string | null };
};

type Pending = { task: "read" | "clear"; nodes: NodeT[] };

function pinText(high?: string | null, low?: string | null) {
  if (!high && !low) return "未在种子中给出";
  return `H ${high || "—"} / L ${low || "—"}`;
}

function harnessScenario() {
  if (typeof window === "undefined") return "success";
  return window.__TOPO_SCENARIO__ || "success";
}

function requireConfirm(fixture: boolean) {
  if (!fixture) return true;
  return typeof window !== "undefined" && window.__TOPO_REQUIRE_CONFIRM__ === true;
}

export function TopologyPage({
  onBusyChange,
  peerBusy = false,
}: {
  onBusyChange?: (busy: boolean) => void;
  peerBusy?: boolean;
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
  const [pending, setPending] = useState<Pending | null>(null);
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
  const selected = selectedId === "obd" ? null : nodes.find((n) => n.id === selectedId) || nodes[0];
  const stSel = selected ? statuses[selected.id] : null;
  const locked = busy || peerBusy;

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
    if (!list.length) {
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
    setError(null);
    setProgress(task === "clear" ? "清除中…" : "读取中…");
    const attachFlags = !fixture || requireConfirm(fixture);
    const ctx = fixture
      ? {
          mode: "simulation" as const,
          sessionTask: task,
          scenario: harnessScenario(),
          ...(attachFlags
            ? {
                x431Inactive: liveFlags.x431Inactive,
                confirmedReadOnly: liveFlags.confirmedReadOnly,
                confirmedClearDtc: liveFlags.confirmedClearDtc,
              }
            : {}),
        }
      : {
          mode: "live" as const,
          sessionTask: task,
          x431Inactive: liveFlags.x431Inactive,
          confirmedReadOnly: liveFlags.confirmedReadOnly,
          confirmedClearDtc: liveFlags.confirmedClearDtc,
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
    } catch (e) {
      if (mounted.current && token === runToken.current) setError(String(e));
    } finally {
      startLock.current = false;
      if (mounted.current && token === runToken.current) setBusy(false);
      queueRef.current = null;
    }
  }

  function requestTask(task: "read" | "clear", list: NodeT[]) {
    if (startLock.current || busy || peerBusy || pending) return;
    if (!list.length) return;
    if (!requireConfirm(fixture)) {
      startLock.current = true;
      void runTask(task, list, { x431Inactive: true, confirmedReadOnly: true, confirmedClearDtc: true }, true);
      return;
    }
    setPending({ task, nodes: list });
  }

  function confirmPending() {
    const p = pending;
    setPending(null);
    if (!p || startLock.current || busy || peerBusy) return;
    startLock.current = true;
    void runTask(
      p.task,
      p.nodes,
      {
        x431Inactive: true,
        confirmedReadOnly: p.task === "read" ? true : undefined,
        confirmedClearDtc: p.task === "clear" ? true : undefined,
      },
      true,
    );
  }

  const selectedAdapted = selected && isAdaptedProfile(selected.profileId);
  const obdSelected = selectedId === "obd";
  const actionsOff = locked || !canHardware;
  const selectedOff = actionsOff || !selectedAdapted;

  function chip(n: NodeT, key: string, index: number) {
    const st = statuses[n.id] || { kind: "unscanned", simulated: false };
    const kind = String(st.kind);
    const badge = dtcBadgeCount(st);
    return (
      <button
        key={key}
        type="button"
        className="topo-chip"
        style={{ gridColumn: Math.floor(index / 2) + 1, gridRow: index % 2 === 0 ? 1 : 3 }}
        title={`${n.label} · ${statusText(st)}`}
        data-testid={`topo-node-${n.id}`}
        data-kind={kind}
        data-stale={st.stale ? "1" : undefined}
        data-secondary={n.secondary ? "1" : undefined}
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
          {view === "list" ? (
            <table className="topo-list" data-testid="topo-list">
              <thead>
                <tr>
                  <th>模块</th>
                  <th>网络</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => {
                  const st = statuses[n.id] || { kind: "unscanned" };
                  const badge = dtcBadgeCount(st);
                  return (
                    <tr key={n.id} data-selected={selectedId === n.id ? "1" : undefined}>
                      <td>
                        <button type="button" className="btn" onClick={() => setSelectedId(n.id)}>
                          {n.short} {n.label}
                        </button>
                      </td>
                      <td>{n.branchLabel}</td>
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
                <button
                  type="button"
                  className="topo-chip topo-obd"
                  data-testid="topo-obd"
                  aria-pressed={obdSelected}
                  onClick={() => setSelectedId("obd")}
                >
                  OBD
                  <span className="sub">诊断</span>
                </button>
                <div className="topo-spine-v" aria-hidden />
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
                        style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.ceil(kids.length / 2))}, minmax(0, 1fr))` }}
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
            <div className="topo-primary">
              <button
                type="button"
                className="btn"
                data-testid="topo-read-all"
                disabled={actionsOff}
                onClick={() => requestTask("read", nodes)}
              >
                读所有系统
              </button>
              <button
                type="button"
                className="btn"
                data-testid="topo-clear-all"
                disabled={actionsOff}
                onClick={() => requestTask("clear", nodes)}
              >
                清故障码
              </button>
            </div>
            {pending ? (
              <div className="topo-confirm" data-testid="topo-confirm">
                <p>
                  {pending.task === "clear"
                    ? `将清除 ${pending.nodes.filter(canTransmit).map((n) => n.short).join("、")} 的故障码。请确认 X431 已退出诊断会话。`
                    : `将读取 ${pending.nodes.filter(canTransmit).map((n) => n.short).join("、")} 的身份与故障码。请确认 X431 已退出诊断会话。`}
                </p>
                <button type="button" className="btn" data-testid="topo-confirm-go" onClick={() => confirmPending()}>
                  确认
                </button>
                <button type="button" className="btn" data-testid="topo-confirm-cancel" onClick={() => setPending(null)}>
                  取消
                </button>
              </div>
            ) : null}
            {progress ? (
              <p className="muted" data-testid="topo-progress">
                {progress}
              </p>
            ) : null}
            {error ? (
              <p className="error" data-testid="topo-error">
                {error}
              </p>
            ) : null}

            {obdSelected ? (
              <dl>
                <dt>节点</dt>
                <dd>OBD 诊断插座</dd>
                <dt>诊断 CAN</dt>
                <dd>{gen?.diagnostic?.label}</dd>
                <dt>插座针脚</dt>
                <dd>{pinText(gen?.diagnostic?.high, gen?.diagnostic?.low)}（X001，不到五路主 CAN）</dd>
                <dt>网关诊断针脚</dt>
                <dd>{pinText(gen?.diagnostic?.gatewayHigh, gen?.diagnostic?.gatewayLow)}</dd>
              </dl>
            ) : selected ? (
              <>
                <dl>
                  <dt>模块</dt>
                  <dd>
                    {selected.short} {selected.label}
                    {selected.secondary ? "（该网上为交叉接口）" : ""}
                  </dd>
                  <dt>状态</dt>
                  <dd>{stSel ? statusText(stSel) : "—"}</dd>
                  {typeof stSel?.dtcCount === "number" && stSel.kind !== "unscanned" && stSel.kind !== "pending-adapt" ? (
                    <>
                      <dt>故障码</dt>
                      <dd>
                        {String(stSel.dtcCount)} 条{stSel.stale ? "（上次读取）" : ""}
                        {Array.isArray(stSel.records)
                          ? (stSel.records as Array<Record<string, string>>).map((r, i) => (
                              <div key={i}>
                                {dtcLabel(r)} {dtcDetail(r)}
                              </div>
                            ))
                          : null}
                      </dd>
                    </>
                  ) : null}
                  <dt>网络</dt>
                  <dd>
                    {selected.branchLabel} · {selected.connectionType || (selected.isGateway ? "Gateway" : "CAN")}
                  </dd>
                  {selected.additionalBranches?.length ? (
                    <>
                      <dt>全部接口</dt>
                      <dd>
                        主：{selected.branchLabel} {pinText(selected.gatewayPins?.high, selected.gatewayPins?.low)}
                        {(selected.additionalBranches || []).map((a) => {
                          const br = gen?.branches?.find((x) => x.id === a.branchId);
                          return (
                            <div key={a.branchId}>
                              另：{br?.label || a.branchId} {pinText(br?.gatewayPins?.high, br?.gatewayPins?.low)}
                            </div>
                          );
                        })}
                      </dd>
                    </>
                  ) : (
                    <>
                      <dt>{selected.isGateway ? "网关诊断针脚" : "网关针脚"}</dt>
                      <dd>
                        {selected.isGateway
                          ? pinText(gen?.diagnostic?.gatewayHigh, gen?.diagnostic?.gatewayLow)
                          : pinText(selected.gatewayPins?.high, selected.gatewayPins?.low)}
                      </dd>
                    </>
                  )}
                  {!selected.profileId ? (
                    <>
                      <dt>能力</dt>
                      <dd>待适配</dd>
                    </>
                  ) : null}
                </dl>
                <div className="topo-secondary">
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
                    disabled={selectedOff}
                    onClick={() => selected && requestTask("clear", [selected])}
                  >
                    清除此系统故障码
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
