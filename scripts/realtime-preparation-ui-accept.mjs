/** Real Electron/IPC/Python acceptance, isolated DB and no vehicle/device I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-realtime-offline-"));
const output = path.join(root, ".local/obd-offline-preparation-20261003/ui-accept");
fs.mkdirSync(output, { recursive: true });
const fixture = path.join(temp, "devices.json");
fs.writeFileSync(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_HEADLESS: "1",
  PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app; const errors = [];
try {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  const units = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-units", generation: "981" }));
  assert.equal(units.ok, true); assert.equal(units.counts.units, 35); assert.equal(units.counts.parameters, 52299);
  // Candidates and definition-only versions must not be silently treated as the current car.
  for (const id of ["pdk", "airbag", "hvac", "pcm"]) {
    await page.getByTestId("eng-system").selectOption(id);
    await page.getByTestId("offline-version-unmatched").waitFor();
    assert.equal(await page.getByTestId("offline-variant").count(), 0);
    assert.equal(await page.getByTestId("offline-parameters").count(), 0);
    assert.equal(await page.getByTestId("offline-plan").count(), 0);
  }
  await page.getByTestId("eng-system").selectOption("dme");
  assert.equal(await page.getByTestId("eng-select-all").count(), 1);
  await page.getByTestId("eng-data-source").selectOption("x431");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 365 项"));
  assert.match(await page.getByTestId("offline-version").innerText(), /SDI9_1_981_3_4L_EU5/);
  assert.equal(await page.getByTestId("offline-variant").count(), 0);
  assert.equal(await page.getByTestId("offline-stop").count(), 0);
  assert.equal(await page.getByTestId("offline-save").count(), 0);
  assert.equal(await page.getByTestId("offline-replay").count(), 1);
  assert.equal(await page.getByRole("button", { name: "导出离线结果", exact: true }).count(), 0);
  assert.ok(await page.getByTestId("eng-system").evaluate((el) =>
    el.closest(".panel") === document.querySelector('[data-testid="offline-parameters"]')?.closest(".panel")));
  const categoryOptions = await page.getByTestId("offline-category").locator("option").allTextContents();
  assert.ok(categoryOptions.some((s) => s.includes("通用信息")));
  assert.ok(categoryOptions.every((s) => !s.includes("（0）")));
  let inputs = page.getByTestId("offline-parameters").locator('input[type="checkbox"]');
  await inputs.nth(1).check(); await inputs.nth(2).check();
  await page.getByTestId("offline-plan").click();
  await page.getByTestId("offline-plan-result").waitFor();
  await page.getByTestId("offline-save").waitFor();
  assert.equal(await page.getByTestId("offline-stop").isDisabled(), true);
  assert.equal(await page.getByTestId("offline-save").isEnabled(), true);
  assert.match(await page.getByTestId("offline-capture-state").innerText(), /尚未采集实车数据/);
  await page.getByTestId("offline-replay").click();
  await page.getByTestId("offline-replay-result").waitFor();
  assert.match(await page.getByTestId("offline-replay-result").innerText(), /历史数据回放，不是当前车辆实时更新/);
  const filename = path.join(temp, "chosen-recording.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async (_window, options) => {
    if (options.title !== "保存此次采集") throw new Error("wrong dialog");
    return { canceled: false, filePath };
  }; }, filename);
  await page.getByTestId("offline-save").click();
  await page.getByTestId("offline-saved").waitFor();
  const recording = JSON.parse(fs.readFileSync(filename, "utf8"));
  assert.equal(recording.vehicleDataCollected, false); assert.equal(recording.kind, "x431-offline-plan");
  assert.equal(recording.parameters.length, 2); assert.equal(recording.profileId, "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5");
  assert.equal(recording.result.executionEnabled, false); assert.equal(recording.result.writePayload, null);
  assert.ok(recording.startedAt && recording.finishedAt);
  await app.evaluate(({ dialog }) => { dialog.showSaveDialog = async () => ({ canceled: true }); });
  await page.getByTestId("offline-save").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="offline-save"]').disabled);
  assert.equal(await page.getByTestId("offline-saved").count(), 0);
  assert.equal(await page.getByTestId("offline-plan-result").count(), 1, "cancel Save As retains result");
  // Executable grouped manufacturer rehearsal through real IPC/Python. It
  // never imports a serial transport or grants a manufacturer live route.
  await page.getByTestId("manufacturer-start").click();
  await page.waitForFunction(() => {
    const text = document.querySelector('[data-testid="manufacturer-state"]')?.textContent || "";
    return /[2-9] 轮/.test(text);
  });
  assert.equal(await page.getByTestId("eng-system").isDisabled(), true);
  assert.equal(await page.getByTestId("offline-category").isDisabled(), true);
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]').disabled);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /已停止/);
  assert.equal(await page.getByTestId("eng-system").isEnabled(), true);
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, temp);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-error").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /文件夹|允许|权限/);
  const rehearsalFile = path.join(temp, "manufacturer.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, rehearsalFile);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-saved").waitFor();
  const rehearsal = JSON.parse(fs.readFileSync(rehearsalFile, "utf8"));
  assert.equal(rehearsal.kind, "manufacturer-acquisition-rehearsal");
  assert.equal(rehearsal.vehicleDataCollected, false); assert.equal(rehearsal.simulation, true);
  assert.ok(rehearsal.cycles.length >= 2); assert.equal(rehearsal.samples.length, rehearsal.cycles.length * 2);
  assert.ok(rehearsal.cycles.every((cycle) => cycle.transactions.length === 1 && cycle.completedCycles === 1));
  assert.ok(rehearsal.samples.every((sample) => sample.synthetic && sample.processedUtc && sample.pduSha256));
  const forbiddenRehearsal = await page.evaluate((parameterIds) => window.porsche981.offlineDiagnostics({
    action: "ready-acquire", ecuId: 1, profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
    parameterIds, mode: "live" }), recording.parameters.map((p) => p.id));
  assert.equal(forbiddenRehearsal.error, "forbidden_field");
  // Real worker cancellation through the window-owned IPC, without any device calls.
  const cancelled = await page.evaluate(async ({ profileId, parameterIds }) => {
    const id = crypto.randomUUID();
    const promise = window.porsche981.offlineDiagnostics({ action: "ready-plan", ecuId: 1, profileId, parameterIds }, id);
    const stopped = await window.porsche981.cancelOfflineDiagnostics(id);
    return { stopped, result: await promise };
  }, { profileId: recording.profileId, parameterIds: recording.parameters.map((p) => p.id) });
  assert.equal(cancelled.stopped.cancelled, true); assert.equal(cancelled.result.error, "cancelled");
  const secondGroup = await page.getByTestId("offline-category").locator("option").nth(1).getAttribute("value");
  await page.getByTestId("offline-category").selectOption(secondGroup);
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /0 轮/);
  await page.getByTestId("offline-category").selectOption("");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 365 项"));
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  inputs = page.getByTestId("offline-parameters").locator('input[type="checkbox"]');
  for (let i = 0; i < 12; i++) if (!await inputs.nth(i).isChecked()) await inputs.nth(i).check();
  assert.equal(await inputs.nth(12).isDisabled(), true);
  assert.equal(await page.getByTestId("offline-save").count(), 0, "selection change resets previous attempt");
  await page.setViewportSize({ width: 800, height: 900 });
  const readable = await page.getByTestId("offline-parameters").locator("label").first().evaluate((el) => ({
    checkbox: el.querySelector("input").getBoundingClientRect().width,
    name: el.querySelector("span").getBoundingClientRect().width, height: el.getBoundingClientRect().height }));
  assert.ok(readable.checkbox <= 20 && readable.name > 120 && readable.height <= 42);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: path.join(output, "current-controls.png"), fullPage: true });
  await page.getByTestId("eng-system").selectOption("gateway");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 1151 项"));
  assert.match(await page.getByTestId("offline-version").innerText(), /CAN_CAN_Gateway_A7_1/);
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  assert.equal(await page.getByTestId("offline-plan-result").count(), 0);
  const bad = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-plan", ecuId: 1, profileId: "x", parameterIds: ["a".repeat(64)], serialPort: "COM999" }));
  assert.equal(bad.error, "forbidden_field");
  for (const generation of ["981", "982"]) {
    await page.getByTestId("offline-catalogue").selectOption(generation);
    await page.getByTestId("eng-system").selectOption("dme");
    await page.waitForFunction(() => document.querySelector('[data-testid="offline-profile"]')?.options.length > 1);
    const profile = await page.getByTestId("offline-profile").locator("option").nth(1).getAttribute("value");
    await page.getByTestId("offline-profile").selectOption(profile);
    await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
    assert.ok(await page.getByTestId("offline-parameters").locator("label").count() > 0);
    assert.match(await page.getByTestId("offline-realtime").innerText(), /尚未与本车身份匹配|历史身份已匹配/);
    assert.equal(await page.getByTestId("offline-plan-result").count(), 0, "catalogue change discards old result");
  }
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, noHardware: true,
    checks: ["automatic verified version", "candidate exclusion", "manual full-directory version is not qualification", "one panel", "12 cap", "category resets selection, pagination retains it", "native Save As", "save canceled retains result", "real worker stop IPC", "historical replay and full 981/982 catalogues", "grouped continuous manufacturer rehearsal", "stop releases controls", "save failure retains complete batch", "manufacturer live request rejected", "narrow layout"], errors }, null, 2));
  console.log("PASS realtime controls: matched versions, selection, stopped worker, native Save As/cancel, provenance, historical replay/full catalogues, no vehicle I/O");
} catch (error) {
  if (app) {
    const page = await app.firstWindow();
    console.error(JSON.stringify({ rendererErrors: errors, panelError: await page.getByTestId("offline-error").allTextContents(),
      panelTail: (await page.locator("body").innerText()).slice(-1200) }));
  }
  throw error;
} finally {
  await app?.close();
  fs.rmSync(temp, { recursive: true, force: true });
}
