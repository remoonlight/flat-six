/** Current four-entry workspace: real Electron/IPC/SQLite + Python simulation, injected connection transport. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = path.resolve(import.meta.dirname, "..");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-obd-workspace-"));
const output = path.join(root, ".local/obd-workspace-accept");
fs.mkdirSync(output, { recursive: true });
const fixture = path.join(temp, "devices.json");
fs.writeFileSync(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_HEADLESS: "1",
  PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_CONNECTION_FIXTURE: fixture, PORSCHE981_CONNECTION_STATE: path.join(temp, "real-connection.json") };
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const errors = [];
const checks = [];
const device = { id: "bt:000000000001", brand: "vLinker", name: "Synthetic adapter", comPort: "COM999",
  available: true, paired: true, transport: "bluetooth-spp" };
const alternateDevice = { ...device, id: "bt:000000000002", brand: "OBDLink MX+", name: "Synthetic alternate adapter", comPort: "COM998" };
let app;
async function open() {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(error.message));
  // Inject at the transport boundary in the test process, without product test hooks.
  await app.evaluate(async ({ ipcMain }, args) => {
    const { EventEmitter } = process.getBuiltinModule("events");
    const { PassThrough } = process.getBuiltinModule("stream");
    const vm = process.getBuiltinModule("vm");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const mgr = createObdConnectionManager({ repoRoot: args.root,
      env: { ...process.env, PORSCHE981_CONNECTION_STATE: args.state },
      listFn: async () => ({ devices: [args.device, args.alternateDevice], errors: [] }),
      livenessMs: 60000,
      monitorSpawnFn: () => {
        const child = new EventEmitter();
        child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
        child.exitCode = null;
        let timer;
        const reading = () => child.stdout.write(JSON.stringify({ ok: true, type: "handshake", volts: 12.6,
          voltageSource: "atrv", simulation: false, deviceId: args.device.id, at: Date.now() }) + "\n");
        child.stdin.once("data", () => { reading(); timer = setInterval(reading, 1000); });
        child.kill = () => { clearInterval(timer); child.exitCode = 0; child.emit("close", 0); return true; };
        return child;
      } });
    globalThis.__workspaceTestConnection = mgr;
    ipcMain.removeHandler(CONNECTION_CHANNEL);
    ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { root, device, alternateDevice, state: path.join(temp, "injected-connection.json"),
    module: pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")).href });
  await page.locator('nav.side button[data-tab="obd"]').click();
  return page;
}
async function close() {
  if (!app) return;
  await app.evaluate(async () => globalThis.__workspaceTestConnection?.shutdown());
  await app.close();
  app = undefined;
}
async function shot(name) {
  const page = await app.firstWindow();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const data = await app.evaluate(async ({ BrowserWindow }) => {
    const contents = BrowserWindow.getAllWindows()[0].webContents;
    return (await contents.capturePage(undefined, { stayHidden: true, stayAwake: true })).toPNG().toString("base64");
  });
  fs.writeFileSync(path.join(output, name), Buffer.from(data, "base64"));
}
try {
  let page = await open();
  assert.deepEqual(await page.locator('[aria-label="OBD 分区"] button').allTextContents(),
    ["连接设置", "系统拓扑", "实时数据", "设码与编程"]);
  for (const retired of ["faults", "session", "offline", "guide", "compare", "broadcast", "developer", "analysis", "vehicle"]) {
    assert.equal(await page.locator(`[data-obd-tab="${retired}"]`).count(), 0);
  }
  // Opening/selecting never transmits. Explicit read/clear clicks execute directly.
  assert.deepEqual(await page.getByTestId("topo-detail").locator("button").allTextContents(),
    ["读取所有单元故障码", "清除所有单元故障码"]);
  assert.equal(await page.getByTestId("topo-detail").locator("dl").count(), 0);
  assert.match(await page.getByTestId("topo-detail").locator("h3").innerText(), /GW/);
  assert.doesNotMatch(await page.locator("body").innerText(), /仅读取 GW、DME 的身份与故障码；其他系统跳过。|此模块尚未接入车辆诊断，可先查看离线资料。/);
  await shot("gw-actions.png");
  await page.getByTestId("topo-clear-all").click();
  assert.equal(await page.getByTestId("topo-confirm").count(), 0);
  assert.equal(await page.getByTestId("topo-cancel").count(), 0);
  await page.waitForFunction(() => !document.querySelector('[data-testid="topo-clear-all"]').disabled
    && document.querySelector('[data-testid="topo-progress"]')?.textContent.includes("失败 1"));
  await page.locator('[data-testid="topo-node-dme"]').first().click();
  assert.equal(await page.getByTestId("topo-overview").count(), 0);
  assert.equal(await page.getByTestId("topo-read-all").count(), 0);
  assert.equal(await page.getByTestId("topo-clear-all").count(), 0);
  assert.equal(await page.getByTestId("topo-capability").count(), 0);
  assert.equal(await page.getByTestId("topo-open-coding").count(), 0);
  assert.equal(await page.getByTestId("topo-adapter").count(), 0);
  assert.equal((await page.evaluate(() => window.porsche981.obdDiag({ op: "snapshot:list" }))).length, 0);
  await page.getByTestId("topo-read-selected").click();
  assert.equal(await page.getByTestId("topo-confirm").count(), 0);
  assert.equal(await page.getByTestId("topo-cancel").count(), 0);
  await page.waitForFunction(() => !document.querySelector('[data-testid="topo-read-selected"]').disabled
    && document.querySelector('[data-testid="topo-progress"]')?.textContent.includes("失败 1"));
  const snapshotsAfterRead = (await page.evaluate(() => window.porsche981.obdDiag({ op: "snapshot:list" }))).length;
  assert.equal(snapshotsAfterRead, 0, "a rejected start has no capture event to save");
  assert.equal(await page.getByTestId("topo-persist-retry").count(), 0);
  assert.doesNotMatch(await page.getByTestId("topo-detail").innerText(), /诊断快照未保存|diag_capture_event_required/);
  await page.getByTestId("topo-clear-selected").click();
  assert.equal(await page.getByTestId("topo-confirm").count(), 0);
  assert.equal(await page.getByTestId("topo-cancel").count(), 0);
  await page.waitForFunction(() => !document.querySelector('[data-testid="topo-clear-selected"]').disabled
    && document.querySelector('[data-testid="topo-progress"]')?.textContent.includes("失败 1"));
  await page.locator('[data-testid="topo-node-pdk"]').first().click();
  assert.equal(await page.getByTestId("topo-read-selected").isDisabled(), true);
  assert.equal(await page.getByTestId("topo-clear-selected").isDisabled(), true);
  await page.locator('[data-testid="topo-node-shaker"]').first().click();
  assert.equal(await page.locator('[data-testid="topo-node-shaker"]').first().getAttribute("data-reference"), "1");
  await page.locator('[data-testid="topo-node-pdk"]').first().click();
  await page.getByTestId("topo-open-live").click();
  await page.getByTestId("offline-realtime").waitFor();
  assert.equal(await page.getByTestId("eng-system").inputValue(), "pdk");
  assert.equal(await page.getByTestId("eng-prepare").count(), 0);
  await page.locator('[data-obd-tab="topology"]').click();
  await page.locator('[data-testid="topo-node-dme"]').first().click();
  await page.getByTestId("topo-view-list").click();
  await page.getByTestId("topo-list").waitFor();
  await page.getByTestId("topo-view-diagram").click();
  await page.getByTestId("topo-diagram").waitFor();
  assert.equal((await page.evaluate(() => window.porsche981.obdDiag({ op: "snapshot:list" }))).length, snapshotsAfterRead);
  await shot("topology.png");
  checks.push("direct read/clear have no confirmation/cancel UI; rejected starts create no snapshot; single-ECU actions, unsupported/reference gates and engine navigation; no implicit scan");

  await page.locator('[data-obd-tab="connection"]').click();
  const radio = page.getByTestId(`obd-device-${device.id}`);
  await radio.check();
  await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-connect"]').disabled);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, false);
  assert.equal(await page.getByTestId("obd-header-device").innerText(), "已连接设备：无");
  await page.getByTestId("obd-conn-connect").click();
  await page.getByTestId("obd-conn-state").getByText("已连接", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="obd-header-voltage"]').textContent.trim() === "电压 12.6 V");
  assert.equal(await page.getByTestId("obd-header-device").innerText(), "已连接设备：vLinker");
  await page.getByTestId(`obd-device-${alternateDevice.id}`).check();
  await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-connect"]').disabled);
  await page.waitForFunction(() => document.querySelector('[data-testid="obd-header-device"]').textContent.trim() === "已连接设备：无");
  const switched = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(switched.selectedDeviceId, alternateDevice.id);
  assert.equal(switched.connected, false);
  assert.equal(await page.locator('input[name="obd-device"]:checked').count(), 1);
  await radio.check();
  await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-connect"]').disabled);
  await page.getByTestId("obd-conn-connect").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="obd-header-device"]').textContent.trim() === "已连接设备：vLinker");
  await page.locator('[data-obd-tab="topology"]').click();
  assert.equal(await page.getByTestId("obd-header-device").innerText(), "已连接设备：vLinker");
  await shot("topology-connected.png");
  await page.locator('[data-obd-tab="connection"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="obd-header-device"]').textContent.trim() === "已连接设备：vLinker");
  await shot("connection.png");
  await page.getByTestId("obd-conn-disconnect").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="obd-header-voltage"]').textContent.includes("12.6"));
  assert.equal(await page.getByTestId("obd-header-device").innerText(), "已连接设备：无");
  checks.push("single-device selection disconnects previous adapter; header reflects explicit connection/disconnection, voltage cleared on disconnect");

  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-select-unit").waitFor();
  assert.equal(await page.getByTestId("eng-start").count(), 0);
  await page.getByTestId("eng-system").selectOption("gateway");
  await page.getByTestId("offline-realtime").waitFor();
  assert.equal(await page.getByTestId("eng-unavailable").count(), 0);
  assert.equal(await page.getByTestId("eng-parameters").count(), 0);
  await page.getByTestId("eng-system").selectOption("dme");
  assert.equal(await page.getByTestId("eng-parameters").locator('input').count(), 6);
  assert.equal(await page.getByTestId("eng-start").count(), 0);
  await page.getByTestId("eng-select-all").click();
  const denied = await page.evaluate(() => window.porsche981.readOnlySession({ action: "start",
    profileId: "porsche-981-2014-dme", mode: "live", sessionTask: "engine", confirmedReadOnly: true, x431Inactive: true }));
  assert.equal(denied.error, "live_not_enabled");
  await page.getByTestId("eng-cycles").fill("1");
  await page.getByTestId("eng-interval").fill("500");
  await page.getByTestId("eng-prepare").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-plan"]').textContent.includes("转速"));
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-kind"]').getAttribute("data-freshness") === "simulated", null, { timeout: 60000 });
  assert.equal(await page.getByTestId("eng-pids").getAttribute("data-live"), "0");
  assert.equal(await page.locator('[data-testid="eng-pids"] > li').count(), 6);
  const exported = path.join(temp, "engine.json");
  await app.evaluate(({ dialog }, filename) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: filename }); }, exported);
  await page.getByTestId("eng-export").click();
  await page.waitForFunction(async () => { await new Promise((r) => setTimeout(r, 100)); return true; });
  const deadline = Date.now() + 5000;
  while (!fs.existsSync(exported) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 50));
  const saved = JSON.parse(fs.readFileSync(exported, "utf8"));
  assert.equal(saved.simulation, true);
  assert.equal(saved.engine.samples.length, 6);
  assert.equal(await page.locator('.eng-chart').count(), 6);
  await page.getByTestId("eng-display-text").click();
  assert.equal(await page.locator('.eng-chart').count(), 0);
  assert.match(await page.getByTestId("eng-pids").innerText(), /2000 rpm/);
  await page.getByTestId("eng-display-graph").click();
  assert.equal(await page.locator('.eng-chart').count(), 6);
  await page.getByTestId("eng-display-both").click();
  await shot("engine.png");
  for (const pid of ["04", "05", "0D", "0F", "11"]) await page.getByTestId(`eng-select-${pid}`).uncheck();
  assert.equal(await page.getByTestId("eng-kind").getAttribute("data-freshness"), "idle");
  assert.equal(await page.locator('.eng-chart').count(), 0);
  await page.getByTestId("eng-cycles").fill("3");
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-kind"]').getAttribute("data-freshness") === "simulated", null, { timeout: 60000 });
  assert.equal(await page.locator('[data-testid="eng-pids"] > li').count(), 1);
  assert.equal(await page.getByTestId("eng-chart-0C").locator('circle').count(), 3);
  const selectedExport = path.join(temp, "engine-selected.json");
  await app.evaluate(({ dialog }, filename) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: filename }); }, selectedExport);
  await page.getByTestId("eng-export").click();
  const selectedDeadline = Date.now() + 5000;
  while (!fs.existsSync(selectedExport) && Date.now() < selectedDeadline) await new Promise((resolve) => setTimeout(resolve, 50));
  const selectedSaved = JSON.parse(fs.readFileSync(selectedExport, "utf8"));
  assert.deepEqual(selectedSaved.engine.selectedPids, ["0C"]);
  assert.deepEqual([...new Set(selectedSaved.engine.samples.map((s) => s.pid))], ["0C"]);
  await page.setViewportSize({ width: 760, height: 900 });
  assert.ok(await page.locator('[data-page="engine-session"]').evaluate((el) => el.scrollWidth <= el.clientWidth));
  await page.getByTestId("eng-pids").scrollIntoViewIfNeeded();
  await shot("engine-selected-narrow.png");
  await page.getByTestId("eng-system").selectOption("gateway");
  assert.equal(await page.getByTestId("eng-pids").count(), 0);
  await page.getByTestId("eng-system").selectOption("dme");
  await page.getByTestId("eng-select-0C").check();
  assert.equal(await page.getByTestId("eng-kind").getAttribute("data-freshness"), "idle");
  await page.getByTestId("eng-scenario").selectOption("slow");
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => document.querySelector('[data-obd-tab="connection"]').disabled);
  assert.equal(await page.getByTestId("eng-system").isDisabled(), true);
  assert.equal(await page.getByTestId("eng-select-0C").isDisabled(), true);
  const busy = await page.evaluate(() => window.porsche981.readOnlySession({ action: "start", profileId: "porsche-981-2014-dme", mode: "simulation" }));
  assert.equal(busy.error, "busy");
  await page.getByTestId("eng-cancel").click();
  await page.waitForFunction(() => !document.querySelector('[data-obd-tab="connection"]').disabled, null, { timeout: 30000 });
  checks.push("controller-first selection, actual selected-PID simulation/export, text/graph views, three timed points, reset on unit/selection change, narrow layout, task locking/cancel, live denied");
  await page.locator('[data-obd-tab="topology"]').click();
  await page.locator('[data-testid="topo-node-dme"]').first().click();
  await page.getByTestId("topo-open-live").click();
  assert.equal(await page.getByTestId("eng-system").inputValue(), "dme");
  assert.equal(await page.getByTestId("eng-start").count(), 0);
  checks.push("topology real-time action opens the selected control unit without starting acquisition");

  await page.locator('[data-obd-tab="coding"]').click();
  await page.getByRole("heading", { name: "请选择系统", exact: true }).waitFor();
  await page.locator('[data-coding-system="dme"]').click();
  assert.equal(await page.locator('[data-coding-category]').count(), 4);
  await page.locator('[data-coding-category="coding"]').click();
  assert.ok(await page.locator('[data-coding-function]').count() > 0);
  await shot("coding.png");
  checks.push("coding workspace system/category navigation; detailed records/PIWIS acceptance separate");
  await close();

  page = await open();
  await page.locator('[data-obd-tab="connection"]').click();
  await page.waitForFunction((id) => document.querySelector(`[data-testid="obd-device-${id}"]`)?.checked, device.id);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, false);
  assert.equal(await page.getByTestId("obd-conn-clear").count(), 0);
  assert.deepEqual(await page.getByTestId("obd-connection").getByRole("button").allTextContents(), ["刷新", "连接设备", "断开设备"]);
  checks.push("restart: selected device without auto-connect; retired faults tab remains absent");
  assert.equal(await page.locator('[data-obd-tab="faults"]').count(), 0);
  await page.setViewportSize({ width: 760, height: 900 });
  assert.ok(await page.locator('[data-page="obd"]').evaluate((el) => el.scrollWidth <= el.clientWidth));
  await shot("narrow.png");
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, noHardware: true,
    connectionTransport: "injected", sessionTransport: "Python simulation", checks, rendererErrors: errors }, null, 2));
  console.log("PASS current four-entry OBD workspace: " + checks.join("; "));
} catch (error) {
  if (app) await shot("failure.png").catch(() => {});
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: false, error: String(error), checks, rendererErrors: errors }, null, 2));
  throw error;
} finally {
  await close();
  // Keep isolated evidence for review. No runtime garage or private device state is changed.
}
