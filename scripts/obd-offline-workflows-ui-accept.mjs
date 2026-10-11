/** Real Electron UI/IPC, injected transport and private synthetic files. No car. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { waitForIpc } from "./obd-accept-helpers.mjs";
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const codingOnly = process.env.PORSCHE981_ACCEPT_CODING_ONLY === "1";
const scratch = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-offline-implementation-20261005"));
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
  generation: "981", membership: "confirmed", pool_records: { coding: { count: 4, records: [{ at: 4047899, name: "巡航控制（测试定义）", byteOffset: 0, bitOffset: 0,
    readSID: 0x21, pid: 1, read_request_candidate_hex: "2101",
    formula: { text: "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF000010F;[0x01]->0xF0000110;LengthInfo=Standard,BitLength=1,BitMask=0,HighLow=1;" },
    enumText: { F000010F: "否", F0000110: "是" } }, { at: 4047900, name: "其他码值块字段（测试定义）", byteOffset: 0, bitOffset: 0,
      readSID: 0x22, pid: 1, read_request_candidate_hex: "220001" },
      { at: 4047901, name: "巡航关联指示（测试定义）", byteOffset: 0, bitOffset: 0,
        readSID: 0x21, pid: 1, read_request_candidate_hex: "2101",
        formula: { text: "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF001;[0x01]->0xF002;BitLength=1,BitMask=0,HighLow=1;" },
        enumText: { F001: "关闭", F002: "开启" } },
      { at: 4047902, name: "保留字段（测试定义）", byteOffset: 0, bitOffset: 1,
        readSID: 0x21, pid: 1, read_request_candidate_hex: "2101",
        formula: { text: "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF001;[0x01]->0xF002;BitLength=1,BitMask=0,HighLow=1;" },
        enumText: { F001: "关闭", F002: "开启" } }] } } }) + "\n");
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PYTHONDONTWRITEBYTECODE: "1", PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"), PORSCHE981_CAN_CAPTURE_ROOT: path.join(temp, "raw-captures"),
  PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "transient-sessions"),
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"), PORSCHE981_CONNECTION_STATE: path.join(temp, "real-state.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app;
const errors = [];
try {
  app = await electron.launch({ executablePath: process.env.PORSCHE981_ACCEPT_ELECTRON_EXECUTABLE,
    args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(15000); page.on("pageerror", (error) => errors.push(error.message));
  await app.evaluate(async ({ ipcMain, dialog }, args) => {
    const vm = process.getBuiltinModule("vm"), fs = process.getBuiltinModule("fs/promises"), path = process.getBuiltinModule("path");
    const load = (name) => vm.runInThisContext(`import(${JSON.stringify(args.modules[name])})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createDiagnosticPreparation } = await load("preparation");
    const { runWorkbench } = await load("offline");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await load("connection");
    const { saveInternalCanRecording } = await load("save");
    let imported = 0, recordingIndex = 0;
    const preparation = createDiagnosticPreparation({ directory: path.join(args.temp, "user-data/diagnostics/coding-backups"),
      chooseOpenFile: async () => path.join(args.temp, imported++ === 0 ? "original.json" : "current.json"),
      saveFile: async (name, data) => { await fs.writeFile(path.join(args.temp, name), JSON.stringify(data)); return { ok: true, saved: true }; },
      connectionStatus: () => null, previewField: (request) => runWorkbench(request, { repoRoot: args.root, variantsPath: path.join(args.temp, "variants.jsonl") }) });
    ipcMain.removeHandler("diagnostics:preparation"); ipcMain.handle("diagnostics:preparation", (_event, request) => preparation.handle(request));
    ipcMain.removeHandler("diagnostics:offline"); ipcMain.handle("diagnostics:offline", (_event, request) => runWorkbench(request, { repoRoot: args.root, variantsPath: path.join(args.temp, "variants.jsonl") }));
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path.join(args.temp, "saved-coding-preview.json") });
    const { EventEmitter } = process.getBuiltinModule("events"), { PassThrough } = process.getBuiltinModule("stream");
    const device = { id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+", available: true, paired: true, label: "injected MX+" };
    const receiveClock = { offset: 0 };
    globalThis.__internalDeadlineClock = receiveClock;
    const mgr = createObdConnectionManager({ repoRoot: args.root, elapsedNow: () => performance.now() + receiveClock.offset, env: { ...process.env, PORSCHE981_CONNECTION_STATE: path.join(args.temp, "injected-state.json") },
      listFn: async () => ({ devices: [device] }), chooseRecordingFile: async () => path.join(args.temp, recordingIndex++ === 0 ? "explicit.pcapng" : "internal-time-limit.pcapng"),
      saveResult: (data) => saveInternalCanRecording(data, { dialog: { showSaveDialog: async () => ({ canceled: false, filePath: path.join(args.temp, "explicit-result.json") }) }, openFile: fs.open }),
      livenessMs: 60000, monitorSpawnFn: () => {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null;
        let timer;
        let receiveBatchId, receiving = false;
        child.stdin.on("data", raw => {
          for (const line of raw.toString().trim().split("\n")) {
            const request = JSON.parse(line);
            if (request.action === "stop") { child.kill(); continue; }
            if (["monitor", "receive-start"].includes(request.action)) { receiveBatchId = request.receiveBatchId; receiving = request.receiveMilliseconds > 0; }
            if (request.action === "receive-stop" && request.receiveBatchId === receiveBatchId) receiving = false;
            child.stdout.write(JSON.stringify({ type: request.action === "monitor" ? "handshake" : "reading", ok: true, commOk: true, simulation: false,
              deviceId: device.id, canNetwork: "drive", volts: 12.6, voltageSource: "atrv", at: Date.now(), receiveBatchId,
              receiveState: receiving ? "receiving" : "standby", receiveReason: receiving ? null : "time-limit" }) + "\n");
            if (!timer) timer = setInterval(() => {
              if (receiving) child.stdout.write(JSON.stringify({ type: "frames", deviceId: device.id, canNetwork: "drive", simulation: false, receiveBatchId,
                frames: [{ canId: 0x123, extended: false, dataHex: "0102", timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" }] }) + "\n");
            }, 100);
          }
        });
        child.kill = () => { clearInterval(timer); child.exitCode = 0; child.emit("close", 0); return true; }; return child;
      } });
    globalThis.__offlineUiConnection = mgr;
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { root, temp, modules: Object.fromEntries(["preparation", "offline", "connection", "save"].map((name) => [name,
    pathToFileURL(path.join(root, "apps/desktop/electron", { preparation: "diagnostic-preparation.mjs", offline: "offline-diagnostics.mjs", connection: "obd-connection.mjs", save: "recording-files.mjs" }[name])).href])) });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  const panel = page.getByTestId("diagnostic-preparation");
  for (const name of ["读取车辆完整原码", "写入车辆", "恢复原码"]) {
    assert.equal(await panel.getByRole("button", { name: `${name}(未完成)`, exact: true }).isDisabled(), true);
  }
  assert.doesNotMatch(await panel.innerText(), /完成未测试/);
  await panel.getByRole("button", { name: "导入并保存完整码值备份", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="码值备份"]').options.length === 2);
  const originalId = await panel.getByLabel("码值备份").locator("option").nth(1).getAttribute("value");
  await panel.getByLabel("码值备份").selectOption(originalId);
  await panel.getByLabel("码值块", { exact: true }).selectOption("LID:01");
  await page.waitForFunction(() => document.querySelector('[aria-label="设码字段"]').options.length === 4);
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
  const effects = page.getByTestId("coding-field-effects");
  assert.match(await effects.innerText(), /巡航控制（测试定义）.*所选字段.*是\s*→\s*否/s);
  assert.match(await effects.innerText(), /巡航关联指示（测试定义）.*同块相关字段.*开启\s*→\s*关闭/s);
  assert.doesNotMatch(await effects.innerText(), /保留字段/);
  await panel.getByRole("button", { name: "保存离线修改或恢复方案", exact: true }).click();
  await panel.getByText("离线方案已保存。", { exact: true }).waitFor();
  const savedPreview = JSON.parse(await fs.readFile(path.join(temp, "saved-coding-preview.json"), "utf8"));
  assert.equal(savedPreview.vehicleWritten, false);
  assert.equal(savedPreview.result.effects.length, 2);
  assert.equal(savedPreview.result.afterHex, "A4");
  await page.screenshot({ path: path.join(temp, "coding-field-effects.png"), fullPage: true });
  await panel.getByLabel("设码合法值").selectOption("1");
  assert.equal(await effects.count(), 0, "changing target clears old field changes");
  await panel.getByRole("button", { name: "预览码值修改", exact: true }).click();
  await page.getByTestId("coding-no-changes").waitFor();
  assert.equal(await effects.count(), 0);
  await panel.getByLabel("设码合法值").selectOption("0");
  await panel.getByRole("button", { name: "模拟设码、备份与回读", exact: true }).click();
  await page.getByTestId("restore-simulation").waitFor();
  assert.equal(await effects.locator("li").count(), 2, "rehearsal retains the reviewed field changes");
  assert.match(await page.getByTestId("coding-operation-state").innerText(), /完整回读已核对并保存/);
  const { createCodingOperationJournal } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/coding-operation-journal.mjs")));
  const journal = createCodingOperationJournal({ directory: path.join(temp, "user-data/diagnostics/coding-backups/operations") });
  const successful = await journal.list();
  assert.equal(successful.length, 1); assert.equal(successful[0].phase, "completed");
  assert.equal(successful[0].metadata.mode, "simulation"); assert.equal(successful[0].automaticWriteRetry, false);
  assert.match(await page.getByTestId("coding-operation-state").innerText(), new RegExp(successful[0].operationId));
  const variantsFile = path.join(temp, "variants.jsonl"), validVariants = await fs.readFile(variantsFile, "utf8");
  const unresolved = JSON.parse(validVariants);
  unresolved.pool_records.coding.records.push({ at: 4047903, name: "未核实布局字段（测试定义）", byteOffset: null,
    bitOffset: 0, readSID: 0x21, pid: 1, read_request_candidate_hex: "2101", formula: { text: "IDENTICAL:BitLength=8;" } });
  unresolved.pool_records.coding.count += 1;
  await fs.writeFile(variantsFile, JSON.stringify(unresolved) + "\n");
  await panel.getByRole("button", { name: "预览码值修改", exact: true }).click();
  await panel.getByText("修改涉及同块中布局尚未核实的字段，预览已停止。", { exact: true }).waitFor();
  assert.equal(await effects.count(), 0, "failed preview cannot retain a previous successful diff");
  assert.equal(await panel.getByRole("button", { name: "保存离线修改或恢复方案", exact: true }).count(), 0);
  await fs.writeFile(variantsFile, validVariants);
  await panel.getByRole("button", { name: "导入并保存完整码值备份", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="码值备份"]').options.length === 4);
  const currentId = await panel.getByLabel("码值备份").locator("option").filter({ hasText: "后续备份 · 981 · TEST-SW · 2026-10-05T00:01:00Z" }).getAttribute("value");
  assert.notEqual(originalId, currentId);
  await page.reload();
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  await page.waitForFunction(() => document.querySelector('[aria-label="码值备份"]')?.options.length === 4);
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
  assert.match(await page.getByTestId("coding-operation-state").innerText(), /结果未知，需核对/);
  const persisted = await createCodingOperationJournal({ directory: path.join(temp, "user-data/diagnostics/coding-backups/operations") }).list();
  const unknown = persisted.find(o => o.phase === "outcome-unknown");
  assert.ok(unknown); assert.equal(unknown.intended.length, 1); assert.equal(unknown.verified.length, 0);
  assert.equal(unknown.requiresReconciliation, true); assert.equal(unknown.automaticWriteRetry, false);
  assert.notEqual(unknown.operationId, successful[0].operationId);
  assert.match(await page.getByTestId("coding-operation-state").innerText(), new RegExp(unknown.operationId));
  await fs.writeFile(path.join(temp, "journal-verification.json"), JSON.stringify({ ok: true, source: root,
    operations: persisted, actualVehicleWrite: false, newVehicleQualification: false, powerLossDurabilityVerified: false }, null, 2));
  await page.screenshot({ path: path.join(temp, "coding.png"), fullPage: true });
  if (codingOnly) {
    const priorPid = app.process().pid;
    await app.close(); app = undefined;
    app = await electron.launch({ executablePath: process.env.PORSCHE981_ACCEPT_ELECTRON_EXECUTABLE,
      args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
    assert.notEqual(app.process().pid, priorPid);
    const restartPage = await app.firstWindow(); restartPage.setDefaultTimeout(15000);
    const restarted = await app.evaluate(async (_electron, input) => {
      const vm = process.getBuiltinModule("vm");
      const mod = await vm.runInThisContext(`import(${JSON.stringify(input.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
      return mod.createCodingOperationJournal({ directory: input.directory }).list();
    }, { module: pathToFileURL(path.join(root, "apps/desktop/electron/coding-operation-journal.mjs")).href, directory: path.join(temp, "user-data/diagnostics/coding-backups/operations") });
    assert.deepEqual(restarted, persisted);
    await restartPage.locator('nav.side button[data-tab="obd"]').click();
    await restartPage.locator('[data-obd-tab="coding"]').click();
    await restartPage.locator('[data-coding-system="dme"]').click();
    await restartPage.locator('[data-coding-category="coding"]').click();
    const history = restartPage.getByTestId("coding-operation-history");
    await history.locator(`[data-operation-id="${unknown.operationId}"]`).waitFor();
    assert.match(await history.locator(`[data-operation-id="${unknown.operationId}"]`).innerText(), /模拟操作.*结果未知，需核对/);
    assert.match(await history.innerText(), new RegExp(successful[0].operationId));
    assert.equal(await restartPage.getByRole("button", { name: "写入车辆", exact: false }).isDisabled(), true);
    const reloaded = await restartPage.evaluate(() => window.porsche981.diagnosticPreparation({ action: "operation-history", ecu: "dme" }));
    assert.equal(reloaded.history.currentEcuTotal, persisted.length);
    assert.ok(reloaded.operations.every(row => row.automaticWriteRetry === false && row.mode === "simulation"));
    await restartPage.screenshot({ path: path.join(temp, "operation-history-restarted.png"), fullPage: true });
    await fs.writeFile(path.join(temp, "restart-verification.json"), JSON.stringify({ ok: true, priorPid, restartedPid: app.process().pid,
      sameEvents: true, productionIpcHistoryVisibleAfterRestart: true,
      operations: restarted.map(o => ({ operationId: o.operationId, phase: o.phase, automaticWriteRetry: o.automaticWriteRetry })), actualVehicleWrite: false }, null, 2));
  }
  if (!codingOnly) {
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
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "record-start" }))).ok, true);
  await page.locator('[data-obd-tab="coding"]').click();
  // Wait for actual receive progress while the receiver's page is unmounted.
  // Fast page switches may finish before the next injected 100 ms frame.
  await waitForIpc(page, async (count) =>
    (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > count, before);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).recording.active, true);
  assert.equal(await page.getByTestId("internal-record-stop").count(), 0, "recording element removed");
  await page.locator('nav.side button[data-tab="settings"]').click();
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).recording.active, true);
  const after = await page.evaluate(async () => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount);
  assert.ok(after > before, "page switches do not stop internal receive");
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "record-stop" }))).ok, true);
  await page.screenshot({ path: path.join(temp, "global-internal-settings.png"), fullPage: true });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("internal-can-panel").waitFor();
  assert.ok((await fs.stat(path.join(temp, "explicit.pcapng"))).size > 100);
  await page.getByTestId("internal-save").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-can-panel"]').textContent.includes("已保存本批结果"));
  assert.ok(JSON.parse(await fs.readFile(path.join(temp, "explicit-result.json"), "utf8")).frames.length > 0);
  await page.screenshot({ path: path.join(temp, "internal.png"), fullPage: true });
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "record-start" }))).ok, true);
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).recording?.frameCount > 0);
  await page.locator('[data-obd-tab="coding"]').click();
  await app.evaluate(() => { globalThis.__internalDeadlineClock.offset += 600000; });
  const internalDeadline = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(internalDeadline.connected, true);
  assert.equal(internalDeadline.internal.receiveState, "standby");
  assert.equal(internalDeadline.internal.stopReason, "time-limit");
  assert.equal(internalDeadline.recording.active, false);
  assert.equal(internalDeadline.recording.reason, "time-limit");
  assert.equal(await page.getByTestId("obd-recording-controls").count(), 0);
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("internal-time-limit").filter({ hasText: "已停止接收并保留诊断头连接" }).waitFor();
  assert.equal(await page.getByTestId("internal-record-start").count(), 0);
  await page.getByTestId("internal-save").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="internal-can-panel"]').textContent.includes("已保存本批结果"));
  const internalSaved = JSON.parse(await fs.readFile(path.join(temp, "explicit-result.json"), "utf8"));
  assert.equal(internalSaved.stopReason, "time-limit");
  assert.equal(internalSaved.frames.length, internalDeadline.internal.retainedFrames);
  assert.ok((await fs.readFile(path.join(temp, "internal-time-limit.pcapng"))).includes(Buffer.from('"reason":"time-limit"')));
  await page.screenshot({ path: path.join(temp, "internal-time-limit.png"), fullPage: true });
  await page.getByTestId("internal-new-batch").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.retainedFrames > 0);
  const nextInternal = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(nextInternal.connected, true);
  assert.notEqual(nextInternal.internal.receiveBatchId, internalDeadline.internal.receiveBatchId);
  assert.equal(nextInternal.internal.receiveState, "receiving");
  assert.equal(nextInternal.internal.stopReason, null);

  await page.locator('[data-obd-tab="connection"]').click(); await page.getByTestId("obd-conn-disconnect").click();
  await page.getByTestId("obd-purpose-diagnostic").check();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme"); await page.getByTestId("eng-select-0C").check();
  await page.getByTestId("eng-cycles").fill("1"); await page.getByTestId("eng-interval").fill("500");
  const diagnosticFile = path.join(temp, "diagnostic-explicit.pcapng");
  await app.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, diagnosticFile);
  assert.equal((await page.evaluate(() => window.porsche981.diagnosticCanRecording({ action: "start", simulation: true, layer: "can" }))).ok, true);
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => /本批保留 ([2-9]|[1-9][0-9]+) 条/.test(document.querySelector('[data-testid="eng-batch-count"]')?.textContent || ""));
  await page.getByTestId("eng-cancel").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="eng-export"]')?.disabled);
  assert.match(await page.getByTestId("eng-batch-count").innerText(), /当前为模拟数据/);
  assert.equal((await page.evaluate(() => window.porsche981.diagnosticCanRecording({ action: "stop" }))).ok, true);
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
  // Use the production main manager and Python simulation worker. Advancing
  // its trusted clock must stop the existing UI batch, not create a fresh one.
  await app.evaluate(async ({ ipcMain, dialog }, args) => {
    const vm = process.getBuiltinModule("vm");
    const fs = process.getBuiltinModule("fs");
    const { saveEngineRecording } = await vm.runInThisContext(
      `import(${JSON.stringify(args.saveModule)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createReadOnlySessionManager, SESSION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const test = { offset: 0 };
    test.manager = createReadOnlySessionManager({ repoRoot: args.root, nowFn: () => Date.now() + test.offset,
      saveEngineRecording: recording => saveEngineRecording(recording, { dialog, openFile: fs.promises.open }),
      env: { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1" } });
    globalThis.__engineDeadlineUi = test;
    ipcMain.removeHandler(SESSION_CHANNEL);
    ipcMain.handle(SESSION_CHANNEL, (event, request) => test.manager.handle(request, { ownerId: event.sender.id }));
  }, { root, module: pathToFileURL(path.join(root, "apps/desktop/electron/read-only-session.mjs")).href,
    saveModule: pathToFileURL(path.join(root, "apps/desktop/electron/recording-files.mjs")).href });
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => /本批保留 ([2-9]|[1-9][0-9]+) 条/.test(document.querySelector('[data-testid="eng-batch-count"]')?.textContent || ""));
  await app.evaluate(() => { globalThis.__engineDeadlineUi.offset = 600000; });
  await page.getByTestId("eng-progress").filter({ hasText: "本批已到十分钟" }).waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="eng-export"]')?.disabled);
  const deadlineFile = path.join(temp, "standard-time-limit.json");
  await app.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, deadlineFile);
  await page.getByTestId("eng-export").click();
  await page.getByTestId("eng-saved").filter({ hasText: "已保存" }).waitFor();
  const deadlineSaved = JSON.parse(await fs.readFile(deadlineFile, "utf8"));
  assert.match(await page.getByTestId("eng-progress").innerText(), /本批已到十分钟/);
  assert.equal(deadlineSaved.stopReason, "time-limit");
  assert.equal(deadlineSaved.retentionComplete, true);
  assert.ok(deadlineSaved.samples.length >= 2);
  assert.equal(deadlineSaved.samples.length, deadlineSaved.runs.reduce((n, run) => n + run.engine.samples.length, 0));
  assert.equal(deadlineSaved.simulation, true);
  await page.screenshot({ path: path.join(temp, "standard-time-limit.png"), fullPage: true });
  }
  assert.deepEqual(errors, []);
  console.log(codingOnly ? "coding operation journal UI PASS: successful full readback and unknown interrupted write persisted, distinct operation IDs, no automatic retry, no actual vehicle write" : "offline workflow UI PASS: imported immutable coding baseline, legal field preview, scoped restore and failed-write simulation, raw recording UI absent with retained explicit recording tested via IPC including outside OBD, internal receiver across pages, explicit PCAPNG/result files, continuous complete batches, main-owned ten-minute cutoff with complete explicit save and actual Python-to-main raw frame streaming");
} catch (error) {
  if (app) {
    const page = await app.firstWindow().catch(() => null);
    if (page) {
      await fs.writeFile(path.join(temp, "failure.txt"), await page.locator("body").innerText().catch(() => "unavailable"));
      await page.screenshot({ path: path.join(temp, "failure.png"), fullPage: true }).catch(() => {});
    }
  }
  throw error;
} finally {
  if (app) { await app.evaluate(async () => { await globalThis.__engineDeadlineUi?.manager.shutdown(); await globalThis.__offlineUiConnection?.shutdown(); }).catch(() => {}); await app.close(); }
}
