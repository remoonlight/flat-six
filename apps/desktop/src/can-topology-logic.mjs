/** Topology status and qualified read / separately confirmed clear queues. */
import manualTitles from "../../../data/seed/dtc/manuals/manual-titles.v1.json" with { type: "json" };
import { sourceTopologyEcu, sourceManualScope } from "./obd/topology-source.mjs";

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
  noResponse: "no-response",
  negativeResponse: "negative-response",
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

export function isAdaptedProfile(id) {
  return typeof id === "string" && (PROFILE_SET.has(id) || sourceTopologyEcu(id)!==null);
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
    for (const [nodeId, n] of byId) {
      byId.set(nodeId, { ...n, sourceGenerations: [
        ...(ba?.nodes?.some((x) => x.id === nodeId) ? ["981"] : []),
        ...(bb?.nodes?.some((x) => x.id === nodeId) ? ["982"] : []),
      ] });
    }
    const base = ba || bb;
    return { ...base, id, nodes: [...byId.values()] };
  });
  return {
    id: "combined",
    diagnostic: a.diagnostic || b.diagnostic,
    gateway: a.gateway || b.gateway ? { ...(a.gateway || b.gateway), sourceGenerations: [
      ...(a.gateway ? ["981"] : []), ...(b.gateway ? ["982"] : []),
    ] } : null,
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
  for (const node of gen.unplacedNodes || []) {
    if(seen.has(node.id))continue;
    seen.add(node.id);rest.push(enrich(node,{id:'unplaced',label:'连接位置未核实'}));
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
          [KIND.unscanned]: entry?.queued === true ? "等待检查" : "未扫描",
          [KIND.scanning]: "扫描中",
          [KIND.commFail]: "通讯失败",
          [KIND.noResponse]: "未识别",
          [KIND.negativeResponse]: "否定响应",
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

// Only an observed, explicit current-status label can change display priority.
// Raw KWP/UDS status bytes and stored/pending/permanent are not interchangeable.
export function orderDtcRecords(records) {
  if (!Array.isArray(records)) return [];
  const current = record => typeof record?.observedStatusText === "string" &&
    ["当前", "当前故障", "当前故障码", "current", "current fault"].includes(record.observedStatusText.trim().toLowerCase());
  return [...records.filter(current), ...records.filter(record => !current(record))];
}

// Manual descriptions are references, never ECU identity/version qualification.
// The current UDS gateway code namespace has no verified manual-code conversion.
const MANUAL_SCOPES = Object.freeze({
  "porsche-981-2014-dme": { generation: "981", module: "dme", namespace: "sae" },
});

export function manualDtcTitle(rec, scope) {
  if (!rec || typeof rec !== "object") return null;
  const literal=scope?.namespace==='manufacturer-hex';
  if(literal){
    if(rec.manufacturerCode!==true||rec.dictionaryMatch!==true||!/^[0-9A-F]{6}$/.test(rec.dtcHex||'')
      ||rec.displayCode!==rec.dtcHex)return null;
    const rows=manualTitles.records.filter(row=>row.codeNamespace==='manufacturer-hex'&&row.code===rec.dtcHex
      &&row.generation===scope.generation&&row.module===scope.module);
    if(!rows.length||rows.some(row=>row.applicability?.length)||new Set(rows.map(row=>row.title)).size!==1)return null;
    return rows[0];
  }
  if(scope?.namespace!=='sae'||rec.manufacturerCode===true)return null;
  const code = rec.displayCode || rec.code;
  if (typeof code !== "string" || !/^[PBCU][0-9A-F]{4}(?:[0-9A-F]{2})?$/.test(code)) return null;
  // A three-byte manufacturer identifier must not be truncated or reinterpreted.
  if (rec.dtcHex && !/^[0-9A-F]{4}$/i.test(rec.dtcHex)) return null;
  if (rec.dtcHex) {
    const first = Number.parseInt(rec.dtcHex[0], 16);
    const sae = `${"PCBU"[first >> 2]}${first & 3}${rec.dtcHex.slice(1).toUpperCase()}`;
    if (code !== sae) return null;
  }
  const matching = manualTitles.records.filter(row => row.generation === scope.generation && row.module === scope.module);
  let rows = matching.filter(row => row.code === code);
  if (!rows.length && code.length === 5) {
    const extended = matching.filter(row => row.code.startsWith(code) && row.code.length === 7);
    // A two-byte code cannot choose between failure subtypes.
    if (extended.some(row => row.code.slice(5) !== "00")) return null;
    rows = extended;
  }
  const preferred = rows.some(row => row.language === "zh") ? rows.filter(row => row.language === "zh") : rows.filter(row => row.language === "en");
  // A manual's conditional entry needs an independently qualified variant.
  // The title lookup has no such evidence and cannot silently drop conditions.
  if (preferred.some(row => row.applicability?.length)) return null;
  if (!preferred.length || new Set(preferred.map(row => row.title)).size !== 1) return null;
  return preferred[0];
}

export function dtcDetail(rec, profileId) {
  if (!rec || typeof rec !== "object") return "";
  const text = value => typeof value === "string" && value.trim() ? value : "";
  const scope=rec.manualScope!==undefined?sourceManualScope(rec.manualScope,profileId):MANUAL_SCOPES[profileId];
  const manual = manualDtcTitle(rec, scope);
  const observed = text(rec.originalEnglish) || text(rec.observedText);
  const originalEnglish = /[A-Za-z]/.test(observed) && !/[\u3400-\u9fff]/.test(observed) ? observed : "";
  return [manual?.title || originalEnglish || "故障说明未收录", text(rec.observedStatusText)]
    .filter(Boolean).join(" · ");
}

export function findDtcResult(final, profileId) {
  const spec = final?.sourceDtcRead===true ? {operationId:'source-dtc'} : DTC_OPS[profileId] || DTC_OPS[final?.profileId];
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
  const sourceType=final?.results?.find(row=>row?.operationId==='source-dtc')?.decoded?.type;
  const spec = final?.sourceDtcRead===true && ['kwp-dtc-18','uds-dtc-19-02'].includes(sourceType)
    ? {operationId:'source-dtc',decodeType:sourceType} : DTC_OPS[profileId];
  const err = String(final?.error || extras.error || "");

  if (extras.mode && final?.mode !== extras.mode) {
    return { kind: KIND.partial, error: "mode-mismatch", simulated };
  }
  if (extras.profileId && final?.profileId !== extras.profileId) {
    return { kind: KIND.partial, error: "profile-mismatch", simulated };
  }

  if (["no-response", "negative-response"].includes(final?.candidateFailure) && final?.linkHealth === "verified" && Array.isArray(final?.restoration?.errors) && final.restoration.errors.length === 0) {
    return { kind: final.candidateFailure === "no-response" ? KIND.noResponse : KIND.negativeResponse, error: err, simulated };
  }
  if (err === "identity-mismatch" || err === "scan-vehicle-changed" || extras.identityMismatch) {
    return { kind: KIND.identity, error: err || "identity-mismatch", simulated };
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
        identityInfo: final.identityInfo,
        sourceProfileId: final.sourceProfileId,
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
      identityInfo: final.identityInfo,
      sourceProfileId: final.sourceProfileId,
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
  return { dtcCount: entry.dtcCount, records: entry.records || [], capturedUtc: entry.capturedUtc,
    dtcSimulated: entry.dtcSimulated ?? entry.simulated };
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
    capturedUtc: snap.capturedUtc,
    dtcSimulated: snap.dtcSimulated,
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
  disconnect: "通讯中断。请检查车辆和诊断头的供电、接线后重试，或终止检查。",
  "pending-timeout": "等待控制单元响应超时。请核对点火电源和接线后重试，或终止检查。",
  timeout: "诊断响应超时。请核对点火电源和接线后重试，或终止检查。",
  negative: "控制单元返回否定响应。请核对所选单元和操作条件后重试。",
  "identity-mismatch": "控制单元身份不匹配。请终止检查，重新核对车辆和控制单元版本。",
  cancelled: "检查已取消。需要继续时，请重新开始检查。",
  "profile-mismatch": "控制单元档案不匹配。请终止检查并重新选择控制单元。",
  "mode-mismatch": "诊断任务模式不匹配。请终止本次检查后重新开始。",
  "session-lock-busy": "诊断链路被其他任务占用。请等待该任务结束后重试。",
  "scan-vehicle-changed": "车辆身份或控制单元版本已变化，检查已停止，请重新开始检查。",
  "scan-vehicle-unconfirmed": "车辆身份或控制单元版本未能重新核对，检查已停止，请重新读取。",
  "scan-device-changed": "诊断头已改变，请终止本次检查后重新开始。",
  "scan-device-unconfirmed": "无法确认本次诊断头，请终止检查并重新选择诊断头。",
  "scan-release-unconfirmed": "无法确认诊断头已释放，请终止检查并核对连接。",
  adapter_release_not_verified: "诊断头释放未通过核对，检查已停止；请返回连接设置检查并重新连接诊断头。",
  port_release_pending: "诊断头尚未释放，请等待后重试，或终止本次检查。",
  "scan-connection-timeout": "重新连接超时，请核对诊断头供电和连接后重试，或终止检查。",
  "scan-connection-unconfirmed": "无法确认诊断头连接，请核对供电和连接后重试，或终止检查。",
  diagnostic_purpose_required: "连接用途已改变，请终止本次检查，选择诊断用途后重新开始。",
};

export function failureReasonZh(error) {
  if (!error) return "";
  const s = error instanceof Error ? error.message : String(error);
  return Object.hasOwn(FAIL_ZH, s) ? FAIL_ZH[s]
    : "本次诊断操作未能完成，原因尚未确认。请检查诊断头供电、连接和本机诊断资料后重试。";
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

const RETRY_READ_ERRORS = new Set(["disconnect", "pending-timeout", "prompt-timeout", "nrc78-timeout", "port-io", "port-error", "timeout", "adapter-missing"]);
const VIN_OPS = { "porsche-981-2014-dme": "dme-vin", "porsche-981-2014-gateway": "gw-vin" };
function scanVin(final) {
  if (final?.sourceDtcRead===true && /^[a-f0-9]{64}$/.test(final.vehicleIdentitySha256 || '')) return final.vehicleIdentitySha256;
  const row = final?.results?.find(r => r.operationId === VIN_OPS[final.profileId] && r.role === "dependent"
    && r.ok === true && r.freshlyRead === true && r.historical === false && r.skippedResume !== true
    && r.decoded?.ok === true && r.decoded.type === "vin-ascii");
  const vin = row?.decoded?.text;
  return typeof vin === "string" && /^[A-HJ-NPR-Z0-9]{17}$/.test(vin) ? vin : null;
}
function scanIdentity(final) {
  return Object.fromEntries((final?.results || []).filter(r => r.role === "identity" && r.ok === true
    && r.decoded?.ok === true && typeof r.field === "string" && typeof r.decoded.text === "string")
    .map(r => [r.field, r.decoded.text]));
}

/** Called only by the user's Retry choice. Never replays a write or selects a head. */
export async function prepareScanRetry({ invoke, connection, jobId, mode, deviceId, model, cancelled = () => false,
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), now = Date.now, budgetMs = 20000 }) {
  const end = now() + budgetMs; let startedConnection = false, lastConnection, succeeded = false;
  const fail = error => ({ ok: false, error });
  try {
    if (!jobId) return fail("scan-release-unconfirmed");
    while (true) {
      if (cancelled()) return fail("cancelled");
      const status = await invoke({ action: "status", jobId });
      if (status?.released === true) {
        if (status.releaseVerified !== true) return fail("adapter_release_not_verified");
        break;
      }
      if (status?.released !== false) return fail("scan-release-unconfirmed");
      if (now() >= end) return fail("port_release_pending");
      await sleep(100);
    }
    if (mode === "simulation") return cancelled() ? fail("cancelled") : { ok: true };
    if (typeof connection !== "function" || !deviceId || !model) return fail("scan-device-unconfirmed");
    lastConnection = await connection({ action: "status" });
    while (true) {
      if (cancelled()) return fail("cancelled");
      if (lastConnection?.selectedDeviceId !== deviceId || lastConnection.model !== model) return fail("scan-device-changed");
      if (lastConnection.purpose !== "diagnostic") return fail("diagnostic_purpose_required");
      if (lastConnection.ok !== true) return fail(lastConnection.error || "scan-connection-unconfirmed");
      if (lastConnection.connected === true) { succeeded = true; return { ok: true }; }
      if (["close_failed", "disconnecting", "diagnostic"].includes(lastConnection.linkState)) return fail(lastConnection.error || "port_release_pending");
      if (!startedConnection && ["idle", "retry-required"].includes(lastConnection.linkState)) {
        startedConnection = true;
        lastConnection = await connection({ action: "connect" });
        continue;
      }
      if (lastConnection.linkState === "retry-required") return fail(lastConnection.connectionError || lastConnection.error || "disconnect");
      if (now() >= end) return fail("scan-connection-timeout");
      await sleep(100); lastConnection = await connection({ action: "status" });
    }
  } catch (error) { return fail(String(error?.message || error)); }
  finally {
    // Failed or terminated retries stop only their own unfinished connection attempt.
    if (!succeeded && startedConnection) {
      try {
        const status = await connection({ action: "status" });
        if (status.selectedDeviceId === deviceId && status.connected !== true) await connection({ action: "disconnect" });
      } catch { /* the connection manager retains its release lock */ }
    }
  }
}

export function createScanQueue({
  invoke,
  pollMs = 80,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  pollBudgetMs = POLL_BUDGET_MS,
}) {
  let aborted = false;
  let stopRequested = false;
  let activeTask = null;
  let inFlight = false;
  let currentJob = null;
  const cancelledIds = new Set();
  const decisionWaiters = new Set();

  async function cancelJobOnce(jobId) {
    if (!jobId || cancelledIds.has(jobId)) return;
    cancelledIds.add(jobId);
    await cancelJob(invoke, jobId);
  }

  async function pollUntilDone(jobId, onStatus) {
    const t0 = Date.now();
    let observationError = null;
    while (true) {
      // A polling budget ends the operation, not the worker's connection lease.
      // Keep observing this same job until actual release has been reported.
      if (Date.now() - t0 >= pollBudgetMs && !observationError) {
        observationError = "poll-timeout";
        await cancelJobOnce(jobId);
      }
      let doc;
      try {
        doc = await invoke({ action: "status", jobId });
      } catch (e) {
        observationError ||= String(e);
      }
      const owned = doc?.jobId === jobId &&
        (TERMINAL.has(doc.state) || doc.state === "running" || doc.state === "cancelling");
      if (!owned || doc.ok === false && !TERMINAL.has(doc.state)) {
        observationError ||= doc?.error || "status_failed";
        await cancelJobOnce(jobId);
        onStatus?.({ cleanupPending: true, error: observationError });
        await sleep(pollMs);
        continue;
      }
      const terminal = TERMINAL.has(doc.state);
      onStatus?.({ ...doc, cleanupPending: doc.state === "cancelling" || terminal && doc.released !== true });
      if (terminal && doc.released === true) {
        const error = doc.releaseVerified !== true ? "adapter_release_not_verified" : observationError;
        return error ? { ...doc, ok: false, error, statusThrow: true } : doc;
      }
      if (aborted) await cancelJobOnce(jobId);
      await sleep(pollMs);
    }
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
      if (!started?.jobId) return { nodeId: node.id, classified: { kind: KIND.cancelled, simulated: ctx.mode === "simulation" }, stop: true };
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
      done?.statusThrow ? { kind: KIND.commFail, error: done.error,
        simulated: ctx.mode === "simulation" || done?.final?.simulation === true,
        records: dtcRecords(findDtcResult(done.final, node.profileId)) || undefined,
      } : classifySessionFinal(done?.final || done, {
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
      (![KIND.dtc, KIND.noDtc, KIND.noResponse, KIND.negativeResponse].includes(classified.kind));
    return { nodeId: node.id, jobId: started.jobId, classified, doc: done, stop };
  }

  return {
    get busy() {
      return inFlight;
    },
    get jobId() {
      return currentJob;
    },
    get cancelled() { return aborted; },
    get stopRequested() { return stopRequested; },
    stopAfterCurrent() {
      if (!inFlight || activeTask !== "read" || aborted) return false;
      stopRequested = true;
      for (const resolve of decisionWaiters) resolve("stop-after-current");
      return true;
    },
    async cancel() {
      aborted = true;
      for (const resolve of decisionWaiters) resolve("terminate");
      if (currentJob) await cancelJobOnce(currentJob);
    },
    async run({ nodes, ctx, hooks = {} }) {
      if (inFlight) return { error: "busy", stopped: false, results: [], clearResults: [], clearOutcomeUnconfirmed: false, readbackStarted: false };
      inFlight = true;
      aborted = false;
      stopRequested = false;
      activeTask = ctx.sessionTask === "clear" ? "clear" : "read";
      const results = [];
      const clearResults = [];
      let phaseCtx = ctx, readbackStarted = false, clearOutcomeUnconfirmed = false;
      let vehicleVin = null;
      let completedIdentityKnown = true;
      const identities = new Map();
      try {
        for (let index = 0; index < nodes.length; index++) {
          const node = nodes[index];
          if (aborted || stopRequested) break;
          const row = await runOne(node, phaseCtx, hooks);
          {
            const final = row.doc?.final;
            const vin = scanVin(final), identity = scanIdentity(final), previous = identities.get(node.id);
            const readbackIdentityMissing = readbackStarted && [KIND.dtc, KIND.noDtc].includes(row.classified?.kind)
              && (!vin || previous && Object.keys(previous).some(key => !Object.hasOwn(identity, key)));
            if (readbackIdentityMissing || vin && vehicleVin && vin !== vehicleVin || previous && Object.entries(previous).some(([k,v]) => Object.hasOwn(identity, k) && identity[k] !== v)) {
              row.classified = { kind: KIND.identity, error: readbackIdentityMissing ? "scan-vehicle-unconfirmed" : "scan-vehicle-changed", simulated: ctx.mode === "simulation" };
              row.stop = true;
            } else {
              if (vin) vehicleVin ||= vin;
              if (Object.keys(identity).length) identities.set(node.id, { ...previous, ...identity });
            }
            if ([KIND.dtc, KIND.noDtc].includes(row.classified?.kind) && !vin) completedIdentityKnown = false;
          }
          hooks.onKind?.(row.nodeId, row.classified);
          results.push(row);
          if (aborted || stopRequested) break;
          if (row.stop) {
            const uncertainClear = phaseCtx.sessionTask === "clear" && RETRY_READ_ERRORS.has(row.classified?.error);
            if (uncertainClear) clearOutcomeUnconfirmed = true;
            const released = row.doc?.released === true && row.doc?.releaseVerified === true;
            if (RETRY_READ_ERRORS.has(row.classified?.error) && typeof hooks.onInterrupted === "function"
                && (phaseCtx.sessionTask !== "clear" || released)) {
              const canRetry = phaseCtx.sessionTask === "clear" ? Boolean(vehicleVin) && completedIdentityKnown
                : results.length === 1 || completedIdentityKnown && Boolean(vehicleVin);
              let resolveCancelled;
              const cancellation = new Promise(resolve => { resolveCancelled = resolve; decisionWaiters.add(resolve); });
              let decision;
              try { decision = await Promise.race([cancellation, hooks.onInterrupted({ row,
                remaining: phaseCtx.sessionTask === "clear" ? nodes : nodes.slice(index),
                readback: phaseCtx.sessionTask === "clear" || readbackStarted, canRetry })]); }
              finally { decisionWaiters.delete(resolveCancelled); }
              if (decision === "retry" && !aborted && !stopRequested && canRetry) {
                if (phaseCtx.sessionTask === "clear") {
                  // Recovery performs only a fresh read of the original range.
                  // Never restart or continue the interrupted write batch.
                  clearResults.push(...results); results.length = 0;
                  phaseCtx = { ...ctx, sessionTask: "read", confirmedReadOnly: true };
                  delete phaseCtx.confirmedClearDtc;
                  readbackStarted = true; index = -1;
                } else { results.pop(); index--; }
                continue;
              }
              if (decision === "terminate") aborted = true;
            }
            break;
          }
        }
      } finally {
        if (currentJob) await cancelJobOnce(currentJob);
        currentJob = null;
        inFlight = false;
        activeTask = null;
      }
      return { error: aborted ? "cancelled" : results.find((r) => r.stop && r.classified?.error)?.classified?.error || null,
        stopped: stopRequested && !aborted, results, clearResults, clearOutcomeUnconfirmed, readbackStarted };
    },
  };
}
