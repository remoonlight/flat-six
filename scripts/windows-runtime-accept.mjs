/** Test the real unpacked artifact with empty userData and no developer PATH. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { resolvePythonCandidates } from "../apps/desktop/electron/offline-diagnostics.mjs";

const root = path.resolve(import.meta.dirname, "..");
const delivery = path.resolve(process.argv[2] || "");
const bundle = path.resolve(process.argv[3] || "");
if (!process.argv[2] || !process.argv[3] || !fs.existsSync(bundle)) throw new Error("Supply delivery directory and private definitions ZIP");
const outputBase = path.resolve(process.argv[4] || path.join(root, ".local/obd-precar-software-20261005"));
const relativeOutput = path.relative(path.join(root, ".local"), outputBase);
if (relativeOutput.startsWith("..") || path.isAbsolute(relativeOutput)) throw new Error("Private output directory required");
fs.mkdirSync(outputBase, { recursive: true });
const output = fs.mkdtempSync(path.join(outputBase, "installed-ui-"));
const userData = path.join(output, "empty-user-data"); fs.mkdirSync(userData);
const devices = path.join(output, "devices.json"); fs.writeFileSync(devices, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, "System32"), PYTHONPATH: "",
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_CONNECTION_FIXTURE: devices, PORSCHE981_USER_DATA: userData };
for (const key of ["ELECTRON_RUN_AS_NODE", "VITE_DEV_SERVER_URL", "PORSCHE981_NODE", "PORSCHE981_PYTHON", "PORSCHE981_DB",
  "PORSCHE981_VARIANTS", "PORSCHE981_REALTIME_PREPARATION", "PORSCHE981_LANGUAGE", "PORSCHE981_DEFINITION_ROOT",
  "PORSCHE981_CONNECTION_STATE", "PORSCHE981_SESSION_ARTIFACT_ROOT", "PORSCHE981_CAN_CAPTURE_ROOT", "PORSCHE981_LOCAL_ROOT"])
  delete env[key];
let app;
const errors = [];
try {
  app = await electron.launch({ executablePath: path.join(delivery, "FlatSix.exe"), args: [], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000);
  page.on("pageerror", (e) => errors.push(e.message));
  const runtime = await app.evaluate(({ app }) => ({ packaged: app.isPackaged, userData: app.getPath("userData"),
    python: process.env.PORSCHE981_PYTHON, node: process.env.PORSCHE981_NODE,
    connectionState: process.env.PORSCHE981_CONNECTION_STATE,
    sessionArtifacts: process.env.PORSCHE981_SESSION_ARTIFACT_ROOT,
    canCaptures: process.env.PORSCHE981_CAN_CAPTURE_ROOT,
    localAssets: process.env.PORSCHE981_LOCAL_ROOT }));
  assert.equal(runtime.packaged, true); assert.equal(runtime.userData, userData);
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
  const acquired = await page.evaluate(({ profileId, ids }) => window.porsche981.offlineDiagnostics({ action: "ready-acquire",
    generation: "981", ecuId: 1, profileId, parameterIds: ids }), { profileId, ids });
  assert.equal(acquired.ok, true, JSON.stringify(acquired)); assert.equal(acquired.samples.length, 12);
  assert.equal(acquired.vehicleDataCollected, false); assert.equal(acquired.transport.closed, true);
  assert.ok(acquired.transport.rawLog.length > 0);
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
  assert.ok(fs.existsSync(path.join(userData, "diagnostic-library/.local/diagnostics/realtime-preparation/rehearsal-fixtures.json")));
  assert.equal(errors.length, 0, errors.join("\n"));
  await page.screenshot({ path: path.join(output, "installed.png") });
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, ...runtime,
    developerPathRemoved: true, bundledPythonUtf8: encoding.utf8 === 1, originalCapturesBundled: false, definitionFilesImported: imported.files,
    samples: acquired.samples.length, requestGroups: acquired.requestCount, codingFields: scope.plan.definitionFieldCount,
    standardEngineSamples: engine.final.engine.samples.length,
    typedCodingRehearsal: typedCoding.result.ok, typedRestoreRehearsal: typedRestore.result.ok, fullScopeReadback: true,
    simulation: true, noDeviceIO: true, vehicleVerified: false, errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, samples: acquired.samples.length, imported: imported.files }));
} finally { if (app) await app.close(); }
