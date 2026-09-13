/** Hidden Electron UI smoke; isolated DB and no attached hardware access. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-obd-ui-"));
const output = path.join(root, ".local", "obd-offline-accept");
fs.mkdirSync(output, { recursive: true });
const env = { ...process.env, PORSCHE981_DB: path.join(dir, "ui.db"), PORSCHE981_OBD_SMOKE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
const launch = () => electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 20000 });
let app;
try {
  app = await launch();
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.getByRole("button", { name: "开发与验证", exact: true }).click();
  await page.getByRole("heading", { name: "先在电脑上演练" }).waitFor();
  await page.getByLabel("采集时间上限").selectOption("15000");
  await page.screenshot({ path: path.join(output, "connection.png") });
  await page.getByRole("button", { name: "开始模拟采集" }).click();
  await page.getByRole("button", { name: "停止并保存" }).waitFor();
  await page.getByRole("button", { name: "实时数据", exact: true }).click();
  await page.getByRole("heading", { name: "转速", exact: true }).waitFor({ timeout: 10000 });
  await page.getByRole("heading", { name: "空气流量", exact: true }).waitFor({ timeout: 10000 });
  await page.locator(".obd-curve").first().waitFor();
  await page.screenshot({ path: path.join(output, "live.png") });
  await page.getByRole("button", { name: "停止并保存" }).click();
  await page.getByText(/模拟会话 #.*已停止并保存/).waitFor();
  await page.getByRole("button", { name: "采集结果", exact: true }).click();
  await page.getByRole("heading", { name: "故障发生时的模拟数据" }).waitFor();
  await page.getByRole("button", { name: "离线演练", exact: true }).click();
  const exportedPath = path.join(dir, "export.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, exportedPath);
  await page.getByRole("button", { name: "导出记录", exact: true }).first().click();
  await page.getByText("记录已导出，文件内保留模拟来源和原始响应。").waitFor();
  const exported = JSON.parse(fs.readFileSync(exportedPath, "utf8"));
  assert.equal(exported.run.source, "simulation");
  assert.ok(exported.observations.some((o) => o.raw.includes("7E8")));

  await app.close();
  app = await launch();
  const reopened = await app.firstWindow();
  reopened.on("pageerror", (error) => errors.push(error.message));
  await reopened.locator('nav.side button[data-tab="obd"]').click();
  const replayPage = reopened;
  await replayPage.getByRole("button", { name: "开发与验证", exact: true }).click();
  // Everything below uses the reopened application and persisted SQLite history.
  await replayPage.getByRole("button", { name: "打开回放" }).first().click();
  await replayPage.getByRole("heading", { name: "会话回放" }).waitFor();
  const slider = replayPage.getByRole("slider", { name: "回放位置" });
  await slider.focus(); await slider.press("End");
  assert.equal(await slider.inputValue(), await slider.getAttribute("max"), "seeking to the end includes the last sample");
  await replayPage.getByRole("button", { name: "实时数据", exact: true }).click();
  await replayPage.getByRole("heading", { name: "转速", exact: true }).waitFor();
  await replayPage.locator(".obd-curve").first().waitFor();
  await replayPage.screenshot({ path: path.join(output, "replay.png") });
  assert.deepEqual(errors, []);
  console.log("PASS OBD UI: simulate, live chart, stop/save, freeze frame, export, app restart and seekable replay; screenshots .local/obd-offline-accept/");
} finally {
  await app?.close();
  // Delete only explicitly known test output within the freshly created temp dir.
  for (const name of ["ui.db", "ui.db-journal", "ui.db-wal", "ui.db-shm", "export.json"]) {
    const target = path.join(dir, name); if (fs.existsSync(target)) fs.unlinkSync(target);
  }
  fs.rmdirSync(dir);
}
