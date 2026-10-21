import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { saveRecordingFile, saveInternalCanRecording, saveEngineRecording, saveManufacturerRecording } from "./recording-files.mjs";
import { createEngineAcquisition } from "../src/engine-acquisition.mjs";
import { createCanFrameBatch } from "./internal-can-data.mjs";
const input = { fileName: "obd-gateway.json", recording: { kind: "x431-offline-plan", vehicleDataCollected: false, samples: [] } };
let shown = 0, opened = 0; const writes = [];
const deps = { dialog: { async showSaveDialog(options) { shown++; assert.equal(options.title, "保存此次采集");
  return { canceled: false, filePath: "chosen.json" }; } },
  async openFile() { opened++; return {
    async write(bytes, offset, length) { writes.push(Buffer.from(bytes.subarray(offset, offset + length))); return { bytesWritten: length }; },
    async sync() {}, async close() {},
  }; }, async renameFile() {}, async unlinkFile() {} };
assert.equal((await saveRecordingFile({ ...input, path: "unapproved.json" }, deps)).ok, false);
assert.equal((await saveRecordingFile({ ...input, fileName: "../obd.json" }, deps)).ok, false);
assert.equal((await saveRecordingFile({ ...input, recording: { value: "x".repeat(8 * 1024 * 1024) } }, deps)).error, "recording_too_large");
assert.equal(shown, 0);
const saved = await saveRecordingFile(input, deps); assert.equal(saved.saved, true); assert.equal(saved.filePath, "chosen.json");
assert.deepEqual(JSON.parse(Buffer.concat(writes).toString()), input.recording);
assert.deepEqual(Object.keys(saved).sort(), ["filePath", "ok", "saved"]);
assert.equal((await saveRecordingFile(input, { ...deps, dialog: { async showSaveDialog() { return { canceled: true }; } } })).canceled, true);
assert.equal(opened, 1);
assert.equal((await saveRecordingFile(input, { ...deps, async openFile() { throw Object.assign(new Error(), { code: "EACCES" }); } })).error, "EACCES");
for (const recording of [{}, { text: "完整记录\nΩ", nested: { missing: null, values: [1, false] } }, new Date("2026-10-12T00:00:00Z")]) {
  writes.length = 0;
  assert.equal((await saveRecordingFile({ fileName: "generic.json", recording }, deps)).saved, true);
  assert.equal(Buffer.concat(writes).toString(), JSON.stringify(recording) + "\n", "generic JSON serialization remains exact");
}
const shownBeforeUnicode = shown;
assert.equal((await saveRecordingFile({ fileName: "generic.json", recording: { text: "车".repeat(3 * 1024 * 1024) } }, deps)).error, "recording_too_large");
assert.equal(shown, shownBeforeUnicode, "UTF-8 byte cap still precedes native file selection");
const directory = path.resolve(process.env.PORSCHE981_SELFCHECK_OUTPUT || ".local/selfchecks/recording-files");
await fs.mkdir(directory, { recursive: true });
const nativeFile = path.join(directory, `SYNTHETIC-complete-internal-${Date.now()}.json`);
const can = createCanFrameBatch();
const makeFrame = index => ({ canId: index % 2048, extended: false, dataHex: index.toString(16).padStart(16, "0").toUpperCase(),
  timestampUs: 1700000000000000 + index, timestampSource: "host-chunk-arrival" });
for (let i = 0; i < 100001; i += 512) can.ingest(Array.from({ length: Math.min(512, 100001 - i) }, (_, n) => makeFrame(i + n)));
const snapshot = () => ({ ...can.export({ stream: true }), syntheticAcceptance: true, simulation: true, vehicleDataCollected: false });
const nativeDeps = { dialog: { async showSaveDialog() { return { canceled: false, filePath: nativeFile }; } }, openFile: fs.open };
assert.equal((await saveInternalCanRecording(snapshot(), { ...nativeDeps, dialog: { async showSaveDialog() { return { canceled: true }; } } })).canceled, true);
await assert.rejects(fs.stat(nativeFile), /ENOENT/, "cancel never creates a result file");
const full = await saveInternalCanRecording(snapshot(), nativeDeps);
assert.equal(full.saved, true); assert.equal(full.frameCount, 100001);
assert.ok((await fs.stat(nativeFile)).size > 8 * 1024 * 1024, "complete explicitly saved batch exceeds old JSON cap");
const result = JSON.parse(await fs.readFile(nativeFile, "utf8"));
assert.equal(result.frames.length, 100001); assert.equal(result.receivedTotal, 100001);
assert.equal(result.syntheticAcceptance, true); assert.equal(result.vehicleDataCollected, false);
result.frames.forEach((value, index) => assert.deepEqual(value, makeFrame(index)));
assert.equal(can.view().retainedFrames, 100001, "saving retains the whole in-memory batch");
let closed = 0, pieces = [];
const small = () => ({ ...snapshot(), frameCount: 3, retainedFrames: 3, receivedTotal: 3,
  frames: [makeFrame(0), makeFrame(1), makeFrame(2)] });
const shortWrite = await saveInternalCanRecording(small(), { ...nativeDeps, async openFile() { return {
  async write(bytes, offset, length) { const bytesWritten = Math.min(length, 7); pieces.push(Buffer.from(bytes.subarray(offset, offset + bytesWritten))); return { bytesWritten }; },
  async close() { closed++; },
  async sync() {},
}; }, async renameFile() {}, async unlinkFile() {} });
assert.equal(shortWrite.saved, true); assert.equal(closed, 1);
assert.deepEqual(JSON.parse(Buffer.concat(pieces).toString()).frames, small().frames);
for (const failure of ["EIO", "short", "close"]) {
  let closeCalls = 0;
  const rejected = await saveInternalCanRecording(small(), { ...nativeDeps, async openFile() { return {
    async write(_bytes, _offset, length) { if (failure === "EIO") throw Object.assign(new Error(), { code: "EIO" }); return { bytesWritten: failure === "short" ? 0 : length }; },
    async sync() {},
    async close() { closeCalls++; if (failure === "close") throw Object.assign(new Error(), { code: "ENOSPC" }); },
  }; } });
  assert.equal(rejected.saved, false); assert.ok(closeCalls >= 1);
  assert.equal(rejected.error, ({ EIO: "EIO", short: "recording-short-write", close: "ENOSPC" })[failure]);
}
assert.equal((await saveInternalCanRecording({ ...small(), frames: [makeFrame(0)] }, nativeDeps)).error, "recording-frame-mismatch");
// A fresh full snapshot must remain retryable after a failed temporary write.
assert.equal((await saveInternalCanRecording(snapshot(), nativeDeps)).saved, true);
assert.equal(JSON.parse(await fs.readFile(nativeFile, "utf8")).frames.length, 100001);
console.log("PASS recording Save As: native destination, cancel without write, size/path validation, preserved provenance and write failure");
console.log("PASS streamed internal Save As: every frame beyond 8MiB, bounded chunks, short writes, close/disk failure and complete retry");

// Finite worker sessions aggregate beyond both old standard-data boundaries.
const engineBatch = createEngineAcquisition();
for (let round = 0; round < 100; round++) {
  const samples = Array.from({ length: 60 }, (_, index) => ({ pid: ["04", "05", "0C", "0D", "0F", "11"][index % 6],
    cycle: Math.floor(index / 6) + 1, value: round * 60 + index, synthetic: true,
    capturedUtc: new Date(1700000000000 + round * 5000 + Math.floor(index / 6) * 500).toISOString() }));
  engineBatch.append({ runId: `synthetic-${round}`, status: "completed", simulation: true, engine: { samples },
    syntheticEvidence: "x".repeat(100000) });
}
const engineSnapshot = engineBatch.export();
assert.equal(engineSnapshot.samples.length, 6000); assert.equal(engineSnapshot.sampleLimit, null);
assert.equal(engineSnapshot.samples.at(-1).cycle, 1000); assert.equal(engineSnapshot.samples.at(-1).elapsedMs, 499500);
engineBatch.append({ runId: "after-snapshot", simulation: true, engine: { samples: [{ pid: "0C", cycle: 1, value: 6000 }] } });
assert.equal(engineSnapshot.samples.length, 6000, "export freezes the selected batch, including array lengths");
const engineFile = path.join(directory, `SYNTHETIC-complete-standard-${Date.now()}.json`);
const engineDeps = { dialog: { async showSaveDialog() { return { canceled: false, filePath: engineFile }; } }, openFile: fs.open };
assert.equal((await saveEngineRecording(engineSnapshot, { ...engineDeps,
  dialog: { async showSaveDialog() { return { canceled: true }; } } })).canceled, true);
await assert.rejects(fs.stat(engineFile), /ENOENT/);
for (const failure of ["EIO", "short", "close"]) {
  let closes = 0;
  const failed = await saveEngineRecording(engineSnapshot, { ...engineDeps, async openFile() { return {
    async write(_bytes, _offset, length) { if (failure === "EIO") throw Object.assign(new Error(), { code: "EIO" }); return { bytesWritten: failure === "short" ? 0 : length }; },
    async sync() {},
    async close() { closes++; if (failure === "close") throw Object.assign(new Error(), { code: "ENOSPC" }); },
  }; } });
  assert.equal(failed.saved, false); assert.ok(closes > 0);
}
const standardSaved = await saveEngineRecording(engineSnapshot, engineDeps);
assert.equal(standardSaved.saved, true); assert.equal(standardSaved.sampleCount, 6000); assert.equal(standardSaved.runCount, 100);
assert.ok((await fs.stat(engineFile)).size > 8 * 1024 * 1024);
assert.deepEqual(JSON.parse(await fs.readFile(engineFile, "utf8")), engineSnapshot);
assert.equal(engineBatch.count, 6001, "cancel/failure/save do not consume the acquisition");
console.log("PASS streamed standard Save As: 6000 samples, >8MiB, frozen complete values/runs, cancel and disk/close failure with complete retry");

// Real filesystem publication failures must preserve an existing saved record.
const publicationRoot = await fs.mkdtemp(path.join(directory, "publication-"));
const previous = Buffer.from('{"previous":"complete saved record"}\n');
const standardSmall = { kind: "engine-acquisition-batch", retentionComplete: true, simulation: true,
  samples: [{ pid: "0C", value: 42 }], runs: [{ runId: "synthetic-publication" }] };
const manufacturerSmall = { kind: "manufacturer-acquisition-rehearsal", retentionComplete: true, simulation: true,
  sampleCount: 1, cycleCount: 1, samples: [{ parameterId: "synthetic", value: 42 }], cycleResults: [{ cycle: 1 }] };
const formats = [["internal", saveInternalCanRecording, small], ["standard", saveEngineRecording, () => structuredClone(standardSmall)],
  ["manufacturer", saveManufacturerRecording, () => structuredClone(manufacturerSmall)],
  ["generic", (recording, deps) => saveRecordingFile({ fileName: "failure.json", recording }, deps), () => structuredClone(standardSmall)]];
let publicationCases = 0;
try {
  for (const [name, save, snapshot] of formats) {
    const target = path.join(publicationRoot, name + ".json");
    for (const failure of ["write", "short", "sync", "close", "publish"]) {
      await fs.writeFile(target, previous); let created, writes = 0, renames = 0;
      const result = await save(snapshot(), {
        dialog: { showSaveDialog: async () => ({ canceled: false, filePath: target }) },
        openFile: async (file, flags) => {
          created = file; const handle = await fs.open(file, flags);
          return {
            write: async (...args) => {
              if (++writes === 2 && failure === "write") throw Object.assign(new Error("injected disk error"), { code: "EIO" });
              if (failure === "short") return { bytesWritten: 0 };
              return handle.write(...args);
            },
            sync: async () => { if (failure === "sync") throw Object.assign(new Error(), { code: "ENOSPC" }); await handle.sync(); },
            close: async () => { await handle.close(); if (failure === "close") throw Object.assign(new Error(), { code: "EIO" }); },
          };
        },
        renameFile: async (from, to) => { renames++; if (failure === "publish") throw Object.assign(new Error(), { code: "EACCES" }); await fs.rename(from, to); },
        unlinkFile: fs.unlink,
      });
      assert.equal(result.saved, false, `${name}/${failure}: publication must fail`);
      assert.deepEqual(await fs.readFile(target), previous, `${name}/${failure}: existing saved record survives`);
      assert.notEqual(created, target); await assert.rejects(fs.stat(created), { code: "ENOENT" });
      assert.equal(renames, failure === "publish" ? 1 : 0);
      publicationCases++;
    }
    await fs.writeFile(target, previous); let opened = 0;
    assert.equal((await save(snapshot(), { dialog: { showSaveDialog: async () => ({ canceled: true }) },
      openFile: async () => { opened++; } })).canceled, true);
    assert.equal(opened, 0); assert.deepEqual(await fs.readFile(target), previous); publicationCases++;
    const saved = await save(snapshot(), { dialog: { showSaveDialog: async () => ({ canceled: false, filePath: target }) }, openFile: fs.open });
    assert.equal(saved.saved, true); assert.equal(saved.filePath, target); assert.equal(JSON.parse(await fs.readFile(target, "utf8")).retentionComplete, true);
    assert.deepEqual((await fs.readdir(publicationRoot)).filter(file => file.endsWith(".tmp")), []); publicationCases++;
    const fresh = path.join(publicationRoot, name + "-new.json");
    assert.equal((await save(snapshot(), { dialog: { showSaveDialog: async () => ({ canceled: false, filePath: fresh }) }, openFile: fs.open })).saved, true);
    assert.equal(JSON.parse(await fs.readFile(fresh, "utf8")).retentionComplete, true); publicationCases++;
    await fs.writeFile(target, previous); let collision, deleted = 0;
    const collided = await save(snapshot(), { dialog: { showSaveDialog: async () => ({ canceled: false, filePath: target }) },
      openFile: async (file, flags) => { collision = file; assert.equal(flags, "wx"); await fs.writeFile(file, "another owner"); return fs.open(file, flags); },
      unlinkFile: async file => { deleted++; await fs.unlink(file); } });
    assert.equal(collided.error, "EEXIST"); assert.equal(deleted, 0); assert.equal(await fs.readFile(collision, "utf8"), "another owner");
    assert.deepEqual(await fs.readFile(target), previous); await fs.unlink(collision); publicationCases++;
    let leftover;
    const cleanupFailed = await save(snapshot(), { dialog: { showSaveDialog: async () => ({ canceled: false, filePath: target }) },
      openFile: async (file, flags) => { leftover = file; const handle = await fs.open(file, flags); return {
        write: async () => { throw Object.assign(new Error(), { code: "EIO" }); }, close: () => handle.close(), sync: () => handle.sync() }; },
      unlinkFile: async () => { throw Object.assign(new Error(), { code: "EACCES" }); } });
    assert.equal(cleanupFailed.error, "EIO", "cleanup cannot hide the original failure");
    assert.deepEqual(await fs.readFile(target), previous); assert.ok(await fs.stat(leftover)); await fs.unlink(leftover); publicationCases++;
  }
} finally { await fs.rm(publicationRoot, { recursive: true, force: true }); }
console.log(`PASS atomic acquisition publication: ${publicationCases} real-filesystem failure/cancel/replacement cases; original files preserved`);
