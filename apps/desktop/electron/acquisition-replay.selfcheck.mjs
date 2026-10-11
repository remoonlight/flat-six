import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = fileURLToPath(new URL("../../../", import.meta.url));
const target = process.env.PORSCHE981_ACCEPT_APP_ROOT || root;
const { createAcquisitionReplay, prepareReplay, replayView } = await import(pathToFileURL(path.join(target, "apps/desktop/electron/acquisition-replay.mjs")));
const { saveEngineRecording, saveManufacturerRecording, saveInternalCanRecording } = await import(pathToFileURL(path.join(target, "apps/desktop/electron/recording-files.mjs")));
const output = process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-value-replay-20261010/main-check");
await fs.mkdir(output, { recursive: true });
const stamp = n => new Date(1700000000000 + n).toISOString();
const engine = { kind: "engine-acquisition-batch", simulation: true, retentionComplete: true, batchComplete: false,
  startedAt: stamp(0), endedAt: stamp(600000), stopReason: "time-limit", sourceKind: "synthetic-acceptance",
  syntheticAcceptance: true, vehicleIO: false,
  samples: Array.from({ length: 6000 }, (_, i) => ({ pid: String(i % 2), label: i % 2 ? "转速" : "电压", unit: i % 2 ? "rpm" : "V",
    cycle: Math.floor(i / 2) + 1, value: i, elapsedMs: i < 3000 ? i * 2 : i * 2 + 100000,
    capturedUtc: stamp(i * 2), synthetic: true })), runs: [{ runId: "SYNTHETIC-only", syntheticEvidence: "合".repeat(3 * 1024 * 1024) }] };
let chosen = path.join(output, "SYNTHETIC-standard.json");
const deps = { dialog: { async showSaveDialog() { return { canceled: false, filePath: chosen }; } }, openFile: fs.open };
assert.equal((await saveEngineRecording(engine, deps)).saved, true);
assert.ok((await fs.stat(chosen)).size > 8 * 1024 * 1024);
const openedFiles = [], selections = [];
let canceled = false, nextText = null, release;
const manager = createAcquisitionReplay({ dialog: { async showOpenDialog(options) {
  selections.push(options); return { canceled, filePaths: [chosen] };
} }, async readFile(file) { openedFiles.push(file); if (release) await release.promise; return nextText ?? fs.readFile(file, "utf8"); } });
const a = await manager.handle({ action: "open" }, 11);
assert.equal(a.ok, true); assert.equal(a.summary.sampleCount, 6000); assert.equal(a.summary.durationMs, 111998);
assert.equal(a.summary.simulation, true); assert.equal(a.summary.stopReason, "time-limit");
assert.equal(selections[0].properties[0], "openFile");
let offset = 0, rows = [];
do {
  const page = await manager.handle({ action: "view", recordingId: a.recordingId, parameterIds: ["0", "1"], positionMs: 111998, offset }, 11);
  assert.equal(page.ok, true); assert.ok(page.rows.length <= 100);
  assert.ok(page.series.every(s => s.plottedCount <= 601)); rows.push(...page.rows); offset = page.nextOffset;
} while (offset < 6000);
assert.equal(rows.length, 6000); rows.forEach((row, i) => {
  assert.equal(row.value, i); assert.equal(row.timestamp, stamp(i * 2)); assert.equal(row.cycle, Math.floor(i / 2) + 1);
  assert.equal(row.sourceElapsedMs, engine.samples[i].elapsedMs); assert.equal(row.synthetic, true);
});
const gap = await manager.handle({ action: "view", recordingId: a.recordingId, parameterIds: ["0"], positionMs: 80000, offset: 0 }, 11);
assert.equal(gap.series[0].current.value, 2998); assert.equal(gap.series[0].current.timeMs, 5996);
const subset = await manager.handle({ action: "view", recordingId: a.recordingId, parameterIds: ["1"], positionMs: 111998, offset: 2900 }, 11);
assert.equal(subset.total, 3000); assert.equal(subset.rows.at(-1).value, 5999);
assert.equal((await manager.handle({ action: "view", recordingId: a.recordingId, parameterIds: ["0"], positionMs: 0, offset: 0 }, 22)).error, "replay_not_found");
for (const request of [ { action: "open", filePath: chosen }, { action: "open", recording: engine },
  { action: "view", recordingId: a.recordingId, parameterIds: ["unknown"], positionMs: 0 },
  { action: "view", recordingId: a.recordingId, parameterIds: ["0", "0"], positionMs: 0 },
  { action: "view", recordingId: a.recordingId, parameterIds: ["0"], positionMs: -1 } ])
  assert.equal((await manager.handle(request, 11)).ok, false);
canceled = true; assert.equal((await manager.handle({ action: "open" }, 11)).canceled, true); canceled = false;
assert.equal((await manager.handle({ action: "view", recordingId: a.recordingId, parameterIds: [], positionMs: 0 }, 11)).ok, true);
nextText = "{"; assert.equal((await manager.handle({ action: "open" }, 11)).error, "replay_invalid_json");
nextText = JSON.stringify({ kind: "other", samples: [] }); assert.equal((await manager.handle({ action: "open" }, 11)).error, "replay_unsupported_format"); nextText = null;
assert.equal((await manager.handle({ action: "view", recordingId: a.recordingId, parameterIds: ["0"], positionMs: 0 }, 11)).ok, true);
release = {}; release.promise = new Promise(resolve => { release.resolve = resolve; });
const pending = manager.handle({ action: "open" }, 33);
await new Promise(resolve => setImmediate(resolve));
assert.equal((await manager.handle({ action: "open" }, 33)).error, "replay_busy");
manager.clearOwned(33); release.resolve(); await pending.then(result => assert.equal(result.error, "replay_window_closed")); release = null;

chosen = path.join(output, "SYNTHETIC-manufacturer.json");
const manu = { kind: "manufacturer-acquisition-rehearsal", simulation: true, retentionComplete: true, sourceKind: "synthetic-acceptance",
  syntheticAcceptance: true, vehicleIO: false, sampleCount: 3, cycleCount: 2,
  samples: [ { parameterId: "enum", name: "档位", unit: null, value: "P", display: "驻车", numeric: false, elapsedMs: 10, processedUtc: stamp(10), cycle: 1 },
    { parameterId: "numeric", name: "电压", unit: "V", value: 12.4, display: "12.4", numeric: true, elapsedMs: 10, processedUtc: stamp(10), cycle: 1 },
    { parameterId: "enum", name: "档位", unit: null, value: "D", display: "前进", numeric: false, elapsedMs: 2010, processedUtc: stamp(2010), cycle: 2 } ],
  cycleResults: [{ cycle: 1 }, { cycle: 2 }], termination: { stopReason: "failed", error: "adapter_release_not_verified" } };
assert.equal((await saveManufacturerRecording(manu, deps)).saved, true);
const b = await manager.handle({ action: "open" }, 11);
assert.equal(b.summary.error, "adapter_release_not_verified"); assert.equal(b.summary.durationMs, 2000);
const enumView = await manager.handle({ action: "view", recordingId: b.recordingId, parameterIds: ["enum", "numeric"], positionMs: 2000 }, 11);
assert.equal(enumView.series[0].current.display, "前进"); assert.equal(enumView.series[0].points.length, 0);
assert.equal(enumView.series[1].current.value, 12.4); assert.equal(enumView.rows[0].sourceElapsedMs, 10);
assert.equal((await manager.handle({ action: "discard", recordingId: a.recordingId }, 11)).error, "replay_not_found");
const missing = { ...manu, samples: manu.samples.slice(0, 2) }; assert.throws(() => prepareReplay(missing, "bad.json"), /count_mismatch/);
assert.throws(() => prepareReplay({ ...manu, samples: manu.samples.map((s, i) => i ? s : { ...s, elapsedMs: null }) }, "bad.json"), /invalid_sample/);
assert.throws(() => prepareReplay({ ...manu, samples: manu.samples.map((s, i) => ({ ...s, elapsedMs: i ? 1e308 : -1e308 })) }, "bad-span.json"), /invalid_sample/);
const regress = prepareReplay({ ...manu, samples: manu.samples.map((s, i) => ({ ...s, elapsedMs: i === 2 ? -10 : s.elapsedMs })) }, "backwards.json");
assert.equal(regress.summary.timeRegressed, true); assert.equal(regress.rows[0].index, 0); assert.equal(regress.rows[2].sourceElapsedMs, -10);
const manySamples = Array.from({ length: 35 }, (_, i) => ({ ...manu.samples[1], parameterId: `p${i}`, name: `参数${i}` }));
const many = prepareReplay({ kind: manu.kind, samples: manySamples }, "many.json");
const allIds = many.summary.parameters.map(p => p.id);
assert.equal(replayView(many, { parameterIds: allIds, positionMs: 0 }).total, 35);
assert.equal(replayView(many, { parameterIds: allIds, positionMs: 0 }).series.length, 12);
assert.equal(replayView(many, { parameterIds: allIds, positionMs: 0, seriesOffset: 24 }).series.length, 11);
const spikes = prepareReplay({ kind: manu.kind, samples: Array.from({ length: 3000 }, (_, i) => ({ ...manu.samples[1],
  elapsedMs: i, value: i === 1500 ? 9999 : i === 2000 ? -9999 : 0 })) }, "spikes.json");
const spikeView = replayView(spikes, { parameterIds: ["numeric"], positionMs: 2999 });
assert.ok(spikeView.series[0].points.some(p => p.value === 9999)); assert.ok(spikeView.series[0].points.some(p => p.value === -9999));
chosen = path.join(output, "SYNTHETIC-many.json");
assert.equal((await saveManufacturerRecording({ ...manu, sampleCount: 35, cycleCount: 1,
  samples: manySamples, cycleResults: [{ cycle: 1 }] }, deps)).saved, true);
chosen = path.join(output, "SYNTHETIC-extreme.json");
assert.equal((await saveManufacturerRecording({ ...manu, sampleCount: 2,
  samples: [{ ...manu.samples[1], value: 1e308, display: "1e308" },
    { ...manu.samples[1], value: -1e308, display: "-1e308", elapsedMs: 2010, cycle: 2 }] }, deps)).saved, true);

const eventFlags = { sourceKind: "historical-fixture-rehearsal", simulation: true, executionEnabled: false, liveVerified: false, writePayload: null };
const connectionEvents = Array.from({ length: 53 }, (_, i) => ["interrupted", "retrying", "qualified", "recovered"].map((phase, n) => ({
  ...eventFlags, sequence: i * 4 + n + 1, episode: i + 1, phase, attempt: n ? 1 : 0,
  connectionEpoch: i + (n >= 2 ? 2 : 1), elapsedMs: (i * 4 + n + 1) * 10,
  observedUtc: stamp((i * 4 + n + 1) * 10), reason: n ? null : "transport-disconnected",
}))).flat();
const connected = { ...manu, sourceKind: eventFlags.sourceKind, sampleCount: 2,
  samples: [{ ...manu.samples[1], elapsedMs: 5, connectionEpoch: 1, timestampSource: "host-response-observed" },
    { ...manu.samples[1], elapsedMs: 2150, cycle: 2, connectionEpoch: 54, timestampSource: "host-response-observed" }],
  connectionEventCount: connectionEvents.length, connectionEvents };
chosen = path.join(output, "SYNTHETIC-connection-events.json");
assert.equal((await saveManufacturerRecording(connected, deps)).saved, true);
const connectedRecord = await manager.handle({ action: "open" }, 11);
assert.equal(connectedRecord.summary.connectionEventCount, 212); assert.equal(connectedRecord.summary.durationMs, 2145);
let eventOffset = 0; const replayedEvents = [];
do {
  const page = await manager.handle({ action: "view", recordingId: connectedRecord.recordingId,
    parameterIds: [], positionMs: 25, connectionOffset: eventOffset }, 11);
  assert.equal(page.ok, true); assert.ok(page.connectionEvents.length <= 100);
  assert.equal(page.currentConnectionEvent.phase, "qualified"); assert.equal(page.currentConnectionEvent.sequence, 3);
  assert.equal(page.rows.length, 0); replayedEvents.push(...page.connectionEvents); eventOffset = page.nextConnectionOffset;
} while (eventOffset < 212);
assert.deepEqual(replayedEvents.map(({ timeMs, ...original }) => original), connectionEvents);
assert.equal(replayedEvents[0].timeMs, 5); assert.equal(replayedEvents.at(-1).timeMs, 2115);
const currentView = await manager.handle({ action: "view", recordingId: connectedRecord.recordingId,
  parameterIds: ["numeric"], positionMs: 2145 }, 11);
assert.equal(currentView.currentConnectionEvent.sequence, 212);
assert.deepEqual(currentView.rows.map(row => row.connectionEpoch), [1, 54]);
assert.equal(currentView.rows[0].timestampKind, "host-response-observed");
const eventOnly = { ...connected, sampleCount: 0, cycleCount: 0, samples: [], cycleResults: [],
  termination: { stopReason: "failed", error: "recovery-close-failed" }, connectionEventCount: 2,
  connectionEvents: [{ ...connectionEvents[0], elapsedMs: 100, observedUtc: stamp(100) },
    { ...connectionEvents[0], sequence: 2, phase: "failed", reason: "recovery-close-failed", elapsedMs: 240, observedUtc: stamp(240) }] };
chosen = path.join(output, "SYNTHETIC-events-only.json");
assert.equal((await saveManufacturerRecording(eventOnly, deps)).saved, true);
const eventOnlyRecord = await manager.handle({ action: "open" }, 11);
assert.equal(eventOnlyRecord.summary.sampleCount, 0); assert.equal(eventOnlyRecord.summary.parameters.length, 0);
assert.equal(eventOnlyRecord.summary.connectionEventCount, 2); assert.equal(eventOnlyRecord.summary.durationMs, 140);
assert.equal(eventOnlyRecord.summary.sourceStartElapsedMs, 100);
const failureView = await manager.handle({ action: "view", recordingId: eventOnlyRecord.recordingId,
  parameterIds: [], positionMs: 140 }, 11);
assert.equal(failureView.currentConnectionEvent.phase, "failed"); assert.equal(failureView.series.length, 0);
assert.equal(failureView.rows.length, 0); assert.equal(failureView.connectionEvents[1].elapsedMs, 240);
const trailing = prepareReplay({ ...connected, connectionEventCount: 2, connectionEvents: eventOnly.connectionEvents }, "tail.json");
assert.equal(trailing.summary.durationMs, 2145);
const tailAfterValues = prepareReplay({ ...connected, connectionEventCount: 2,
  connectionEvents: eventOnly.connectionEvents.map((e, i) => ({ ...e, elapsedMs: 3000 + i * 100 })) }, "after-values.json");
assert.equal(tailAfterValues.summary.durationMs, 3095);
const partial = prepareReplay({ ...eventOnly, connectionEventCount: 3, connectionEvents: connectionEvents.slice(0, 3) }, "partial.json");
assert.equal(replayView(partial, { parameterIds: [], positionMs: partial.summary.durationMs }).currentConnectionEvent.phase, "qualified");
chosen = path.join(output, "SYNTHETIC-unconfirmed-recovery.json");
assert.equal((await saveManufacturerRecording({ ...eventOnly, connectionEventCount: 3, connectionEvents: connectionEvents.slice(0, 3),
  termination: { stopReason: "failed", error: "stream_worker_failed" } }, deps)).saved, true);
assert.throws(() => prepareReplay({ ...eventOnly, connectionEventCount: 0, connectionEvents: [] }, "empty.json"), /no_samples/);
const invalidEventDocs = [
  { ...eventOnly, connectionEventCount: 1 }, { ...eventOnly, connectionEventCount: null },
  { ...eventOnly, sourceKind: "independent-manufacturer-read" }, { ...eventOnly, simulation: false },
  { ...eventOnly, connectionEvents: null }, { ...eventOnly, connectionEvents: [null, eventOnly.connectionEvents[1]] },
  ...[
    { phase: "recovered" }, { sequence: 2 }, { elapsedMs: -1 }, { observedUtc: "bad" },
    { connectionEpoch: 0 }, { writePayload: "22" }, { liveVerified: true }, { executionEnabled: true },
    { sourceKind: "independent-manufacturer-read" }, { reason: "unknown" }, { unexpected: "ignored?" },
  ].map(change => ({ ...eventOnly, connectionEvents: [{ ...eventOnly.connectionEvents[0], ...change }, eventOnly.connectionEvents[1]] })),
  { ...eventOnly, connectionEvents: [eventOnly.connectionEvents[0], { ...eventOnly.connectionEvents[1], elapsedMs: 50 }] },
  { ...engine, connectionEvents: [], connectionEventCount: 0 },
];
for (const doc of invalidEventDocs) assert.throws(() => prepareReplay(doc, "invalid-events.json"), /invalid_connection_events/);
for (const connectionOffset of [-1, .5, 3, "0"]) assert.equal(replayView(prepareReplay(eventOnly, "offset.json"),
  { parameterIds: [], positionMs: 0, connectionOffset }).error, "replay_invalid_view");
const validLive = { ...eventOnly, kind: "manufacturer-acquisition-read", simulation: false, sourceKind: "independent-manufacturer-read",
  connectionEvents: eventOnly.connectionEvents.map(e => ({ ...e, simulation: false, sourceKind: "independent-manufacturer-read", executionEnabled: true })) };
assert.equal(prepareReplay(validLive, "live-declared.json").summary.vehicleDataCollected, false);
assert.equal((await manager.handle({ action: "view", recordingId: eventOnlyRecord.recordingId,
  parameterIds: [], positionMs: 0, connectionOffset: 0 }, 22)).error, "replay_not_found");

chosen = path.join(output, "SYNTHETIC-internal.json");
const frames = Array.from({ length: 100001 }, (_, i) => ({ canId: i % 2 ? 0x7FF : 0x18DAF110, extended: !(i % 2), dataHex: "00112233",
  timestampUs: 1700000000000000 + i * 1000, timestampSource: "host-chunk-arrival" }));
assert.equal((await saveInternalCanRecording({ kind: "internal-can-acquisition", frames, frameCount: frames.length,
  retainedFrames: frames.length, receivedTotal: frames.length, retentionComplete: true, simulation: true,
  syntheticAcceptance: true, vehicleIO: false }, deps)).saved, true);
const c = await manager.handle({ action: "open" }, 11);
assert.equal(c.summary.sampleCount, 100001); assert.equal(c.summary.durationMs, 100000);
const can = await manager.handle({ action: "view", recordingId: c.recordingId, parameterIds: c.summary.parameters.map(p => p.id), positionMs: 100000, offset: 100000 }, 11);
assert.equal(can.rows[0].value, "00112233"); assert.equal(can.rows[0].timestamp, stamp(100000));
assert.equal(can.series[0].points.length, 0); assert.equal(can.rows[0].timestampKind, "host-chunk-arrival");
assert.equal(can.rows[0].timestampUs, frames.at(-1).timestampUs);
const micro = prepareReplay({ kind: "internal-can-acquisition", frames: [{ ...frames[0], timestampUs: frames[0].timestampUs + 1 }] }, "micro.json");
assert.equal(replayView(micro, { parameterIds: micro.summary.parameters.map(p => p.id), positionMs: 0 }).rows[0].timestampUs, frames[0].timestampUs + 1);
manager.clearOwned(11); assert.equal((await manager.handle({ action: "discard", recordingId: c.recordingId }, 11)).error, "replay_not_found");
const native = createAcquisitionReplay({ dialog: { async showOpenDialog() { return { canceled: false, filePaths: [path.join(output, "SYNTHETIC-standard.json")] }; } } });
let timerTicks = 0; const clock = setInterval(() => timerTicks++, 1);
const nativeRecord = await native.handle({ action: "open" }, 44); clearInterval(clock);
assert.equal(nativeRecord.summary.sampleCount, 6000); assert.ok(timerTicks > 0); native.clearOwned(44);
const report = { ok: true, moduleRoot: target, noHardware: true, vehicleIO: false, standardSamples: rows.length,
  manufacturerSamples: b.summary.sampleCount, internalFrames: c.summary.sampleCount,
  above8MiB: (await fs.stat(path.join(output, "SYNTHETIC-standard.json"))).size, gapPreserved: true, enumPreserved: true,
  ownerAndNativeChoiceChecked: true, originalFilesUnmodified: true, readerWorker: true, mainTimerTicksDuringOpen: timerTicks };
Object.assign(report, { connectionEventsPaged: replayedEvents.length, eventOnlyFailure: true, eventsIndependentOfSelectedValues: true,
  savedTimesAndEpochsPreserved: true, invalidEventDocumentsRejected: invalidEventDocs.length, partialRecoveryNeverInvented: true });
await fs.writeFile(path.join(output, "result.json"), JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report));
