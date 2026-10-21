import assert from "node:assert/strict";
import factoryTitles from '../../../data/seed/dtc/manuals/manual-titles.v1.json' with {type:'json'};
import { topologyAdapterProgress, topologyCapability } from "./can-topology-capabilities.mjs";
import {
  ADAPTED_PROFILES,
  KIND,
  adaptedNodes,
  branchAppearances,
  buildExportReport,
  buildSessionStart,
  canTransmit,
  classifySessionFinal,
  combinedGeneration,
  completionFeedback,
  createScanQueue,
  defaultKind,
  dtcBadgeCount,
  dtcDetail,
  manualDtcTitle,
  dtcLabel,
  orderDtcRecords,
  emptyStatusMap,
  flattenNodes,
  generationOf,
  headerTaskState,
  isHealthyGreen,
  liveAllowed,
  mergeStatusAfterJob,
  prepareScanRetry,
} from "./can-topology-logic.mjs";

const seed = {
  schemaVersion: 1,
  sources: [{ id: "981-wiring", file: "981.pdf", modelYear: "2013 (D)" }],
  generations: [
    {
      id: "981",
      gateway: { id: "gateway", short: "GW", label: "网关", profileId: "porsche-981-2014-gateway", sourcePages: [11] },
      branches: [
        {
          id: "drive",
          label: "驱动 CAN",
          color: "#C62828",
          gatewayPins: { high: "A010 A16", low: "A010 A6" },
          nodes: [
            { id: "dme", short: "DME", label: "发动机", profileId: "porsche-981-2014-dme", sourcePages: [12], connectionType: "CAN" },
            { id: "pdk", short: "PDK", label: "PDK", sourcePages: [12], connectionType: "CAN" },
          ],
        },
        {
          id: "chassis",
          label: "底盘 CAN",
          color: "#1565C0",
          gatewayPins: { high: "A010 A20", low: "A010 A10" },
          nodes: [{ id: "psm", short: "PSM", label: "PSM", sourcePages: [12], connectionType: "CAN" }],
        },
        {
          id: "comfort",
          label: "舒适性 CAN",
          color: "#2E7D32",
          gatewayPins: { high: "A010 A15", low: "A010 A5" },
          nodes: [
            {
              id: "steering-column",
              short: "KLS",
              label: "转向柱",
              sourcePages: [12],
              connectionType: "CAN",
              additionalBranches: [{ branchId: "chassis", notes: "also chassis" }],
            },
          ],
        },
      ],
    },
    {
      id: "982",
      gateway: { id: "gateway", short: "GW", label: "网关", sourcePages: [12] },
      branches: [
        {
          id: "drive",
          label: "驱动 CAN",
          color: "#C62828",
          nodes: [
            { id: "dme", short: "DME", label: "发动机", connectionType: "CAN" },
            { id: "shaker", short: "SHA", label: "发动机振动", connectionType: "CAN" },
          ],
        },
      ],
    },
  ],
};

const g981 = generationOf(seed, "981");
const nodes = flattenNodes(g981);
assert.equal(nodes.filter((n) => n.id === "steering-column").length, 1);
const rails = branchAppearances(g981);
const chassis = rails.find((b) => b.id === "chassis");
assert.ok(chassis.appearances.some((n) => n.id === "steering-column" && n.secondary));
assert.equal(defaultKind(nodes.find((n) => n.id === "dme"), "981"), KIND.unscanned);
assert.equal(isHealthyGreen(KIND.unscanned), false);
assert.equal(liveAllowed("combined", "live"), true);
assert.equal(liveAllowed("982", "simulation"), false);
assert.equal(adaptedNodes(g981).length, 2);

const union = combinedGeneration(seed);
const unionNodes = flattenNodes(union);
assert.equal(unionNodes.filter((n) => n.id === "dme").length, 1);
assert.equal(unionNodes.filter((n) => n.id === "steering-column").length, 1);
assert.ok(unionNodes.some((n) => n.id === "shaker"));
assert.equal(unionNodes.find((n) => n.id === "dme").profileId, "porsche-981-2014-dme");
assert.equal(canTransmit(unionNodes.find((n) => n.id === "shaker")), false);
assert.equal(canTransmit(unionNodes.find((n) => n.id === "dme")), true);
assert.equal(defaultKind(unionNodes.find((n) => n.id === "shaker")), KIND.pendingAdapt);
const unionRails = branchAppearances(union);
const unionChassis = unionRails.find((b) => b.id === "chassis");
assert.ok(unionChassis.appearances.some((n) => n.id === "steering-column" && n.secondary));

const healthy = {
  ok: true,
  status: "completed",
  mode: "simulation",
  simulation: true,
  profileId: "porsche-981-2014-dme",
  identityQualification: { observedProfileMatch: true },
  results: [
    {
      role: "dependent",
      operationId: "dme-dtc",
      ok: true,
      freshlyRead: true,
      decoded: { ok: true, type: "kwp-dtc-18", records: [] },
    },
  ],
};
assert.equal(classifySessionFinal(healthy, { profileId: "porsche-981-2014-dme", mode: "simulation" }).kind, KIND.noDtc);
assert.notEqual(classifySessionFinal({ ...healthy, error: "timeout" }).kind, KIND.noDtc);
assert.notEqual(classifySessionFinal({ ...healthy, mode: undefined }, { mode: "simulation" }).kind, KIND.noDtc);
assert.notEqual(classifySessionFinal({ ...healthy, profileId: undefined }, { profileId: healthy.profileId }).kind, KIND.noDtc);

const withFaults = {
  ...healthy,
  results: [
    {
      role: "dependent",
      operationId: "dme-dtc",
      ok: true,
      freshlyRead: true,
      capturedUtc: "2026-09-27T00:00:00Z",
      decoded: {
        ok: true,
        type: "kwp-dtc-18",
        records: [
          { dtcHex: "C447", statusHex: "28", displayCode: "U0447", observedText: "lost comm" },
          { dtcHex: "C412", statusHex: "28", displayCode: "U0412" },
        ],
      },
    },
  ],
};
// Protocol status bytes alone, ambiguous descriptions and unsupported labels
// cannot be promoted to "current". Ordering never mutates decoding/evidence.
{
  const unknown = Object.freeze({ code: "UNKNOWN", statusHex: "01" });
  const history = Object.freeze({ code: "HISTORY", observedStatusText: "历史故障" });
  const current = Object.freeze({ code: "CURRENT", observedStatusText: " 当前故障 " });
  const pending = Object.freeze({ code: "PENDING", status: "pending" });
  const ambiguous = Object.freeze({ code: "AMBIGUOUS", observedStatusText: "历史或当前" });
  const english = Object.freeze({ code: "ENGLISH", observedStatusText: "Current" });
  const rows = Object.freeze([unknown, history, current, pending, ambiguous, english, current]);
  assert.deepEqual(orderDtcRecords(rows), [current, english, current, unknown, history, pending, ambiguous]);
  assert.deepEqual(rows, [unknown, history, current, pending, ambiguous, english, current]);
  assert.equal(orderDtcRecords(rows)[0], current);
  assert.deepEqual(orderDtcRecords([unknown, history, pending, ambiguous]), [unknown, history, pending, ambiguous]);
  assert.deepEqual(orderDtcRecords(null), []);
}

const faulted = classifySessionFinal(withFaults, { profileId: "porsche-981-2014-dme", mode: "simulation" });
assert.equal(faulted.kind, KIND.dtc);
assert.equal(dtcLabel(faulted.records[0]), "U0447");

assert.equal(
  classifySessionFinal(
    {
      ok: false,
      status: "failed",
      error: "close-failed",
      results: [{ role: "dependent", operationId: "dme-dtc", ok: false, decoded: { ok: true, records: [] } }],
    },
    { profileId: "porsche-981-2014-dme" },
  ).kind,
  KIND.partial,
);

assert.equal(
  classifySessionFinal(
    { ...healthy, results: [{ role: "dependent", operationId: "dme-dtc", ok: true, decoded: { ok: true, type: "kwp-dtc-18", records: [] } }] },
    { profileId: "porsche-981-2014-dme" },
  ).kind,
  KIND.decodeError,
);

assert.equal(
  classifySessionFinal({ ...healthy, identityQualification: { observedProfileMatch: false } }, { profileId: "porsche-981-2014-dme" }).kind,
  KIND.decodeError,
);

assert.equal(
  classifySessionFinal(
    { ...withFaults, mode: "live", simulation: false },
    { profileId: "porsche-981-2014-dme", mode: "simulation" },
  ).kind,
  KIND.partial,
);

assert.equal(
  classifySessionFinal(
    {
      ok: true,
      status: "completed",
      simulation: true,
      profileId: "porsche-981-2014-gateway",
      identityQualification: { observedProfileMatch: true },
      results: [
        {
          role: "dependent",
          operationId: "gateway-dtc",
          ok: true,
          freshlyRead: true,
          decoded: { ok: true, type: "uds-dtc-19-02", records: [] },
        },
      ],
    },
    { profileId: "porsche-981-2014-gateway" },
  ).kind,
  KIND.decodeError,
);

const gwFinal = {
  ok: true,
  status: "completed",
  simulation: true,
  mode: "simulation",
  profileId: "porsche-981-2014-gateway",
  identityQualification: { observedProfileMatch: true },
  results: [
    {
      role: "dependent",
      operationId: "gw-dtc",
      ok: true,
      freshlyRead: true,
      decoded: { ok: true, type: "uds-dtc-19-02", records: [] },
    },
  ],
};
const gwOk = classifySessionFinal(gwFinal, { profileId: "porsche-981-2014-gateway", mode: "simulation" });
assert.equal(gwOk.kind, KIND.noDtc);

const empty = emptyStatusMap(g981);
const reportUnscanned = buildExportReport({ seed, generationId: "981", mode: "simulation", statuses: empty, exportedAt: "t1" });
assert.equal(reportUnscanned.scannedAt, null);
assert.equal(reportUnscanned.exportedAt, "t1");
assert.equal(buildExportReport({ seed, generationId: "981", mode: "simulation", statuses: { ...empty, dme: { kind: KIND.partial } }, exportedAt: "t1" }).scannedAt, null);

{
  const cancels = [];
  const starts = [];
  const invoke = async (req) => {
    if (req.action === "start") {
      starts.push(req.profileId);
      return { ok: true, jobId: "j" + starts.length.toString().padStart(16, "0") };
    }
    if (req.action === "status") return cancels.length
      ? { ok: false, jobId: req.jobId, state: "failed", error: "status_failed", released: true, releaseVerified: true }
      : { ok: false, error: "status_failed" };
    if (req.action === "cancel") {
      cancels.push(req.jobId);
      return { ok: true, state: "cancelled" };
    }
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const out = await q.run({
    nodes: adaptedNodes(g981),
    ctx: { generationId: "981", mode: "simulation" },
    hooks: {},
  });
  assert.equal(starts.length, 1);
  assert.ok(cancels.length >= 1);
  assert.equal(out.results.length, 1);
}

{
  const cancels = [];
  const invoke = async (req) => {
    if (req.action === "start") return { ok: true, jobId: "jthrow00000000001" };
    if (req.action === "status") {
      if (!cancels.length) throw new Error("ipc");
      return { ok: false, jobId: req.jobId, state: "failed", error: "ipc", released: true, releaseVerified: true };
    }
    if (req.action === "cancel") {
      cancels.push(req.jobId);
      return { ok: true };
    }
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const out = await q.run({
    nodes: adaptedNodes(g981),
    ctx: { generationId: "981", mode: "simulation" },
    hooks: {},
  });
  assert.equal(cancels.length, 1);
  assert.equal(out.results.length, 1);
}

{
  let starts = 0, cancelled = false;
  const invoke = async (req) => {
    if (req.action === "start") {
      starts += 1;
      await new Promise((r) => setTimeout(r, 30));
      return { ok: true, jobId: "jdelay00000000001" };
    }
    if (req.action === "cancel") { cancelled = true; return { ok: true, state: "cancelling" }; }
    if (req.action === "status") return { ok: true, state: cancelled ? "cancelled" : "running", jobId: req.jobId,
      released: cancelled, releaseVerified: cancelled };
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 5, sleep: (ms) => new Promise((r) => setTimeout(r, ms)), pollBudgetMs: 5000 });
  const p = q.run({ nodes: adaptedNodes(g981), ctx: { generationId: "981", mode: "simulation", scenario: "slow" }, hooks: {} });
  await new Promise((r) => setTimeout(r, 5));
  await q.cancel();
  const out = await p;
  assert.ok(out.results.length <= 1);
  assert.ok(starts <= 1);
}

{
  const invoke = async (req) => {
    if (req.action === "start") {
      return {
        ok: true,
        jobId: "jok00000000000001",
        final: healthy,
      };
    }
    if (req.action === "status") {
      return { ok: true, state: "completed", released: true, releaseVerified: true, jobId: req.jobId, final: { ...healthy, profileId: req.jobId } };
    }
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const a = q.run({ nodes: adaptedNodes(g981).slice(0, 1), ctx: { generationId: "981", mode: "simulation" }, hooks: {} });
  const b = await q.run({ nodes: adaptedNodes(g981).slice(0, 1), ctx: { generationId: "981", mode: "simulation" }, hooks: {} });
  assert.equal(b.error, "busy");
  await a;
}

{
  const invoke = async () => {
    throw new Error("unsupported must not call");
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const out = await q.run({
    nodes: flattenNodes(generationOf(seed, "982")),
    ctx: { mode: "live", sessionTask: "read", confirmedReadOnly: true, x431Inactive: true },
    hooks: {},
  });
  assert.equal(out.results.every((r) => r.classified.kind === KIND.pendingAdapt), true);
}

{
  const starts = [];
  const jobs = new Map();
  const invoke = async (req) => {
    if (req.action === "start") {
      starts.push(req);
      const jobId = "jread" + String(starts.length).padStart(12, "0");
      jobs.set(jobId, req.profileId);
      const final = String(req.profileId).includes("gateway") ? gwFinal : healthy;
      return { ok: true, jobId, final };
    }
    if (req.action === "status") {
      const pid = jobs.get(req.jobId);
      const final = String(pid).includes("gateway") ? gwFinal : healthy;
      return { ok: true, state: "completed", jobId: req.jobId, released: true, releaseVerified: true, final };
    }
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  await q.run({
    nodes: adaptedNodes(g981),
    ctx: { mode: "simulation", sessionTask: "read" },
    hooks: {},
  });
  assert.equal(starts.length, 2);
  assert.equal(starts[0].sessionTask, "read");
  const q2 = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  starts.length = 0;
  await q2.run({
    nodes: adaptedNodes(g981).slice(0, 1),
    ctx: { mode: "simulation", sessionTask: "clear" },
    hooks: {},
  });
  assert.equal(starts.length, 1);
  assert.equal(starts[0].sessionTask, "clear");
  assert.equal(starts[0].confirmedReadOnly, undefined);
}

{
  const starts = [];
  const invoke = async (req) => {
    starts.push(req);
    return { ok: false, error: "blocked" };
  };
  const mixed = [flattenNodes(g981).find((n) => n.id === "pdk"), ...adaptedNodes(g981).slice(0, 1)];
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const out = await q.run({
    nodes: mixed,
    ctx: { mode: "simulation", sessionTask: "read" },
    hooks: {},
  });
  assert.equal(starts.filter((s) => s.action === "start").length, 1);
  assert.ok(out.results.some((r) => r.classified.kind === KIND.pendingAdapt));
}

{
  const readReq = buildSessionStart({ profileId: "porsche-981-2014-dme" }, { mode: "live", sessionTask: "read" });
  assert.equal(readReq.confirmedReadOnly, true);
  assert.equal(readReq.sessionTask, "read");
  const clearReq = buildSessionStart({ profileId: "porsche-981-2014-dme" }, { mode: "live", sessionTask: "clear" });
  assert.equal(clearReq.confirmedClearDtc, true);
  assert.equal(clearReq.confirmedReadOnly, undefined);
  assert.equal(clearReq.sessionTask, "clear");
}

{
  const prev = { kind: KIND.dtc, dtcCount: 2, records: [{ displayCode: "U0447" }] };
  const scanning = mergeStatusAfterJob(prev, { kind: KIND.scanning }, "clear");
  assert.equal(scanning.dtcCount, 2);
  assert.equal(scanning.stale, true);
  const failed = { kind: KIND.commFail, error: "disconnect" };
  const kept = mergeStatusAfterJob(scanning, failed, "clear");
  assert.equal(kept.dtcCount, 2);
  assert.equal(kept.retainedDtc, true);
  assert.equal(kept.stale, true);
  assert.equal(kept.dtcSource, "上次读取");
  assert.notEqual(kept.kind, KIND.noDtc);
  const okZero = mergeStatusAfterJob(prev, { kind: KIND.noDtc, dtcCount: 0, records: [] }, "clear");
  assert.equal(okZero.dtcCount, 0);
  assert.equal(okZero.stale, undefined);
  assert.equal(dtcBadgeCount({ kind: KIND.commFail }), null);
  assert.equal(dtcBadgeCount({ kind: KIND.dtc, dtcCount: 2 }), 2);
  assert.equal(dtcBadgeCount({ kind: KIND.noDtc, dtcCount: 0 }), null);
  assert.equal(dtcBadgeCount(kept), 2);
  const fromPre = mergeStatusAfterJob({ kind: KIND.unscanned }, { kind: KIND.commFail, error: "disconnect" }, "clear", {
    final: { clear: { preClear: { dtcCount: 2, records: [{ displayCode: "U0447" }] } } },
  });
  assert.equal(fromPre.dtcCount, 2);
  const failedRefreshZero = mergeStatusAfterJob(prev, { kind: KIND.commFail, dtcCount: 0, records: [] }, "read");
  assert.equal(failedRefreshZero.dtcCount, 2);
  assert.equal(failedRefreshZero.stale, true);
  const baselineZero = mergeStatusAfterJob({ kind: KIND.unscanned }, { kind: KIND.commFail, dtcCount: 0 }, "read");
  assert.equal(baselineZero.dtcCount, undefined);
}

{
  const map = { dme: { kind: KIND.dtc, dtcCount: 2, records: [{ displayCode: "U0447" }, { displayCode: "U0412" }] } };
  const invoke = async (req) => {
    if (req.action === "start") return { ok: true, jobId: "jclr0000000000001" };
    if (req.action === "status") {
      return {
        ok: true,
        state: "failed", jobId: req.jobId, released: true, releaseVerified: true,
        error: "disconnect",
        jobId: req.jobId,
        final: { ok: false, status: "failed", error: "disconnect", mode: "simulation", simulation: true, profileId: "porsche-981-2014-dme" },
      };
    }
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const dmeOnly = adaptedNodes(g981).filter((n) => n.id === "dme");
  await q.run({
    nodes: dmeOnly,
    ctx: { mode: "simulation", sessionTask: "clear", scenario: "disconnect" },
    hooks: {
      statusOf: (id) => map[id],
      onKind: (id, classified) => {
        map[id] = mergeStatusAfterJob(map[id], classified, "clear");
      },
    },
  });
  assert.equal(map.dme.dtcCount, 2);
  assert.equal(map.dme.stale, true);
  assert.equal(map.dme.kind, KIND.commFail);
}

{
  const starts = [];
  const jobs = new Map();
  const invoke = async (req) => {
    if (req.action === "start") {
      starts.push(req.profileId);
      const jobId = "jstp" + String(starts.length).padStart(13, "0");
      jobs.set(jobId, req.profileId);
      return { ok: true, jobId };
    }
    if (req.action === "status") {
      return {
        ok: true,
        state: "failed", jobId: req.jobId, released: true, releaseVerified: true,
        error: "disconnect",
        final: {
          ok: false,
          status: "failed",
          error: "disconnect",
          mode: "simulation",
          simulation: true,
          profileId: jobs.get(req.jobId),
        },
      };
    }
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  const out = await q.run({
    nodes: adaptedNodes(g981),
    ctx: { mode: "simulation", sessionTask: "read" },
    hooks: {},
  });
  assert.equal(starts.length, 1);
  const fb = completionFeedback({ results: out.results, adaptedQueued: 2, totalNodes: 6 });
  assert.match(fb, /成功 0/);
  assert.match(fb, /失败 1/);
  assert.match(fb, /未处理 1/);
  assert.match(fb, /未适配 4/);
  assert.match(fb, /通讯中断/);
}

assert.equal(headerTaskState({ apiAvailable: false }), "offline");
assert.equal(headerTaskState({ apiAvailable: true, localRunning: true, overviewState: "idle", overviewOk: true }), "running");
assert.equal(headerTaskState({ apiAvailable: true, commLost: true }), "offline");
assert.equal(headerTaskState({ apiAvailable: true, overviewOk: true, overviewState: "idle" }), "idle");
assert.equal(headerTaskState({ apiAvailable: true, overviewOk: false }), "offline");
assert.equal(headerTaskState({ apiAvailable: true, overviewOk: true, overviewState: "nope" }), "offline");
assert.equal(headerTaskState({ apiAvailable: true, overviewOk: true }), "offline");
assert.equal(headerTaskState({ apiAvailable: true, overviewOk: false, overviewState: "running" }), "offline");

// A failed clear-all outcome can still carry a valid, fresh post-clear DTC read.
{
  const final = {
    profileId: "porsche-981-2014-dme", sessionTask: "clear",
    identityQualification: { observedProfileMatch: true },
    clear: { clearSucceeded: true, outcome: "cleared-residual" },
    results: [{ role: "dependent", operationId: "dme-dtc", ok: true,
      postClear: true, freshlyRead: true, historical: false,
      decoded: { ok: true, type: "kwp-dtc-18", records: [{ displayCode: "U0447" }] } }],
  };
  const next = { kind: KIND.partial, error: "residual-dtc-after-clear", dtcCount: 1 };
  const fresh = mergeStatusAfterJob(null, next, "clear", { final });
  assert.equal(fresh.stale, false);
  assert.equal(fresh.dtcSource, "清码后读取");
  assert.equal(fresh.kind, KIND.partial);
  final.results[0].freshlyRead = false;
  assert.equal(mergeStatusAfterJob(null, next, "clear", { final }).stale, true);
}

{
  let liveFlags = null;
  const invoke = async (req) => {
    liveFlags = req;
    return { ok: false, error: "nope" };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  await q.run({
    nodes: adaptedNodes(g981).slice(0, 1),
    ctx: { mode: "live", sessionTask: "clear", confirmedClearDtc: true, x431Inactive: true },
    hooks: {},
  });
  assert.equal(liveFlags.sessionTask, "clear");
  assert.equal(liveFlags.confirmedReadOnly, undefined);
  assert.equal(liveFlags.confirmedClearDtc, true);
}

{
  const starts = [];
  const invoke = async (req) => {
    if (req.action === "start") starts.push(req);
    return { ok: false, error: "nope" };
  };
  const q = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
  await q.run({
    nodes: adaptedNodes(g981).slice(0, 1),
    ctx: { mode: "live", sessionTask: "read" },
    hooks: {},
  });
  assert.equal(starts.length, 0);
}

{
  const combined = combinedGeneration(seed);
  const combinedNodes = flattenNodes(combined);
  const dme = combinedNodes.find((n) => n.id === "dme");
  const shaker = combinedNodes.find((n) => n.id === "shaker");
  assert.deepEqual(dme.sourceGenerations, ["981", "982"]);
  assert.deepEqual(shaker.sourceGenerations, ["982"]);
  assert.equal(topologyCapability(shaker).referenceOnly, true);
  assert.equal(topologyCapability(shaker).readable, false);
  assert.equal(topologyCapability(shaker).clearable, false);
  assert.equal(topologyCapability(dme, "vLinker").clearable, true);
  assert.equal(topologyCapability(dme, "vLinker", "tcp:192.168.0.10:35000").readable, true);
  assert.equal(topologyCapability(dme, "vLinker", "tcp:192.168.0.10:35000").clearable, false);
  assert.match(topologyCapability(dme, "vLinker", "tcp:192.168.0.10:35000").readEvidence, /网络诊断头与本车待验收/);
  assert.equal(topologyCapability(dme, "VNCI").readable, true);
  assert.equal(topologyCapability(dme, "VNCI").clearable, false);
  assert.equal(topologyCapability(dme, "PT3G").clearable, false);
  assert.equal(topologyCapability(dme, "PT3G").readable, false);
  for (const family of ["ELM327", "OBDKey", "OBDLink"]) {
    assert.equal(topologyCapability(dme, family).readable, true);
    assert.equal(topologyCapability(dme, family).clearable, false);
    assert.match(topologyCapability(dme, family).readEvidence, /本型号/);
  }
  assert.match(topologyCapability(dme, "PT3G").readEvidence, /仅支持诊断头/);
  assert.equal(topologyCapability({ id: "dme" }).engine, false);
  assert.equal(topologyCapability({ id: "pdk" }, "vLinker").clearable, false);
  assert.match(topologyCapability({ id: "bcm-rear" }).codingDetail, /2 项重名/);
  assert.match(topologyAdapterProgress("VNCI"), /暂不支持清码/);
  const prior = { kind: KIND.dtc, dtcCount: 2, records: [{ displayCode: "U0447" }],
    simulated: true, capturedUtc: "2026-10-01T09:00:00Z" };
  const stale = mergeStatusAfterJob(prior, { kind: KIND.commFail, simulated: false, error: "disconnect" }, "read");
  assert.equal(stale.capturedUtc, prior.capturedUtc);
  assert.equal(stale.dtcSimulated, true, "a failed live attempt must not relabel the retained simulation as vehicle data");
  const fresh = mergeStatusAfterJob(stale, { kind: KIND.noDtc, dtcCount: 0, records: [], simulated: false }, "read");
  assert.equal(fresh.dtcSimulated, undefined);
}

console.log(`can-topology-logic.selfcheck ok ${ADAPTED_PROFILES.length} profiles; capability, transport and retained-source gates`);

{
  const targets = adaptedNodes(g981), vin = "WP0ZZZ98ZES000000";
  function final(profileId, extra = {}) {
    return { ok: true, status: "completed", mode: "simulation", simulation: true, profileId,
      identityQualification: { observedProfileMatch: true }, results: [
        { role: "identity", field: "hardware", ok: true, decoded: { ok: true, text: "HW1" } },
        { role: "dependent", operationId: profileId === ADAPTED_PROFILES[0] ? "gw-vin" : "dme-vin", ok: true,
          freshlyRead: true, historical: false, decoded: { ok: true, type: "vin-ascii", text: vin } },
        { role: "dependent", operationId: profileId === ADAPTED_PROFILES[0] ? "gw-dtc" : "dme-dtc", ok: true,
          freshlyRead: true, historical: false, decoded: { ok: true, type: profileId === ADAPTED_PROFILES[0] ? "uds-dtc-19-02" : "kwp-dtc-18", records: [] } },
      ], ...extra };
  }
  for (const scenario of ["retry", "terminate", "cancel-wait", "vin-change", "version-change", "missing-vin"]) {
    const starts = [], byJob = new Map(); let choose, checkpoint;
    const invoke = async req => {
      if (req.action === "start") { starts.push(req.profileId); const jobId = `recovery${starts.length}`;
        byJob.set(jobId, { profileId: req.profileId, attempt: starts.filter(id => id === req.profileId).length }); return { ok: true, jobId }; }
      if (req.action === "status") {
        const { profileId, attempt } = byJob.get(req.jobId), isSecond = profileId === targets[1].profileId;
        let doc = final(profileId);
        if (scenario === "missing-vin" && !isSecond) doc.results = doc.results.filter(row => row.decoded?.type !== "vin-ascii");
        if (isSecond && attempt === 1) doc = { ...doc, ok: false, status: "failed", error: "disconnect" };
        if (isSecond && attempt === 2 && scenario === "vin-change") doc.results[1].decoded.text = "WP0ZZZ98ZES000001";
        if (isSecond && attempt === 2 && scenario === "version-change") doc.results[0].decoded.text = "HW2";
        return { ok: true, jobId: req.jobId, state: doc.ok ? "completed" : "failed", final: doc, error: doc.error, released: true, releaseVerified: true };
      }
      return { ok: true };
    };
    const q = createScanQueue({ invoke, sleep: async () => {} });
    const pending = q.run({ nodes: targets, ctx: { mode: "simulation", sessionTask: "read" },
      hooks: { onInterrupted: c => { checkpoint = c; return new Promise(resolve => { choose = resolve; }); } } });
    for (let i = 0; i < 20 && !choose; i++) await Promise.resolve();
    {
      assert.equal(q.busy, true); assert.equal(starts.length, 2, "no automatic restart while waiting for human choice");
      assert.deepEqual(checkpoint.remaining.map(n => n.id), [targets[1].id], "completed first ECU excluded");
      if (scenario === "missing-vin") assert.equal(checkpoint.canRetry, false, "prior results cannot be associated with the retried vehicle");
      if (scenario === "cancel-wait") await q.cancel(); else choose(["terminate"].includes(scenario) ? "terminate" : "retry");
    }
    const out = await pending;
    assert.equal(q.busy, false); assert.equal(out.results[0].classified.kind, KIND.noDtc);
    assert.equal(starts.filter(id => id === targets[0].profileId).length, 1, "prior completed ECU never read again");
    if (["terminate", "cancel-wait", "missing-vin"].includes(scenario)) assert.equal(starts.length, 2);
    else {
      assert.equal(starts.length, 3);
      assert.equal(out.results[1].classified.kind, ["vin-change", "version-change"].includes(scenario) ? KIND.identity : KIND.noDtc);
    }
  }
}

{
  const head = { ok: true, selectedDeviceId: "bt:AABBCCDDEEFF", model: "vLinker", purpose: "diagnostic", connected: false, linkState: "retry-required" };
  for (const scenario of ["closed", "delayed-release", "unsafe-release", "unknown-release", "changed-head", "changed-purpose", "connect-fails", "connect-timeout", "cancel-connect", "simulation"]) {
    let clock = 0, polls = 0, connects = 0, disconnects = 0, cancelled = false;
    const result = await prepareScanRetry({ jobId: "job", mode: scenario === "simulation" ? "simulation" : "live", deviceId: head.selectedDeviceId, model: head.model,
      now: () => clock, budgetMs: 300, sleep: async ms => { clock += ms; }, cancelled: () => cancelled,
      invoke: async () => { polls++; return scenario === "unknown-release" ? { state: "failed" } : {
        released: scenario !== "delayed-release" || polls > 1, releaseVerified: scenario !== "unsafe-release" }; },
      connection: async req => {
        if (req.action === "connect") { connects++; if (scenario === "cancel-connect") cancelled = true;
          return { ...head, linkState: scenario === "connect-fails" ? "retry-required" : "connecting", connected: false }; }
        if (req.action === "disconnect") { disconnects++; return { ...head, linkState: "idle" }; }
        return { ...head, ...(scenario === "changed-head" ? { selectedDeviceId: "bt:000000000000" } : {}),
          ...(scenario === "changed-purpose" ? { purpose: "internal" } : {}),
          ...(connects ? { connected: !["connect-fails", "connect-timeout", "cancel-connect"].includes(scenario), linkState: "connecting" } : {}) };
      } });
    assert.equal(result.ok, ["closed", "delayed-release", "simulation"].includes(scenario), scenario);
    assert.ok(connects <= 1, "one explicit choice makes at most one connection attempt");
    if (["unsafe-release", "unknown-release", "changed-head", "changed-purpose", "simulation"].includes(scenario)) assert.equal(connects, 0);
    if (["connect-timeout", "cancel-connect", "connect-fails"].includes(scenario)) assert.equal(disconnects, 1, "own unfinished attempt stopped");
  }
}
console.log("PASS manual topology continuation, completed-prefix retention, identity/version changes, no write replay, release/connection gates and cancellation");

{
  // Continue a candidate-local terminal response only with independent link and cleanup evidence.
  for (const error of ["NO DATA", "negative-response", "prompt-timeout"]) {
    const starts = [];
    const candidateFailure = error === "NO DATA" ? "no-response" : "negative-response";
    const invoke = async (request) => {
      if (request.action === "start") { starts.push(request.profileId); return { ok: true, jobId: `j${starts.length}` }; }
      if (request.action === "status") return { ok: false, jobId: request.jobId, released: true, releaseVerified: true, state: "failed", error,
        final: { ok: false, status: "failed", mode: "simulation", simulation: true, profileId: starts.at(-1),
          error, ...(error !== "prompt-timeout" ? { candidateFailure, linkHealth: "verified", restoration: { errors: [] } } : {}) } };
      return { ok: true };
    };
    const queue = createScanQueue({ invoke, pollMs: 1, sleep: async () => {} });
    const out = await queue.run({ nodes: adaptedNodes(g981), ctx: { mode: "simulation", sessionTask: "read" } });
    assert.equal(starts.length, error === "prompt-timeout" ? 1 : 2);
    if (error === "NO DATA") assert.equal(out.results[0].classified.kind, KIND.noResponse);
  }
}

// Factory titles stay scoped to the manual generation, module and code namespace.
const dmeScope = { generation: "981", module: "dme", namespace: "sae" };
{
 const literal=factoryTitles.records.filter(row=>row.codeNamespace==='manufacturer-hex');assert.equal(literal.length,113);
 for(const row of literal){
  const record={displayCode:row.code,dtcHex:row.code,manufacturerCode:true,dictionaryMatch:true};
  const scope={generation:row.generation,module:row.module,namespace:'manufacturer-hex'};
  const sameCode=literal.filter(r=>r.generation===row.generation&&r.module===row.module&&r.code===row.code);
  const unique=new Set(sameCode.map(r=>r.title)).size===1&&!sameCode.some(r=>r.applicability?.length);
  assert.equal(manualDtcTitle(record,scope)?.title,unique?row.title:undefined);
  for(const bad of [{...record,dictionaryMatch:false},{...record,manufacturerCode:false},{...record,dtcHex:row.code+'00'},
    {...record,displayCode:row.code.slice(0,5)},{...record,dtcHex:row.code.toLowerCase()}])assert.equal(manualDtcTitle(bad,scope),null);
  assert.equal(manualDtcTitle(record,{...scope,generation:'981'}),null);
  assert.equal(manualDtcTitle(record,{...scope,namespace:'sae'}),null);
 }
 const rear={displayCode:'C13235',dtcHex:'C13235',manufacturerCode:true,dictionaryMatch:true,originalEnglish:'Original English',
  manualScope:{generation:'982',ecuId:35,sourceProfileId:'9x1:BCM_hinten:BCM_hinten_9x1_GT4',sourceCatalogId:'31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba',namespace:'manufacturer-hex'}};
 assert.equal(dtcDetail(rear,'manufacturer-ecu-35'),'Drive Air deflector');
 assert.equal(dtcDetail(rear,'manufacturer-ecu-32'),'Original English');
 for(const mutate of [s=>s.ecuId=32,s=>s.generation='981',s=>s.sourceCatalogId='0'.repeat(64),s=>s.sourceProfileId='unknown',s=>s.namespace='sae',s=>s.other='extra']){
  const bad=structuredClone(rear);mutate(bad.manualScope);assert.equal(dtcDetail(bad,'manufacturer-ecu-35'),'Original English');
 }
 const side={...rear,displayCode:'C11000',dtcHex:'C11000',manualScope:{...rear.manualScope,ecuId:47,sourceProfileId:'9x1:SWSG:SWSG_Master_mit_AFS_ab_1310'}};
 assert.equal(dtcDetail(side,'manufacturer-ecu-48'),'Original English');
 console.log('PASS 113 literal factory references: complete raw code, dictionary membership, vehicle/unit scope and original English fallback; no SAE conversion');
}
assert.equal(manualDtcTitle({ displayCode: "P000A", dtcHex: "000A" }, dmeScope)?.title, "气缸组 1 进气凸轮轴调节太慢");
assert.equal(manualDtcTitle({ displayCode: "P030100" }, dmeScope)?.title, "检测到缺火 气缸 1");
assert.equal(manualDtcTitle({ displayCode: "P030600" }, dmeScope)?.title, "检测到缺火 气缸 6");
assert.match(manualDtcTitle({ displayCode: "P0011" }, dmeScope)?.title || "", /气缸组 1.*，凸轮轴延迟，设定点未达到/);
assert.match(manualDtcTitle({ displayCode: "P1127" }, dmeScope)?.title || "", /燃油修正自适应（多） - 系统过浓/);
assert.match(manualDtcTitle({ displayCode: "P197C" }, dmeScope)?.title || "", /二次空气喷射系统，气缸组 1 和气缸组 2 已互换/);
assert.equal(manualDtcTitle({ displayCode: "P0502" }, dmeScope)?.title, "车速信号 1 - 信号过低");
assert.equal(manualDtcTitle({ displayCode: "P150A" }, dmeScope)?.title, "点火开关关闭时间信号 - 不可靠");
const englishScope = { ...dmeScope, generation: "982" };
assert.equal(manualDtcTitle({ displayCode: "P0030" }, englishScope)?.title, "Oxygen sensor heater driver for oxygen sensor upstream of catalytic converter, bank 1");
assert.equal(manualDtcTitle({ displayCode: "P043E" }, englishScope)?.title, "DMTL - Tank leakage diagnosis module");
assert.equal(manualDtcTitle({ displayCode: "P083F" }, englishScope)?.title, "Clutch switch implausible in relation to clutch travel sensor (parking brake control unit - EPB)");
assert.equal(manualDtcTitle({ displayCode: "P025A" }, englishScope), null, "conditional source entries do not become universally applicable when their titles agree");
assert.equal(manualDtcTitle({ displayCode: "P117B" }, englishScope), null, "extracted Carrera-only heading remains outside target evidence");
assert.equal(manualDtcTitle({ displayCode: "C104A4B" }, englishScope), null, "95B source section overrides a misleading DME header");

assert.equal(manualDtcTitle({ displayCode: "P000A" }, { ...dmeScope, generation: "982" })?.title, "Bank 1 intake camshaft adjustment");
for (const [record, scope] of [
  [{ displayCode: "P1572" }, dmeScope], // Conflicting titles in the source itself.
  [{ displayCode: "P164C00" }, dmeScope],
  [{ displayCode: "U0422" }, dmeScope],
  [{ displayCode: "P9999" }, dmeScope], // No corresponding manual title; do not guess.
  [{ displayCode: "P000A" }, { ...dmeScope, module: "gateway" }],
  [{ displayCode: "P000A" }, { ...dmeScope, generation: "987" }],
  [{ displayCode: "P000A", dtcHex: "000A00" }, dmeScope],
  [{ displayCode: "P000A", dtcHex: "000B" }, dmeScope],
  [{ displayCode: "P000A", manufacturerCode: true }, dmeScope],
  [{ displayCode: "P000A01" }, dmeScope],
]) assert.equal(manualDtcTitle(record, scope), null);
const fault = { displayCode: "U0447", dtcHex: "C447", observedText: "旧码表的中文说明", observedStatusText: "当前故障" };
const snapshot = JSON.stringify(fault);
assert.equal(dtcDetail(fault, "porsche-981-2014-dme"), "网关控制单元 - 信号不可靠 · 当前故障");
assert.equal(JSON.stringify(fault), snapshot, "presentation never changes raw code/status/source evidence");
assert.equal(dtcDetail({ displayCode: "P000A", observedText: "Original English text" }, "porsche-981-2014-dme"), "气缸组 1 进气凸轮轴调节太慢");
assert.equal(dtcDetail({ observedText: "Original English text", observedStatusText: "历史故障" }), "Original English text · 历史故障");
assert.equal(dtcDetail({ observedText: "旧译文", referenceText: "无来源说明", statusHex: "28" }), "故障说明未收录");
assert.equal(dtcDetail({ dtcHex: "FFFF", statusHex: "28" }), "故障说明未收录");

// An operation result, cancellation reply or expired polling budget cannot release
// the next unit. Only the same job's actual, verified release can do that.
for (const scenario of ["completed", "no-response", "negative-response", "unsafe-release", "ipc-error", "poll-budget", "cancel"]) {
  const targets = adaptedNodes(g981), starts = [], cancels = [], observations = [];
  let release = false, observed = false, now = 0;
  const originalNow = Date.now;
  if (scenario === "poll-budget") Date.now = () => now;
  const invoke = async req => {
    if (req.action === "start") { starts.push(req.profileId); return { ok: true, jobId: `release${starts.length}` }; }
    if (req.action === "cancel") { cancels.push(req.jobId); return { ok: true, state: "cancelling" }; }
    if (req.action !== "status") return { ok: true };
    if (scenario === "ipc-error" && !release) throw new Error("observation-failed");
    const local = ["no-response", "negative-response"].includes(scenario);
    const error = local ? scenario === "no-response" ? "NO DATA" : "negative-response" : null;
    const final = { ...(starts.at(-1).endsWith("gateway") ? gwFinal : healthy), profileId: starts.at(-1), mode: "simulation", simulation: true,
      ...(local ? { ok: false, status: "failed", error, candidateFailure: scenario, linkHealth: "verified", restoration: { errors: [] } } : {}) };
    return { ok: !local, jobId: req.jobId, state: local ? "failed" : "completed", error, final,
      released: release, releaseVerified: release && scenario !== "unsafe-release" };
  };
  const queue = createScanQueue({ invoke, pollMs: 1, pollBudgetMs: scenario === "poll-budget" ? 5 : 10000,
    sleep: async () => { observed = true; now = 10; await new Promise(resolve => setTimeout(resolve, 1)); } });
  let finished = false;
  try {
    const promise = queue.run({ nodes: targets, ctx: { mode: "simulation", sessionTask: "read" },
      hooks: { onStatus: doc => observations.push(doc) } }).then(result => { finished = true; return result; });
    for (let i = 0; i < 100 && !observed; i++) await new Promise(resolve => setTimeout(resolve, 1));
    assert(observed && queue.busy && !finished && starts.length === 1, `${scenario}: terminal result retains lease`);
    if (scenario === "cancel") await queue.cancel();
    // Keep the operation timeout isolated to its scenario; wall time cannot expire other fixtures.
    if (scenario !== "poll-budget") now = 0;
    release = true;
    const result = await promise;
    assert(!queue.busy, `${scenario}: release settles queue`);
    if (["ipc-error", "poll-budget", "unsafe-release", "cancel"].includes(scenario)) assert.equal(starts.length, 1, scenario);
    else assert.equal(starts.length, 2, `${scenario}: next unit starts only after release`);
    if (scenario === "unsafe-release") assert.equal(result.results[0].classified.error, "adapter_release_not_verified");
    if (scenario === "poll-budget") assert.equal(result.results[0].classified.error, "poll-timeout");
    if (scenario === "no-response") assert.equal(result.results[0].classified.kind, KIND.noResponse);
    assert(cancels.filter(id => id === "release1").length <= 1, `${scenario}: no repeated cancellation`);
    assert(observations.some(doc => doc.cleanupPending), `${scenario}: waiting remains visible`);
  } finally { Date.now = originalNow; }
}
console.log("PASS topology actual-release waits, real failed-snapshot continuation, IPC failure, deadline, cancellation and unsafe release");

// An uncertain clear can reconnect for read-only verification of its original range.
{
  const targets = adaptedNodes(g981), originalVin = "WP0ZZZ98ZES000000";
  for (const scenario of ["recover", "terminate", "changed-vin", "changed-version", "missing-vin", "unsafe-release", "read-fails", "first-unit"]) {
    const starts = [], jobs = new Map(); let choices = 0;
    const invoke = async req => {
      if (req.action === "start") {
        const jobId = `clear-check-${starts.length + 1}`; starts.push(req); jobs.set(jobId, req);
        return { ok: true, jobId };
      }
      if (req.action !== "status") return { ok: true };
      const request = jobs.get(req.jobId), gateway = request.profileId === ADAPTED_PROFILES[0];
      const writing = request.sessionTask === "clear", interrupted = writing && (scenario === "first-unit" ? gateway : !gateway);
      const final = { ok: !interrupted, status: interrupted ? "failed" : "completed", error: interrupted ? "disconnect" : null,
        mode: "live", simulation: false, profileId: request.profileId, sessionTask: request.sessionTask,
        identityQualification: { observedProfileMatch: true }, results: [
          { role: "identity", field: "hardware", ok: true, decoded: { ok: true, text: !writing && scenario === "changed-version" ? "HW2" : "HW1" } },
          { role: "dependent", operationId: gateway ? "gw-vin" : "dme-vin", ok: true, freshlyRead: true, historical: false,
            decoded: { ok: true, type: "vin-ascii", text: !writing && scenario === "changed-vin" ? "WP0ZZZ98ZES000001" : originalVin } },
          { role: "dependent", operationId: gateway ? "gw-dtc" : "dme-dtc", ok: true, freshlyRead: true, historical: false,
            decoded: { ok: true, type: gateway ? "uds-dtc-19-02" : "kwp-dtc-18", records: [] } },
        ] };
      if (!writing && scenario === "missing-vin") final.results = final.results.filter(row => row.decoded?.type !== "vin-ascii");
      if (!writing && scenario === "read-fails") Object.assign(final, { ok: false, status: "failed", error: "disconnect" });
      return { ok: true, jobId: req.jobId, state: final.ok ? "completed" : "failed", final, error: final.error,
        released: true, releaseVerified: scenario !== "unsafe-release" };
    };
    const q = createScanQueue({ invoke, sleep: async () => {} });
    const out = await q.run({ nodes: targets, ctx: { mode: "live", sessionTask: "clear", confirmedClearDtc: true, x431Inactive: true },
      hooks: { onInterrupted: async checkpoint => {
        choices++; assert.equal(checkpoint.readback, true); assert.equal(checkpoint.canRetry, true);
        if (choices === 1) assert.deepEqual(checkpoint.remaining, targets);
        return scenario === "terminate" || choices > 1 ? "terminate" : "retry";
      } } });
    const writes = starts.filter(req => req.sessionTask === "clear"), reads = starts.filter(req => req.sessionTask === "read");
    assert.equal(writes.length, scenario === "first-unit" || scenario === "unsafe-release" ? 1 : 2);
    for (const req of reads) { assert.equal(req.confirmedReadOnly, true); assert.equal(req.confirmedClearDtc, undefined); }
    if (["recover", "first-unit"].includes(scenario)) {
      assert.deepEqual(reads.map(req => req.profileId), targets.map(node => node.profileId));
      assert.equal(out.readbackStarted, true); assert.equal(out.clearOutcomeUnconfirmed, true); assert.equal(out.error, null);
      assert.equal(out.clearResults.length, writes.length);
    } else if (["changed-vin", "changed-version", "missing-vin"].includes(scenario)) {
      assert.equal(reads.length, 1); assert.equal(out.results[0].classified.kind, KIND.identity);
    } else if (scenario === "read-fails") {
      assert.equal(reads.length, 1); assert.equal(choices, 2); assert.equal(out.error, "cancelled");
    } else {
      assert.equal(reads.length, 0); assert.equal(choices, scenario === "unsafe-release" ? 0 : 1);
    }
  }
}
console.log("PASS uncertain-clear original-range readback, no write replay/continuation, identity and release gates, termination and readback interruption");

// A user stop of a read batch finishes the current job and its real release;
// it cannot cancel that job early, start another ECU or enter recovery.
for (const phase of ["pending-start", "running", "terminal-unreleased", "failed"]) {
  const targets = adaptedNodes(g981), starts = [], cancels = [];
  let release = false, statusSeen = false, interrupted = 0, allowStart;
  const startGate = new Promise(resolve => { allowStart = resolve; });
  const invoke = async req => {
    if (req.action === "start") {
      starts.push(req);
      if (phase === "pending-start") await startGate;
      return { ok: true, jobId: `stop-read-${starts.length}` };
    }
    if (req.action === "cancel") { cancels.push({ jobId: req.jobId, released: release }); return { ok: true }; }
    assert.equal(req.action, "status"); statusSeen = true;
    const failed = phase === "failed";
    const final = { ...(starts.at(-1).profileId.endsWith("gateway") ? gwFinal : healthy),
      profileId: starts.at(-1).profileId, mode: "simulation", simulation: true,
      ...(failed ? { ok: false, status: "failed", error: "disconnect" } : {}) };
    return { ok: !failed, jobId: req.jobId, state: !release && phase === "running" ? "running" : failed ? "failed" : "completed",
      error: failed ? "disconnect" : null, final, released: release, releaseVerified: release };
  };
  const queue = createScanQueue({ invoke, pollMs: 1 });
  assert.equal(queue.stopAfterCurrent(), false, "idle queue cannot receive a stop request");
  let finished = false;
  const promise = queue.run({ nodes: targets, ctx: { mode: "simulation", sessionTask: "read" },
    hooks: { onInterrupted: () => { interrupted++; return "terminate"; } } }).then(out => { finished = true; return out; });
  if (phase !== "pending-start") {
    for (let i=0; i<100 && !statusSeen; i++) await new Promise(resolve => setTimeout(resolve, 1));
    assert(statusSeen);
  }
  assert.equal(queue.stopAfterCurrent(), true);
  if (phase === "pending-start") allowStart();
  for (let i=0; i<100 && !statusSeen; i++) await new Promise(resolve => setTimeout(resolve, 1));
  assert(statusSeen && queue.busy && !finished && starts.length === 1);
  assert.equal(cancels.length, 0, "graceful stop never cancels the current read before release");
  release = true;
  const out = await promise;
  assert(out.stopped && !queue.busy && starts.length === 1 && interrupted === 0);
  assert.equal(out.results.length, 1);
  assert(cancels.every(row => row.released), "existing terminal-job cleanup runs only after release");
  if (phase === "failed") assert.equal(out.error, "disconnect", "stopping cannot hide the read failure");
  else assert([KIND.dtc, KIND.noDtc].includes(out.results[0].classified.kind), "current read result is preserved");
  if (phase === "running") {
    const restarted = await queue.run({ nodes: targets, ctx: { mode: "simulation", sessionTask: "read" } });
    assert(!restarted.stopped && restarted.results.length === targets.length, "a new batch resets the stop request");
  }
}
{
  let queue;
  queue = createScanQueue({ invoke: async req => {
    if (req.action === "start") {
      assert.equal(queue.stopAfterCurrent(), false, "read stopping cannot interrupt a clear operation");
      return { ok: false, error: "explicit-test-no-write" };
    }
    return { ok: true };
  } });
  const out = await queue.run({ nodes: adaptedNodes(g981), ctx: { mode: "simulation", sessionTask: "clear" } });
  assert.equal(out.stopped, false);
}
console.log("PASS graceful whole-read stop: pending start, active read, actual release, failure visibility, preserved current result, new-batch reset and no clear interruption");
