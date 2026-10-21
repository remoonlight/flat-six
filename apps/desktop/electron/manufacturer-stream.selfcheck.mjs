import { validConnectionEvent } from "./manufacturer-connection.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createManufacturerStream } from "./manufacturer-stream.mjs";
import fs from "node:fs/promises";
import path from "node:path";
import { createTransportGate } from "./obd-connection.mjs";
import { saveManufacturerRecording } from "./recording-files.mjs";
import { createPlotClock } from "./capture-clock.mjs";

const FLAGS = { executionEnabled: false, liveVerified: false, writePayload: null };
const SOURCE = { ...FLAGS, simulation: true, vehicleDataCollected: false, sourceKind: "historical-fixture-rehearsal" };
const children = [];
function spawnFn(exe, args, options) {
  assert.equal(options.shell, false); assert.equal(options.windowsHide, true);
  assert.equal(args.at(-1), "scripts.diagnostics.manufacturer_stream");
  const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.input = ""; child.stdin.on("data", b => { child.input += b.toString(); });
  child.kill = () => { child.killed = true; };
  children.push(child); return child;
}
const manager = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, stopTimeoutMs: 10, timeoutMs: 1000 });
const selection = { action: "start", profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", parameterIds: ["a".repeat(64)], continuous: true };
for (const extra of [{ serialPort: "COM1" }, { mode: "live" }, { path: "private" }, { continuous: 1 }])
  assert.equal(manager.handle({ ...selection, ...extra }, 1).ok, false);
assert.equal(children.length, 0);
const started = manager.handle(selection, 1), child = children[0]; child.emit("spawn");
assert.equal(manager.handle(selection, 1).error, "stream_busy");
assert.equal(manager.handle({ action: "stop", sessionId: started.sessionId }, 2).error, "unknown_session");
const evidence = (requestHex, pduHex, cycle = 1, elapsedMs = 0, connectionEpoch = 1) => ({ requestHex, pduHex,
  pduSha256: createHash("sha256").update(Buffer.from(pduHex, "hex")).digest("hex"), cycle, elapsedMs, connectionEpoch,
  sourceKind: SOURCE.sourceKind, timestampSource: "host-response-observed", processedUtc: "2026-10-11T00:00:00+00:00", ok: true });
const valueEvidence = (...args) => { const { pduHex: _raw, ...fields } = evidence(...args); return fields; };

const tx = evidence("21A4", "61A42A");
const event = { ...SOURCE, ok: true, cycle: 1, completedCycles: 1, transactions: [tx],
  samples: [{ ...valueEvidence("21A4", "61A42A"), parameterId: selection.parameterIds[0], cycle: 1, synthetic: true, elapsedMs: 0, display: "演练" }] };
const frame = Buffer.from(JSON.stringify({ event: "cycle", result: event }) + "\n");
{
  let now = 0;
  const plot = createPlotClock({ now: () => now });
  now = 5000; assert.equal(plot.read(), null, "no elapsed data is invented before the first observation");
  plot.observe(100); now = 6000; assert.equal(plot.read(), 1100);
  plot.observe(50); assert.equal(plot.read(), 1100, "late samples cannot move the window backwards");
  plot.freeze(); now = 9000; assert.equal(plot.read(), 1100);
  plot.resume(); assert.equal(plot.read(), 4100, "a same-batch retry includes time spent waiting");
  plot.freeze(); now = 15000; assert.equal(plot.read(), 4100);
}
const split = frame.indexOf(Buffer.from("演练")) + 1;
child.stdout.write(frame.subarray(0, split)); child.stdout.write(frame.subarray(split));
let state = manager.handle({ action: "status", sessionId: started.sessionId }, 1);
assert.equal(state.events.length, 1); assert.equal(state.events[0].samples[0].display, "演练");
assert.equal(manager.handle({ action: "status", sessionId: started.sessionId, cursor: 1 }, 1).events.length, 0);
assert.equal(manager.handle({ action: "status", sessionId: started.sessionId, cursor: 2 }, 1).error, "invalid_cursor");
manager.handle({ action: "stop", sessionId: started.sessionId }, 1);
assert.match(child.input, /"action":"stop"/);
child.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: false, error: "cancelled", completedCycles: 1, requestCount: 1 } }) + "\n");
child.emit("close", 0);
state = manager.handle({ action: "status", sessionId: started.sessionId, cursor: 1 }, 1);
assert.equal(state.state, "finished"); assert.equal(state.result.error, "cancelled");
assert.equal(state.events.length, 0);
const second = manager.handle(selection, 1), bad = children[1]; bad.emit("spawn");
bad.stdout.write(frame);
bad.stdout.write(JSON.stringify({ event: "cycle", result: { ...event, cycle: 2, liveVerified: true } }) + "\n");
state = manager.handle({ action: "status", sessionId: second.sessionId }, 1);
assert.equal(state.error, "stream_source_invalid"); assert.equal(state.events.length, 1); assert.equal(bad.killed, true);
assert.equal(state.state, "closing", "aborting an offline worker does not finish before actual close");
assert.equal(state.released, false);
assert.equal(manager.handle(selection, 1).error, "stream_busy", "aborted worker must close before new start");
bad.emit("close", 1);
const third = manager.handle(selection, 1), stuck = children[2]; stuck.emit("spawn"); stuck.stdout.write(frame);
manager.handle({ action: "stop", sessionId: third.sessionId }, 1);
await new Promise(resolve => setTimeout(resolve, 20));
state = manager.handle({ action: "status", sessionId: third.sessionId }, 1);
assert.equal(state.error, "stream_stop_timeout"); assert.equal(state.events.length, 1); assert.equal(stuck.killed, true);
assert.equal(state.state, "closing", "stop timeout still waits for actual process close");
assert.equal(state.released, false);

assert.equal(manager.handle(selection, 1).error, "stream_busy", "timed out worker still holds lease until close");
stuck.emit("close", 1);
const fourth = manager.handle(selection, 1), mismatch = children[3]; mismatch.emit("spawn"); mismatch.stdout.write(frame);
mismatch.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: true, completedCycles: 0, requestCount: 1 } }) + "\n");
assert.equal(manager.handle({ action: "status", sessionId: fourth.sessionId }, 1).error, "stream_summary_mismatch");
mismatch.emit("close", 1);

let missingAttempts = 0;
const missing = createManufacturerStream({ repoRoot: process.cwd(), env: { PORSCHE981_PYTHON: "/missing/pinned" },
  spawnFn() {
    missingAttempts += 1;
    const child = spawnFn(...arguments);
    queueMicrotask(() => { const error = new Error("not found"); error.code = "ENOENT"; child.emit("error", error); child.emit("close", 1); });
    return child;
  } });
const absent = missing.handle(selection, 3);
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(missing.handle({ action: "status", sessionId: absent.sessionId }, 3).error, "python_runtime_missing");
assert.equal(missingAttempts, 1, "pinned interpreter never falls back to a different runtime");
assert.equal(missing.handle(selection, 3).ok, true, "missing process does not retain a busy lease");
await new Promise(resolve => setTimeout(resolve, 0));
console.log("manufacturer-stream selfcheck passed: ownership, UTF8 framing, incremental results, stop, lease release, summary agreement, pinned runtime");

const closing = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, closeWaitMs: 5, stopTimeoutMs: 5, timeoutMs: 1000 });
const closingJob = closing.handle(selection, 44), delayed = children.at(-1); delayed.emit("spawn");
delayed.stdout.write(frame);
delayed.stdout.write(JSON.stringify({ event: "cycle", result: { ...event, cycle: 2, liveVerified: true } }) + "\n");
assert.equal(closing.isIdle(), false);
assert.equal((await closing.shutdown()).error, "port_release_pending", "shutdown cannot claim closed after a kill request");
const beforeClose = closing.handle({ action: "status", sessionId: closingJob.sessionId }, 44);
assert.equal(beforeClose.error, "stream_source_invalid", "quit preserves the actual job failure while release is pending");
delayed.emit("close", 1);
assert.equal(closing.isIdle(), true);
assert.equal((await closing.shutdown()).ok, true);
console.log("manufacturer-stream release selfcheck passed: offline abort/timeout stay closing, partial results retained, shutdown waits for actual close");

{
  const clocked = createManufacturerStream({ repoRoot: process.cwd(), spawnFn });
  const start = clocked.handle(selection, 45), worker = children.at(-1); worker.emit("spawn");
  const wall = Date.now;
  try {
    for (const delta of [600001, -600001]) {
      Date.now = () => wall() + delta;
      assert.equal(clocked.handle({ action: "status", sessionId: start.sessionId }, 45).state, "running",
        "system wall-clock changes do not expire or reset the monotonic batch clock");
    }
  } finally { Date.now = wall; }
  worker.stdout.write(frame);
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: true, completedCycles: 1, requestCount: 1 } }) + "\n");
  worker.emit("close", 0); await clocked.shutdown();
}

// Explicitly injected live-shaped frames exercise the physical branch's contract.
// No native/serial/TCP factory is used; saved test files carry synthetic provenance.
for (const deviceId of ["usb:" + "a".repeat(64), "tcp:192.168.0.10:35000"]) {
  const output = path.resolve(process.env.PORSCHE981_SELFCHECK_OUTPUT || ".local/selfchecks/manufacturer-stream");
  await fs.mkdir(output, { recursive: true });
  const directory = await fs.mkdtemp(path.join(output, "run-"));
  const file = path.join(directory, "SYNTHETIC-manufacturer-complete.json");
  let worker, mode = "cancel", choose, reserved = 0, resumed = 0;
  const native = createManufacturerStream({ repoRoot: process.cwd(), env: {}, allowInjectedLive: true,
    gate: createTransportGate(), connection: {
      snapshot: () => ({ purpose: "diagnostic", model: "OBDKey", serialBaud: deviceId.startsWith("usb:") ? 230400 : null }), selectedId: () => deviceId,
      reserveDispatch() { reserved++; return true; }, releaseDispatch() {},
      async suspendForSession() { return { ok: true }; }, async resumeAfterSession() { resumed++; },
    }, spawnFn(_exe, args) {
      assert.equal(args.at(-1), "scripts.diagnostics.manufacturer_read");
      worker = new EventEmitter(); worker.stdin = new PassThrough(); worker.stdout = new PassThrough(); worker.stderr = new PassThrough();
      worker.input = ""; worker.stdin.on("data", value => { worker.input += value; }); worker.kill = () => { worker.killed = true; };
      return worker;
    }, saveRecording: recording => saveManufacturerRecording({ ...recording, syntheticAcceptance: true, vehicleIO: false }, {
      dialog: { showSaveDialog: () => mode === "cancel" ? new Promise(resolve => { choose = resolve; })
        : Promise.resolve({ canceled: false, filePath: file }) },
      openFile: (...args) => { if (mode === "failure") throw Object.assign(new Error(), { code: "EIO" }); return fs.open(...args); },
    }) });
  const ids = ["a".repeat(64), "b".repeat(64)];
  const request = { ...selection, action: "start-read", parameterIds: ids, confirmedReadOnly: true, x431Inactive: true };
  const start = await native.handle(request, 91); worker.emit("spawn");
  assert.equal(JSON.parse(worker.input.split("\n")[0]).cycleCount, null, "continuous physical reads end by elapsed time, not 1000 rounds");
  assert.equal(JSON.parse(worker.input.split("\n")[0]).adapterModel, "OBDKey");
  assert.equal(JSON.parse(worker.input.split("\n")[0]).deviceId, deviceId);
  assert.equal(JSON.parse(worker.input.split("\n")[0]).serialBaud, deviceId.startsWith("usb:") ? 230400 : undefined);
  for (let cycle = 1; cycle <= 3201; cycle++) {
    const doc = { ...SOURCE, executionEnabled: true, simulation: false, vehicleDataCollected: true,
      sourceKind: "independent-manufacturer-read", ok: true, completedCycles: 1, cycle,
      syntheticEvidence: "x".repeat(2500), transactions: [{ ...evidence("22F100", "62F1002A07", cycle, cycle * 100), sourceKind: "independent-manufacturer-read" }],
      samples: ids.map((parameterId, i) => ({ ...valueEvidence("22F100", "62F1002A07", cycle, cycle * 100), sourceKind: "independent-manufacturer-read", parameterId, cycle, synthetic: false, elapsedMs: cycle * 100,
        value: (cycle - 1) * 2 + i, display: String((cycle - 1) * 2 + i) })) };
    worker.stdout.write(JSON.stringify({ event: "cycle", result: doc }) + "\n");
  }
  const save = { action: "save", sessionId: start.sessionId };
  assert.equal((await native.handle(save, 91)).error, "stream_busy", "save waits for actual close");
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, executionEnabled: true,
    simulation: false, vehicleDataCollected: true, sourceKind: "independent-manufacturer-read", ok: true,
    completedCycles: 3201, requestCount: 3201, transport: { closed: true, closeErrors: [], rawLog: ["raw-private-test-bytes"] } } }) + "\n");
  assert.equal((await native.handle(save, 91)).error, "stream_busy");
  worker.emit("close", 0);
  let cursor = 0; const values = []; let pages = 0;
  do {
    const page = native.handle({ action: "status", sessionId: start.sessionId, cursor }, 91);
    assert.equal(page.error, null); assert.equal(page.state, "finished"); assert.ok(page.events.length <= 32);
    for (const event of page.events) { values.push(...event.samples.map(value => value.value)); assert.ok(!JSON.stringify(event).includes("DEADBEEF")); }
    cursor = page.cursor; pages++; if (!page.hasMore) break;
  } while (true);
  assert.equal(cursor, 3201); assert.ok(pages > 100);
  assert.deepEqual(values, Array.from({ length: 6402 }, (_, i) => i), "finished worker pages retain every actual injected sample");
  assert.equal((await native.handle(save, 92)).error, "unknown_session");
  assert.equal((await native.handle({ ...save, filePath: file }, 91)).error, "forbidden_field");
  assert.equal((await native.handle({ ...save, recording: {} }, 91)).error, "forbidden_field");
  const pending = native.handle(save, 91);
  assert.equal((await native.handle(request, 91)).error, "stream_busy");
  assert.equal((await native.handle({ action: "discard", sessionId: start.sessionId }, 91)).error, "stream_busy");
  assert.equal(reserved, 1, "pending save cannot open a second handoff");
  choose({ canceled: true }); assert.equal((await pending).canceled, true); await assert.rejects(fs.stat(file), /ENOENT/);
  mode = "failure"; assert.equal((await native.handle(save, 91)).error, "EIO");
  mode = "save"; assert.equal((await native.handle(save, 91)).saved, true);
  const bytes = await fs.readFile(file), complete = JSON.parse(bytes.toString());
  assert.ok(bytes.length > 8 * 1024 * 1024); assert.equal(complete.sampleCount, 6402); assert.equal(complete.cycleCount, 3201);
  assert.equal(complete.samples.length, 6402); assert.equal(complete.cycleResults.length, 3201); assert.equal(complete.cycles.length, 1);
  assert.equal(complete.syntheticAcceptance, true); assert.equal(complete.vehicleIO, false); assert.equal(complete.retentionComplete, true);
  assert.deepEqual(complete.samples.map(s => s.value), values);
  assert.ok(!bytes.includes(Buffer.from("DEADBEEF")) && !bytes.includes(Buffer.from("raw-private-test-bytes")));
  assert.equal(resumed, 1); assert.equal((await native.handle({ action: "discard", sessionId: start.sessionId }, 91)).ok, true);
  assert.equal((await native.handle(save, 91)).error, "unknown_session"); await native.shutdown();
  console.log(JSON.stringify({ check: "manufacturer complete retention/save", synthetic: true, vehicleIO: false,
    cycles: 3201, samples: 6402, pages, bytes: bytes.length, actualCloseVerified: true, cancelAndDiskRetry: true }));
}
for (const connectionEpoch of [0, -1, true, 1.5, "1", null]) {
  const isolated = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000 });
  const session = isolated.handle(selection, 88), invalid = children.at(-1); invalid.emit("spawn");
  invalid.stdout.write(JSON.stringify({ event: "cycle", result: { ...event,
    samples: [{ ...event.samples[0], connectionEpoch }] } }) + "\n");
  assert.equal(isolated.handle({ action: "status", sessionId: session.sessionId }, 88).error, "stream_source_invalid");
  invalid.emit("close", 1);
  await isolated.shutdown();
}
console.log("PASS manufacturer connection epochs reject invalid worker metadata");
{
  const directory = path.resolve(process.env.PORSCHE981_SELFCHECK_OUTPUT || ".local/selfchecks/manufacturer-stream");
  await fs.mkdir(directory, { recursive: true });
  let file = path.join(directory, "SYNTHETIC-connection-events.json");
  const extra = { sourceKind: SOURCE.sourceKind, simulation: true, ...FLAGS };
  const connection = (sequence, episode, phase, attempt, connectionEpoch, reason = null) => ({ ...extra,
    sequence, episode, phase, attempt, connectionEpoch, reason, elapsedMs: sequence * 10, observedUtc: "2026-10-11T00:00:00+00:00" });
  const tracker = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000,
    saveRecording: recording => saveManufacturerRecording({ ...recording, syntheticAcceptance: true, vehicleIO: false },
      { dialog: { showSaveDialog: async () => ({ canceled: false, filePath: file }) }, openFile: fs.open }) });
  const start = tracker.handle(selection, 97), worker = children.at(-1); worker.emit("spawn");
  const expected = [];
  for (let episode = 1; episode <= 12; episode++) {
    for (const [phase, attempt, epoch, reason] of [["interrupted", 0, episode, "transport-disconnected"],
      ["retrying", 1, episode, null], ["qualified", 1, episode + 1, null], ["recovered", 1, episode + 1, null]]) {
      const value = connection(expected.length + 1, episode, phase, attempt, epoch, reason); expected.push(value);
      worker.stdout.write(JSON.stringify({ event: "connection-state", result: value }) + "\n");
    }
  }
  let status = tracker.handle({ action: "status", sessionId: start.sessionId }, 97);
  assert.equal(status.state, "running"); assert.equal(status.events.length, 0);
  assert.equal(status.connectionEvents.length, 32); assert.equal(status.hasMoreConnectionEvents, true);
  assert.equal(tracker.handle({ action: "status", sessionId: start.sessionId, connectionCursor: 49 }, 97).error, "invalid_cursor");
  worker.stdout.write(JSON.stringify({ event: "cycle", result: { ...event,
    transactions: [evidence("21A4", "61A42A", 1, 500, 13)], samples: [{ ...event.samples[0], connectionEpoch: 13, elapsedMs: 500 }] } }) + "\n");
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: true, completedCycles: 1,
    requestCount: 1, connectionEventCount: 48 } }) + "\n"); worker.emit("close", 0);
  status = tracker.handle({ action: "status", sessionId: start.sessionId, connectionCursor: 32 }, 97);
  assert.equal(status.state, "finished"); assert.equal(status.connectionEvents.length, 16);
  assert.equal(status.hasMoreConnectionEvents, false); assert.equal(status.connectionCursor, 48);
  assert.equal((await tracker.handle({ action: "save", sessionId: start.sessionId }, 97)).saved, true);
  const saved = JSON.parse(await fs.readFile(file));
  assert.deepEqual(saved.connectionEvents, expected); assert.equal(saved.connectionEventCount, 48);
  assert.equal(saved.sampleCount, 1); assert.equal(saved.samples[0].connectionEpoch, 13);
  // An interrupted worker can finish before producing any values. Keep the
  // explicit event-only save available after the process actually closes.
  const empty = tracker.handle(selection, 97), failed = children.at(-1); failed.emit("spawn");
  failed.stdout.write(JSON.stringify({ event: "connection-state", result: connection(1, 1, "interrupted", 0, 1, "transport-disconnected") }) + "\n");
  failed.stdout.write(JSON.stringify({ event: "connection-state", result: connection(2, 1, "failed", 0, 1, "recovery-close-failed") }) + "\n");
  failed.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: false, error: "recovery-close-failed",
    completedCycles: 0, requestCount: 1, connectionEventCount: 2 } }) + "\n"); failed.emit("close", 0);
  file = path.join(directory, "SYNTHETIC-connection-events-only.json");
  assert.equal((await tracker.handle({ action: "save", sessionId: empty.sessionId }, 97)).saved, true);
  const emptySave = JSON.parse(await fs.readFile(file));
  assert.equal(emptySave.sampleCount, 0); assert.equal(emptySave.samples.length, 0);
  assert.equal(emptySave.connectionEventCount, 2); assert.equal(emptySave.connectionEvents.at(-1).phase, "failed");
  await tracker.shutdown();
  const first = connection(1, 1, "interrupted", 0, 1, "transport-disconnected");
  for (const change of [{ sequence: 2 }, { phase: "recovered" }, { attempt: 4 }, { connectionEpoch: 0 },
    { episode: 2 }, { elapsedMs: -1 }, { observedUtc: "bad" }, { reason: "unknown" }, { pduHex: "DEADBEEF" }]) {
    const isolated = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000 });
    const session = isolated.handle(selection, 98), invalid = children.at(-1); invalid.emit("spawn");
    invalid.stdout.write(JSON.stringify({ event: "connection-state", result: { ...first, ...change } }) + "\n");
    assert.equal(isolated.handle({ action: "status", sessionId: session.sessionId }, 98).error, "stream_connection_state_invalid");
    invalid.emit("close", 1); await isolated.shutdown();
  }
  for (const badSummary of [false, true]) {
    const isolated = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000 });
    const session = isolated.handle(selection, 99), invalid = children.at(-1); invalid.emit("spawn");
    invalid.stdout.write(JSON.stringify({ event: "connection-state", result: first }) + "\n");
    invalid.stdout.write(JSON.stringify(badSummary
      ? { event: "finished", result: { ...SOURCE, ok: false, error: "cancelled", connectionEventCount: 2 } }
      : { event: "connection-state", result: connection(2, 1, "recovered", 0, 1) }) + "\n");
    const status = isolated.handle({ action: "status", sessionId: session.sessionId }, 99);
    assert.equal(status.error, badSummary ? "stream_summary_mismatch" : "stream_connection_state_invalid");
    assert.equal(status.connectionEvents.length, 1); assert.equal(status.released, false);
    invalid.emit("close", 1); await isolated.shutdown();
  }
  console.log("PASS recovery events: immediate IPC before samples, 32-event pages, full explicit save, event-only failure save and invalid transitions");
}

// A negative reply may disable only its bound read parameters for this batch.
// Successful remaining reads keep their original evidence and no fake values.
{
  const ids = ["a".repeat(64), "b".repeat(64)];
  const make = (cycle, negative = false) => {
    const elapsed = cycle * 10;
    const healthy = { ...evidence("22F101", "62F10107", cycle, elapsed), ok: true };
    const bad = { ...evidence("22F100", negative ? "7F2231" : "62F1002A", cycle, elapsed),
      ok: !negative, ...(negative ? { error: "parameter-unavailable" } : {}) };
    const rejected = { ...valueEvidence("22F100", "7F2231", cycle, elapsed),
      parameterId: ids[0], reason: "parameter-unavailable", nrc: 0x31 };
    delete rejected.ok;
    return { ...SOURCE, ok: true, cycle, completedCycles: 1,
      transactions: cycle <= 2 ? [bad, healthy] : [healthy],
      unavailableParameters: negative ? [rejected] : [],
      samples: [...(cycle === 1 ? [{ ...valueEvidence("22F100", "62F1002A", cycle, elapsed),
        parameterId: ids[0], synthetic: true, value: 42, display: "42" }] : []),
        { ...valueEvidence("22F101", "62F10107", cycle, elapsed), parameterId: ids[1], synthetic: true, value: 7, display: "7" }] };
  };
  const tracker = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000 });
  const started = tracker.handle({ ...selection, parameterIds: ids }, 700), worker = children.at(-1); worker.emit("spawn");
  const docs = [make(1), make(2, true), make(3)];
  for (const doc of docs) worker.stdout.write(JSON.stringify({ event: "cycle", result: doc }) + "\n");
  let status = tracker.handle({ action: "status", sessionId: started.sessionId }, 700);
  assert.equal(status.error, null); assert.equal(status.state, "running");
  assert.deepEqual(status.events.flatMap(e => e.samples.map(s => s.value)), [42, 7, 7, 7]);
  assert.equal(status.unavailableParameters.length, 1);
  assert.equal(status.unavailableParameters[0].parameterId, ids[0]);
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: true,
    completedCycles: 3, requestCount: 5, unavailableParameters: docs[1].unavailableParameters } }) + "\n");
  worker.emit("close", 0);
  status = tracker.handle({ action: "status", sessionId: started.sessionId }, 700);
  assert.equal(status.error, null); assert.equal(status.released, true);
  const fresh = tracker.handle({ ...selection, parameterIds: ids }, 700), next = children.at(-1); next.emit("spawn");
  assert.deepEqual(tracker.handle({ action: "status", sessionId: fresh.sessionId }, 700).unavailableParameters, []);
  next.stdout.write(JSON.stringify({ event: "cycle", result: make(1) }) + "\n");
  next.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: true, completedCycles: 1, requestCount: 2 } }) + "\n");
  next.emit("close", 0); await tracker.shutdown();

  for (const mutate of [
    d => { d.unavailableParameters[0].nrc = 0x33; },
    d => { d.unavailableParameters[0].parameterId = "f".repeat(64); },
    d => { d.unavailableParameters[0].elapsedMs++; },
    d => { d.unavailableParameters.push({ ...d.unavailableParameters[0] }); },
    d => { d.unavailableParameters = []; },
    d => { d.samples = []; },
    d => { d.transactions[0].pduHex = "7F2233"; d.transactions[0].pduSha256 = evidence("22F100", "7F2233").pduSha256;
      d.unavailableParameters[0].pduSha256 = d.transactions[0].pduSha256; d.unavailableParameters[0].nrc = 0x33; },
  ]) {
    const isolated = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000 });
    const start = isolated.handle({ ...selection, parameterIds: ids }, 701), badWorker = children.at(-1); badWorker.emit("spawn");
    const doc = make(2, true); doc.cycle = 1;
    for (const row of [...doc.transactions, ...doc.samples, ...doc.unavailableParameters]) row.cycle = 1;
    mutate(doc);
    badWorker.stdout.write(JSON.stringify({ event: "cycle", result: doc }) + "\n");
    assert.equal(isolated.handle({ action: "status", sessionId: start.sessionId }, 701).error, "stream_evidence_invalid");
    badWorker.emit("close", 1); await isolated.shutdown();
  }
  console.log("PASS parameter isolation: bound negative evidence, continuing samples, last values retained, fresh batch reset, forged/incomplete rejection");
}

// Production manager + injected child only: no device/vehicle I/O.
for (const ending of ["terminate", "deadline"]) {
  let worker, clock = 0, resumes = 0;
  const gate = createTransportGate();
  const tracker = createManufacturerStream({ repoRoot: process.cwd(), env: {}, allowInjectedLive: true,
    nowFn: () => clock, timeoutMs: 25, stopTimeoutMs: 1000, gate,
    connection: { snapshot: () => ({ purpose: "diagnostic", model: "OBDKey", serialBaud: 230400 }),
      selectedId: () => "usb:" + "a".repeat(64), reserveDispatch: () => true, releaseDispatch() {},
      suspendForSession: async () => ({ ok: true }), resumeAfterSession: async () => { resumes++; } },
    spawnFn() {
      worker = new EventEmitter();
      worker.stdin = new PassThrough(); worker.stdout = new PassThrough(); worker.stderr = new PassThrough();
      worker.input = ""; worker.stdin.on("data", v => { worker.input += v; }); worker.kill = () => {};
      return worker;
    } });
  const start = await tracker.handle({ ...selection, action: "start-read", confirmedReadOnly: true, x431Inactive: true }, 801);
  worker.emit("spawn");
  let sequence = 0, epoch = 1;
  const send = (phase, attempt, reason = null) => worker.stdout.write(JSON.stringify({ event: "connection-state", result: {
    sequence: ++sequence, episode: 1, phase, attempt, connectionEpoch: epoch, reason, elapsedMs: sequence,
    observedUtc: "2026-10-12T00:00:00+00:00", sourceKind: "independent-manufacturer-read", simulation: false,
    executionEnabled: true, liveVerified: false, writePayload: null, ...(phase === "awaiting-retry" ? { linkReleased: true } : {}) } }) + "\n");
  send("interrupted", 0, "transport-disconnected"); send("awaiting-retry", 0);
  await new Promise(resolve => setTimeout(resolve, 60));
  let status = tracker.handle({ action: "status", sessionId: start.sessionId }, 801);
  assert.equal(status.state, "running"); assert.equal(status.awaitingRetry, true);
  assert.equal(status.error, null, "watchdog must pause for user decision");
  assert.doesNotMatch(worker.input, /"action":"retry"/);
  assert.equal(gate.tryAcquire("session"), false, "waiting still owns vehicle operation");
  const choice = (attempt, extra = {}, owner = 801) => tracker.handle({ action: "retry", sessionId: start.sessionId, episode: 1, attempt, ...extra }, owner);
  assert.equal(choice(0, {}, 802).error, "unknown_session");
  assert.equal(choice(1).ok, false); assert.equal(choice(0, { deviceId: "other" }).ok, false);
  for (let attempt = 0; attempt < 4; attempt++) {
    clock += 1000;
    assert.equal(choice(attempt).ok, true);
    assert.equal(choice(attempt).ok, false, "one choice grants only one attempt");
    send("retrying", attempt + 1);
    epoch++; send("qualified", attempt + 1);
    send("retry-failed", attempt + 1, "transport-disconnected"); send("awaiting-retry", attempt + 1);
  }
  assert.equal(worker.input.split("\n").filter(v => v.includes('"action":"retry"')).length, 4);
  if (ending === "deadline") {
    clock = 600001;
    assert.equal(choice(4).ok, false, "retry cannot restart original ten minute deadline");
    status = tracker.handle({ action: "status", sessionId: start.sessionId }, 801);
    assert.equal(status.stopReason, "time-limit");
  } else tracker.handle({ action: "stop", sessionId: start.sessionId }, 801);
  assert.match(worker.input, /"action":"stop"/);
  assert.equal(gate.tryAcquire("session"), false, "termination waits for actual process close");
  send("failed", 4, ending === "deadline" ? "deadline-expired" : "cancelled");
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ok: false, executionEnabled: true,
    sourceKind: "independent-manufacturer-read", simulation: false, vehicleDataCollected: false, liveVerified: false,
    writePayload: null, error: ending === "deadline" ? "deadline-expired" : "cancelled", connectionEventCount: sequence,
    completedCycles: 0, requestCount: 0, transport: { closed: true, closeErrors: [], openAttempted: true } } }) + "\n");
  worker.emit("close", 1);
  status = tracker.handle({ action: "status", sessionId: start.sessionId }, 801);
  assert.equal(status.error, null); assert.equal(status.released, true); assert.equal(status.awaitingRetry, false);
  assert.equal(resumes, 1); await tracker.shutdown();
}
console.log("PASS manual recovery: no auto retry, owner/episode/attempt binding, four explicit attempts, watchdog pause, original deadline and actual close");

{
  let saved = null;
  const tracker = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, stopTimeoutMs: 10,
    timeoutMs: 1000, closeWaitMs: 1, saveRecording: async recording => {
      saved = [...recording.samples]; return { ok: true, file: "synthetic.pcapng" };
    } });
  const started = tracker.handle(selection, 991), worker = children.at(-1); worker.emit("spawn");
  worker.stdout.write(frame);
  assert.equal((await tracker.shutdown()).error, "port_release_pending");
  assert.equal(tracker.isIdle(), false);
  const status = tracker.handle({ action: "status", sessionId: started.sessionId }, 991);
  assert.equal(status.ok, true); assert.equal(status.events.length, 1);
  assert.equal(tracker.handle({ action: "status", sessionId: started.sessionId }, 992).error, "unknown_session");
  assert.equal(tracker.handle(selection, 991).error, "stream_shutdown");
  assert.equal(tracker.handle({ action: "retry", sessionId: started.sessionId }, 991).error, "stream_shutdown");
  assert.equal(tracker.handle({ action: "save", sessionId: started.sessionId }, 991).error, "stream_busy");
  assert.equal(tracker.handle({ action: "stop", sessionId: started.sessionId }, 991).ok, true);
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: false,
    error: "cancelled", completedCycles: 1, requestCount: 1 } }) + "\n");
  worker.emit("close", 0);
  assert.equal(tracker.handle({ action: "status", sessionId: started.sessionId }, 991).released, true);
  assert.equal((await tracker.handle({ action: "save", sessionId: started.sessionId }, 991)).ok, true);
  assert.equal(saved.length, 1); assert.equal(saved[0].display, "演练");
  assert.equal(tracker.handle({ action: "discard", sessionId: started.sessionId }, 991).ok, true);
  assert.equal(tracker.handle({ action: "status", sessionId: started.sessionId }, 991).error, "unknown_session");
  assert.equal((await tracker.shutdown()).ok, true);
}
console.log("PASS quit timeout retention: owner-bound status/stop/save/discard remain available; start/retry stay blocked until exit");

{
  const base = { simulation: false, executionEnabled: true, liveVerified: false, writePayload: null,
    sourceKind: "independent-manufacturer-read", episode: 1, connectionEpoch: 1, elapsedMs: 1,
    observedUtc: "2026-10-12T00:00:00Z" };
  const interrupted = { ...base, sequence: 1, phase: "interrupted", attempt: 0, reason: "transport-disconnected" };
  const waiting = { ...base, sequence: 2, phase: "awaiting-retry", attempt: 0, reason: null, linkReleased: true };
  const job = { live: true, requireManualRetry: true, connectionEvents: [interrupted] };
  assert.equal(validConnectionEvent({ ...waiting, linkReleased: false }, job), false);
  assert.equal(validConnectionEvent(waiting, job), true);
  const retrying = { ...base, sequence: 2, phase: "retrying", attempt: 1, reason: null };
  assert.equal(validConnectionEvent(retrying, job), false, "active worker cannot auto retry after interruption");
  job.connectionEvents.push(waiting); retrying.sequence = 3;
  assert.equal(validConnectionEvent(retrying, job), false, "waiting alone is not a user decision");
  job.retryRequested = true;
  assert.equal(validConnectionEvent(retrying, job), true);
}

// Transient value errors retain no fake sample and must recover on a later cycle.
{
  const id = "a".repeat(64);
  const make = (n, invalid = false) => {
    const pdu = invalid ? "62F100FF" : "62F1002A";
    const entry = { ...valueEvidence("22F100", pdu, n, n * 10), parameterId: id };
    return { ...SOURCE, ok: true, cycle: n, completedCycles: 1,
      transactions: [evidence("22F100", pdu, n, n * 10)], unavailableParameters: [],
      samples: invalid ? [] : [{ ...entry, synthetic: true, value: 42, display: "42" }],
      invalidParameters: invalid ? [{ ...entry, reason: "invalid_reserved" }] : [] };
  };
  let savedCount = 0;
  const tracker = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000,
    saveRecording: async recording => {
      const samples = [...recording.samples]; assert.equal(recording.sampleCount, 2);
      assert.deepEqual(samples.map(s => s.cycle), [1, 4]); savedCount = samples.length;
      return { ok: true, saved: true };
    } });
  const start = tracker.handle(selection, 940), worker = children.at(-1); worker.emit("spawn");
  for (const doc of [make(1), make(2, true), make(3, true), make(4)]) {
    worker.stdout.write(JSON.stringify({ event: "cycle", result: doc }) + "\n");
    const status = tracker.handle({ action: "status", sessionId: start.sessionId }, 940);
    assert.equal(status.error, null); assert.equal(status.state, "running");
    assert.equal(status.invalidParameters.length, doc.invalidParameters.length);
  }
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: true,
    completedCycles: 4, requestCount: 4, unavailableParameters: [], invalidParameters: [] } }) + "\n");
  worker.emit("close", 0);
  const status = tracker.handle({ action: "status", sessionId: start.sessionId }, 940);
  assert.equal(status.error, null); assert.equal(status.released, true);
  assert.deepEqual(status.events.map(e => e.samples.length), [1, 0, 0, 1]);
  assert.deepEqual(status.invalidParameters, []);
  assert.equal((await tracker.handle({ action: "save", sessionId: start.sessionId }, 940)).saved, true);
  assert.equal(savedCount, 2); await tracker.shutdown();
  for (const mutate of [
    d => { d.invalidParameters[0].reason = "payload_truncated"; },
    d => { d.invalidParameters[0].parameterId = "f".repeat(64); },
    d => { d.invalidParameters[0].pduSha256 = "0".repeat(64); },
    d => { d.invalidParameters[0].elapsedMs++; },
    d => { d.invalidParameters.push({ ...d.invalidParameters[0] }); },
    d => { d.samples = [{ ...d.invalidParameters[0], synthetic: true, value: 42 }]; },
    d => { d.transactions[0].ok = false; },
    d => { d.transactions[0].pduHex = "62F101FF";
      d.transactions[0].pduSha256 = evidence("22F100", "62F101FF").pduSha256;
      d.invalidParameters[0].pduSha256 = d.transactions[0].pduSha256; },
  ]) {
    const isolated = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, timeoutMs: 1000 });
    const start = isolated.handle(selection, 941), worker = children.at(-1); worker.emit("spawn");
    const doc = make(1, true); mutate(doc);
    worker.stdout.write(JSON.stringify({ event: "cycle", result: doc }) + "\n");
    assert.equal(isolated.handle({ action: "status", sessionId: start.sessionId }, 941).error, "stream_evidence_invalid");
    worker.emit("close", 1); await isolated.shutdown();
  }
  console.log("PASS transient invalid values: repeated invalid responses, recovery, no fake samples, bound evidence and fail-closed structure");
}

{
  let clock = 0;
  const timed = createManufacturerStream({ repoRoot: process.cwd(), spawnFn, nowFn: () => clock, stopTimeoutMs: 1000, timeoutMs: 1000 });
  const started = timed.handle(selection, 919), worker = children.at(-1); worker.emit("spawn"); worker.stdout.write(frame);
  const original = JSON.stringify(timed.handle({ action: "status", sessionId: started.sessionId }, 919).events);
  clock = 70000;
  const silent = timed.handle({ action: "status", sessionId: started.sessionId }, 919);
  assert.equal(silent.plotElapsedMs, 70000); assert.equal(JSON.stringify(silent.events), original);
  timed.handle({ action: "stop", sessionId: started.sessionId }, 919);
  clock = 200000;
  assert.equal(timed.handle({ action: "status", sessionId: started.sessionId }, 919).plotElapsedMs, 70000,
    "silent graph keeps moving during acquisition, then freezes on stop without inventing values");
  worker.stdout.write(JSON.stringify({ event: "finished", result: { ...SOURCE, ok: false, error: "cancelled", completedCycles: 1, requestCount: 1 } }) + "\n");
  worker.emit("close", 0); await timed.shutdown();
}
