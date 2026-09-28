/**
 * Self-check: validate + injected children + Python simulation when sessions.py exists.
 * node apps/desktop/electron/read-only-session.selfcheck.mjs
 */
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  backendReadyPath,
  createReadOnlySessionManager,
  fail,
  liveBlocked,
  stubSpawnFn,
  validateSessionRequest,
} from "./read-only-session.mjs";
import { attachSessionHandoff, createObdConnectionManager, createTransportGate, parseVoltageVolts } from "./obd-connection.mjs";
import {
  afterStatusIpcFailure,
  afterStartJobAssigned,
  buildEnginePrepareRequest,
  buildEngineStartRequest,
  buildStartRequest,
  canOperate,
  engineFreshness,
  enginePlanView,
  formatEngineValue,
  interpretStatusDoc,
  liveReady,
  sampleCurrency,
} from "../src/read-only-session-logic.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const DME = "porsche-981-2014-dme";
const GW = "porsche-981-2014-gateway";

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function mockChild({
  lines = [],
  code = 0,
  hang = false,
  flood = 0,
  emitCloseOnKill = true,
  hangAfterLines = false,
  raw = null,
} = {}) {
  const child = new EventEmitter();
  child.killed = false;
  child.exitCode = null;
  child.stdin = new EventEmitter();
  child.stdin.write = () => true;
  child.stdin.end = () => {};
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {
    child.killed = true;
    child.exitCode = 1;
    if (emitCloseOnKill) queueMicrotask(() => child.emit("close", 1));
  };
  queueMicrotask(() => {
    if (raw != null) {
      child.stdout.emit("data", Buffer.from(raw));
      child.exitCode = code;
      child.emit("close", code);
      return;
    }
    if (lines.length) child.stdout.emit("data", Buffer.from(lines.join("\n") + "\n"));
    if (hang || hangAfterLines) return;
    if (flood) {
      child.stdout.emit("data", Buffer.alloc(flood, 0x41));
      return;
    }
    child.exitCode = code;
    child.emit("close", code);
  });
  return child;
}

const okResult = (extra = {}) =>
  JSON.stringify({
    type: "result",
    ok: true,
    mode: "simulation",
    runId: "simokrunidxxxxxxxx",
    profileId: DME,
    status: "completed",
    error: null,
    results: [],
    simulation: true,
    liveVerified: false,
    writePayload: null,
    ...extra,
  });

const okClearResult = (extra = {}) =>
  JSON.stringify({
    type: "result",
    ok: true,
    mode: "simulation",
    runId: "simokrunidxxxxxxxx",
    profileId: DME,
    status: "completed",
    error: null,
    sessionTask: "clear",
    results: [{ operationId: "dme-dtc", postClear: true, ok: true, decoded: { ok: true, records: [] } }],
    clear: {
      clearSucceeded: true,
      outcome: "cleared-zero",
      dtcCountAfter: 0,
      requestHex: "14FF00",
      expectedPositiveHex: "54FF00",
    },
    simulation: true,
    liveVerified: false,
    writePayload: null,
    ...extra,
  });

async function waitJob(mgr, jobId, ownerId, ms = 4000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const s = await mgr.handle({ action: "status", jobId }, { ownerId });
    if (s.state && s.state !== "running" && s.state !== "cancelling") return s;
    await new Promise((r) => setTimeout(r, 15));
  }
  return mgr.handle({ action: "status", jobId }, { ownerId });
}

assert(validateSessionRequest({ action: "nope" }).error === "invalid_action", "invalid_action");
assert(validateSessionRequest({ action: "prepare", profileId: DME, port: "COM3" }).error === "forbidden_field", "port");
assert(validateSessionRequest({ action: "start", profileId: "nope" }).error === "invalid_profile_id", "profile");
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    mode: "live",
    confirmedReadOnly: true,
    x431Inactive: false,
  }).error === "live_confirmation_required",
  "live checks",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "clear",
    confirmedClearDtc: true,
    x431Inactive: true,
    confirmedReadOnly: true,
  }).error === "clear_conflicts_read_only",
  "clear not read-only",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "clear",
    x431Inactive: true,
  }).error === "live_clear_confirmation_required",
  "clear live flags",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "clear",
    resumeRunId: "s1234567",
  }).error === "clear_resume_forbidden",
  "clear resume",
);
assert(validateSessionRequest({ action: "overview", profileId: DME }).error === "forbidden_field", "overview extra");
assert(validateSessionRequest({ action: "overview" }) == null, "overview ok");
assert(fail("x").liveVerified === false, "flags");
assert(liveBlocked({ spawnFn: () => {} }, {}) === "injected_child", "injected block");
assert(liveBlocked({}, { PORSCHE981_HEADLESS: "1" }) === "headless", "headless block");
assert(liveBlocked({}, { PORSCHE981_SESSION_DENY_LIVE: "1" }) === "deny_live", "deny_live block");

const envAllowInjectedLive = { ...process.env };
delete envAllowInjectedLive.PORSCHE981_SESSION_DENY_LIVE;
delete envAllowInjectedLive.PORSCHE981_HEADLESS;

const mgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: stubSpawnFn(),
  gracefulMs: 40,
  killWatchMs: 20,
});
const prep = await mgr.handle({ action: "prepare", profileId: DME }, { ownerId: 1 });
assert(prep.ok && prep.plan?.identityOperations, `prepare ${JSON.stringify(prep)}`);

const started = await mgr.handle({ action: "start", profileId: DME, mode: "simulation" }, { ownerId: 1 });
const done = await waitJob(mgr, started.jobId, 1);
assert(done.state === "completed" && done.final?.liveVerified === false, `done ${done.state}`);
assert((await mgr.handle({ action: "status", jobId: started.jobId }, { ownerId: 2 })).error === "ownership_denied", "own");

const ovIdle = await mgr.handle({ action: "overview" }, { ownerId: 1 });
assert(ovIdle.ok && ovIdle.taskState === "idle" && ovIdle.liveVerified === false, `overview idle ${JSON.stringify(ovIdle)}`);
const ovOther = await mgr.handle({ action: "overview" }, { ownerId: 2 });
assert(ovOther.ok && ovOther.taskState === "idle" && ovOther.latestJobId !== started.jobId, "overview ownership");

const clearShapeMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ lines: [okResult()], code: 0 }),
});
const clearShape = await waitJob(
  clearShapeMgr,
  (await clearShapeMgr.handle({ action: "start", profileId: DME, sessionTask: "clear" }, { ownerId: 1 })).jobId,
  1,
);
assert(clearShape.state === "failed" && clearShape.error === "protocol_error", `read-shaped clear ${clearShape.error}`);

const clearOkMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ lines: [okClearResult()], code: 0 }),
});
const clearOk = await waitJob(
  clearOkMgr,
  (await clearOkMgr.handle({ action: "start", profileId: DME, sessionTask: "clear" }, { ownerId: 1 })).jobId,
  1,
);
assert(clearOk.state === "completed", `clear final ${clearOk.state} ${clearOk.error}`);
assert(clearOk.final?.sessionTask === "clear" && clearOk.final?.clear?.clearSucceeded === true, "clear metadata");
assert(clearOk.final?.results?.some((r) => r.postClear && r.operationId === "dme-dtc"), "postClear dependent");

const offMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () =>
    mockChild({
      lines: [
        JSON.stringify({
          type: "result",
          ok: false,
          mode: "simulation",
          profileId: DME,
          status: "failed",
          error: "vlinker-port-not-unique:2",
          results: [],
          simulation: true,
          liveVerified: false,
          writePayload: null,
        }),
      ],
      code: 1,
    }),
});
await waitJob(offMgr, (await offMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert((await offMgr.handle({ action: "overview" }, { ownerId: 1 })).taskState === "offline", "offline unique port");
const off2 = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () =>
    mockChild({
      lines: [
        JSON.stringify({
          type: "result",
          ok: false,
          mode: "simulation",
          profileId: DME,
          status: "failed",
          error: "pending-timeout",
          results: [],
          simulation: true,
          liveVerified: false,
          writePayload: null,
        }),
      ],
      code: 1,
    }),
});
await waitJob(off2, (await off2.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert((await off2.handle({ action: "overview" }, { ownerId: 1 })).taskState === "offline", "offline pending-timeout");

let spawned = 0;
const denyMgr = createReadOnlySessionManager({
  repoRoot,
  env: { ...process.env, PORSCHE981_HEADLESS: "1" },
  spawnFn: () => {
    spawned += 1;
    return mockChild({ lines: [okResult()] });
  },
});
const liveDeny = await denyMgr.handle(
  { action: "start", profileId: DME, mode: "live", confirmedReadOnly: true, x431Inactive: true },
  { ownerId: 1 },
);
assert(liveDeny.error === "live_not_enabled" && spawned === 0, `deny spawn ${spawned} ${liveDeny.error}`);
const liveClearDeny = await denyMgr.handle(
  {
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "clear",
    confirmedClearDtc: true,
    x431Inactive: true,
  },
  { ownerId: 1 },
);
assert(liveClearDeny.error === "live_not_enabled" && spawned === 0, "deny clear live");

let written = "";
const probe = createReadOnlySessionManager({
  repoRoot,
  allowInjectedLive: true,
  env: envAllowInjectedLive,
  spawnFn: () => {
    const c = mockChild({
      lines: [
        JSON.stringify({
          type: "result",
          ok: true,
          mode: "live",
          runId: "liverunidxxxxxxxx",
          profileId: DME,
          status: "completed",
          error: null,
          results: [],
          simulation: false,
          liveVerified: false,
          writePayload: null,
        }),
      ],
    });
    c.stdin.write = (chunk) => {
      written += String(chunk);
      return true;
    };
    return c;
  },
});
const liveMap = await probe.handle(
  { action: "start", profileId: DME, mode: "live", confirmedReadOnly: true, x431Inactive: true },
  { ownerId: 1 },
);
assert(liveMap.ok, "live probe start");
await waitJob(probe, liveMap.jobId, 1);
const sent = JSON.parse(written.trim().split("\n")[0]);
assert(sent.action === "run" && sent.mode === "live" && sent.confirmedReadOnly === true && sent.x431Inactive === true, `map ${written}`);
assert(!("port" in sent) && !("path" in sent) && !("deviceId" in sent), "no port");

let clearWritten = "";
const clearProbe = createReadOnlySessionManager({
  repoRoot,
  allowInjectedLive: true,
  env: envAllowInjectedLive,
  spawnFn: () => {
    const c = mockChild({
      lines: [
        JSON.stringify({
          type: "result",
          ok: true,
          mode: "live",
          runId: "liverunidxxxxxxxx",
          profileId: DME,
          status: "completed",
          error: null,
          results: [],
          simulation: false,
          liveVerified: false,
          writePayload: null,
        }),
      ],
    });
    c.stdin.write = (chunk) => {
      clearWritten += String(chunk);
      return true;
    };
    return c;
  },
});
const clearMap = await clearProbe.handle(
  {
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "clear",
    confirmedClearDtc: true,
    x431Inactive: true,
  },
  { ownerId: 1 },
);
assert(clearMap.ok, "clear live probe start");
await waitJob(clearProbe, clearMap.jobId, 1);
const clearSent = JSON.parse(clearWritten.trim().split("\n")[0]);
assert(clearSent.sessionTask === "clear" && clearSent.confirmedClearDtc === true && clearSent.x431Inactive === true, `clear map ${clearWritten}`);
assert(clearSent.confirmedReadOnly !== true, "clear must not claim read-only");

const hangMgr = createReadOnlySessionManager({
  repoRoot,
  timeoutMs: 80,
  gracefulMs: 20,
  killWatchMs: 20,
  spawnFn: () => mockChild({ hang: true }),
});
const slow = await hangMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 });
assert((await hangMgr.handle({ action: "overview" }, { ownerId: 1 })).taskState === "running", "overview running");
assert((await hangMgr.handle({ action: "start", profileId: GW }, { ownerId: 1 })).error === "busy", "busy");
const timed = await waitJob(hangMgr, slow.jobId, 1, 3000);
assert(timed.error === "timeout" || timed.state === "failed", `timeout ${timed.error}`);

const capMgr = createReadOnlySessionManager({
  repoRoot,
  maxStdout: 16,
  spawnFn: () => mockChild({ flood: 64 }),
  killWatchMs: 20,
});
const cap = await waitJob(capMgr, (await capMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert(cap.error === "output_cap", `cap ${cap.error}`);

const nzMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ lines: [okResult()], code: 2 }),
});
const nz = await waitJob(nzMgr, (await nzMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert(nz.state === "failed" && nz.error === "process_failed", `nonzero ${nz.error}`);

const extraMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ lines: [okResult(), "not-json"], code: 0 }),
  killWatchMs: 20,
});
const extra = await waitJob(extraMgr, (await extraMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert(extra.state === "failed" && extra.final?.ok === true && extra.error, `extra ${extra.error} ${extra.state}`);

const hangOk = createReadOnlySessionManager({
  repoRoot,
  timeoutMs: 80,
  gracefulMs: 20,
  killWatchMs: 20,
  spawnFn: () => mockChild({ lines: [okResult()], hangAfterLines: true, emitCloseOnKill: true }),
});
const hangOkJob = await waitJob(
  hangOk,
  (await hangOk.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId,
  1,
  3000,
);
assert(hangOkJob.state === "failed", `hang after result ${hangOkJob.state} ${hangOkJob.error}`);

const missMgr = createReadOnlySessionManager({ repoRoot, spawnFn: () => mockChild({ lines: [], code: 0 }) });
const miss = await waitJob(missMgr, (await missMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert(miss.error === "missing_result", `miss ${miss.error}`);

const canMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ hang: true }),
  gracefulMs: 80,
  killWatchMs: 20,
});
const canStart = await canMgr.handle({ action: "start", profileId: DME }, { ownerId: 7 });
await canMgr.handle({ action: "cancel", jobId: canStart.jobId }, { ownerId: 7 });
assert((await canMgr.handle({ action: "overview" }, { ownerId: 7 })).taskState === "running", "overview cancelling");
const canDone = await waitJob(canMgr, canStart.jobId, 7, 3000);
assert(canDone.state === "cancelled" || canDone.final?.status === "cancelled" || canDone.state === "failed", `cancel ${canDone.state}`);

const trailMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ raw: okResult() + "\nGARBAGE", code: 0 }),
});
const trailJob = await waitJob(
  trailMgr,
  (await trailMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId,
  1,
);
assert(trailJob.state === "failed" && trailJob.error === "malformed_output", `trail ${trailJob.error}`);

const sigMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ lines: [okResult()], code: null }),
});
const sig = await waitJob(sigMgr, (await sigMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert(sig.state === "failed" && sig.error === "process_failed", `signal ${sig.error} ${sig.state}`);

const mfMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () =>
    mockChild({
      lines: [
        JSON.stringify({
          type: "result",
          ok: true,
          mode: "simulation",
          profileId: DME,
          status: "completed",
          runId: "simokrunidxxxxxxxx",
          simulation: true,
          liveVerified: false,
          writePayload: null,
          error: null,
        }),
      ],
      code: 0,
    }),
});
const mf = await waitJob(mfMgr, (await mfMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 })).jobId, 1);
assert(mf.state === "failed", `mf ${mf.error}`);

const seq = [];
const t0 = Date.now();
const toMgr = createReadOnlySessionManager({
  repoRoot,
  timeoutMs: 40,
  gracefulMs: 80,
  killWatchMs: 20,
  spawnFn: () => {
    const c = mockChild({ hang: true, emitCloseOnKill: true });
    c.stdin.write = (chunk) => {
      const s = String(chunk);
      if (s.includes("cancel")) seq.push({ ev: "cancel", t: Date.now() - t0 });
      return true;
    };
    const orig = c.kill;
    c.kill = () => {
      seq.push({ ev: "kill", t: Date.now() - t0 });
      return orig();
    };
    return c;
  },
});
const toStart = await toMgr.handle({ action: "start", profileId: DME }, { ownerId: 1 });
await waitJob(toMgr, toStart.jobId, 1, 3000);
const ci = seq.findIndex((x) => x.ev === "cancel");
const ki = seq.findIndex((x) => x.ev === "kill");
assert(ci >= 0 && ki > ci, `cancel before kill ${JSON.stringify(seq)}`);
assert(seq[ki].t - seq[ci].t >= 50, `grace ${JSON.stringify(seq)}`);

assert(liveReady(true, true) && !liveReady(true, false), "liveReady");
const sel = `${DME}:live`;
assert(!canOperate("live", false, "s1234567", sel, sel, false).resume, "resume needs liveReady");
assert(canOperate("live", false, "s1234567", sel, sel, true).resume, "resume with liveReady");
assert(buildStartRequest({ mode: "live", profileId: DME, confirmedReadOnly: false, x431Inactive: true }).error === "live_confirmation_required", "handler gate");
assert(buildStartRequest({ mode: "live", profileId: DME, confirmedReadOnly: true, x431Inactive: true }).confirmedReadOnly === true, "send actual");
let cancelled = false;
const bad = await interpretStatusDoc({ ok: false, error: "unknown_job" }, "jabc", async () => {
  cancelled = true;
});
assert(bad.stop && cancelled, "status ok=false cancels");
let ipcCancel = false;
await afterStatusIpcFailure("jabc", async () => {
  ipcCancel = true;
});
assert(ipcCancel, "ipc fail cancels");

assert(
  validateSessionRequest({
    action: "start",
    profileId: GW,
    sessionTask: "engine",
  }).error === "invalid_profile_id",
  "engine gateway",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "read",
    sampleCycles: 5,
  }).error === "engine_fields_without_task",
  "cycles on read",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "engine",
    sampleCycles: true,
  }).error === "invalid_sample_cycles",
  "cycles bool",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "engine",
    intervalMs: 100,
  }).error === "invalid_interval_ms",
  "interval low",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "engine",
    resumeRunId: "s1234567",
  }).error === "engine_resume_forbidden",
  "engine resume",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "engine",
    operationIds: ["x"],
  }).error === "engine_operation_ids_forbidden",
  "engine ops",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    sessionTask: "engine",
    confirmedClearDtc: true,
  }).error === "engine_conflicts_clear",
  "engine clear flag",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "engine",
    confirmedReadOnly: true,
    x431Inactive: true,
  }) == null,
  "engine live ok",
);
assert(
  validateSessionRequest({
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "engine",
    x431Inactive: true,
  }).error === "live_confirmation_required",
  "engine live flags",
);
assert(
  buildEngineStartRequest({ mode: "live", confirmedReadOnly: false, x431Inactive: true }).error === "live_confirmation_required",
  "eng handler gate",
);
assert(buildEngineStartRequest({ mode: "simulation", scenario: "success" }).sessionTask === "engine", "eng sim start");
assert(buildEnginePrepareRequest({ sampleCycles: 3, intervalMs: 800 }).sampleCycles === 3, "eng prepare cycles");
assert(buildEnginePrepareRequest({ sampleCycles: 3, intervalMs: 800 }).intervalMs === 800, "eng prepare interval");
assert(engineFreshness("running", null, null, "live") === "sampling", "fresh live sampling");
assert(engineFreshness("running", null, null, "simulation") === "sampling-sim", "fresh sim sampling");
assert(engineFreshness("running", null, null) === "sampling-sim", "fresh running default sim");
assert(engineFreshness("cancelling", null, null, "live") === "cancelling", "fresh cancelling");
assert(engineFreshness("completed", { mode: "simulation", status: "completed" }, null) === "simulated", "fresh sim");
assert(engineFreshness("failed", { mode: "live" }, "disconnect") === "stale", "fresh stale");
assert(afterStartJobAssigned(null, true, true).action === "fail", "start no id");
assert(afterStartJobAssigned("job-1", true, true).action === "cancel-pending", "cancel during start");
assert(afterStartJobAssigned("job-1", false, false).action === "cancel-unmount", "unmount cancel");
assert(formatEngineValue(12.345, "C") === "12.35 °C", "celsius format");
assert(formatEngineValue(900, "rpm") === "900 rpm", "int format");
assert(
  sampleCurrency({ pid: "0C", cycle: 1, capturedUtc: "2026-09-27T00:00:00Z", value: 800 }, {
    freshness: "sampling",
    latestCycle: 1,
    nowMs: Date.parse("2026-09-27T00:00:02Z"),
    intervalMs: 1000,
    waiting: true,
  }).stale === true,
  "stale while waiting",
);
assert(
  sampleCurrency({ pid: "0C", cycle: 1, capturedUtc: "2026-09-27T00:00:00Z", value: 800 }, {
    freshness: "sampling",
    latestCycle: 1,
    nowMs: Date.parse("2026-09-27T00:00:00.200Z"),
    intervalMs: 1000,
    waiting: true,
  }).stale === false,
  "fresh live within interval",
);
assert(
  enginePlanView({
    engine: {
      definitions: [{ pid: "0C", label: "Engine RPM" }, { pid: "05" }],
      sampleCycles: 3,
      intervalMs: 800,
    },
  }).text.includes("转速") &&
    !enginePlanView({
      engine: { definitions: [{ pid: "0C" }], sampleCycles: 3, intervalMs: 800, evidenceStatus: "standard-obd-unverified-on-vehicle" },
    }).text.includes("standard-obd"),
  "plan chinese no enum",
);

function engineBlob(extra = {}) {
  return {
    supportedPids: ["04", "05", "0C", "0D", "0F", "11"],
    unsupportedPids: [],
    samples: [
      {
        pid: "0C",
        label: "rpm",
        value: 800,
        unit: "rpm",
        capturedUtc: "2026-09-27T00:00:00Z",
        elapsedMs: 12,
        cycle: 1,
        synthetic: true,
      },
    ],
    completedCycles: 1,
    sampleCycles: 5,
    intervalMs: 1000,
    ...extra,
  };
}

const okEngineResult = (extra = {}) =>
  JSON.stringify({
    type: "result",
    ok: true,
    mode: "simulation",
    runId: "simokrunidxxxxxxxx",
    profileId: DME,
    status: "completed",
    error: null,
    sessionTask: "engine",
    results: [{ operationId: "dme-dsn", ok: true }],
    engine: engineBlob(),
    simulation: true,
    liveVerified: false,
    writePayload: null,
    ...extra,
  });

const engineShapeMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () => mockChild({ lines: [okResult()], code: 0 }),
});
const engineShape = await waitJob(
  engineShapeMgr,
  (await engineShapeMgr.handle({ action: "start", profileId: DME, sessionTask: "engine" }, { ownerId: 1 })).jobId,
  1,
);
assert(engineShape.state === "failed" && engineShape.error === "protocol_error", `read-shaped engine ${engineShape.error}`);

const engineOkMgr = createReadOnlySessionManager({
  repoRoot,
  spawnFn: () =>
    mockChild({
      lines: [
        JSON.stringify({
          type: "progress",
          runId: "simokrunidxxxxxxxx",
          stage: "engine",
          profileId: DME,
          completed: 1,
          total: 5,
          engine: engineBlob({ completedCycles: 1 }),
        }),
        okEngineResult(),
      ],
      code: 0,
    }),
});
const engineOk = await waitJob(
  engineOkMgr,
  (
    await engineOkMgr.handle(
      { action: "start", profileId: DME, sessionTask: "engine", sampleCycles: 5, intervalMs: 1000 },
      { ownerId: 1 },
    )
  ).jobId,
  1,
);
assert(engineOk.state === "completed", `engine final ${engineOk.state} ${engineOk.error}`);
assert(engineOk.final?.sessionTask === "engine" && engineOk.final?.engine?.samples?.length === 1, "engine metadata");
assert(engineOk.final?.liveVerified === false && engineOk.final?.writePayload === null, "engine flags");

let engineWritten = "";
const engineProbe = createReadOnlySessionManager({
  repoRoot,
  allowInjectedLive: true,
  env: envAllowInjectedLive,
  spawnFn: () => {
    const c = mockChild({
      lines: [
        JSON.stringify({
          type: "result",
          ok: true,
          mode: "live",
          runId: "liverunidxxxxxxxx",
          profileId: DME,
          status: "completed",
          error: null,
          sessionTask: "engine",
          results: [],
          engine: engineBlob(),
          simulation: false,
          liveVerified: false,
          writePayload: null,
        }),
      ],
    });
    c.stdin.write = (chunk) => {
      engineWritten += String(chunk);
      return true;
    };
    return c;
  },
});
const engineMap = await engineProbe.handle(
  {
    action: "start",
    profileId: DME,
    mode: "live",
    sessionTask: "engine",
    confirmedReadOnly: true,
    x431Inactive: true,
  },
  { ownerId: 1 },
);
assert(engineMap.ok, "engine live probe start");
await waitJob(engineProbe, engineMap.jobId, 1);
const engineSent = JSON.parse(engineWritten.trim().split("\n")[0]);
assert(
  engineSent.sessionTask === "engine" && engineSent.confirmedReadOnly === true && engineSent.x431Inactive === true,
  `engine map ${engineWritten}`,
);
assert(engineSent.sampleCycles === 5 && engineSent.intervalMs === 1000, "engine defaults");
assert(engineSent.confirmedClearDtc !== true, "engine must not clear");

const hangEng = createReadOnlySessionManager({
  repoRoot,
  timeoutMs: 80,
  gracefulMs: 20,
  killWatchMs: 20,
  spawnFn: () => mockChild({ hang: true }),
});
const hangEngStart = await hangEng.handle({ action: "start", profileId: DME, sessionTask: "engine" }, { ownerId: 1 });
assert((await hangEng.handle({ action: "start", profileId: DME, sessionTask: "read" }, { ownerId: 1 })).error === "busy", "engine locks read");
assert((await hangEng.handle({ action: "start", profileId: DME, sessionTask: "clear" }, { ownerId: 1 })).error === "busy", "engine locks clear");
await waitJob(hangEng, hangEngStart.jobId, 1, 3000);

const python = [];
const sessionsPy = path.join(repoRoot, "scripts", "diagnostics", "sessions.py");
if (fs.existsSync(sessionsPy)) {
  const real = createReadOnlySessionManager({ repoRoot, prepareTimeoutMs: 60_000, timeoutMs: 120_000 });
  for (const profileId of [DME, GW]) {
    const p = await real.handle({ action: "prepare", profileId, mode: "simulation" }, { ownerId: 1 });
    python.push({ profileId, action: "prepare", ok: p.ok, error: p.error || null, steps: p.plan?.identityOperations?.length });
    assert(p.ok && p.plan?.identityOperations?.length, `real prepare ${profileId}`);
    const s = await real.handle({ action: "start", profileId, mode: "simulation", scenario: "success" }, { ownerId: 1 });
    const d = await waitJob(real, s.jobId, 1, 120_000);
    python.push({ profileId, action: "run", state: d.state, error: d.error, liveVerified: d.final?.liveVerified });
    assert(d.state === "completed" && d.final?.liveVerified === false, `real run ${profileId} ${d.state} ${d.error}`);
    assert(d.final?.simulation === true || d.final?.mode === "simulation", "sim");
  }
  const mm = await real.handle(
    { action: "start", profileId: DME, mode: "simulation", scenario: "identity-mismatch" },
    { ownerId: 1 },
  );
  const md = await waitJob(real, mm.jobId, 1, 120_000);
  python.push({ action: "identity-mismatch", state: md.state, error: md.final?.error || md.error, ok: md.final?.ok });
  assert(md.final && md.final.ok === false, `mismatch ${JSON.stringify(md.final).slice(0, 300)}`);
  const cs = await real.handle({ action: "start", profileId: DME, mode: "simulation", sessionTask: "clear" }, { ownerId: 1 });
  const cd = await waitJob(real, cs.jobId, 1, 120_000);
  python.push({
    action: "clear",
    state: cd.state,
    error: cd.error,
    outcome: cd.final?.clear?.outcome,
    dtcCountAfter: cd.final?.clear?.dtcCountAfter,
    writePayload: cd.final?.writePayload,
  });
  assert(cd.state === "completed" && cd.final?.clear?.clearSucceeded === true, `real clear ${cd.state} ${cd.error}`);
  assert(cd.final?.sessionTask === "clear", "sessionTask");
  assert(cd.final?.writePayload === null && cd.final?.liveVerified === false, "clear flags");
  assert(cd.final?.clear?.expectedPositiveHex === "54FF00", "kwp echo");
  assert(cd.final?.clear?.dtcCountAfter === 0, "post-clear count from decode");
  assert(cd.final?.results?.some((r) => r.postClear && r.operationId === "dme-dtc"), "real postClear");
  const ep = await real.handle(
    { action: "prepare", profileId: DME, mode: "simulation", sessionTask: "engine", sampleCycles: 3, intervalMs: 800 },
    { ownerId: 1 },
  );
  python.push({
    action: "engine-prepare",
    ok: ep.ok,
    error: ep.error || null,
    sampleCycles: ep.plan?.engine?.sampleCycles ?? null,
    intervalMs: ep.plan?.engine?.intervalMs ?? null,
    definitionKind: typeof ep.plan?.engine?.definitions?.[0],
  });
  if (ep.ok) {
    assert(Array.isArray(ep.plan?.engine?.definitions) && typeof ep.plan.engine.definitions[0] === "object", "engine defs objects");
    assert(typeof ep.plan.engine.definitions[0].pid === "string", "engine def pid");
    assert(ep.plan.engine.sampleCycles === 3 && ep.plan.engine.intervalMs === 800, "engine prepare options");
    assert(ep.plan.engine.options?.sampleCycles && ep.plan.engine.options?.intervalMs, "engine option bounds");
  }
  if (ep.ok) {
    const es = await real.handle(
      { action: "start", profileId: DME, mode: "simulation", sessionTask: "engine", scenario: "success", sampleCycles: 1, intervalMs: 500 },
      { ownerId: 1 },
    );
    const ed = await waitJob(real, es.jobId, 1, 120_000);
    python.push({
      action: "engine",
      state: ed.state,
      error: ed.error,
      supported: ed.final?.engine?.supportedPids,
      unsupported: ed.final?.engine?.unsupportedPids,
      samples: ed.final?.engine?.samples?.length,
    });
    assert(ed.state === "completed" && ed.final?.sessionTask === "engine", `real engine ${ed.state} ${ed.error}`);
    assert(ed.final?.liveVerified === false && ed.final?.writePayload === null, "engine flags real");
    assert(Array.isArray(ed.final?.engine?.supportedPids), "engine pids");
    const em = await real.handle(
      { action: "start", profileId: DME, mode: "simulation", sessionTask: "engine", scenario: "identity-mismatch" },
      { ownerId: 1 },
    );
    const emd = await waitJob(real, em.jobId, 1, 120_000);
    python.push({ action: "engine-identity-mismatch", state: emd.state, error: emd.final?.error || emd.error, ok: emd.final?.ok });
    assert(emd.final && emd.final.ok === false, `engine mismatch ${JSON.stringify(emd.final).slice(0, 300)}`);
  }
} else {
  python.push({ skipped: true, reason: "sessions_py_missing", ready: fs.existsSync(backendReadyPath(repoRoot)) });
}

{
  const gate = createTransportGate();
  const connFile = path.join(repoRoot, ".local", "cursor-coordination", "obd-connection-20260927", "scratch", "conn-state.json");
  try {
    fs.unlinkSync(connFile);
  } catch {
    /* */
  }
  const conn = createObdConnectionManager({
    repoRoot,
    gate,
    env: { ...process.env, PORSCHE981_CONNECTION_STATE: connFile },
    listFn: async () => ({
      ok: true,
      devices: [
        {
          id: "bt:0425E85BD4CB",
          brand: "vLinker",
          name: "vLinker FS 11436",
          comPort: "COM9",
          available: true,
          paired: true,
        },
      ],
      errors: [],
    }),
    probeFn: async () => ({ ok: true, volts: 12.6, voltageSource: "atrv" }),
  });
  const listed = await conn.handle({ action: "list" });
  assert(listed.devices?.length === 1 && listed.voltageVolts == null, "list no voltage");
  const sel = await conn.handle({ action: "select", deviceId: "bt:0425E85BD4CB" });
  assert(sel.ok && sel.selectedDeviceId === "bt:0425E85BD4CB", "select");
  let routed = "";
  const noDev = createReadOnlySessionManager({
    repoRoot,
    allowInjectedLive: true,
    env: envAllowInjectedLive,
    gate,
    getLiveDeviceId: () => conn.selectedId(),
    spawnFn: () => {
      const c = mockChild({
        lines: [
          JSON.stringify({
            type: "result",
            ok: true,
            mode: "live",
            runId: "liverunidxxxxxxxx",
            profileId: DME,
            status: "completed",
            error: null,
            results: [],
            simulation: false,
            liveVerified: false,
            writePayload: null,
          }),
        ],
      });
      c.stdin.write = (chunk) => {
        routed += String(chunk);
        return true;
      };
      return c;
    },
  });
  const liveSel = await noDev.handle(
    { action: "start", profileId: DME, mode: "live", confirmedReadOnly: true, x431Inactive: true },
    { ownerId: 1 },
  );
  assert(liveSel.ok, "live with selected id");
  await waitJob(noDev, liveSel.jobId, 1);
  const routedDoc = JSON.parse(routed.trim().split("\n")[0]);
  assert(routedDoc.deviceId === "bt:0425E85BD4CB" && !("port" in routedDoc), `routed ${routed}`);
  await conn.handle({ action: "clear" });
  const missing = createReadOnlySessionManager({
    repoRoot,
    allowInjectedLive: true,
    env: envAllowInjectedLive,
    getLiveDeviceId: () => conn.selectedId(),
    spawnFn: () => mockChild({ lines: [] }),
  });
  assert(
    (await missing.handle({ action: "start", profileId: DME, mode: "live", confirmedReadOnly: true, x431Inactive: true }, { ownerId: 1 }))
      .error === "device_not_selected",
    "live without selection",
  );
  const sim = createReadOnlySessionManager({
    repoRoot,
    spawnFn: stubSpawnFn([{ type: "plan", ok: true, plan: { profileId: DME } }]),
  });
  assert((await sim.handle({ action: "prepare", profileId: DME, mode: "simulation" }, { ownerId: 1 })).ok, "sim no hw");
  assert(parseVoltageVolts("12.6V") === 12.6 && parseVoltageVolts("0V") == null, "js voltage");
  const g2 = createTransportGate();
  assert(g2.tryAcquire("probe") && !g2.tryAcquire("session"), "gate exclusive");
  g2.release("probe");
  const busyConn = createObdConnectionManager({
    repoRoot,
    gate: g2,
    env: { ...process.env, PORSCHE981_CONNECTION_STATE: connFile + ".2" },
    listFn: async () => ({
      ok: true,
      devices: [
        { id: "bt:0425E85BD4CB", brand: "vLinker", name: "vLinker", comPort: "COM9", available: true, paired: true },
      ],
      errors: [],
    }),
    monitorSpawnFn: () => {
      const c = mockChild({ hang: true, emitCloseOnKill: true });
      queueMicrotask(() => {
        c.stdout.emit(
          "data",
          Buffer.from(JSON.stringify({ ok: true, type: "handshake", volts: 12.4, voltageSource: "atrv", simulation: false, deviceId: "bt:0425E85BD4CB", at: Date.now() }) + "\n"),
        );
      });
      return c;
    },
  });
  await busyConn.handle({ action: "list" });
  await busyConn.handle({ action: "select", deviceId: "bt:0425E85BD4CB" });
  g2.tryAcquire("session");
  const blocked = await busyConn.handle({ action: "connect", deviceId: "bt:0425E85BD4CB" });
  assert(blocked.error === "busy", `probe vs session ${blocked.error}`);
  g2.release("session");
  const fakeSim = await busyConn.handle({ action: "connect", deviceId: "bt:0425E85BD4CB" });
  assert(fakeSim.ok, "connect ok");
  await new Promise((r) => setTimeout(r, 20));
  assert(fakeSim.voltageSource === "atrv" || busyConn.voltageView().voltageVolts === 12.4, "connect volts");
  assert(busyConn.voltageView().voltageVolts === 12.4, "handshake volts");
  busyConn.noteLiveAtrv({ atrv: "11.0V" }, "simulation");
  assert(busyConn.voltageView().voltageVolts === 12.4, "sim atrv ignored");
  await busyConn.handle({ action: "disconnect" });
  assert(busyConn.voltageView().voltageVolts == null, "disconnect clears");
  conn.shutdown();
  busyConn.shutdown();
}

{
  const gate = createTransportGate();
  const connFile = path.join(repoRoot, ".local", "cursor-coordination", "obd-x431-cadence-20260927", "scratch", "handoff.json");
  const devices = [{ id: "bt:0425E85BD4CB", brand: "vLinker", available: true, paired: true, comPort: "COM9" }];
  let monitorAlive = false;
  let overlap = false;
  const conn = createObdConnectionManager({
    repoRoot,
    gate,
    env: { PORSCHE981_CONNECTION_STATE: connFile },
    listFn: async () => ({ ok: true, devices, errors: [] }),
    monitorSpawnFn: () => {
      const c = mockChild({ hang: true });
      monitorAlive = true;
      const kill = c.kill;
      c.kill = () => {
        monitorAlive = false;
        kill();
      };
      queueMicrotask(() => {
        c.stdout.emit(
          "data",
          Buffer.from(JSON.stringify({ ok: true, type: "handshake", volts: 12.6, voltageSource: "atrv", simulation: false, deviceId: "bt:0425E85BD4CB", at: Date.now() }) + "\n"),
        );
      });
      return c;
    },
  });
  await conn.handle({ action: "list" });
  await conn.handle({ action: "select", deviceId: "bt:0425E85BD4CB" });
  await conn.handle({ action: "connect" });
  await new Promise((r) => setTimeout(r, 20));
  assert(conn.voltageView().voltageVolts === 12.6, "pre-session voltage");
  let sessChild;
  const sess = attachSessionHandoff(
    conn,
    createReadOnlySessionManager({
      repoRoot,
      gate,
      getLiveDeviceId: () => conn.selectedId(),
      onSessionEnded: () => {
        void conn.resumeAfterSession();
      },
      spawnFn: () => {
        if (monitorAlive) overlap = true;
        sessChild = mockChild({
          hangAfterLines: true,
          lines: [
            JSON.stringify({
              type: "result",
              ok: true,
              mode: "simulation",
              runId: "liverunidxxxxxxxx",
              profileId: DME,
              status: "completed",
              error: null,
              results: [],
              simulation: true,
              liveVerified: false,
              writePayload: null,
            }),
          ],
        });
        return sessChild;
      },
    }),
  );
  const first = await sess.handle({ action: "start", profileId: DME, mode: "simulation" }, { ownerId: 1 });
  assert(first.ok, "handoff start");
  const second = await sess.handle({ action: "start", profileId: DME, mode: "simulation" }, { ownerId: 1 });
  assert(second.error === "busy", `simultaneous ${second.error}`);
  assert((await conn.handle({ action: "select", deviceId: "bt:0425E85BD4CB" })).error === "busy", "select during job");
  sessChild.exitCode = 0;
  sessChild.emit("close", 0);
  await waitJob(sess, first.jobId, 1);
  assert(!overlap, "serial overlap");
  await new Promise((r) => setTimeout(r, 20));
  assert(conn._state().wantConnected === true, "intent stays");
  const rejected = await sess.handle({ action: "start", profileId: DME, mode: "live", confirmedReadOnly: true, x431Inactive: true }, { ownerId: 1 });
  void rejected;
  await conn.shutdown();
}

{
  const gate = createTransportGate();
  const connFile = path.join(repoRoot, ".local", "cursor-coordination", "obd-x431-cadence-20260927", "scratch", "handoff-delay.json");
  let monitor;
  const conn = createObdConnectionManager({
    repoRoot,
    gate,
    gracefulMs: 20,
    killWaitMs: 20,
    env: { PORSCHE981_CONNECTION_STATE: connFile },
    listFn: async () => ({
      ok: true,
      devices: [{ id: "bt:0425E85BD4CB", brand: "vLinker", available: true, paired: true, comPort: "COM9" }],
      errors: [],
    }),
    monitorSpawnFn: () => {
      monitor = mockChild({ hang: true, emitCloseOnKill: false });
      monitor.kill = () => {
        monitor.killed = true;
        return false;
      };
      queueMicrotask(() => {
        monitor.stdout.emit(
          "data",
          Buffer.from(
            JSON.stringify({
              ok: true,
              type: "handshake",
              volts: 12.6,
              voltageSource: "atrv",
              simulation: false,
              deviceId: "bt:0425E85BD4CB",
              at: Date.now(),
            }) + "\n",
          ),
        );
      });
      return monitor;
    },
  });
  await conn.handle({ action: "list" });
  await conn.handle({ action: "select", deviceId: "bt:0425E85BD4CB" });
  await conn.handle({ action: "connect" });
  await new Promise((r) => setTimeout(r, 20));
  const sess = attachSessionHandoff(
    conn,
    createReadOnlySessionManager({
      repoRoot,
      gate,
      spawnFn: () => {
        throw new Error("prepare must wait for monitor close");
      },
    }),
  );
  const first = sess.handle({ action: "prepare", profileId: DME, mode: "simulation" }, { ownerId: 1 });
  const second = await sess.handle({ action: "prepare", profileId: DME, mode: "simulation" }, { ownerId: 1 });
  assert(second.error === "busy", `concurrent prepare ${second.error}`);
  const failed = await first;
  assert(failed.error === "monitor_close_timeout", `delayed close ${failed.error}`);
  assert(String(gate.owner()).startsWith("monitor"), "lock until close");
  monitor.exitCode = 0;
  monitor.emit("close", 0);
  await conn.shutdown();
}

console.log("read-only-session.selfcheck: ok");
console.log(JSON.stringify({ python }, null, 2));
