import { useCallback, useEffect, useRef, useState } from "react";
import { api, hasDesktopApi, readOnlySessionFixtureEnabled, type ReadOnlySessionRequest, type ReadOnlySessionResult } from "../api";
import {
  afterStatusIpcFailure,
  buildStartRequest,
  canOperate,
  interpretStatusDoc,
  liveReady,
} from "../read-only-session-logic.mjs";
import "../read-only-session.css";

type ProfileId = "porsche-981-2014-dme" | "porsche-981-2014-gateway";
type Mode = "simulation" | "live";
type Scenario =
  | "success"
  | "identity-mismatch"
  | "negative"
  | "pending-timeout"
  | "disconnect"
  | "slow";

const PROFILES: { id: ProfileId; label: string }[] = [
  { id: "porsche-981-2014-dme", label: "DME（发动机）" },
  { id: "porsche-981-2014-gateway", label: "网关" },
];

const SCENARIOS: { id: Scenario; label: string }[] = [
  { id: "success", label: "成功" },
  { id: "identity-mismatch", label: "身份不匹配" },
  { id: "negative", label: "否定响应" },
  { id: "pending-timeout", label: "等待超时" },
  { id: "disconnect", label: "断开" },
  { id: "slow", label: "缓慢（用于取消）" },
];

const FIELD_ZH: Record<string, string> = {
  dsn: "DSN",
  software: "软件",
  hardware: "硬件",
  porschePart: "保时捷零件号",
  hardwarePart: "硬件零件号",
  system: "系统",
  identification: "识别",
  dataRecord: "数据记录",
};

function asPlan(doc: ReadOnlySessionResult | null): Record<string, unknown> | null {
  const p = doc?.plan;
  if (p && typeof p === "object" && !Array.isArray(p)) return p as Record<string, unknown>;
  return null;
}

function sessionLine(plan: Record<string, unknown> | null): string | null {
  const s = plan?.session;
  if (!s || typeof s !== "object" || Array.isArray(s)) return null;
  const o = s as Record<string, unknown>;
  if (!o.requestHex) return null;
  return `诊断会话 ${String(o.requestHex)} → ${String(o.expectedPositiveHex || "")}`.trim();
}

function identLines(plan: Record<string, unknown> | null): string[] {
  const arr = plan?.identityOperations;
  if (!Array.isArray(arr)) return [];
  return arr.map((it) => {
    if (!it || typeof it !== "object") return String(it);
    const o = it as Record<string, unknown>;
    const field = String(o.field || "");
    return `身份 · ${FIELD_ZH[field] || field}`;
  });
}

function depLines(plan: Record<string, unknown> | null): string[] {
  const arr = plan?.dependentOperations;
  if (!Array.isArray(arr)) return [];
  return arr.map((it) => {
    if (!it || typeof it !== "object") return String(it);
    const o = it as Record<string, unknown>;
    const kind = String(o.kind || "");
    const label = kind.includes("vin") ? "VIN" : "故障码";
    return `${label} · ${String(o.operationId || "")}`;
  });
}

function blockedLines(plan: Record<string, unknown> | null): string[] {
  const arr = plan?.blockedCandidates;
  if (!Array.isArray(arr)) return [];
  return arr.map((it) => {
    if (!it || typeof it !== "object") return String(it);
    const o = it as Record<string, unknown>;
    const reason = String(o.reason || "");
    const zh: Record<string, string> = {
      "offline-plan-not-executable-authority": "离线计划不能当作实车授权",
      "session-not-arbitrary-sid10": "会话子功能仅限已观察值",
      "public-candidates-not-admitted": "公开候选测量未纳入",
      "not-liveAllowed": "未允许实车的定义",
    };
    return zh[reason] || reason;
  });
}

export function ReadOnlySessionPage({
  onBusyChange,
  peerBusy = false,
}: {
  onBusyChange?: (busy: boolean) => void;
  peerBusy?: boolean;
}) {
  const desktop = hasDesktopApi() || readOnlySessionFixtureEnabled();
  const jobIdRef = useRef<string | null>(null);
  const startLock = useRef(false);
  const mountedRef = useRef(true);
  const selRef = useRef(0);
  const seed =
    import.meta.env.MODE === "session-test" && typeof window !== "undefined"
      ? window.__ROS_SEED_LIVE_RUN__
      : undefined;
  const [profileId, setProfileId] = useState<ProfileId>("porsche-981-2014-dme");
  const [mode, setMode] = useState<Mode>("simulation");
  const [scenario, setScenario] = useState<Scenario>("success");
  const [x431Off, setX431Off] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [planDoc, setPlanDoc] = useState<ReadOnlySessionResult | null>(null);
  const [status, setStatus] = useState<ReadOnlySessionResult | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [lastRunId, setLastRunId] = useState<string | null>(seed?.runId ?? null);
  const [lastRunKey, setLastRunKey] = useState<string | null>(seed?.key ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busyPlan, setBusyPlan] = useState(false);

  const running = status?.state === "running" || status?.state === "cancelling" || starting || Boolean(jobId);
  const selKey = `${profileId}:${mode}`;
  const locked = running || peerBusy;

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
    setX431Off(false);
    setReadOnly(false);
    selRef.current += 1;
    setPlanDoc(null);
    setError(null);
    setBusyPlan(false);
    if (!jobIdRef.current && !startLock.current) {
      setStatus(null);
      setJobId(null);
    }
  }, [profileId, mode, scenario]);

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
        setStatus(interpreted.doc || doc);
        if (interpreted.error) setError(interpreted.error);
        const runId = typeof interpreted.doc?.final?.runId === "string" ? interpreted.doc.final.runId : null;
        if (runId && interpreted.terminal) {
          setLastRunId(runId);
          setLastRunKey(selKey);
        }
        if (interpreted.stop) {
          jobIdRef.current = null;
          setJobId(null);
          startLock.current = false;
          setStarting(false);
          return;
        }
      } catch (e) {
        if (!stop) {
          await afterStatusIpcFailure(token, fn);
          setError(String(e));
          jobIdRef.current = null;
          setJobId(null);
          startLock.current = false;
          setStarting(false);
        }
        return;
      }
      if (!stop) setTimeout(tick, 400);
    };
    void tick();
    return () => {
      stop = true;
    };
  }, [jobId, selKey]);

  const loadPlan = useCallback(async () => {
    setError(null);
    const token = ++selRef.current;
    setBusyPlan(true);
    try {
      const doc = await api().readOnlySession!({
        action: "prepare",
        profileId,
        mode: "simulation",
      });
      if (!mountedRef.current || token !== selRef.current) return;
      setPlanDoc(doc);
      if (!doc.ok) setError(doc.error || "计划失败");
    } catch (e) {
      if (mountedRef.current && token === selRef.current) setError(String(e));
    } finally {
      if (token === selRef.current) setBusyPlan(false);
    }
  }, [profileId]);

  async function start(resume?: boolean) {
    if (startLock.current || jobIdRef.current) return;
    startLock.current = true;
    setStarting(true);
    setError(null);
    const token = selRef.current;
    const fn = api().readOnlySession;
    if (!fn) {
      startLock.current = false;
      setStarting(false);
      return;
    }
    try {
      const req = buildStartRequest({
        mode,
        profileId,
        scenario,
        resume,
        lastRunId,
        lastRunKey,
        selKey,
        confirmedReadOnly: readOnly,
        x431Inactive: x431Off,
      });
      if ("error" in req && req.error) {
        setError(req.error);
        startLock.current = false;
        setStarting(false);
        return;
      }
      const doc = await fn(req as ReadOnlySessionRequest);
      if (!mountedRef.current || token !== selRef.current) {
        if (doc.jobId) {
          jobIdRef.current = doc.jobId;
          await fn({ action: "cancel", jobId: doc.jobId }).catch(() => {});
          jobIdRef.current = null;
        }
        return;
      }
      if (!doc.ok || !doc.jobId) {
        setError(doc.error || "无法开始");
        startLock.current = false;
        setStarting(false);
        return;
      }
      jobIdRef.current = doc.jobId;
      setStatus({ ...doc, state: "running", final: null, latest: null });
      setJobId(doc.jobId);
    } catch (e) {
      if (mountedRef.current && token === selRef.current) {
        setError(String(e));
        startLock.current = false;
        setStarting(false);
      }
    }
  }

  async function cancel() {
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
    const name = typeof status?.final?.runId === "string" ? status.final.runId : "readonly-session";
    a.download = `${name}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  if (!desktop) {
    return (
      <section className="panel" data-page="read-only-session">
        <h2>只读采集</h2>
        <p className="callout">需要桌面端。浏览器不会连接车辆。</p>
      </section>
    );
  }

  const plan = asPlan(planDoc);
  const lines = [sessionLine(plan), ...identLines(plan), ...depLines(plan)].filter(Boolean) as string[];
  const blocked = blockedLines(plan);
  const liveOk = liveReady(x431Off, readOnly);
  const ops = canOperate(mode, locked, lastRunId, lastRunKey, selKey, liveOk);
  const canStart = ops.start;
  const canResume = ops.resume;
  const final = status?.final;
  const failedTransport = Boolean(status?.error && status.state === "failed");
  const simulated = final ? final.mode !== "live" : null;
  const stateLabel: Record<string, string> = {
    running: "采集中", cancelling: "正在取消", completed: "已完成",
    cancelled: "已取消", failed: "未完成",
    configure: "配置适配器", session: "建立诊断会话", identity: "核对身份", read: "读取数据",
  };
  const progress = status?.latest?.type === "progress" ? status.latest : null;
  const progressText = progress
    ? `${stateLabel[String(progress.stage)] || String(progress.stage)} · ${String(progress.completed)} / ${String(progress.total)}`
    : stateLabel[status?.state || ""] || (running ? "采集中…" : "尚未开始");

  return (
    <div className="ros" data-page="read-only-session">
      <section className="panel">
        <h2>只读采集</h2>
        <p className="muted">本页只做已定义的身份与故障码读取。清故障码在「系统拓扑」。发动机 PID 在「实时数据」。独立车辆验证尚未完成。</p>
        <div className="ros-toolbar">
          <label>
            控制单元
            <select
              data-testid="ros-profile"
              value={profileId}
              disabled={locked}
              onChange={(e) => setProfileId(e.target.value as ProfileId)}
            >
              {PROFILES.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            方式
            <select
              data-testid="ros-mode"
              value={mode}
              disabled={locked}
              onChange={(e) => setMode(e.target.value as Mode)}
            >
              <option value="simulation">模拟</option>
              <option value="live">实车</option>
            </select>
          </label>
          {mode === "simulation" ? (
            <label>
              模拟情景
              <select
                data-testid="ros-scenario"
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
          <button type="button" data-testid="ros-prepare" disabled={busyPlan || locked} onClick={() => loadPlan()}>
            加载计划
          </button>
          {mode === "live" ? (
            <button type="button" data-testid="ros-start" disabled={!canStart} onClick={() => start(false)}>
              读取实车身份与故障码
            </button>
          ) : (
            <button type="button" data-testid="ros-start" disabled={!canStart} onClick={() => start(false)}>
              开始模拟
            </button>
          )}
          <button type="button" data-testid="ros-cancel" disabled={!jobId && !starting} onClick={() => cancel()}>
            取消
          </button>
          <button type="button" data-testid="ros-resume" disabled={!canResume} onClick={() => start(true)}>
            按上次运行继续
          </button>
          <button type="button" data-testid="ros-export" disabled={!final && !planDoc} onClick={() => exportJson()}>
            导出 JSON
          </button>
        </div>
        {mode === "live" ? (
          <div className="ros-checks">
            <label>
              <input
                data-testid="ros-x431"
                type="checkbox"
                checked={x431Off}
                onChange={(e) => setX431Off(e.target.checked)}
              />
              X431 已退出诊断会话
            </label>
            <label>
              <input
                data-testid="ros-readonly"
                type="checkbox"
                checked={readOnly}
                onChange={(e) => setReadOnly(e.target.checked)}
              />
              仅执行已定义的身份读取和故障码读取
            </label>
          </div>
        ) : null}
        {error ? <p className="error">{error}</p> : null}
      </section>

      <section className="panel">
        <h3>计划</h3>
        <ul className="plain-list" data-testid="ros-plan">
          {lines.length === 0 ? <li className="muted">尚未加载。步骤以计划为准，不预设成功条数。</li> : null}
          {lines.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
        {blocked.length ? (
          <p className="muted" data-testid="ros-blocked">
            未纳入：{blocked.join("；")}
          </p>
        ) : null}
      </section>

      <section className="panel">
        <h3>进度</h3>
        <p className="ros-progress" data-testid="ros-progress">
          {progressText}
        </p>
      </section>

      <section className="panel">
        <h3>结果</h3>
        <p className="ros-kind" data-testid="ros-kind">
          {failedTransport
            ? "采集失败（进程或协议错误，不以成功结束）"
            : final
              ? simulated
                ? status?.resumed
                  ? "模拟继续：已完成项会跳过，不是新的故障码扫描"
                  : "模拟结果，不是实车已验证读取"
                : status?.resumed
                  ? "实车继续：已完成项会跳过，不是新的故障码扫描"
                  : "实车读取结果；独立验证尚未完成"
              : "尚无结果"}
        </p>
        {final || failedTransport ? (
          <p>{failedTransport ? "采集未完成" : stateLabel[String(final?.status)] || "采集结束"}
            {status?.error || final?.error ? `：${String(status?.error || final?.error)}` : "。原始记录已保存，可导出查看。"}
          </p>
        ) : null}
        <details>
          <summary>查看详细结果与原始响应</summary>
          <pre className="ros-result" data-testid="ros-result">
            {failedTransport ? status?.error : final ? JSON.stringify(final, null, 2) : status?.error || ""}
          </pre>
        </details>
      </section>
    </div>
  );
}
