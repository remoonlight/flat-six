/** Real Electron/preload/IPC with isolated state and discovery fixture. No hardware. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { _electron as electron } from "playwright";

const root = path.resolve(import.meta.dirname, "..");
const scratch = path.join(root, ".local", "mxplus-support", "scratch");
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mxplus-electron-"));
const state = path.join(dir, "connection.json");
const fixture = path.join(dir, "discovery.json");
const mx = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", name: "OBDLink MX+", available: true, paired: true, comPort: "COM12" };
const unknown = { ...mx, id: "bt:112233445566", brand: "unresolved", name: "OBDLink", comPort: "COM15" };
fs.writeFileSync(fixture, JSON.stringify({ devices: [mx, unknown], errors: [] }));
const registered = [
  { id: "bt:010203040506", family: "vLinker", name: "vLinker FS BT" },
  { id: "vnci:10001", family: "VNCI", serial: "10001" },
  { id: "pt3g:20002", family: "PT3G", serial: "20002", usbInstanceId: "USB\\VID_0BDA&PID_8152\\CONFIRMED" },
  { id: "x431:test", family: "X431" },
  { id: "tablet:test", family: "X431-tablet" },
  { id: "usb:test", family: "Espressif" },
];
fs.writeFileSync(state.replace(/\.json$/, ".devices.json"), JSON.stringify({ version: 1, devices: registered }));
fs.writeFileSync(fixture, JSON.stringify({ devices: [mx, unknown], errors: [], host: {
  usb: [{ InstanceId: registered[2].usbInstanceId, Status: "OK" }],
  services: [{ Name: "VciToolServerPORSCHE", State: "Running" }],
  drivers: { PT3G: { installed: true, version: "E70" }, VNCI: { installed: true, version: "29.0.0" } },
} }));
const env = {
  ...process.env,
  PORSCHE981_DB: path.join(dir, "garage.db"),
  PORSCHE981_CONNECTION_STATE: state,
  PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_HEADLESS: "1",
  PORSCHE981_DEVTOOLS: "",
};
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const require = createRequire(import.meta.url);
let app;
const errors = [];
async function launch() {
  app = await electron.launch({ executablePath: require("electron"), args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="connection"]').click();
  await page.locator(`[data-testid="obd-device-${mx.id}"]`).waitFor();
  return page;
}
try {
  let page = await launch();
  const linked = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(linked.deviceRegistry.length, 7);
  assert.equal(linked.deviceRegistry.find((d) => d.family === "PT3G").state, "USB 在线");
  assert.equal(linked.deviceRegistry.find((d) => d.family === "PT3G").connectable, false);
  for (const family of ["vLinker", "OBDLink MX+", "VNCI", "PT3G", "X431", "X431-tablet", "Espressif"])
    await page.getByTestId(`obd-registered-${family}`).waitFor();
  await page.locator(`[data-testid="obd-device-${mx.id}"]`).check();
  await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-connect"]').disabled);
  const selection = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(selection.selectedDeviceId, mx.id);
  assert.equal(selection.model, "OBDLink MX+");
  assert.equal(selection.connected, false);
  assert.equal(JSON.parse(fs.readFileSync(state, "utf8")).model, "OBDLink MX+");
  assert.equal(await page.locator('[data-testid="obd-conn-bluetooth"]').isEnabled(), true);
  const forbidden = await page.evaluate(() => window.porsche981.obdConnection({ action: "connect", port: "COM12" }));
  assert.equal(forbidden.error, "forbidden_field");
  await page.locator(`[data-testid="obd-device-${unknown.id}"]`).check();
  await page.locator('[data-testid="obd-model-pick"]').selectOption("OBDLink MX+");
  await page.waitForFunction(async () => (await window.porsche981.obdConnection({ action: "status" })).model === "OBDLink MX+");
  await app.close();
  app = null;
  page = await launch();
  assert.equal(await page.locator(`[data-testid="obd-device-${unknown.id}"]`).isChecked(), true);
  assert.equal(await page.locator('[data-testid="obd-model-pick"]').inputValue(), "OBDLink MX+");
  const restored = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(restored.connected, false);
  assert.equal(restored.voltageVolts, null);
  assert.equal(restored.deviceRegistry.length, 7);
  assert.equal(restored.deviceRegistry.find((d) => d.family === "VNCI").present, false);
  await page.locator('[data-testid="obd-conn-clear"]').click();
  await page.waitForFunction(async () => (await window.porsche981.obdConnection({ action: "status" })).selectedDeviceId === null);
  assert.equal(JSON.parse(fs.readFileSync(state, "utf8")).model, null);
  assert.deepEqual(errors, []);
  console.log("obd-mxplus-electron-accept: PASS real preload/IPC, explicit MX+ selection, manual model, restart, clear, forbidden COM input; no hardware");
} finally {
  await app?.close();
}
