/** Real Electron UI/IPC, injected transport and private synthetic files. No car.
 * Internal raw JSON saves below test an injected developer callback, not product parameter export. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { waitForIpc } from "./obd-accept-helpers.mjs";
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const codingOnly = process.env.PORSCHE981_ACCEPT_CODING_ONLY === "1";
const batchOnly = process.env.PORSCHE981_ACCEPT_CODING_BATCH_ONLY === "1";
const exitMode = process.env.PORSCHE981_ACCEPT_CODING_EXIT || "";
assert.ok(!exitMode || (batchOnly && ["window", "quit"].includes(exitMode)));
const scratch = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-offline-implementation-20261005"));
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(scratch, "ui-"));
const desktop = path.join(temp, "Desktop"); await fs.mkdir(desktop);
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
        formula: { text: batchOnly ? "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF001;[0x01]->0xF002;[0x02]->0xF003;[0x03]->0xF004;BitLength=2,BitMask=0,HighLow=1;" : "TEXTTABLE:DataType=A_UINT32,[0x00]->0xF001;[0x01]->0xF002;BitLength=1,BitMask=0,HighLow=1;" },
        enumText: { F001: "关闭", F002: "开启", F003: "组合二", F004: "组合三" } },
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
      livenessMs: 60000, retryDelayMs: () => 88,
      setTimeoutFn: (fn, ms) => {
        if (ms !== 88) return setTimeout(fn, ms);
        const token = { fn }; globalThis.__internalResumeRetry = () => { if (!token.cleared) fn(); }; return token;
      },
      clearTimeoutFn: token => { if (token?.fn) token.cleared = true; else clearTimeout(token); },
      monitorSpawnFn: () => {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.exitCode = null;
        let timer;
        let receiveBatchId, receiving = false;
        child.stdin.on("data", raw => {
          for (const line of raw.toString().trim().split("\n")) {
            const request = JSON.parse(line);
            if (request.action === "stop") {
              child.stdout.write(JSON.stringify({ type: "stopped", ok: true, restoration: { errors: [] } }) + "\n");
              child.kill(); continue;
            }
            if (["monitor", "receive-start"].includes(request.action)) { receiveBatchId = request.receiveBatchId; receiving = request.receiveMilliseconds > 0; }
            if (request.action === "receive-stop" && request.receiveBatchId === receiveBatchId) receiving = false;
            child.stdout.write(JSON.stringify({ type: request.action === "monitor" ? "handshake" : "reading", ok: true, commOk: true, simulation: false,
              deviceId: device.id, canNetwork: "drive", volts: 12.6, voltageSource: "atrv", at: Date.now(), receiveBatchId,
              receiveState: receiving ? "receiving" : "standby", receiveReason: receiving ? null : request.reason || "time-limit" }) + "\n");
            if (!timer) timer = setInterval(() => {
              if (receiving) child.stdout.write(JSON.stringify({ type: "frames", deviceId: device.id, canNetwork: "drive", simulation: false, receiveBatchId,
                frames: [{ canId: 0x123, extended: false, dataHex: "0102", timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" }] }) + "\n");
            }, 100);
          }
        });
        child.kill = () => { clearInterval(timer); child.exitCode = 0; child.emit("close", 0); return true; };
        globalThis.__internalDropLink = () => {
          child.stdout.write(JSON.stringify({ type: "error", ok: false, simulation: false, deviceId: device.id, error: "port-io:injected-drop" }) + "\n");
          child.stdout.write(JSON.stringify({ type: "stopped", ok: true, simulation: false, deviceId: device.id, restoration: { errors: [] } }) + "\n");
          child.kill();
        }; return child;
      } });
    globalThis.__offlineUiConnection = mgr;
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, async (_event, request) => {
      if (["new-batch", "receive-stop"].includes(request.action) && globalThis.__internalActionFailure) {
        const failure = globalThis.__internalActionFailure; globalThis.__internalActionFailure = null;
        if (failure.throw) throw new Error(failure.error);
        return { ...await mgr.handle({ action: "status" }), ok: false, error: failure.error };
      }
      return mgr.handle(request);
    });
  }, { root, temp, modules: Object.fromEntries(["preparation", "offline", "connection", "save"].map((name) => [name,
    pathToFileURL(path.join(root, "apps/desktop/electron", { preparation: "diagnostic-preparation.mjs", offline: "offline-diagnostics.mjs", connection: "obd-connection.mjs", save: "recording-files.mjs" }[name])).href])) });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  assert.equal(await page.getByTestId("diagnostic-preparation").count(), 0);
  const actions = page.locator('[aria-label="系统功能类别"]');
  for (const id of ["coding-backup-original", "coding-read-original"]) {
    assert.equal(await actions.getByTestId(id).count(), 1);
    await actions.getByTestId(id).click();
    assert.match(await page.getByTestId("coding-original-notice").innerText(), /尚未完成.*暂不能执行/);
  }
  assert.equal(await page.getByRole("heading", { name: "当前控制单元码值与恢复", exact: true }).count(), 0);
  await page.screenshot({ path: path.join(scratch, "coding-actions.png"), fullPage: true });
  // Retired editor UI is replaced by direct production IPC checks; no vehicle I/O.
  const backend = await page.evaluate(async () => {
    const call = req => window.porsche981.diagnosticPreparation({ ecu: "dme", ...req });
    const first = await call({ action: "import-backup" });
    const second = await call({ action: "import-backup" });
    const baseline = await call({ action: "backup", id: first.id });
    const current = await call({ action: "backup", id: second.id });
    const options = await call({ action: "coding-options", id: second.id, blockKey: "LID:01", recordAt: 4047899 });
    const preview = await call({ action: "coding-preview", id: second.id, blockKey: "LID:01", recordAt: 4047899, rawValue: 0 });
    const plan = await call({ action: "restore-plan", id: second.id });
    const completed = await call({ action: "simulate-restore", id: second.id });
    const interrupted = await call({ action: "simulate-restore", id: second.id, scenario: "disconnect" });
    const history = await call({ action: "operation-history" });
    const baselineAfter = await call({ action: "backup", id: first.id });
    return { first, second, baseline, current, options, preview, plan, completed, interrupted, history, baselineAfter };
  });
  for (const key of ["first", "second", "baseline", "current", "options", "preview", "plan", "completed", "interrupted", "history", "baselineAfter"]) assert.equal(backend[key].ok, true, JSON.stringify(backend[key]));
  assert.equal(backend.first.backups.find(row => row.id === backend.first.id).original, true);
  assert.equal(backend.second.backups.find(row => row.id === backend.second.id).original, false);
  assert.equal(backend.baseline.backup.blocks.find(block => block.identifierKind === "LID" && block.identifierHex === "01").dataHex, "A5");
  assert.equal(backend.current.backup.blocks.find(block => block.identifierKind === "LID" && block.identifierHex === "01").dataHex, "A4");
  assert.equal(backend.preview.afterHex, "A4");
  assert.equal(backend.plan.plan.changedBlocks, 1);
  assert.equal(backend.completed.result.ok, true);
  assert.equal(backend.interrupted.result.ok, false);
  assert.equal(backend.interrupted.result.operationState, "outcome-unknown");
  assert.notEqual(backend.completed.result.operationId, backend.interrupted.result.operationId);
  assert.deepEqual(backend.baselineAfter.backup, backend.baseline.backup);
  await fs.writeFile(path.join(scratch, "coding-backend-result.json"), JSON.stringify({ passed: true, retiredPanelAbsent: true, twoOriginalActionsAtCategoryLevel: true,
    immutableBaselinePreserved: true, scopedRestoreAndUnknownWritePreserved: true, vehicleIO: false, deviceIO: false, backend }, null, 2));
  if (!codingOnly && !batchOnly) {
  await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-refresh").click();
  await page.locator('input[name="obd-device"]').check();
  await page.getByTestId("obd-purpose-drive").check();
  await page.getByTestId("obd-conn-connect").click();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("internal-can-panel").waitFor();
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).internal.receiveState, "idle");
  assert.equal(await page.getByTestId("internal-stop").isDisabled(), true);
  for (const failure of [
    { error: "internal_receive_busy", expected: "本批接收尚未停止完成" },
    { error: "private-internal-error:C:/Users/private/driver", expected: "无法确认本次接收操作结果" },
    { error: "private-internal-ipc:C:/Users/private/driver", throw: true, expected: "无法确认本次接收操作结果" },
  ]) {
    await app.evaluate((_electron, failure) => { globalThis.__internalActionFailure = failure; }, failure);
    await page.getByTestId("internal-new-batch").click();
    await page.getByTestId("internal-action-message").filter({ hasText: failure.expected }).waitFor();
    assert.doesNotMatch(await page.getByTestId("internal-can-panel").innerText(), /private-internal|C:\/Users|internal_receive_busy/);
    assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).internal.receiveState, "idle");
  }
  await page.getByTestId("internal-new-batch").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.frameCount > 0);
  await assert.rejects(fs.stat(path.join(temp, "explicit.pcapng")), { code: "ENOENT" });
  await app.evaluate(() => globalThis.__internalDropLink());
  await page.getByTestId("internal-reconnect-status").filter({ hasText: "正在自动重连" }).waitFor();
  const dropped = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(dropped.linkState, "reconnecting");
  assert.ok(dropped.internal.retainedFrames > 0);
  await app.evaluate(() => globalThis.__internalResumeRetry());
  await waitForIpc(page, async count => (await window.porsche981.obdConnection({ action: "status" })).internal.retainedFrames > count, dropped.internal.retainedFrames);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).internal.receiveBatchId, dropped.internal.receiveBatchId);
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
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "save-result" }))).error, "internal_receive_busy");
  await app.evaluate(() => { globalThis.__internalActionFailure = { error: "private-stop-error:driver" }; });
  await page.getByTestId("internal-stop").click();
  await page.getByTestId("internal-action-message").filter({ hasText: "无法确认本次接收操作结果" }).waitFor();
  assert.doesNotMatch(await page.getByTestId("internal-can-panel").innerText(), /private-stop-error/);
  const beforeStopRetry = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(beforeStopRetry.internal.receiveState, "receiving");
  assert.ok(beforeStopRetry.internal.retainedFrames > 0);
  await page.getByTestId("internal-stop").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.receiveState === "standby");
  const stoppedBatch = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(stoppedBatch.connected, true);
  assert.equal(stoppedBatch.internal.stopReason, "user-stopped");
  assert.ok(stoppedBatch.internal.retainedFrames > 0);
  // Raw frames remain testable through the injected developer save callback,
  // after the same receive-stop confirmation required by parameter saves.
  const developerSaved = await page.evaluate(() => window.porsche981.obdConnection({ action: "save-result" }));
  assert.equal(developerSaved.saved.saved, true);
  assert.ok(JSON.parse(await fs.readFile(path.join(temp, "explicit-result.json"), "utf8")).frames.length > 0);
  assert.equal(await page.getByTestId("internal-frames").count(), 0);
  assert.equal(await page.getByTestId("internal-can-count").count(), 0);
  assert.doesNotMatch(await page.getByTestId("internal-can-panel").innerText(), /0x123|0102|帧/);
  await page.screenshot({ path: path.join(temp, "internal.png"), fullPage: true });
  await page.getByTestId("internal-new-batch").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.receiveState === "receiving");
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
  assert.equal(await page.getByTestId("internal-save").isDisabled(), true);
  const developerDeadlineSaved = await page.evaluate(() => window.porsche981.obdConnection({ action: "save-result" }));
  assert.equal(developerDeadlineSaved.saved.saved, true);
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

  await page.getByTestId("internal-stop").click();
  await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).internal.receiveState === "standby");
  await page.locator('[data-obd-tab="connection"]').click(); await page.getByTestId("obd-conn-disconnect").click();
  await page.getByTestId("obd-purpose-diagnostic").check();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme"); await page.getByTestId("eng-select-0C").check(); await page.getByTestId("eng-mode").selectOption("simulation");
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
  await app.evaluate(async ({ ipcMain, app }, args) => {
    const vm = process.getBuiltinModule("vm");
    const fs = process.getBuiltinModule("fs");
    const { saveParameterRecording } = await vm.runInThisContext(
      `import(${JSON.stringify(args.saveModule)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createReadOnlySessionManager, SESSION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const test = { offset: 0 };
    app.setPath("desktop", args.desktop);
    test.manager = createReadOnlySessionManager({ repoRoot: args.root, nowFn: () => Date.now() + test.offset,
      saveEngineRecording: recording => {
        test.saved = recording;
        return saveParameterRecording(recording, { desktopPath: args.desktop, openFile: fs.promises.open });
      },
      env: { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1" } });
    globalThis.__engineDeadlineUi = test;
    ipcMain.removeHandler(SESSION_CHANNEL);
    ipcMain.handle(SESSION_CHANNEL, (event, request) => test.manager.handle(request, { ownerId: event.sender.id }));
  }, { root, desktop, module: pathToFileURL(path.join(root, "apps/desktop/electron/read-only-session.mjs")).href,
    saveModule: pathToFileURL(path.join(root, "apps/desktop/electron/parameter-recording.mjs")).href });
  await page.getByTestId("eng-start").click();
  await page.waitForFunction(() => /本批保留 ([2-9]|[1-9][0-9]+) 条/.test(document.querySelector('[data-testid="eng-batch-count"]')?.textContent || ""));
  await app.evaluate(() => { globalThis.__engineDeadlineUi.offset = 600000; });
  await page.getByTestId("eng-progress").filter({ hasText: "本批已到十分钟" }).waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="eng-export"]')?.disabled);
  const retained = Number((await page.getByTestId("eng-batch-count").innerText()).match(/本批保留 (\d+)/)[1]);
  await page.getByTestId("eng-export").click();
  await page.getByTestId("eng-saved").filter({ hasText: "已保存" }).waitFor();
  const files = await fs.readdir(desktop); assert.equal(files.length, 1);
  assert.match(files[0], /^SYNTHETIC-DME-\d{8}T\d{6}Z-parameters.*\.pcapng$/);
  const bytes = await fs.readFile(path.join(desktop, files[0])), values = [];
  for (let offset = 0; offset < bytes.length;) {
    const length = bytes.readUInt32LE(offset + 4);
    assert.ok(length >= 12 && length % 4 === 0 && offset + length <= bytes.length);
    assert.equal(bytes.readUInt32LE(offset + length - 4), length);
    if (bytes.readUInt32LE(offset) === 6) {
      const captured = bytes.readUInt32LE(offset + 20);
      values.push(JSON.parse(bytes.subarray(offset + 40, offset + 28 + captured).toString("utf8")));
    }
    offset += length;
  }
  assert.equal(values.length, retained);
  for (const value of values) {
    assert.deepEqual(Object.keys(value), ["name", "value", "unit", "capturedUtc"]);
    assert.equal(value.name, "转速"); assert.equal(value.unit, "rpm");
    assert.ok(Number.isFinite(value.value) && Number.isFinite(Date.parse(value.capturedUtc)));
  }
  assert.doesNotMatch(bytes.toString(), /stopReason|runs|requestHex|pduHex|transactions/);
  // Check private retention/deadline metadata in the owned callback, never in the public parameter file.
  const deadlineSaved = await app.evaluate(() => globalThis.__engineDeadlineUi.saved);
  assert.match(await page.getByTestId("eng-progress").innerText(), /本批已到十分钟/);
  assert.equal(deadlineSaved.stopReason, "time-limit");
  assert.equal(deadlineSaved.retentionComplete, true);
  assert.ok(deadlineSaved.samples.length >= 2);
  assert.equal(deadlineSaved.samples.length, deadlineSaved.runs.reduce((n, run) => n + run.engine.samples.length, 0));
  assert.equal(deadlineSaved.simulation, true);
  await page.screenshot({ path: path.join(temp, "standard-time-limit.png"), fullPage: true });
  }
  assert.deepEqual(errors, []);
  if (!batchOnly) console.log(codingOnly ? "coding action layout and backend IPC PASS: immutable baseline, preview, scoped restore and unknown-write journal preserved; no vehicle I/O" : "offline workflow UI/IPC PASS: two coding actions, retained immutable baseline/preview/scoped restore backend, raw recording UI absent with retained explicit recording tested via IPC including outside OBD, internal receiver across pages, explicit PCAPNG/result files, continuous complete batches, main-owned ten-minute cutoff with complete explicit save and actual Python-to-main raw frame streaming");
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
  if (app) {
    if (exitMode) await app.evaluate(({ dialog }) => { dialog.showMessageBox = async () => ({ response: 1 }); }).catch(() => {});
    await app.evaluate(async () => { await globalThis.__engineDeadlineUi?.manager.shutdown(); await globalThis.__offlineUiConnection?.shutdown(); }).catch(() => {});
    await app.close();
  }
}
