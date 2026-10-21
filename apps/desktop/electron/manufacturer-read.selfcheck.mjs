import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createManufacturerStream } from "./manufacturer-stream.mjs";
import { createDiagnosticCanRecorder } from "./diagnostic-can-recording.mjs";
import { createTransportGate } from "../src/obd-connection-logic.mjs";

const selection = { action: "start-read", profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
  parameterIds: ["a".repeat(64)], continuous: true, confirmedReadOnly: true, x431Inactive: true };
const source = { executionEnabled: true, liveVerified: false, writePayload: null,
  sourceKind: "independent-manufacturer-read", simulation: false, vehicleDataCollected: true };
const evidence = { requestHex: "21A4", pduHex: "61A42A", pduSha256: createHash("sha256").update(Buffer.from("61A42A", "hex")).digest("hex"),
  ok: true, cycle: 1, elapsedMs: 1, connectionEpoch: 1, sourceKind: source.sourceKind,
  timestampSource: "host-response-observed", processedUtc: "2026-10-11T00:00:00+00:00" };
const { pduHex: _rawEvidence, ...valueEvidence } = evidence;
const cycle = { ...source, ok: true, cycle: 1, completedCycles: 1,
  transactions: [evidence],
  samples: [{ ...valueEvidence, parameterId: selection.parameterIds[0], cycle: 1, synthetic: false, value: 42, display: "42", elapsedMs: 1 }] };
function harness(extra = {}) {
  const children = [], frames = [], pdus = [], gate = createTransportGate();
  let reserved = false, resumes = 0, device = "bt:001122334455";
  const connection = { snapshot: () => ({ purpose: "diagnostic" }), selectedId: () => device,
    reserveDispatch() { if (reserved || gate.owner()) return false; reserved = true; return true; },
    releaseDispatch() { reserved = false; }, suspendForSession: async () => ({ ok: true }),
    async resumeAfterSession() { reserved = false; resumes++; } };
  function spawnFn(_exe, args, options) {
    assert.equal(args.at(-1), "scripts.diagnostics.manufacturer_read");
    assert.ok(args.includes("-B")); assert.equal(options.shell, false);
    const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
    child.input = ""; child.stdin.on("data", b => { child.input += b; }); child.kill = () => { child.killed = true; };
    children.push(child); queueMicrotask(() => child.emit("spawn")); return child;
  }
  const manager = createManufacturerStream({ repoRoot: process.cwd(), gate, connection, spawnFn,
    allowInjectedLive: true, env: {}, timeoutMs: 1000, stopTimeoutMs: 10, closeWaitMs: 30,
    onCanFrame: event => frames.push(event), onDiagnosticPdu: event => pdus.push(event), ...extra });
  return { manager, gate, connection, children, frames, pdus, resumes: () => resumes,
    select: value => { device = value; } };
}
function emit(child, event, result) { child.stdout.write(JSON.stringify({ event, result }) + "\n"); }

{
  let clock = Date.now(), next = 0;
  const timers = new Map();
  const timed = harness({ nowFn: () => clock,
    setTimeoutFn: (fn, delay) => { timers.set(++next, {fn, delay}); return next; },
    clearTimeoutFn: id => timers.delete(id) });
  const job = await timed.manager.handle(selection, 71), worker = timed.children[0];
  emit(worker, "cycle", cycle);
  const duration = [...timers.values()].find(t => t.delay === 600000).fn;
  clock += 599999;
  assert.equal(timed.manager.handle({action:"status",sessionId:job.sessionId},71).state,"running");
  clock++;
  let stopped = timed.manager.handle({action:"status",sessionId:job.sessionId},71);
  assert.equal(stopped.state,"stopping"); assert.equal(stopped.stopReason,"time-limit");
  assert.match(worker.input,/"action":"stop"/); assert.equal(worker.killed,undefined);
  assert.equal(timed.gate.owner(),"session","deadline requests graceful cleanup and keeps the lease until actual close");
  emit(worker,"finished",{...source,ok:false,error:"cancelled",completedCycles:1,requestCount:1,
    transport:{closed:true,closeErrors:[]}});
  assert.equal(timed.gate.owner(),"session"); worker.emit("close",1);
  stopped=timed.manager.handle({action:"status",sessionId:job.sessionId},71);
  assert.equal(stopped.state,"finished"); assert.equal(stopped.events.length,1);
  assert.equal(stopped.events[0].samples[0].value,42); assert.equal(stopped.stopReason,"time-limit");
  assert.equal(timed.resumes(),1); assert.equal(timed.gate.owner(),null);
  const fresh = await timed.manager.handle(selection,71), freshWorker=timed.children[1];
  duration();
  assert.equal(timed.manager.handle({action:"status",sessionId:fresh.sessionId},71).state,"running",
    "a late callback from the old acquisition cannot stop a fresh batch");
  const freshDuration=[...timers.values()].find(t=>t.delay===600000).fn;
  clock+=600000; freshDuration();
  assert.match(freshWorker.input,/"action":"stop"/,"idle acquisition expires without renderer polling");
  emit(freshWorker,"finished",{...source,vehicleDataCollected:false,ok:false,error:"cancelled",
    completedCycles:0,requestCount:0,transport:{closed:true,closeErrors:[]}});
  freshWorker.emit("close",1);
  assert.equal(timed.manager.isIdle(),true);
}

{
  let clock=Date.now(), completeHandoff;
  const timers=[];
  const pending=harness({nowFn:()=>clock,setTimeoutFn:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeoutFn:()=>{}});
  pending.connection.suspendForSession=()=>new Promise(resolve=>{completeHandoff=resolve;});
  const opening=pending.manager.handle(selection,72);
  clock+=600000;timers.find(t=>t.delay===600000).fn();
  completeHandoff({ok:true});
  const cancelled=await opening;
  assert.equal(cancelled.stopReason,"time-limit");assert.equal(cancelled.state,"finished");
  assert.equal(pending.children.length,0,"no worker or ECU request starts after the handoff consumed the deadline");
  assert.equal(pending.manager.isIdle(),true);
}

const denied = harness({ allowInjectedLive: false });
assert.equal((await denied.manager.handle(selection, 1)).error, "live_not_enabled");
assert.equal(denied.children.length, 0);
for (const env of [{ PORSCHE981_HEADLESS: "1" }, { PORSCHE981_SESSION_DENY_LIVE: "1" }, { PORSCHE981_SESSION_STUB: "1" }]) {
  const h = harness({ env }); assert.equal((await h.manager.handle(selection, 1)).error, "live_not_enabled");
}
const h = harness();
for (const invalid of [{ ...selection, deviceId: "bt:001122334455" }, { ...selection, requestHex: "2EF100" },
  { ...selection, confirmedReadOnly: false }, { ...selection, x431Inactive: 1 },
  { ...selection, profileId: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_2" }])
  assert.equal((await h.manager.handle(invalid, 1)).ok, false);
for (const device of ["pt3g:123"]) {
  h.select(device); assert.match((await h.manager.handle(selection, 1)).error, /not-qualified/);
}
h.select("bt:001122334455");
for (const device of ["bt:001122334455", "vnci:123"]) {
  const gateway = harness(); gateway.select(device);
  const request = { ...selection, profileId: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1" };
  const job = await gateway.manager.handle(request, 1), worker = gateway.children[0];
  assert.equal(job.ok, true); const typed = JSON.parse(worker.input.trim());
  assert.equal(typed.ecuId, 9); assert.equal(typed.generation, "981"); assert.equal(typed.deviceId, device);
  assert.equal(typed.profileId, request.profileId); assert.equal(typed.txId, undefined);
  emit(worker, "finished", { ...source, vehicleDataCollected: false, ok: false, error: "identity-mismatch",
    completedCycles: 0, requestCount: 0, transport: { closed: true, closeErrors: [] } });
  assert.equal(gateway.gate.owner(), "session"); worker.emit("close", 1);
  assert.equal(gateway.gate.owner(), null); assert.equal(gateway.resumes(), 1);
}
const started = await h.manager.handle(selection, 1), child = h.children[0];
assert.equal(h.gate.owner(), "session"); assert.equal(h.manager.isIdle(), false);
assert.equal(JSON.parse(child.input.trim()).deviceId, "bt:001122334455");
assert.equal((await h.manager.handle(selection, 2)).error, "busy");
assert.equal(h.manager.handle({ action: "stop", sessionId: started.sessionId }, 2).error, "unknown_session");
const frame = { canId: 0x7E8, extended: false, dataHex: "0361A42A", timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" };
emit(child, "can-frame", { ...source, frame });
assert.equal(h.frames.length, 1); assert.equal(h.frames[0].simulation, false);
emit(child, "cycle", cycle);
let status = h.manager.handle({ action: "status", sessionId: started.sessionId }, 1);
assert.equal(status.events[0].transactions[0].pduHex, undefined);
assert.equal(status.events[0].samples[0].value, 42);
emit(child, "finished", { ...source, ok: false, error: "cancelled", completedCycles: 1, requestCount: 1,
  transport: { closed: true, closeErrors: [], rawLog: [{ private: "raw" }] } });
assert.equal(h.gate.owner(), "session", "a final frame alone never releases the device");
assert.equal(h.manager.handle({ action: "status", sessionId: started.sessionId }, 1).state, "running");
child.emit("close", 1);
status = h.manager.handle({ action: "status", sessionId: started.sessionId }, 1);
assert.equal(status.state, "finished"); assert.equal(status.error, null);
assert.equal(status.result.transport.rawLog, undefined); assert.equal(h.gate.owner(), null);
assert.equal(h.resumes(), 1); assert.equal(h.manager.isIdle(), true);

const timeout = harness(); const timed = await timeout.manager.handle(selection, 1);
const stopped = timeout.manager.cancelActiveAndWait();
await new Promise(resolve => setTimeout(resolve, 15));
assert.equal(timeout.children[0].killed, true);
assert.equal(timeout.manager.handle({ action: "status", sessionId: timed.sessionId }, 1).state, "closing");
assert.equal(timeout.gate.owner(), "session");
timeout.children[0].emit("close", 1);
assert.equal((await stopped).error, "adapter_release_not_verified", "forced worker exit without a close report cannot authorize another device session");
assert.equal(timeout.gate.owner(), null);

const pending = harness(); let completeHandoff;
pending.connection.suspendForSession = () => new Promise(resolve => { completeHandoff = resolve; });
const startup = pending.manager.handle(selection, 8);
pending.manager.cancelOwned(8); completeHandoff({ ok: true });
await startup;
assert.equal(pending.children.length, 0, "destroying a window during handoff never opens the device");
assert.equal(pending.manager.isIdle(), true); assert.equal(pending.resumes(), 1);

const failed = harness(); failed.connection.suspendForSession = async () => ({ ok: false, error: "monitor_close_timeout" });
assert.equal((await failed.manager.handle(selection, 1)).error, "monitor_close_timeout");
assert.equal(failed.children.length, 0); assert.equal(failed.resumes(), 0);
assert.equal(failed.manager.isIdle(), true);
const vnci = harness(); vnci.select("vnci:123");
const vnciJob = await vnci.manager.handle(selection, 3);
assert.equal(vnci.gate.owner(), "session");
assert.equal(JSON.parse(vnci.children[0].input.trim()).deviceId, "vnci:123");
emit(vnci.children[0], "finished", { ...source, ok: false, error: "vnci-device-identity-missing-or-ambiguous",
  completedCycles: 0, vehicleDataCollected: false, requestCount: 0, transport: { closed: true, closeErrors: [] } });
assert.equal(vnci.gate.owner(), "session");
vnci.children[0].emit("close", 1);
assert.equal(vnci.gate.owner(), null);
assert.equal(vnci.manager.handle({ action: "status", sessionId: vnciJob.sessionId }, 3).state, "finished");
await vnci.manager.shutdown();
// Software-injected physical branch only: verify the existing recorder gets
// exact received frames exclusively during the explicit interval. No device IO.
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-injected-rx-"));
const captureFile = path.join(scratch, "INJECTED-NO-VEHICLE.pcapng");
const recorder = createDiagnosticCanRecorder({ chooseFile: async () => captureFile, isLiveReady: () => true });
const capture = harness({ onCanFrame: event => recorder.onFrame(event) });
try {
  await capture.manager.handle(selection, 4);
  const c = capture.children[0];
  emit(c, "can-frame", { ...source, frame });
  assert.equal(fs.existsSync(captureFile), false);
  await recorder.handle({ action: "start", simulation: false });
  emit(c, "can-frame", { ...source, frame: { ...frame, timestampUs: 1 } });
  assert.equal(recorder.snapshot().recording.frameCount, 0);
  emit(c, "can-frame", { ...source, frame: { ...frame, timestampUs: Date.now() * 1000 + 1000 } });
  await recorder.handle({ action: "stop" });
  emit(c, "can-frame", { ...source, frame });
  assert.equal(recorder.snapshot().recording.frameCount, 1);
  const bytes = fs.readFileSync(captureFile), packets = [];
  for (let pos = 0; pos < bytes.length;) {
    const length = bytes.readUInt32LE(pos + 4);
    assert.equal(bytes.readUInt32LE(pos + length - 4), length);
    if (bytes.readUInt32LE(pos) === 6) packets.push(bytes.subarray(pos + 8, pos + length - 4));
    pos += length;
  }
  assert.equal(packets.length, 1);
  assert.equal(packets[0].readUInt32BE(20), frame.canId);
  assert.equal(packets[0][24], frame.dataHex.length / 2);
  assert.equal(packets[0].subarray(28, 32).toString("hex").toUpperCase(), frame.dataHex);
  emit(c, "finished", { ...source, ok: false, error: "cancelled", completedCycles: 0,
    vehicleDataCollected: false, requestCount: 0, transport: { closed: true, closeErrors: [] } });
  c.emit("close", 1);
  await capture.manager.shutdown();
} finally { recorder.shutdown(); fs.rmSync(scratch, { recursive: true, force: true }); }
// Raw application observations remain available even when the cycle fails.
const application = harness(); application.select("vnci:123");
const applicationJob = await application.manager.handle(selection, 9), ac = application.children[0];
const pdu = { requestHex: "21A4", pduHex: "7F2131", timestampUs: Date.now()*1000,
  timestampSource: "host-pdu-observed", transportKind: "d-pdu-iso-tp", observation: "native-result-data",
  duringCleanup: true, primitive: 1, uniqueId: 77, flagsHex: "00000000" };
emit(ac, "diagnostic-pdu", { ...source, pdu });
assert.equal(application.pdus.length, 1); assert.deepEqual(application.pdus[0].pdu,pdu);
assert.equal(application.pdus[0].profileId,selection.profileId);
emit(ac,"cycle",{...cycle,ok:false,completedCycles:0,samples:[],transactions:[{...evidence,ok:false}],vehicleDataCollected:false,error:"negative-response"});
const appStatus = application.manager.handle({action:"status",sessionId:applicationJob.sessionId},9);
assert.ok(!JSON.stringify(appStatus).includes("7F2131"),"raw bytes never enter ordinary values/status");
emit(ac,"finished",{...source,ok:false,error:"negative-response",completedCycles:0,vehicleDataCollected:false,requestCount:1,transport:{closed:true,closeErrors:[]}});
assert.equal(application.gate.owner(),"session"); ac.emit("close",1); assert.equal(application.gate.owner(),null);
await application.manager.shutdown();
const invalidPdu = harness(); await invalidPdu.manager.handle(selection,10);
emit(invalidPdu.children[0],"diagnostic-pdu",{...source,pdu:{...pdu,pduHex:"lowercase"}});
assert.equal(invalidPdu.pdus.length,0); assert.equal(invalidPdu.gate.owner(),"session");
invalidPdu.children[0].emit("close",1); assert.equal(invalidPdu.gate.owner(),null); await invalidPdu.manager.shutdown();
await h.manager.shutdown(); await denied.manager.shutdown(); await pending.manager.shutdown(); await failed.manager.shutdown();
console.log("manufacturer-read selfcheck passed: strict IDs/flags, selected-device handoff, shared lease, actual-close release, cancellation race, RX streaming, default raw exclusion, exact PCAPNG bridge and explicit interval (injected, no device IO)");
