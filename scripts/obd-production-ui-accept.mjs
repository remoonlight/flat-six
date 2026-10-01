/** Isolated mock transport only — never a production vehicle claim. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { GarageDb } from '../packages/db/dist/index.js';

const root = path.resolve(import.meta.dirname, "..");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-obd-prod-ui-"));
const output = path.join(root, ".local", "obd-production-accept");
fs.mkdirSync(output, { recursive: true });
const env = {
  ...process.env,
  PORSCHE981_DB: path.join(dir, "ui.db"),
  PORSCHE981_OBD_SMOKE: "1",
  PORSCHE981_OBD_MOCK: "1",
};
delete env.ELECTRON_RUN_AS_NODE;
delete env.VITE_DEV_SERVER_URL;
const seed = new GarageDb(env.PORSCHE981_DB);
seed.obd.upsertEcu({vehicleKey:'WP0ZZZ98ZES000001',moduleKey:'obd-can:7E8',name:'测试用既存单元',ecuAddress:'7E8',
  hardwareId:'TEST-HW',serial:'TEST-SERIAL',softwareId:null,calibrationId:'OLDER-CAL',cvn:null,codingFingerprint:null,
  lastSuccessAt:'2026-01-01T00:00:00.000Z'},[]);
seed.close();
const launch = () => electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 20000 });
let app;
try {
  app = await launch();
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.getByRole("button", { name: "OBD设备匹配设置", exact: true }).click();
  await page.locator('[data-obd="mock-banner"]').getByText("隔离测试传输，不是实车").waitFor();
  await page.locator('[data-obd="vehicle-status"]').getByText("无").waitFor();
  await page.locator('[data-obd="voltage"]').getByText("—").waitFor();
  assert.equal(await page.locator('[data-obd="voltage"]').innerText(), "—");

  await page.getByRole("button", { name: "刷新端口" }).click();
  await page.getByText("vLinker FS BT (COM5)").waitFor();
  await page.getByText("Standard Serial over Bluetooth link (COM9) | OBDLink MX+").waitFor();
  await page.locator('label').filter({ hasText: "vLinker FS BT (COM5)" }).click();
  await page.getByText("已保存选择：").waitFor({ timeout: 10000 });
  await page.getByRole("button", { name: "连接", exact: true }).click();
  await page.locator('[data-obd="adapter-status"]').getByText("已连接").waitFor({ timeout: 15000 });

  await page.getByRole("button", { name: "故障码", exact: true }).click();
  const readBtn = page.getByRole("button", { name: "读取当前故障码" });
  await readBtn.waitFor();
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent === "读取当前故障码");
    return b && !b.disabled;
  }, null, { timeout: 20000 });
  await readBtn.click();
  await page.getByText("P0301").waitFor({ timeout: 20000 });
  await page.locator('[data-obd="unsupported"]').waitFor();
  const body = await page.locator('[data-page="obd"]').innerText();
  assert.doesNotMatch(body, /"model"\s*:/);
  assert.doesNotMatch(body, /vehicleKey/);
  assert.doesNotMatch(body, /出处：/);
  const row = page.locator('[data-obd="fault-row"]').filter({ hasText: "P0301" }).first();
  assert.equal(await row.locator('[data-obd="fault-detail"]').count(), 0);
  await row.locator('input[type="checkbox"]').click();
  assert.equal(await row.locator('[data-obd="fault-detail"]').count(), 0, "checkbox must not expand the row");
  await row.locator("button.obd-fault-title").click();
  await row.getByRole("tab", { name: "检查办法" }).waitFor();
  assert.match(await row.innerText(), /未找到该模块的对应手册检查项/);
  assert.doesNotMatch(await row.innerText(), /出处：|\.pdf/);
  await page.getByRole("button", { name: "实时数据分析" }).waitFor();
  await page.getByRole("button", { name: "数据分析", exact: true }).waitFor();

  const scanBox = await page.locator(".obd-scan").boundingBox();
  const clearBox = await page.locator('[data-obd="clear-row"]').boundingBox();
  assert.ok(scanBox && clearBox && clearBox.y > scanBox.y, "clear button must sit below the fault list");

  const clearBtn = page.getByRole("button", { name: "一键清除故障码" });
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent === "一键清除故障码");
    return b && !b.disabled;
  }, null, { timeout: 5000 });
  await clearBtn.click();
  await page.getByText("仍有码").waitFor({ timeout: 20000 });
  await page.screenshot({path:path.join(output,'production-faults.png'),fullPage:true});

  await page.getByRole("button", { name: "车辆信息", exact: true }).click();
  await page.getByRole("heading", { name: "车辆信息" }).waitFor();
  await page.getByText("最近观察到的变化").waitFor();
  await page.getByText("最近成功").waitFor();
  await page.getByRole("columnheader", { name: "校验号" }).waitFor();
  await page.locator('[data-obd="ecu-changes"]').getByText(/OLDER-CAL.*DME-CAL-981/).waitFor();
  assert.equal(await page.locator('.obd-ecu-table tbody tr').count(),1);
  await page.getByRole('cell',{name:'TEST-SERIAL',exact:true}).waitFor();

  await page.getByRole("button", { name: "开发与验证", exact: true }).click();
  await page.getByRole("heading", { name: "先在电脑上演练" }).waitFor();
  await page.getByRole("heading", { name: "手工查码与记录" }).waitFor();
  await page.getByRole("button", { name: "开始模拟采集" }).click();
  await page.getByRole("button", { name: "停止并保存" }).waitFor();
  assert.equal((await page.locator('[data-obd="vehicle-status"]').innerText()).includes("无") || (await page.locator('[data-obd="mock-banner"]').count()) > 0, true);
  const liveStatus = await page.locator('[data-obd="vehicle-status"]').innerText();
  assert.notEqual(liveStatus, "有控制单元应答", "simulator must not mark production header live");
  await page.getByRole("button", { name: "停止并保存" }).click();

  await page.getByRole("button", { name: "设码", exact: true }).click();
  await page.getByRole("heading", { name: "设码与隐藏功能" }).waitFor();
  await page.screenshot({ path: path.join(output, "production-nav.png") });
  assert.deepEqual(errors, []);
  await app.close();
  app=await launch();
  const reopened=await app.firstWindow();
  await reopened.locator('nav.side button[data-tab="obd"]').click();
  await reopened.getByRole('button',{name:'车辆信息',exact:true}).click();
  await reopened.getByRole('cell',{name:'TEST-SERIAL',exact:true}).waitFor();
  await reopened.locator('[data-obd="ecu-changes"]').getByText(/OLDER-CAL.*DME-CAL-981/).waitFor();
  assert.equal(await reopened.locator('.obd-ecu-table tbody tr').count(),1);
  assert.equal(await reopened.locator('[data-obd="vehicle-status"]').innerText(),'无');
  await reopened.screenshot({path:path.join(output,'production-vehicle.png'),fullPage:true});
  console.log("PASS OBD production UI (isolated mock): header, unsupported, clear-below, remaining, ECU updates and changes, restart persistence, simulator isolation");
} finally {
  await app?.close();
  for (const name of ["ui.db", "ui.db-journal", "ui.db-wal", "ui.db-shm"]) {
    const target = path.join(dir, name); if (fs.existsSync(target)) fs.unlinkSync(target);
  }
  fs.rmdirSync(dir);
}
