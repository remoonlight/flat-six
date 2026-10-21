/** Real Electron/IPC/managers, synthetic manufacturer transport and standard Python simulation. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
const source = path.resolve(import.meta.dirname, ".."), root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || source);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, ".local/obd-navigation-retention-20261012/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const desktop = path.join(temp, "Desktop"); await fs.mkdir(desktop);
function parameterRows(bytes) {
  const rows = [];
  for (let offset = 0; offset < bytes.length;) {
    const length = bytes.readUInt32LE(offset + 4); assert.ok(length >= 12 && length % 4 === 0);
    assert.equal(bytes.readUInt32LE(offset + length - 4), length);
    if (bytes.readUInt32LE(offset) === 6) {
      const captured = bytes.readUInt32LE(offset + 20);
      const row = JSON.parse(bytes.subarray(offset + 40, offset + 28 + captured).toString("utf8"));
      assert.deepEqual(Object.keys(row), ["name", "value", "unit", "capturedUtc"]); rows.push(row);
    }
    offset += length;
  }
  return rows;
}
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "sessions") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const errors = [], checks = [];
const watchdog = setTimeout(() => { console.error("navigation acceptance total timeout"); void app?.close(); }, 120000);
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(async ({ ipcMain, dialog }, args) => {
    const vm = process.getBuiltinModule("vm"), load = name => vm.runInThisContext(`import(${JSON.stringify(args.modules[name])})`,
      { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createReadOnlySessionManager, SESSION_CHANNEL } = await load("session");
    const { createManufacturerStream } = await load("manufacturer");
    const { saveParameterRecording } = await load("save");
    const fs = process.getBuiltinModule("fs"), { EventEmitter } = process.getBuiltinModule("events"), { PassThrough } = process.getBuiltinModule("stream");
    const { createHash } = process.getBuiltinModule("crypto");
    const state = globalThis.__navigation = { clock: 0, calls: [], standard: [], manufacturer: [], file: null };
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: state.file });
    const deps = { desktopPath: args.desktop };
    const session = createReadOnlySessionManager({ repoRoot: args.root, nowFn: () => state.clock,
      saveEngineRecording: recording => saveParameterRecording(recording, deps) });
    const profile = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", parameter = "a".repeat(64);
    const manufacturer = createManufacturerStream({ repoRoot: args.root, nowFn: () => state.clock,
      saveRecording: recording => saveParameterRecording(recording, deps), spawnFn: () => {
        const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
        let cycle = 0, timer, ended = false;
        const flags = { executionEnabled: false, liveVerified: false, writePayload: null, simulation: true,
          vehicleDataCollected: false, sourceKind: "historical-fixture-rehearsal" };
        const emit = (event, result) => child.stdout.write(JSON.stringify({ event, result }) + "\n");
        const end = () => { if (ended) return; ended = true; clearInterval(timer);
          emit("finished", { ...flags, ok: true, stopReason: "cancelled", completedCycles: cycle, requestCount: cycle }); child.emit("close", 0); };
        child.kill = () => { end(); return true; };
        child.stdin.on("data", bytes => { for (const line of bytes.toString().trim().split("\n")) {
          const request = JSON.parse(line); if (request.action === "stop") { end(); continue; }
          const tick = () => { if (ended) return; cycle++; const evidence = { requestHex: "21A4", pduHex: "61A42A",
            pduSha256: createHash("sha256").update(Buffer.from("61A42A", "hex")).digest("hex"),
            cycle, elapsedMs: cycle * 100, connectionEpoch: 1, sourceKind: flags.sourceKind,
            timestampSource: "host-response-observed", processedUtc: new Date().toISOString(), ok: true };
            const { pduHex: _raw, ...sampleEvidence } = evidence;
            emit("cycle", { ...flags, ok: true, cycle, completedCycles: 1, transactions: [evidence],
              samples: [{ ...sampleEvidence, parameterId: parameter, name: "合成温度", unit: "°C", synthetic: true, value: cycle, display: cycle, numeric: true }] }); };
          tick(); timer = setInterval(tick, 100);
        } }); return child;
      } });
    globalThis.__navigationManagers = { session, manufacturer };
    ipcMain.removeHandler(SESSION_CHANNEL); ipcMain.handle(SESSION_CHANNEL, async (event, request) => {
      state.calls.push({ kind: "standard", action: request.action });
      const result = await session.handle(request, { ownerId: event.sender.id });
      if (request.action === "start" && result.ok) state.standard.push({ jobId: result.jobId, batchId: result.engineBatchId });
      return result;
    });
    ipcMain.removeHandler("diagnostics:manufacturerStream"); ipcMain.handle("diagnostics:manufacturerStream", async (event, request) => {
      state.calls.push({ kind: "manufacturer", action: request.action });
      // Test-only IPC routing: exercise the current read button with synthetic
      // workers; this is not a physical-read qualification.
      const result = await manufacturer.handle(request.action === "start-read" ? {
        action: "start", profileId: request.profileId, parameterIds: request.parameterIds, continuous: request.continuous,
      } : request, event.sender.id);
      if (["start", "start-read"].includes(request.action) && result.ok) state.manufacturer.push(result.sessionId);
      return result;
    });
    ipcMain.removeHandler("diagnostics:offline"); ipcMain.handle("diagnostics:offline", (_event, request) => {
      if (request.action === "ready-units") return { ok: true, present: true, units: [{ systemId: "dme", ecuId: 1,
        variants: [{ profileId: profile, name: "Synthetic navigation definition", status: "identity-matched", parameterCount: 1, replayParameterCount: 1 }] }] };
      if (request.action === "ready-parameters") return { ok: true, total: 1, items: [{ id: parameter,
        name: "Synthetic navigation value", unit: "", decoderReady: true, decodedSampleCount: 1 }], groups: [] };
      return { ok: false, error: "not-in-navigation-fixture" };
    });
  }, { root, desktop, modules: Object.fromEntries(["session", "manufacturer", "save"].map(name => [name,
    pathToFileURL(path.join(root, "apps/desktop/electron", { session: "read-only-session.mjs", manufacturer: "manufacturer-stream.mjs", save: "parameter-recording.mjs" }[name])).href])) });
  const side = name => page.locator(`nav.side [data-tab="${name}"]`);
  await side("obd").click();
  for (const tab of ["connection", "topology", "live"]) {
    await page.locator(`[data-obd-tab="${tab}"]`).click();
    for (const id of ["obd-network-host", "obd-network-port", "obd-network-save", "obd-network-remove", "acquisition-replay"]) {
      assert.equal(await page.getByTestId(id).count(), 0, `${id} must not be mounted on ${tab}`);
    }
    assert.doesNotMatch(await page.locator('[data-page="obd"]').innerText(), /TCP \/ Wi-Fi 诊断头|断车后：全候选身份与版本核对|断车后：完整故障码核对|采集记录回放/);
    await page.screenshot({ path: path.join(output, `converged-${tab}.png`), fullPage: true });
  }
  assert.deepEqual(await page.evaluate(() => ["obdConnection", "dtcCapture", "identityCapture", "acquisitionReplay"].map(name =>
    typeof window.porsche981?.[name])), ["function", "function", "function", "function"]);
  checks.push("requested connection/capture/replay panels removed; backend APIs retained");
  // Returning with no connected head opens settings without remounting live data.
  await side("settings").click(); await side("obd").click();
  await page.waitForFunction(() => document.querySelector('[data-obd-tab="connection"]')?.classList.contains("active"));
  await page.locator('[data-obd-tab="live"]').click();
  checks.push("returning to OBD with an authoritative disconnected status opens connection settings");
  await page.getByTestId("eng-system").selectOption("dme"); await page.getByTestId("eng-select-0C").check(); await page.getByTestId("eng-mode").selectOption("simulation");
  await page.getByTestId("eng-start").click(); await page.getByTestId("eng-cancel").waitFor();
  await page.getByTestId("eng-display-graph").click();
  await side("settings").click();
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(await app.evaluate(() => globalThis.__navigation.calls.filter(c => c.kind === "standard" && c.action === "cancel").length), 0,
    "main navigation must not cancel or orphan an acquisition");
  await side("obd").click(); await page.locator('[data-obd-tab="connection"]').click();
  assert.equal(await page.locator('[data-page="engine-session"]').isVisible(), false);
  await page.locator('[data-obd-tab="live"]').click();
  assert.equal(await page.getByTestId("eng-system").inputValue(), "dme"); assert.equal(await page.getByTestId("eng-select-0C").isChecked(), true);
  assert.equal(await page.getByTestId("eng-display-graph").getAttribute("aria-pressed"), "true");
  const before = await app.evaluate(() => globalThis.__navigation.standard.map(s => s.batchId));
  assert.ok(before.length > 0); assert.equal(new Set(before).size, 1);
  await side("settings").click(); await app.evaluate(() => { globalThis.__navigation.clock = 600000; });
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-export"]')?.disabled === false);
  await side("obd").click();
  await page.waitForFunction(() => document.querySelector('[data-obd-tab="connection"]')?.classList.contains("active"));
  await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-export").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="eng-saved"]')?.textContent.includes("已保存"));
  const [file] = (await fs.readdir(desktop)).filter(name => name.endsWith(".pcapng"));
  assert.ok(file.startsWith("SYNTHETIC-")); const saved = parameterRows(await fs.readFile(path.join(desktop, file)));
  assert.ok(saved.length > 0); assert.equal(saved[0].name, "转速");
  assert.equal(new Set(await app.evaluate(() => globalThis.__navigation.standard.map(s => s.batchId))).size, 1);
  checks.push("standard batch, selection, original deadline and complete explicit save survive both navigation levels");
  await page.getByTestId("eng-data-source").selectOption("x431");
  await page.getByTestId(`offline-select-${"a".repeat(64)}`).check();
  await app.evaluate(() => { globalThis.__navigation.clock = 0; });
  assert.equal(await page.getByTestId("manufacturer-start").count(), 0);
  await page.getByTestId("manufacturer-readonly").check();
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => /·\s*[1-9]\d* 个/.test(document.querySelector('[data-testid="manufacturer-state"]')?.textContent || ""));
  await page.getByTestId("manufacturer-display-graph").click();
  await page.locator('[data-obd-tab="connection"]').click(); await side("settings").click();
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(await app.evaluate(() => globalThis.__navigation.calls.filter(c => c.kind === "manufacturer" && c.action === "stop").length), 0);
  await app.evaluate(() => { globalThis.__navigation.clock = 600000; });
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-save"]')?.disabled === false);
  await side("obd").click(); await page.locator('[data-obd-tab="live"]').click();
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /十分钟/);
  assert.equal(await page.getByTestId("manufacturer-display-graph").getAttribute("aria-pressed"), "true");
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  const [manufacturerFile] = (await fs.readdir(desktop)).filter(name => name.endsWith(".pcapng") && name !== file);
  assert.ok(manufacturerFile.startsWith("SYNTHETIC-"));
  const manufacturerSaved = parameterRows(await fs.readFile(path.join(desktop, manufacturerFile)));
  assert.ok(manufacturerSaved.length > 1); assert.ok(manufacturerSaved.every(row => row.name === "合成温度"));
  assert.equal((await app.evaluate(() => globalThis.__navigation.manufacturer)).length, 1);
  checks.push("manufacturer session continues while hidden, expires at original deadline and remains saveable on return");
  await page.locator('[data-obd-tab="topology"]').click();
  await page.getByTestId("topo-node-pdk").first().click(); await page.getByTestId("topo-open-live").click();
  assert.equal(await page.getByTestId("eng-system").inputValue(), "pdk", "explicit target navigation still changes the retained page");
  await page.locator('[data-obd-tab="topology"]').click();
  await page.getByTestId("topo-node-dme").first().click(); await page.getByTestId("topo-open-live").click();
  assert.equal(await page.getByTestId("eng-system").inputValue(), "dme");
  checks.push("explicit topology target changes remain effective after retaining the live page");
  // Presentation-only connection status: no physical connection or device I/O.
  await app.evaluate(({ ipcMain }) => {
    ipcMain.removeHandler("diagnostics:connection");
    ipcMain.handle("diagnostics:connection", () => ({ ok: true, connected: true,
      linkState: "connected", purpose: "diagnostic", selectedDeviceId: null,
      executionEnabled: false, liveVerified: false, writePayload: null }));
  });
  await side("settings").click(); await side("obd").click();
  // Wait for a fresh status poll, not just the initial retained DOM.
  await page.waitForFunction(() => document.querySelector('[data-testid="obd-header-device"]')?.textContent.includes("型号未知"));
  assert.match(await page.locator('[data-obd-tab="live"]').getAttribute("class"), /active/);
  assert.equal(await page.getByTestId("eng-system").inputValue(), "dme");
  checks.push("returning with an injected connected status retains the prior live page and selected unit");
  assert.deepEqual(errors, []); passed = true;
} finally {
  if (app) { await app.evaluate(async () => { await globalThis.__navigationManagers?.session.shutdown(); await globalThis.__navigationManagers?.manufacturer.shutdown(); }).catch(() => {}); await app.close(); }
  clearTimeout(watchdog);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, rendererErrors: errors,
    injectedClock: true, manufacturerTransport: "synthetic", manufacturerStartReadRewrittenByFixture: true,
    standardTransport: "Python simulation", deviceIO: false, vehicleIO: false }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
