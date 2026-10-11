import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createCanFrameBatch, createCanPcapWriter } from "./internal-can-data.mjs";
import { createObdConnectionManager } from "./obd-connection.mjs";

const scratch = path.resolve(process.env.PORSCHE981_SELFCHECK_OUTPUT || path.join(process.cwd(), ".local/selfchecks/internal-can"));
fs.mkdirSync(scratch, { recursive: true });
const frame = (canId, dataHex = "0102", extended = false) => ({ canId, dataHex, extended,
  timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" });
const batch = createCanFrameBatch();
batch.ingest([frame(0x123), frame(0x124), frame(0x125)]);
assert.equal(batch.view().frameCount, 3);
assert.equal(batch.export().frameCount, 3);
assert.equal(batch.export().frames.length, 3);
assert.equal(batch.view().batchClosed, false);
assert.equal(batch.view().latest.length, 3);
assert.throws(() => batch.ingest([{ ...frame(0x123), dataHex: "PDU" }]));
assert.throws(() => batch.ingest([{ ...frame(0x123), unknown: "do not silently discard fields" }]));
const original = index => frame(index % 2 ? 0x1fffffff : index % 2048,
  (index % 256).toString(16).padStart(2, "0").toUpperCase().repeat(index % 9), index % 2 === 1);
const complete = createCanFrameBatch();
const count = 80003;
for (let i = 0; i < count; i += 512) complete.ingest(Array.from({ length: Math.min(512, count - i) }, (_, n) =>
  ({ ...original(i + n), timestampUs: Number.MAX_SAFE_INTEGER - (i + n) })));
const captured = complete.export({ stream: true });
complete.ingest([frame(0)]);
let observed = 0;
for (const actual of captured.frames) {
  assert.deepEqual(actual, { ...original(observed), timestampUs: Number.MAX_SAFE_INTEGER - observed }); observed++;
}
assert.equal(observed, count, "snapshot is exact and excludes frames received after Save As began");
assert.equal(complete.view().retainedFrames, count + 1);
assert.equal(complete.view().frameLimit, null);
assert.equal(complete.view().batchClosed, false);
assert.equal(complete.view().retainedBytes, (count + 1) * 21);
assert.ok(complete.view().allocatedBytes - complete.view().retainedBytes < 4096 * 21);
complete.stop("time-limit"); complete.ingest([frame(0)]);
assert.equal(complete.view().retainedFrames, count + 1);

const file = path.join(scratch, `wire-format-${Date.now()}.pcapng`);
const writer = createCanPcapWriter(file, "drive");
writer.append([frame(0x123), frame(0x18daf101, "AABBCC", true)]);
writer.finish();
const bytes = fs.readFileSync(file);
const blocks = [];
for (let position = 0; position < bytes.length;) {
  const type = bytes.readUInt32LE(position), length = bytes.readUInt32LE(position + 4);
  assert.ok(length >= 12 && length % 4 === 0);
  assert.equal(bytes.readUInt32LE(position + length - 4), length);
  blocks.push({ type, body: bytes.subarray(position + 8, position + length - 4) }); position += length;
}
assert.deepEqual(blocks.map((entry) => entry.type), [0x0a0d0d0a, 1, 6, 6, 5]);
assert.equal(blocks[1].body.readUInt16LE(), 227);
assert.equal(blocks[2].body.readUInt32BE(20), 0x123);
assert.equal(blocks[2].body[24], 2);
assert.equal(blocks[2].body.subarray(28, 30).toString("hex"), "0102");
assert.equal(blocks[3].body.readUInt32BE(20), 0x98daf101);
assert.throws(() => createCanPcapWriter(file, "drive"), /EEXIST/);

function gracefulStop(worker) {
  const write = worker.stdin.write.bind(worker.stdin);
  worker.stdin.write = (bytes, ...args) => {
    const accepted = write(bytes, ...args);
    if (String(bytes).includes('"stop"')) {
      worker.stdout.write(JSON.stringify({ type: "stopped", ok: true, restoration: { errors: [] } }) + "\n");
      worker.exitCode = 0; worker.emit("close", 0);
    }
    return accepted;
  };
}
let child;
const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", available: true, paired: true };
const recording = path.join(scratch, `only-enabled-period-${Date.now()}.pcapng`);
const manager = createObdConnectionManager({ repoRoot: process.cwd(),
  env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `manager-${Date.now()}.json`) },
  listFn: async () => ({ devices: [device] }),
  chooseRecordingFile: async () => recording,
  saveResult: async (data) => ({ ok: true, saved: true, frames: Array.from(data.frames).length }),
  monitorSpawnFn: () => {
    child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null;
    gracefulStop(child);
    child.kill = () => { child.exitCode = 0; child.emit("close", 0); return true; };
    return child;
  }, gracefulMs: 10, killWaitMs: 10 });
await manager.handle({ action: "list" });
await manager.handle({ action: "select", deviceId: device.id });
await manager.handle({ action: "configure", purpose: "internal", canNetwork: "drive" });
await manager.handle({ action: "connect" });
const emit = (data) => child.stdout.write(JSON.stringify({ receiveBatchId: manager.snapshot().internal?.receiveBatchId, receiveState: "receiving", deviceId: device.id, canNetwork: "drive", simulation: false, ...data }) + "\n");
emit({ type: "handshake", ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now() });
emit({ type: "frames", frames: [frame(0x100)] });
assert.equal(fs.existsSync(recording), false, "normal reception never creates capture files");
assert.equal(manager.snapshot().internal.retainedFrames, 1);
assert.equal((await manager.handle({ action: "record-start" })).ok, true);
emit({ type: "frames", frames: [frame(0x101)] });
await manager.handle({ action: "record-stop" });
emit({ type: "frames", frames: [frame(0x102)] });
assert.equal(manager.snapshot().recording.frameCount, 1);
assert.equal((await manager.handle({ action: "save-result" })).saved.frames, 3);
await manager.handle({ action: "disconnect" });
assert.equal(manager.snapshot().connected, false);
await manager.shutdown();

{
  let clock=Date.now(), elapsed=0, worker, index=0;
  const expiries=[],receiveExpiries=[];
  const timed=createObdConnectionManager({repoRoot:process.cwd(),now:()=>clock,elapsedNow:()=>elapsed,
    env:{PORSCHE981_CONNECTION_STATE:path.join(scratch,`timed-${Date.now()}.json`)},
    listFn:async()=>({devices:[device]}), retryDelayMs:()=>1,
    chooseRecordingFile:async()=>path.join(scratch,`timed-record-${Date.now()}-${++index}.pcapng`),
    setReceiveTimeoutFn:(fn,delay)=>{const token={fn,delay};receiveExpiries.push(token);return token;},
    clearReceiveTimeoutFn:token=>{token.cleared=true;},
    setTimeoutFn:(fn,delay)=>{if(delay===600000){const token={fn};expiries.push(token);return token;}return setTimeout(fn,delay);},
    clearTimeoutFn:token=>{if(token?.fn)token.cleared=true;else clearTimeout(token);},
    monitorSpawnFn:()=>{
      worker=new EventEmitter();worker.stdin=new PassThrough();worker.stdout=new PassThrough();worker.stderr=new PassThrough();worker.exitCode=null;
      gracefulStop(worker);
      worker.kill=()=>{worker.exitCode=0;worker.emit("close",0);return true;};return worker;
    },gracefulMs:10,killWaitMs:10});
  await timed.handle({action:"list"});await timed.handle({action:"select",deviceId:device.id});
  await timed.handle({action:"configure",purpose:"internal",canNetwork:"drive"});await timed.handle({action:"connect"});
  const send=doc=>worker.stdout.write(JSON.stringify({receiveBatchId:timed.snapshot().internal?.receiveBatchId,receiveState:"receiving",deviceId:device.id,canNetwork:"drive",simulation:false,...doc})+"\n");
  send({type:"handshake",ok:true,commOk:true,volts:12.6,voltageSource:"atrv",at:clock});
  const firstBatchId=timed.snapshot().internal.receiveBatchId;
  await timed.handle({action:"record-start"});
  const first=timed.snapshot().recording.file;
  const originalWall=clock;
  clock+=3600000;
  assert.equal(timed.snapshot().recording.active,true,"forward clock correction cannot end PCAPNG early");
  assert.equal(timed.snapshot().internal.receiveState,"receiving");
  clock-=7200000;
  assert.equal(timed.snapshot().recording.active,true,"backward correction cannot change the deadline");
  clock=originalWall;
  elapsed+=599999;clock+=599999;send({type:"frames",frames:[frame(0x105)]});
  assert.equal(timed.snapshot().recording.frameCount,1);
  elapsed++;clock++;send({type:"frames",frames:[frame(0x106)]});
  assert.equal(timed.snapshot().recording.reason,"time-limit");
  assert.equal(timed.snapshot().recording.frameCount,1);assert.equal(timed.snapshot().connected,true);
  assert.equal(expiries[0].cleared,true);
  const completed=fs.readFileSync(first);assert.ok(completed.includes(Buffer.from('"reason":"time-limit"')));
  send({type:"frames",frames:[frame(0x107)]});assert.deepEqual(fs.readFileSync(first),completed);
  assert.equal(timed.snapshot().internal.stopReason,"time-limit");
  assert.equal(timed.snapshot().internal.retainedFrames,1);
  assert.equal(timed.snapshot().internal.receiveState,"stopping");
  const controls=worker.stdin.read().toString().trim().split("\n").map(JSON.parse);
  assert.ok(controls.some(r=>r.action==="receive-stop"&&r.receiveBatchId===firstBatchId));
  send({type:"reading",ok:true,commOk:true,volts:12.6,voltageSource:"atrv",at:clock,receiveState:"standby",receiveReason:"time-limit"});
  assert.equal(timed.snapshot().internal.receiveState,"standby");
  const expiredWorker=worker;send({type:"stopped",ok:true,restoration:{errors:[]}});worker.exitCode=0;worker.emit("close",0);
  await new Promise(resolve=>setTimeout(resolve,15));
  assert.notEqual(worker,expiredWorker);
  const reconnect=JSON.parse(worker.stdin.read().toString().trim());
  assert.equal(reconnect.receiveMilliseconds,0,"reconnect after expiry must enter standby");
  assert.equal(reconnect.receiveBatchId,firstBatchId,"reconnect cannot reset batch identity");
  send({type:"handshake",ok:true,commOk:true,volts:12.6,voltageSource:"atrv",at:clock,receiveState:"standby",receiveReason:"time-limit"});
  await timed.handle({action:"new-batch"});
  send({type:"reading",ok:true,commOk:true,volts:12.6,voltageSource:"atrv",at:clock});
  const nextBatchId=timed.snapshot().internal.receiveBatchId;
  assert.notEqual(nextBatchId,firstBatchId);
  receiveExpiries[0].fn();assert.equal(timed.snapshot().internal.receiveState,"receiving","old receive deadline cannot end next batch");
  send({type:"frames",receiveBatchId:firstBatchId,frames:[frame(0x108)]});
  assert.equal(timed.snapshot().internal.retainedFrames,0,"late old-batch frames cannot enter next batch");
  await timed.handle({action:"record-start"});const second=timed.snapshot().recording.file;
  expiries[0].fn();assert.equal(timed.snapshot().recording.active,true);
  elapsed+=600000;clock+=600000;expiries[1].fn();
  assert.ok(fs.readFileSync(second).includes(Buffer.from('"reason":"time-limit"')),"idle recording closes without status polling");
  assert.equal(timed.snapshot().recording.frameCount,0);assert.equal(timed.snapshot().connected,true);
  await timed.handle({action:"disconnect"});await timed.shutdown();
}
console.log("internal CAN checks PASS: packed complete batches beyond 25000 frames, PCAPNG wire format, explicit recording period, no automatic files, deadline/actual close");
