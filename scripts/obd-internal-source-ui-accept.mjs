/** Current source definitions through real renderer/preload/IPC and parameter
 * writer. Adapter worker events are explicitly synthetic; no device IO. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const source = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const root = appRoot || source, output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, ".local/obd-internal-brake-20261017/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), desktop = path.join(temp, "Desktop"); await fs.mkdir(desktop);
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"),
  PORSCHE981_CONNECTION_FIXTURE: fixture, PORSCHE981_CONNECTION_STATE: path.join(temp, "original-state.json"), PYTHONDONTWRITEBYTECODE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const errors = [], checks = [];
const watchdog = setTimeout(() => { console.error("source internal UI timeout"); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [appRoot || path.join(source, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(async ({ ipcMain, app }, args) => {
    const vm = process.getBuiltinModule("vm"), load = name => vm.runInThisContext(`import(${JSON.stringify(args.modules[name])})`,
      { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await load("connection");
    const { createParameterRecordingService } = await load("save");
    const { EventEmitter } = process.getBuiltinModule("events"), { PassThrough } = process.getBuiltinModule("stream");
    const state = globalThis.__internalSource = { clock: 0, controls: [], files: [] }; app.setPath("desktop", args.desktop);
    const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", available: true, paired: true };
    const save = createParameterRecordingService({ desktopPath: () => args.desktop });
    const send = event => state.worker.stdout.write(JSON.stringify({ deviceId: device.id, canNetwork: "drive", simulation: false,
      receiveBatchId: manager.snapshot().internal?.receiveBatchId, ...event }) + "\n");
    const reading = (type, extra = {}) => send({ type, ok: true, commOk: true, volts: 12.6, voltageSource: "atrv", at: Date.now(), receiveState: "receiving", ...extra });
    const manager = createObdConnectionManager({ repoRoot: args.root, elapsedNow: () => state.clock,
      env: { ...process.env, PORSCHE981_CONNECTION_STATE: args.stateFile }, listFn: async () => ({ devices: [device] }), livenessMs: 3600000,
      saveResult: async (recording, owner) => { const result = await save.save(recording, owner); if (result.saved) state.files.push(result.filePath); return result; },
      monitorSpawnFn: () => {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null; state.worker = child;
        child.stdin.on("data", bytes => { for (const line of bytes.toString().trim().split("\n")) {
          const command = JSON.parse(line); state.controls.push(command);
          if (command.action === "monitor") queueMicrotask(() => reading("handshake", { receiveState: "standby" }));
          if (command.action === "receive-start") queueMicrotask(() => reading("reading"));
          if (command.action === "receive-stop") queueMicrotask(() => reading("reading", { receiveState: "standby", receiveReason: command.reason }));
          if (command.action === "stop") { send({ type: "stopped", ok: true, restoration: { errors: [] } }); child.exitCode = 0; child.emit("close", 0); }
        } }); child.kill = () => { child.exitCode = 0; child.emit("close", 0); }; return child;
      } });
    state.manager = manager;
    state.frame = (dataHex = "A5FF803E55AA7DCC", canId = 261) => send({ type: "frames", frames: [{ canId, extended: false, dataHex,
      timestampUs: 1792195200000123 + state.clock * 1000, timestampSource: "host-chunk-arrival" }] });
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (event, request) => manager.handle(request, event.sender.id));
  }, { root, desktop, stateFile: path.join(temp, "model-state.json"), modules: Object.fromEntries(["connection", "save"].map(name => [name,
    pathToFileURL(path.join(root, "apps/desktop/electron", name === "connection" ? "obd-connection.mjs" : "parameter-recording.mjs")).href])) });
  const call = request => page.evaluate(request => window.porsche981.obdConnection(request), request);
  await call({ action: "list" }); await call({ action: "select", deviceId: "bt:AABBCCDDEEFF" });
  await call({ action: "configure", purpose: "internal", canNetwork: "drive" }); await call({ action: "connect" });
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("internal-new-batch").waitFor(); await page.waitForFunction(() => !document.querySelector('[data-testid="internal-new-batch"]')?.disabled);
  await page.getByTestId("internal-new-batch").click(); await app.evaluate(() => { __internalSource.frame(); __internalSource.frame("A5FFCC0B55AA7DCC", 159); __internalSource.frame("A5AA11334455CCDD", 287); });
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("8 条"));
  assert.equal(await page.getByTestId("internal-values").count(), 0);
  const snapshot = await call({ action: "status" });
  assert.deepEqual(snapshot.internal.parameters.map(p => [p.nameEn, p.value, p.unit]), [["Engine_Speed", 4000, "rpm"], ["Throttle_Position", 50, "%"], ["Steering", 100, "%"], ["Brake", 100, "psi"], ["BrakeLF", 10, ""], ["BrakeRF", 30, ""], ["BrakeLR", 40, ""], ["BrakeRR", 50, "%"]]);
  checks.push("real default source loader supplies eight source-bound 981 parameters; no injected definitions or preselected display");
  await page.getByTestId("internal-selection").locator("summary").click();
  await page.getByTestId("internal-select-broadcast-asl-981-brake").check();
  assert.equal(await page.getByTestId("internal-values").locator("dd").count(), 1); assert.match(await page.getByTestId("internal-values").innerText(), /689\.476/);
  await page.getByTestId("internal-display-graph").click(); assert.equal(await page.getByTestId("live-curve-group").count(), 1); assert.match(await page.getByTestId("live-curve-group").innerText(), /689\.476 kPa/);
  checks.push("source brake channel is selectable and shown in mainland kPa in numeric/graph views while all other source parameters keep collecting");
  for (const name of ["brakelf", "brakerf", "brakelr", "brakerr"]) await page.getByTestId("internal-select-broadcast-asl-981-" + name).check();
  assert.equal(await page.getByTestId("live-curve-group").count(), 5);
  assert.equal(await page.getByTestId("live-curve-group").filter({ has: page.locator('[data-parameter-id="broadcast-asl-981-brakerr"]') }).getAttribute("data-unit"), "%");
  for (const name of ["brakelf", "brakerf", "brakelr"]) assert.equal(await page.getByTestId("live-curve-group").filter({ has: page.locator('[data-parameter-id="broadcast-asl-981-' + name + '"]') }).getAttribute("data-unit"), "");
  checks.push("four additional source-named brake inputs are selectable; three unknown units stay empty and separate, BrakeRR retains its source percentage");

  await app.evaluate(() => { __internalSource.clock = 70001; __internalSource.frame("A5FF204E55AA64CC"); __internalSource.frame("A5FFCC8B55AA7DCC", 159); __internalSource.frame("A55522446688CCDD", 287); __internalSource.frame("FFFFFFFFFFFFFFFF", 0x555); });
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-parameter-count"]')?.textContent.includes("16 条"));
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  await page.screenshot({ path: path.join(output, "source-brake-graph.png"), fullPage: true });
  const current = await call({ action: "status" }); assert.equal(current.internal.parameters[0].value, 5000); assert.equal(current.internal.parameters[1].value, 40); assert.equal(current.internal.parameters[2].value, -100); assert.equal(current.internal.parameters[3].value, 50);
  assert.ok(current.internal.parameters[3].segments.flat().every(p => p.elapsedMs >= 10001));
  checks.push("exact source byte locations decode updated RPM/throttle and steering/brake channels; unknown CAN IDs add no parameters; graphs retain only recent sixty seconds");
  await page.getByTestId("internal-stop").click(); await page.waitForFunction(() => !document.querySelector('[data-testid="internal-save"]')?.disabled);
  await page.getByTestId("internal-save").click(); await page.waitForFunction(() => document.querySelector('[data-testid="internal-action-message"]')?.textContent.includes("已保存"));
  const files = await app.evaluate(() => __internalSource.files); assert.equal(files.length, 1); assert.equal(path.dirname(files[0]), desktop);
  const bytes = await fs.readFile(files[0]), rows = [];
  for (let at = 0; at < bytes.length;) { const length = bytes.readUInt32LE(at + 4); assert.ok(length >= 12 && length % 4 === 0); assert.equal(bytes.readUInt32LE(at + length - 4), length);
    if (bytes.readUInt32LE(at) === 6) { const n = bytes.readUInt32LE(at + 20); rows.push(JSON.parse(bytes.subarray(at + 40, at + 28 + n))); } at += length; }
  assert.equal(rows.length, 16); for (const row of rows) assert.deepEqual(Object.keys(row), ["name", "value", "unit", "capturedUtc"]);
  assert.deepEqual(rows.map(p => p.value), [4000, 50, 100, 100 * 6.894757293168361, 10, 30, 40, 50, 5000, 40, -100, 50 * 6.894757293168361, 20, 40, 60, 80]);
  assert.deepEqual(rows.slice(4, 8).map(row => row.unit), ["", "", "", "%"]);
  assert.deepEqual(rows.slice(12, 16).map(row => row.unit), ["", "", "", "%"]);
  assert.equal(rows[3].unit, "kPa"); assert.equal(rows[11].unit, "kPa");
  assert.deepEqual(rows.map(p => p.name), ["发动机转速", "节气门开度", "转向输入", "制动输入", "制动输入（BrakeLF）", "制动输入（BrakeRF）", "制动输入（BrakeLR）", "制动输入（BrakeRR）", "发动机转速", "节气门开度", "转向输入", "制动输入", "制动输入（BrakeLF）", "制动输入（BrakeRF）", "制动输入（BrakeLR）", "制动输入（BrakeRR）"]);
  assert.equal(Date.parse(rows[8].capturedUtc) - Date.parse(rows[0].capturedUtc), 70001);
  await fs.writeFile(path.join(output, "parameter-only.pcapng"), bytes);
  checks.push("desktop PCAPNG preserves full-precision kPa and all sixteen recognized samples, including unselected RPM/throttle/steering and expired graph points, with actual arrival times and parameter-only fields");
  await call({ action: "disconnect" }); assert.equal((await call({ action: "status" })).connected, false);
  assert.deepEqual(errors, []); passed = true;
  const hashes = {}; for (const name of ["apps/desktop/electron/internal-can-data.mjs", "data/seed/obd/internal-signals.v1.json"]) hashes[name] = createHash("sha256").update(await fs.readFile(path.join(root, name))).digest("hex");
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, sourceHashes: hashes, savedSamples: rows.length,
    sourceDefinitionCount: 8, syntheticWorker: true, injectedDefinitions: false, deviceIO: false, vehicleIO: false, windowsExecuted: false }, null, 2) + "\n");
  console.log(JSON.stringify({ passed, checks: checks.length, savedSamples: rows.length, output }));
} finally { clearTimeout(watchdog); if (app) await app.close(); if (passed) await fs.rm(temp, { recursive: true, force: true }); }
