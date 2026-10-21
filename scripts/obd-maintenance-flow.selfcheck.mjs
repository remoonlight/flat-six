/** Real coordinator with explicitly synthetic source callbacks; no vehicle I/O. */
import assert from "node:assert/strict";
import path from "node:path";
import { pathToFileURL } from "node:url";
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const { createWorkshopExecution } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/piwis-workshop.mjs")));
const { createTransportGate } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")));
const delay = () => new Promise(resolve => setTimeout(resolve, 1));
async function until(executor, phase, owner = 101) {
  for (let n = 0; n < 1000; n++) { const state = await executor.handle({ action: "status" }, owner); if (state.job?.phase === phase) return state.job; await delay(); }
  throw new Error(`phase not reached: ${phase}`);
}
const deferred = () => { let resolve; return { promise: new Promise(done => { resolve = done; }), resolve: value => resolve(value) }; };
function setup(steps, extra = {}) {
  const gate = createTransportGate(), calls = [];
  let connected = true, reserved = false, selectedDeviceId = "private-model-only";
  const connection = {
    snapshot: () => ({ connected, purpose: "diagnostic", selectedDeviceId, model: "private-model" }),
    reserveDispatch: () => { if (reserved) return false; reserved = true; return true; },
    releaseDispatch: () => { reserved = false; },
    suspendForSession: async () => { calls.push("handoff"); connected = false; return { ok: true }; },
    resumeAfterSession: async () => { calls.push("resume-connection"); connected = true; reserved = false; return { ok: true }; },
  };
  const recipe = { id: "synthetic-maintenance", title: "维护流程（测试定义）", generations: ["981"], sourceQualified: true,
    sourceHash: "a".repeat(64), steps,
    open: async () => { calls.push("open"); return { qualified: true, context: { privateFixture: true } }; },
    preflight: async () => { calls.push("preflight"); return { ok: true, resumeIdentity: "1".repeat(64) }; },
    close: async () => { calls.push("close"); return { released: true }; }, ...extra };
  const executor = createWorkshopExecution({ connection, gate, getRecipe: () => recipe });
  return { executor, calls, gate, recipe, reserved: () => reserved, selectDevice: id => { selectedDeviceId = id; } };
}
const software = (id, execute = async () => ({ ok: true }), extra = {}) => ({ id, label: id, kind: "software", safeStopBefore: false, safeStopAfter: false, execute, ...extra });
const manual = id => ({ id, label: id, kind: "manual", safeStopBefore: false, safeStopAfter: false });
const start = executor => executor.handle({ action: "start", functionId: "synthetic-maintenance", generation: "981" }, 101);
const checks = [];
{
  const executor = createWorkshopExecution();
  assert.equal((await executor.handle({ action: "capability", functionId: "unknown", generation: "981" }, 101)).ready, false);
  assert.equal((await start(executor)).error, "workshop-recipe-unavailable");
  assert.equal(executor.isIdle(), true);
  assert.equal((await executor.handle({ action: "start", functionId: "unknown", generation: "981", steps: [] }, 101)).error, "workshop-request-invalid");
  const { executor: wrongGeneration, calls } = setup([software("不得执行")]);
  assert.equal((await wrongGeneration.handle({ action: "start", functionId: "synthetic-maintenance", generation: "982" }, 101)).error, "workshop-recipe-invalid");
  assert.deepEqual(calls, []);
  const { executor: persistence, recipe } = setup([manual("人工操作")], { id: "psm-roller", allowedPersistence: ["ignition-cycle"] });
  assert.equal((await persistence.handle({ action: "capability", functionId: recipe.id, generation: "981" }, 101)).error, "workshop-request-invalid");
  assert.equal((await persistence.handle({ action: "capability", functionId: recipe.id, generation: "981", persistence: "persistent" }, 101)).ready, false);
  assert.equal((await persistence.handle({ action: "capability", functionId: recipe.id, generation: "981", persistence: "ignition-cycle" }, 101)).ready, true);
  checks.push("no production recipe, no dispatch; renderer payload/step injection rejected");
}
{
  const performed = [], { executor, gate, reserved } = setup([
    software("自动检查", async () => { performed.push("first"); return { ok: true }; }), manual("人工操作一"), manual("人工操作二"),
    software("软件核对", async () => { performed.push("last"); return { ok: true }; }),
  ]);
  const started = await start(executor);
  const waiting = await until(executor, "waiting-manual");
  assert.deepEqual(performed, ["first"]); assert.equal(waiting.currentIndex, 1);
  assert.equal(gate.owner(), "workshop-execution"); assert.equal(reserved(), true);
  assert.equal(executor.isOwnedActive(101), true); assert.equal(executor.isOwnedActive(102), false);
  assert.equal((await start(executor)).error, "vehicle-busy");
  assert.equal((await executor.handle({ action: "cancel", jobId: started.job.id }, 102)).error, "workshop-owner-mismatch");
  assert.equal((await executor.handle({ action: "status" }, 102)).job, null);
  assert.equal((await executor.handle({ action: "continue", jobId: "old", stepId: "人工操作一" }, 101)).error, "workshop-job-unknown");
  await executor.handle({ action: "continue", jobId: started.job.id, stepId: "人工操作一" }, 101);
  const next = await until(executor, "waiting-manual"); assert.equal(next.currentIndex, 2);
  assert.equal((await executor.handle({ action: "continue", jobId: started.job.id, stepId: "人工操作一" }, 101)).error, "workshop-not-waiting-manual", "double/stale clicks cannot acknowledge the next manual step");
  await executor.handle({ action: "continue", jobId: started.job.id, stepId: "人工操作二" }, 101);
  const done = await until(executor, "completed");
  assert.deepEqual(performed, ["first", "last"]); assert.ok(done.steps.every(step => step.status === "completed"));
  assert.equal(done.released, true); assert.equal(gate.owner(), null); assert.equal(reserved(), false);
  assert.equal(executor.isOwnedActive(101), false);
  assert.equal((await executor.handle({ action: "dismiss", jobId: started.job.id }, 101)).job, null);
  checks.push("software advances; manual steps require owner/job/step-bound confirmation; one vehicle task; result waits for explicit return");
}
for (const canStop of [true, false]) {
  const held = deferred(), performed = [];
  const { executor, gate } = setup([
    software("等待软件完成", async () => { performed.push("first"); await held.promise; return { ok: true }; }, { safeStopAfter: canStop }),
    software("后续动作", async () => { performed.push("later"); return { ok: true }; }),
  ], canStop ? { stop: async () => { performed.push("safe-stop"); return { ok: true }; } } : {});
  const started = await start(executor); await until(executor, "running");
  await executor.handle({ action: "cancel", jobId: started.job.id }, 101);
  assert.equal(executor.snapshot().job.cancelRequested, true); assert.equal(executor.isIdle(), false);
  assert.equal(gate.owner(), "workshop-execution"); assert.deepEqual(performed, ["first"]);
  held.resolve();
  const done = await until(executor, canStop ? "cancelled" : "completed");
  assert.deepEqual(performed, canStop ? ["first", "safe-stop"] : ["first", "later"]);
  assert.equal(done.cancelNotApplied, !canStop); assert.equal(gate.owner(), null);
}
checks.push("cancel request waits for the declared safe boundary; no safe boundary completes the job and reports no early stop");
{
  const held = deferred(), events = [];
  const { executor } = setup([{ ...manual("人工核对"), safeStopAfter: true, verify: async () => { events.push("verify"); await held.promise; return { ok: true }; } }, software("后续")],
    { stop: async () => { events.push("stop"); return { ok: true }; } });
  const started = await start(executor); await until(executor, "waiting-manual");
  await executor.handle({ action: "continue", jobId: started.job.id, stepId: "人工核对" }, 101);
  await until(executor, "running");
  await executor.handle({ action: "cancel", jobId: started.job.id }, 101);
  assert.deepEqual(events, ["verify"]); assert.equal(executor.isIdle(), false);
  held.resolve(); const done = await until(executor, "cancelled");
  assert.deepEqual(events, ["verify", "stop"]); assert.deepEqual(done.steps.map(step => step.status), ["completed", "pending"]);
}
{
  let attempts = 0, canResume = false;
  const { executor } = setup([software("已完成"), software("中断步骤", async (_ctx, options) => {
    attempts++; return options.resuming ? { ok: true } : { ok: false, cause: "测试条件不满足", remedy: "恢复测试条件后核对。" };
  }), software("未执行")], { resumeSupported: true, checkResume: async () => ({ ok: canResume, resumeIdentity: "1".repeat(64), cause: "继续条件尚未满足", remedy: "先恢复车辆条件。" }) });
  const started = await start(executor), failed = await until(executor, "failed");
  assert.deepEqual(failed.steps.map(step => step.status), ["completed", "failed", "pending"]);
  assert.equal(attempts, 1);
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101);
  const blocked = await until(executor, "failed"); assert.equal(attempts, 1);
  assert.equal(blocked.failure.cause, failed.failure.cause, "a rejected resume check preserves the original failed result");
  assert.equal(blocked.resumeFailure.cause, "继续条件尚未满足");
  canResume = true; await executor.handle({ action: "check-resume", jobId: started.job.id }, 101);
  await until(executor, "resume-ready"); assert.equal(attempts, 1, "passing checks never resumes automatically");
  canResume = false; await executor.handle({ action: "resume", jobId: started.job.id }, 101);
  await until(executor, "failed"); assert.equal(attempts, 1, "conditions are checked again before the explicit continuation");
  canResume = true; await executor.handle({ action: "check-resume", jobId: started.job.id }, 101); await until(executor, "resume-ready");
  await executor.handle({ action: "resume", jobId: started.job.id }, 101);
  const done = await until(executor, "completed"); assert.equal(attempts, 2); assert.ok(done.steps.every(step => step.status === "completed"));
  checks.push("failed result preserves completed/failed/unexecuted steps; supported resume checks conditions, waits for user and rechecks before action");
}
{
  let identity = "1".repeat(64), attempts = 0;
  const { executor, recipe, gate, selectDevice, calls } = setup([
    software("已完成"), software("中断步骤", async (_ctx, options) => { attempts++; return { ok: options.resuming }; }), software("未执行"),
  ], { resumeSupported: true, checkResume: async () => ({ ok: true, resumeIdentity: identity }) });
  const started = await start(executor), failed = await until(executor, "failed");
  identity = "2".repeat(64);
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101);
  const changed = await until(executor, "failed");
  assert.equal(attempts, 1); assert.deepEqual(changed.steps, failed.steps); assert.deepEqual(changed.failure, failed.failure);
  assert.match(changed.resumeFailure.cause, /身份|不一致/); assert.equal(gate.owner(), null);
  assert.doesNotMatch(JSON.stringify(changed), /22222222|resumeIdentity/);
  identity = "1".repeat(64);
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101); await until(executor, "resume-ready");
  identity = "3".repeat(64);
  await executor.handle({ action: "resume", jobId: started.job.id }, 101); await until(executor, "failed");
  assert.equal(attempts, 1, "a target change after the successful check is detected again before action");
  identity = "1".repeat(64);
  const opens = calls.filter(value => value === "open").length;
  recipe.sourceHash = "b".repeat(64);
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101);
  const sourceChanged = await until(executor, "failed");
  assert.match(sourceChanged.resumeFailure.cause, /作业已变化/); assert.equal(calls.filter(value => value === "open").length, opens);
  recipe.sourceHash = "a".repeat(64);
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101); await until(executor, "resume-ready");
  recipe.sourceHash = "c".repeat(64);
  await executor.handle({ action: "resume", jobId: started.job.id }, 101); await until(executor, "failed");
  assert.equal(attempts, 1, "a source change after review is also rejected");
  recipe.sourceHash = "a".repeat(64);
  // A newly qualified head can continue the same vehicle/unit identity. The
  // coordinator does not invent an original-head-only business restriction.
  selectDevice("another-qualified-model-head");
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101); await until(executor, "resume-ready");
  await executor.handle({ action: "resume", jobId: started.job.id }, 101); await until(executor, "completed");
  assert.equal(attempts, 2);
  checks.push("resume binds the original vehicle/units and source at check and execution; completed results survive conflicts; a qualified replacement head is permitted");
}
{
  for (const identity of [undefined, null, "", "1".repeat(63), "A".repeat(64), { toString: () => "1".repeat(64) }]) {
    let actions = 0;
    const { executor, gate, calls } = setup([software("不得执行", async () => { actions++; return { ok: true }; })], {
      resumeSupported: true, preflight: async () => ({ ok: true, resumeIdentity: identity }),
      checkResume: async () => ({ ok: true, resumeIdentity: "1".repeat(64) }),
    });
    await start(executor); const failed = await until(executor, "failed");
    assert.equal(actions, 0); assert.equal(failed.resumeSupported, false); assert.equal(failed.released, true);
    assert.equal(gate.owner(), null); assert.ok(calls.includes("close")); assert.match(failed.failure.cause, /完整身份/);
  }
  checks.push("a resumable recipe without a valid source-owned initial identity cannot execute; source/identity fingerprints stay private");
}
{
  const held = deferred(); let checksDone = 0, attempts = 0;
  const { executor, recipe } = setup([software("中断步骤", async (_ctx, options) => { attempts++; return { ok: options.resuming }; })], {
    resumeSupported: true, checkResume: async () => {
      if (++checksDone === 2) await held.promise;
      return { ok: true, resumeIdentity: "1".repeat(64) };
    },
  });
  const started = await start(executor); await until(executor, "failed");
  await executor.handle({ action: "check-resume", jobId: started.job.id }, 101); await until(executor, "resume-ready");
  await executor.handle({ action: "resume", jobId: started.job.id }, 101);
  for (let n = 0; n < 1000 && checksDone !== 2; n++) await delay();
  assert.equal(checksDone, 2); recipe.sourceHash = "b".repeat(64); held.resolve();
  const failed = await until(executor, "failed");
  assert.equal(attempts, 1); assert.match(failed.resumeFailure.cause, /作业已变化/);
  checks.push("source changes while asynchronous resume checks run are rejected before any continued action");
}
{
  let released = false;
  const { executor, gate } = setup([software("执行")], { close: async () => ({ released: false }), observeRelease: async () => released });
  const started = await start(executor); await until(executor, "release-pending");
  assert.equal(executor.isIdle(), false); assert.equal(gate.owner(), "workshop-execution");
  assert.equal((await executor.handle({ action: "dismiss", jobId: started.job.id }, 101)).error, "vehicle-busy");
  released = true; const done = await until(executor, "completed"); assert.equal(done.released, true); assert.equal(gate.owner(), null);
  checks.push("completed software does not release the vehicle lease until physical client release is independently confirmed");
}
{
  const { executor } = setup([software("执行", async () => { throw new Error("PRIVATE/path/driver-stack"); })]);
  await start(executor); const failed = await until(executor, "failed");
  assert.equal(failed.steps[0].status, "unconfirmed"); assert.doesNotMatch(JSON.stringify(failed), /PRIVATE|driver-stack/);
  assert.equal(failed.resumeSupported, false);
  checks.push("unknown outcomes stay unconfirmed; private errors are retained internally and not shown in daily results");
}
console.log(JSON.stringify({ passed: true, checks, sourceCallbacksSynthetic: true, productionRecipeProvided: false, deviceIO: false, vehicleIO: false }));
