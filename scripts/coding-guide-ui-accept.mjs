import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-coding-ui-"));
const output = path.join(root, ".local/coding-guide-accept");
fs.mkdirSync(output, { recursive: true });
const env = {
  ...process.env,
  PORSCHE981_DB: path.join(dir, "ui.db"),
  PORSCHE981_OBD_SMOKE: "1",
  PORSCHE981_HEADLESS: "1",
  PORSCHE981_SESSION_DENY_LIVE: "1",
};
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const launch = () =>
  electron.launch({
    args: [path.join(root, "apps/desktop")],
    env,
    timeout: 20000,
  });
let app;
const errors = [];
async function enter(page) {
  page.on("pageerror", (e) => errors.push(e.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-page="coding"]').waitFor();
}
try {
  const seed = JSON.parse(
    fs.readFileSync(path.join(root, "data/seed/coding-guide/981.json"), "utf8"),
  );
  assert.equal(seed.items.length, 64);
  assert.equal(
    seed.items.reduce((n, i) => n + i.steps.length, 0),
    129,
  );
  assert.ok(
    seed.items.every(
      (i) =>
        i.compat.includes("981") &&
        i.steps.every((s) => !s.compat || s.compat.includes("981")),
    ),
  );
  app = await launch();
  let page = await app.firstWindow();
  await enter(page);
  await page.getByRole("heading", { name: "请选择系统", exact: true }).waitFor();
  assert.equal(await page.locator('[data-coding-category]').count(), 0);
  await page.locator('[data-coding-system="cluster"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  await page.locator('[data-coding-function="guide-2"]').click();
  assert.equal(await page.locator(".coding-steps li").count(), 8);
  assert.deepEqual((await page.locator('[aria-label="系统功能类别"] button').allTextContents()).map((x) => x.split("（")[0]), ["维护", "设码", "特殊功能", "编程"]);
  await page.screenshot({ path: path.join(output, "catalog.png") });
  assert.equal(await page.locator('[data-coding-view]').count(), 0);
  assert.equal(await page.getByLabel("搜索功能").count(), 0);
  const excluded = seed.items.find((item) => item.name.includes("涡轮"));
  await page.locator(`[data-coding-function="guide-${excluded.id}"]`).click();
  assert.equal(await page.getByRole("button", { name: "记录此步骤", exact: true }).count(), 0);
  const water = seed.items.find((item) => item.name.includes("显示真实水温"));
  await page.locator(`[data-coding-function="guide-${water.id}"]`).click();
  await page
    .getByRole("button", { name: "记录此步骤", exact: true })
    .first()
    .click();
  assert.equal(
    await page.getByLabel("实际原值", { exact: true }).inputValue(),
    "",
  );
  assert.equal(
    await page.getByLabel("操作后实测值", { exact: true }).inputValue(),
    "",
  );
  await page
    .getByLabel("操作后实测值", { exact: true })
    .fill("UI acceptance sample — not vehicle data");
  await page.getByLabel("ECU 型号或软件版本").fill("test-only");
  await page.getByRole("button", { name: "保存实测记录" }).click();
  await page.getByText("X431 实测记录已保存。", { exact: true }).waitFor();
  assert.equal(await page.locator('[data-page="x431-archive"]').count(), 0);
  const saved = await page.evaluate(() => window.porsche981.listCoding());
  assert.ok(saved.some((item) => item.after_value.includes("UI acceptance sample")));
  await app.close();
  app = await launch();
  page = await app.firstWindow();
  await enter(page);
  const restored = await page.evaluate(() => window.porsche981.listCoding());
  assert.ok(restored.some((item) => item.after_value.includes("UI acceptance sample") && item.note.includes(seed.source.revision)));
  assert.deepEqual(errors, []);
  console.log(
    "PASS coding UI: direct system functions, four categories, excluded guide, blank manual values, inline record saving and SQLite restart persistence",
  );
} finally {
  await app?.close();
  for (const name of ["ui.db", "ui.db-journal", "ui.db-wal", "ui.db-shm"]) {
    const file = path.join(dir, name);
    if (fs.existsSync(file)) fs.unlinkSync(file);
  }
  fs.rmdirSync(dir);
}
