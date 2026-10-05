import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createCanFrameBatch, createCanPcapWriter } from "./internal-can-data.mjs";
import { createObdConnectionManager } from "./obd-connection.mjs";

const scratch = path.join(process.cwd(), ".local/selfchecks/internal-can");
fs.mkdirSync(scratch, { recursive: true });
const frame = (canId, dataHex = "0102", extended = false) => ({ canId, dataHex, extended,
  timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" });
const batch = createCanFrameBatch({ limit: 2 });
batch.ingest([frame(0x123), frame(0x124), frame(0x125)]);
assert.equal(batch.view().frameCount, 3);
assert.equal(batch.export().frameCount, 2);
assert.equal(batch.export().frames.length, 2);
assert.equal(batch.view().batchClosed, true);
assert.equal(batch.view().latest.length, 3);
assert.throws(() => batch.ingest([{ ...frame(0x123), dataHex: "PDU" }]));

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

let child;
const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", available: true, paired: true };
const recording = path.join(scratch, `only-enabled-period-${Date.now()}.pcapng`);
const manager = createObdConnectionManager({ repoRoot: process.cwd(),
  env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `manager-${Date.now()}.json`) },
  listFn: async () => ({ devices: [device] }),
  chooseRecordingFile: async () => recording,
  saveResult: async (data) => ({ ok: true, saved: true, frames: data.frames.length }),
  monitorSpawnFn: () => {
    child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null;
    child.kill = () => { child.exitCode = 0; child.emit("close", 0); return true; };
    return child;
  }, gracefulMs: 10, killWaitMs: 10 });
await manager.handle({ action: "list" });
await manager.handle({ action: "select", deviceId: device.id });
await manager.handle({ action: "configure", purpose: "internal", canNetwork: "drive" });
await manager.handle({ action: "connect" });
const emit = (data) => child.stdout.write(JSON.stringify({ deviceId: device.id, canNetwork: "drive", simulation: false, ...data }) + "\n");
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
console.log("internal CAN checks PASS: bounded complete batches, PCAPNG wire format, explicit recording period, no automatic files, actual close");
