import { useEffect, useRef, useState } from "react";
import {
  api,
  engineSessionFixtureEnabled,
  hasDesktopApi,
  type ReadOnlySessionRequest,
  type ReadOnlySessionResult,
} from "../api";
import {
  afterStartJobAssigned,
  afterStatusIpcFailure,
  buildEnginePrepareRequest,
  buildEngineStartRequest,
  canOperate,
  clampEngineOptions,
  ENGINE_PID_META,
  engineFreshness,
  enginePlanView,
  formatEngineValue,
  formatSampleAge,
  interpretStatusDoc,
  latestCycleOf,
  liveReady,
  pickEngineView,
  sampleCurrency,
} from "../read-only-session-logic.mjs";
import "../engine-session.css";

type Mode = "simulation" | "live";
type Scenario =
  | "success"
  | "identity-mismatch"
  | "negative"
  | "pending-timeout"
  | "disconnect"
  | "slow";

const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: "success", label: "成功" },
  { id: "identity-mismatch", label: "身份不匹配" },
  { id: "negative", label: "否定响应" },
  { id: "pending-timeout", label: "等待超时" },
  { id: "disconnect", label: "断开" },
  { id: "slow", label: "缓慢（用于取消）" },
];

type Sample = {
  pid: string;
  label?: string;
  value?: number;
  unit?: string;
  capturedUtc?: string;
  elapsedMs?: number;
  cycle?: number;
  synthetic?: boolean;
};

function latestByPid(samples: Sample[] | undefined) {
  const map = new Map<string, Sample>();
  for (const s of samples || []) {
    if (!s?.pid) continue;
    const prev = map.get(s.pid);
    if (!prev || (s.cycle || 0) >= (prev.cycle || 0)) map.set(s.pid, s);
  }
  return map;
}

export function EngineDataPage({
  onBusyChange,
  peerBusy = false,
}: {
  onBusyChange?: (busy: boolean) => void;
  peerBusy?: boolean;
}) {
  const desktop = hasDesktopApi() || engineSessionFixtureEnabled();
  const jobIdRef = useRef<string | null>(null);
  const startLock = useRef(false);
  const mountedRef = useRef(true);
  const pendingCancelRef = useRef(false);
  const jobModeRef = useRef<Mode>("simulation");
  const [mode, setMode] = useState<Mode>("simulation");
  const [scenario, setScenario] = useState<Scenario>("success");
  const [x431Off, setX431Off] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [sampleCycles, setSampleCycles] = useState(5);
  const [intervalMs, setIntervalMs] = useState(1000);
  const [planDoc, setPlanDoc] = useState<ReadOnlySessionResult | null>(null);
  const [status, setStatus] = useState<ReadOnlySessionResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyPlan, setBusyPlan] = useState(false);

  const running = status?.state === "running" || status?.state === "cancelling" || starting || Boolean(jobId);
  const locked = running || peerBusy;
  const liveOk = liveReady(x431Off, readOnly);
  const ops = canOperate(mode, locked, null, null, null, liveOk);

  useEffect(() => {
    onBusyChange?.(running);
  }, [running, onBusyChange]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const id = jobIdRef.current;
      if (id) void api().readOnlySession?.({ action: "cancel", jobId: id }).catch(() => {});
    };
  }, []);

  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const token = jobId;
    const tick = async () => {
      if (stop) return;
      const fn = api().readOnlySession;
      if (!fn) return;
      try {
        const doc = await fn({ action: "status", jobId: token });
        if (stop) return;
        const interpreted = await interpretStatusDoc(doc, token, fn);
        if (jobIdRef.current !== token && !interpreted.stop) return;
        if (!mountedRef.current) {
          if (interpreted.stop) {
            jobIdRef.current = null;
            startLock.current = false;
          }
          return;
        }
        setStatus(interpreted.doc || doc);
        if (interpreted.error) setError(interpreted.error);
        if (interpreted.stop) {
          jobIdRef.current = null;
          pendingCancelRef.current = false;
          setJobId(null);
          startLock.current = false;
          setStarting(false);
          return;
        }
      } catch (e) {
        if (stop) return;
        await afterStatusIpcFailure(token, fn);
        if (!mountedRef.current) {
          jobIdRef.current = null;
          startLock.current = false;
          return;
        }
        setError(String(e));
        jobIdRef.current = null;
        pendingCancelRef.current = false;
        setJobId(null);
        startLock.current = false;
        setStarting(false);
        setStatus(null);
        return;
      }
      if (!stop) setTimeout(tick, 400);
    };
    void tick();
    return () => {
      stop = true;
    };
  }, [jobId]);

  async function loadPlan() {
    setError(null);
    setBusyPlan(true);
    try {
      const doc = await api().readOnlySession!(buildEnginePrepareRequest({ sampleCycles, intervalMs }));
      if (!mountedRef.current) return;
      setPlanDoc(doc);
      if (!doc.ok) setError(doc.error || "计划失败");
    } catch (e) {
      if (mountedRef.current) setError(String(e));
    } finally {
      setBusyPlan(false);
    }
  }

  async function start() {
    if (startLock.current || jobIdRef.current || peerBusy) return;
    startLock.current = true;
    pendingCancelRef.current = false;
    jobModeRef.current = mode;
    setStarting(true);
    setError(null);
    setStatus(null);
    const fn = api().readOnlySession;
    if (!fn) {
      startLock.current = false;
      setStarting(false);
      return;
    }
    try {
      const opts = clampEngineOptions(sampleCycles, intervalMs);
      const req = buildEngineStartRequest({
        mode,
        scenario,
        confirmedReadOnly: readOnly,
        x431Inactive: x431Off,
        sampleCycles: opts.sampleCycles,
        intervalMs: opts.intervalMs,
      });
      if ("error" in req && req.error) {
        setError(req.error);
        startLock.current = false;
        setStarting(false);
        return;
      }
      const doc = await fn(req as ReadOnlySessionRequest);
      const next = afterStartJobAssigned(doc.jobId, pendingCancelRef.current, mountedRef.current);
      if (next.action === "fail" || !doc.ok) {
        if (mountedRef.current) {
          setError(doc.error || "无法开始");
          startLock.current = false;
          setStarting(false);
        }
        return;
      }
      jobIdRef.current = next.jobId!;
      if (next.action === "cancel-unmount") {
        await fn({ action: "cancel", jobId: next.jobId }).catch(() => {});
        return;
      }
      if (next.action === "cancel-pending") {
        setStatus({ ...doc, state: "cancelling", final: null, latest: null });
        setJobId(next.jobId!);
        await fn({ action: "cancel", jobId: next.jobId }).catch((e) => setError(String(e)));
        return;
      }
      setStatus({ ...doc, state: "running", final: null, latest: null });
      setJobId(next.jobId!);
    } catch (e) {
      if (mountedRef.current) {
        setError(String(e));
        startLock.current = false;
        setStarting(false);
      }
    }
  }

  async function cancel() {
    pendingCancelRef.current = true;
    const id = jobIdRef.current;
    if (!id) return;
    try {
      await api().readOnlySession?.({ action: "cancel", jobId: id });
    } catch (e) {
      setError(String(e));
    }
  }

  function exportJson() {
    const payload = status?.final || status || planDoc;
    if (!payload) return;
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const name = typeof status?.final?.runId === "string" ? status.final.runId : "engine-session";
    a.download = `${name}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  if (!desktop) {
    return (
      <section className="panel" data-page="engine-session">
        <h2>发动机采样</h2>
        <p className="callout">需要桌面端。浏览器不会连接车辆。</p>
      </section>
    );
  }

  const view = pickEngineView(status);
  const engine = view.engine as {
    supportedPids?: string[];
    unsupportedPids?: string[];
    samples?: Sample[];
    completedCycles?: number;
    sampleCycles?: number;
    intervalMs?: number;
  } | null;
  const freshness = engineFreshness(status?.state, status?.final, status?.error || error, jobModeRef.current);
  const liveDisplay = freshness === "sampling";
  const byPid = latestByPid(engine?.samples);
  const plan = planDoc?.plan && typeof planDoc.plan === "object" ? (planDoc.plan as Record<string, unknown>) : null;
  const planView = enginePlanView(plan);
  const nowMs = Date.now();
  const latestCycle = latestCycleOf(engine?.samples);
  const waiting =
    (freshness === "sampling" || freshness === "sampling-sim") &&
    Number.isInteger(engine?.completedCycles) &&
    Number.isInteger(engine?.sampleCycles) &&
    (engine?.completedCycles || 0) < (engine?.sampleCycles || 0);
  const progress = status?.latest?.type === "progress" ? status.latest : null;
  const stateLabel: Record<string, string> = {
    running: "采样中",
    cancelling: "正在取消",
    completed: "已完成",
    cancelled: "已取消",
    failed: "未完成",
    identity: "核对身份",
    engine: "读取参数",
  };
  const progressText = progress
    ? `${stateLabel[String(progress.stage)] || "进行中"} · 步骤 ${String(progress.completed)} / ${String(progress.total)}`
    : stateLabel[status?.state || ""] || (starting ? "正在启动…" : running ? "采样中…" : "尚未开始");
  const kindText =
    freshness === "sampling"
      ? "正在实车采样（非缓存回放）"
      : freshness === "sampling-sim"
        ? "正在模拟采样，不是实车数据"
        : freshness === "cancelling"
          ? "正在取消，数值已中断"
          : freshness === "simulated"
            ? "模拟结果，不是实车已验证读取"
            : freshness === "historical"
              ? "历史结果，不是正在更新的实车数据"
              : freshness === "stale"
                ? "已失效或已中断，不是实时数据"
                : "尚无结果";

  return (
    <div className="eng" data-page="engine-session">
      <section className="panel">
        <h2>发动机采样</h2>
        <p className="muted">默认模拟。读取六项发动机参数；定义为通用标准，本车尚未实车验证。清故障码在「系统拓扑」。</p>
        <div className="eng-toolbar">
          <label>
            方式
            <select data-testid="eng-mode" value={mode} disabled={locked} onChange={(e) => setMode(e.target.value as Mode)}>
              <option value="simulation">模拟</option>
              <option value="live">实车</option>
            </select>
          </label>
          {mode === "simulation" ? (
            <label>
              模拟情景
              <select
                data-testid="eng-scenario"
                value={scenario}
                disabled={locked}
                onChange={(e) => setScenario(e.target.value as Scenario)}
              >
                {SCENARIOS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            采样轮次
            <input
              data-testid="eng-cycles"
              type="number"
              min={1}
              max={10}
              value={sampleCycles}
              disabled={locked || busyPlan}
              onChange={(e) => {
                setSampleCycles(Number(e.target.value));
                setPlanDoc(null);
              }}
            />
          </label>
          <label>
            间隔（毫秒）
            <input
              data-testid="eng-interval"
              type="number"
              min={500}
              max={5000}
              step={100}
              value={intervalMs}
              disabled={locked || busyPlan}
              onChange={(e) => {
                setIntervalMs(Number(e.target.value));
                setPlanDoc(null);
              }}
            />
          </label>
          <button type="button" data-testid="eng-prepare" disabled={busyPlan || locked} onClick={() => loadPlan()}>
            加载计划
          </button>
          <button type="button" data-testid="eng-start" disabled={!ops.start} onClick={() => start()}>
            {mode === "live" ? "读取实车发动机参数" : "开始模拟"}
          </button>
          <button type="button" data-testid="eng-cancel" disabled={!jobId && !starting} onClick={() => cancel()}>
            取消
          </button>
          <button type="button" data-testid="eng-restart" disabled={!ops.start || running} onClick={() => start()}>
            重新采样
          </button>
          <button type="button" data-testid="eng-export" disabled={!status?.final && !planDoc} onClick={() => exportJson()}>
            导出 JSON
          </button>
        </div>
        {mode === "live" ? (
          <div className="eng-checks">
            <label>
              <input data-testid="eng-x431" type="checkbox" checked={x431Off} onChange={(e) => setX431Off(e.target.checked)} />
              X431 已退出诊断会话
            </label>
            <label>
              <input
                data-testid="eng-readonly"
                type="checkbox"
                checked={readOnly}
                onChange={(e) => setReadOnly(e.target.checked)}
              />
              仅执行已定义的只读发动机采样
            </label>
          </div>
        ) : null}
        {peerBusy ? <p className="muted" data-testid="eng-peer-busy">已有其它诊断任务在进行</p> : null}
        {error ? <p className="error" data-testid="eng-error">{error}</p> : null}
      </section>

      <section className="panel">
        <h3>计划</h3>
        <p className="muted" data-testid="eng-plan">
          {planView.text}
        </p>
      </section>

      <section className="panel">
        <h3>进度</h3>
        <p className="eng-progress" data-testid="eng-progress">
          {progressText}
        </p>
        <p className="eng-kind" data-testid="eng-kind" data-freshness={freshness}>
          {kindText}
        </p>
      </section>

      <section className="panel">
        <h3>PID</h3>
        <p className="muted" data-testid="eng-support">
          支持：{(engine?.supportedPids || []).join(", ") || "—"}；不支持：{(engine?.unsupportedPids || []).join(", ") || "—"}
          {engine?.completedCycles != null ? ` · 第 ${engine.completedCycles}/${engine.sampleCycles} 轮` : ""}
        </p>
        <ul className="eng-pids" data-testid="eng-pids" data-live={liveDisplay ? "1" : "0"}>
          {Object.keys(ENGINE_PID_META).map((pid) => {
            const meta = ENGINE_PID_META[pid as keyof typeof ENGINE_PID_META];
            const sample = byPid.get(pid);
            const unsupported = (engine?.unsupportedPids || []).includes(pid);
            const currency = sampleCurrency(sample, {
              freshness,
              latestCycle,
              nowMs,
              intervalMs: engine?.intervalMs ?? intervalMs,
              waiting,
            });
            const unit = sample?.unit || meta.unit;
            const valueText = unsupported
              ? "不支持"
              : sample && freshness === "stale"
                ? "已失效"
                : sample && currency.show
                  ? `${formatEngineValue(sample.value, unit)}${currency.stale ? "（等待更新）" : ""}`
                  : "—";
            const age = sample?.capturedUtc && liveDisplay ? formatSampleAge(sample.capturedUtc, nowMs) : "";
            return (
              <li
                key={pid}
                className="eng-pid"
                data-pid={pid}
                data-unsupported={unsupported ? "1" : undefined}
                data-stale={currency.stale ? "1" : undefined}
              >
                <dl>
                  <dt>{meta.label}</dt>
                  <dd>{valueText}</dd>
                  {sample?.capturedUtc && (liveDisplay || freshness === "simulated" || freshness === "historical" || freshness === "sampling-sim") ? (
                    <time dateTime={sample.capturedUtc}>
                      {liveDisplay ? `上次更新 ${age}` : sample.capturedUtc}
                    </time>
                  ) : null}
                </dl>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
