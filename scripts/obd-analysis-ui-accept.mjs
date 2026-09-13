/** Isolated mock transport only — never a production vehicle claim. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { GarageDb } from '../packages/db/dist/index.js';

const root = path.resolve(import.meta.dirname, "..");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-obd-analysis-ui-"));
const output = path.join(root, ".local", "obd-analysis");
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
async function waitEnabled(page, label, timeout = 20000) {
  await page.waitForFunction((name) => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent === name);
    return b && !b.disabled;
  }, label, { timeout });
}
let app;
try {
  app = await launch();
  const page = await app.firstWindow();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.getByRole("button", { name: "OBD设备匹配设置", exact: true }).click();
  await page.getByRole("button", { name: "刷新端口" }).click();
  await page.getByText("vLinker FS BT (COM5)").waitFor();
  await page.locator('label').filter({ hasText: "vLinker FS BT (COM5)" }).click();
  await page.getByRole("button", { name: "连接", exact: true }).click();
  await page.locator('[data-obd="adapter-status"]').getByText("已连接").waitFor({ timeout: 15000 });
  await page.getByRole("button", { name: "故障码", exact: true }).click();
  await waitEnabled(page, "读取当前故障码");
  await page.getByRole("button", { name: "读取当前故障码" }).click();
  await page.getByText("P0301").waitFor({ timeout: 20000 });
  await page.getByText("P0571").waitFor();
  await page.getByText("P0420").waitFor();

  const row = page.locator('[data-obd="fault-row"]').filter({ hasText: "P0301" }).first();
  assert.equal(await row.locator('[data-obd="fault-detail"]').count(), 0);
  await row.locator('input[type="checkbox"]').click();
  assert.equal(await row.locator('[data-obd="fault-detail"]').count(), 0);
  await row.locator("button.obd-fault-title").click();
  await row.getByRole("tab", { name: "检查办法" }).waitFor();
  assert.match(await row.locator("button.obd-fault-title").innerText(), /P0301\s+—/);
  assert.doesNotMatch(await row.locator("button.obd-fault-title").innerText(), /^P0301\s*$/);
  assert.match(await row.innerText(), /未找到该模块的对应手册检查项/);
  assert.doesNotMatch(await row.innerText(), /出处：|981_Boxster|\.pdf/);
  await row.getByRole("tab", { name: "需要读取的数据" }).click();
  assert.match(await row.innerText(), /发动机转速|失火计数|待核实|不可采集/);
  await row.getByRole("tab", { name: "问题说明" }).click();
  assert.doesNotMatch(await row.innerText(), /出处：|\.pdf/);
  assert.match(await row.locator('[data-obd="fault-about"]').innerText(), /适用|核实|系统/);

  const brake = page.locator('[data-obd="fault-row"]').filter({ hasText: "P0571" }).first();
  await brake.locator("button.obd-fault-title").click();
  assert.match(await brake.locator("button.obd-fault-title").innerText(), /P0571\s+—/);
  assert.match(await brake.locator("button.obd-fault-title").innerText(), /P0571/);
  await brake.getByRole("tab", { name: "问题说明" }).click();
  assert.match(await brake.innerText(), /P0571/);
  assert.doesNotMatch(await brake.innerText(), /出处：|\.pdf/);

  await page.getByRole("button", { name: "实时数据分析" }).click();
  await page.locator('[data-obd="analysis-page"]').waitFor();
  await page.locator('[data-obd="analysis-hold"]').waitFor();
  await page.locator('[data-obd="analysis-gaps"]').waitFor();
  assert.match(await page.locator('[data-obd="analysis-gaps"]').innerText(), /P0301/);
  await page.locator('[data-obd="analysis-catalog"] [data-analysis-key="obd-can:7E8/rpm"]').waitFor();
  await page.locator('[data-obd="vehicle-status"]').getByText("有控制单元应答").waitFor({ timeout: 15000 });
  await waitEnabled(page, "开始采集");

  // Delay a real mock-host invocation to exercise cancellation while IPC is outstanding.
  await app.evaluate(({ipcMain}) => {
    const original = ipcMain._invokeHandlers.get('obd:readAnalysis');
    globalThis.__obdAnalysisTest = {active:0,max:0,release:null,delay:true};
    ipcMain.removeHandler('obd:readAnalysis');
    ipcMain.handle('obd:readAnalysis', async (event, selections) => {
      const state=globalThis.__obdAnalysisTest; state.active++;state.max=Math.max(state.max,state.active);
      try {
        if(state.delay){state.delay=false;await new Promise(resolve=>state.release=resolve);}
        return await original(event,selections);
      } finally {state.active--;}
    });
  });
  await page.getByRole('button',{name:'开始采集',exact:true}).click();
  await app.evaluate(async()=>{
    for(let i=0;i<100&&!globalThis.__obdAnalysisTest.release;i++)await new Promise(r=>setTimeout(r,20));
    if(!globalThis.__obdAnalysisTest.release)throw new Error('delayed_analysis_not_started');
  });
  await page.getByRole('button',{name:'暂停',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'开始采集',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'车辆信息',exact:true}).click();
  await page.getByRole('button',{name:'数据分析',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'开始采集',exact:true}).isDisabled(),true);
  await app.evaluate(()=>globalThis.__obdAnalysisTest.release());
  await waitEnabled(page,'开始采集',25000);
  assert.equal(await page.locator('[data-obd="analysis-chosen"]').getByText('2000',{exact:true}).count(),0,'late result after pause must be discarded');

  await page.getByRole("button", { name: "开始采集" }).click();
  const rpm = page.locator('[data-obd="analysis-chosen"] [data-analysis-key="obd-can:7E8/rpm"]');
  await rpm.getByText("2000").waitFor({ timeout: 25000 });
  await page.locator('[data-obd="analysis-refresh"]').waitFor();
  assert.match(await page.locator('[data-obd="analysis-refresh"]').innerText(), /本批\s*\d+\s*项.*耗时\s*\d+\s*ms/);
  assert.doesNotMatch(await rpm.innerText(), /已暂停|50\.196/);

  await page.getByRole("button", { name: "暂停" }).click();
  await waitEnabled(page, "开始采集", 25000);
  await page.getByRole("button", { name: "开始采集" }).click();
  await rpm.getByText("2000").waitFor({ timeout: 25000 });
  await page.getByRole("button", { name: "暂停" }).click();
  await page.getByRole("button", { name: "车辆信息", exact: true }).click();
  await page.getByRole("heading", { name: "车辆信息" }).waitFor();
  await page.getByRole("button", { name: "数据分析", exact: true }).click();
  const paused = await rpm.innerText();
  assert.match(paused, /2000/);
  assert.match(paused, /已暂停|历史/);
  assert.doesNotMatch(paused, /实时/);
  const stamp = paused;
  await page.locator('[data-obd="analysis-catalog"] [data-analysis-key="obd-can:7E8/rpm"] input[type="checkbox"]').waitFor();

  const boxes = page.locator('[data-obd="analysis-catalog"] li[data-analysis-key] input[type="checkbox"]');
  const n = await boxes.count();
  assert.ok(n > 0);
  for (let i = 0; i < n; i++) {
    const box = boxes.nth(i);
    if (await box.isChecked() || await box.isDisabled()) continue;
    await box.click();
  }
  let checked = 0, enabledUnchecked = 0;
  for (let i = 0; i < n; i++) {
    const box = boxes.nth(i);
    if (await box.isChecked()) checked += 1;
    else if (!(await box.isDisabled())) enabledUnchecked += 1;
  }
  assert.ok(checked >= 1 && checked <= 8);
  assert.equal(enabledUnchecked, 0, "overcap disables extra supported checks; list is not dropped");
  assert.match(await page.locator('[data-obd="analysis-count"]').innerText(), /已选 \d+ \/ 8/);

  await page.waitForTimeout(800);
  assert.equal(await rpm.innerText(), stamp, "paused/nav must stop further cycles");
  assert.equal(await app.evaluate(()=>globalThis.__obdAnalysisTest.max),1,'only one analysis IPC invocation may be outstanding');
  await page.locator('[data-obd="analysis-catalog"] [data-analysis-key="dme/misfire-counts"] button').click();
  assert.match(await page.locator('[data-obd="analysis-item-detail"]').innerText(),/待验证|待核实/);
  await page.locator('.obd-analysis-left').evaluate(el=>el.scrollTop=0);
  await page.screenshot({ path: path.join(output, "analysis-ui.png"), fullPage: true });
  assert.deepEqual(errors, []);
  console.log("PASS OBD analysis UI (isolated mock): collapse/checkbox, source hidden, P0571 title, jump+gaps, RPM 2000, refresh time, overcap, pause/start/nav");
} finally {
  await app?.close();
  for (const name of ["ui.db", "ui.db-journal", "ui.db-wal", "ui.db-shm"]) {
    const target = path.join(dir, name); if (fs.existsSync(target)) fs.unlinkSync(target);
  }
  fs.rmdirSync(dir);
}
