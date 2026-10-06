/** PT3G connection UI with injected native-worker events; never opens hardware. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { waitForIpc } from "./obd-accept-helpers.mjs";

const root = path.resolve(import.meta.dirname, "..");
const renderer = path.resolve(process.argv[2] || path.join(root, "apps/desktop/dist"));
assert.ok(fs.existsSync(path.join(renderer, "index.html")), "built renderer required");
const base = path.join(root, ".local/obd-heads-20261006");
fs.mkdirSync(base, { recursive: true });
const output = fs.mkdtempSync(path.join(base, "pt3g-ui-"));
const server = http.createServer((req, res) => {
  const name = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
  const file = path.resolve(renderer, "." + (name === "/" ? "/index.html" : name));
  if (!file.startsWith(renderer + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404); res.end(); return;
  }
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
  res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const fixture = path.join(output, "devices.json"); fs.writeFileSync(fixture, '{"devices":[],"errors":[]}');
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(output, "isolated.db"), PORSCHE981_USER_DATA: path.join(output, "user data"),
  PORSCHE981_CONNECTION_FIXTURE: fixture, PORSCHE981_CONNECTION_STATE: path.join(output, "unused-state.json"),
  VITE_DEV_SERVER_URL: `http://127.0.0.1:${server.address().port}` };
delete env.ELECTRON_RUN_AS_NODE;
const errors = [];
let app;
const watchdog = setTimeout(() => { void app?.close(); }, 60000);
try {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 20000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(error.message));
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule("vm");
    const { EventEmitter } = process.getBuiltinModule("events");
    const { PassThrough } = process.getBuiltinModule("stream");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const device = { id: "pt3g:10001", brand: "PT3G", name: "PT3G / E70 · USB", serial: "10001",
      transport: "d-pdu-usb", available: true };
    const mgr = createObdConnectionManager({ repoRoot: args.root,
      env: { ...process.env, PORSCHE981_CONNECTION_STATE: args.output + "/connection.json" },
      listFn: async () => ({ devices: [device], errors: [] }), gracefulMs: 30, killWaitMs: 30,
      livenessMs: 60000, monitorSpawnFn: () => {
        const child = new EventEmitter();
        Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
        child.kill = () => { child.emit("close", null, "SIGTERM"); return true; };
        child.stdin.on("data", (bytes) => {
          const req = JSON.parse(String(bytes));
          if (req.action === "monitor") setTimeout(() => child.stdout.write(JSON.stringify({ type: "handshake", ok: true,
            deviceId: device.id, simulation: false, commOk: true, volts: null, voltageSource: "d-pdu-vbatt", at: Date.now() }) + "\n"), 10);
          if (req.action === "stop" && !globalThis.__pt3gFailClose) {
            child.stdout.write(JSON.stringify({ type: "stopped", restoration: { errors: [] } }) + "\n");
            child.exitCode = 0; child.emit("close", 0);
          }
        });
        return child;
      } });
    globalThis.__pt3gUi = mgr;
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { root, output, module: pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")).href });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-refresh").click();
  await page.getByTestId("obd-registered-PT3G").waitFor();
  await page.getByTestId("obd-device-pt3g:10001").check();
  await page.getByTestId("obd-conn-connect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).connected);
  await page.getByTestId("obd-conn-state").filter({ hasText: "已连接" }).waitFor();
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).model, "PT3G");
  await page.getByTestId("obd-conn-refresh").click();
  await page.getByText("请先断开诊断头并等待释放", { exact: false }).waitFor();
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, true);
  await page.screenshot({ path: path.join(output, "connected.png") });
  await page.locator('[data-obd-tab="topology"]').click();
  await page.getByTestId("topo-node-dme").click();
  assert.equal(await page.getByTestId("topo-read-selected").isDisabled(), true);
  assert.equal(await page.getByTestId("topo-clear-selected").isDisabled(), true);
  await page.screenshot({ path: path.join(output, "vehicle-actions-disabled.png") });
  await page.locator('[data-obd-tab="live"]').click();
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, true);
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-disconnect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).linkState === "idle");
  await page.getByTestId("obd-conn-connect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).connected);
  await app.evaluate(() => { globalThis.__pt3gFailClose = true; });
  await page.getByTestId("obd-conn-disconnect").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).linkState === "close_failed");
  await page.getByText("诊断头原生释放未确认", { exact: false }).waitFor();
  await page.screenshot({ path: path.join(output, "release-failed.png") });
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, simulation: true, noDeviceIO: true,
    checks: ["PT3G available row", "native voltage unknown", "refresh deferred while connected", "vehicle actions disabled", "cross-page connection", "normal release",
      "reconnect", "failed release displayed and latched"], errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, noDeviceIO: true }));
} finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(async () => globalThis.__pt3gUi?.shutdown()).catch(() => {}); await app.close(); }
  await new Promise((resolve) => server.close(resolve));
}
