/** Production renderer; explicit IPC fixtures, no vehicle or adapter I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { installRealtimeUiIdentityModel, selectIdentifiedRealtimeUnit } from "./obd-live-ui-identity-model.mjs";
const root = path.resolve(import.meta.dirname, ".."), installed = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || ".local/obd-parameter-isolation-20261012/source-ui");
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const checks = [], errors = [];
const watchdog = setTimeout(() => { console.error("isolation UI timeout"); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [installed || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ ipcMain }) => {
    const ids = ["a".repeat(64), "b".repeat(64)], profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
    const params = ids.map((id, i) => ({ id, name: i ? "进气温度" : "冷却液温度", nameResolved: true, unit: "°C",
      requestHex: i ? "22F101" : "22F100", decoderReady: true, status: "ready", decodedSampleCount: 0 }));
    const state = { phase: 0, starts: 0, stopped: false, allUnavailable: false, transient: false, saved: 0 };
    globalThis.parameterIsolationAcceptance = state;
    ipcMain.removeHandler("diagnostics:offline");
    ipcMain.handle("diagnostics:offline", (_e, req) => {
      if (req.action === "ready-units") return { ok: true, present: true, units: [{ systemId: "dme", ecuId: 1, label: "DME",
        variants: [{ profileId, name: "Explicit catalog fixture", status: "identity-matched", parameterCount: 2 }] }] };
      return { ok: true, items: params, total: 2, categories: [] };
    });
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream", (_e, req) => {
      if (["start", "start-read"].includes(req.action)) {
        if (req.action !== "start-read" || JSON.stringify(req.parameterIds) !== JSON.stringify(ids)) throw Error("unexpected fixture operation");
        state.starts++; state.phase = 0; state.stopped = false; return { ok: true, sessionId: "fixture-" + state.starts };
      }
      if (req.action === "stop") { state.stopped = true; return { ok: true }; }
      if (req.action === "discard") return { ok: true };
      if (req.action === "save") { state.saved++; return { ok: true, saved: true, filePath: "SYNTHETIC-parameters.pcapng" }; }
      if (req.action !== "status") throw Error("unexpected fixture operation");
      const samples = (cycle, chosen) => chosen.map(i => ({ parameterId: ids[i], value: 40 + cycle + i,
        display: String(40 + cycle + i), numeric: true, cycle, synthetic: true, elapsedMs: cycle * 100,
        processedUtc: `2026-10-12T01:00:0${cycle}.000Z`, connectionEpoch: 1 }));
      const events = state.allUnavailable ? [{ ok: true, completedCycles: 1, samples: [] }]
        : [{ ok: true, completedCycles: 1, samples: samples(1, [0, 1]) },
          ...(state.phase ? [{ ok: true, completedCycles: 1, samples: samples(2, [1]) }] : [])];
      if (state.transient && state.phase >= 2) events.push({ ok: true, completedCycles: 1, samples: samples(3, [0, 1]) });
      const rejected = state.allUnavailable ? ids : !state.transient && state.phase ? [ids[0]] : [];
      return { ok: true, state: state.stopped || state.allUnavailable ? "finished" : "running",
        released: state.stopped || state.allUnavailable,
        events: events.slice(req.cursor), cursor: events.length, hasMore: false,
        connectionEvents: [], connectionCursor: 0, hasMoreConnectionEvents: false,
        invalidParameters: state.transient && state.phase === 1 ? [{ parameterId: ids[0], reason: "invalid_reserved" }] : [],
        unavailableParameters: rejected.map(parameterId => ({ parameterId, reason: "parameter-unavailable", nrc: 49 })),
        result: state.stopped || state.allUnavailable ? { ok: true,
          stopReason: state.allUnavailable ? "all-parameters-unavailable" : "user-stopped" } : null };
    });
  });
  const matchedProfile = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
  await installRealtimeUiIdentityModel(app, { 1: matchedProfile, 9: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1" });
  await page.locator('nav.side [data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId: matchedProfile });
  const first = "a".repeat(64), second = "b".repeat(64);
  await page.getByTestId(`offline-select-${first}`).check(); await page.getByTestId(`offline-select-${second}`).check();
  await page.getByTestId("manufacturer-read").click();
  const panel = page.getByTestId("manufacturer-rehearsal");
  const row = id => panel.locator(`div[data-parameter-id="${id}"]`).first();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("2 个数值"));
  await app.evaluate(() => { globalThis.parameterIsolationAcceptance.phase = 1; });
  await page.waitForFunction(id => document.querySelector(`div[data-parameter-id="${id}"]`)?.getAttribute("data-unavailable") === "1", first);
  assert.match(await row(first).innerText(), /不可用（本批）.*上次值：41/s);
  assert.match(await row(second).innerText(), /43/);
  assert.equal(await page.getByTestId("manufacturer-stop").isEnabled(), true);
  assert.equal(await page.getByTestId("manufacturer-save").isEnabled(), false);
  await page.getByTestId("manufacturer-display-both").click();
  assert.equal(await panel.locator(`[data-curve-id="${first}"] circle`).count(), 1);
  assert.equal(await panel.locator(`[data-curve-id="${second}"] circle`).count(), 2);
  checks.push("unavailable item retains prior value/time and curve; other item updates while same batch remains active without invented data");
  await page.screenshot({ path: path.join(output, "unavailable-parameter.png"), fullPage: true });
  await page.getByTestId("manufacturer-stop").click();
  await page.getByTestId("manufacturer-save").waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  assert.match(await row(first).innerText(), /不可用（本批）/);
  checks.push("manual stop and explicit save retain current values, unavailable status and historical curve");
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(id => document.querySelector(`div[data-parameter-id="${id}"]`)?.getAttribute("data-unavailable") !== "1", first);
  assert.match(await row(first).innerText(), /41/); assert.equal(await row(first).locator("time").count(), 0);
  await page.getByTestId("manufacturer-display-both").click();
  assert.equal(await panel.locator(`[data-curve-id="${second}"] circle`).count(), 1);
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]')?.disabled);
  checks.push("fresh manually started batch clears prior unavailable verdict and prior samples before re-reading");
  await app.evaluate(() => { globalThis.parameterIsolationAcceptance.allUnavailable = true; });
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("均不可用"));
  assert.equal(await page.getByTestId("manufacturer-save").isEnabled(), false);
  assert.equal(await page.getByTestId("manufacturer-stop").isEnabled(), false);
  assert.equal(await panel.locator('[data-unavailable="1"]').count(), 2);
  assert.equal(await app.evaluate(() => globalThis.parameterIsolationAcceptance.starts), 3);
  checks.push("all selected parameters unavailable ends batch, disables empty save and leaves manual restart available");
  await app.evaluate(() => { const s = globalThis.parameterIsolationAcceptance; s.allUnavailable = false; s.transient = true; });
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(id => document.querySelector(`div[data-parameter-id="${id}"]`)?.textContent?.includes("41"), first);
  await app.evaluate(() => { globalThis.parameterIsolationAcceptance.phase = 1; });
  await page.waitForFunction(id => document.querySelector(`div[data-parameter-id="${id}"]`)?.textContent?.includes("未更新"), first);
  assert.match(await row(first).innerText(), /41.*未更新.*上次值：41/s);
  assert.doesNotMatch(await row(first).innerText(), /不可用/);
  assert.match(await row(second).innerText(), /43/);
  assert.equal(await page.getByTestId("manufacturer-stop").isEnabled(), true);
  await page.getByTestId("manufacturer-display-both").click();
  assert.equal(await panel.locator(`[data-curve-id="${first}"] circle`).count(), 1);
  assert.equal(await panel.locator(`[data-curve-id="${second}"] circle`).count(), 2);
  await page.screenshot({ path: path.join(output, "transient-invalid.png"), fullPage: true });
  await app.evaluate(() => { globalThis.parameterIsolationAcceptance.phase = 2; });
  await page.waitForFunction(id => { const t = document.querySelector(`div[data-parameter-id="${id}"]`)?.textContent; return t?.includes("43") && !t.includes("未更新"); }, first);
  assert.equal(await panel.locator(`[data-curve-id="${first}"] circle`).count(), 2);
  assert.equal(await panel.locator(`[data-curve-id="${second}"] circle`).count(), 3);
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  checks.push("transient invalid value keeps last value marked stale, healthy parameter continues and later fresh value recovers without synthetic curve points");
  await app.evaluate(async ({ ipcMain }, modulePath) => {
    const vm = process.getBuiltinModule("vm"), { pathToFileURL } = process.getBuiltinModule("url");
    const { EventEmitter } = process.getBuiltinModule("events");
    const { createReadOnlySessionManager } = await vm.runInThisContext(`import(${JSON.stringify(pathToFileURL(modulePath).href)})`,
      { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const state = { requests: [], saves: [] }; globalThis.standardIsolationAcceptance = state;
    const manager = createReadOnlySessionManager({ repoRoot: process.cwd(), spawnFn: () => {
      const child = new EventEmitter(); child.stdin = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
      let body;
      child.stdin.write = text => { body = JSON.parse(text); return true; };
      child.stdin.end = () => {}; child.kill = () => { child.killed = true; };
      queueMicrotask(() => {
        const index = state.requests.length; state.requests.push(body);
        const selected = body.selectedPids, unavailable = index === 0 ? ["0C"] : index === 1 ? ["0D"] : [];
        const result = { type: "result", ok: true, mode: "simulation", simulation: true, liveVerified: false, writePayload: null,
          runId: "isolation_ui_run_" + index, profileId: body.profileId, status: "completed", error: null,
          sessionTask: "engine", results: [], engine: { selectedPids: selected, unsupportedPids: unavailable,
            supportedPids: selected.filter(pid => !unavailable.includes(pid)), completedCycles: 1,
            sampleCycles: body.sampleCycles, intervalMs: body.intervalMs,
            samples: selected.filter(pid => !unavailable.includes(pid)).map(pid => ({ pid, label: pid, value: 42, unit: pid === "0C" ? "rpm" : "km/h",
              capturedUtc: "2026-10-12T01:00:00.000Z", elapsedMs: 10, cycle: 1, synthetic: true })) } };
        child.stdout.emit("data", Buffer.from(JSON.stringify(result) + "\n")); child.exitCode = 0; child.emit("close", 0);
      });
      return child;
    }, saveEngineRecording: async recording => {
      state.saves.push(recording); return { ok: true, saved: true, filePath: "SYNTHETIC-standard.pcapng" };
    } });
    ipcMain.removeHandler("diagnostics:session"); ipcMain.handle("diagnostics:session", (event, req) => manager.handle(req, { ownerId: event.sender.id }));
  }, path.join(installed || path.join(root, "apps/desktop"), installed ? "apps/desktop/electron/read-only-session.mjs" : "electron/read-only-session.mjs"));
  // The standard page is retired. Preserve its real batch-manager checks
  // through preload/IPC rather than restoring controls that users removed.
  const standard = await page.evaluate(async () => {
    const api = window.porsche981.readOnlySession;
    const request = { action: "start", profileId: "porsche-981-2014-dme", sessionTask: "engine", mode: "simulation",
      selectedPids: ["0C", "0D"], sampleCycles: 1, intervalMs: 500 };
    const wait = async jobId => {
      const deadline = Date.now() + 10000;
      while (Date.now() < deadline) {
        const doc = await api({ action: "status", jobId });
        if (!doc.ok || ["completed", "failed", "cancelled"].includes(doc.state)) return doc;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      throw Error("standard isolation batch timeout");
    };
    const first = await api(request); const firstResult = await wait(first.jobId);
    const second = await api({ ...request, engineBatchId: first.engineBatchId }); const secondResult = await wait(second.jobId);
    const saved = await api({ action: "saveEngine", engineBatchId: first.engineBatchId });
    const fresh = await api(request); const freshResult = await wait(fresh.jobId);
    return { first, firstResult, second, secondResult, saved, fresh, freshResult };
  });
  assert.equal(standard.first.ok, true); assert.equal(standard.second.ok, true);
  assert.equal(standard.secondResult.stopReason, "all-parameters-unavailable"); assert.equal(standard.saved.ok, true);
  assert.deepEqual(await app.evaluate(() => globalThis.standardIsolationAcceptance.requests.map(r => r.selectedPids)), [["0C", "0D"], ["0D"], ["0C", "0D"]]);
  assert.equal(await app.evaluate(() => globalThis.standardIsolationAcceptance.saves[0].samples.length), 1);
  assert.notEqual(standard.fresh.engineBatchId, standard.first.engineBatchId);
  assert.deepEqual(standard.freshResult.final.engine.unsupportedPids, []);
  checks.push("retained standard IPC filters unavailable PID, stops empty continuation, saves actual retained value and resets a fresh batch without retired UI");
  // Source failures are task messages, with private backend detail kept out of the page.
  await app.evaluate(({ ipcMain }) => {
    const id = "a".repeat(64), profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
    const state = { catalogError: "", parameterError: "", throwCatalog: false, throwParameter: false, startError: "", finishError: "" };
    globalThis.sourceErrorAcceptance = state;
    ipcMain.removeHandler("diagnostics:offline");
    ipcMain.handle("diagnostics:offline", (_e, req) => {
      if (req.action === "ready-units") {
        if (state.throwCatalog) throw Error(state.catalogError);
        if (state.catalogError) return { ok: false, error: state.catalogError };
        return { ok: true, present: true, units: [{ systemId: "dme", ecuId: 1, label: "DME",
          variants: [{ profileId, name: "Explicit source-error fixture", status: "identity-matched", parameterCount: 1 }] }] };
      }
      if (state.throwParameter) throw Error(state.parameterError);
      if (state.parameterError) return { ok: false, error: state.parameterError };
      return { ok: true, total: 1, categories: [], items: [{ id, name: "冷却液温度", nameResolved: true,
        unit: "°C", requestHex: "22F100", decoderReady: true, status: "ready", decodedSampleCount: 0 }] };
    });
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream", (_e, req) => {
      if (req.action === "start-read") return state.startError ? { ok: false, error: state.startError } : { ok: true, sessionId: "source-error-fixture" };
      if (req.action === "status") return { ok: true, state: "finished", released: true, cursor: 1, connectionCursor: 0,
        error: state.finishError, result: { ok: !state.finishError, error: state.finishError || null },
        events: req.cursor ? [] : [{ ok: true, completedCycles: 1, samples: [{ parameterId: id, value: 41,
          display: "41", numeric: true, synthetic: true, cycle: 1, elapsedMs: 10,
          processedUtc: "2026-10-15T01:00:00.000Z", connectionEpoch: 1 }] }] };
      if (["discard", "stop"].includes(req.action)) return { ok: true };
      throw Error("unexpected source error fixture action");
    });
  });
  await installRealtimeUiIdentityModel(app, { 1: matchedProfile, 9: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1" });
  async function catalogState(state) {
    await page.getByTestId("eng-system").selectOption("");
    await app.evaluate((_, change) => Object.assign(globalThis.sourceErrorAcceptance,
      { catalogError: "", parameterError: "", throwCatalog: false, throwParameter: false, startError: "", finishError: "" }, change), state);
    await page.getByTestId("eng-system").selectOption("dme");
  }
  async function privateMessage(locator, expected) {
    await locator.waitFor(); const text = await locator.innerText();
    assert.match(text, expected); assert.match(text, /请/);
    assert.doesNotMatch(text, /PRIVATE|Errno|ENOENT|source-hash|\.json|\.py|Error invoking|详细错误/);
  }
  const missingSource = "[Errno 2] No such file or directory: 'C:\\PRIVATE\\manifest.json'";
  await catalogState({ catalogError: missingSource });
  await privateMessage(page.getByTestId("offline-error"), /资料不完整/);
  await page.screenshot({ path: path.join(output, "missing-catalog.png"), fullPage: true });
  checks.push("missing catalog shows source reason and remedy without raw path or errno");
  await catalogState({ catalogError: "PRIVATE_IPC_STACK", throwCatalog: true });
  await privateMessage(page.getByTestId("offline-error"), /诊断资料暂时无法读取/);
  checks.push("catalog IPC rejection keeps technical text out of the task message");
  for (const thrown of [false, true]) {
    await catalogState({ parameterError: "PRIVATE damaged JSON at column 12", throwParameter: thrown });
    await privateMessage(page.getByTestId("offline-error"), /诊断资料暂时无法读取/);
    assert.equal(await page.getByTestId("offline-parameters").locator("input").count(), 0);
  }
  checks.push("parameter list error and IPC rejection show remedy without stale rows or raw decoder details");
  for (const [error, expected] of [["source-hash-mismatch:PRIVATE.json", /资料与当前程序不匹配/],
    ["manufacturer-read-code-missing", /资料与当前程序不匹配/],
    ["manufacturer-profile-unqualified", /当前控制单元版本.*独立读取适配/],
    ["[Errno 2] Could not open port PRIVATE_COM99", /诊断头连接/]]) {
    await catalogState({ startError: error });
    await page.getByTestId("offline-select-" + "a".repeat(64)).check();
      await page.getByTestId("manufacturer-read").click();
    await privateMessage(page.getByTestId("manufacturer-error"), expected);
    assert.equal(await page.getByTestId("manufacturer-save").isEnabled(), false);
    checks.push("start failure preserves its one translated task reason: " + error.split(":")[0]);
  }
  await catalogState({ finishError: missingSource });
  await page.getByTestId("offline-select-" + "a".repeat(64)).check();
  await page.getByTestId("manufacturer-read").click();
  await privateMessage(page.getByTestId("manufacturer-error"), /资料不完整/);
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  assert.match(await page.locator('div[data-parameter-id="' + "a".repeat(64) + '"]').innerText(), /41/);
  await page.screenshot({ path: path.join(output, "source-failure-retains-data.png"), fullPage: true });
  checks.push("source failure after a retained sample preserves its value and save capability after verified release");
  assert.deepEqual(errors, []); passed = true;
} finally {
  if (app) await app.close(); clearTimeout(watchdog);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, errors,
    injectedIpc: true, injectedStandardWorker: true, installedEntryOnMac: Boolean(installed), host: "Mac", windowsExecuted: false,
    deviceIO: false, vehicleIO: false, goalComplete: false }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
