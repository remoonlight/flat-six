import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";

// An external fixture can exercise the actual installed modules without adding
// files to that installation or opening any adapter.
const root = path.resolve(process.argv[2] || process.cwd());
const { createManufacturerStream } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/manufacturer-stream.mjs")));
const { createTransportGate } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")));
const request = { action: "start-read", profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
  parameterIds: ["a".repeat(64)], continuous: true, confirmedReadOnly: true, x431Inactive: true };
const source = { executionEnabled: true, liveVerified: false, writePayload: null,
  simulation: false, vehicleDataCollected: true, sourceKind: "independent-manufacturer-read" };
const evidence = { requestHex: "21A4", pduHex: "61A42A",
  pduSha256: createHash("sha256").update(Buffer.from("61A42A", "hex")).digest("hex"),
  ok: true, cycle: 1, elapsedMs: 1, connectionEpoch: 1, sourceKind: source.sourceKind,
  timestampSource: "host-response-observed", processedUtc: "2026-10-12T00:00:00+00:00" };
const { pduHex: _raw, ...valueEvidence } = evidence;
const cycle = { ...source, ok: true, cycle: 1, completedCycles: 1, transactions: [evidence],
  samples: [{ ...valueEvidence, parameterId: request.parameterIds[0], synthetic: false, value: 42, display: "42" }] };
const cases = [
  { name: "clean", code: 0, transport: { closed: true, closeErrors: [] }, safe: true },
  { name: "read-failed-but-closed", code: 1, ok: false, transport: { closed: true, closeErrors: [] }, safe: true },
  { name: "missing-report", code: 0, safe: false },
  { name: "not-closed", code: 0, transport: { closed: false, closeErrors: [] }, safe: false },
  { name: "cleanup-failed", code: 1, ok: false, transport: { closed: true, closeErrors: ["ATPC:timeout"] }, safe: false },
  { name: "port-close-failed", code: 1, ok: false, transport: { closed: true, closeErrors: ["close:EIO"] }, safe: false },
  { name: "poisoned-but-port-closed", code: 1, ok: false, transport: { closed: true, closeErrors: ["poisoned:disconnect"] }, safe: true, nativeSafe: false },
  { name: "non-string-error", code: 0, transport: { closed: true, closeErrors: [null] }, safe: false },
  { name: "missing-errors", code: 0, transport: { closed: true }, safe: false },
  { name: "abnormal-exit", code: 2, transport: { closed: true, closeErrors: [] }, safe: false },
  { name: "signal-exit", code: null, transport: { closed: true, closeErrors: [] }, safe: false },
  { name: "forced-kill", code: 0, abort: true, transport: { closed: true, closeErrors: [] }, safe: false },
];
let count = 0;
for (const deviceId of ["bt:001122334455", "usb:" + "a".repeat(64), "tcp:192.0.2.1:35000", "vnci:123"]) {
  for (const test of cases) {
    let worker, resumed;
    const gate = createTransportGate();
    const manager = createManufacturerStream({ repoRoot: root, env: {}, allowInjectedLive: true, gate,
      connection: { snapshot: () => ({ purpose: "diagnostic", model: deviceId.startsWith("vnci:") ? "VNCI" : "OBDKey" }),
        selectedId: () => deviceId, reserveDispatch: () => true, releaseDispatch() {},
        suspendForSession: async () => ({ ok: true }), resumeAfterSession: async result => { resumed = result; } },
      spawnFn() {
        worker = new EventEmitter();
        for (const name of ["stdin", "stdout", "stderr"]) worker[name] = new PassThrough();
        worker.kill = () => { worker.killed = true; };
        return worker;
      } });
    const start = await manager.handle(request, 1);
    assert.equal(start.ok, true, `${deviceId}:${test.name}`);
    assert.ok(worker, `${deviceId}:${test.name}: worker created`);
    worker.emit("spawn");
    worker.stdout.write(JSON.stringify({ event: "cycle", result: cycle }) + "\n");
    if (test.abort) worker.stdout.write("{}\n");
    else worker.stdout.write(JSON.stringify({ event: "finished", result: {
      executionEnabled: true, liveVerified: false, writePayload: null,
      simulation: false, vehicleDataCollected: true, sourceKind: "independent-manufacturer-read",
      ok: test.ok ?? true, completedCycles: 1, requestCount: 1, ...(test.transport ? { transport: test.transport } : {}),
    } }) + "\n");
    assert.equal(resumed, undefined, "a report/kill request never resumes before actual process close");
    assert.equal(manager.isIdle(), false);
    worker.emit("close", test.code);
    const safe = deviceId.startsWith("vnci:") ? test.nativeSafe ?? test.safe : test.safe;
    assert.equal(resumed?.nativeReleaseVerified, safe, `${deviceId}:${test.name}: release verdict`);
    const status = manager.handle({ action: "status", sessionId: start.sessionId }, 1);
    assert.equal(status.released, true);
    assert.equal(status.events[0].samples[0].value, 42, "close failure retains the completed value batch");
    if (!safe) assert.equal(status.error, "adapter_release_not_verified", `${deviceId}:${test.name}`);
    await manager.shutdown(); count++;
  }
}
// A failed preflight may report that no adapter factory was ever attempted.
// The report is useful only after actual exit and with no earlier worker activity.
let unopenedCases = 0;
const unopened = { ...source, executionEnabled: false, vehicleDataCollected: false,
  ok: false, error: "SYNTHETIC preparation missing", completedCycles: 0, requestCount: 0,
  transport: { openAttempted: false, closed: false, closeErrors: [] } };
const unopenedTests = [
  { name: "verified-no-open", safe: true },
  ...[undefined, true, null, 0, "false"].map(value => ({ name: "open-flag-" + String(value), transport: { openAttempted: value } })),
  ...[0, 2, null].map(code => ({ name: "exit-" + code, code })),
  { name: "success-result", result: { ok: true } },
  { name: "execution-enabled", result: { executionEnabled: true } },
  { name: "vehicle-data", result: { vehicleDataCollected: true } },
  { name: "request-issued", result: { requestCount: 1 } },
  { name: "request-unknown", result: { requestCount: undefined } },
  { name: "cycle-completed", result: { completedCycles: 1 } },
  { name: "cycle-unknown", result: { completedCycles: undefined } },
  { name: "contradictory-close", transport: { closed: true } },
  { name: "cleanup-error", transport: { closeErrors: ["close:EIO"] } },
  { name: "missing-errors", transport: { closeErrors: undefined } },
  { name: "earlier-pdu", event: "diagnostic-pdu", activity: { ...source, pdu: {
    requestHex: "21A4", pduHex: "61A42A", timestampUs: 1, timestampSource: "host-pdu-observed",
    duringCleanup: false, transportKind: "elm-iso-tp", observation: "reassembled-complete-pdu" } } },
  { name: "earlier-can", event: "can-frame", activity: { ...source, frame: {
    canId: 0x7e8, extended: false, dataHex: "0361A42A", timestampUs: 1, timestampSource: "host-chunk-arrival" } } },
  { name: "earlier-connection", event: "connection-state", activity: {
    executionEnabled: true, liveVerified: false, writePayload: null, simulation: false, sourceKind: source.sourceKind,
    sequence: 1, episode: 1, phase: "interrupted", attempt: 0, connectionEpoch: 1, elapsedMs: 0,
    observedUtc: "2026-10-12T00:00:00Z", reason: "transport-disconnected" } },
  { name: "earlier-cycle", event: "cycle", activity: cycle },
  { name: "invalid-frame", invalid: true },
  { name: "trailing-frame", trailing: true },
];
for (const deviceId of ["bt:001122334455", "usb:" + "a".repeat(64), "tcp:192.0.2.1:35000", "vnci:123"]) {
  for (const test of unopenedTests) {
    let worker, resumed;
    const gate = createTransportGate();
    const manager = createManufacturerStream({ repoRoot: root, env: {}, allowInjectedLive: true, gate,
      connection: { snapshot: () => ({ purpose: "diagnostic", model: deviceId.startsWith("vnci:") ? "VNCI" : "OBDKey" }),
        selectedId: () => deviceId, reserveDispatch: () => true, releaseDispatch() {},
        suspendForSession: async () => ({ ok: true }), resumeAfterSession: async value => { resumed = value; } },
      spawnFn() {
        worker = new EventEmitter();
        for (const name of ["stdin", "stdout", "stderr"]) worker[name] = new PassThrough();
        worker.kill = () => { worker.killed = true; }; return worker;
      } });
    const start = await manager.handle(request, 300);
    assert.equal(start.ok, true); worker.emit("spawn");
    if (test.event) worker.stdout.write(JSON.stringify({ event: test.event, result: test.activity }) + "\n");
    if (test.invalid) worker.stdout.write("{}\n");
    const result = { ...unopened, ...test.result, transport: { ...unopened.transport, ...test.transport } };
    worker.stdout.write(JSON.stringify({ event: "finished", result }) + "\n");
    if (test.trailing) worker.stdout.write("{}\n");
    assert.equal(resumed, undefined, `${deviceId}:${test.name}: wait for process close`);
    assert.equal(gate.owner(), "session");
    worker.emit("close", Object.hasOwn(test, "code") ? test.code : 1);
    const status = manager.handle({ action: "status", sessionId: start.sessionId }, 300);
    assert.equal(resumed?.nativeReleaseVerified, test.safe === true, `${deviceId}:${test.name}`);
    assert.equal(status.released, true); assert.equal(gate.owner(), null);
    if (test.safe) {
      assert.equal(status.error, null);
      assert.equal(status.result.ok, false, "handoff does not turn the failed acquisition into success");
      assert.equal(status.result.error, unopened.error); assert.equal(status.result.transport.closed, false);
    } else assert.equal(status.error, "adapter_release_not_verified");
    await manager.shutdown(); unopenedCases++;
  }
}
// The monotonic acquisition deadline survives early, late and stale callbacks.
let deadlineCases = 0;
for (const deviceId of [null, "bt:001122334455", "usb:" + "a".repeat(64), "tcp:192.0.2.1:35000", "vnci:123"]) {
  let clock = 1000, worker, resumed = 0;
  const timers = [], gate = createTransportGate();
  const live = deviceId !== null;
  const manager = createManufacturerStream({ repoRoot: root, env: {}, allowInjectedLive: true, gate,
    nowFn: () => clock,
    setTimeoutFn(fn, ms) { const timer = { fn, ms, cleared: false, unref() {} }; timers.push(timer); return timer; },
    clearTimeoutFn(timer) { if (timer) timer.cleared = true; },
    connection: { snapshot: () => ({ purpose: "diagnostic", model: deviceId?.startsWith("vnci:") ? "VNCI" : "OBDKey" }),
      selectedId: () => deviceId, reserveDispatch: () => true, releaseDispatch() {},
      suspendForSession: async () => ({ ok: true }), resumeAfterSession: async result => {
        assert.equal(result.nativeReleaseVerified, true); resumed++;
      } },
    spawnFn() {
      worker = new EventEmitter();
      for (const name of ["stdin", "stdout", "stderr"]) worker[name] = new PassThrough();
      worker.input = ""; worker.stdin.on("data", bytes => { worker.input += bytes; });
      worker.kill = () => { worker.killed = true; };
      return worker;
    } });
  const selection = live ? request : { action: "start", profileId: request.profileId, parameterIds: request.parameterIds, continuous: true };
  const start = await manager.handle(selection, 100);
  assert.equal(start.ok, true); worker.emit("spawn");
  const flags = { ...source, executionEnabled: live, simulation: !live, vehicleDataCollected: live,
    sourceKind: live ? source.sourceKind : "historical-fixture-rehearsal" };
  worker.stdout.write(JSON.stringify({ event: "cycle", result: { ...cycle, ...flags,
    transactions: cycle.transactions.map(t => ({ ...t, sourceKind: flags.sourceKind })),
    samples: cycle.samples.map(s => ({ ...s, sourceKind: flags.sourceKind, synthetic: !live })) } }) + "\n");
  const first = timers[0]; assert.equal(first.ms, 600000);
  clock = 600999.25; first.fn();
  let status = manager.handle({ action: "status", sessionId: start.sessionId }, 100);
  assert.equal(status.state, "running", `${deviceId}: an early callback cannot end the batch`);
  assert.equal(status.stopReason, null); assert.ok(!worker.input.includes('"action":"stop"'));
  const rearmed = timers.at(-1); assert.equal(rearmed.ms, 1);
  rearmed.fn(); assert.equal(timers.at(-1).ms, 1, "repeated early delivery retains the original deadline");
  const last = timers.at(-1);
  clock = 601000; last.fn();
  status = manager.handle({ action: "status", sessionId: start.sessionId }, 100);
  assert.equal(status.state, "stopping"); assert.equal(status.stopReason, "time-limit");
  assert.equal(status.released, false); assert.equal(status.events.length, 1);
  assert.equal(gate.owner(), live ? "session" : null, "deadline waits for the actual worker close");
  first.fn(); rearmed.fn(); last.fn();
  assert.equal((worker.input.match(/"action":"stop"/g) || []).length, 1);
  assert.equal(worker.killed, undefined);
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...flags, ok: false, error: "cancelled",
    completedCycles: 1, requestCount: 1, transport: { closed: true, closeErrors: [] } } }) + "\n");
  worker.emit("close", live ? 1 : 0);
  status = manager.handle({ action: "status", sessionId: start.sessionId }, 100);
  assert.equal(status.state, "finished"); assert.equal(status.released, true);
  assert.equal(status.events[0].samples[0].value, 42); assert.equal(gate.owner(), null);
  assert.equal(resumed, Number(live));
  clock += 100;
  const next = await manager.handle(selection, 100); worker.emit("spawn");
  const nextDeadline = timers.findLast(t => t.ms === 600000);
  assert.notEqual(next.sessionId, start.sessionId); assert.ok(nextDeadline);
  const timerCount = timers.length; first.fn(); rearmed.fn(); last.fn();
  assert.equal(timers.length, timerCount, "old callbacks cannot rearm after closure or alter the next batch");
  assert.equal(manager.handle({ action: "status", sessionId: next.sessionId }, 100).state, "running");
  await manager.handle({ action: "stop", sessionId: next.sessionId }, 100);
  const stoppedCount = timers.length; nextDeadline.fn();
  assert.equal(timers.length, stoppedCount, "manual stop cannot be undone by an early deadline callback");
  const stopped = manager.handle({ action: "status", sessionId: next.sessionId }, 100);
  assert.equal(stopped.stopReason, "user-stopped");
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...flags, vehicleDataCollected: false,
    ok: false, error: "cancelled", completedCycles: 0, requestCount: 0, transport: { closed: true, closeErrors: [] } } }) + "\n");
  worker.emit("close", live ? 1 : 0); await manager.shutdown(); deadlineCases++;
}
for (const deviceId of ["bt:001122334455", "usb:" + "a".repeat(64), "tcp:192.0.2.1:35000", "vnci:123"]) {
  let clock = 0, handoff, worker;
  const timers = [];
  const manager = createManufacturerStream({ repoRoot: root, env: {}, allowInjectedLive: true, gate: createTransportGate(),
    nowFn: () => clock,
    setTimeoutFn(fn, ms) { const timer = { fn, ms, unref() {} }; timers.push(timer); return timer; }, clearTimeoutFn() {},
    connection: { snapshot: () => ({ purpose: "diagnostic", model: deviceId.startsWith("vnci:") ? "VNCI" : "OBDKey" }),
      selectedId: () => deviceId, reserveDispatch: () => true, releaseDispatch() {},
      suspendForSession: () => new Promise(resolve => { handoff = resolve; }), resumeAfterSession: async () => {} },
    spawnFn() { worker = new EventEmitter(); for (const n of ["stdin", "stdout", "stderr"]) worker[n] = new PassThrough(); worker.kill = () => {}; return worker; } });
  const starting = manager.handle(request, 200);
  clock = 599999.25; timers[0].fn();
  assert.equal(worker, undefined, "handoff has not opened a worker");
  handoff({ ok: true }); const started = await starting;
  assert.equal(started.state, "running", `${deviceId}: early timer during handoff cannot cancel the new batch`);
  worker.emit("spawn"); clock = 600001;
  const status = manager.handle({ action: "status", sessionId: started.sessionId }, 200);
  assert.equal(status.state, "stopping"); assert.equal(status.stopReason, "time-limit");
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...source, vehicleDataCollected: false,
    ok: false, error: "cancelled", completedCycles: 0, requestCount: 0, transport: { closed: true, closeErrors: [] } } }) + "\n");
  worker.emit("close", 1); await manager.shutdown(); deadlineCases++;
}
console.log(JSON.stringify({ passed: true, checks: count, deadlineCases, unopenedCases, transportInjected: true, deviceIO: false, vehicleIO: false }));
