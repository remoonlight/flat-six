/** Sustained injected receive test. No adapters, live sessions or vehicle values. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { createCanFrameBatch, createCanPcapWriter } from "../apps/desktop/electron/internal-can-data.mjs";

const output = path.resolve(process.argv[2] || ".local/obd-precar-software-20261005/stress");
const relative = path.relative(path.resolve(".local"), output);
if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Private output required");
fs.mkdirSync(output, { recursive: true });
const file = path.join(output, `injected-${Date.now()}.pcapng`);
const batch = createCanFrameBatch();
const writer = createCanPcapWriter(file, "drive", { description: "SYNTHETIC sustained software receive test" });
const started = performance.now();
const durationIndex = process.argv.indexOf("--duration-ms");
const durationMs = durationIndex < 0 ? 0 : Number(process.argv[durationIndex + 1]);
if (!Number.isInteger(durationMs) || durationMs < 0 || durationMs > 60000) throw new Error("Duration must be between 0 and 60000 ms");
const heapSamples = [];
const total = 200000;
for (let offset = 0; offset < total; offset += 500) {
  const frames = Array.from({ length: Math.min(500, total - offset) }, (_, index) => ({
    canId: (offset + index) % 512, extended: false, dataHex: "0102030405060708",
    timestampUs: 1000000 + offset + index, timestampSource: "host-chunk-arrival" }));
  batch.ingest(frames); writer.append(frames);
  if ((offset + frames.length) % 25000 === 0) {
    global.gc?.();
    heapSamples.push({ frames: offset + frames.length, heapBytes: process.memoryUsage().heapUsed });
  }
  if (durationMs) {
    const wait = started + durationMs * (offset + frames.length) / total - performance.now();
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  }
}
writer.finish("injected-test-completed");
const view = batch.view(), exported = batch.export();
assert.equal(view.frameCount, total); assert.equal(exported.frames.length, 25000);
assert.equal(view.latest.length, 256); assert.equal(view.batchClosed, true);
assert.equal(exported.frames[0].timestampUs, 1000000);
assert.equal(exported.frames.at(-1).timestampUs, 1024999);
assert.equal(writer.count, total, "file recording continues after in-memory batch closes");
assert.equal(exported.physicalSilenceVerified, false);
// Verify each block from disk, including total packet count and bounded size.
const data = fs.readFileSync(file); let packets = 0;
for (let offset = 0; offset < data.length;) {
  const size = data.readUInt32LE(offset + 4);
  assert.ok(size >= 12 && size % 4 === 0 && offset + size <= data.length);
  assert.equal(data.readUInt32LE(offset + size - 4), size);
  if (data.readUInt32LE(offset) === 6) packets++;
  offset += size;
}
assert.equal(packets, total);
const heapGrowthBytes = Math.max(...heapSamples.map((s) => s.heapBytes)) - heapSamples[0].heapBytes;
if (global.gc) assert.ok(heapGrowthBytes < 8 * 1024 * 1024, "retained heap must stabilize after the first complete batch");
const result = { ok: true, simulation: true, noDeviceIO: true, framesReceived: total,
  retained: exported.frames.length, latest: view.latest.length, pcapFrames: packets,
  pcapBytes: data.length, elapsedMs: performance.now() - started, durationMs,
  heapSamples, forcedGcForMeasurement: !!global.gc, heapGrowthBytes,
  tested: ["batch-bound", "latest-bound", "earliest-samples-preserved", "recording-continues", "packet-block-integrity"],
  untested: ["overnight-endurance", "physical-silence", "host-sleep", "driver-release", "vehicle-load"] };
fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
