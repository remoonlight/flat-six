/** Unified routing/coverage and measured-record acceptance in isolated Electron, no vehicle I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const scratch = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/coding-system-rework"));
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(os.tmpdir(), "porsche-coding-workspace-"));
await fs.writeFile(path.join(temp, "devices.json"), JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"), PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"), PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PORSCHE981_CAN_CAPTURE_ROOT: path.join(temp, "raw-captures"),
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1" };
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const read = async (name) => JSON.parse(await fs.readFile(path.join(root, name), "utf8"));
const menu = await read("data/seed/coding-guide/workspace-menu.json");
const guide = await read("data/seed/coding-guide/981.json");
const marker = "isolated UI record, not vehicle evidence";
let app;
const errors = [];
const firmwareChecks = [], programmingSystems = [];
async function open() {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? path.join(root, "apps/desktop/electron/main.mjs") : path.join(root, "apps/desktop")], env, timeout: 30000 });
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
    assert.ok(counts.every(text => !text.includes("未完成") && !text.includes("完成未测试")), "parent categories retain plain names");
    assert.doesNotMatch(await page.locator('[data-coding-system]').nth(index).innerText(), /未完成|完成未测试/);
    assert.ok(counts.some((text) => Number(text.match(/（(\d+)）/)?.[1]) > 0), "a visible system must have content in at least one category");
    for (const category of ["maintenance", "coding", "special", "programming"]) {
      await page.locator(`[data-coding-category="${category}"]`).click();
      if (category === "coding") {
        assert.equal(await page.getByTestId("diagnostic-preparation").count(), 0);
        assert.equal(await page.getByRole("heading", { name: "当前控制单元码值与恢复", exact: true }).count(), 0);
        const actions = page.locator('[aria-label="系统功能类别"]');
        assert.equal(await actions.getByTestId("coding-backup-original").count(), 1);
        assert.equal(await actions.getByTestId("coding-read-original").count(), 1);
      }
      const functions = await page.locator('[data-coding-function]').evaluateAll((els) => els.flatMap((el) => el.dataset.codingSourceIds.split(" ")));
      if (category === "programming") {
        assert.equal(await page.getByTestId("diagnostic-preparation").count(), 0, "programming details require an explicit function choice");
        if (functions.length) programmingSystems.push(ids[index]);
      }
      functions.forEach((f) => found.add(f));
      const names = await page.locator('[data-coding-function] strong').allTextContents();
      assert.ok(names.every(name => name.endsWith("(未完成)") && !name.includes("完成未测试")), "unimplemented vehicle functions carry only the leaf suffix");
      assert.deepEqual(await page.locator('[data-coding-function]').allTextContents(), names, "function list shows only names and status, without source or family metadata");
      assert.equal(await page.locator('[data-coding-function][aria-pressed="true"]').count(), 0, "system/category changes do not select a function");
      assert.equal(await page.locator('.coding-function-detail > section').count(), 0, "function details require an explicit selection");
      assert.equal(await page.getByTestId("coding-function-unselected").count(), functions.length ? 1 : 0);
      if (!functions.length) assert.equal(await page.getByRole("heading", { name: "暂无内容", exact: true }).count(), 1);
    }
  }
  assert.ok(menu.items.every((item) => found.has(item.id)), "no menu entries lost");
  assert.ok(guide.items.every((item) => found.has(`guide-${item.id}`)), "no community features lost");
  firmwareChecks.push("all programming categories wait for an explicit function choice");
  assert.ok(programmingSystems.length >= 2);
  for (const systemId of programmingSystems.slice(0, 2)) {
    await page.locator(`[data-coding-system="${systemId}"]`).click();
    await page.locator('[data-coding-category="programming"]').click();
    assert.equal(await page.getByTestId("diagnostic-preparation").count(), 0);
    firmwareChecks.push(`${systemId}: switching units clears the previous programming detail`);
    await page.locator('[data-coding-function]').first().click();
    const panel = page.getByTestId("diagnostic-preparation");
    await panel.getByRole("heading", { name: "原厂固件刷写(未完成)", exact: true }).waitFor();
    assert.equal(await panel.getAttribute("data-programming-ecu"), systemId);
    assert.equal(await panel.getByRole("button", { name: "确认刷写(未完成)", exact: true }).isDisabled(), true);
    assert.match(await panel.getByTestId("firmware-version-unfinished").innerText(), /版本列表尚未完成/);
    firmwareChecks.push(`${systemId}: unfinished matching and execution stay explicit and disabled`);
    assert.equal(await panel.locator("select,input,code").count(), 0);
    assert.equal(await page.getByLabel("固件匹配备份").count(), 0);
    assert.equal(await page.getByRole("button", { name: "选择并校验原厂固件", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "保存固件准备清单", exact: true }).count(), 0);
    assert.equal(await page.getByTestId("firmware-container-inspection").count(), 0);
    firmwareChecks.push(`${systemId}: retired backup/file/export/container controls absent`);
  }
  await page.screenshot({ path: path.join(scratch, "firmware-unfinished.png"), fullPage: true });
  await page.locator('[data-coding-system="psm"]').click();
  await page.locator('[data-coding-category="special"]').click();
  const beforeSearch = await page.locator('[data-coding-function]').evaluateAll(rows => rows.map(row => row.getAttribute("data-coding-function")));
  for (const purpose of ["滚筒模式", "传感器测试", "校准"]) assert.equal(await page.locator(`[data-maintenance-purpose="${purpose}"]`).count(), 1);
  assert.equal(await page.locator('[data-coding-function="workshop-vehicle-roller"]').count(), 0);
  await page.getByTestId("maintenance-search").fill("轮速");
  assert.equal(await page.locator('[data-coding-function="workshop-wheel-speed-test"]').count(), 1);
  assert.ok((await page.locator('[data-coding-function] strong').allTextContents()).every(name => name.includes("轮速")));
  await page.getByTestId("maintenance-search").fill("完全没有此维护功能");
  assert.equal(await page.locator('[data-coding-function]').count(), 0);
  assert.equal(await page.getByTestId("maintenance-no-matches").isVisible(), true);
  await page.getByTestId("maintenance-search").fill("");
  assert.deepEqual(await page.locator('[data-coding-function]').evaluateAll(rows => rows.map(row => row.getAttribute("data-coding-function"))), beforeSearch);
  await page.locator('[data-coding-system="whole-vehicle"]').click();
  await page.locator('[data-coding-category="special"]').click();
  assert.equal(await page.getByTestId("maintenance-search").inputValue(), "");
  assert.equal(await page.locator('[data-coding-function="workshop-vehicle-roller"]').count(), 1);
  await page.screenshot({ path: path.join(scratch, "maintenance-whole-vehicle.png"), fullPage: true });
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
  assert.equal(await button.innerText(), `${door.subFunction ?? `${door.system} · ${door.function}`}(未完成)`);
  assert.equal(await page.getByTestId("coding-menu-detail").count(), 0);
  await button.click();
  const detail = page.locator('[data-testid="coding-menu-detail"]');
  assert.equal(await page.getByTestId("coding-function-unselected").count(), 0);
  assert.match(await detail.locator("h3").innerText(), /\(未完成\)$/);
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
  assert.ok(saved.filter(row => row.after_value.includes(marker)).every(row => !row.function_name.includes("未完成") && !(row.sub_function || "").includes("未完成")), "display status never changes stored original function names");
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
  await fs.writeFile(path.join(scratch, "result.json"), JSON.stringify({ passed: true,
    systems: ids.length, sourceEntries: found.size, programmingSystems, firmwareChecks,
    platform: process.platform, windowsExecuted: process.platform === "win32", deviceIO: false, vehicleIO: false,
    verifiedVersionListImplemented: false, firmwareExecutionImplemented: false }, null, 2) + "\n");
  console.log(`PASS unified coding workspace: ${ids.length} nonempty systems, ${found.size} reachable source entries, four categories, names-only lists, explicit detail selection, source families retained in details, aliases, manual record validation/restart and narrow layout.`);
} catch (e) {
  const page = app ? await app.firstWindow() : null;
  if (page) await page.screenshot({ path: path.join(scratch, "failure.png"), fullPage: true });
  throw e;
} finally {
  await app?.close();
  for (const name of ["ui.db", "ui.db-wal", "ui.db-shm", "ui.db-journal"]) await fs.unlink(path.join(temp, name)).catch((e) => { if (e.code !== "ENOENT") throw e; });
  await fs.rm(temp, { recursive: true, force: true });
}
