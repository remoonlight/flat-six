import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createManufacturerStream } from "./manufacturer-stream.mjs";
import fs from "node:fs/promises";
import path from "node:path";
import { createTransportGate } from "./obd-connection.mjs";
import { saveManufacturerRecording } from "./recording-files.mjs";

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
assert.equal(beforeClose.error, "stream_shutdown");
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
// No native/serial factory is used; saved test files carry synthetic provenance.
{
  const output = path.resolve(process.env.PORSCHE981_SELFCHECK_OUTPUT || ".local/selfchecks/manufacturer-stream");
  await fs.mkdir(output, { recursive: true });
  const directory = await fs.mkdtemp(path.join(output, "run-"));
  const file = path.join(directory, "SYNTHETIC-manufacturer-complete.json");
  let worker, mode = "cancel", choose, reserved = 0, resumed = 0;
  const native = createManufacturerStream({ repoRoot: process.cwd(), env: {}, allowInjectedLive: true,
    gate: createTransportGate(), connection: {
      snapshot: () => ({ purpose: "diagnostic", model: "OBDKey" }), selectedId: () => "usb:" + "a".repeat(64),
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
  assert.equal(JSON.parse(worker.input.split("\n")[0]).deviceId, "usb:" + "a".repeat(64));
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
