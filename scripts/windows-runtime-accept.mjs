/** Test the real unpacked artifact with empty userData and no developer PATH. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawnSync } from "node:child_process";
import { resolvePythonCandidates } from "../apps/desktop/electron/offline-diagnostics.mjs";
import { verifyLaptopAcceptanceInputs } from "./laptop-accept-inputs.mjs";

const root = path.resolve(import.meta.dirname, "..");
const args = process.argv.slice(2);
const checkOnly = args.at(-1) === "--check-only";
if (checkOnly) args.pop();
if (args.length < 2 || args.length > 3 || args.some(arg => arg.startsWith("--"))) throw new Error("Supply delivery directory, private definitions ZIP, optional private output and --check-only");
const delivery = path.resolve(args[0]), bundle = path.resolve(args[1]);
const kitMarker = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).name === "flat-six-laptop-acceptance";
const kitRoot = kitMarker || fs.existsSync(path.join(root, "laptop-accept-manifest.json")) ? root : undefined;
const preflight = verifyLaptopAcceptanceInputs(delivery, bundle, kitRoot);
if (checkOnly) { console.log(JSON.stringify({ ok: true, ...preflight, playwrightModule: import.meta.resolve("playwright") })); process.exit(0); }
if (process.platform !== "win32") throw new Error("Windows execution required; --check-only is static validation, not Windows acceptance");
if (preflight.kitBound) assert.equal(fs.realpathSync(process.execPath).toLowerCase(),
  fs.realpathSync(path.join(delivery, "resources/app/runtime/node.exe")).toLowerCase(), "Run the kit using the delivery's bundled Node");
const outputBase = path.resolve(args[2] || path.join(root, ".local/obd-precar-software-20261005"));
const relativeOutput = path.relative(path.join(root, ".local"), outputBase);
if (relativeOutput.startsWith("..") || path.isAbsolute(relativeOutput)) throw new Error("Private output directory required");
fs.mkdirSync(outputBase, { recursive: true });
const output = fs.mkdtempSync(path.join(outputBase, "installed-ui-"));
const userData = path.join(output, "empty-user-data"); fs.mkdirSync(userData);
const devices = path.join(output, "devices.json"); fs.writeFileSync(devices, JSON.stringify({ devices: [], errors: [] }));
const inherited = { ...process.env };
for (const key of Object.keys(inherited)) if (key.toUpperCase().startsWith("PORSCHE981_") || ["PATH", "PYTHONPATH"].includes(key.toUpperCase())) delete inherited[key];
const env = { ...inherited, PATH: path.join(process.env.SystemRoot, "System32"), PYTHONPATH: "", PYTHONDONTWRITEBYTECODE: "1",
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_CONNECTION_FIXTURE: devices, PORSCHE981_USER_DATA: userData };
for (const key of ["ELECTRON_RUN_AS_NODE", "VITE_DEV_SERVER_URL", "PORSCHE981_NODE", "PORSCHE981_PYTHON", "PORSCHE981_DB",
  "PORSCHE981_VARIANTS", "PORSCHE981_REALTIME_PREPARATION", "PORSCHE981_LANGUAGE", "PORSCHE981_DEFINITION_ROOT",
  "PORSCHE981_CONNECTION_STATE", "PORSCHE981_SESSION_ARTIFACT_ROOT", "PORSCHE981_CAN_CAPTURE_ROOT", "PORSCHE981_LOCAL_ROOT"])
  delete env[key];
let app, timedOut = false;
const deadline = setTimeout(() => { timedOut = true; void app?.close().catch(() => {}); }, 300000);
deadline.unref();
const errors = [];
try {
  app = await electron.launch({ executablePath: path.join(delivery, "FlatSix.exe"), args: [], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000);
  page.on("pageerror", (e) => errors.push(e.message));
  const runtime = await app.evaluate(({ app }, entry) => {
    const load = process.getBuiltinModule("module").createRequire(entry), cached = load.cache[load.resolve(entry)];
    return { packaged: app.isPackaged, userData: app.getPath("userData"), appPath: app.getAppPath(),
      bootstrapFile: cached?.filename || "", bootstrapLoaded: cached?.loaded === true,
      python: process.env.PORSCHE981_PYTHON, node: process.env.PORSCHE981_NODE,
      connectionState: process.env.PORSCHE981_CONNECTION_STATE,
      sessionArtifacts: process.env.PORSCHE981_SESSION_ARTIFACT_ROOT,
      canCaptures: process.env.PORSCHE981_CAN_CAPTURE_ROOT, localAssets: process.env.PORSCHE981_LOCAL_ROOT };
  }, path.join(delivery, "resources/app/apps/desktop/electron/installed-bootstrap.cjs"));
  assert.equal(runtime.packaged, true); assert.equal(runtime.userData, userData);
  assert.equal(path.resolve(runtime.appPath), path.join(delivery, "resources/app"));
  assert.equal(path.resolve(runtime.bootstrapFile), path.join(delivery, "resources/app/apps/desktop/electron/installed-bootstrap.cjs"));
  assert.equal(runtime.bootstrapLoaded, true);
  assert.ok(runtime.python.startsWith(delivery)); assert.ok(runtime.node.startsWith(delivery));
  for (const key of ["connectionState", "sessionArtifacts", "canCaptures", "localAssets"])
    assert.ok(runtime[key].startsWith(userData + path.sep), key);
  const helper = "apps/desktop/electron/offline-diagnostics.mjs";
  assert.equal(fs.readFileSync(path.join(delivery, "resources/app", helper), "utf8"), fs.readFileSync(path.join(root, helper), "utf8"));
  const py = resolvePythonCandidates({ PORSCHE981_PYTHON: runtime.python })[0];
  const encoded = spawnSync(py.exe, [...py.prefix, "-c", "import sys,json;print(json.dumps({'utf8':sys.flags.utf8_mode,'text':'原码读取'},ensure_ascii=False))"],
    { encoding: "utf8", timeout: 10000, windowsHide: true, env });
  assert.equal(encoded.status, 0, encoded.stderr);
  const encoding = JSON.parse(encoded.stdout);
  assert.equal(encoding.utf8, 1); assert.equal(encoding.text, "原码读取");
  const beforeClockUs = Number(process.hrtime.bigint() / 1000n);
  const clockProbe = spawnSync(py.exe, [...py.prefix, "-c", "import json;from scripts.diagnostics.capture_clock import capture_timestamp;print(json.dumps(capture_timestamp()))"],
    { cwd: path.join(delivery, "resources/app"), encoding: "utf8", timeout: 10000, windowsHide: true, env });
  const afterClockUs = Number(process.hrtime.bigint() / 1000n);
  assert.equal(clockProbe.status, 0, clockProbe.stderr);
  const observedClock = JSON.parse(clockProbe.stdout);
  assert.equal(observedClock.monotonicClock, "windows-qpc");
  assert.ok(Number.isSafeInteger(observedClock.monotonicTimestampUs)
    && beforeClockUs - 1 <= observedClock.monotonicTimestampUs && observedClock.monotonicTimestampUs <= afterClockUs + 1,
    "installed Node/Python capture clocks must share the host QPC timeline");
  await page.waitForFunction(() => !!window.porsche981?.offlineDiagnostics);
  const setting = await page.evaluate(() => window.porsche981.obdConnection({ action: "configure", purpose: "internal", canNetwork: "drive" }));
  assert.equal(setting.ok, true, JSON.stringify(setting));
  assert.ok(fs.existsSync(path.join(userData, "diagnostics/connection.json")), "remembered connection settings belong in userData");
  await page.evaluate(() => window.porsche981.obdConnection({ action: "configure", purpose: "diagnostic", canNetwork: "drive" }));
  await page.evaluate(async () => {
    const initial = await window.porsche981.offlineDiagnostics({ action: "ready-units", generation: "981" });
    if (!initial.ok || initial.present !== false) throw new Error("empty install fabricated definitions");
  });
  await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, bundle);
  const imported = await page.evaluate(() => window.porsche981.diagnosticDefinitionBundle({ action: "import" }));
  assert.equal(imported.ok, true, JSON.stringify(imported));
  const units = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-units", generation: "981" }));
  assert.equal(units.ok, true); assert.equal(units.counts.parameters, 52299);
  const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
  const params = await page.evaluate((profileId) => window.porsche981.offlineDiagnostics({ action: "ready-parameters",
    generation: "981", ecuId: 1, profileId, limit: 100 }), profileId);
  const ids = params.items.filter((p) => p.decodedSampleCount > 0).slice(0, 12).map((p) => p.id);
  assert.equal(ids.length, 12);
  const deniedManufacturer = await page.evaluate(({ profileId, ids }) => window.porsche981.manufacturerStream({
    action: "start-read", profileId, parameterIds: ids, continuous: true, confirmedReadOnly: true, x431Inactive: true }), { profileId, ids });
  assert.equal(deniedManufacturer.error, "live_not_enabled", "headless acceptance never creates a physical worker");
  const acquired = await page.evaluate(({ profileId, ids }) => window.porsche981.offlineDiagnostics({ action: "ready-acquire",
    generation: "981", ecuId: 1, profileId, parameterIds: ids }), { profileId, ids });
  assert.equal(acquired.ok, true, JSON.stringify(acquired)); assert.equal(acquired.samples.length, 12);
  assert.equal(acquired.vehicleDataCollected, false); assert.equal(acquired.transport.closed, true);
  assert.ok(acquired.transport.rawLog.length > 0);
  const streamStart = await page.evaluate(({ profileId, ids }) => window.porsche981.manufacturerStream({
    action: "start", profileId, parameterIds: ids, continuous: true }), { profileId, ids });
  assert.equal(streamStart.ok, true, JSON.stringify(streamStart));
  let streaming, stopped = false;
  for (let count = 0; count < 150; count++) {
    streaming = await page.evaluate(sessionId => window.porsche981.manufacturerStream({ action: "status", sessionId }), streamStart.sessionId);
    assert.equal(streaming.ok, true, JSON.stringify(streaming));
    if (streaming.events.length >= 2 && !stopped) {
      await page.evaluate(sessionId => window.porsche981.manufacturerStream({ action: "stop", sessionId }), streamStart.sessionId);
      stopped = true;
    }
    if (streaming.state === "finished") break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(stopped, true); assert.equal(streaming.state, "finished");
  assert.equal(streaming.error, null); assert.equal(streaming.result.error, "cancelled");
  assert.equal(streaming.result.transport.closed, true); assert.deepEqual(streaming.result.transport.closeErrors, []);
  assert.equal(streaming.result.transport.rawLog.filter(entry => entry.req === "1089").length, 1);
  assert.ok(streaming.result.completedCycles >= 2);
  assert.equal(streaming.events.flatMap(event => event.samples).length, streaming.result.completedCycles * 12);
  assert.equal(streaming.events.length, streaming.result.completedCycles);
  fs.writeFileSync(path.join(output, "manufacturer-stream.json"), JSON.stringify(streaming));
  const engineJob = await page.evaluate(() => window.porsche981.readOnlySession({ action: "start", profileId: "porsche-981-2014-dme",
    mode: "simulation", sessionTask: "engine", sampleCycles: 1, intervalMs: 500, selectedPids: ["0C"] }));
  assert.equal(engineJob.ok, true, JSON.stringify(engineJob));
  let engine;
  for (let count = 0; count < 100; count++) {
    engine = await page.evaluate((jobId) => window.porsche981.readOnlySession({ action: "status", jobId }), engineJob.jobId);
    if (engine.final) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(engine.final?.ok, true, JSON.stringify(engine));
  assert.equal(engine.final.engine.samples.length, 1);
  const scope = await page.evaluate((profileId) => window.porsche981.offlineDiagnostics({ action: "catalog-coding-plan",
    generation: "981", ecuId: 1, profileId }), profileId);
  assert.equal(scope.ok, true, JSON.stringify(scope)); assert.equal(scope.plan.definitionFieldCount, 39);
  assert.equal(scope.plan.completeVehicleCodingScopeQualified, false);
  const syntheticIdentity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "TEST-HW", software: "TEST-SW" };
  const typedFiles = ["synthetic-original.json", "synthetic-current.json"].map((name) => path.join(output, name));
  for (const [i, file] of typedFiles.entries()) fs.writeFileSync(file, JSON.stringify({ schemaVersion: 2, kind: "ecu-coding-backup",
    identity: syntheticIdentity, profileId, capturedUtc: `2026-10-05T01:0${i}:00Z`,
    expectedBlocks: [{ identifierKind: "LID", identifierHex: "01" }, { identifierKind: "DID", identifierHex: "0001" }],
    blocks: [{ identifierKind: "LID", identifierHex: "01", dataHex: i ? "A4" : "A5" },
      { identifierKind: "DID", identifierHex: "0001", dataHex: "7E" }] }));
  const importedCoding = [];
  for (const file of typedFiles) {
    await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, file);
    const doc = await page.evaluate(() => window.porsche981.diagnosticPreparation({ action: "import-backup", ecu: "dme" }));
    assert.equal(doc.ok, true, JSON.stringify(doc)); importedCoding.push(doc);
  }
  const typedCoding = await page.evaluate((id) => window.porsche981.diagnosticPreparation({ action: "simulate-coding", ecu: "dme", id,
    blockKey: "LID:01", recordAt: 4047899, rawValue: 0 }), importedCoding[0].id);
  assert.equal(typedCoding.result?.ok, true, JSON.stringify(typedCoding));
  assert.equal(typedCoding.result.readbacks.length, 2); assert.equal(typedCoding.result.simulation, true);
  const typedRestore = await page.evaluate((id) => window.porsche981.diagnosticPreparation({ action: "simulate-restore", ecu: "dme", id }), importedCoding[1].id);
  assert.equal(typedRestore.result?.ok, true, JSON.stringify(typedRestore));
  assert.equal(typedRestore.plan.changedBlocks, 1); assert.equal(typedRestore.result.readbacks.length, 2);
  const wrongBlock = await page.evaluate((id) => window.porsche981.diagnosticPreparation({ action: "coding-preview", ecu: "dme", id,
    blockKey: "DID:0001", recordAt: 4047899, rawValue: 0 }), importedCoding[0].id);
  assert.equal(wrongBlock.error, "coding_field_block_mismatch");
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme");
  await page.getByTestId("eng-data-source").selectOption("x431");
  await page.getByTestId("offline-parameters").locator('input[type="checkbox"]').first().check();
  assert.equal(await page.getByTestId("manufacturer-read").isDisabled(), true);
  await page.getByTestId("manufacturer-x431-inactive").check();
  await page.getByTestId("manufacturer-readonly").check();
  assert.equal(await page.getByTestId("obd-recording-controls").count(), 0);
  await page.getByTestId("manufacturer-read").click();
  await page.getByTestId("manufacturer-error").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /live_not_enabled/);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  await page.getByTestId("eng-system").selectOption("gateway");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 1151 项"));
  assert.match(await page.getByTestId("offline-version").innerText(), /CAN_CAN_Gateway_A7_1/);
  await page.getByTestId("offline-search").fill("里程");
  await page.getByTestId("offline-select-5fd482040869539e351eb1e79edd82be0d9d9ccf6bd794c4373f68d0ca75301e").check();
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
  const gatewayFile = path.join(output, "gateway-complete-batch.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, gatewayFile);
  await page.getByTestId("manufacturer-save").click();
  await page.getByTestId("manufacturer-saved").waitFor();
  const gateway = JSON.parse(fs.readFileSync(gatewayFile, "utf8"));
  assert.equal(gateway.profileId, "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1");
  assert.equal(gateway.simulation, true); assert.equal(gateway.vehicleDataCollected, false);
  assert.ok(gateway.cycleResults.length >= 2); assert.equal(gateway.samples.length, gateway.cycleResults.length);
  assert.ok(gateway.samples.every(s => s.synthetic && s.value === "126380"));
  assert.equal(gateway.cycles[0].transport.closed, true);
  assert.equal(gateway.cycles[0].transport.rawLog.filter(row => row.req === "1003").length, 1);
  assert.equal(gateway.cycles[0].readContract.identityRequests.length, 7);
  assert.equal(gateway.cycles[0].readContract.ecuId, 9);
  assert.equal(gateway.cycles[0].readContract.rxId, 0x77A);
  await page.screenshot({ path: path.join(output, "gateway-stopped.png"), fullPage: true });
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  await page.getByLabel("原码范围版本").selectOption(profileId);
  await page.getByTestId("coding-read-scope-result").waitFor();
  assert.match(await page.getByTestId("coding-read-scope-result").innerText(), /39 个来源字段/);
  assert.match(await page.getByTestId("coding-read-scope-result").innerText(), /编码块的完整长度.*尚未确认/);
  await page.getByText("查看全部 28 个编码读取组", { exact: true }).click();
  await page.getByLabel("码值备份", { exact: true }).selectOption(importedCoding[0].id);
  await page.getByLabel("码值块", { exact: true }).selectOption("LID:01");
  await page.getByLabel("设码字段", { exact: true }).selectOption("4047899");
  await page.getByText("查看备份中的完整原始码值", { exact: true }).click();
  assert.match(await page.getByTestId("diagnostic-preparation").innerText(), /DID 0001：\s*7E/);
  assert.match(await page.getByTestId("coding-read-scope-result").innerText(), /LID A4/);
  await page.getByText("查看全部 28 个编码读取组", { exact: true }).click();
  assert.ok(fs.existsSync(path.join(userData, "garage.db")), "bundled Node initialized the fresh SQLite database");
  assert.equal(fs.readdirSync(runtime.sessionArtifacts).length, 0, "completed transient reads must not persist unsaved results");
  for (const file of ["model-oem-links.json", "xray-mesh-state.json", "xray-transforms.json"])
    assert.ok(fs.existsSync(path.join(runtime.localAssets, file)), file);
  assert.equal(fs.existsSync(path.join(delivery, "resources/app/.local")), false, "runtime must not write data inside the program directory");
  const manifest = JSON.parse(fs.readFileSync(path.join(delivery, "delivery-manifest.json"), "utf8"));
  const expectedProgramFiles = new Set([...manifest.files.map(row => row.path), "delivery-manifest.json"]);
  const actualProgramFiles = fs.readdirSync(delivery, { recursive: true, withFileTypes: true }).filter(entry => entry.isFile())
    .map(entry => path.relative(delivery, path.join(entry.parentPath, entry.name)).split(path.sep).join("/"));
  assert.deepEqual(new Set(actualProgramFiles), expectedProgramFiles, "installed GUI must not create program bytecode or other files");
  assert.ok(fs.existsSync(path.join(userData, "diagnostic-library/.local/diagnostics/realtime-preparation/rehearsal-fixtures.json")));
  verifyLaptopAcceptanceInputs(delivery, bundle, kitRoot);
  assert.equal(errors.length, 0, errors.join("\n"));
  assert.equal(timedOut, false, "total Windows acceptance deadline");
  await page.screenshot({ path: path.join(output, "installed.png") });
  await app.close(); app = undefined;
  assert.equal(timedOut, false, "total Windows acceptance deadline including close");
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, ...runtime,
    inputs: preflight, windowsExecutionVerified: true, nativeHeadVerified: false,
    host: { hostname: os.hostname(), release: os.release(), arch: process.arch, nodeVersion: process.version },
    developerPathRemoved: true, bundledPythonUtf8: encoding.utf8 === 1, originalCapturesBundled: false, definitionFilesImported: imported.files,
    samples: acquired.samples.length, requestGroups: acquired.requestCount, codingFields: scope.plan.definitionFieldCount,
    standardEngineSamples: engine.final.engine.samples.length,
    continuousManufacturerCycles: streaming.result.completedCycles, manufacturerSessionCount: 1, gracefulManufacturerStop: true,
    manufacturerVehicleButton: true, manufacturerLiveGate: deniedManufacturer.error,
    gatewayContinuousRehearsal: true, gatewayCompleteSave: true,
    gatewayLiveGate: "live_not_enabled", gatewayIdentityFields: 7,
    typedCodingRehearsal: typedCoding.result.ok, typedRestoreRehearsal: typedRestore.result.ok, fullScopeReadback: true,
    simulation: true, noDeviceIO: true, vehicleVerified: false, errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, samples: acquired.samples.length, imported: imported.files }));
} catch (error) {
  fs.writeFileSync(path.join(output, "failure.json"), JSON.stringify({ ok: false, inputs: preflight,
    windowsExecutionVerified: false, nativeHeadVerified: false, vehicleVerified: false, noDeviceIO: true,
    timedOut, error: String(error.message || error), stack: error.stack || null }, null, 2));
  throw error;
} finally { clearTimeout(deadline); if (app) await app.close(); }
