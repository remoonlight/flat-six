/** Launch an installed artifact from a restored business-data directory, without reimport. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

const root = path.resolve(import.meta.dirname, "..");
const [delivery, backup, userData, output] = process.argv.slice(2).map((p) => path.resolve(p));
assert.ok(delivery && backup && userData && output, "Supply package, backup, restored userData and output");
for (const dir of [userData, output]) {
  const rel = path.relative(path.join(root, ".local"), dir);
  assert.ok(rel && !rel.startsWith("..") && !path.isAbsolute(rel), "Private test paths required");
}
assert.equal(fs.existsSync(output), false, "Preserve existing evidence");
fs.mkdirSync(output, { recursive: true });
const hashFile = async (file) => {
  const hash = createHash("sha256");
  for await (const bytes of fs.createReadStream(file)) hash.update(bytes);
  return hash.digest("hex").toUpperCase();
};
const manifest = JSON.parse(fs.readFileSync(path.join(backup, "manifest.json"), "utf8"));
for (const entry of manifest.files) {
  const relative = entry.file.split("/");
  assert.ok(relative.every((p) => p && p !== ".." && p !== "." && !p.includes(":") && !p.includes("\\")));
  assert.equal(await hashFile(path.join(userData, ...relative)), entry.sha256);
}
const expectedConnection = JSON.parse(fs.readFileSync(path.join(userData, "diagnostics/connection.json"), "utf8"));
const vault = path.join(userData, "diagnostics/coding-backups");
const expectedIds = fs.readdirSync(vault).filter((p) => /^[a-f0-9]{64}\.json$/.test(p)).map((p) => p.slice(0, -5)).sort();
const expectedBaseline = fs.readdirSync(vault).filter((p) => p.startsWith("baseline-")).map((p) =>
  JSON.parse(fs.readFileSync(path.join(vault, p), "utf8")).id).sort();
const devices = path.join(output, "devices.json"); fs.writeFileSync(devices, '{"devices":[],"errors":[]}');
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, "System32"), PYTHONPATH: "",
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_CONNECTION_FIXTURE: devices, PORSCHE981_USER_DATA: userData };
for (const key of ["ELECTRON_RUN_AS_NODE", "VITE_DEV_SERVER_URL", "PORSCHE981_NODE", "PORSCHE981_PYTHON", "PORSCHE981_DB",
  "PORSCHE981_VARIANTS", "PORSCHE981_REALTIME_PREPARATION", "PORSCHE981_LANGUAGE", "PORSCHE981_DEFINITION_ROOT",
  "PORSCHE981_CONNECTION_STATE", "PORSCHE981_SESSION_ARTIFACT_ROOT", "PORSCHE981_CAN_CAPTURE_ROOT", "PORSCHE981_LOCAL_ROOT"])
  delete env[key];
const dbRead = spawnSync(path.join(delivery, "resources/app/runtime/node.exe"), ["--input-type=module", "-e",
  "import {DatabaseSync} from 'node:sqlite';const db=new DatabaseSync(process.argv[1],{readOnly:true});console.log(JSON.stringify(db.prepare('SELECT * FROM vehicle WHERE id=1').get()));db.close();",
  path.join(userData, "garage.db")], { env, encoding: "utf8", windowsHide: true, timeout: 10000 });
assert.equal(dbRead.status, 0, dbRead.stderr);
const expectedVehicle = JSON.parse(dbRead.stdout);
let app;
const errors = [];
try {
  app = await electron.launch({ executablePath: path.join(delivery, "FlatSix.exe"), args: [], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.waitForFunction(() => !!window.porsche981?.offlineDiagnostics);
  const runtime = await app.evaluate(({ app }) => ({ packaged: app.isPackaged, userData: app.getPath("userData"),
    definitions: process.env.PORSCHE981_DEFINITION_ROOT, coding: app.getPath("userData") + "/diagnostics/coding-backups" }));
  assert.equal(runtime.packaged, true); assert.equal(runtime.userData, userData);
  assert.equal(runtime.definitions, path.join(userData, "diagnostic-library"));
  assert.deepEqual(await page.evaluate(() => window.porsche981.getVehicle()), expectedVehicle);
  const connection = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
  assert.equal(connection.purpose, expectedConnection.purpose); assert.equal(connection.canNetwork, expectedConnection.canNetwork);
  assert.equal(connection.selectedDeviceId, expectedConnection.deviceId); assert.equal(connection.connected, false);
  assert.equal(connection.monitorDiagnostics.starts, 0, "Restored settings must not auto-connect");
  const backups = await page.evaluate(() => window.porsche981.diagnosticPreparation({ action: "list", ecu: "dme" }));
  assert.equal(backups.ok, true, JSON.stringify(backups));
  assert.deepEqual(backups.backups.map((b) => b.id).sort(), expectedIds);
  assert.deepEqual(backups.backups.filter((b) => b.original).map((b) => b.id).sort(), expectedBaseline);
  for (const id of expectedBaseline) {
    const plan = await page.evaluate((id) => window.porsche981.diagnosticPreparation({ action: "restore-plan", ecu: "dme", id }), id);
    assert.equal(plan.ok, true, JSON.stringify(plan));
  }
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
  assert.equal(fs.existsSync(path.join(delivery, "resources/app/.local")), false);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(output, "restored.png") });
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, packaged: true,
    restoredUserData: userData, filesHashVerifiedBeforeLaunch: manifest.files.length, vehicleDatabaseRowMatched: true,
    connectionSettingsRestored: true, automaticConnectionStarted: false, codingBackups: expectedIds.length,
    originalBaselines: expectedBaseline.length, definitionParameters: units.counts.parameters,
    samples: acquired.samples.length, definitionReimported: false, developerPathRemoved: true,
    noDeviceIO: true, vehicleVerified: false, errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, restoredFiles: manifest.files.length, samples: acquired.samples.length }));
} finally { if (app) await app.close(); }
