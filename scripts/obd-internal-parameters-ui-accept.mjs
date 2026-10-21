/** Production renderer, preload, connection manager, packed batches and PCAPNG
 * writer; private source definitions and synthetic worker events only. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const source = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const root = appRoot || source, output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, ".local/obd-internal-parameters-20261015/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), desktop = path.join(temp, "Desktop"); await fs.mkdir(desktop);
const catalogFile = path.join(root, "data/seed/diagnostic-reference-981/catalog.json"), originalCatalog = await fs.readFile(catalogFile);
assert.equal(JSON.parse(originalCatalog).entries.filter(e => e.kind === "broadcast-signal" && e.decoderReady).length, 0);
const devices = path.join(temp, "devices.json"); await fs.writeFile(devices, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: devices,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "original-state.json"), PYTHONDONTWRITEBYTECODE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
function rows(bytes) {
  const result = [];
  for (let at = 0; at < bytes.length;) {
    const length = bytes.readUInt32LE(at + 4); assert.ok(length >= 12 && length % 4 === 0);
    assert.equal(bytes.readUInt32LE(at + length - 4), length);
    if (bytes.readUInt32LE(at) === 6) {
      const n = bytes.readUInt32LE(at + 20), row = JSON.parse(bytes.subarray(at + 40, at + 28 + n));
      assert.deepEqual(Object.keys(row), ["name", "value", "unit", "capturedUtc"]); result.push(row);
    }
    at += length;
  }
  return result;
}
let app, passed = false; const errors = [], checks = [];
const watchdog = setTimeout(() => { console.error("internal parameter acceptance timeout"); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [appRoot || path.join(source, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(async ({ ipcMain, app }, args) => {
    const vm = process.getBuiltinModule("vm"), load = name => vm.runInThisContext(`import(${JSON.stringify(args.modules[name])})`,
      { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await load("connection");
    const { createParameterRecordingService } = await load("save");
    const { EventEmitter } = process.getBuiltinModule("events"), { PassThrough } = process.getBuiltinModule("stream");
    const fs = process.getBuiltinModule("fs"), state = globalThis.__internalParameters = { clock: 0, deadlines: [], controls: [], retries: [], failSave: true, files: [], calls: [] };
    app.setPath("desktop", args.desktop);
    const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", available: true, paired: true };
    const definition = (id, name, canId, startBit, unit, factor = "0.1") => ({ id, name, canId, startBit,
      extended: false, length: 16, signed: false, byteOrder: "Intel", dlc: 8, factor, offset: "0", unit, sourceQualified: true });
    const signals = [definition("coolant", "冷却液温度", 0x321, 0, "°F"), definition("oil", "机油温度", 0x321, 16, "°C"),
      definition("rpm", "发动机转速", 0x322, 0, "rpm", "1")];
    const saveService = createParameterRecordingService({ desktopPath: () => args.desktop, openFile: async (...values) => {
      if (state.failSave) { state.failSave = false; throw Object.assign(new Error("private disk failure"), { code: "ENOSPC" }); }
      return fs.promises.open(...values);
    } });
    const send = event => state.worker.stdout.write(JSON.stringify({ deviceId: device.id, canNetwork: "drive", simulation: false,
      receiveBatchId: manager.snapshot().internal?.receiveBatchId, ...event }) + "\n");
    const reading = (type = "reading", extra = {}) => send({ type, ok: true, commOk: true, volts: 12.6,
      voltageSource: "atrv", at: Date.now(), receiveState: "receiving", ...extra });
    const manager = createObdConnectionManager({ repoRoot: args.root, elapsedNow: () => state.clock,
      env: { ...process.env, PORSCHE981_CONNECTION_STATE: args.stateFile }, internalSignals: () => signals,
      listFn: async () => ({ devices: [device] }), livenessMs: 3600000, retryDelayMs: () => 57,
      setReceiveTimeoutFn: (fn, ms) => { const timer = { fn, ms }; state.deadlines.push(timer); return timer; },
      clearReceiveTimeoutFn: timer => { timer.cleared = true; },
      setTimeoutFn: (fn, ms) => { if (ms !== 57) return setTimeout(fn, ms); const timer = { fn }; state.retries.push(timer); return timer; },
      clearTimeoutFn: timer => { if (timer?.fn) timer.cleared = true; else clearTimeout(timer); },
      saveResult: async (recording, owner) => {
        const saved = await saveService.save(recording, owner);
        if (saved.saved) state.files.push(saved.filePath); return saved;
      }, monitorSpawnFn: () => {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null;
        state.worker = child;
        child.stdin.on("data", bytes => {
          for (const line of bytes.toString().trim().split("\n")) {
            const command = JSON.parse(line); state.controls.push(command);
            if (command.action === "monitor") queueMicrotask(() => reading("handshake", command.receiveMilliseconds ? {} : { receiveState: "standby" }));
            if (command.action === "receive-start") queueMicrotask(() => reading());
            if (command.action === "receive-stop") state.stopReason = command.reason;
            if (command.action === "stop") { send({ type: "stopped", ok: true, restoration: { errors: [] } }); child.exitCode = 0; child.emit("close", 0); }
          }
        });
        child.kill = () => { child.exitCode = 0; child.emit("close", 0); }; return child;
      } });
    state.manager = manager;
    state.frame = (canId = 0x321, first = 2000, second = 500) => {
      const bytes = Buffer.alloc(8); bytes.writeUInt16LE(first); bytes.writeUInt16LE(second, 2);
      send({ type: "frames", frames: [{ canId, extended: false, dataHex: bytes.toString("hex").toUpperCase(),
        timestampUs: 1792022400000123 + state.clock * 1000, timestampSource: "host-chunk-arrival" }] });
    };
    state.confirmStop = () => reading("reading", { receiveState: "standby", receiveReason: state.stopReason });
    state.drop = () => {
      send({ type: "error", ok: false, error: "port-io:private synthetic disconnect" });
      send({ type: "stopped", ok: true, restoration: { errors: [] } }); state.worker.exitCode = 1; state.worker.emit("close", 1);
    };
    ipcMain.removeHandler(CONNECTION_CHANNEL);
    ipcMain.handle(CONNECTION_CHANNEL, (event, request) => { state.calls.push(request.action); return manager.handle(request, event.sender.id); });
  }, { root, desktop, stateFile: path.join(temp, "fixture-state.json"), modules: Object.fromEntries(["connection", "save"].map(name => [name,
    pathToFileURL(path.join(root, "apps/desktop/electron", name === "connection" ? "obd-connection.mjs" : "parameter-recording.mjs")).href])) });
  const call = request => page.evaluate(request => window.porsche981.obdConnection(request), request);
  await call({ action: "list" }); await call({ action: "select", deviceId: "bt:AABBCCDDEEFF" });
  await call({ action: "configure", purpose: "internal", canNetwork: "drive" }); await call({ action: "connect" });
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("internal-new-batch").waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="internal-new-batch"]')?.disabled);
  assert.equal(await page.getByTestId("internal-display-text").getAttribute("aria-pressed"), "true");
  assert.match(await page.getByTestId("internal-no-selection").innerText(), /尚未选择/);
  await page.getByTestId("internal-new-batch").click();
  await app.evaluate(() => { __internalParameters.frame(); __internalParameters.frame(0x322, 2000); });
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("3 条"));
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  assert.equal(await page.getByTestId("internal-values").count(), 0);
  assert.equal(await page.getByTestId("obd-task-status").getAttribute("data-state"), "running");
  checks.push("no parameter is preselected; acquisition proceeds with no display selection; saving is disabled while receiving");
  await page.getByTestId("internal-selection").locator("summary").click();
  await page.getByTestId("internal-select-coolant").check(); await page.getByTestId("internal-select-oil").check();
  assert.equal(await page.getByTestId("internal-values").locator("dd").count(), 2);
  assert.match(await page.getByTestId("internal-values").innerText(), /93\.3/);
  await page.getByTestId("internal-display-graph").click();
  assert.equal(await page.getByTestId("live-curve-group").count(), 1);
  assert.equal(await page.getByTestId("live-curve-group").getAttribute("data-unit"), "°C");
  assert.equal(await page.locator('[data-curve-id]').count(), 2);
  const batchId = await app.evaluate(() => __internalParameters.manager.snapshot().internal.receiveBatchId);
  await page.locator('[data-obd-tab="connection"]').click();
  await app.evaluate(() => { __internalParameters.clock = 30000; __internalParameters.frame(); });
  await page.locator('[data-obd-tab="live"]').click();
  assert.equal(await page.getByTestId("internal-display-graph").getAttribute("aria-pressed"), "true");
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("5 条"));
  assert.equal(await app.evaluate(() => __internalParameters.manager.snapshot().internal.receiveBatchId), batchId);
  checks.push("display checkboxes can change during reception; numeric values and combined temperature graphs use mainland units; navigation retains selection, graph and batch");
  await app.evaluate(() => { __internalParameters.drop(); });
  await page.getByTestId("internal-reconnect-status").waitFor();
  await app.evaluate(() => { __internalParameters.clock = 70000; __internalParameters.retries.at(-1).fn(); });
  await page.waitForFunction(async () => (await window.porsche981.obdConnection({ action: "status" })).connected === true);
  await app.evaluate(() => { __internalParameters.frame(); });
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("7 条"));
  const recovered = await app.evaluate(() => ({ snapshot: __internalParameters.manager.snapshot(), monitor: __internalParameters.controls.filter(x => x.action === "monitor").at(-1) }));
  assert.equal(recovered.snapshot.internal.receiveBatchId, batchId); assert.equal(recovered.monitor.receiveMilliseconds, 530000);
  assert.equal(recovered.snapshot.internal.parameters[0].segments.length, 2);
  await page.screenshot({ path: path.join(output, "reconnected-parameters.png"), fullPage: true });
  checks.push("automatic reconnection keeps the original batch and remaining ten-minute budget; sixty-second graphs separate pre/post-disconnect segments");
  await page.getByTestId("internal-select-coolant").uncheck(); await page.getByTestId("internal-select-oil").uncheck();
  await page.getByTestId("internal-select-rpm").check();
  await app.evaluate(() => { __internalParameters.clock = 71000; __internalParameters.frame(0x322, 2500); });
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("8 条"));
  await page.getByTestId("internal-select-rpm").uncheck();
  assert.equal(await page.getByTestId("internal-no-selection").count(), 1);
  await page.getByTestId("internal-stop").click();
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  assert.equal(await page.getByTestId("obd-task-status").getAttribute("data-state"), "running");
  assert.equal((await call({ action: "save-result" })).error, "internal_receive_busy");
  await app.evaluate(() => { __internalParameters.clock = 150000; __internalParameters.frame(); __internalParameters.confirmStop(); });
  await page.waitForFunction(() => !document.querySelector('[data-testid="internal-save"]')?.disabled);
  await page.getByTestId("internal-save").click();
  assert.match(await page.getByTestId("internal-action-message").innerText(), /磁盘空间不足/);
  assert.match(await page.getByTestId("internal-parameter-count").innerText(), /8 条/);
  await page.getByTestId("internal-save").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-action-message"]')?.textContent.includes("已保存参数"));
  const [savedFile] = await app.evaluate(() => __internalParameters.files);
  assert.equal(path.dirname(savedFile), desktop);
  const saved = rows(await fs.readFile(savedFile)); assert.equal(saved.length, 8);
  assert.deepEqual([...new Set(saved.map(row => row.name))], ["冷却液温度", "机油温度", "发动机转速"]);
  assert.equal(saved[0].capturedUtc, "2026-10-15T00:00:00.000123Z");
  assert.equal(saved[5].capturedUtc, "2026-10-15T00:01:10.000123Z");
  assert.ok(saved.filter(row => row.name.includes("温度")).every(row => row.unit === "°C"));
  assert.equal(saved[0].value, (200 * 5 / 9 - 32 * 5 / 9));
  assert.ok(Math.abs(saved[0].value - 93.33333333333334) < 1e-10);
  checks.push("stop acknowledgement gates saving; a failed save retains the batch for retry; a parameter-only Desktop PCAPNG contains all recognized parameters regardless of display selection, with original arrival times and full conversion precision");
  await page.getByTestId("internal-select-coolant").check(); await page.getByTestId("internal-select-oil").check();
  assert.equal(await page.getByTestId("internal-display-graph").getAttribute("aria-pressed"), "true");
  const frozen = await app.evaluate(() => __internalParameters.manager.snapshot().internal.plotElapsedMs); assert.equal(frozen, 71000);
  await app.evaluate(() => { __internalParameters.clock = 190000; });
  await page.getByTestId("internal-display-text").click(); assert.equal(await page.getByTestId("internal-values").locator("dd").count(), 2);
  await page.getByTestId("internal-display-graph").click(); assert.equal(await page.getByTestId("live-curve-group").count(), 1);
  assert.equal(await app.evaluate(() => __internalParameters.manager.snapshot().internal.plotElapsedMs), frozen);
  checks.push("saving keeps retained data and graph view; stopped graph time stays frozen when changing views or display parameters");
  await page.getByTestId("internal-new-batch").click();
  assert.equal(await page.getByTestId("internal-display-text").getAttribute("aria-pressed"), "true");
  assert.doesNotMatch(await page.getByTestId("internal-values").innerText(), /°F/);
  await app.evaluate(() => { __internalParameters.frame(0x555); });
  await page.getByTestId("internal-stop").click(); await app.evaluate(() => __internalParameters.confirmStop());
  await page.waitForFunction(() => !document.querySelector('[data-testid="internal-new-batch"]')?.disabled);
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  assert.match(await page.getByTestId("internal-parameter-count").innerText(), /0 条/);
  checks.push("new batches discard previous observations; unknown frames remain backend-only and cannot become parameter files");
  await page.getByTestId("internal-new-batch").click(); await app.evaluate(() => __internalParameters.frame());
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("2 条"));
  await app.evaluate(() => { const s = __internalParameters; s.clock += 600000; s.deadlines.at(-1).fn(); });
  await page.getByTestId("internal-time-limit").waitFor(); assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  await app.evaluate(() => __internalParameters.confirmStop());
  await page.waitForFunction(() => !document.querySelector('[data-testid="internal-save"]')?.disabled);
  assert.equal(await app.evaluate(() => __internalParameters.manager.snapshot().internal.plotElapsedMs), 600000);
  assert.equal(await app.evaluate(() => __internalParameters.manager.snapshot().connected), true);
  assert.equal((await call({ action: "status" })).internal.parameterSampleCount, 2);
  checks.push("the original ten-minute deadline stops reception, waits for confirmation and retains the connected adapter and complete current parameter batch");
  assert.deepEqual(errors, []); assert.deepEqual(await fs.readFile(catalogFile), originalCatalog); passed = true;
} catch (error) { errors.push(error.stack || String(error)); throw error; }
finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(() => __internalParameters?.manager.shutdown()).catch(() => {}); await app.close().catch(() => {}); }
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, errors,
    originalCatalogSha256: createHash("sha256").update(originalCatalog).digest("hex"), sourceDefinitionsInjected: true,
    workerEventsSynthetic: true, currentPublicDefinitionsQualified: false, deviceIO: false, vehicleIO: false }, null, 2) + "\n");
}
