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
  await page.getByRole("button", { name: "设码", exact: true }).click();
  await page.getByRole("heading", { name: "设码与隐藏功能" }).waitFor();
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
  assert.equal(await page.locator(".coding-feature").count(), 63);
  assert.equal(await page.locator(".coding-steps li").count(), 8);
  await page.screenshot({ path: path.join(output, "catalog.png") });
  await page.getByLabel("搜索功能").fill("涡轮");
  assert.equal(await page.locator(".coding-feature").count(), 0);
  await page.getByLabel("显示不适用条目").check();
  assert.equal(
    await page
      .getByRole("checkbox", { name: "选择 涡轮压力显示范围", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByLabel("搜索功能").fill("Sport Chrono直刷");
  await page.getByRole("checkbox", { name: /选择 Sport Chrono直刷/ }).check();
  await page.getByRole("button", { name: "查看功能方案", exact: true }).click();
  assert.equal(await page.locator(".coding-plan-step").count(), 7);
  assert.equal(await page.locator(".coding-plan-group").count(), 3);
  assert.equal(await page.getByText(/Joker Sport Plus/).count(), 0);
  await page.screenshot({ path: path.join(output, "plan.png") });
  await page.getByRole("button", { name: "清空方案", exact: true }).click();
  await page.getByRole("button", { name: "981 功能库", exact: true }).click();
  await page.getByLabel("搜索功能").fill("后加装运排");
  await page.getByRole("checkbox", { name: /选择 后加装运排/ }).check();
  await page.getByRole("button", { name: "查看功能方案", exact: true }).click();
  await page.getByText("请先补齐方案", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "复制完整方案" }).count(),
    0,
  );
  await page.getByLabel("运排按键方案").selectOption("B");
  assert.equal(await page.locator(".coding-plan-step").count(), 4);
  assert.equal(await page.getByText(/\[Plan A\]/).count(), 0);
  await page.getByRole("button", { name: "复制完整方案" }).click();
  await page.getByRole("status").filter({ hasText: "方案已复制" }).waitFor();
  const clipboard = await app.evaluate(({ clipboard }) => clipboard.readText());
  assert.ok(clipboard.includes("[Plan B]") && !clipboard.includes("[Plan A]"));
  await page.getByRole("button", { name: "981 功能库", exact: true }).click();
  await page.getByLabel("搜索功能").fill("显示真实水温");
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
  await page
    .getByRole("button", { name: "X431 原始菜单", exact: true })
    .click();
  await page.getByRole("heading", { name: "X431 原始菜单与快照" }).waitFor();
  assert.ok(
    (await page.locator('[data-page="x431-archive"] select option').count()) >
      0,
  );
  assert.ok(
    (await page.locator('[data-page="x431-archive"]').innerText()).includes(
      "UI acceptance sample",
    ),
  );
  await page.screenshot({ path: path.join(output, "x431.png") });
  await app.close();
  app = await launch();
  page = await app.firstWindow();
  await enter(page);
  await page.getByRole("button", { name: "操作记录", exact: true }).click();
  await page.locator(".coding-snapshot").first().waitFor();
  await page.locator(".coding-snapshot summary").first().click();
  assert.ok(
    (await page.locator(".coding-snapshot").innerText()).includes(
      "UI acceptance sample",
    ),
  );
  assert.ok(
    (await page.locator(".coding-snapshot").innerText()).includes(
      seed.source.revision,
    ),
  );
  await page.screenshot({ path: path.join(output, "records.png") });
  assert.deepEqual(errors, []);
  console.log(
    "PASS coding UI: 981 filtering, multi-module plan, alternatives, clipboard, blank measured values, legacy X431 and SQLite restart persistence",
  );
} finally {
  await app?.close();
  for (const name of ["ui.db", "ui.db-journal", "ui.db-wal", "ui.db-shm"]) {
    const file = path.join(dir, name);
    if (fs.existsSync(file)) fs.unlinkSync(file);
  }
  fs.rmdirSync(dir);
}
