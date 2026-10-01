/** Real preload + IPC + generated replay. Isolated DB; live explicitly denied. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { _electron as electron } from "playwright";
import { createReplayFixture } from "./can-replay-fixture.mjs";

const root = path.resolve(import.meta.dirname, "..");
const saved = createReplayFixture(root);
const scratch = path.join(root, ".local/vehicle-analysis/981-precar-20261001/checks");
fs.mkdirSync(scratch, { recursive: true });
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "can-replay-ui-"));
const fixture = path.join(temporary, "devices.json");
fs.writeFileSync(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_DB: path.join(temporary, "garage.db"),
  PORSCHE981_CONNECTION_STATE: path.join(temporary, "connection.json"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_HEADLESS: "1", PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_DEVTOOLS: "" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
const require = createRequire(import.meta.url);
let app;
try {
  app = await electron.launch({ executablePath: require("electron"), args: [path.join(root, "apps/desktop")], env });
  const page = await app.firstWindow();
  const errors = []; page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="broadcast"]').click();
  const section = page.locator('[data-page="can-capture"]');
  await section.getByText(saved.runId, { exact: false }).waitFor();
  const denied = await page.evaluate(() => window.porsche981.canCapture({ action: "start", seconds: 20,
    confirmedReadOnly: true, x431Inactive: true }));
  assert.equal(denied.error, "live_not_enabled");
  await section.locator("li").filter({ hasText: saved.runId }).getByRole("button").click();
  await section.getByText("本地回放，文件完整性已核对", { exact: true }).waitFor();
  await section.getByText(/CAN ERROR × 2/).waitFor();
  assert.equal(await section.locator("tbody tr").count(), 6);
  assert.match(await section.innerText(), /6 帧/);
  assert.match(await section.innerText(), /记录存在异常/);
  const snapshot = await app.evaluate(async ({ BrowserWindow }) => {
    const contents = BrowserWindow.getAllWindows()[0].webContents;
    contents.setBackgroundThrottling(false);
    await contents.capturePage(undefined, { stayHidden: true, stayAwake: true });
    await new Promise((resolve) => setTimeout(resolve, 250));
    const image = await contents.capturePage(undefined, { stayHidden: true, stayAwake: true });
    return image.toPNG().toString("base64");
  });
  fs.writeFileSync(path.join(scratch, "can-replay.png"), Buffer.from(snapshot, "base64"));
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(scratch, "can-electron.json"), JSON.stringify({ ok: true, noHardware: true,
    checks: ["real preload/IPC", "live denied before device open", "generated capture hashes and raw replay", "6 synthetic frames / 2 CAN ERROR", "six separate partitions", "no renderer errors"] }, null, 2));
  console.log("can-capture-electron-accept: PASS real UI, preload/IPC, generated replay, quality warnings, live deny; no hardware");
} finally { await app?.close(); saved.cleanup(); }
