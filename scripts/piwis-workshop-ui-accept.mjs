/** Current workshop descriptions plus retained offline IPC; no device/vehicle I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
const root = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/piwis-workshop-accept"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), exported = path.join(temp, "offline-preview.json"), fixture = path.join(temp, "devices.json");
await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"),
  PORSCHE981_CONNECTION_FIXTURE: fixture, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const errors = [], checks = [];
const watchdog = setTimeout(() => { console.error("workshop scope acceptance timeout"); void app?.close(); }, 90000);
async function noPreparationControls(page) {
  for (const label of ["准备清单车系", "研究规则", "搜索刷写规则"]) assert.equal(await page.getByLabel(label, { exact: true }).count(), 0, label + " is retired");
  for (const name of ["生成准备清单", "保存准备清单"]) assert.equal(await page.getByRole("button", { name, exact: true }).count(), 0, name + " is retired");
  for (const id of ["workshop-preview", "workshop-flash", "workshop-rule"]) assert.equal(await page.getByTestId(id).count(), 0, id + " is retired");
}
try {
  app = await electron.launch({ args: [appRoot || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ ipcMain, dialog }, file) => {
    const state = globalThis.__workshopScope = { indexReads: 0, saveDialogs: 0 };
    ipcMain.removeHandler("workshop:flashIndex");
    ipcMain.handle("workshop:flashIndex", () => { state.indexReads++; return { status: "loaded", index: { schemaVersion: 1, sources: [], rules: [] } }; });
    dialog.showSaveDialog = async () => { state.saveDialogs++; return { canceled: false, filePath: file }; };
  }, exported);
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="psm"]').click(); await page.locator('[data-coding-category="special"]').click();
  await page.locator('[data-coding-function="workshop-psm-roller"]').click();
  await page.getByTestId("workshop-detail").waitFor(); await noPreparationControls(page);
  assert.match(await page.locator('.workshop-roller').innerText(), /未读取/);
  for (const name of ["启用滚筒模式", "读取滚筒状态", "退出滚筒模式"]) assert.equal(await page.getByRole("button", { name: `${name}(未完成)`, exact: true }).isDisabled(), true);
  assert.match(await page.getByTestId("workshop-detail").innerText(), /执行前需要核实/);
  assert.ok(await page.getByTestId("workshop-detail").locator("ol li").count() > 0);
  checks.push("special function retains purpose, prerequisites and steps; unfinished vehicle actions stay disabled; preparation controls absent");
  await page.locator('[data-coding-system="pdk"]').click(); await page.locator('[data-coding-function="workshop-pdk-calibration"]').click();
  await noPreparationControls(page); assert.match(await page.getByTestId("workshop-detail").innerText(), /activateProgramming=true/);
  assert.equal(await page.getByTestId("workshop-family-note").count(), 0, "do not invent family notes absent from the PDK source");
  checks.push("PDK dependency retained without preparation-family, invented family notes or export UI");
  await page.locator('[data-coding-system="dme"]').click(); await page.locator('[data-coding-category="programming"]').click();
  assert.equal(await page.getByTestId("workshop-detail").count(), 0);
  await page.locator('[data-coding-function="workshop-program-dme"]').click(); await noPreparationControls(page);
  assert.match(await page.getByTestId("firmware-version-unfinished").innerText(), /版本列表尚未完成/);
  assert.equal(await page.getByRole("button", { name: "确认刷写(未完成)", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "执行控制单元编程(未完成)", exact: true }).count(), 0);
  checks.push("programming exposes only its confirmed unfinished flash action; research rules and duplicate execution controls absent");
  await page.locator('[data-coding-system="psm"]').click(); await page.locator('[data-coding-category="maintenance"]').click();
  await page.locator('[data-coding-function="workshop-brake-bleed"]').click(); await noPreparationControls(page);
  await page.getByRole("heading", { name: "刹车排气(未完成)", exact: true }).waitFor();
  assert.equal(await page.getByLabel("搜索功能").count(), 1);
  assert.deepEqual(await app.evaluate(() => globalThis.__workshopScope), { indexReads: 0, saveDialogs: 0 });
  assert.equal(await fs.stat(exported).then(() => true, () => false), false);
  checks.push("maintenance search and description retained; browsing all categories reads no research index and creates no preparation file");
  // Keep the old offline interface covered independently of the retired UI.
  const saved = await page.evaluate(() => window.porsche981.workshopExportPreview({ generation: "981", functionId: "psm-roller", rollerPersistence: "persistent" }));
  assert.equal(saved.saved, true); const doc = JSON.parse(await fs.readFile(exported, "utf8"));
  assert.equal(doc.generation, "981"); assert.equal(doc.rollerPersistence, "persistent"); assert.equal(doc.executionEnabled, false);
  const rejected = await page.evaluate(async () => { try { await window.porsche981.workshopExportPreview({ generation: "991.2", functionId: "program-dme" }); return false; } catch { return true; } });
  assert.equal(rejected, true); assert.equal(await app.evaluate(() => globalThis.__workshopScope.saveDialogs), 1);
  checks.push("retained offline IPC still exports a non-executable reference and rejects other families before saving");
  await page.setViewportSize({ width: 760, height: 900 });
  assert.equal(await page.locator('.workshop').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({ path: path.join(output, "workshop-description.png"), fullPage: true });
  assert.deepEqual(errors, []); checks.push("narrow layout and renderer errors checked"); passed = true;
} finally {
  if (app) await app.close(); clearTimeout(watchdog);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, errors, installedEntryOnMac: Boolean(appRoot),
    researchIndexProbe: "injected IPC", offlineExport: "production IPC into isolated file", windowsExecuted: false, deviceIO: false, vehicleIO: false }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
