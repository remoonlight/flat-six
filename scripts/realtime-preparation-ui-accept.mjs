/** Real Electron/IPC/Python acceptance, isolated DB and no vehicle/device I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
if (process.env.PORSCHE981_ACCEPT_APP_ROOT) verifyInstalledEntry(root);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-offline-preparation-20261003/ui-accept"));
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, "ui-"));
const bundle = process.env.PORSCHE981_ACCEPT_DEFINITIONS_ZIP ? path.resolve(process.env.PORSCHE981_ACCEPT_DEFINITIONS_ZIP) : null;
if (process.env.PORSCHE981_ACCEPT_APP_ROOT && !bundle) throw new Error("Supply PORSCHE981_ACCEPT_DEFINITIONS_ZIP for the isolated installed-module acceptance");
const library = path.join(temp, "diagnostic-library");
const fixture = path.join(temp, "devices.json");
fs.writeFileSync(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_HEADLESS: "1",
  PYTHONDONTWRITEBYTECODE: "1", PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"), PORSCHE981_CAN_CAPTURE_ROOT: path.join(temp, "raw-captures"),
  PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "transient-sessions"),
  PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json") };
if (bundle) {
  assert.ok(fs.statSync(bundle).isFile(), "explicit current definitions ZIP required");
  // Mac Electron can load installed modules without app.isPackaged. Keep all
  // imported private definitions outside the immutable delivery in that case.
  env.PORSCHE981_DEFINITION_ROOT = library;
  env.PORSCHE981_REALTIME_PREPARATION = path.join(library, ".local/diagnostics/realtime-preparation");
  env.PORSCHE981_VARIANTS = path.join(library, ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl");
  env.PORSCHE981_LANGUAGE = path.join(library, ".local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP");
}
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false, imported = null; const errors = [];
const watchdog = setTimeout(() => { console.error("realtime UI total timeout"); void app?.close(); }, 180000);
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000);
  const startup = await app.evaluate(({ app }, entry) => {
    const { createRequire } = process.getBuiltinModule("module"), load = createRequire(entry);
    const cached = load.cache[load.resolve(entry)];
    return { appPath: app.getAppPath(), packaged: app.isPackaged, mainFile: cached?.filename || "", bootstrapLoaded: cached?.loaded === true };
  }, path.join(root, "apps/desktop/electron/installed-bootstrap.cjs"));
  if (process.env.PORSCHE981_ACCEPT_APP_ROOT) {
    assert.equal(path.resolve(startup.appPath), root);
    assert.equal(path.resolve(startup.mainFile), path.join(root, "apps/desktop/electron/installed-bootstrap.cjs"));
    assert.equal(startup.bootstrapLoaded, true);
  }
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  if (bundle) {
    const empty = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-units", generation: "981" }));
    assert.equal(empty.ok, true); assert.equal(empty.present, false, "empty private library has no fabricated definitions");
    await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, bundle);
    imported = await page.evaluate(() => window.porsche981.diagnosticDefinitionBundle({ action: "import" }));
    assert.equal(imported.ok, true, JSON.stringify(imported));
    assert.ok(imported.files > 0, "real import IPC populated the isolated definition library");
  }
  const units = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-units", generation: "981" }));
  assert.equal(units.ok, true); assert.equal(units.counts.units, 35); assert.equal(units.counts.parameters, 52299);
  // Candidates and definition-only versions must not be silently treated as the current car.
  for (const id of ["pdk", "airbag", "hvac", "pcm"]) {
    await page.getByTestId("eng-system").selectOption(id);
    await page.getByTestId("offline-version-unmatched").waitFor();
    assert.match(await page.getByTestId("offline-version-unmatched").innerText(), /厂商实时读取\(未完成\)/);
    assert.equal(await page.getByTestId("offline-variant").count(), 0);
    assert.equal(await page.getByTestId("offline-parameters").count(), 0);
    assert.equal(await page.getByTestId("offline-plan").count(), 0);
  }
  await page.getByTestId("eng-system").selectOption("dme");
  assert.equal(await page.getByTestId("eng-select-all").count(), 1);
  await page.getByTestId("eng-data-source").selectOption("x431");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 365 项"));
  assert.match(await page.getByTestId("offline-version").innerText(), /SDI9_1_981_3_4L_EU5/);
  // Change only reported UI context; this does not select or open a head.
  await app.evaluate(({ ipcMain }) => {
    globalThis.__labelAdapter = "vLinker";
    ipcMain.removeHandler("diagnostics:connection");
    ipcMain.handle("diagnostics:connection", (_event, request) => request.action === "status"
      ? { ok: true, model: globalThis.__labelAdapter, purpose: "diagnostic", connected: false, linkState: "idle" }
      : { ok: false, error: "label-check-no-device-operations" });
  });
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]').textContent.includes("未完成"));
  await app.evaluate(() => { globalThis.__labelAdapter = "PT3G"; });
  await page.getByTestId("manufacturer-read").filter({ hasText: "(未完成)" }).waitFor();
  assert.equal(await page.getByTestId("obd-recording-controls").count(), 0);
  const pt3gNames = await page.locator(".offline-parameter-name").allTextContents();
  assert.ok(pt3gNames.length > 0 && pt3gNames.every(name => name.endsWith("(未完成)")));
  await page.getByTestId("eng-data-source").selectOption("standard");
  const standardNames = await page.getByTestId("eng-parameters").locator("label > span").allTextContents();
  assert.ok(standardNames.length === 6 && standardNames.every(name => name.endsWith("(未完成)")));
  await app.evaluate(() => { globalThis.__labelAdapter = "VNCI"; });
  await page.getByTestId("eng-data-source").selectOption("x431");
  await page.waitForFunction(() => {
    const button = document.querySelector('[data-testid="manufacturer-read"]');
    return button && !button.textContent.includes("未完成");
  });
  await app.evaluate(() => { globalThis.__labelAdapter = "vLinker"; });
  assert.equal(await page.getByTestId("offline-variant").count(), 0);
  assert.equal(await page.getByTestId("offline-stop").count(), 0);
  assert.equal(await page.getByTestId("offline-save").count(), 0);
  assert.equal(await page.getByTestId("offline-replay").count(), 1);
  assert.equal(await page.getByRole("button", { name: "导出离线结果", exact: true }).count(), 0);
  assert.ok(await page.getByTestId("eng-system").evaluate((el) =>
    el.closest(".panel") === document.querySelector('[data-testid="offline-parameters"]')?.closest(".panel")));
  const categoryOptions = await page.getByTestId("offline-category").locator("option").allTextContents();
  assert.ok(categoryOptions.some((s) => s.includes("通用信息")));
  assert.ok(categoryOptions.every((s) => !s.includes("（0）")));
  let inputs = page.getByTestId("offline-parameters").locator('input[type="checkbox"]');
  await inputs.nth(1).check(); await inputs.nth(2).check();
  await page.getByTestId("offline-plan").click();
  await page.getByTestId("offline-plan-result").waitFor();
  await page.getByTestId("offline-save").waitFor();
  assert.equal(await page.getByTestId("offline-stop").isDisabled(), true);
  assert.equal(await page.getByTestId("offline-save").isEnabled(), true);
  assert.match(await page.getByTestId("offline-capture-state").innerText(), /尚未采集实车数据/);
  await page.getByTestId("offline-replay").click();
  await page.getByTestId("offline-replay-result").waitFor();
  assert.match(await page.getByTestId("offline-replay-result").innerText(), /历史数据回放，不是当前车辆实时更新/);
  const filename = path.join(temp, "chosen-recording.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async (_window, options) => {
    if (options.title !== "保存此次采集") throw new Error("wrong dialog");
    return { canceled: false, filePath };
  }; }, filename);
  await page.getByTestId("offline-save").click();
  await page.getByTestId("offline-saved").waitFor();
  const recording = JSON.parse(fs.readFileSync(filename, "utf8"));
  assert.equal(recording.vehicleDataCollected, false); assert.equal(recording.kind, "x431-offline-plan");
  assert.equal(recording.parameters.length, 2); assert.equal(recording.profileId, "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5");
  assert.ok(recording.parameters.every(parameter => !parameter.name.endsWith("(未完成)")), "UI suffix never changes saved parameter identity");
  assert.equal(recording.result.executionEnabled, false); assert.equal(recording.result.writePayload, null);
  assert.ok(recording.startedAt && recording.finishedAt);
  await app.evaluate(({ dialog }) => { dialog.showSaveDialog = async () => ({ canceled: true }); });
  await page.getByTestId("offline-save").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="offline-save"]').disabled);
  assert.equal(await page.getByTestId("offline-saved").count(), 0);
  assert.equal(await page.getByTestId("offline-plan-result").count(), 1, "cancel Save As retains result");
  // The vehicle action reaches the live gate, which denies headless acceptance
  // before any physical child. It must not reuse historical values as live.
  assert.equal(await page.getByTestId("manufacturer-read").isDisabled(), true);
  await page.getByTestId("manufacturer-x431-inactive").check();
  await page.getByTestId("manufacturer-readonly").check();
  assert.equal(await page.getByTestId("obd-recording-controls").count(), 0);
  await page.getByTestId("manufacturer-read").click();
  await page.getByTestId("manufacturer-error").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /live_not_enabled/);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /0 轮/);
  // Executable grouped manufacturer rehearsal through real IPC/Python. It
  // never imports a serial transport or grants a manufacturer live route.
  await page.getByTestId("manufacturer-start").click();
  await page.waitForFunction(() => {
    const text = document.querySelector('[data-testid="manufacturer-state"]')?.textContent || "";
    return /[2-9] 轮/.test(text);
  });
  assert.equal(await page.getByTestId("eng-system").isDisabled(), true);
  assert.equal(await page.getByTestId("offline-category").isDisabled(), true);
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]').disabled);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /已停止/);
  assert.equal(await page.getByTestId("eng-system").isEnabled(), true);
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, temp);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-error").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /文件夹|允许|权限/);
  const rehearsalFile = path.join(temp, "manufacturer.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, rehearsalFile);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-saved").waitFor();
  const rehearsal = JSON.parse(fs.readFileSync(rehearsalFile, "utf8"));
  assert.equal(rehearsal.kind, "manufacturer-acquisition-rehearsal");
  assert.equal(rehearsal.vehicleDataCollected, false); assert.equal(rehearsal.simulation, true);
  assert.equal(rehearsal.cycles.length, 1, "single worker final summary");
  const final = rehearsal.cycles[0], completedCycles = final.completedCycles;
  assert.ok(completedCycles >= 2);
  assert.equal(rehearsal.samples.length, completedCycles * 2);
  assert.equal(rehearsal.cycleResults.length, completedCycles);
  assert.ok(rehearsal.cycleResults.every(chunk => chunk.transactions.length === 1 && chunk.completedCycles === 1));
  assert.equal(final.transport.closed, true); assert.deepEqual(final.transport.closeErrors, []);
  assert.equal(final.transport.rawLog.filter(entry => entry.req === "1089").length, 1, "session entered once across cycles");
  assert.equal(final.transport.rawLog.filter(entry => entry.req === rehearsal.cycleResults[0].transactions[0].requestHex).length, completedCycles);
  assert.equal(rehearsal.termination.finalReceived, true); assert.equal(rehearsal.termination.error, "cancelled");
  assert.equal(new Set(rehearsal.samples.map(sample => sample.cycle)).size, completedCycles);
  fs.copyFileSync(rehearsalFile, path.join(output, "manufacturer-complete-batch.json"));
  await page.screenshot({ path: path.join(output, "manufacturer-stopped.png"), fullPage: true });
  assert.ok(rehearsal.samples.every((sample) => sample.synthetic && sample.processedUtc && sample.pduSha256));
  // A deliberately fast synthetic producer finishes before the UI polls. The
  // actual manager/IPC/UI must drain every bounded page and save the whole batch.
  await app.evaluate(async ({ ipcMain, dialog }, args) => {
    const vm = process.getBuiltinModule("vm"), fs = process.getBuiltinModule("fs");
    const load = url => vm.runInThisContext(`import(${JSON.stringify(url)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createManufacturerStream } = await load(args.module);
    const { saveManufacturerRecording } = await load(args.saveModule);
    const { EventEmitter } = process.getBuiltinModule("events"), { PassThrough } = process.getBuiltinModule("stream");
    const manager = createManufacturerStream({ repoRoot: args.root,
      saveRecording: recording => saveManufacturerRecording(recording, { dialog, openFile: fs.promises.open }),
      spawnFn() {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
        child.kill = () => { child.emit("close", 1); };
        child.stdin.once("data", raw => {
          const request = JSON.parse(raw.toString());
          queueMicrotask(() => {
            child.emit("spawn");
            const flags = { executionEnabled: false, liveVerified: false, writePayload: null, simulation: true,
              vehicleDataCollected: false, sourceKind: "historical-fixture-rehearsal", ok: true };
            let connectionCount = 0;
            const notify = (phase, attempt, connectionEpoch, reason = null) => {
              child.stdout.write(JSON.stringify({ event: "connection-state", result: {
                executionEnabled: false, liveVerified: false, writePayload: null, simulation: true,
                sourceKind: flags.sourceKind, sequence: ++connectionCount, episode: 1, phase, attempt,
                connectionEpoch, reason, elapsedMs: 5000 + connectionCount, observedUtc: "2026-10-11T00:00:00+00:00" } }) + "\n");
            };
            if (globalThis.__burstUi.eventsOnly) {
              notify("interrupted", 0, 1, "transport-disconnected");
              notify("failed", 0, 1, "recovery-close-failed");
              child.stdout.write(JSON.stringify({ event: "finished", result: { ...flags, ok: false,
                error: "recovery-close-failed", completedCycles: 0, requestCount: 0, connectionEventCount: connectionCount } }) + "\n");
              child.emit("close", 0); return;
            }
            const cycleEvent = cycle => {
              const samples = request.parameterIds.map((parameterId, i) => ({ ...args.samples.find(s => s.parameterId === parameterId),
                parameterId, cycle, synthetic: true, elapsedMs: cycle * 100,
                connectionEpoch: cycle <= 50 ? 1 : 2,
                value: cycle * 100 + i, display: String(cycle * 100 + i) }));
              const doc = { ...flags, completedCycles: 1, cycle,
                samples, transactions: [...new Set(samples.map(s => s.requestHex))].map(requestHex => ({
                  ...args.transactions.find(t => t.requestHex === requestHex), cycle, elapsedMs: cycle * 100,
                  connectionEpoch: cycle <= 50 ? 1 : 2 })), syntheticAcceptance: true,
                syntheticEvidence: "x".repeat(100000) };
              if (globalThis.__burstUi.corrupt && cycle === 2) doc.samples[0].pduSha256 = "0".repeat(64);
              child.stdout.write(JSON.stringify({ event: "cycle", result: doc }) + "\n");
            };
            if (globalThis.__burstUi.corrupt) { cycleEvent(1); cycleEvent(2); return; }
            for (let cycle = 1; cycle <= 50; cycle++) cycleEvent(cycle);
            notify("interrupted", 0, 1, "transport-disconnected");
            notify("retrying", 1, 1);
            // The test resumes each stage explicitly after the actual UI sees it.
            globalThis.__burstUi.qualify = () => {
              notify("qualified", 1, 2);
              globalThis.__burstUi.answer = () => {
                notify("recovered", 1, 2);
                for (let cycle = 51; cycle <= 100; cycle++) cycleEvent(cycle);
                child.stdout.write(JSON.stringify({ event: "finished", result: { ...flags, completedCycles: 100,
                  requestCount: 100, connectionEventCount: connectionCount, transport: { closed: true, closeErrors: [] } } }) + "\n");
                child.emit("close", 0);
              };
            };
          });
        });
        return child;
      } });
    globalThis.__burstUi = { manager };
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream", (event, request) => manager.handle(request, event.sender.id));
  }, { root, samples: rehearsal.samples.slice(0, 2), transactions: rehearsal.cycleResults[0].transactions,
    module: pathToFileURL(path.join(root, "apps/desktop/electron/manufacturer-stream.mjs")).href,
    saveModule: pathToFileURL(path.join(root, "apps/desktop/electron/recording-files.mjs")).href });
  await page.getByTestId("manufacturer-start").click();
  await page.waitForFunction(() => /第 1\/3 次重连/.test(document.querySelector('[data-testid="manufacturer-connection-state"]')?.textContent || ""));
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /50 轮 · 100 个/);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  await page.screenshot({ path: path.join(output, "manufacturer-reconnecting.png"), fullPage: true });
  await app.evaluate(() => globalThis.__burstUi.qualify());
  await page.waitForFunction(() => /身份核对一致，等待/.test(document.querySelector('[data-testid="manufacturer-connection-state"]')?.textContent || ""));
  assert.doesNotMatch(await page.getByTestId("manufacturer-connection-state").innerText(), /已恢复/);
  await app.evaluate(() => globalThis.__burstUi.answer());
  await page.waitForFunction(() => /100 轮 · 200 个/.test(document.querySelector('[data-testid="manufacturer-state"]')?.textContent || ""));
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]').disabled);
  assert.equal(await page.getByTestId("manufacturer-timing").count(), 2);
  for (const text of await page.getByTestId("manufacturer-timing").allTextContents()) {
    assert.match(text, /最近更新间隔：100\.0 毫秒/);
    assert.match(text, /通信中断分段 1 处/);
  }
  assert.equal(await page.locator('[data-testid="manufacturer-rehearsal"] svg polyline').count(), 4,
    "two parameter charts each retain two segments across the recorded connection change");
  const burstFile = path.join(output, "SYNTHETIC-paged-manufacturer-complete.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, burstFile);
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  const burst = JSON.parse(fs.readFileSync(burstFile, "utf8"));
  assert.equal(burst.samples.length, 200); assert.equal(burst.cycleResults.length, 100);
  assert.ok(fs.statSync(burstFile).size > 8 * 1024 * 1024);
  assert.ok(burst.samples.every((s, i) => s.value === (Math.floor(i / 2) + 1) * 100 + i % 2));
  assert.ok(burst.samples.every((s, i) => s.connectionEpoch === (i < 100 ? 1 : 2)),
    "full saved batch retains the source connection boundary");
  assert.equal(burst.simulation, true); assert.equal(burst.vehicleDataCollected, false);
  assert.equal(burst.connectionEventCount, 4);
  assert.deepEqual(burst.connectionEvents.map(e => e.phase), ["interrupted", "retrying", "qualified", "recovered"]);
  await page.screenshot({ path: path.join(output, "manufacturer-pages-complete.png"), fullPage: true });
  await app.evaluate(() => { globalThis.__burstUi.corrupt = true; });
  await page.getByTestId("manufacturer-start").click();
  await page.getByTestId("manufacturer-error").filter({ hasText: "关联不一致" }).waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]').disabled);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /1 轮 · 2 个/);
  const rejectedFile = path.join(output, "SYNTHETIC-evidence-rejection.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, rejectedFile);
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  const rejected = JSON.parse(fs.readFileSync(rejectedFile, "utf8"));
  assert.equal(rejected.termination.error, "stream_evidence_invalid");
  assert.equal(rejected.cycleResults.length, 1); assert.equal(rejected.samples.length, 2);
  assert.ok(rejected.samples.every(s => s.cycle === 1 && s.pduSha256 !== "0".repeat(64)));
  await page.screenshot({ path: path.join(output, "manufacturer-evidence-rejected.png"), fullPage: true });
  await app.evaluate(() => { globalThis.__burstUi.corrupt = false; globalThis.__burstUi.eventsOnly = true; });
  await page.getByTestId("manufacturer-start").click();
  await page.getByTestId("manufacturer-error").waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]').disabled);
  assert.match(await page.getByTestId("manufacturer-connection-state").innerText(), /恢复失败/);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /0 轮 · 0 个/);
  const eventsOnlyFile = path.join(output, "SYNTHETIC-manufacturer-events-only.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, eventsOnlyFile);
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  const eventsOnly = JSON.parse(fs.readFileSync(eventsOnlyFile, "utf8"));
  assert.equal(eventsOnly.samples.length, 0); assert.equal(eventsOnly.connectionEventCount, 2);
  assert.equal(eventsOnly.termination.error, "recovery-close-failed");
  // Production recorder + real Python/IPC/UI, with only the main clock advanced.
  // This verifies expiry without spending vehicle time or a ten-minute UI sleep.
  await app.evaluate(async ({ipcMain, dialog}, args) => {
    const vm=process.getBuiltinModule("vm"), fs=process.getBuiltinModule("fs");
    const {saveManufacturerRecording}=await vm.runInThisContext(`import(${JSON.stringify(args.saveModule)})`,
      {importModuleDynamically:vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER});
    const {createManufacturerStream}=await vm.runInThisContext(`import(${JSON.stringify(args.module)})`,
      {importModuleDynamically:vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER});
    const clock={offset:0};
    const manager=createManufacturerStream({repoRoot:args.root,nowFn:()=>Date.now()+clock.offset,
      saveRecording:recording=>saveManufacturerRecording(recording,{dialog,openFile:fs.promises.open})});
    globalThis.__deadlineUi={clock,manager};
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream",(event,request)=>manager.handle(request,event.sender.id));
  },{root,module:pathToFileURL(path.join(root,"apps/desktop/electron/manufacturer-stream.mjs")).href,
    saveModule:pathToFileURL(path.join(root,"apps/desktop/electron/recording-files.mjs")).href});
  await page.getByTestId("manufacturer-start").click();
  await page.waitForFunction(()=>/[1-9] 轮/.test(document.querySelector('[data-testid="manufacturer-state"]')?.textContent||""));
  await app.evaluate(()=>{globalThis.__deadlineUi.clock.offset+=600000;});
  await page.waitForFunction(()=>!document.querySelector('[data-testid="manufacturer-save"]').disabled);
  assert.match(await page.getByTestId("manufacturer-state").innerText(),/已到十分钟/);
  assert.equal(await page.getByTestId("eng-system").isEnabled(),true);
  const timedFile=path.join(temp,"manufacturer-time-limit.json");
  await app.evaluate(({dialog},filePath)=>{dialog.showSaveDialog=async()=>({canceled:false,filePath});},timedFile);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-saved").waitFor();
  const timed=JSON.parse(fs.readFileSync(timedFile,"utf8"));
  assert.equal(timed.termination.stopReason,"time-limit");
  assert.equal(timed.cycles[0].transport.closed,true);
  assert.deepEqual(timed.cycles[0].transport.closeErrors,[]);
  assert.ok(timed.samples.length>=2);
  assert.equal(timed.samples.length,timed.cycleResults.reduce((n,item)=>n+item.completedCycles*2,0));
  assert.equal(timed.vehicleDataCollected,false);
  fs.copyFileSync(timedFile,path.join(output,"manufacturer-time-limit.json"));
  const forbiddenRehearsal = await page.evaluate((parameterIds) => window.porsche981.offlineDiagnostics({
    action: "ready-acquire", ecuId: 1, profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
    parameterIds, mode: "live" }), recording.parameters.map((p) => p.id));
  assert.equal(forbiddenRehearsal.error, "forbidden_field");
  // Real worker cancellation through the window-owned IPC, without any device calls.
  const cancelled = await page.evaluate(async ({ profileId, parameterIds }) => {
    const id = crypto.randomUUID();
    const promise = window.porsche981.offlineDiagnostics({ action: "ready-plan", ecuId: 1, profileId, parameterIds }, id);
    const stopped = await window.porsche981.cancelOfflineDiagnostics(id);
    return { stopped, result: await promise };
  }, { profileId: recording.profileId, parameterIds: recording.parameters.map((p) => p.id) });
  assert.equal(cancelled.stopped.cancelled, true); assert.equal(cancelled.result.error, "cancelled");
  const secondGroup = await page.getByTestId("offline-category").locator("option").nth(1).getAttribute("value");
  await page.getByTestId("offline-category").selectOption(secondGroup);
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /0 轮/);
  await page.getByTestId("offline-category").selectOption("");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 365 项"));
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  inputs = page.getByTestId("offline-parameters").locator('input[type="checkbox"]');
  for (let i = 0; i < 12; i++) if (!await inputs.nth(i).isChecked()) await inputs.nth(i).check();
  assert.equal(await inputs.nth(12).isDisabled(), true);
  assert.equal(await page.getByTestId("offline-save").count(), 0, "selection change resets previous attempt");
  await page.setViewportSize({ width: 800, height: 900 });
  const readable = await page.getByTestId("offline-parameters").locator("label").first().evaluate((el) => ({
    checkbox: el.querySelector("input").getBoundingClientRect().width,
    name: el.querySelector("span").getBoundingClientRect().width, height: el.getBoundingClientRect().height }));
  assert.ok(readable.checkbox <= 20 && readable.name > 120 && readable.height <= 42);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: path.join(output, "current-controls.png"), fullPage: true });
  await page.getByTestId("eng-system").selectOption("gateway");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 1151 项"));
  assert.match(await page.getByTestId("offline-version").innerText(), /CAN_CAN_Gateway_A7_1/);
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  assert.equal(await page.getByTestId("offline-plan-result").count(), 0);
  // Shared 9x1 Gateway definitions still use the exact observed 981 identity,
  // address pair and historical reference. This never grants a 982 live route.
  assert.equal(await page.getByTestId("manufacturer-rehearsal").count(), 1);
  await page.getByTestId("offline-search").fill("里程");
  const mileage = page.getByTestId("offline-select-5fd482040869539e351eb1e79edd82be0d9d9ccf6bd794c4373f68d0ca75301e");
  await mileage.check();
  await page.getByTestId("manufacturer-x431-inactive").check();
  await page.getByTestId("manufacturer-readonly").check();
  await page.getByTestId("manufacturer-read").click();
  await page.getByTestId("manufacturer-error").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /live_not_enabled/);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  await page.getByTestId("manufacturer-start").click();
  await page.waitForFunction(() => /[2-9] 轮/.test(document.querySelector('[data-testid="manufacturer-state"]')?.textContent || ""));
  assert.equal(await page.getByTestId("eng-system").isDisabled(), true);
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]').disabled);
  const gatewayFile = path.join(temp, "gateway-manufacturer.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, gatewayFile);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-saved").waitFor();
  const gateway = JSON.parse(fs.readFileSync(gatewayFile, "utf8"));
  assert.equal(gateway.profileId, "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1");
  assert.equal(gateway.vehicleDataCollected, false); assert.equal(gateway.simulation, true);
  assert.ok(gateway.cycleResults.length >= 2);
  assert.equal(gateway.samples.length, gateway.cycleResults.length);
  assert.ok(gateway.samples.every(s => s.value === "126380" && s.synthetic));
  assert.equal(gateway.cycles[0].transport.rawLog.filter(entry => entry.req === "1003").length, 1);
  assert.equal(gateway.cycles[0].transport.closed, true);
  assert.equal(gateway.cycles[0].readContract.ecuId, 9);
  assert.equal(gateway.cycles[0].readContract.rxId, 0x77A);
  fs.copyFileSync(gatewayFile, path.join(output, "gateway-complete-batch.json"));
  await page.screenshot({ path: path.join(output, "gateway-stopped.png"), fullPage: true });
  await mileage.uncheck();
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /0 轮/);
  const bad = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-plan", ecuId: 1, profileId: "x", parameterIds: ["a".repeat(64)], serialPort: "COM999" }));
  assert.equal(bad.error, "forbidden_field");
  assert.equal(await page.getByTestId("offline-catalogue").count(), 0);
  assert.equal(await page.getByTestId("offline-profile").count(), 0);
  assert.doesNotMatch(await page.getByTestId("offline-realtime").innerText(), /资料范围|请选择定义版本/);
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, noHardware: true,
    moduleRoot: root, syntheticProducer: true, deadlineClockInjected: true, startup,
    definitionFilesImported: imported?.files || 0, recordingElementsRemoved: true,
    pagedManufacturerSamples: burst.samples.length, pagedManufacturerCycles: burst.cycleResults.length,
    pagedManufacturerBytes: fs.statSync(burstFile).size,
    checks: ["request/response evidence mismatch rejected; prior results remain saveable", "automatic verified version", "candidate exclusion", "scope and manual version controls removed", "one panel", "12 cap", "category resets selection, pagination retains it", "native Save As", "save canceled retains result", "real worker stop IPC", "historical replay", "grouped continuous manufacturer rehearsal", "Gateway source-bound continuous rehearsal and complete save", "Gateway live gate denies no-device request", "stop releases controls", "save failure retains complete batch", "manufacturer live request rejected", "narrow layout"], errors }, null, 2));
  console.log("PASS realtime controls: matched versions, selection, stopped worker, native Save As/cancel, provenance, historical replay, no vehicle I/O");
  passed = true;
} catch (error) {
  if (app) {
    const page = await app.firstWindow();
    console.error(JSON.stringify({ rendererErrors: errors, panelError: await page.getByTestId("offline-error").allTextContents(),
      panelTail: (await page.locator("body").innerText()).slice(-1200) }));
  }
  throw error;
} finally {
  clearTimeout(watchdog);
  if (app) await app.evaluate(async () => { await globalThis.__deadlineUi?.manager.shutdown(); await globalThis.__burstUi?.manager.shutdown(); }).catch(() => {});
  if (app) await app.close().catch(() => {});
  if (passed) fs.rmSync(temp, { recursive: true, force: true });
}
