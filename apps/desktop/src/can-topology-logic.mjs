/** Topology node status + sequential read-only scan. No ECU write. */

export const ADAPTED_PROFILES = Object.freeze([
  "porsche-981-2014-gateway",
  "porsche-981-2014-dme",
]);

export const PROFILE_SET = new Set(ADAPTED_PROFILES);

export const DTC_OPS = Object.freeze({
  "porsche-981-2014-dme": { operationId: "dme-dtc", decodeType: "kwp-dtc-18" },
  "porsche-981-2014-gateway": { operationId: "gw-dtc", decodeType: "uds-dtc-19-02" },
});

export const KIND = Object.freeze({
  unscanned: "unscanned",
  scanning: "scanning",
  commFail: "comm-fail",
  identity: "identity-mismatch",
  cancelled: "cancelled",
  notInstalled: "not-installed",
  pendingAdapt: "pending-adapt",
  assemblyUnconfirmed: "assembly-unconfirmed",
  dtc: "dtc-present",
  noDtc: "no-dtc",
  decodeError: "decode-error",
  partial: "partial",
  rejected: "rejected",
});

const COMM_ERRORS = new Set([
  "disconnect",
  "pending-timeout",
  "negative",
  "session-lock-busy",
  "adapter-missing",
  "port-error",
  "timeout",
]);

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const POLL_BUDGET_MS = 110_000;
const CANCEL_WAIT_MS = 8_000;

export function isAdaptedProfile(id) {
  return typeof id === "string" && PROFILE_SET.has(id);
}

export function generationOf(seed, id) {
  return (seed?.generations || []).find((g) => g.id === id) || null;
}

function pickNode(preferred, extra) {
  if (!preferred) return extra;
  if (!extra) return preferred;
  return preferred;
}

/** Union 981+982 display graph. Same module id wins 981 fields (profiles). Source records are not merged. */
export function combinedGeneration(seed) {
  const a = generationOf(seed, "981");
  const b = generationOf(seed, "982");
  if (!a && !b) return null;
  if (!a) return { ...b, id: "combined" };
  if (!b) return { ...a, id: "combined" };
  const ids = [];
  const seen = new Set();
  for (const br of [...(a.branches || []), ...(b.branches || [])]) {
    if (!br?.id || seen.has(br.id)) continue;
    seen.add(br.id);
    ids.push(br.id);
  }
  const branches = ids.map((id) => {
    const ba = (a.branches || []).find((x) => x.id === id);
    const bb = (b.branches || []).find((x) => x.id === id);
    const byId = new Map();
    for (const n of bb?.nodes || []) byId.set(n.id, n);
    for (const n of ba?.nodes || []) byId.set(n.id, pickNode(n, byId.get(n.id)));
    const base = ba || bb;
    return { ...base, id, nodes: [...byId.values()] };
  });
  return {
    id: "combined",
    diagnostic: a.diagnostic || b.diagnostic,
    gateway: a.gateway || b.gateway,
    branches,
  };
}

function enrich(n, branch, extra = {}) {
  return {
    ...n,
    branchId: branch.id,
    branchLabel: branch.label,
    branchColor: branch.color,
    gatewayPins: branch.gatewayPins || null,
    isGateway: false,
    ...extra,
  };
}

export function flattenNodes(gen) {
  if (!gen) return [];
  const gw = gen.gateway
    ? [
        {
          ...gen.gateway,
          branchId: "gateway",
          branchLabel: "网关",
          branchColor: "#6b1c1c",
          gatewayPins: null,
          isGateway: true,
        },
      ]
    : [];
  const rest = [];
  const seen = new Set();
  for (const b of gen.branches || []) {
    for (const n of b.nodes || []) {
      if (seen.has(n.id)) continue;
      seen.add(n.id);
      rest.push(enrich(n, b));
    }
  }
  return [...gw, ...rest];
}

export function branchAppearances(gen) {
  const branches = gen?.branches || [];
  return branches.map((b) => {
    const items = (b.nodes || []).map((n) => enrich(n, b, { secondary: false }));
    for (const ob of branches) {
      if (ob.id === b.id) continue;
      for (const n of ob.nodes || []) {
        for (const extra of n.additionalBranches || []) {
          if (extra.branchId === b.id) {
            items.push(
              enrich(n, b, {
                secondary: true,
                additionalNote: extra.notes,
                additionalSourcePages: extra.sourcePages,
              }),
            );
          }
        }
      }
    }
    return { ...b, appearances: items };
  });
}

export function defaultKind(node, _generationId) {
  if (isAdaptedProfile(node?.profileId)) return KIND.unscanned;
  return KIND.pendingAdapt;
}

export function statusText(entry, { simulated } = {}) {
  const kind = entry?.kind || KIND.unscanned;
  const dual =
    kind === KIND.pendingAdapt || kind === KIND.assemblyUnconfirmed
      ? "待适配"
      : {
          [KIND.unscanned]: "未扫描",
          [KIND.scanning]: "扫描中",
          [KIND.commFail]: "通讯失败",
          [KIND.identity]: "身份不匹配",
          [KIND.cancelled]: "已取消",
          [KIND.notInstalled]: "未装配",
          [KIND.dtc]: "有故障码",
          [KIND.noDtc]: "无故障码",
          [KIND.decodeError]: "解码失败",
          [KIND.partial]: "部分完成",
          [KIND.rejected]: "已拒绝",
        }[kind] || kind;
  const sim = simulated ?? entry?.simulated;
  let text = dual;
  if (sim && kind !== KIND.unscanned && kind !== KIND.pendingAdapt && kind !== KIND.assemblyUnconfirmed) {
    text = `模拟 · ${dual}`;
  }
  if (entry?.stale && kind !== KIND.noDtc && kind !== KIND.dtc) {
    text = `${text} · 上次读取`;
  }
  return text;
}

export function isHealthyGreen(kind) {
  return kind === KIND.noDtc;
}

export function dtcLabel(rec) {
  if (!rec || typeof rec !== "object") return "?";
  return rec.displayCode || rec.dtcHex || rec.code || "?";
}

export function dtcDetail(rec) {
  if (!rec || typeof rec !== "object") return "";
  return [rec.observedText, rec.observedStatusText].filter(Boolean).join(" · ");
}

export function findDtcResult(final, profileId) {
  const spec = DTC_OPS[profileId] || DTC_OPS[final?.profileId];
  const rows = Array.isArray(final?.results) ? final.results : [];
  if (spec) {
    return rows.find((r) => r && r.role === "dependent" && r.operationId === spec.operationId) || null;
  }
  return null;
}

function dtcRecords(dtc) {
  const dec = dtc?.decoded;
  if (!dec || typeof dec !== "object" || Array.isArray(dec)) return null;
  if (!Array.isArray(dec.records)) return null;
  return dec.records;
}

export function classifySessionFinal(final, extras = {}) {
  if (extras.startRejected) {
    return { kind: KIND.rejected, error: extras.error || final?.error || "rejected", simulated: extras.simulated === true };
  }
  if (extras.cancelled || final?.status === "cancelled" || final?.error === "cancelled") {
    return {
      kind: KIND.cancelled,
      error: "cancelled",
      simulated: Boolean(final?.simulation || extras.simulated),
      records: dtcRecords(findDtcResult(final, extras.profileId || final?.profileId)) || undefined,
    };
  }

  const simulated = Boolean(final?.simulation === true || extras.simulated || extras.mode === "simulation");
  const profileId = extras.profileId || final?.profileId;
  const spec = DTC_OPS[profileId];
  const err = String(final?.error || extras.error || "");

  if (extras.mode && final?.mode !== extras.mode) {
    return { kind: KIND.partial, error: "mode-mismatch", simulated };
  }
  if (extras.profileId && final?.profileId !== extras.profileId) {
    return { kind: KIND.partial, error: "profile-mismatch", simulated };
  }

  if (err === "identity-mismatch" || extras.identityMismatch) {
    return { kind: KIND.identity, error: "identity-mismatch", simulated };
  }
  if (err === "not-installed" && extras.notInstalledEvidence === true) {
    return { kind: KIND.notInstalled, error: err, simulated };
  }

  const dtc = findDtcResult(final, profileId);
  const records = dtcRecords(dtc);
  const identityOk = final?.identityQualification?.observedProfileMatch === true;
  const typeOk = !spec || (dtc?.decoded && dtc.decoded.type === spec.decodeType);
  const healthyGate =
    !err &&
    final?.ok === true &&
    final?.status === "completed" &&
    identityOk &&
    spec &&
    dtc &&
    dtc.ok === true &&
    dtc.freshlyRead === true &&
    dtc.historical !== true &&
    dtc.skippedResume !== true &&
    dtc.decoded?.ok === true &&
    typeOk &&
    records != null;

  if (healthyGate) {
    if (records.length === 0) {
      return {
        kind: KIND.noDtc,
        dtcCount: 0,
        records,
        simulated,
        identity: final.identityQualification,
        runId: final.runId || null,
        capturedUtc: dtc.capturedUtc || final.endedUtc || null,
      };
    }
    return {
      kind: KIND.dtc,
      dtcCount: records.length,
      records,
      simulated,
      identity: final.identityQualification,
      runId: final.runId || null,
      capturedUtc: dtc.capturedUtc || final.endedUtc || null,
    };
  }

  const partialRecs = records && records.length ? records : undefined;
  if (COMM_ERRORS.has(err) || extras.commFail) {
    return {
      kind: KIND.commFail,
      error: err || "comm-fail",
      simulated,
      timeoutNotPhysical: /timeout/i.test(err),
      records: partialRecs,
      dtcCount: partialRecs?.length,
    };
  }
  if (final?.ok === true && final?.status === "completed" && !healthyGate) {
    return {
      kind: KIND.decodeError,
      error: err || "dtc-not-qualified",
      simulated,
      records: partialRecs,
      dtcCount: partialRecs?.length,
    };
  }
  return {
    kind: KIND.partial,
    error: err || "incomplete",
    simulated,
    records: partialRecs,
    dtcCount: partialRecs?.length,
  };
}

export function emptyStatusMap(gen) {
  const map = {};
  for (const n of flattenNodes(gen)) {
    map[n.id] = { kind: defaultKind(n, gen.id), simulated: false };
  }
  return map;
}

export function adaptedNodes(gen) {
  return flattenNodes(gen).filter((n) => isAdaptedProfile(n.profileId));
}

export function canTransmit(node) {
  return isAdaptedProfile(node?.profileId);
}

export function liveAllowed(generationId, mode) {
  return (mode ?? generationId) === "live";
}

export function dtcBadgeCount(entry) {
  if (typeof entry?.dtcCount !== "number") return null;
  if (entry.kind === KIND.noDtc && !entry.stale) return null;
  if (entry.dtcCount === 0 && (entry.stale || entry.kind !== KIND.dtc)) return null;
  return entry.dtcCount;
}

function countSnapshot(entry) {
  if (!entry || typeof entry.dtcCount !== "number") return null;
  if (entry.dtcCount === 0 && entry.kind !== KIND.noDtc && !entry.stale) return null;
  if (entry.kind === KIND.noDtc && !entry.stale) return { dtcCount: 0, records: entry.records || [] };
  return { dtcCount: entry.dtcCount, records: entry.records };
}

export function preClearSnapshot(final) {
  if (!final || typeof final !== "object") return null;
  const c = final.clear && typeof final.clear === "object" ? final.clear : final;
  const cand = c.preClear || c.preRead || c.preClearDtc || final.preClear || final.preRead;
  if (cand && typeof cand === "object") {
    if (typeof cand.dtcCount === "number") return { dtcCount: cand.dtcCount, records: cand.records || cand.recordsBefore };
    if (Array.isArray(cand.records)) return { dtcCount: cand.records.length, records: cand.records };
  }
  if (typeof c.dtcCountBefore === "number") {
    return { dtcCount: c.dtcCountBefore, records: c.recordsBefore || c.preRecords };
  }
  return null;
}

function asStale(next, snap) {
  return {
    ...next,
    dtcCount: snap.dtcCount,
    records: snap.records,
    stale: true,
    retainedDtc: true,
    dtcSource: "上次读取",
  };
}

export function mergeStatusAfterJob(prev, next, sessionTask, extras = {}) {
  if (!next) return next;
  const known = countSnapshot(prev) || preClearSnapshot(extras.final || next.final);
  if (next.kind === KIND.scanning) {
    return known ? asStale(next, known) : next;
  }
  const failed = next.kind !== KIND.dtc && next.kind !== KIND.noDtc;
  if (!failed) {
    const { stale, retainedDtc, dtcSource, ...rest } = next;
    return rest;
  }
  const bogusZero = next.dtcCount === 0;
  const nextHasCount = typeof next.dtcCount === "number" && !bogusZero;
  if (sessionTask === "clear") {
    const final = extras.final || next.final;
    const post = findDtcResult(final, final?.profileId);
    if (
      next.error === "residual-dtc-after-clear" &&
      final?.sessionTask === "clear" &&
      final?.identityQualification?.observedProfileMatch === true &&
      final?.clear?.clearSucceeded === true &&
      final?.clear?.outcome === "cleared-residual" &&
      post?.postClear === true && post.ok === true &&
      post.freshlyRead === true && post.historical !== true &&
      post.decoded?.ok === true &&
      post.decoded.type === DTC_OPS[final.profileId]?.decodeType &&
      Array.isArray(post.decoded.records) && post.decoded.records.length > 0
    ) {
      return { ...next, dtcCount: post.decoded.records.length, records: post.decoded.records,
        stale: false, retainedDtc: false, dtcSource: "清码后读取" };
    }
    if (nextHasCount) return { ...next, stale: true, dtcSource: "上次读取", retainedDtc: true };
    if (known) return asStale(next, known);
    return bogusZero ? { ...next, dtcCount: undefined, records: undefined } : next;
  }
  if (known && (bogusZero || typeof next.dtcCount !== "number")) return asStale(next, known);
  if (bogusZero) {
    const { dtcCount, records, ...rest } = next;
    return rest;
  }
  return next;
}

const VALID_TASK = new Set(["idle", "running", "offline"]);

export function headerTaskState({ apiAvailable, overviewOk, overviewState, localRunning, commLost }) {
  if (!apiAvailable || commLost) return "offline";
  if (localRunning) return "running";
  if (overviewOk !== true || !VALID_TASK.has(overviewState)) return "offline";
  return overviewState;
}

export function headerTaskLabel(state) {
  return ({ idle: "空闲", running: "运行中", offline: "离线" })[state] || "离线";
}

export function buildSessionStart(node, ctx) {
  const task = ctx.sessionTask === "clear" ? "clear" : "read";
  if (ctx.mode === "live") {
    if (task === "clear") {
      return {
        action: "start",
        profileId: node.profileId,
        mode: "live",
        sessionTask: "clear",
        confirmedClearDtc: true,
        x431Inactive: true,
      };
    }
    return {
      action: "start",
      profileId: node.profileId,
      mode: "live",
      sessionTask: "read",
      confirmedReadOnly: true,
      x431Inactive: true,
    };
  }
  const req = {
    action: "start",
    profileId: node.profileId,
    mode: "simulation",
    sessionTask: task,
    scenario: ctx.scenario || "success",
  };
  if (ctx.x431Inactive === true) req.x431Inactive = true;
  if (task === "read" && ctx.confirmedReadOnly === true) req.confirmedReadOnly = true;
  if (task === "clear" && ctx.confirmedClearDtc === true) req.confirmedClearDtc = true;
  return req;
}

const FAIL_ZH = {
  disconnect: "通讯中断",
  "pending-timeout": "等待超时",
  timeout: "超时",
  negative: "否定响应",
  "identity-mismatch": "身份不匹配",
  cancelled: "已取消",
  "profile-mismatch": "档案不匹配",
  "mode-mismatch": "模式不匹配",
  "session-lock-busy": "会话忙",
};

export function failureReasonZh(error) {
  if (!error) return "";
  const s = String(error);
  return FAIL_ZH[s] || s;
}

export function completionFeedback({ results, adaptedQueued, totalNodes } = {}) {
  const rows = results || [];
  let success = 0;
  let failed = 0;
  let failErr = "";
  for (const r of rows) {
    const k = r?.classified?.kind;
    if (k === KIND.pendingAdapt || k === KIND.assemblyUnconfirmed) continue;
    if (k === KIND.noDtc || k === KIND.dtc) success += 1;
    else {
      failed += 1;
      if (!failErr) failErr = failureReasonZh(r?.classified?.error || r?.doc?.error);
    }
  }
  const attempted = success + failed;
  const notProcessed = Math.max(0, (adaptedQueued ?? attempted) - attempted);
  const unsupported = Math.max(0, (totalNodes ?? 0) - (adaptedQueued ?? 0));
  let text = `成功 ${success} · 失败 ${failed} · 未处理 ${notProcessed} · 未适配 ${unsupported}`;
  if (failErr) text += ` · ${failErr}`;
  return text;
}

export function matchQuery(node, q) {
  if (!q) return true;
  const s = q.trim().toLowerCase();
  if (!s) return true;
  return [node.id, node.short, node.label, node.branchLabel].some((x) =>
    String(x || "").toLowerCase().includes(s),
  );
}

export function countSummary(nodes, statuses) {
  const out = {
    total: nodes.length,
    adapted: 0,
    unscanned: 0,
    noDtc: 0,
    dtc: 0,
    other: 0,
    pending: 0,
    fail: 0,
    simulated: 0,
  };
  for (const n of nodes) {
    if (isAdaptedProfile(n.profileId)) out.adapted += 1;
    const st = statuses[n.id];
    const k = st?.kind;
    if (st?.simulated) out.simulated += 1;
    if (k === KIND.unscanned) out.unscanned += 1;
    else if (k === KIND.noDtc) out.noDtc += 1;
    else if (k === KIND.dtc) out.dtc += 1;
    else if (k === KIND.pendingAdapt || k === KIND.assemblyUnconfirmed) out.pending += 1;
    else if (k === KIND.commFail || k === KIND.partial || k === KIND.decodeError || k === KIND.identity) out.fail += 1;
    else out.other += 1;
  }
  return out;
}

export function buildExportReport({ seed, generationId, mode, statuses, exportedAt }) {
  const gen = generationOf(seed, generationId);
  const nodes = flattenNodes(gen);
  const profiles = [];
  const skipped = [];
  let anyScan = false;
  for (const n of nodes) {
    const st = statuses[n.id] || { kind: defaultKind(n, generationId) };
    const scanned = st.kind && st.kind !== KIND.unscanned && st.kind !== KIND.pendingAdapt && st.kind !== KIND.assemblyUnconfirmed;
    if (scanned) anyScan = true;
    if (isAdaptedProfile(n.profileId)) {
      profiles.push({
        nodeId: n.id,
        short: n.short,
        label: n.label,
        profileId: n.profileId,
        kind: st.kind,
        text: statusText(st, { simulated: st.simulated }),
        dtcCount: st.dtcCount ?? null,
        records: st.records || null,
        runId: st.runId || null,
        capturedUtc: st.capturedUtc || null,
        error: st.error ?? null,
        simulated: Boolean(st.simulated),
      });
    } else {
      skipped.push({
        nodeId: n.id,
        short: n.short,
        label: n.label,
        reason: "unsupported",
        kind: st.kind,
      });
    }
  }
  return {
    mode,
    generation: generationId,
    exportedAt: exportedAt || new Date().toISOString(),
    scannedAt: anyScan ? profiles.find((p) => p.capturedUtc)?.capturedUtc || null : null,
    liveVerified: false,
    writePayload: null,
    capability: "read-only-limited",
    adaptedCount: profiles.length,
    note: "仅检测已适配模块；超时不能证明 CAN 物理故障。",
    profiles,
    skippedUnsupported: skipped,
    sourceRefs: seed?.sources || [],
    diagnostic: gen?.diagnostic || null,
    gateway: gen?.gateway ? { id: gen.gateway.id, sourcePages: gen.gateway.sourcePages } : null,
  };
}

async function cancelJob(invoke, jobId) {
  if (!jobId) return;
  try {
    await invoke({ action: "cancel", jobId });
  } catch {
    /* */
  }
}

export function createScanQueue({
  invoke,
  pollMs = 80,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  pollBudgetMs = POLL_BUDGET_MS,
  cancelWaitMs = CANCEL_WAIT_MS,
}) {
  let aborted = false;
  let inFlight = false;
  let currentJob = null;
  const cancelledIds = new Set();

  async function cancelJobOnce(jobId) {
    if (!jobId || cancelledIds.has(jobId)) return;
    cancelledIds.add(jobId);
    await cancelJob(invoke, jobId);
  }

  async function pollUntilDone(jobId, onStatus) {
    const t0 = Date.now();
    let cancelSent = false;
    let cancelAt = 0;
    while (Date.now() - t0 < pollBudgetMs) {
      let doc;
      try {
        doc = await invoke({ action: "status", jobId });
      } catch (e) {
        await cancelJobOnce(jobId);
        return { ok: false, error: String(e), state: "failed", jobId, statusThrow: true };
      }
      onStatus?.(doc);
      if (!doc || doc.ok === false) {
        await cancelJobOnce(jobId);
        return doc || { ok: false, error: "status_failed", jobId };
      }
      const st = doc.state;
      if (TERMINAL.has(st)) return doc;
      if (st === "running" || st === "cancelling") {
        if (aborted && !cancelSent) {
          await cancelJobOnce(jobId);
          cancelSent = true;
          cancelAt = Date.now();
        }
        if (aborted && cancelSent && Date.now() - cancelAt >= cancelWaitMs) {
          return { ok: false, error: "cancelled", state: st, jobId, final: doc.final, boundedCancel: true };
        }
        await sleep(pollMs);
        continue;
      }
      await cancelJobOnce(jobId);
      return { ok: false, error: "unknown_state", state: st, jobId, final: doc.final };
    }
    await cancelJobOnce(jobId);
    return { ok: false, error: "poll-timeout", state: "failed", jobId };
  }

  async function runOne(node, ctx, hooks) {
    const task = ctx.sessionTask === "clear" ? "clear" : "read";
    const scanning = mergeStatusAfterJob(hooks.statusOf?.(node.id), { kind: KIND.scanning, simulated: ctx.mode === "simulation" }, task);
    hooks.onKind?.(node.id, scanning);
    if (!isAdaptedProfile(node.profileId)) {
      return { nodeId: node.id, classified: { kind: KIND.pendingAdapt, simulated: false } };
    }
    if (ctx.mode === "live") {
      const flagsOk =
        task === "clear"
          ? ctx.confirmedClearDtc === true && ctx.x431Inactive === true
          : ctx.confirmedReadOnly === true && ctx.x431Inactive === true;
      if (!flagsOk) {
        return {
          nodeId: node.id,
          classified: classifySessionFinal(null, { startRejected: true, error: "live_confirmation_required" }),
          stop: true,
        };
      }
    }
    const req = buildSessionStart(node, ctx);
    let started;
    try {
      started = await invoke(req);
    } catch (e) {
      return {
        nodeId: node.id,
        classified: { kind: KIND.partial, error: String(e), simulated: ctx.mode === "simulation" },
        stop: true,
      };
    }
    if (aborted) {
      if (started?.jobId) {
        currentJob = started.jobId;
        await cancelJobOnce(started.jobId);
      }
      return { nodeId: node.id, classified: { kind: KIND.cancelled, simulated: ctx.mode === "simulation" }, stop: true };
    }
    if (!started?.ok || !started.jobId) {
      return {
        nodeId: node.id,
        classified: classifySessionFinal(started, {
          startRejected: true,
          error: started?.error,
          simulated: ctx.mode === "simulation",
          profileId: node.profileId,
          mode: ctx.mode,
        }),
        stop: true,
      };
    }
    currentJob = started.jobId;
    hooks.onJob?.(started.jobId);
    let done;
    try {
      done = await pollUntilDone(started.jobId, hooks.onStatus);
    } catch (e) {
      await cancelJobOnce(currentJob);
      return {
        nodeId: node.id,
        classified: { kind: KIND.partial, error: String(e), simulated: ctx.mode === "simulation" },
        stop: true,
      };
    }
    const classified = mergeStatusAfterJob(
      hooks.statusOf?.(node.id),
      classifySessionFinal(done?.final || done, {
        cancelled: aborted || done?.state === "cancelled" || done?.error === "cancelled",
        simulated: ctx.mode === "simulation" || done?.final?.simulation === true,
        error: done?.error,
        profileId: node.profileId,
        mode: ctx.mode,
      }),
      task,
      { final: done?.final || done },
    );
    const stop =
      aborted ||
      done?.statusThrow ||
      done?.ok === false ||
      (classified.kind !== KIND.dtc && classified.kind !== KIND.noDtc);
    return { nodeId: node.id, classified, doc: done, stop };
  }

  return {
    get busy() {
      return inFlight;
    },
    get jobId() {
      return currentJob;
    },
    async cancel() {
      aborted = true;
      if (currentJob) await cancelJobOnce(currentJob);
    },
    async run({ nodes, ctx, hooks = {} }) {
      if (inFlight) return { error: "busy", results: [] };
      inFlight = true;
      aborted = false;
      const results = [];
      try {
        for (const node of nodes) {
          if (aborted) break;
          const row = await runOne(node, ctx, hooks);
          hooks.onKind?.(row.nodeId, row.classified);
          results.push(row);
          if (aborted || row.stop) break;
        }
      } finally {
        if (currentJob) await cancelJobOnce(currentJob);
        currentJob = null;
        inFlight = false;
      }
      return { error: aborted ? "cancelled" : results.find((r) => r.stop && r.classified?.error)?.classified?.error || null, results };
    },
  };
}
