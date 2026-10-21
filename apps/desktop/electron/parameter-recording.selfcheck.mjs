import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { saveParameterRecording, createParameterRecordingService, parameterTimestampNs } from "./parameter-recording.mjs";

const root = await fs.mkdtemp(path.join(os.tmpdir(), "flat-six-parameter-export-"));
const date = new Date("2020-01-01T00:00:00Z"), when = "2020-01-01T00:00:00.123456+00:00";
const sample = { pid: "0C", value: 2000, unit: "rpm", capturedUtc: when, synthetic: true,
  requestHex: "SECRET-REQUEST", pduHex: "SECRET-PDU", rawValue: "SECRET-RAW", source: { secret: "PRIVATE-SOURCE" } };
const engine = { kind: "engine-acquisition-batch", retentionComplete: true, simulation: true,
  samples: [sample] };
Object.defineProperty(engine, "runs", { get() { throw new Error("must-not-read-communication-results"); } });
const native = { name: "当前状态", unit: null, numeric: false, value: 1, display: "开启", processedUtc: when,
  synthetic: false, requestHex: "SECRET-REQUEST", pduSha256: "PRIVATE-SOURCE", rawValue: 1 };
const manufacturer = (samples = [native], count = samples.length) => ({ kind: "manufacturer-acquisition-read", simulation: false,
  retentionComplete: true, profileId: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1", sampleCount: count,
  samples, get cycleResults() { throw new Error("must-not-read-communication-results"); } });
const deps = { desktopPath: root, nowFn: () => date };

// Independent PCAPNG reader: validate every block boundary, interface, tag,
// original timestamp and exact JSON allowlist without using writer helpers.
function read(bytes, onSample = () => {}) {
  let offset = 0, count = 0, finalCount, section = false, interfaceSeen = false;
  while (offset < bytes.length) {
    const type = bytes.readUInt32LE(offset), length = bytes.readUInt32LE(offset + 4);
    assert.ok(length >= 12 && length % 4 === 0 && offset + length <= bytes.length);
    assert.equal(bytes.readUInt32LE(offset + length - 4), length);
    const body = bytes.subarray(offset + 8, offset + length - 4);
    if (type === 0x0a0d0d0a) { assert.equal(section, false); assert.equal(body.readUInt32LE(), 0x1a2b3c4d); section = true; }
    else if (type === 1) {
      assert.ok(section); assert.equal(interfaceSeen, false); interfaceSeen = true;
      assert.equal(body.readUInt16LE(), 252);
      let i = 8, resolution;
      while (i < body.length) { const code = body.readUInt16LE(i), n = body.readUInt16LE(i + 2);
        if (!code) break; if (code === 9) resolution = body[i + 4]; i += 4 + Math.ceil(n / 4) * 4; }
      assert.equal(resolution, 9);
    } else if (type === 6) {
      assert.ok(interfaceSeen); assert.equal(body.readUInt32LE(), 0);
      const timestamp = (BigInt(body.readUInt32LE(4)) << 32n) | BigInt(body.readUInt32LE(8));
      const length = body.readUInt32LE(12); assert.equal(body.readUInt32LE(16), length);
      const data = body.subarray(20, 20 + length);
      assert.equal(data.readUInt16BE(), 12); assert.equal(data.readUInt16BE(2), 4);
      assert.equal(data.toString("ascii", 4, 8), "json"); assert.equal(data.readUInt32BE(8), 0);
      const value = JSON.parse(data.subarray(12).toString("utf8"));
      assert.deepEqual(Object.keys(value), ["name", "value", "unit", "capturedUtc"]);
      onSample(value, timestamp, count++);
    } else if (type === 5) { assert.equal(body.readUInt16LE(12), 4); finalCount = Number(body.readBigUInt64LE(16)); }
    else assert.fail(`unexpected PCAPNG block ${type}`);
    offset += length;
  }
  assert.equal(offset, bytes.length); assert.equal(finalCount, count); return count;
}
try {
  assert.equal(parameterTimestampNs(when), 1577836800123456000n);
  assert.equal(parameterTimestampNs("2020-01-01T08:00:00.123456789+08:00"), 1577836800123456789n);
  for (const invalid of ["bad", "2020-02-31T00:00:00Z", "2020-01-01T24:00:00Z", "2020-01-01T00:00:00", "1969-01-01T00:00:00Z", "2020-01-01T00:00:00.1234567890Z"])
    assert.throws(() => parameterTimestampNs(invalid), /parameter_timestamp_invalid/);
  const first = await saveParameterRecording(engine, deps); assert.equal(first.saved, true);
  const initial = await fs.readFile(first.filePath);
  assert.equal(read(initial, (value, timestamp) => {
    assert.deepEqual(value, { name: "转速", value: 2000, unit: "rpm", capturedUtc: when });
    assert.equal(timestamp, 1577836800123456000n);
  }), 1);
  assert.match(initial.toString(), /SYNTHETIC/); assert.doesNotMatch(initial.toString(), /SECRET|PRIVATE|requestHex|pduHex|rawValue/);
  const second = await saveParameterRecording(engine, deps); assert.equal(second.saved, true); assert.notEqual(second.filePath, first.filePath);
  assert.deepEqual(await fs.readFile(first.filePath), initial, "same timestamp does not overwrite an earlier file");
  const vendor = await saveParameterRecording(manufacturer(), deps); assert.equal(vendor.saved, true);
  assert.match(path.basename(vendor.filePath), /^Gateway-20200101T000000Z-parameters/);
  read(await fs.readFile(vendor.filePath), value => assert.deepEqual(value, { name: "当前状态", value: "开启", unit: null, capturedUtc: when }));
  // Decoded zero-terminated strings and MinLength=0 byte fields may be empty.
  // Preserve complete values even when a reference tool abbreviates its display.
  const fieldValues = ["", "A".repeat(248), Buffer.from(Array.from({ length: 3851 }, (_, i) => i % 256)).toString("hex").toUpperCase()];
  const fields = fieldValues.map((display, i) => ({ ...native, name: `字段${i}`, value: display, display }));
  const fieldFile = await saveParameterRecording(manufacturer(fields), deps);
  assert.equal(fieldFile.saved, true, "a valid empty value must not reject the entire parameter batch");
  assert.equal(read(await fs.readFile(fieldFile.filePath), (value, timestamp, i) => {
    assert.deepEqual(value, { name: `字段${i}`, value: fieldValues[i], unit: null, capturedUtc: when });
    assert.equal(timestamp, 1577836800123456000n);
  }), 3);
  for (const invalid of [undefined, null, "A".repeat(65537)]) {
    const failed = await saveParameterRecording(manufacturer([{ ...native, display: invalid }]), deps);
    assert.equal(failed.saved, false, "missing and overlong values remain invalid");
  }
  assert.equal((await saveParameterRecording(manufacturer([{ ...native, numeric: true, value: "", display: "" }]), deps)).saved, false,
    "an empty numeric value is not a valid decoded string");
  const mixedUnits = [
    { ...native, name: "进气温度", numeric: true, value: 212, display: "212", unit: "°F" },
    { ...native, name: "冷却液温度", numeric: true, value: 100, display: "100", unit: "°C" },
    { ...native, name: "压力", numeric: true, value: 1, display: "1", unit: "bar" },
    { ...native, name: "速度", numeric: true, value: 60, display: "60", unit: "mph" },
    { ...native, name: "电流", numeric: true, value: 500, display: "500", unit: "mA" },
    { ...native, name: "未知单位", numeric: true, value: 7, display: "7", unit: "unknown" },
    { ...native, name: "枚举", numeric: false, value: 1, display: "开启", unit: null },
    { ...native, name: "数值字符串", numeric: true, value: "212", display: "212", unit: "°F" },
    { ...native, name: "转速中文单位", numeric: true, value: "1500", display: "1500", unit: "转/分" },
    { ...native, name: "航速", numeric: true, value: 10, display: "10", unit: "knot" },
    { ...native, name: "电阻", numeric: true, value: 1500, display: "1500", unit: "mOhm" },
    { ...native, name: "频率", numeric: true, value: .5, display: "0.5", unit: "MHz" },
    { ...native, name: "时长", numeric: true, value: 250000, display: "250000", unit: "us" },
    { ...native, name: "运行时长", numeric: true, value: .5, display: "0.5", unit: "小时" },
    { ...native, name: "未明确含义的单位", numeric: true, value: 5, display: "5", unit: "最低" },
  ];
  const mixedBefore = JSON.stringify(mixedUnits);
  const normalized = await saveParameterRecording(manufacturer(mixedUnits), deps);
  assert.equal(normalized.saved, true);
  const expected = [[100, "°C"], [100, "°C"], [100, "kPa"], [96.56064, "km/h"], [.5, "A"], [7, "unknown"], ["开启", null], [100, "°C"],
    [1500, "rpm"], [18.52, "km/h"], [1.5, "Ω"], [500000, "Hz"], [.25, "s"], [1800, "s"], [5, "最低"]];
  assert.equal(read(await fs.readFile(normalized.filePath), (value, timestamp, i) => {
    if (typeof expected[i][0] === "number") assert.ok(Math.abs(value.value - expected[i][0]) < 1e-9);
    else assert.equal(value.value, expected[i][0]);
    assert.equal(value.unit, expected[i][1]); assert.equal(value.capturedUtc, when);
    assert.equal(timestamp, 1577836800123456000n);
  }), mixedUnits.length);
  assert.equal(JSON.stringify(mixedUnits), mixedBefore, "export conversion never mutates the captured source or enum data");
  const capture = manufacturer(); capture.startedUtc = "2019-12-31T23:59:58Z";
  const captureNamed = await saveParameterRecording(capture, deps);
  assert.match(path.basename(captureNamed.filePath), /^Gateway-20191231T235958Z-parameters/,
    "filename uses batch capture start, not the later save time");
  const before = (await fs.readdir(root)).length;
  for (const recording of [{ ...engine, retentionComplete: false }, { ...engine, samples: [{ ...sample, value: NaN }] },
    { ...engine, samples: [{ ...sample, synthetic: false }] }, { ...engine, samples: [{ ...sample, capturedUtc: "bad" }] },
    manufacturer([native], 2), manufacturer([native, native], 1), manufacturer([], 0),
    manufacturer([{ ...native, numeric: true, value: "开启", unit: "°F" }]),
    manufacturer([{ ...native, numeric: true, value: "", unit: "°F" }]),
    { kind: "internal-can-acquisition", simulation: false, retentionComplete: true }]) {
    const result = await saveParameterRecording(recording, deps); assert.equal(result.saved, false);
    assert.equal((await fs.readdir(root)).length, before, "invalid/incomplete batch never published and temporary files removed");
  }
  for (const mode of ["write", "zero", "sync", "close", "publish"]) {
    const result = await saveParameterRecording(engine, { ...deps,
      openFile: async (...args) => { const file = await fs.open(...args); return {
        write: (...values) => mode === "write" ? Promise.reject(Object.assign(new Error("injected"), { code: "EIO" }))
          : mode === "zero" ? Promise.resolve({ bytesWritten: 0 }) : file.write(...values),
        sync: () => mode === "sync" ? Promise.reject(Object.assign(new Error("injected"), { code: "ENOSPC" })) : file.sync(),
        close: async () => { await file.close(); if (mode === "close") throw new Error("injected-close"); },
      }; },
      linkFile: mode === "publish" ? async () => { throw Object.assign(new Error(), { code: "EIO" }); } : fs.link,
    });
    assert.equal(result.saved, false, mode); assert.equal((await fs.readdir(root)).length, before, mode);
    assert.deepEqual(await fs.readFile(first.filePath), initial);
  }
  const partial = await saveParameterRecording(engine, { ...deps, openFile: async (...args) => {
    const file = await fs.open(...args); return { write: (bytes, start, length, position) => file.write(bytes, start, Math.min(length, 7), position),
      sync: () => file.sync(), close: () => file.close() };
  } });
  assert.equal(partial.saved, true); assert.equal(read(await fs.readFile(partial.filePath)), 1);
  const largeCount = 50000;
  const many = { *[Symbol.iterator]() { for (let i = 0; i < largeCount; i++) yield { ...native, display: "值".repeat(100) + i }; } };
  const large = await saveParameterRecording(manufacturer(many, largeCount), deps);
  assert.equal(large.sampleCount, largeCount); assert.ok((await fs.stat(large.filePath)).size > 8 * 1024 * 1024);
  assert.equal(read(await fs.readFile(large.filePath), (value, _stamp, i) => assert.equal(value.value, "值".repeat(100) + i)), largeCount);
  const revealed = [], service = createParameterRecordingService({ ...deps, revealFile: file => revealed.push(file) });
  const owned = await service.save(engine, 1); assert.equal(owned.saved, true);
  assert.equal(service.reveal({ saveId: owned.saveId }, 2).ok, false);
  assert.equal(service.reveal({ saveId: owned.saveId, filePath: "/injected" }, 1).ok, false);
  assert.equal(service.reveal({ saveId: owned.saveId }, 1).ok, true); assert.deepEqual(revealed, [owned.filePath]);
  service.clearOwner(1); assert.equal(service.reveal({ saveId: owned.saveId }, 1).ok, false);
  assert.equal((await service.save(engine, 0)).saved, false);
  console.log("PASS parameter-only PCAPNG block/JSON/timestamp validation; standard/enumerated values; synthetic labeling; no overwrite; invalid counts/data; write/sync/close/publish failures; partial writes; 50,000 observations over 8 MiB; owner-bound reveal");
} finally { await fs.rm(root, { recursive: true, force: true }); }
