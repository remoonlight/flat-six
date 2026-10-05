/** Unified routing/coverage and measured-record acceptance in isolated Electron, no vehicle I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
const root = path.resolve(import.meta.dirname, "..");
const scratch = path.join(root, ".local/coding-system-rework");
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(os.tmpdir(), "porsche-coding-workspace-"));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1" };
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const read = async (name) => JSON.parse(await fs.readFile(path.join(root, name), "utf8"));
const menu = await read("data/seed/coding-guide/workspace-menu.json");
const guide = await read("data/seed/coding-guide/981.json");
const marker = "isolated UI record, not vehicle evidence";
let app;
const errors = [];
async function open() {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  return page;
}
try {
  let page = await open();
  await page.getByRole("heading", { name: "请选择系统", exact: true }).waitFor();
  assert.equal(await page.locator('[data-coding-view="plan"]').count(), 0);
  assert.equal(await page.locator('[data-coding-view="x431"]').count(), 0);
  assert.equal(await page.locator('.coding-heading').count(), 0);
  assert.equal(await page.locator('.coding-systems input').count(), 0);
  assert.equal(await page.locator('[data-coding-view]').count(), 0);
  assert.equal(await page.locator('.coding-function-search').count(), 0);
  assert.equal(await page.locator('[aria-label="系统功能类别"]').count(), 0);
  const ids = await page.locator('[data-coding-system]').evaluateAll((els) => els.map((el) => el.dataset.codingSystem));
  assert.equal(ids.length, new Set(ids).size);
  assert.ok(!ids.includes("source-驾驶员车门") && !ids.includes("source-附加仪表时钟"), "guide aliases must use actual topology systems");
  // Every guide and plaintext menu must remain reachable after regrouping.
  const found = new Set();
  for (const [index] of ids.entries()) {
    await page.locator('[data-coding-system]').nth(index).click();
    assert.equal(await page.locator('[data-coding-category]').count(), 4);
    const counts = await page.locator('[data-coding-category]').allTextContents();
    assert.ok(counts.some((text) => Number(text.match(/（(\d+)）/)?.[1]) > 0), "a visible system must have content in at least one category");
    for (const category of ["maintenance", "coding", "special", "programming"]) {
      await page.locator(`[data-coding-category="${category}"]`).click();
      const functions = await page.locator('[data-coding-function]').evaluateAll((els) => els.flatMap((el) => el.dataset.codingSourceIds.split(" ")));
      functions.forEach((f) => found.add(f));
      if (!functions.length) assert.equal(await page.getByRole("heading", { name: "暂无内容", exact: true }).count(), 1);
    }
  }
  assert.ok(menu.items.every((item) => found.has(item.id)), "no menu entries lost");
  assert.ok(guide.items.every((item) => found.has(`guide-${item.id}`)), "no community features lost");
  await page.locator('[data-coding-system="gateway"]').click();
  await page.locator('[data-coding-category="maintenance"]').click();
  assert.equal(await page.locator('[data-coding-function="workshop-battery-change"]').count(), 1);
  assert.equal(await page.locator('[data-coding-function="menu-51"]').count(), 0, "battery menu merged into one function");
  await page.locator('[data-coding-function="workshop-battery-change"]').click();
  await page.getByTestId("coding-menu-detail").getByText("来源与年款标签", { exact: true }).click();
  assert.match(await page.getByTestId("coding-archive-evidence").innerText(), /网关/);
  // Tiptronic source labels must not be presented as a qualified PDK mapping.
  await page.locator('[data-coding-system="pdk"]').click();
  await page.locator('[data-coding-category="maintenance"]').click();
  assert.ok(!(await page.locator('[aria-label="系统功能列表"]').innerText()).includes("Tiptronic"));
  assert.equal(await page.locator('[data-coding-system="can-adapter"]').count(), 0, "empty control unit is hidden");
  // One merged menu entry exposes both source families and stores actual blank/manual values only.
  const door = menu.items.find((item) => item.system === "驾驶员侧车门" && item.function === "设码");
  await page.locator('[data-coding-system="door-driver"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  const button = page.locator(`[data-coding-function="${door.id}"]`);
  assert.equal(await button.count(), 1);
  assert.match(await button.innerText(), /981 \/ 982/);
  await button.click();
  const detail = page.locator('[data-testid="coding-menu-detail"]');
  assert.equal(await detail.getByLabel("实际原值", { exact: true }).inputValue(), "");
  assert.equal(await detail.getByLabel("操作后实测值", { exact: true }).inputValue(), "");
  await detail.getByRole("button", { name: "保存实测记录", exact: true }).click();
  await detail.getByRole("alert").waitFor();
  await detail.getByLabel("实际原值", { exact: true }).fill("discarded draft");
  await detail.getByLabel("资料车系").selectOption("982");
  assert.equal(await detail.getByLabel("实际原值", { exact: true }).inputValue(), "");
  await detail.getByLabel("操作后实测值", { exact: true }).fill(marker);
  await detail.getByRole("button", { name: "保存实测记录", exact: true }).click();
  await detail.getByRole("status").waitFor();
  let saved = await page.evaluate(() => window.porsche981.listCoding());
  assert.ok(saved.some((s) => s.note.includes("来源菜单：982") && s.after_value.includes(marker)));
  await app.close(); app = undefined;
  page = await open();
  saved = await page.evaluate(() => window.porsche981.listCoding());
  assert.ok(saved.some((s) => s.after_value.includes(marker)), "existing records persist without a records tab");
  await page.locator('[data-coding-system="psm"]').click();
  await page.locator('[data-coding-category="special"]').click();
  await page.setViewportSize({ width: 760, height: 900 });
  assert.equal(await page.locator('[data-page="coding"]').evaluate((el) => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({ path: path.join(scratch, "unified-narrow.png"), fullPage: true });
  assert.deepEqual(errors, []);
  console.log(`PASS unified coding workspace: ${ids.length} nonempty systems, ${found.size} reachable source entries, four categories, blank categories, merged family labels, aliases, manual record validation/restart and narrow layout.`);
} catch (e) {
  const page = app ? await app.firstWindow() : null;
  if (page) await page.screenshot({ path: path.join(scratch, "failure.png"), fullPage: true });
  throw e;
} finally {
  await app?.close();
  for (const name of ["ui.db", "ui.db-wal", "ui.db-shm", "ui.db-journal"]) await fs.unlink(path.join(temp, name)).catch((e) => { if (e.code !== "ENOENT") throw e; });
  await fs.rmdir(temp);
}
