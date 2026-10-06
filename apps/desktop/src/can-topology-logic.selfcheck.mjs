import assert from "node:assert/strict";
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
  dtcLabel,
  emptyStatusMap,
  flattenNodes,
  generationOf,
  headerTaskState,
  isHealthyGreen,
  liveAllowed,
  mergeStatusAfterJob,
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
    if (req.action === "status") return { ok: false, error: "status_failed" };
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
    if (req.action === "status") throw new Error("ipc");
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
  let starts = 0;
  const invoke = async (req) => {
    if (req.action === "start") {
      starts += 1;
      await new Promise((r) => setTimeout(r, 30));
      return { ok: true, jobId: "jdelay00000000001" };
    }
    if (req.action === "cancel") return { ok: true, state: "cancelled" };
    if (req.action === "status") return { ok: true, state: "running", jobId: req.jobId };
    return { ok: true };
  };
  const q = createScanQueue({ invoke, pollMs: 5, sleep: (ms) => new Promise((r) => setTimeout(r, ms)), cancelWaitMs: 20, pollBudgetMs: 5000 });
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
      return { ok: true, state: "completed", jobId: req.jobId, final: { ...healthy, profileId: req.jobId } };
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
      return { ok: true, state: "completed", jobId: req.jobId, final };
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
        state: "failed",
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
        state: "failed",
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
  assert.equal(topologyCapability(dme, "VNCI").readable, true);
  assert.equal(topologyCapability(dme, "VNCI").clearable, false);
  assert.equal(topologyCapability(dme, "PT3G").clearable, false);
  assert.equal(topologyCapability(dme, "PT3G").readable, false);
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
  // Continue a candidate-local terminal response only with independent link and cleanup evidence.
  for (const error of ["NO DATA", "negative-response", "prompt-timeout"]) {
    const starts = [];
    const candidateFailure = error === "NO DATA" ? "no-response" : "negative-response";
    const invoke = async (request) => {
      if (request.action === "start") { starts.push(request.profileId); return { ok: true, jobId: `j${starts.length}` }; }
      if (request.action === "status") return { ok: true, state: "failed", error,
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
