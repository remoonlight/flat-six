/** Shared start/resume and status-tick rules. Used by the page and selfcheck. */

export function liveReady(x431Inactive, confirmedReadOnly) {
  return x431Inactive === true && confirmedReadOnly === true;
}

export function canOperate(mode, locked, lastRunId, lastRunKey, selKey, liveOk) {
  const allowed = mode === "simulation" || liveOk === true;
  return {
    start: !locked && allowed,
    resume: !locked && allowed && Boolean(lastRunId && lastRunKey === selKey),
  };
}

export const ENGINE_PROFILE_ID = "porsche-981-2014-dme";

export const ENGINE_PID_META = Object.freeze({
  "04": { label: "发动机负荷", unit: "%" },
  "05": { label: "冷却液温度", unit: "°C" },
  "0C": { label: "转速", unit: "rpm" },
  "0D": { label: "车速", unit: "km/h" },
  "0F": { label: "进气温度", unit: "°C" },
  "11": { label: "节气门", unit: "%" },
});

export function clampEngineOptions(sampleCycles, intervalMs) {
  let cycles = sampleCycles == null ? 5 : sampleCycles;
  let interval = intervalMs == null ? 1000 : intervalMs;
  if (!Number.isInteger(cycles) || cycles < 1) cycles = 5;
  if (cycles > 10) cycles = 10;
  if (!Number.isInteger(interval) || interval < 500) interval = 1000;
  if (interval > 5000) interval = 5000;
  return { sampleCycles: cycles, intervalMs: interval };
}

export function buildEnginePrepareRequest({ sampleCycles, intervalMs }) {
  const opts = clampEngineOptions(sampleCycles, intervalMs);
  return {
    action: "prepare",
    profileId: ENGINE_PROFILE_ID,
    mode: "simulation",
    sessionTask: "engine",
    sampleCycles: opts.sampleCycles,
    intervalMs: opts.intervalMs,
  };
}

export function afterStartJobAssigned(jobId, pendingCancel, mounted) {
  if (!jobId) return { action: "fail" };
  if (!mounted) return { action: "cancel-unmount", jobId };
  if (pendingCancel) return { action: "cancel-pending", jobId };
  return { action: "accept", jobId };
}

export function buildEngineStartRequest({
  mode,
  scenario,
  confirmedReadOnly,
  x431Inactive,
  sampleCycles,
  intervalMs,
}) {
  const opts = clampEngineOptions(sampleCycles, intervalMs);
  if (mode === "live") {
    if (confirmedReadOnly !== true || x431Inactive !== true) {
      return { error: "live_confirmation_required" };
    }
    return {
      action: "start",
      profileId: ENGINE_PROFILE_ID,
      mode: "live",
      sessionTask: "engine",
      confirmedReadOnly: true,
      x431Inactive: true,
      sampleCycles: opts.sampleCycles,
      intervalMs: opts.intervalMs,
    };
  }
  return {
    action: "start",
    profileId: ENGINE_PROFILE_ID,
    mode: mode || "simulation",
    sessionTask: "engine",
    scenario,
    sampleCycles: opts.sampleCycles,
    intervalMs: opts.intervalMs,
  };
}

export function engineFreshness(state, final, error, jobMode) {
  const mode = (final && typeof final.mode === "string" ? final.mode : null) || jobMode || "simulation";
  if (state === "cancelling") return "cancelling";
  if (state === "running") return mode === "live" ? "sampling" : "sampling-sim";
  if (state === "cancelled" || error === "cancelled") return "stale";
  if (state === "failed" || error) return "stale";
  if (final && (final.status === "completed" || state === "completed")) {
    return final.mode === "live" ? "historical" : "simulated";
  }
  return "idle";
}

export function formatEngineValue(value, unit) {
  if (value == null || typeof value !== "number" || !Number.isFinite(value)) return "—";
  const u = unit === "C" || unit === "°C" ? "°C" : unit || "";
  const rounded = Math.round(value * 100) / 100;
  const text = Number.isInteger(rounded) ? String(rounded) : String(rounded);
  return u ? `${text} ${u}` : text;
}

export function formatSampleAge(capturedUtc, nowMs = Date.now()) {
  const t = Date.parse(capturedUtc);
  if (!Number.isFinite(t)) return "";
  const sec = Math.max(0, (nowMs - t) / 1000);
  if (sec < 2) return "刚刚";
  return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)} 秒前`;
}

export function latestCycleOf(samples) {
  let n = 0;
  for (const s of samples || []) {
    if (Number.isInteger(s?.cycle) && s.cycle > n) n = s.cycle;
  }
  return n || null;
}

export function sampleCurrency(sample, { freshness, latestCycle, nowMs, intervalMs, waiting }) {
  if (!sample) return { show: false, stale: false };
  if (freshness === "stale") return { show: true, stale: true };
  if (freshness === "cancelling") return { show: true, stale: true };
  if (freshness === "simulated" || freshness === "historical" || freshness === "sampling-sim") {
    return { show: true, stale: false };
  }
  if (freshness === "sampling") {
    const cycleBehind = latestCycle != null && Number.isInteger(sample.cycle) && sample.cycle < latestCycle;
    const age = nowMs - Date.parse(sample.capturedUtc || "");
    const oldWait = Boolean(waiting) && Number.isFinite(age) && Number.isInteger(intervalMs) && age >= intervalMs;
    return { show: true, stale: cycleBehind || oldWait };
  }
  return { show: false, stale: false };
}

export function enginePlanView(plan) {
  const engine = plan && typeof plan === "object" && plan.engine && typeof plan.engine === "object" ? plan.engine : null;
  if (!engine) return { loaded: false, names: [], sampleCycles: null, intervalMs: null, text: "尚未加载" };
  const defs = Array.isArray(engine.definitions) ? engine.definitions : [];
  const names = [];
  for (const d of defs) {
    const pid = typeof d === "object" && d ? String(d.pid || "") : String(d || "");
    if (!pid) continue;
    names.push(ENGINE_PID_META[pid]?.label || pid);
  }
  const sampleCycles = Number.isInteger(engine.sampleCycles) ? engine.sampleCycles : null;
  const intervalMs = Number.isInteger(engine.intervalMs) ? engine.intervalMs : null;
  const bits = [];
  if (names.length) bits.push(`参数：${names.join("、")}`);
  if (sampleCycles != null) bits.push(`采样 ${sampleCycles} 轮`);
  if (intervalMs != null) bits.push(`间隔 ${intervalMs} 毫秒`);
  bits.push(engine.acquisitionRoute?.protocol === "standard-mode01"
    ? "标准 OBD；本车已有采集，桌面整合待实车验收"
    : "通用定义，本车尚未实车验证");
  return { loaded: true, names, sampleCycles, intervalMs, text: bits.join(" · ") };
}

export function pickEngineView(status) {
  const final = status?.final;
  if (final && typeof final === "object" && final.engine && typeof final.engine === "object") {
    return { engine: final.engine, source: "final", simulated: final.mode !== "live" };
  }
  const latest = status?.latest;
  if (latest && latest.type === "progress" && latest.engine && typeof latest.engine === "object") {
    return { engine: latest.engine, source: "progress", simulated: status?.final == null };
  }
  return { engine: null, source: null, simulated: null };
}

export function buildStartRequest({
  mode,
  profileId,
  scenario,
  resume,
  lastRunId,
  lastRunKey,
  selKey,
  confirmedReadOnly,
  x431Inactive,
}) {
  if (mode === "live") {
    if (confirmedReadOnly !== true || x431Inactive !== true) {
      return { error: "live_confirmation_required" };
    }
    return {
      action: "start",
      profileId,
      mode,
      confirmedReadOnly,
      x431Inactive,
      ...(resume && lastRunId && lastRunKey === selKey ? { resumeRunId: lastRunId } : {}),
    };
  }
  return {
    action: "start",
    profileId,
    mode: mode || "simulation",
    scenario,
    ...(resume && lastRunId && lastRunKey === selKey ? { resumeRunId: lastRunId } : {}),
  };
}

export async function afterStatusIpcFailure(token, invoke) {
  if (!token || !invoke) return;
  try {
    await invoke({ action: "cancel", jobId: token });
  } catch {
    /* */
  }
}

export async function interpretStatusDoc(doc, token, invoke) {
  if (!doc || doc.ok === false) {
    await afterStatusIpcFailure(token, invoke);
    return { stop: true, error: (doc && doc.error) || "status_failed", doc };
  }
  const terminal = Boolean(doc.state && doc.state !== "running" && doc.state !== "cancelling");
  return { stop: terminal, terminal, doc };
}
