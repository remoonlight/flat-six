/** Actual Electron preparation IPC and Python parser, with test-owned pickers. No head or car. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT);
const inputs = path.resolve(process.env.PORSCHE981_FIRMWARE_TEST_INPUT);
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "ui-"));
const expected = JSON.parse(await fs.readFile(path.join(inputs, "expected.json"), "utf8"));
const devices = path.join(temp, "devices.json"); await fs.writeFile(devices, JSON.stringify({ devices: [], errors: [] }));
const hash = async file => createHash("sha256").update(await fs.readFile(file)).digest("hex");
const before = await hash(path.join(inputs, "dme.pdx"));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"),
  PORSCHE981_DB: path.join(temp, "test.db"), PORSCHE981_CONNECTION_FIXTURE: devices,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection-state.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
const errors = []; let app;
try {
  app = await electron.launch({ executablePath: process.env.PORSCHE981_ACCEPT_ELECTRON_EXECUTABLE,
    args: [...(process.platform === "win32" ? ["--disable-gpu"] : []), process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000); page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(({ dialog }, args) => {
    const path = process.getBuiltinModule("path");
    const files = ["dme-manifest.json", "dme.pdx", "bad-manifest.json", "bad.pdx"];
    dialog.showOpenDialog = async () => { const file = files.shift(); return file ? { canceled: false, filePaths: [path.join(args.inputs, file)] } : { canceled: true, filePaths: [] }; };
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path.join(args.temp, "firmware-preparation.json") });
  }, { inputs, temp });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="programming"]').click();
  await page.getByRole("heading", { name: "原厂控制单元固件准备", exact: true }).waitFor();
  await page.getByRole("button", { name: "选择并校验原厂固件", exact: true }).click();
  const container = page.getByTestId("firmware-container-inspection");
  await container.waitFor({ timeout: 60000 });
  assert.match(await page.getByTestId("firmware-container-status").innerText(), /核对通过/);
  assert.match(await container.innerText(), new RegExp(`${expected.dme.counts.blocks} 个数据块`));
  assert.equal(await page.getByRole("button", { name: "刷写车辆固件(未完成)", exact: true }).isDisabled(), true);
  assert.match(await page.getByTestId("firmware-connection-requirement").innerText(), /目标诊断头：VNCI \/ PT3G.*本次有线连接：未核实/);
  const details = container.locator("details").first(); await details.locator("summary").click();
  assert.match(await details.innerText(), /SHA-256/);
  await container.scrollIntoViewIfNeeded();
  await container.screenshot({ path: path.join(temp, "container-panel.png") });
  await page.screenshot({ path: path.join(temp, "firmware-page.png"), fullPage: true });
  const screenshotStats = await app.evaluate(({ nativeImage }, base64) => {
    const image = nativeImage.createFromBuffer(Buffer.from(base64, "base64"));
    const pixels = image.toBitmap(); let opaque = 0, nonUniform = false;
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3] > 0) opaque++;
      if (pixels[i] !== pixels[0] || pixels[i + 1] !== pixels[1] || pixels[i + 2] !== pixels[2]) nonUniform = true;
    }
    return { ...image.getSize(), opaquePixels: opaque, nonUniform };
  }, (await fs.readFile(path.join(temp, "container-panel.png"))).toString("base64"));
  assert.ok(screenshotStats.nonUniform && screenshotStats.opaquePixels > 100, "actual container screenshot must contain visible content");
  await page.getByRole("button", { name: "保存固件准备清单", exact: true }).click();
  await page.getByText("文件已保存。", { exact: true }).waitFor();
  const saved = JSON.parse(await fs.readFile(path.join(temp, "firmware-preparation.json"), "utf8"));
  assert.equal(saved.file.sha256, before); assert.equal(saved.hashMatches, true);
  assert.equal(saved.executionEnabled, false); assert.equal(saved.writePayload, null);
  assert.deepEqual(saved.transportRequirement.targetFamilies, ["VNCI", "PT3G"]);
  assert.equal(saved.transportRequirement.wiredConnectionVerified, false);
  assert.deepEqual(saved.container, expected.dme, "normal production export retains every inspected member, session, block and payload");
  await page.getByRole("button", { name: "选择并校验原厂固件", exact: true }).click();
  await page.getByText("固件容器损坏、校验失败或压缩格式不支持。", { exact: true }).waitFor();
  assert.equal(await page.getByTestId("firmware-container-inspection").count(), 0);
  assert.equal(await page.getByRole("button", { name: "保存固件准备清单", exact: true }).isDisabled(), true);
  const stale = await page.evaluate(() => window.porsche981.diagnosticPreparation({ action: "export-firmware", ecu: "dme" }));
  assert.equal(stale.error, "firmware-not-prepared");
  assert.equal(await hash(path.join(inputs, "dme.pdx")), before);
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(temp, "result.json"), JSON.stringify({ ok: true, inputSha256: before, counts: saved.container.counts,
    normalProductionPreparationIpc: true, realPythonContainerInspector: true, fullExportCompared: true,
    invalidReplacementClearsPrevious: true, originalUnchanged: true, screenshotStats, pageErrors: errors, vehicleIO: false }, null, 2)+"\n");
  console.log(JSON.stringify({ ok: true, temp, counts: saved.container.counts }));
} finally { if (app) await app.close(); }
