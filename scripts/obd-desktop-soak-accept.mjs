/** Full Electron/IPC/Python passive stream and standard data, injected BytePort only. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { waitForIpc } from "./obd-accept-helpers.mjs";
import { resolvePythonCandidates } from "../apps/desktop/electron/offline-diagnostics.mjs";

const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const durationMs = Number(process.argv[2] || 120000);
assert.ok(Number.isInteger(durationMs) && durationMs >= 10000 && durationMs <= 300000, "10 s–5 min receive phase leaves time for recovery/save checks within the ten-minute batch");
const base = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-precar-followup-20261005")); fs.mkdirSync(base, { recursive: true });
const output = fs.mkdtempSync(path.join(base, "desktop-soak-"));
const desktop = path.join(output, "Desktop"); fs.mkdirSync(desktop);
console.log(JSON.stringify({ phase: "started", output, soakRequestedMs: durationMs, synthetic: true }));
const fixture = path.join(output, "devices.json"); fs.writeFileSync(fixture, '{"devices":[],"errors":[]}');
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PYTHONDONTWRITEBYTECODE: "1",
  PORSCHE981_DB: path.join(output, "isolated.db"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_USER_DATA: path.join(output, "user-data"), PORSCHE981_LOCAL_ROOT: path.join(output, "local-assets"),
  PORSCHE981_CAN_CAPTURE_ROOT: path.join(output, "raw-captures"),
  PORSCHE981_CONNECTION_STATE: path.join(output, "unused-connection.json"),
  PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(output, "transient-sessions") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let python;
for (const candidate of resolvePythonCandidates(env)) {
  const probe = spawnSync(candidate.exe, [...candidate.prefix, "-c", "import sys;print(sys.executable)"],
    { env, encoding: "utf8", windowsHide: true, timeout: 10000 });
  if (probe.status === 0 && probe.stdout.trim()) { python = { exe: probe.stdout.trim(), prefix: ["-X", "utf8"] }; break; }
}
assert.ok(python, "direct CPython executable required for owned lifecycle evidence");
let app;
const errors = [], samples = [];
const watchdog = setTimeout(() => { console.error("desktop soak total timeout"); void app?.close(); }, durationMs + 180000);
function checkPcap(file, expectedCount) {
  const fd = fs.openSync(file, "r"), header = Buffer.alloc(8), tail = Buffer.alloc(4), packet = Buffer.alloc(36);
  let offset = 0, packets = 0;
  try {
    const bytes = fs.fstatSync(fd).size;
    while (offset < bytes) {
      assert.equal(fs.readSync(fd, header, 0, 8, offset), 8);
      const length = header.readUInt32LE(4); assert.ok(length >= 12 && length <= 65536 && length % 4 === 0);
      assert.ok(offset + length <= bytes);
      assert.equal(fs.readSync(fd, tail, 0, 4, offset + length - 4), 4);
      assert.equal(tail.readUInt32LE(), length);
      if (header.readUInt32LE() === 6) {
        assert.equal(fs.readSync(fd, packet, 0, 36, offset + 8), 36);
        assert.equal(packet.readUInt32LE(12), 16); assert.equal(packet.readUInt32LE(16), 16);
        assert.equal(packet[24], 8);
        assert.equal(packet.readUInt32BE(20), Number(packet.readBigUInt64BE(28) % 512n));
        packets++;
      }
      offset += length;
    }
    assert.equal(packets, expectedCount);
    return { packets, bytes };
  } finally { fs.closeSync(fd); }
}
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? path.join(root, "apps/desktop/electron/main.mjs") : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000); page.on("pageerror", (e) => errors.push(e.message));
  await app.evaluate(({ app }, desktop) => app.setPath("desktop", desktop), desktop);
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule("vm"), fs = process.getBuiltinModule("fs"), cp = process.getBuiltinModule("child_process");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createCanPcapWriter } = await vm.runInThisContext(
      `import(${JSON.stringify(args.writerModule)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { saveInternalCanRecording } = await vm.runInThisContext(
      `import(${JSON.stringify(args.saveModule)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", label: "SYNTHETIC BytePort", available: true, paired: true };
    let saveFails = false, recordingFails = false, recordingNumber = 0, clockOffset = 0;
    const mgr = createObdConnectionManager({ repoRoot: args.root, elapsedNow: () => performance.now() + clockOffset,
      env: { ...process.env, PORSCHE981_CONNECTION_STATE: args.output + "/connection.json" },
      listFn: async () => ({ devices: [device] }), retryDelayMs: () => 100,
      chooseRecordingFile: async () => args.output + `/SYNTHETIC-${++recordingNumber}.pcapng`,
      createRecordingWriter: (file, network) => {
        const writer = createCanPcapWriter(file, network, { description: "SYNTHETIC injected BytePort desktop acceptance" });
        const append = writer.append.bind(writer);
        writer.append = (frames) => {
          if (recordingFails) { const error = new Error("synthetic disk full"); error.code = "ENOSPC"; throw error; }
          return append(frames);
        };
        return writer;
      },
      saveResult: async (data) => {
        if (saveFails) return { ok: false, error: "synthetic-write-denied" };
        return saveInternalCanRecording({ ...data, syntheticAcceptance: true }, {
          dialog: { showSaveDialog: async () => ({ canceled: false, filePath: args.output + "/explicit-result.json" }) },
          openFile: fs.promises.open,
        });
      },
      onMonitorEvent: (event) => fs.appendFileSync(args.output + "/lifecycle.jsonl", JSON.stringify(event) + "\n"),
      monitorSpawnFn: (_exe, _argv, opts) => cp.spawn(args.python.exe,
        [...args.python.prefix, "-B", args.fixtureWorker, args.output], { ...opts, env: { ...opts.env, PYTHONPATH: args.root } }) });
    globalThis.__soak = { mgr, failSave: (value) => { saveFails = value; }, failRecording: (value) => { recordingFails = value; },
      expireBatch: () => { clockOffset += 600000; return mgr.snapshot(); } };
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { root, output, python, fixtureWorker: path.join(import.meta.dirname, "diagnostics/tests/precar_stream_worker.py"),
    module: pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")).href,
    writerModule: pathToFileURL(path.join(root, "apps/desktop/electron/internal-can-data.mjs")).href,
    saveModule: pathToFileURL(path.join(root, "apps/desktop/electron/recording-files.mjs")).href });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-refresh").click(); await page.locator('input[name="obd-device"]').check();
  await page.getByTestId("obd-purpose-drive").check(); await page.getByTestId("obd-conn-connect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).connected);
  await page.locator('[data-obd-tab="live"]').click(); await page.getByTestId("internal-can-panel").waitFor();
  assert.equal(fs.existsSync(path.join(output, "explicit-result.json")), false);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "record-start" }))).ok, true);
  const started = Date.now();
  while (Date.now() - started < durationMs) {
    const state = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
    assert.equal(state.connected, true, JSON.stringify(state.monitorDiagnostics));
    assert.equal(state.internal.retainedFrames, state.internal.frameCount);
    assert.equal(state.internal.frameLimit, null);
    assert.ok(state.internal.latest.length <= 256);
    const memory = await app.evaluate(() => process.memoryUsage());
    const processes = await app.evaluate(({ app }) => app.getAppMetrics().map((metric) => ({
      type: metric.type, workingSetBytes: metric.memory.workingSetSize * 1024,
      privateBytes: metric.memory.privateBytes == null ? null : metric.memory.privateBytes * 1024,
    })));
    samples.push({ elapsedMs: Date.now() - started, frames: state.internal.frameCount, retained: state.internal.retainedFrames,
      mainRssBytes: memory.rss, mainHeapBytes: memory.heapUsed, processes });
    fs.writeFileSync(path.join(output, "progress.json"), JSON.stringify({ phase: "receiving", synthetic: true,
      soakRequestedMs: durationMs, sample: samples.at(-1), pageSwitches: samples.length * 2 }, null, 2));
    await page.locator('[data-obd-tab="coding"]').click();
    const previous = state.internal.frameCount;
    await waitForIpc(page, async (count) => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > count, previous);
    await page.locator('[data-obd-tab="live"]').click(); await page.getByTestId("internal-can-panel").waitFor();
    await new Promise((resolve) => setTimeout(resolve, Math.min(5000, Math.max(1, durationMs - (Date.now() - started)))));
  }
  // An orderly disconnect with a release report can recover through monitor ownership.
  for (const mode of ["disconnect"]) {
    const before = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
    fs.writeFileSync(path.join(output, "fixture-control.json"), JSON.stringify({ mode }));
    await waitForIpc(page, async (starts) => {
      const status = await window.porsche981.obdConnection({ action: "status" });
      return status.connected && status.monitorDiagnostics.starts > starts;
    }, before.monitorDiagnostics.starts, 20000);
    await waitForIpc(page, async (count) => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > count,
      before.internal.frameCount);
  }
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.retainedFrames > 25000);
  const expired = await app.evaluate(() => globalThis.__soak.expireBatch());
  assert.equal(expired.internal.stopReason, "time-limit");
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.receiveState === "standby");
  const fullCount = expired.internal.retainedFrames;
  assert.ok(fullCount > 25000);
  assert.equal(expired.internal.frameCount, fullCount);
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true, "unqualified raw frames are not product parameter exports");
  // Retain the developer raw-frame completeness check through the existing injected callback.
  await app.evaluate(() => globalThis.__soak.failSave(true));
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "save-result" }))).saved.error, "synthetic-write-denied");
  const retained = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(retained.internal.retainedFrames, fullCount, "failed save retains full batch after deadline");
  assert.equal(retained.connected, true, "time limit retains the adapter connection");
  await app.evaluate(() => globalThis.__soak.failSave(false));
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "save-result" }))).saved.ok, true);
  const saved = JSON.parse(fs.readFileSync(path.join(output, "explicit-result.json"), "utf8"));
  assert.equal(saved.frames.length, fullCount); assert.equal(saved.receivedTotal, fullCount);
  assert.equal(saved.stopReason, "time-limit"); assert.equal(saved.physicalSilenceVerified, false);
  assert.ok(saved.frames.every((frame) => frame.canId === Number(BigInt("0x" + frame.dataHex) % 512n)));
  await page.getByTestId("internal-new-batch").click();
  await waitForIpc(page, async () => {
    const state = await window.porsche981.obdConnection({ action: "status" });
    return state.internal.frameCount > 0 && state.internal.frameCount < 25000;
  });
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).recording?.active === false);
  const end = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(end.recording.active, false); assert.equal(end.recording.error, undefined);
  assert.equal(end.recording.reason, "time-limit", "deadline already closed the recording before the manual next batch");
  assert.equal(await page.getByTestId("internal-record-stop").count(), 0);
  const capture = checkPcap(path.join(output, "SYNTHETIC-1.pcapng"), end.recording.frameCount);
  await app.evaluate(() => globalThis.__soak.failRecording(true));
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "record-start" }))).ok, true);
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).recording?.error === "ENOSPC");
  const failedRecording = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(failedRecording.connected, true); assert.equal(failedRecording.recording.active, false);
  await waitForIpc(page, async (count) => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > count,
    failedRecording.internal.frameCount);
  await page.locator('[data-obd-tab="connection"]').click(); await page.getByTestId("obd-conn-disconnect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).linkState === "idle");
  const closed = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(closed.linkState, "idle");
  assert.equal(await app.evaluate(() => globalThis.__soak.mgr.gate.owner()), null);
  const workerReports = fs.readdirSync(output).filter((name) => /^worker-\d+\.json$/.test(name)).map((name) => JSON.parse(fs.readFileSync(path.join(output, name))));
  assert.ok(workerReports.length >= 2);
  assert.ok(workerReports.every((report) => report.closed && report.serialOpens === 0 && report.ecuRequestsSent === 0));
  assert.ok(workerReports.every(report => path.resolve(report.productionInternalStreamPath) === path.join(root, "scripts/diagnostics/internal_stream.py")
    && path.resolve(report.productionCanMonitorPath) === path.join(root, "scripts/diagnostics/can_monitor.py")), "worker uses the selected source/delivery production modules");
  // Real Python standard-data sessions plus UI cancellation and complete explicit save.
  await page.getByTestId("obd-purpose-diagnostic").check(); await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme"); await page.getByTestId("eng-select-0C").check(); await page.getByTestId("eng-mode").selectOption("simulation");
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => /本批保留 ([2-9]|[1-9][0-9]+) 条/.test(document.querySelector('[data-testid="eng-batch-count"]')?.textContent || ""));
  await page.getByTestId("eng-cancel").click(); await page.waitForFunction(() => !document.querySelector('[data-testid="eng-export"]').disabled);
  const engineRetained = Number((await page.getByTestId("eng-batch-count").innerText()).match(/本批保留 (\d+)/)[1]);
  await page.getByTestId("eng-export").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-saved"]')?.textContent.includes("已保存"));
  const files = fs.readdirSync(desktop); assert.equal(files.length, 1);
  assert.match(files[0], /^SYNTHETIC-DME-\d{8}T\d{6}Z-parameters.*\.pcapng$/);
  const engineBytes = fs.readFileSync(path.join(desktop, files[0])), engineSamples = [];
  for (let offset = 0; offset < engineBytes.length;) {
    const length = engineBytes.readUInt32LE(offset + 4);
    assert.ok(length >= 12 && length % 4 === 0 && offset + length <= engineBytes.length);
    assert.equal(engineBytes.readUInt32LE(offset + length - 4), length);
    if (engineBytes.readUInt32LE(offset) === 6) {
      const captured = engineBytes.readUInt32LE(offset + 20);
      engineSamples.push(JSON.parse(engineBytes.subarray(offset + 40, offset + 28 + captured).toString("utf8")));
    }
    offset += length;
  }
  assert.equal(engineSamples.length, engineRetained); assert.ok(engineSamples.length >= 2);
  for (const sample of engineSamples) {
    assert.deepEqual(Object.keys(sample), ["name", "value", "unit", "capturedUtc"]);
    assert.equal(sample.name, "转速");
    assert.equal(sample.unit, "rpm"); assert.ok(Number.isFinite(sample.value)); assert.ok(Number.isFinite(Date.parse(sample.capturedUtc)));
  }
  assert.doesNotMatch(engineBytes.toString(), /requestHex|pduHex|transactions|sourceRunId/);
  // A forced watchdog termination has no verified release report and must stop, never reconnect.
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-purpose-drive").check(); await page.getByTestId("obd-conn-connect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).connected);
  const beforeStall = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  fs.writeFileSync(path.join(output, "fixture-control.json"), JSON.stringify({ mode: "stall" }));
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).linkState === "close_failed", undefined, 15000);
  const stalled = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(stalled.connected, false);
  assert.equal(stalled.monitorDiagnostics.starts, beforeStall.monitorDiagnostics.starts);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "connect" }))).error, "adapter_release_not_verified");
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, synthetic: true,
    vehicleVerified: false, physicalSilenceVerified: false, actualOsSleepTested: false,
    durationMs: Date.now() - started, soakRequestedMs: durationMs, pageSwitches: samples.length * 2,
    samples, capture, internalSavedFrames: fullCount, internalResultBytes: fs.statSync(path.join(output, "explicit-result.json")).size,
    injectedDeadline: true, monitorDiagnostics: closed.monitorDiagnostics, standardSamples: engineSamples.length,
    stalledWorkerStoppedWithoutReconnect: true, stalledState: stalled, workerReports, errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, soakRequestedMs: durationMs, capture, standardSamples: engineSamples.length }));
} catch (error) {
  fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({ ok: false, synthetic: true,
    soakRequestedMs: durationMs, error: String(error.stack || error), samples, errors }, null, 2));
  throw error;
} finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(async () => globalThis.__soak?.mgr.shutdown()).catch(() => {}); await app.close(); }
}
