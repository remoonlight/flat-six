/** Retained historical-data IPC plus absence of retired UI. Synthetic files only; no vehicle I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";
const source = path.resolve(import.meta.dirname, ".."), root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || source);
const base = path.join(source, ".local/obd-value-replay-20261010");
if (process.env.PORSCHE981_ACCEPT_APP_ROOT) verifyInstalledEntry(root);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(base, "source-ui"));
const input = path.resolve(process.env.PORSCHE981_REPLAY_INPUT || path.join(base, "main-check"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const files = ["SYNTHETIC-standard.json", "SYNTHETIC-manufacturer.json", "SYNTHETIC-internal.json", "SYNTHETIC-many.json", "SYNTHETIC-extreme.json",
  "SYNTHETIC-connection-events.json", "SYNTHETIC-events-only.json", "SYNTHETIC-unconfirmed-recovery.json"].map(name => path.join(input, name));
const hashes = await Promise.all(files.map(async file => createHash("sha256").update(await fs.readFile(file)).digest("hex")));
const invalid = path.join(temp, "SYNTHETIC-truncated.json"); await fs.writeFile(invalid, "{");
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"), PORSCHE981_CAN_CAPTURE_ROOT: path.join(temp, "raw-captures"),
  PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "sessions"), PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"),
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_CONNECTION_FIXTURE: fixture, PYTHONDONTWRITEBYTECODE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const errors = [];
const watchdog = setTimeout(() => { console.error("value replay UI total timeout"); void app?.close(); }, 120000);
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(15000);
  const startup = await app.evaluate(({ app }, entry) => {
    const { createRequire } = process.getBuiltinModule("module"), load = createRequire(entry);
    const cached = load.cache[load.resolve(entry)];
    return { appPath: app.getAppPath(), mainFile: cached?.filename || "", bootstrapLoaded: cached?.loaded === true };
  }, path.join(root, "apps/desktop/electron/installed-bootstrap.cjs"));
  if (process.env.PORSCHE981_ACCEPT_APP_ROOT) {
    assert.equal(path.resolve(startup.appPath), root);
    assert.equal(path.resolve(startup.mainFile), path.join(root, "apps/desktop/electron/installed-bootstrap.cjs"));
    assert.equal(startup.bootstrapLoaded, true);
  }
  page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(({ dialog }) => {
    globalThis.__replayChoice = null; globalThis.__replayCanceled = false; globalThis.__replayDialogs = 0;
    dialog.showOpenDialog = async (window, options) => {
      if (!options || options.title !== "打开采集记录" || !window?.webContents) throw new Error("native-owner-or-options-missing");
      globalThis.__replayDialogs++;
      return { canceled: globalThis.__replayCanceled, filePaths: [globalThis.__replayChoice] };
    };
  });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  for (const id of ["replay-open", "acquisition-replay", "offline-replay"])
    assert.equal(await page.getByTestId(id).count(), 0, "historical data has no project UI");
  const call = request => page.evaluate(request => window.porsche981.acquisitionReplay(request), request);
  async function open(file) {
    await app.evaluate((_electron, chosen) => { globalThis.__replayChoice = chosen; globalThis.__replayCanceled = false; }, file);
    const result = await call({ action: "open" });
    assert.equal(result.ok, true, JSON.stringify(result));
    return result;
  }
  const view = (record, values = {}) => call({ action: "view", recordingId: record.recordingId,
    parameterIds: record.summary.parameters.map(p => p.id), positionMs: record.summary.durationMs, offset: 0, ...values });
  const standard = await open(files[0]);
  assert.equal(standard.summary.sampleCount, 6000); assert.equal(standard.summary.simulation, true);
  const seen = [];
  for (let offset = 0; offset < 6000; offset += 100) {
    const pageResult = await view(standard, { offset }); assert.equal(pageResult.ok, true);
    assert.equal(pageResult.rows.length, 100);
    pageResult.rows.forEach((row, i) => {
      assert.equal(row.index, offset + i); assert.equal(row.cycle, Math.floor((offset + i) / 2) + 1);
      assert.equal(row.synthetic, true);
    });
    seen.push(...pageResult.rows.map(row => row.index));
  }
  assert.equal(new Set(seen).size, 6000);
  const earlier = await view(standard, { positionMs: 80000 });
  assert.equal(earlier.series.find(s => s.name === "电压").current.value, 2998);
  assert.equal((await view(standard)).series.find(s => s.name === "转速").current.value, 5999);
  assert.equal((await view(standard, { parameterIds: [standard.summary.parameters[0].id] })).total, 3000);
  await app.evaluate(() => { globalThis.__replayCanceled = true; });
  assert.equal((await call({ action: "open" })).canceled, true);
  assert.equal((await view(standard)).total, 6000);
  await app.evaluate((_electron, chosen) => { globalThis.__replayChoice = chosen; globalThis.__replayCanceled = false; }, invalid);
  assert.equal((await call({ action: "open" })).ok, false);
  assert.equal((await view(standard)).total, 6000, "invalid load retains previous data");
  const manufacturer = await open(files[1]);
  assert.equal(manufacturer.summary.sampleCount, 3);
  assert.equal(manufacturer.summary.error, "adapter_release_not_verified");
  assert.equal((await view(manufacturer, { positionMs: 0 })).series[0].current.display, "驻车");
  assert.equal((await view(manufacturer, { positionMs: 2000 })).series[0].current.display, "前进");
  const many = await open(files[3]); assert.equal(many.summary.sampleCount, 35);
  assert.equal((await view(many)).series.length, 12);
  assert.equal((await view(many, { seriesOffset: 12 })).series[0].name, "参数12");
  assert.equal((await view(many, { seriesOffset: 24 })).series.length, 11);
  assert.equal((await view(many, { parameterIds: many.summary.parameters.slice(0, 34).map(p => p.id) })).total, 34);
  assert.equal((await view(many, { parameterIds: [] })).total, 0);
  const extreme = await open(files[4]); assert.equal(extreme.summary.sampleCount, 2);
  assert.ok((await view(extreme)).series.flatMap(s => s.points).every(p => Number.isFinite(p.value)));
  const connections = await open(files[5]); assert.equal(connections.summary.connectionEventCount, 212);
  const eventSeen = [];
  for (let connectionOffset = 0; connectionOffset < 212; connectionOffset += 100) {
    const chunk = await view(connections, { connectionOffset }); assert.equal(chunk.ok, true);
    eventSeen.push(...chunk.connectionEvents.map(event => event.sequence));
  }
  assert.equal(eventSeen.length, 212); assert.equal(new Set(eventSeen).size, 212);
  assert.equal((await view(connections, { parameterIds: [] })).connectionTotal, 212, "events independent of value selection");
  assert.equal((await view(connections, { positionMs: 2145 })).currentConnectionEvent.phase, "recovered");
  const eventsOnly = await open(files[6]);
  assert.equal(eventsOnly.summary.sampleCount, 0); assert.equal(eventsOnly.summary.connectionEventCount, 2);
  const eventFailure = await view(eventsOnly);
  assert.equal(eventFailure.rows.length, 0); assert.equal(eventFailure.series.length, 0);
  assert.equal(eventFailure.currentConnectionEvent.phase, "failed");
  assert.equal(eventsOnly.summary.error, "recovery-close-failed");
  const badEvents = path.join(temp, "SYNTHETIC-bad-events.json");
  await fs.writeFile(badEvents, JSON.stringify({ ...JSON.parse(await fs.readFile(files[6], "utf8")), connectionEventCount: 3 }));
  await app.evaluate((_electron, chosen) => { globalThis.__replayChoice = chosen; }, badEvents);
  assert.equal((await call({ action: "open" })).error, "replay_invalid_connection_events");
  assert.equal((await view(eventsOnly)).connectionTotal, 2, "invalid event load retains previous data");
  const unconfirmed = await open(files[7]);
  assert.equal(unconfirmed.summary.connectionEventCount, 3);
  assert.equal((await view(unconfirmed, { positionMs: 20 })).currentConnectionEvent.phase, "qualified");
  const internal = await open(files[2]); assert.equal(internal.summary.sampleCount, 100001);
  const internalView = await view(internal, { positionMs: 100000 });
  assert.equal(internalView.connectionTotal, 0);
  assert.equal(internalView.series.find(s => s.name === "CAN 扩展 18DAF110").current.display, "00112233");
  assert.equal(internalView.rows[0].timestampUs, 1700000000000000);
  assert.equal((await call({ action: "discard", recordingId: internal.recordingId })).ok, true);
  assert.equal((await view(internal)).error, "replay_not_found");
  for (const id of ["replay-open", "acquisition-replay", "offline-replay"])
    assert.equal(await page.getByTestId(id).count(), 0, "IPC tests never restore historical UI");
  assert.deepEqual(await Promise.all(files.map(async file => createHash("sha256").update(await fs.readFile(file)).digest("hex"))), hashes);
  assert.deepEqual(errors, []); passed = true;
  const result = { ok: true, moduleRoot: root, noHardware: true, vehicleIO: false, startup, errors,
    standardRowsPaged: seen.length, connectionEventsPaged: eventSeen.length,
    retainedHistoricalIpc: true, retiredUiAbsent: true, nativeOwnerDialog: true, inputsUnmodified: true,
    manufacturerEnumAndFailure: true, internalFrames: 100001, extremeFiniteValues: true,
    selection35: true, failedLoadRetainsPriorData: true };
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally {
  clearTimeout(watchdog); if (app) await app.close(); if (passed) await fs.rm(temp, { recursive: true, force: true });
}
