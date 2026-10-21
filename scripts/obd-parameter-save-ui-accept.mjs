/** Current renderer + real offline Python/parameter storage. Identity/start routing explicitly modeled; no hardware. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { installRealtimeUiIdentityModel, selectIdentifiedRealtimeUnit } from "./obd-live-ui-identity-model.mjs";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";
const root = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT), output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-parameter-export-20261012/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), desktop = path.join(temp, "Desktop"); await fs.mkdir(desktop);
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "sessions") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
if (appRoot) verifyInstalledEntry(appRoot);
const library = path.join(temp, "library");
const bundle = path.resolve(process.env.PORSCHE981_ACCEPT_DEFINITIONS_ZIP || path.join(root, ".local/obd-coding-readiness-20261009/definitions-live-current.zip"));
Object.assign(env, { PORSCHE981_DEFINITION_ROOT: library,
  PORSCHE981_REALTIME_PREPARATION: path.join(library, ".local/diagnostics/realtime-preparation"),
  PORSCHE981_VARIANTS: path.join(library, ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl"),
  PORSCHE981_LANGUAGE: path.join(library, ".local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP") });
async function stopAfterSamples(page, minimum) {
  await page.waitForFunction(n => Number(document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.match(/(\d+) 个数值/)?.[1] || 0) >= n, minimum, { timeout: 60000 });
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
}
let app, passed = false; const checks = [], errors = [];
const timeout = setTimeout(() => { console.error("parameter save UI total timeout"); void app?.close(); }, 180000);
function samples(bytes) {
  const rows = [];
  for (let offset = 0; offset < bytes.length;) {
    const length = bytes.readUInt32LE(offset + 4); assert.ok(length >= 12 && length % 4 === 0);
    assert.equal(bytes.readUInt32LE(offset + length - 4), length);
    if (bytes.readUInt32LE(offset) === 6) {
      const captured = bytes.readUInt32LE(offset + 20);
      rows.push(JSON.parse(bytes.subarray(offset + 40, offset + 28 + captured).toString("utf8")));
    }
    offset += length;
  }
  return rows;
}
try {
  app = await electron.launch({ args: [appRoot || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ app, shell, dialog }, desktop) => {
    app.setPath("desktop", desktop);
    const fs = process.getBuiltinModule("fs"), path = process.getBuiltinModule("path"), originalOpen = fs.promises.open;
    const state = globalThis.__parameterSave = { fail: false, revealed: [], dialogs: 0, originalOpen };
    dialog.showSaveDialog = async () => { state.dialogs++; throw new Error("unexpected Save As dialog"); };
    shell.showItemInFolder = file => state.revealed.push(file);
    fs.promises.open = async (...args) => {
      const handle = await originalOpen(...args);
      if (path.dirname(args[0]) !== desktop || !path.basename(args[0]).startsWith(".flat-six-parameters-")) return handle;
      return { write: (...values) => state.fail ? Promise.reject(Object.assign(new Error("injected"), { code: state.errorCode || "ENOSPC" })) : handle.write(...values),
        sync: () => handle.sync(), close: () => handle.close() };
    };
  }, desktop);
  await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, bundle);
  const imported = await page.evaluate(() => window.porsche981.diagnosticDefinitionBundle({ action: "import" })); assert.equal(imported.ok, true, JSON.stringify(imported));
  const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
  const directory = await page.evaluate(profileId => window.porsche981.offlineDiagnostics({ action: "ready-parameters", generation: "981", ecuId: 1, profileId, limit: 40 }), profileId);
  assert.equal(directory.ok, true); const parameters = directory.items.filter(p => p.decoderReady && p.nameResolved && p.decodedSampleCount > 0).slice(0, 6); assert.equal(parameters.length, 6);
  // Only redirect the test's read button to the retained real offline worker.
  // The production physical read handler stays denied in this process.
  await app.evaluate(({ ipcMain }) => {
    const realStream = ipcMain._invokeHandlers.get("diagnostics:manufacturerStream");
    globalThis.__realtimeUiReadHandler = (event, request) => request.action === "start-read"
      ? globalThis.__parameterSave.startError ? { ok: false, error: globalThis.__parameterSave.startError }
        : realStream(event, { action: "start", profileId: request.profileId, parameterIds: request.parameterIds, continuous: true })
      : realStream(event, request);
  });
  await installRealtimeUiIdentityModel(app, { 1: profileId, 9: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1" });
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="live"]').click();
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId });
  for (const parameter of parameters) await page.getByTestId("offline-select-" + parameter.id).check();
  await page.getByTestId("manufacturer-read").click(); await stopAfterSamples(page, 18);
  const visible = () => page.locator('[data-testid="manufacturer-rehearsal"] > [data-parameter-id]').allTextContents();
  const before = await visible();
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-open-folder").waitFor();
  const [first] = (await fs.readdir(desktop)).filter(f => f.endsWith(".pcapng")); assert.ok(first);
  assert.match(first, /^SYNTHETIC-.*\.pcapng$/);
  const firstPath = path.join(desktop, first), data = await fs.readFile(firstPath), values = samples(data);
  assert.ok(values.length >= 18); assert.equal(new Set(values.map(row => row.name)).size, 6);
  for (const row of values) assert.deepEqual(Object.keys(row), ["name", "value", "unit", "capturedUtc"]);
  assert.doesNotMatch(data.toString(), /requestHex|pduHex|rawValue|sourceRunId|transactions|identity|PRIVATE/);
  assert.deepEqual(await visible(), before, "saving leaves data visible");
  await page.getByTestId("manufacturer-open-folder").click();
  assert.deepEqual(await app.evaluate(() => globalThis.__parameterSave.revealed), [firstPath]);
  checks.push("real offline Python batch saves all retained values to isolated system Desktop as parameter-only PCAPNG; success retains values; reveal receives only saved path");
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-open-folder").waitFor();
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".pcapng")).length, 2);
  assert.deepEqual(await fs.readFile(firstPath), data);
  await app.evaluate(() => { globalThis.__parameterSave.fail = true; });
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-error").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /磁盘空间不足/);
  assert.deepEqual(await visible(), before);
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".pcapng")).length, 2);
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".tmp")).length, 0);
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /已有数据保留.*释放.*重试/);
  assert.equal(await page.getByTestId("manufacturer-save-error-details").count(), 0);
  await app.evaluate(() => { globalThis.__parameterSave.errorCode = "SYNTHETIC_UNKNOWN_SAVE_FAILURE"; });
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-error").getByText(/请检查桌面写入权限和磁盘空间后重试/).waitFor();
  assert.doesNotMatch(await page.getByTestId("manufacturer-error").innerText(), /SYNTHETIC_UNKNOWN/);
  assert.equal(await page.getByTestId("manufacturer-save-error-details").count(), 0);
  assert.deepEqual(await visible(), before);
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".pcapng")).length, 2);
  checks.push("known and unknown save errors show a cause/remedy with no technical detail element; failed saves retain the same batch and publish no file");
  await app.evaluate(() => { globalThis.__parameterSave.fail = false; });
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-open-folder").waitFor();
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".pcapng")).length, 3);
  assert.equal(await page.getByTestId("manufacturer-save-error-details").count(), 0);
  assert.equal(await app.evaluate(() => globalThis.__parameterSave.dialogs), 0);
  checks.push("repeat save never overwrites; injected disk failure publishes no file and retains batch for successful retry; no Save As dialog");
  const selectedBeforeRestart = await page.locator('input[data-testid^="offline-select-"]:checked').evaluateAll(inputs => inputs.map(input => input.getAttribute("data-testid")));
  const priorFiles = new Set((await fs.readdir(desktop)).filter(name => name.endsWith(".pcapng")));
  await page.getByTestId("manufacturer-read").click(); await stopAfterSamples(page, 6);
  assert.deepEqual(await page.locator('input[data-testid^="offline-select-"]:checked').evaluateAll(inputs => inputs.map(input => input.getAttribute("data-testid"))), selectedBeforeRestart);
  const retainedCount = Number((await page.getByTestId("manufacturer-state").innerText()).match(/(\d+) 个数值/)[1]);
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-open-folder").waitFor();
  const newFiles = (await fs.readdir(desktop)).filter(name => name.endsWith(".pcapng") && !priorFiles.has(name)); assert.equal(newFiles.length, 1);
  const restarted = samples(await fs.readFile(path.join(desktop, newFiles[0]))); assert.equal(restarted.length, retainedCount);
  const firstTimes = new Set(values.map(sample => sample.capturedUtc)); assert.ok(restarted.every(sample => !firstTimes.has(sample.capturedUtc)));
  assert.deepEqual(await fs.readFile(firstPath), data);
  checks.push("restart retains selected parameters, saves exactly the new retained samples, and preserves prior files");
  await page.screenshot({ path: path.join(output, "saved-parameter-data.png"), fullPage: true });
  assert.equal((await page.evaluate(() => window.porsche981.revealParameterRecording({ saveId: "unknown", path: "/injected" }))).ok, false);
  await page.getByTestId("offline-select-" + parameters[0].id).uncheck();
  assert.equal(await page.getByTestId("manufacturer-open-folder").count(), 0);
  await app.evaluate(() => { globalThis.__parameterSave.startError = "identity-mismatch"; });
  await page.getByTestId("manufacturer-read").click(); await page.getByTestId("manufacturer-error").waitFor();
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".json")).length, 0);
  checks.push("selection clears the old saved-path action; failed empty batch cannot save or publish communication JSON");
  // Keep the retired standard page's real Python simulation and exporter
  // exercised through its existing preload API, without restoring that UI.
  const standard = await page.evaluate(async () => {
    const api = window.porsche981.readOnlySession;
    const request = { action: "start", profileId: "porsche-981-2014-dme", mode: "simulation", sessionTask: "engine",
      selectedPids: ["04", "05", "0C", "0D", "0F", "11"], sampleCycles: 3, intervalMs: 500 };
    const wait = async jobId => {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        const doc = await api({ action: "status", jobId });
        if (!doc.ok || doc.state && !["running", "cancelling"].includes(doc.state)) return doc;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      throw Error("retained standard simulation timeout");
    };
    const started = await api(request); const finished = await wait(started.jobId);
    const saved = await api({ action: "saveEngine", engineBatchId: started.engineBatchId });
    const failed = await api({ ...request, scenario: "identity-mismatch" }); const failure = await wait(failed.jobId);
    const unsaved = await api({ action: "saveEngine", engineBatchId: failed.engineBatchId });
    return { started, finished, saved, failed, failure, unsaved };
  });
  assert.equal(standard.started.ok, true); assert.equal(standard.finished.final.ok, true);
  assert.equal(standard.finished.final.engine.samples.length, 18); assert.equal(standard.saved.ok, true);
  const standardRows = samples(await fs.readFile(standard.saved.filePath)); assert.equal(standardRows.length, 18);
  assert.equal(new Set(standardRows.map(row => row.name)).size, 6);
  for (const row of standardRows) assert.deepEqual(Object.keys(row), ["name", "value", "unit", "capturedUtc"]);
  assert.equal(path.dirname(standard.saved.filePath), desktop);
  assert.equal(standard.failure.final.ok, false); assert.equal(standard.unsaved.ok, false);
  assert.equal((await fs.readdir(desktop)).filter(f => f.endsWith(".json")).length, 0);
  checks.push("retained standard preload/IPC runs real Python simulation, saves 18 samples for six parameters, and refuses failed empty-batch save without retired controls");
  assert.deepEqual(errors, []); passed = true;
  await fs.writeFile(path.join(output, "example-pcapng-path.txt"), firstPath + "\n");
} finally {
  if (app) { await app.evaluate(() => { if (globalThis.__parameterSave) process.getBuiltinModule("fs").promises.open = globalThis.__parameterSave.originalOpen; }).catch(() => {}); await app.close(); }
  clearTimeout(timeout);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, rendererErrors: errors, productionIPCAndPythonRehearsal: true, identityAndStartRoutingModeled: true,
    systemDesktopIsolated: true, diskFailureInjected: true, folderRevealShellIntercepted: true,
    installedEntryOnMac: Boolean(appRoot), deviceIO: false, vehicleIO: false, windowsExecuted: false, windowsDeliverySynchronized: false, goalComplete: false }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
