/** Real Electron/IPC/Python acceptance, isolated DB and no vehicle/device I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { installRealtimeUiIdentityModel, selectIdentifiedRealtimeUnit } from "./obd-live-ui-identity-model.mjs";
import { pathToFileURL } from "node:url";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
if (process.env.PORSCHE981_ACCEPT_APP_ROOT) verifyInstalledEntry(root);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-offline-preparation-20261003/ui-accept"));
fs.mkdirSync(output, { recursive: true });
const temp = fs.mkdtempSync(path.join(output, "ui-"));
const desktop = path.join(temp, "Desktop"); fs.mkdirSync(desktop);
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
const watchdog = setTimeout(() => { console.error("realtime UI total timeout"); void app?.close(); }, 480000);
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
  await app.evaluate(({ app, dialog }, desktopPath) => {
    app.setPath("desktop", desktopPath);
    dialog.showSaveDialog = async () => { throw new Error("unexpected parameter Save As dialog"); };
  }, desktop);
  const checks = [];
  const units = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "ready-units", generation: "981" }));
  assert.equal(units.ok, true); assert.equal(units.counts.units, 35); assert.equal(units.counts.parameters, 52299);
  for (const unit of units.units) {
    await page.getByTestId("eng-system").selectOption(unit.systemId);
    await page.getByTestId("offline-identity-state").waitFor();
    assert.equal(await page.getByTestId("offline-selection").count(), 0);
    assert.equal(await page.getByTestId("offline-variant").count(), 0);
  }
  for (const id of ["eng-data-source", "offline-version", "offline-variant", "manufacturer-readonly", "manufacturer-x431-inactive",
    "offline-plan", "offline-stop", "offline-save", "offline-replay", "replay-open", "manufacturer-start", "obd-recording-controls"])
    assert.equal(await page.getByTestId(id).count(), 0, `${id} is a retired UI element`);
  checks.push("all 35 unidentified units hide parameter selection; manual catalogue/version, consent, replay and recording controls remain absent");

  // Keep full catalogue/physical-denial checks through real IPC; versions are
  // not selected through production UI, which now requires fresh recognition.
  const { profileFitsGeneration } = await import(pathToFileURL(path.join(root, "apps/desktop/src/obd/manufacturer-profiles.mjs")));
  let versions = 0, guardedVersions = 0;
  for (const unit of units.units) {
    for (const variant of unit.variants) {
      const doc = await page.evaluate(request => window.porsche981.offlineDiagnostics(request),
        { action: "ready-parameters", generation: "981", ecuId: unit.ecuId, profileId: variant.profileId, limit: 1 });
      assert.equal(doc.ok, true, JSON.stringify(doc)); assert.equal(doc.total, variant.parameterCount);
      assert.equal(doc.items.length, Math.min(variant.parameterCount, 1)); versions++;
      const parameter = doc.items[0];
      if (profileFitsGeneration(variant.profileId, "981") && parameter?.decoderReady && parameter.nameResolved && parameter.requestHex) {
        const denied = await page.evaluate(request => window.porsche981.manufacturerStream(request),
          { action: "start-read", ecuId: unit.ecuId, profileId: variant.profileId, parameterIds: [parameter.id], continuous: true, confirmedReadOnly: true, x431Inactive: true });
        assert.equal(denied.error, "live_not_enabled", variant.profileId); guardedVersions++;
      }
    }
    console.log(`catalogue ${unit.systemId}: ${unit.variants.length} versions checked`);
  }
  assert.ok(guardedVersions > 0);
  checks.push(`real IPC: ${versions} source catalogues and ${guardedVersions} applicable first-parameter requests checked; physical I/O remains denied`);

  const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
  const gatewayProfileId = "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1";
  await installRealtimeUiIdentityModel(app, { 1: profileId, 9: gatewayProfileId });
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId });
  assert.equal(await page.getByTestId("offline-selection").evaluate(el => el.tagName), "DIV");
  assert.equal(await page.getByTestId("offline-selection").locator("summary").count(), 0);
  assert.equal(await page.getByTestId("offline-parameters").locator("input:checked").count(), 0);
  const inputs = page.getByTestId("offline-parameters").locator('input[type="checkbox"]');
  await inputs.nth(1).check(); await inputs.nth(2).check();
  const parameterIds = await inputs.evaluateAll(items => items.filter(item => item.checked).map(item => item.dataset.testid.replace("offline-select-", "")));
  const request = { ecuId: 1, profileId, parameterIds }; assert.equal(parameterIds.length, 2);
  const plan = await page.evaluate(request => window.porsche981.offlineDiagnostics({ action: "ready-plan", ...request }), request);
  assert.equal(plan.ok, true); assert.equal(plan.executionEnabled, false);
  const replay = await page.evaluate(request => window.porsche981.offlineDiagnostics({ action: "ready-replay", ...request }), request);
  assert.equal(replay.ok, true); assert.equal(replay.parameters.length, 2); assert.equal(replay.executionEnabled, false); assert.equal(replay.liveVerified, false);
  const forbidden = await page.evaluate(request => window.porsche981.offlineDiagnostics({ action: "ready-acquire", ...request, mode: "live" }), request);
  assert.equal(forbidden.error, "forbidden_field");
  const cancelled = await page.evaluate(async request => {
    const operationId = crypto.randomUUID(); const running = window.porsche981.offlineDiagnostics({ action: "ready-plan", ...request }, operationId);
    const stopped = await window.porsche981.cancelOfflineDiagnostics(operationId); return { stopped, result: await running };
  }, request);
  assert.equal(cancelled.stopped.cancelled, true); assert.equal(cancelled.result.error, "cancelled");
  const denied = await page.evaluate(request => window.porsche981.manufacturerStream({ action: "start-read", ...request,
    continuous: true, confirmedReadOnly: true, x431Inactive: true }), request);
  assert.equal(denied.error, "live_not_enabled"); assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  checks.push("real plan/replay/cancel IPC retained off-page; modeled renderer identity cannot bypass the real physical-I/O guard");
  async function rehearsalAndSave(profileId, ids, expectedSamplesPerCycle) {
    const started = await page.evaluate(args => window.porsche981.manufacturerStream({ action: "start", ...args, continuous: true }), { profileId, parameterIds: ids });
    assert.equal(started.ok, true, JSON.stringify(started));
    const events = []; let cursor = 0, connectionCursor = 0, state, stopSent = false;
    const deadline = Date.now() + 30000;
    do {
      assert.ok(Date.now() < deadline, "bounded rehearsal stop and release");
      state = await page.evaluate(args => window.porsche981.manufacturerStream({ action: "status", ...args }), { sessionId: started.sessionId, cursor, connectionCursor });
      assert.equal(state.ok, true, JSON.stringify(state));
      events.push(...state.events); cursor = state.cursor; connectionCursor = state.connectionCursor;
      if (events.length >= 2 && !stopSent) {
        stopSent = true;
        assert.equal((await page.evaluate(sessionId => window.porsche981.manufacturerStream({ action: "stop", sessionId }), started.sessionId)).ok, true);
      }
      if (state.state !== "finished" || !state.released || state.hasMore) await page.waitForTimeout(50);
    } while (state.state !== "finished" || !state.released || state.hasMore);
    assert.equal(state.error, null, JSON.stringify(state));
    assert.ok(events.length >= 2); assert.equal(state.result.transport.closed, true);
    assert.deepEqual(state.result.transport.closeErrors, []);
    assert.equal(state.result.simulation, true); assert.equal(state.result.vehicleDataCollected, false);
    const samples = events.flatMap(event => event.samples);
    assert.equal(samples.length, events.length * expectedSamplesPerCycle);
    assert.ok(samples.every(sample => sample.synthetic && sample.processedUtc && sample.pduSha256));
    const saved = await page.evaluate(sessionId => window.porsche981.manufacturerStream({ action: "save", sessionId }), started.sessionId);
    assert.equal(saved.ok, true, JSON.stringify(saved)); assert.equal(saved.saved, true);
    assert.equal(path.dirname(saved.filePath), desktop); assert.match(path.basename(saved.filePath), /^SYNTHETIC-.*\.pcapng$/);
    const bytes = fs.readFileSync(saved.filePath), exported = [];
    for (let offset = 0; offset < bytes.length;) {
      const length = bytes.readUInt32LE(offset + 4);
      assert.ok(length >= 12 && length % 4 === 0 && offset + length <= bytes.length);
      assert.equal(bytes.readUInt32LE(offset + length - 4), length);
      if (bytes.readUInt32LE(offset) === 6) {
        const captured = bytes.readUInt32LE(offset + 20);
        exported.push(JSON.parse(bytes.subarray(offset + 40, offset + 28 + captured).toString("utf8")));
      }
      offset += length;
    }
    assert.equal(exported.length, samples.length);
    for (const row of exported) assert.deepEqual(Object.keys(row), ["name", "value", "unit", "capturedUtc"]);
    assert.doesNotMatch(bytes.toString(), /requestHex|pduHex|transactions|connectionEvents/);
    return { cycles: events.length, samples: samples.length, bytes: bytes.length };
  }
  const dme = await rehearsalAndSave(profileId, parameterIds, 2);
  const secondGroup = await page.getByTestId("offline-category").locator("option").nth(1).getAttribute("value");
  await page.getByTestId("offline-category").selectOption(secondGroup);
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  assert.equal(await page.getByTestId("offline-selected").locator("button").count(), 2);
  await page.getByTestId("offline-category").selectOption("");
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-selection-count"]')?.textContent.includes("共 365 项"));
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  assert.equal(await page.getByTestId("offline-selected").locator("button").count(), 2);
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="offline-parameters"]')?.getAttribute("aria-busy") === "false");
  for (let i = 0; i < 12; i++) if (!await inputs.nth(i).isChecked()) await inputs.nth(i).check();
  assert.equal(await inputs.nth(12).isDisabled(), true);
  await page.setViewportSize({ width: 800, height: 900 });
  const readable = await page.getByTestId("offline-parameters").locator("label").first().evaluate(el => ({
    checkbox: el.querySelector("input").getBoundingClientRect().width, name: el.querySelector("span").getBoundingClientRect().width, height: el.getBoundingClientRect().height }));
  assert.ok(readable.checkbox <= 20 && readable.name > 120 && readable.height <= 42);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.screenshot({ path: path.join(output, "current-controls.png"), fullPage: true });
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "gateway", ecuId: 9, profileId: gatewayProfileId });
  assert.match(await page.getByTestId("offline-selection-count").innerText(), /已选 0/);
  const mileageId = "5fd482040869539e351eb1e79edd82be0d9d9ccf6bd794c4373f68d0ca75301e";
  await page.getByTestId("offline-search").fill("里程"); await page.getByTestId(`offline-select-${mileageId}`).check();
  const gateway = await rehearsalAndSave(gatewayProfileId, [mileageId], 1);
  await page.getByTestId(`offline-select-${mileageId}`).uncheck();
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  checks.push("automatic version matching, expanded selection, retained filters/pagination, 12-item cap, unit-switch reset and narrow layout");
  checks.push("real DME/Gateway Python rehearsal, release and parameter-only Desktop PCAPNG save; renderer identity is explicitly modeled");
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, noHardware: true,
    moduleRoot: root, startup, definitionFilesImported: imported?.files || 0, recordingElementsRemoved: true,
    identityAndConnectionModeled: true, pythonRehearsalAndSaveReal: true, versions, guardedVersions, dme, gateway, checks, errors }, null, 2));
  console.log("PASS realtime preparation: current auto-match controls, real offline IPC/Python rehearsal and parameter-only Desktop save; no vehicle I/O");
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
  if (app) await app.close().catch(() => {});
  if (passed) fs.rmSync(temp, { recursive: true, force: true });
}
