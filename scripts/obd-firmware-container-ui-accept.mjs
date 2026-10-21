/** Current firmware page + actual preparation IPC/Python parser; owned pickers, no head/car. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";

const projectRoot = path.resolve(import.meta.dirname, ".."), root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || projectRoot);
if (!process.env.PORSCHE981_FIRMWARE_TEST_INPUT) throw new Error("Set PORSCHE981_FIRMWARE_TEST_INPUT to the retained, read-only firmware test input directory.");
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(projectRoot, ".local/firmware-container-accept"));
const inputs = path.resolve(process.env.PORSCHE981_FIRMWARE_TEST_INPUT);
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "ui-"));
const legacy = JSON.parse(await fs.readFile(path.join(inputs, "expected.json"), "utf8")).dme;
const devices = path.join(temp, "devices.json"); await fs.writeFile(devices, JSON.stringify({ devices: [], errors: [] }));
const hash = async file => createHash("sha256").update(await fs.readFile(file)).digest("hex");
const before = await hash(path.join(inputs, "dme.pdx"));
const { runWorkbench } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/offline-diagnostics.mjs")).href);
const inspected = await runWorkbench({ action: "firmware-container" }, { repoRoot: root, firmwareFile: path.join(inputs, "dme.pdx"), timeoutMs: 60000 });
assert.equal(inspected.ok, true, JSON.stringify(inspected));
// Keep the retained fixture intact. Current identifier/reference details are
// compared through the standalone Python entry and the normal Electron IPC.
assert.deepEqual(inspected.container.file, legacy.file);
assert.deepEqual(inspected.container.counts, legacy.counts);
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"),
  PORSCHE981_DB: path.join(temp, "test.db"), PORSCHE981_CONNECTION_FIXTURE: devices,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection-state.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
const errors = [], checks = []; let app, passed = false, saved;
const watchdog = setTimeout(() => { console.error("firmware IPC acceptance timeout"); void app?.close(); }, 120000);
try {
  app = await electron.launch({ executablePath: process.env.PORSCHE981_ACCEPT_ELECTRON_EXECUTABLE,
    args: [...(process.platform === "win32" ? ["--disable-gpu"] : []), process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000); page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(({ dialog }, args) => {
    const path = process.getBuiltinModule("path");
    const files = ["dme-manifest.json", "dme.pdx", "bad-manifest.json", "bad.pdx", "dme-manifest.json", "dme.pdx", null];
    const state = globalThis.__firmwarePickers = { opens: 0, saves: 0 };
    dialog.showOpenDialog = async () => { state.opens++; const file = files.shift(); return file ? { canceled: false, filePaths: [path.join(args.inputs, file)] } : { canceled: true, filePaths: [] }; };
    dialog.showSaveDialog = async () => { state.saves++; return { canceled: false, filePath: path.join(args.temp, "firmware-preparation.json") }; };
  }, { inputs, temp });
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click(); await page.locator('[data-coding-category="programming"]').click();
  await page.locator('[data-coding-function="workshop-program-dme"]').click();
  await page.getByTestId("firmware-version-unfinished").waitFor();
  assert.equal(await page.getByRole("button", { name: "确认刷写(未完成)", exact: true }).isDisabled(), true);
  for (const name of ["选择并校验原厂固件", "保存固件准备清单", "生成准备清单", "保存准备清单"]) assert.equal(await page.getByRole("button", { name, exact: true }).count(), 0);
  assert.equal(await page.getByTestId("firmware-container-inspection").count(), 0);
  assert.equal(await page.getByLabel("研究规则", { exact: true }).count(), 0);
  assert.deepEqual(await app.evaluate(() => globalThis.__firmwarePickers), { opens: 0, saves: 0 });
  await page.screenshot({ path: path.join(temp, "firmware-page.png"), fullPage: true });
  checks.push("current page keeps unmatched flashing unfinished and disabled; retired file/container/preparation controls are absent and browsing opens no picker");
  const request = action => page.evaluate(action => window.porsche981.diagnosticPreparation({ action, ecu: "dme" }), action);
  const first = await request("prepare-firmware"); assert.equal(first.ok, true, JSON.stringify(first));
  assert.deepEqual(first.preparation.container, inspected.container);
  assert.equal(first.preparation.executionEnabled, false); assert.equal(first.preparation.writePayload, null);
  assert.equal((await request("export-firmware")).saved, true);
  saved = JSON.parse(await fs.readFile(path.join(temp, "firmware-preparation.json"), "utf8"));
  assert.equal(saved.file.sha256, before); assert.equal(saved.hashMatches, true);
  assert.equal(saved.executionEnabled, false); assert.equal(saved.writePayload, null);
  assert.deepEqual(saved.transportRequirement.targetFamilies, ["VNCI", "PT3G"]);
  assert.equal(saved.transportRequirement.wiredConnectionVerified, false);
  assert.deepEqual(saved.container, inspected.container, "compatibility export preserves every current member, identifier, session, block and payload");
  checks.push("normal preparation IPC invokes real Python inspection; full current metadata survives offline export without execution or wired qualification");
  const failed = await request("prepare-firmware"); assert.equal(failed.ok, false);
  assert.equal(failed.error, "firmware-container-zip-invalid");
  assert.equal((await request("export-firmware")).error, "firmware-not-prepared");
  checks.push("invalid replacement clears the prior preparation and cannot export stale firmware");
  assert.equal((await request("prepare-firmware")).ok, true);
  const cancelled = await request("prepare-firmware"); assert.equal(cancelled.canceled, true);
  assert.equal((await request("export-firmware")).error, "firmware-not-prepared");
  assert.deepEqual(await app.evaluate(() => globalThis.__firmwarePickers), { opens: 7, saves: 1 });
  assert.equal(await hash(path.join(inputs, "dme.pdx")), before); assert.deepEqual(errors, []);
  checks.push("cancelled replacement clears a fresh preparation; stale exports open no save dialog; original input stays byte-identical");
  passed = true;
} finally {
  if (app) await app.close(); clearTimeout(watchdog);
  const result = { ok: passed, passed, temp, checks, inputSha256: before, counts: saved?.container.counts,
    normalProductionPreparationIpc: true, realPythonContainerInspector: true, currentParserReference: true,
    fullExportCompared: passed, invalidReplacementClearsPrevious: passed, canceledReplacementClearsPrevious: passed,
    retiredContainerPanelAbsent: passed, originalUnchanged: passed, pageErrors: errors,
    installedEntryOnMac: Boolean(process.env.PORSCHE981_ACCEPT_APP_ROOT), windowsExecuted: process.platform === "win32", deviceIO: false, vehicleIO: false };
  await fs.writeFile(path.join(temp, "result.json"), JSON.stringify(result, null, 2)+"\n");
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify(result, null, 2)+"\n");
}
console.log(JSON.stringify({ ok: passed, temp, checks }));
