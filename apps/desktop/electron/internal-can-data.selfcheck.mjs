import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { spawnSync } from "node:child_process";
import { createCanFrameBatch, createCanPcapWriter, decodeRacelogicInteger, decodeInternalInteger, loadInternalSignals } from "./internal-can-data.mjs";
import { createObdConnectionManager } from "./obd-connection.mjs";

const scratch = path.resolve(process.env.PORSCHE981_SELFCHECK_OUTPUT || path.join(process.cwd(), ".local/selfchecks/internal-can"));
fs.mkdirSync(scratch, { recursive: true });
{
  const signal = (startBit, length, extra = {}) => ({ startBit, length, signed: false, byteOrder: "Motorola", dlc: 8,
    factor: "1", offset: "0", ...extra });
  // Vendor application note p2 and its DBC on p8 describe the same signals.
  const pdu = "0C52260A12979763";
  assert.deepEqual(decodeRacelogicInteger(pdu, signal(56, 8)), { raw: 12, value: 12 });
  assert.deepEqual(decodeRacelogicInteger(pdu, signal(32, 24, { factor: "0.01" })), { raw: 5383690, value: 53836.9 });
  assert.equal(decodeRacelogicInteger(pdu, signal(0, 32, { factor: "1E-005", signed: true })).raw, 311924579);
  const negative = Buffer.alloc(8); negative.writeInt32BE(-5882246);
  const longitude = decodeRacelogicInteger(negative.toString("hex").toUpperCase(), signal(32, 32, { factor: "1e-5", signed: true }));
  assert.equal(longitude.raw, -5882246); assert.ok(Math.abs(longitude.value + 58.82246) < 1e-10);
  // Independent bit walk in Vector DBC coordinates, rather than the decoder's
  // integer/shift implementation. The vendor's Sats 56 -> DBC 7 and Time
  // 32 -> DBC 15 establish which end of a Motorola signal Start Bit denotes.
  const dbcStart = (start, length) => {
    const msb = start + length - 1;
    return Math.floor((63 - msb) / 8) * 8 + msb % 8;
  };
  assert.equal(dbcStart(56, 8), 7); assert.equal(dbcStart(32, 24), 15); assert.equal(dbcStart(0, 32), 39);
  const bytes = Buffer.from("0123456789ABCDEF", "hex");
  let cases = 0;
  for (let start = 0; start < 64; start++) for (let length = 1; length <= Math.min(32, 64 - start); length++) {
    let bit = dbcStart(start, length), expected = 0;
    for (let n = 0; n < length; n++) {
      expected = expected * 2 + (bytes[Math.floor(bit / 8)] >> (bit % 8) & 1);
      bit = bit % 8 === 0 ? bit + 15 : bit - 1;
    }
    assert.equal(decodeRacelogicInteger(bytes.toString("hex").toUpperCase(), signal(start, length)).raw, expected);
    let little = 0;
    for (let n = 0; n < length; n++) little += (bytes[Math.floor((start + n) / 8)] >> ((start + n) % 8) & 1) * 2 ** n;
    assert.equal(decodeRacelogicInteger(bytes.toString("hex").toUpperCase(), signal(start, length, { byteOrder: "Intel" })).raw, little);
    cases += 2;
  }
  assert.deepEqual(decodeRacelogicInteger("000000000000FFFF", signal(0, 16, { signed: true, factor: ".5", offset: "-1" })), { raw: -1, value: -1.5 });
  for (const change of [{ startBit: -1 }, { startBit: 64 }, { length: 0 }, { length: 54 }, { startBit: 63, length: 2 },
    { dlc: 7 }, { byteOrder: "motorola-vendor-label" }, { signed: 0 }])
    assert.throws(() => decodeRacelogicInteger(pdu, signal(0, 8, change)), /internal-signal-layout-invalid/);
  for (const value of ["", "0x1", "Infinity", NaN, true])
    assert.throws(() => decodeRacelogicInteger(pdu, signal(0, 8, { factor: value })), /internal-signal-scale-invalid/);
  for (const data of ["", "00".repeat(7), "00".repeat(9), "0123456789abcdef"])
    assert.throws(() => decodeRacelogicInteger(data, signal(0, 8)), /internal-signal-data-invalid/);
  console.log(`Racelogic integer checks PASS: vendor manual/DBC pairs, signed scaling, ${cases} independent bit-layout cases; no source or vehicle qualification inferred`);
}
const frame = (canId, dataHex = "0102", extended = false) => ({ canId, dataHex, extended,
  timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" });
{
  // Exercise the actual Python producer against the desktop's ingest bound.
  // Serial reads and clocks are modeled; no port or device API is opened.
  const produced = spawnSync(process.env.PORSCHE981_PYTHON || "python3", ["-B", "-c", `
import json
import inspect
from pathlib import Path
from scripts.diagnostics.internal_stream import MemoryReceiver
assert Path(inspect.getfile(MemoryReceiver)).resolve().is_relative_to(Path.cwd().resolve())
cases=[]
for name,chunks in [('zero-byte', [b'123\\r'*1024]), ('one-byte', [b'123 01\\r'*585]),
                    ('mixed', [b'123\\r'*1024,b'105 A5 FF 80 3E 55 AA 7D CC\\r'])]:
    rows=[]
    receiver=MemoryReceiver(rows.append,'bt:001122334455','drive',clock=lambda:1700000000123456789)
    receiver.enabled=True;receiver.batch_id=name
    for chunk in chunks:
        assert len(chunk)<=4096
        receiver.feed(chunk)
    cases.append(dict(name=name,rows=rows,count=receiver.count))
print(json.dumps(cases))
`], { cwd: process.cwd(), encoding: "utf8", timeout: 10000, maxBuffer: 2 * 1024 * 1024, windowsHide: true });
  assert.equal(produced.status, 0, produced.stderr || String(produced.error));
  const cases = JSON.parse(produced.stdout), expected = [1024, 585, 1025];
  for (const [i, test] of cases.entries()) {
    const received = createCanFrameBatch({ signals: loadInternalSignals(process.cwd(), "drive") });
    let count = 0;
    for (const event of test.rows) {
      assert.ok(event.frames.length > 0 && event.frames.length <= 512);
      count += event.frames.length;
      assert.equal(event.frameCount, count);
      assert.equal(event.receiveBatchId, test.name);
      assert.ok(event.frames.every(frame => frame.timestampUs === 1700000000123456));
      received.ingest(event.frames);
    }
    assert.equal(count, expected[i]); assert.equal(test.count, count);
    assert.equal(received.view().retainedFrames, count);
    assert.equal([...received.export({ stream: true }).frames].length, count);
    if (test.name === "mixed") {
      received.stop("user-stopped");
      const parameters = received.exportParameters("drive");
      assert.equal(parameters.sampleCount, 2);
      const samples = [...parameters.samples];
      assert.equal(samples.length, 2);
      assert.deepEqual(samples.map(sample => [sample.value, sample.unit]), [[4000, "rpm"], [50, "%"]]);
      assert.equal(samples[0].capturedUtc, "2023-11-14T22:13:20.123456Z");
    }
  }
  console.log("internal serial burst PASS: actual Python producer → bounded desktop ingest; zero/one-byte frames retained, mixed parameter decode and arrival timestamps preserved");
}
{
  const sourceSignals = loadInternalSignals(process.cwd(), "drive");
  assert.deepEqual(sourceSignals.map(d => [d.nameEn, d.unit]), [["Engine_Speed", "rpm"], ["Throttle_Position", "%"], ["Steering", "%"], ["Brake", "psi"],
    ["BrakeLF", ""], ["BrakeRF", ""], ["BrakeLR", ""], ["BrakeRR", "%"]]);
  assert.deepEqual(sourceSignals.slice(4).map(d => decodeInternalInteger("A5AA11334455CCDD", d).value), [10, 30, 40, 50]);
  assert.deepEqual(sourceSignals.slice(0, 2).map(d => decodeRacelogicInteger("A5FF803E55AA7DCC", d).value), [4000, 50]);
  const steering = sourceSignals[2];
  for (const [word, expected] of [[0, 0], [1, 1], [3020, 3020], [32767, 32767], [32768, -0], [32769, -1], [35788, -3020], [65535, -32767]]) {
    const bytes = Buffer.alloc(8, 0xA5); bytes.writeUInt16LE(word, 2);
    const decoded = decodeInternalInteger(bytes.toString("hex").toUpperCase(), steering);
    assert.equal(decoded.raw, expected); assert.equal(decoded.value, expected / 30.2 + 0);
  }
  assert.throws(() => decodeInternalInteger("0000000000000000", { ...steering, signEncoding: "unknown" }), /layout-invalid/);
  assert.throws(() => decodeInternalInteger("0000000000000000", { ...steering, signed: false }), /layout-invalid/);
  assert.throws(() => decodeInternalInteger("0000000000000000", { ...steering, divisor: 0 }), /scale-invalid/);
  const brake = sourceSignals[3];
  for (let value = 0; value < 256; value++) {
    const bytes = Buffer.alloc(8, 0xFF); bytes[1] = value;
    const decoded = decodeInternalInteger(bytes.toString("hex").toUpperCase(), brake);
    assert.equal(decoded.raw, value); assert.equal(decoded.value, value / 1.7);
  }
  for (const divisor of [0, null, NaN, Infinity, "1.7", true])
    assert.throws(() => decodeInternalInteger("0000000000000000", { ...brake, divisor }), /scale-invalid/);
  const batch = createCanFrameBatch({ signals: sourceSignals });
  batch.ingest([{ ...frame(159, "FFFFCC8BFFFFFFFF"), timestampUs: 1792022400123456 }]);
  assert.equal(batch.view().parameters[2].value, -100);
  assert.deepEqual(loadInternalSignals(process.cwd(), "drive", "982"), [], "981 source definitions do not qualify 982");
  assert.deepEqual(loadInternalSignals(process.cwd(), "comfort"), [], "drive source definitions do not qualify other wiring");
  const fixture = fs.mkdtempSync(path.join(scratch, "source-binding-"));
  const seed = "data/seed/obd/internal-signals.v1.json", seedFile = path.join(fixture, seed);
  const original = JSON.parse(fs.readFileSync(path.join(process.cwd(), seed), "utf8"));
  fs.mkdirSync(path.dirname(seedFile), { recursive: true });
  const invalid = [d => { d.generation = "982"; }, d => { d.sources.racelogic.sha256 = "0".repeat(64); },
    d => { d.sources.autosportlabs.sha256 = "0".repeat(64); }, d => { d.entries[0].refRow = d.entries[0].refRow.replace(",261,", ",262,"); },
    d => { d.entries[0].presetChannel.offset++; }, d => { d.entries[0].presetChannel.bigEndian = true; },
    d => { d.entries[0].presetChannel.div = 0; }, d => { d.entries[0].presetChannel.div = 2; },
    d => { d.entries[0].presetChannel.subId = 1; }, d => { d.entries[0].presetChannel.idMask = 0x7ff; },
    d => { d.entries[0].presetChannel.filtId = 1; }, d => { d.entries[0].presetChannel.type = 1; },
    d => { d.entries[0].presetChannel.bm = true; }, d => { d.entries[0].presetChannel.bus = 1; },
    d => { d.entries[0].presetChannel.ut = "rpm"; }, d => { d.entries[0].presetChannel.id++; },
    d => { d.entries[0].presetChannel.len++; }, d => { d.entries[0].presetChannel.mult = "1"; },
    d => { d.entries[0].presetChannel.add++; }, d => { d.entries[1] = d.entries[0]; },
    d => { d.entries.push(d.entries[0]); }, d => { d.entries[1].id = "unverified"; },
    d => { d.entries[2].presetChannel.type = 1; }, d => { d.entries[2].presetChannel.offset = 3; },
    d => { d.entries[2].presetChannel.bigEndian = true; }, d => { d.entries[2].presetChannel.div = 30; },
    d => { d.entries[2].presetChannel.ut = "°"; }, d => { d.entries[2].presetChannel.bm = true; },
    d => { d.entries[2].presetChannel.id = 160; }, d => { d.entries[2].sourceKind = "REF"; },
    d => { d.entries[2].refRow = "unverified"; }, d => { d.entries[2].name = "方向盘角度"; },
    d => { d.sources.consumer.sha256 = "0".repeat(64); },
    d => { d.entries[3].presetChannel.type = 3; }, d => { d.entries[3].presetChannel.offset = 5; },
    d => { d.entries[3].presetChannel.bigEndian = true; }, d => { d.entries[3].presetChannel.div = 0; },
    d => { d.entries[3].presetChannel.ut = "bar"; }, d => { d.entries[3].presetChannel.filtId = 1; },
    d => { d.entries[3].sourceKind = "REF"; }, d => { d.entries[3].nameEn = "BrakeLF"; },
    d => { d.entries[3].presetChannel.id = 288; }, d => { d.entries[3] = d.entries[2]; }];
  for (const index of [4, 5, 6, 7]) {
    invalid.push(d => { d.entries[index].presetChannel.offset = 1; },
      d => { d.entries[index].presetChannel.ut = "PSI"; },
      d => { d.entries[index].presetChannel.type = 3; },
      d => { d.entries[index].presetChannel.div = 1; },
      d => { d.entries[index].name = "制动压力"; },
      d => { d.entries[index].presetChannel.nm = "Brake"; });
  }
  try {
    for (const change of invalid) {
      const mutated = structuredClone(original); change(mutated); fs.writeFileSync(seedFile, JSON.stringify(mutated));
      assert.throws(() => loadInternalSignals(fixture, "drive"), /internal-signal-source-invalid/);
    }
  } finally { fs.rmSync(fixture, { recursive: true }); }
  console.log(`internal source binding PASS: eight source-bound 981 definitions; ${invalid.length} conflicting/unqualified source cases rejected`);
  const definition = (id, canId, startBit, unit) => ({ id, name: id, canId, extended: false, startBit,
    length: 16, signed: false, byteOrder: "Intel", dlc: 8, factor: "0.1", offset: "0", unit, sourceQualified: true });
  const signals = [definition("temperature-a", 0x321, 0, "°F"), definition("temperature-b", 0x321, 16, "°C"), definition("rpm", 0x322, 0, "rpm")];
  let tick = 0;
  const values = createCanFrameBatch({ signals, elapsedNow: () => tick });
  signals[0].factor = "999"; // The retained batch owns an immutable source snapshot.
  const input = frame(0x321, "D007F40100000000"); input.timestampUs = 1792022400123456;
  values.ingest([input, frame(0x322, "1027000000000000"), frame(0x555, "FFFFFFFFFFFFFFFF")]);
  assert.equal(values.view().parameterSampleCount, 3);
  assert.deepEqual(values.view().parameters.map(p => p.value), [200, 50, 1000]);
  assert.throws(() => values.exportParameters("drive"), /internal_receive_busy/);
  tick = 100; values.ingest([frame(0x321)]);
  assert.deepEqual(values.view().parameters.slice(0, 2).map(p => [p.value, p.invalid]), [[200, true], [50, true]]);
  tick = 500; values.ingest([input]);
  assert.equal(values.view().parameters[0].segments.length, 2, "invalid observations break a line, retaining the last valid value");
  values.interrupt("synthetic-link-loss"); tick = 60001;
  values.ingest([input]);
  assert.equal(values.view().parameterSampleCount, 7);
  assert.equal(values.view().parameters[0].segments.length, 2, "reconnected observations do not join across the gap");
  assert.equal(values.view().parameters[0].segments[0][0].elapsedMs, 500, "the exact sixty-second window excludes expired observations");
  tick = 120002;
  assert.equal(values.view().parameters.flatMap(p => p.segments).length, 0, "silent graph windows keep advancing");
  assert.equal(values.view().parameters[0].value, 200, "the last numeric value survives graph expiry");
  values.stop("user-stopped"); tick = 500000;
  assert.equal(values.view().plotElapsedMs, 120002);
  const saved = values.exportParameters("drive"), samples = [...saved.samples];
  assert.equal(saved.sampleCount, 7); assert.equal(samples.length, 7);
  assert.equal(samples[0].capturedUtc, "2026-10-15T00:00:00.123456Z");
  assert.deepEqual(samples.slice(0, 3).map(p => p.parameterId), ["temperature-a", "temperature-b", "rpm"]);
  assert.equal(samples[0].value, 200, "later caller changes to definitions do not alter exported values");
  assert.deepEqual([...saved.samples], samples, "a retained export can be iterated again without losing its prefix");
  assert.equal(samples.some(p => p.parameterId === "0x555"), false);
  const dense = createCanFrameBatch({ signals: [definition("dense", 0x321, 0, "°C")], elapsedNow: () => 0 });
  for (let n = 0; n < 1000; n++) {
    const data = Buffer.alloc(8); data.writeUInt16LE(n);
    dense.ingest([frame(0x321, data.toString("hex").toUpperCase())]);
  }
  assert.ok(dense.view().parameters[0].segments.flat().length <= 4);
  assert.deepEqual(dense.view().parameters[0].segments.flat().map(p => p.value), [0, 99.9]);
  dense.stop("user-stopped"); assert.equal([...dense.exportParameters("drive").samples].length, 1000,
    "display reduction never reduces complete saved observations");
  assert.throws(() => createCanFrameBatch({ signals: [{ ...definition("bad", 1, 0, ""), sourceQualified: false }] }), /internal-signal-source-invalid/);
  console.log("internal parameter batch PASS: immutable definitions, all recognized parameters, microsecond arrival times, gaps, invalid isolation, silent window/freeze and full export beyond display reduction");
}
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

for (const partial of [false, true]) {
  const failedFile = path.join(scratch, `lost-write-${partial}-${Date.now()}.pcapng`);
  let fail = false, wrotePartial = false, closed = false;
  const interrupted = createCanPcapWriter(failedFile, "synthetic", {
    write: (fd, data, offset, length) => {
      if (fail) {
        if (partial && !wrotePartial) { wrotePartial = true; return fs.writeSync(fd, data, offset, Math.min(5,length)); }
        fail = false; throw Object.assign(new Error("disk write failed"), {code:"ENOSPC"});
      }
      return fs.writeSync(fd, data, offset, length);
    }, close: fd => { fs.closeSync(fd); closed = true; },
  });
  interrupted.append([frame(0x123)]); const prefix = fs.readFileSync(failedFile); fail = true;
  assert.throws(() => interrupted.append([frame(0x124)]), /disk write failed/);
  const failedBytes = fs.readFileSync(failedFile);
  assert.throws(() => interrupted.append([frame(0x125)]), /pcap-write-failed/);
  assert.deepEqual(fs.readFileSync(failedFile), failedBytes);
  interrupted.finish("recording-write-failed"); interrupted.finish("idempotent");
  assert.equal(closed, true); assert.equal(interrupted.count, 1);
  assert.deepEqual(fs.readFileSync(failedFile), failedBytes, "failed capture has no normal end statistics");
  assert.deepEqual(failedBytes.subarray(0,prefix.length), prefix);
  const types = []; let at = 0;
  while (at + 8 <= failedBytes.length) {
    const type = failedBytes.readUInt32LE(at), length = failedBytes.readUInt32LE(at+4);
    if (length < 12 || at + length > failedBytes.length) break;
    assert.equal(failedBytes.readUInt32LE(at+length-4),length); types.push(type); at += length;
  }
  assert.deepEqual(types,[0x0a0d0d0a,1,6],"only the complete received prefix is readable");
}

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
  internalSignals: () => [], // This block verifies retained raw compatibility, without parameter definitions.
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
emit({ type: "handshake", ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now(), receiveState: "standby", receiveReason: "time-limit" });
assert.equal(manager.snapshot().internal.receiveState, "idle");
assert.equal(manager.snapshot().internal.receiveBatchId, undefined);
emit({ type: "frames", frames: [frame(0x099)] });
assert.equal(manager.snapshot().internal.retainedFrames, 0, "connection alone never accepts frames");
const [manualBegin, racingRefresh] = await Promise.all([manager.handle({ action: "new-batch" }), manager.handle({ action: "list" })]);
assert.equal(manualBegin.ok, true); assert.equal(racingRefresh.error, "busy", "queued refresh cannot bypass a just-started batch");
emit({ type: "reading", ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now() });
emit({ type: "frames", frames: [frame(0x100)] });
assert.equal(fs.existsSync(recording), false, "normal reception never creates capture files");
assert.equal(manager.snapshot().internal.retainedFrames, 1);
assert.equal((await manager.handle({ action: "record-start" })).ok, true);
emit({ type: "frames", frames: [frame(0x101)] });
await manager.handle({ action: "record-stop" });
emit({ type: "frames", frames: [frame(0x102)] });
assert.equal(manager.snapshot().recording.frameCount, 1);
assert.equal((await manager.handle({ action: "save-result" })).error, "internal_receive_busy", "save waits for receive stop confirmation");
assert.equal((await manager.handle({ action: "disconnect" })).error, "busy");
assert.equal((await manager.handle({ action: "new-batch" })).error, "internal_receive_busy");
await manager.handle({ action: "receive-stop" });
assert.equal(manager.snapshot().internal.stopReason, "user-stopped");
assert.equal(manager.snapshot().internal.receiveState, "stopping");
assert.equal((await manager.handle({ action: "new-batch" })).error, "internal_receive_busy");
emit({ type: "reading", ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now(), receiveState: "standby", receiveReason: "user-stopped" });
assert.equal(manager.snapshot().internal.receiveState, "standby");
assert.equal((await manager.handle({ action: "save-result" })).saved.frames, 3);
assert.equal(manager.snapshot().connected, true);
emit({ type: "frames", frames: [frame(0x103)] });
assert.equal(manager.snapshot().internal.retainedFrames, 3);
const stoppedBatchId = manager.snapshot().internal.receiveBatchId;
emit({ type: "stopped", ok: true, restoration: { errors: [] } }); child.exitCode = 0; child.emit("close", 0);
await manager.handle({ action: "connect" });
const reconnectIdle = child.stdin.read().toString().trim().split("\n").map(JSON.parse).find(r => r.action === "monitor");
assert.equal(reconnectIdle.receiveMilliseconds, 0, "manual stop cannot resume on reconnect before the old deadline");
assert.equal(reconnectIdle.receiveBatchId, stoppedBatchId);
emit({ type: "handshake", ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now(), receiveState: "standby", receiveReason: "time-limit" });
assert.equal(manager.snapshot().internal.stopReason, "user-stopped");
await manager.handle({ action: "new-batch" });
emit({ type: "reading", ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now() });
await manager.handle({ action: "receive-stop" });
assert.equal(manager.snapshot().internal.receiveState, "stopping");
emit({ type: "stopped", ok: true, restoration: { errors: [] } }); child.exitCode = 0; child.emit("close", 0);
assert.equal(manager.snapshot().internal.receiveState, "standby", "verified process release ends a pending stop when standby heartbeat is lost");
assert.equal(manager.snapshot().internal.stopReason, "user-stopped");
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
  assert.equal(timed.snapshot().internal.receiveState,"idle");
  assert.equal(JSON.parse(worker.stdin.read().toString().trim()).receiveMilliseconds,0);
  elapsed+=700000; assert.equal(timed.snapshot().internal.receiveState,"idle","no batch deadline before manual start");
  await timed.handle({action:"new-batch"});
  send({type:"reading",ok:true,commOk:true,volts:12.6,voltageSource:"atrv",at:clock});
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
  assert.equal(worker,expiredWorker,"idle disconnection waits for explicit retry");
  assert.equal(timed.snapshot().linkState,"retry-required");
  await timed.handle({action:"connect"});
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
  await timed.handle({action:"receive-stop"});
  send({type:"reading",ok:true,commOk:true,volts:12.6,voltageSource:"atrv",at:clock,receiveState:"standby",receiveReason:"user-stopped"});
  await timed.handle({action:"disconnect"});await timed.shutdown();
}
// Reconnect uses the original batch/deadline and actual arrival timestamps.
{
  let elapsed = 0, wall = 1700000000000, worker;
  const retries = [], deadlines = [];
  const recovering = createObdConnectionManager({ repoRoot: process.cwd(), now: () => wall, elapsedNow: () => elapsed,
    internalSignals: () => [], // Raw compatibility fixture; source-parameter reception is checked separately.
    env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `recovery-${Date.now()}.json`) },
    listFn: async () => ({ devices: [device] }), retryDelayMs: () => 77,
    setTimeoutFn: (fn, ms) => { if (ms === 77) { const token = { fn }; retries.push(token); return token; } return setTimeout(fn, ms); },
    clearTimeoutFn: token => { if (token?.fn) token.cleared = true; else clearTimeout(token); },
    setReceiveTimeoutFn: (fn, ms) => { const token = { fn, ms }; deadlines.push(token); return token; },
    clearReceiveTimeoutFn: token => { token.cleared = true; },
    saveResult: async data => ({ ok: true, frames: Array.from(data.frames) }),
    monitorSpawnFn: () => {
      worker = new EventEmitter(); worker.stdin = new PassThrough(); worker.stdout = new PassThrough(); worker.stderr = new PassThrough(); worker.exitCode = null;
      gracefulStop(worker); worker.kill = () => { worker.exitCode = 0; worker.emit("close", 0); return true; }; return worker;
    }, gracefulMs: 10, killWaitMs: 10 });
  const send = doc => worker.stdout.write(JSON.stringify({ receiveBatchId: recovering.snapshot().internal?.receiveBatchId,
    deviceId: device.id, canNetwork: "drive", simulation: false, ...doc }) + "\n");
  const reading = (type = "reading", extra = {}) => send({ type, ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: wall, receiveState: "receiving", ...extra });
  const loseLink = () => {
    send({ type: "error", ok: false, error: "port-io:injected-link-loss" });
    send({ type: "stopped", ok: true, restoration: { errors: [] } });
    worker.exitCode = 1; worker.emit("close", 1);
  };
  await recovering.handle({ action: "list" }); await recovering.handle({ action: "select", deviceId: device.id });
  await recovering.handle({ action: "configure", purpose: "internal", canNetwork: "drive" }); await recovering.handle({ action: "connect" });
  reading("handshake", { receiveState: "standby", receiveReason: "time-limit" });
  await recovering.handle({ action: "new-batch" }); reading();
  const originalId = recovering.snapshot().internal.receiveBatchId, firstStamp = wall * 1000;
  send({ type: "frames", frames: [{ ...frame(0x120), timestampUs: firstStamp }] });
  elapsed += 120000; wall += 120000; const oldWorker = worker; loseLink();
  assert.equal(recovering.snapshot().linkState, "reconnecting");
  assert.equal(retries.length, 1, "active receive schedules automatic reconnection");
  assert.equal(recovering.snapshot().internal.retainedFrames, 1);
  elapsed += 45000; wall += 45000; retries[0].fn();
  assert.notEqual(worker, oldWorker);
  const reconnect = JSON.parse(worker.stdin.read().toString().trim());
  assert.equal(reconnect.receiveBatchId, originalId);
  assert.equal(reconnect.receiveMilliseconds, 435000, "reconnection consumes the original ten-minute budget");
  reading("handshake");
  send({ type: "frames", frames: [{ ...frame(0x121), timestampUs: wall * 1000 }] });
  assert.equal((await recovering.handle({ action: "save-result" })).error, "internal_receive_busy");
  assert.equal(recovering.snapshot().internal.interruptions.length, 1);
  elapsed += 435000; wall += 435000; deadlines[0].fn();
  assert.equal(recovering.snapshot().internal.stopReason, "time-limit");
  assert.equal(recovering.snapshot().internal.retainedFrames, 2);
  reading("reading", { receiveState: "standby", receiveReason: "time-limit" });
  const saved = (await recovering.handle({ action: "save-result" })).saved;
  assert.deepEqual(saved.frames.map(row => row.timestampUs), [firstStamp, firstStamp + 165000000], "no gap filling or compressed reconnect time");
  await recovering.handle({ action: "new-batch" }); reading();
  const nextId = recovering.snapshot().internal.receiveBatchId;
  elapsed += 599000; wall += 599000; loseLink();
  assert.equal(retries.length, 2);
  elapsed += 2000; wall += 2000; retries[1].fn();
  const afterExpiry = worker.stdin.read().toString().trim().split("\n").map(JSON.parse).find(row => row.action === "monitor");
  assert.equal(afterExpiry.receiveBatchId, nextId);
  assert.equal(afterExpiry.receiveMilliseconds, 0, "automatic reconnect after deadline only opens standby");
  reading("handshake", { receiveState: "standby", receiveReason: "time-limit" });
  send({ type: "frames", frames: [{ ...frame(0x122), timestampUs: wall * 1000 }] });
  assert.equal(recovering.snapshot().internal.retainedFrames, 0);
  assert.equal(recovering.snapshot().internal.receiveState, "standby");
  assert.equal(recovering.snapshot().internal.stopReason, "time-limit");
  await recovering.handle({ action: "disconnect" }); await recovering.shutdown();
}
console.log("internal automatic reconnect checks PASS: original batch and deadline, exact arrival timestamps, no gap filling, expired reconnect stays idle");
console.log("internal CAN checks PASS: packed complete batches beyond 25000 frames, PCAPNG wire format, explicit recording period, no automatic files, deadline/actual close");
