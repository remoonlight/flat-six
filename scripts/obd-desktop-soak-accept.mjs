/** Full Electron/IPC/Python passive stream and standard data, injected BytePort only. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { waitForIpc } from "./obd-accept-helpers.mjs";
import { resolvePythonCandidates } from "../apps/desktop/electron/offline-diagnostics.mjs";

const root = path.resolve(import.meta.dirname, "..");
const durationMs = Number(process.argv[2] || 120000);
assert.ok(Number.isInteger(durationMs) && durationMs >= 10000 && durationMs <= 3600000, "10 s–1 h bounded duration");
const base = path.join(root, ".local/obd-precar-followup-20261005"); fs.mkdirSync(base, { recursive: true });
const output = fs.mkdtempSync(path.join(base, "desktop-soak-"));
console.log(JSON.stringify({ phase: "started", output, soakRequestedMs: durationMs, synthetic: true }));
const fixture = path.join(output, "devices.json"); fs.writeFileSync(fixture, '{"devices":[],"errors":[]}');
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(output, "isolated.db"), PORSCHE981_CONNECTION_FIXTURE: fixture,
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
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000); page.on("pageerror", (e) => errors.push(e.message));
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule("vm"), fs = process.getBuiltinModule("fs"), cp = process.getBuiltinModule("child_process");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createCanPcapWriter } = await vm.runInThisContext(
      `import(${JSON.stringify(args.writerModule)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", label: "SYNTHETIC BytePort", available: true, paired: true };
    let saveFails = false, recordingFails = false, recordingNumber = 0;
    const mgr = createObdConnectionManager({ repoRoot: args.root,
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
        fs.writeFileSync(args.output + "/explicit-result.json", JSON.stringify({ ...data, syntheticAcceptance: true }));
        return { ok: true, saved: true };
      },
      onMonitorEvent: (event) => fs.appendFileSync(args.output + "/lifecycle.jsonl", JSON.stringify(event) + "\n"),
      monitorSpawnFn: (_exe, _argv, opts) => cp.spawn(args.python.exe,
        [...args.python.prefix, "-m", "scripts.diagnostics.tests.precar_stream_worker", args.output], opts) });
    globalThis.__soak = { mgr, failSave: (value) => { saveFails = value; }, failRecording: (value) => { recordingFails = value; } };
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { root, output, python, module: pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")).href,
    writerModule: pathToFileURL(path.join(root, "apps/desktop/electron/internal-can-data.mjs")).href });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-refresh").click(); await page.locator('input[name="obd-device"]').check();
  await page.getByTestId("obd-purpose-drive").check(); await page.getByTestId("obd-conn-connect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).connected);
  await page.locator('[data-obd-tab="live"]').click(); await page.getByTestId("internal-can-panel").waitFor();
  assert.equal(fs.existsSync(path.join(output, "explicit-result.json")), false);
  await page.getByTestId("internal-record-start").click();
  const started = Date.now();
  while (Date.now() - started < durationMs) {
    const state = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
    assert.equal(state.connected, true, JSON.stringify(state.monitorDiagnostics));
    assert.ok(state.internal.retainedFrames <= 25000 && state.internal.latest.length <= 256);
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
  // Disconnect and a delayed worker each recover through production monitor ownership.
  for (const mode of ["disconnect", "stall"]) {
    const before = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
    fs.writeFileSync(path.join(output, "fixture-control.json"), JSON.stringify({ mode }));
    await waitForIpc(page, async (starts) => {
      const status = await window.porsche981.obdConnection({ action: "status" });
      return status.connected && status.monitorDiagnostics.starts > starts;
    }, before.monitorDiagnostics.starts, 20000);
    await waitForIpc(page, async (count) => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > count,
      before.internal.frameCount);
  }
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.retainedFrames === 25000);
  await app.evaluate(() => globalThis.__soak.failSave(true));
  await page.getByTestId("internal-save").click();
  await page.getByTestId("internal-can-panel").getByText("保存失败：synthetic-write-denied", { exact: true }).waitFor();
  const retained = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(retained.internal.retainedFrames, 25000, "failed save retains full batch");
  await app.evaluate(() => globalThis.__soak.failSave(false)); await page.getByTestId("internal-save").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-can-panel"]').textContent.includes("已保存本批结果"));
  const saved = JSON.parse(fs.readFileSync(path.join(output, "explicit-result.json"), "utf8"));
  assert.equal(saved.frames.length, 25000); assert.equal(saved.physicalSilenceVerified, false);
  assert.ok(saved.frames.every((frame) => frame.canId === Number(BigInt("0x" + frame.dataHex) % 512n)));
  await page.getByTestId("internal-new-batch").click();
  await waitForIpc(page, async () => {
    const state = await window.porsche981.obdConnection({ action: "status" });
    return state.internal.frameCount > 0 && state.internal.frameCount < 25000;
  });
  await page.getByTestId("internal-record-stop").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).recording?.active === false);
  const end = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(end.recording.active, false); assert.equal(end.recording.error, undefined);
  const capture = checkPcap(path.join(output, "SYNTHETIC-1.pcapng"), end.recording.frameCount);
  await app.evaluate(() => globalThis.__soak.failRecording(true));
  await page.getByTestId("internal-record-start").click();
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
  // Real Python standard-data sessions plus UI cancellation and complete explicit save.
  await page.getByTestId("obd-purpose-diagnostic").check(); await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme"); await page.getByTestId("eng-select-0C").check();
  await page.getByTestId("eng-cycles").fill("1"); await page.getByTestId("eng-interval").fill("500");
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => /本批保留 ([2-9]|[1-9][0-9]+) 条/.test(document.querySelector('[data-testid="eng-batch-count"]')?.textContent || ""));
  await page.getByTestId("eng-cancel").click(); await page.waitForFunction(() => !document.querySelector('[data-testid="eng-export"]').disabled);
  const engineFile = path.join(output, "standard-synthetic.json");
  await app.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, engineFile);
  await page.getByTestId("eng-export").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-saved"]')?.textContent.includes("已保存"));
  const engine = JSON.parse(fs.readFileSync(engineFile, "utf8"));
  assert.ok(engine.samples.length >= 2 && engine.samples.every((sample) => sample.synthetic && sample.pid === "0C"));
  assert.ok(engine.runs.length >= 2); assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, synthetic: true,
    vehicleVerified: false, physicalSilenceVerified: false, actualOsSleepTested: false,
    durationMs: Date.now() - started, soakRequestedMs: durationMs, pageSwitches: samples.length * 2,
    samples, capture, monitorDiagnostics: closed.monitorDiagnostics, standardSamples: engine.samples.length,
    workerReports, errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, soakRequestedMs: durationMs, capture, standardSamples: engine.samples.length }));
} catch (error) {
  fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({ ok: false, synthetic: true,
    soakRequestedMs: durationMs, error: String(error.stack || error), samples, errors }, null, 2));
  throw error;
} finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(async () => globalThis.__soak?.mgr.shutdown()).catch(() => {}); await app.close(); }
}
