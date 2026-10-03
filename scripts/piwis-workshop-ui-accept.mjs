/** Real Electron/preload/IPC acceptance with isolated DB; no vehicle requests. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { readWorkshopFlashIndex } from "../apps/desktop/electron/piwis-workshop.mjs";

const root = path.resolve(import.meta.dirname, "..");
const scratch = path.join(root, ".local", "piwis-workshop-accept");
await fs.mkdir(scratch, { recursive: true });
const exported = path.join(scratch, "exported-preview.json");
const temp = await fs.mkdtemp(path.join(os.tmpdir(), "porsche-piwis-ui-"));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_OBD_MOCK: "1", PORSCHE981_SESSION_DENY_LIVE: "1" };
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
let app;
try {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  assert.deepEqual(await page.locator('nav[aria-label="OBD 分区"] button').allTextContents(), ["连接设置", "系统拓扑", "实时数据", "设码与编程"]);
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-page="coding"]').waitFor();
  await page.locator('[data-coding-system="psm"]').click();
  await page.locator('[data-coding-category="special"]').click();
  await page.locator('[data-coding-function="workshop-psm-roller"]').click();
  assert.equal(await page.getByLabel("准备清单车系").inputValue(), "981");
  await page.getByText("当前滚筒状态：").waitFor();
  assert.match(await page.locator('.workshop-roller').innerText(), /未读取/);
  for (const name of ["启用滚筒模式", "读取滚筒状态", "退出滚筒模式"]) assert.equal(await page.getByRole("button", { name, exact: true }).isDisabled(), true);
  await page.getByLabel("滚筒保持方式").selectOption("persistent");
  await page.getByRole("button", { name: "生成准备清单", exact: true }).click();
  await page.locator('[data-testid="workshop-preview"]').getByText("保持方式：跨点火周期保持").waitFor();
  await app.evaluate(({ dialog }, filePath) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath });
  }, exported);
  await page.getByRole("button", { name: "保存准备清单", exact: true }).click();
  await page.getByText("准备清单已保存，包含适用性待核实状态。").waitFor();
  let doc = JSON.parse(await fs.readFile(exported, "utf8"));
  assert.equal(doc.generation, "981");
  assert.equal(doc.rollerPersistence, "persistent");
  assert.equal(doc.executionEnabled, false);

  await page.getByLabel("准备清单车系").selectOption("982");
  assert.equal(await page.locator('[data-testid="workshop-preview"]').count(), 0, "family change must invalidate preparation");
  await page.locator('[data-coding-system="pdk"]').click();
  await page.locator('[data-coding-function="workshop-pdk-calibration"]').click();
  await page.getByLabel("准备清单车系").selectOption("982");
  assert.match(await page.locator('[data-testid="workshop-detail"]').innerText(), /activateProgramming=true/);
  await page.getByRole("button", { name: "生成准备清单", exact: true }).click();
  assert.match(await page.locator('[data-testid="workshop-preview"]').innerText(), /982.*PDK 完整校准/s);

  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="programming"]').click();
  assert.equal(await page.locator('[data-testid="workshop-preview"]').count(), 0);
  assert.equal(await page.getByRole("button", { name: "执行控制单元编程", exact: true }).isDisabled(), true);
  const local = await readWorkshopFlashIndex(path.join(root, ".local/diagnostics/piwis-workshop/flash-index.json"));
  if (local.status === "loaded") {
    const allDmeRules = local.index.rules.filter((r) => r.ecu === "DME");
    assert.equal(await page.getByLabel("研究规则", { exact: true }).locator("option").count(), allDmeRules.length + 1, "both families must be shown together");
    const candidates = local.index.rules.filter((r) => r.generation === "982" && r.ecu === "DME");
    assert.ok(candidates.length);
    await page.getByLabel("研究规则", { exact: false }).selectOption(candidates[0].id);
    assert.equal(await page.getByLabel("准备清单车系").inputValue(), "982", "preparation family follows the selected rule");
    await page.locator('[data-testid="workshop-rule"]').getByText(candidates[0].targets[0].softwarePartNumber, { exact: true }).waitFor();
    await page.getByRole("button", { name: "生成准备清单", exact: true }).click();
    await page.getByRole("button", { name: "保存准备清单", exact: true }).click();
    await page.getByText("准备清单已保存，包含适用性待核实状态。").waitFor();
    doc = JSON.parse(await fs.readFile(exported, "utf8"));
    assert.equal(doc.flashRule.id, candidates[0].id);
    assert.equal(doc.applicabilityStatus, "unverified");
    assert.equal(doc.executionEnabled, false);
    assert.equal(doc.sources.length, local.index.sources.length);
    await page.screenshot({ path: path.join(scratch, "982-programming.png"), fullPage: true });
    await page.getByLabel("准备清单车系").selectOption("981");
    assert.equal(await page.getByLabel("研究规则", { exact: false }).inputValue(), "");
    assert.equal(await page.locator('[data-testid="workshop-rule"]').count(), 0);
    assert.equal(await page.locator('[data-testid="workshop-preview"]').count(), 0);
    await page.getByLabel("搜索刷写规则").fill("NO-MATCH-EXPECTED");
    await page.getByText("没有匹配的规则，请调整搜索条件。").waitFor();
    const airbag = local.index.rules.find((r) => r.generation === "981" && r.ecu === "Airbag");
    if (airbag) {
      await page.locator('[data-coding-system="airbag"]').click();
      await page.locator('[data-coding-function="workshop-program-airbag"]').click();
      await page.getByLabel("研究规则", { exact: false }).selectOption(airbag.id);
      await page.locator('[data-testid="workshop-current-conditions"]').getByText("当前硬件号条件", { exact: true }).waitFor();
      await page.getByRole("button", { name: "生成准备清单", exact: true }).click();
      await page.getByRole("button", { name: "保存准备清单", exact: true }).click();
      await page.getByText("准备清单已保存，包含适用性待核实状态。").waitFor();
      doc = JSON.parse(await fs.readFile(exported, "utf8"));
      assert.deepEqual(doc.flashRule.currentEcus, airbag.currentEcus);
    }
    const blocked = local.index.rules.find((r) => r.generation === "981" && r.ecu === "Gateway" && r.kind === "blocked");
    if (blocked) {
      await page.locator('[data-coding-system="gateway"]').click();
      await page.locator('[data-coding-function="workshop-program-gateway"]').click();
      await page.getByLabel("研究规则", { exact: false }).selectOption(blocked.id);
      await page.locator('[data-testid="workshop-no-flash"]').waitFor();
      assert.doesNotMatch(await page.locator('[data-testid="workshop-rule"]').innerText(), /目标软件号/);
      await page.getByRole("button", { name: "生成准备清单", exact: true }).click();
      await page.getByRole("button", { name: "保存准备清单", exact: true }).click();
      await page.getByText("准备清单已保存，包含适用性待核实状态。").waitFor();
      doc = JSON.parse(await fs.readFile(exported, "utf8"));
      assert.equal(doc.programmingDisposition, "blocked");
      await page.screenshot({ path: path.join(scratch, "gateway-no-flash.png"), fullPage: true });
    }
    const dataset = local.index.rules.find((r) => r.generation === "981" && r.ecu === "左LED前灯" && r.kind === "dataset");
    if (dataset) {
      await page.locator('[data-coding-system="headlamp-left"]').click();
      await page.locator('[data-coding-function="workshop-program-led-left"]').click();
      await page.getByLabel("研究规则", { exact: false }).selectOption(dataset.id);
      assert.match(await page.locator('[data-testid="workshop-rule"]').innerText(), /目标数据集会话/);
      assert.match(await page.locator('[data-testid="workshop-rule"]').innerText(), /具体适用性待核实/);
    }
  } else {
    await page.getByText(/尚未导入本机 PIWIS 规则|本机规则数据无效/).waitFor();
  }
  const rejected = await page.evaluate(async () => {
    try { await window.porsche981.workshopExportPreview({ generation: "991.2", functionId: "program-dme" }); return false; }
    catch { return true; }
  });
  assert.equal(rejected, true, "main-process validation must reject reserved families");
  await page.locator('[data-coding-system="psm"]').click();
  await page.locator('[data-coding-category="maintenance"]').click();
  await page.locator('[data-coding-function="workshop-brake-bleed"]').click();
  await page.getByRole("heading", { name: "刹车排气", exact: true }).waitFor();
  assert.equal(await page.getByLabel("搜索功能").count(), 0);
  await page.locator('[data-coding-system="can-adapter"]').click();
  await page.getByRole("heading", { name: "暂无内容", exact: true }).waitFor();
  assert.equal(await page.locator('[data-testid="workshop-detail"]').count(), 0);
  await page.locator('[data-coding-system="psm"]').click();
  await page.locator('[data-coding-category="special"]').click();
  await page.locator('[data-coding-function="workshop-psm-roller"]').click();
  await page.screenshot({ path: path.join(scratch, "981-maintenance.png"), fullPage: true });
  await page.setViewportSize({ width: 760, height: 900 });
  assert.equal(await page.evaluate(() => document.querySelector('.workshop').scrollWidth <= document.querySelector('.workshop').clientWidth), true);
  assert.deepEqual(errors, []);
  console.log("PASS workshop Electron UI: system/category navigation, combined families, roller options, calibration dependency, rule filter/invalidation, real IPC export, reserved-family rejection and responsive layout.");
} finally {
  await app?.close();
  // Preserve any test diagnostics in scratch, remove only our known temporary DB files.
  for (const name of ["ui.db", "ui.db-wal", "ui.db-shm", "ui.db-journal"]) await fs.unlink(path.join(temp, name)).catch((e) => { if (e.code !== "ENOENT") throw e; });
  await fs.rmdir(temp);
}
