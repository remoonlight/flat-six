/** Real Electron UI/IPC, injected transport and private synthetic files. No car. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { waitForIpc } from "./obd-accept-helpers.mjs";
const root = path.resolve(import.meta.dirname, "..");
const scratch = path.join(root, ".local/obd-offline-implementation-20261005");
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(scratch, "ui-"));
const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
const identity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "TEST-HW", software: "TEST-SW" };
const backup = (dataHex, capturedUtc) => ({ schemaVersion: 2, kind: "ecu-coding-backup", identity, profileId,
  capturedUtc, expectedBlocks: [{ identifierKind: "LID", identifierHex: "01" }, { identifierKind: "DID", identifierHex: "0001" }],
  blocks: [{ identifierKind: "LID", identifierHex: "01", dataHex }, { identifierKind: "DID", identifierHex: "0001", dataHex: "7E" }] });
await fs.writeFile(path.join(temp, "original.json"), JSON.stringify(backup("A5", "2026-10-05T00:00:00Z")));
await fs.writeFile(path.join(temp, "current.json"), JSON.stringify(backup("A4", "2026-10-05T00:01:00Z")));
await fs.writeFile(path.join(temp, "devices.json"), JSON.stringify({ devices: [], errors: [] }));
await fs.writeFile(path.join(temp, "variants.jsonl"), JSON.stringify({ profile_id: profileId, name: "TEST definition, not car", module: "DME_BDE_Continental",
  generation: "981", membership: "confirmed", pool_records: { coding: { count: 2, records: [{ at: 4047899, name: "巡航控制（测试定义）", byteOffset: 0, bitOffset: 0,
    readSID: 0x21, pid: 1, read_request_candidate_hex: "2101",
    formula: { text: "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;" },
    enumText: { F000010F: "否", F0000110: "是" } }, { at: 4047900, name: "其他码值块字段（测试定义）", byteOffset: 0, bitOffset: 0,
      readSID: 0x22, pid: 1, read_request_candidate_hex: "220001" }] } } }) + "\n");
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"), PORSCHE981_CONNECTION_STATE: path.join(temp, "real-state.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app;
const errors = [];
try {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(15000); page.on("pageerror", (error) => errors.push(error.message));
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule("vm"), fs = process.getBuiltinModule("fs/promises"), path = process.getBuiltinModule("path");
    const load = (name) => vm.runInThisContext(`import(${JSON.stringify(args.modules[name])})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createDiagnosticPreparation } = await load("preparation");
    const { runWorkbench } = await load("offline");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await load("connection");
    let imported = 0;
    const preparation = createDiagnosticPreparation({ directory: path.join(args.temp, "vault"),
      chooseOpenFile: async () => path.join(args.temp, imported++ === 0 ? "original.json" : "current.json"),
      saveFile: async (name, data) => { await fs.writeFile(path.join(args.temp, name), JSON.stringify(data)); return { ok: true, saved: true }; },
      connectionStatus: () => null, previewField: (request) => runWorkbench(request, { repoRoot: args.root, variantsPath: path.join(args.temp, "variants.jsonl") }) });
    ipcMain.removeHandler("diagnostics:preparation"); ipcMain.handle("diagnostics:preparation", (_event, request) => preparation.handle(request));
    ipcMain.removeHandler("diagnostics:offline"); ipcMain.handle("diagnostics:offline", (_event, request) => runWorkbench(request, { repoRoot: args.root, variantsPath: path.join(args.temp, "variants.jsonl") }));
    const { EventEmitter } = process.getBuiltinModule("events"), { PassThrough } = process.getBuiltinModule("stream");
    const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", available: true, paired: true, label: "injected MX+" };
    const mgr = createObdConnectionManager({ repoRoot: args.root, env: { ...process.env, PORSCHE981_CONNECTION_STATE: path.join(args.temp, "injected-state.json") },
      listFn: async () => ({ devices: [device] }), chooseRecordingFile: async () => path.join(args.temp, "explicit.pcapng"),
      saveResult: async (data) => { await fs.writeFile(path.join(args.temp, "explicit-result.json"), JSON.stringify(data)); return { ok: true, saved: true }; },
      livenessMs: 60000, monitorSpawnFn: () => {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null;
        let timer;
        child.stdin.once("data", () => {
          child.stdout.write(JSON.stringify({ type: "handshake", ok: true, commOk: true, simulation: false, deviceId: device.id, volts: 12.6, voltageSource: "atrv", at: Date.now() }) + "\n");
          timer = setInterval(() => child.stdout.write(JSON.stringify({ type: "frames", deviceId: device.id, canNetwork: "drive", simulation: false,
            frames: [{ canId: 0x123, extended: false, dataHex: "0102", timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" }] }) + "\n"), 100);
        });
        child.kill = () => { clearInterval(timer); child.exitCode = 0; child.emit("close", 0); return true; }; return child;
      } });
    globalThis.__offlineUiConnection = mgr;
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { root, temp, modules: Object.fromEntries(["preparation", "offline", "connection"].map((name) => [name,
    pathToFileURL(path.join(root, "apps/desktop/electron", { preparation: "diagnostic-preparation.mjs", offline: "offline-diagnostics.mjs", connection: "obd-connection.mjs" }[name])).href])) });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  const panel = page.getByTestId("diagnostic-preparation");
  await panel.getByRole("button", { name: "导入并保存完整码值备份", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="码值备份"]').options.length === 2);
  const originalId = await panel.getByLabel("码值备份").locator("option").nth(1).getAttribute("value");
  await panel.getByLabel("码值备份").selectOption(originalId);
  await panel.getByLabel("码值块", { exact: true }).selectOption("LID:01");
  await page.waitForFunction(() => document.querySelector('[aria-label="设码字段"]').options.length === 2);
  assert.equal(await panel.getByLabel("设码字段", { exact: true }).locator('option[value="4047900"]').count(), 0);
  const mismatched = await page.evaluate(async ({ id }) => window.porsche981.diagnosticPreparation({ action: "coding-preview", ecu: "dme", id,
    blockKey: "DID:0001", recordAt: 4047899, rawValue: 0 }), { id: originalId });
  assert.equal(mismatched.error, "coding_field_block_mismatch");
  await panel.getByLabel("设码字段", { exact: true }).selectOption("4047899");
  await panel.getByRole("button", { name: "分析当前值与合法选项", exact: true }).click();
  await panel.getByLabel("设码合法值").selectOption("0");
  await panel.getByRole("button", { name: "预览码值修改", exact: true }).click();
  await panel.getByText("修改后：", { exact: false }).waitFor();
  assert.match(await panel.innerText(), /A4/); assert.match(await panel.innerText(), /实际改变位：\s*01/);
  await panel.getByRole("button", { name: "导入并保存完整码值备份", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="码值备份"]').options.length === 3);
  const currentId = await panel.getByLabel("码值备份").locator("option").nth(1).getAttribute("value");
  assert.notEqual(originalId, currentId);
  await page.reload();
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  await page.waitForFunction(() => document.querySelector('[aria-label="码值备份"]')?.options.length === 3);
  assert.equal(await panel.getByLabel("码值备份").locator(`option[value="${originalId}"]`).count(), 1,
    "saved original baseline loads on first visit after reload, without another import");
  await panel.getByLabel("码值备份").selectOption(currentId);
  await panel.getByLabel("码值块", { exact: true }).selectOption("LID:01");
  await panel.getByText("查看备份中的完整原始码值", { exact: true }).click();
  assert.match(await panel.innerText(), /DID 0001：\s*7E/);
  assert.match(await panel.innerText(), /LID 01：\s*A4/);
  await panel.getByRole("button", { name: "恢复当前控制单元原码：生成方案", exact: true }).click();
  await panel.getByText("共 1 个码值块需要改变", { exact: false }).waitFor();
  await panel.getByRole("button", { name: "模拟写入中断", exact: true }).click();
  await page.getByTestId("restore-simulation").waitFor();
  assert.match(await page.getByTestId("restore-simulation").innerText(), /模拟连接在写入过程中断开/);
  assert.equal(await panel.getByRole("button", { name: "写入车辆", exact: false }).isDisabled(), true);
  await page.screenshot({ path: path.join(temp, "coding.png"), fullPage: true });
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-refresh").click();
  await page.locator('input[name="obd-device"]').check();
  await page.getByTestId("obd-purpose-drive").check();
  await page.getByTestId("obd-conn-connect").click();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("internal-can-panel").waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-frames"]').children.length > 0);
  await assert.rejects(fs.stat(path.join(temp, "explicit.pcapng")), { code: "ENOENT" });
  const before = await page.evaluate(async () => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount);
  await page.getByTestId("internal-record-start").click();
  await page.locator('[data-obd-tab="coding"]').click();
  // Wait for actual receive progress while the receiver's page is unmounted.
  // Fast page switches may finish before the next injected 100 ms frame.
  await waitForIpc(page, async (count) =>
    (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > count, before);
  await page.locator('[data-obd-tab="live"]').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-record-state"]').textContent.includes("正在记录"));
  const after = await page.evaluate(async () => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount);
  assert.ok(after > before, "page switches do not stop internal receive");
  await page.getByTestId("internal-record-stop").click();
  assert.ok((await fs.stat(path.join(temp, "explicit.pcapng"))).size > 100);
  await page.getByTestId("internal-save").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-can-panel"]').textContent.includes("已保存本批结果"));
  assert.ok(JSON.parse(await fs.readFile(path.join(temp, "explicit-result.json"), "utf8")).frames.length > 0);
  await page.screenshot({ path: path.join(temp, "internal.png"), fullPage: true });
  await page.locator('[data-obd-tab="connection"]').click(); await page.getByTestId("obd-conn-disconnect").click();
  await page.getByTestId("obd-purpose-diagnostic").check();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme"); await page.getByTestId("eng-select-0C").check();
  await page.getByTestId("eng-cycles").fill("1"); await page.getByTestId("eng-interval").fill("500");
  const diagnosticFile = path.join(temp, "diagnostic-explicit.pcapng");
  await app.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, diagnosticFile);
  const rawControls = page.getByTestId("diagnostic-can-recording");
  await rawControls.getByRole("button", { name: "开始记录模拟原始接收帧", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="diagnostic-can-recording"]')?.textContent.includes("进行中"));
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => /本批保留 ([2-9]|[1-9][0-9]+) 条/.test(document.querySelector('[data-testid="eng-batch-count"]')?.textContent || ""));
  await page.getByTestId("eng-cancel").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="eng-export"]')?.disabled);
  assert.match(await page.getByTestId("eng-batch-count").innerText(), /当前为模拟数据/);
  await rawControls.getByRole("button", { name: "结束原始帧记录", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="diagnostic-can-recording"]')?.textContent.includes("已结束"));
  const diagnosticRecording = await fs.readFile(diagnosticFile);
  assert.ok(diagnosticRecording.includes(Buffer.from("SYNTHETIC")));
  let offset = 0, packets = 0;
  while (offset < diagnosticRecording.length) {
    const type = diagnosticRecording.readUInt32LE(offset), bytes = diagnosticRecording.readUInt32LE(offset + 4);
    assert.ok(bytes >= 12 && offset + bytes <= diagnosticRecording.length);
    if (type === 6) packets++;
    offset += bytes;
  }
  assert.ok(packets > 0, "real Python received frames reach the main recorder through session IPC");
  assert.deepEqual(errors, []);
  console.log("offline workflow UI PASS: imported immutable coding baseline, legal field preview, scoped restore and failed-write simulation, persistent internal receiver across pages, explicit PCAPNG/result files, continuous complete batches and actual Python-to-main raw frame streaming");
} finally {
  if (app) { await app.evaluate(async () => globalThis.__offlineUiConnection?.shutdown()).catch(() => {}); await app.close(); }
}
