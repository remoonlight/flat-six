import assert from "node:assert/strict";
import { saveRecordingFile } from "./recording-files.mjs";
const input = { fileName: "obd-gateway.json", recording: { kind: "x431-offline-plan", vehicleDataCollected: false, samples: [] } };
let shown = 0; const writes = [];
const deps = { dialog: { async showSaveDialog(options) { shown++; assert.equal(options.title, "保存此次采集");
  return { canceled: false, filePath: "chosen.json" }; } },
  async writeFile(...args) { writes.push(args); } };
assert.equal((await saveRecordingFile({ ...input, path: "unapproved.json" }, deps)).ok, false);
assert.equal((await saveRecordingFile({ ...input, fileName: "../obd.json" }, deps)).ok, false);
assert.equal((await saveRecordingFile({ ...input, recording: { value: "x".repeat(8 * 1024 * 1024) } }, deps)).error, "recording_too_large");
assert.equal(shown, 0);
const saved = await saveRecordingFile(input, deps); assert.equal(saved.saved, true); assert.equal(saved.filePath, "chosen.json");
assert.deepEqual(JSON.parse(writes[0][1]), input.recording);
assert.equal((await saveRecordingFile(input, { ...deps, dialog: { async showSaveDialog() { return { canceled: true }; } } })).canceled, true);
assert.equal(writes.length, 1);
assert.equal((await saveRecordingFile(input, { ...deps, async writeFile() { throw Object.assign(new Error(), { code: "EACCES" }); } })).error, "EACCES");
console.log("PASS recording Save As: native destination, cancel without write, size/path validation, preserved provenance and write failure");
