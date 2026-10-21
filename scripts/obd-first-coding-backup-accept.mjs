/** Production main/preload IPC and real private files. Imported declarations
 * exercise file persistence only; they never qualify a vehicle coding read. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";

const source = path.resolve(import.meta.dirname, "..");
const appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, ".local/obd-first-desktop-backup-20261015/source-ipc"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const desktop = path.join(temp, "Desktop"); await fs.mkdir(desktop);
const identity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "synthetic-hw", software: "synthetic-sw" };
const backup = (dataHex, version = identity.software, ecu = "dme") => ({ schemaVersion: 2, kind: "ecu-coding-backup",
  identity: { ...identity, software: version, ecu }, profileId: "synthetic-backup-only",
  capturedUtc: "2026-10-15T00:01:02Z", expectedBlocks: [{ identifierKind: "LID", identifierHex: "10" }],
  blocks: [{ identifierKind: "LID", identifierHex: "10", dataHex }] });
const inputs = [backup("01"), backup("02"), backup("03", "new-version"), backup("04", identity.software, "gateway")];
const inputFiles = await Promise.all(inputs.map(async (data, i) => {
  const file = path.join(temp, `input-${i}.json`); await fs.writeFile(file, JSON.stringify(data)); return file;
}));
const devices = path.join(temp, "devices.json"); await fs.writeFile(devices, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: devices,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PYTHONDONTWRITEBYTECODE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
const checks = [], errors = []; let app, passed = false;
const watchdog = setTimeout(() => { console.error("first coding backup acceptance timeout"); void app?.close(); }, 60000);
try {
  app = await electron.launch({ args: [appRoot || path.join(source, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ app, dialog }, { desktop, files }) => {
    app.setPath("desktop", desktop);
    let next = 0;
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [files[next++]] });
    dialog.showSaveDialog = async () => { throw new Error("desktop original must not open a save dialog"); };
    const fs = process.getBuiltinModule("fs"), path = process.getBuiltinModule("path"), originalLink = fs.promises.link;
    globalThis.__firstBackupFault = { fail: true, restore: () => { fs.promises.link = originalLink; } };
    fs.promises.link = async (from, to) => {
      if (globalThis.__firstBackupFault.fail && to.startsWith(desktop + path.sep)) {
        globalThis.__firstBackupFault.fail = false;
        throw Object.assign(new Error("injected desktop save failure"), { code: "ENOSPC" });
      }
      return originalLink(from, to);
    };
  }, { desktop, files: inputFiles });
  const call = request => page.evaluate(request => window.porsche981.diagnosticPreparation(request), request);
  const first = await call({ action: "import-backup", ecu: "dme" });
  const later = await call({ action: "import-backup", ecu: "dme" });
  const version = await call({ action: "import-backup", ecu: "dme" });
  assert.ok(first.ok && later.ok && version.ok);
  const failed = await call({ action: "export-backup", ecu: "dme", id: later.id });
  assert.equal(failed.ok, false); assert.equal(failed.saved, undefined); assert.equal(failed.executionEnabled, false);
  assert.deepEqual(await fs.readdir(desktop), []);
  checks.push("desktop write failure returns no success and leaves no partial final file; internal original remains available for retry");
  const saved = await call({ action: "export-backup", ecu: "dme", id: version.id });
  assert.equal(saved.ok, true); assert.equal(saved.saved, true); assert.equal(saved.id, first.id);
  assert.equal(saved.executionEnabled, false); assert.equal(saved.freshReadProven, false);
  assert.equal(path.dirname(saved.filePath), desktop);
  assert.equal(path.basename(saved.filePath), "WP0ZZZ98ZES000000_dme_2026-10-15T00-01-02-000Z.json");
  const originalBytes = await fs.readFile(saved.filePath);
  const original = JSON.parse(originalBytes); assert.equal(original.blocks[0].dataHex, "01");
  assert.equal(original.provenance, "imported"); assert.equal(original.freshReadProven, false);
  checks.push("retry through a later software version publishes only the reserved first baseline, with automatic VIN/unit/time naming on system Desktop and unchanged provenance");
  assert.equal((await call({ action: "export-backup", ecu: "dme", id: later.id })).filePath, saved.filePath);
  assert.deepEqual(await fs.readdir(desktop), [path.basename(saved.filePath)]);
  assert.equal((await call({ action: "export-backup", ecu: "gateway", id: first.id })).ok, false);
  const gateway = await call({ action: "import-backup", ecu: "gateway" });
  const separate = await call({ action: "export-backup", ecu: "gateway", id: gateway.id });
  assert.ok(separate.saved); assert.equal(JSON.parse(await fs.readFile(separate.filePath)).identity.ecu, "gateway");
  assert.equal((await fs.readdir(desktop)).length, 2);
  checks.push("later snapshots reuse the first file; wrong-unit requests fail and another unit has its own first file");
  await fs.writeFile(saved.filePath, "damaged private test file");
  const damaged = await call({ action: "export-backup", ecu: "dme", id: first.id });
  assert.equal(damaged.ok, false); assert.equal(damaged.error, "backup-desktop-integrity-failed");
  assert.equal(await fs.readFile(saved.filePath, "utf8"), "damaged private test file");
  await fs.writeFile(saved.filePath, originalBytes);
  checks.push("an existing damaged desktop original fails verification and is never overwritten");
  await page.locator('nav.side [data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  for (const id of ["coding-backup-original", "coding-read-original"]) {
    assert.match(await page.getByTestId(id).innerText(), /未完成/);
    await page.getByTestId(id).click();
    assert.match(await page.getByTestId("coding-original-notice").innerText(), /尚未完成/);
  }
  assert.equal(await page.getByTestId("coding-write").count(), 0);
  assert.equal(await page.getByTestId("diagnostic-preparation").count(), 0);
  checks.push("file persistence does not enable the incomplete vehicle backup/read buttons or restore retired preparation UI");
  assert.deepEqual(errors, []); passed = true;
} catch (error) { errors.push(error.stack || String(error)); throw error; }
finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(() => globalThis.__firstBackupFault?.restore()).catch(() => {}); await app.close().catch(() => {}); }
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, errors,
    productionIpc: true, systemDesktopIsolated: true, importedFixturesOnly: true, deviceIO: false, vehicleIO: false }, null, 2) + "\n");
}
