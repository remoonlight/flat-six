/** Sustained injected receive test. No adapters, live sessions or vehicle values. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { createCanFrameBatch, createCanPcapWriter } from "../apps/desktop/electron/internal-can-data.mjs";

const output = path.resolve(process.argv[2] || ".local/obd-precar-software-20261005/stress");
const relative = path.relative(path.resolve(".local"), output);
if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("Private output required");
const durationIndex = process.argv.indexOf("--duration-ms");
const durationMs = durationIndex < 0 ? 0 : Number(process.argv[durationIndex + 1]);
if (!Number.isInteger(durationMs) || durationMs < 0 || durationMs > 86400000) throw new Error("Duration must be between 0 and 86400000 ms");
const frameIndex = process.argv.indexOf("--frame-count");
const total = frameIndex < 0 ? 200000 : Number(process.argv[frameIndex + 1]);
if (!Number.isInteger(total) || total < 25000 || total > 2000000 || total % 500 !== 0) throw new Error("Frame count must be a multiple of 500 between 25000 and 2000000");
fs.mkdirSync(output, { recursive: true });
const file = path.join(output, `injected-${Date.now()}.pcapng`);
const batch = createCanFrameBatch();
const writer = createCanPcapWriter(file, "drive", { description: "SYNTHETIC sustained software receive test" });
const started = performance.now();
const heapSamples = [];
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
const view = batch.view(), exported = batch.export({ stream: true });
assert.equal(view.frameCount, total); assert.equal(exported.retainedFrames, total);
assert.equal(view.latest.length, 256); assert.equal(view.batchClosed, false);
assert.equal(view.retainedBytes, total * 21);
assert.ok(view.allocatedBytes - view.retainedBytes < 4096 * 21);
let decoded = 0;
for (const frame of exported.frames) {
  assert.deepEqual(frame, { canId: decoded % 512, extended: false, dataHex: "0102030405060708",
    timestampUs: 1000000 + decoded, timestampSource: "host-chunk-arrival" }); decoded++;
}
assert.equal(decoded, total, "every packed frame decodes in original order");
assert.equal(writer.count, total, "explicit PCAP and volatile result both retain every received frame");
assert.equal(exported.physicalSilenceVerified, false);
// Verify each block from disk, including total packet count and bounded size.
const fileBytes = fs.statSync(file).size; let packets = 0;
const descriptor = fs.openSync(file, "r"), header = Buffer.alloc(8), trailer = Buffer.alloc(4);
try {
  for (let offset = 0; offset < fileBytes;) {
    assert.equal(fs.readSync(descriptor, header, 0, 8, offset), 8);
    const size = header.readUInt32LE(4);
    assert.ok(size >= 12 && size % 4 === 0 && offset + size <= fileBytes);
    assert.equal(fs.readSync(descriptor, trailer, 0, 4, offset + size - 4), 4);
    assert.equal(trailer.readUInt32LE(), size);
    if (header.readUInt32LE() === 6) packets++;
    offset += size;
  }
} finally {
  fs.closeSync(descriptor);
}
assert.equal(packets, total);
const heapGrowthBytes = Math.max(...heapSamples.map((s) => s.heapBytes)) - heapSamples[0].heapBytes;
if (global.gc) assert.ok(heapGrowthBytes < 8 * 1024 * 1024, "frame payloads live in packed buffers rather than growing the object heap");
const result = { ok: true, simulation: true, noDeviceIO: true, framesReceived: total,
  retained: exported.retainedFrames, retainedBytes: view.retainedBytes, allocatedBytes: view.allocatedBytes,
  latest: view.latest.length, pcapFrames: packets,
  pcapBytes: fileBytes, elapsedMs: performance.now() - started, durationMs,
  heapSamples, forcedGcForMeasurement: !!global.gc, heapGrowthBytes,
  tested: ["all-frames-preserved", "packed-memory", "latest-bound", "explicit-recording-complete", "packet-block-integrity"],
  untested: [...(durationMs < 8 * 3600000 ? ["overnight-endurance"] : []), "physical-silence", "host-sleep", "driver-release", "vehicle-load"] };
fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result));
