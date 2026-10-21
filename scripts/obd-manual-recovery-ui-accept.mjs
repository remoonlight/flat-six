/** Production renderer; explicit IPC fixtures, no vehicle or adapter I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { installRealtimeUiIdentityModel, selectIdentifiedRealtimeUnit } from "./obd-live-ui-identity-model.mjs";
const root = path.resolve(import.meta.dirname, ".."), installed = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || ".local/obd-manual-live-recovery-20261012/source-ui");
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const checks = [], errors = [];
const watchdog = setTimeout(() => { console.error("manual recovery UI timeout"); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [installed || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ ipcMain }) => {
    const id = "a".repeat(64), profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
    const state = { starts: 0, startSelections: [], retries: 0, saves: 0, stopped: false, expired: false, samples: [], connections: [],
      stopCalls: 0, statusCalls: 0, statusFailures: 0, invalidStatuses: 0, deferRelease: false, terminalWithoutRelease: false, finalError: null };
    globalThis.manualRecoveryAcceptance = state;
    const sample = (cycle, epoch) => ({ parameterId: id, value: 40 + cycle, display: String(40 + cycle), numeric: true,
      cycle, synthetic: true, elapsedMs: cycle * 100, processedUtc: `2026-10-12T01:00:0${cycle}.000Z`, connectionEpoch: epoch });
    const add = (episode, phase, attempt, epoch) => state.connections.push({ sequence: state.connections.length + 1,
      episode, phase, attempt, connectionEpoch: epoch, reason: phase === "interrupted" ? "transport-disconnected" : null,
      elapsedMs: state.connections.length * 100, observedUtc: "2026-10-12T01:00:03.000Z",
      ...(phase === "awaiting-retry" ? { linkReleased: true } : {}) });
    state.interrupt = () => { add(2, "interrupted", 0, 2); add(2, "awaiting-retry", 0, 2); };
    ipcMain.removeHandler("diagnostics:offline");
    ipcMain.handle("diagnostics:offline", (_e, req) => req.action === "ready-units"
      ? { ok: true, present: true, units: [{ systemId: "dme", ecuId: 1, label: "DME", variants: [{ profileId,
          name: "Explicit fixture", status: "identity-matched", parameterCount: 1 }] }] }
      : { ok: true, items: [{ id, name: "冷却液温度", nameResolved: true, unit: "°C", requestHex: "22F100",
          decoderReady: true, status: "ready", decodedSampleCount: 0 }], total: 1, categories: [] });
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream", (_e, req) => {
      if (req.action === "start-read") {
        state.starts++; state.startSelections.push(req.parameterIds); state.stopped = false; state.expired = false; state.connections = [];
        state.samples = [{ ok: true, completedCycles: 1, samples: [sample(1, 1)] }];
        add(1, "interrupted", 0, 1); add(1, "awaiting-retry", 0, 1);
        const started = { ok: true, sessionId: "fixture-" + state.starts };
        if (state.deferStart) return new Promise(resolve => { state.finishStart = () => resolve(started); });
        return started;
      }
      if (req.action === "retry") {
        const last = state.connections.at(-1);
        if (state.stopped || state.expired || last.phase !== "awaiting-retry" || req.episode !== last.episode || req.attempt !== last.attempt)
          return { ok: false, error: "stream_retry_not_available" };
        state.retries++; add(1, "retrying", 1, 1); add(1, "qualified", 1, 2); add(1, "recovered", 1, 2);
        state.samples.push({ ok: true, completedCycles: 1, samples: [sample(2, 2)] });
        return { ok: true };
      }
      if (req.action === "stop") {
        state.stopCalls++;
        if (state.stopFailures > 0) { state.stopFailures--; throw Error("SYNTHETIC stop IPC failure"); }
        state.stopped = true; return { ok: true };
      }
      if (req.action === "discard") return { ok: true };
      if (req.action === "save") { state.saves++; return state.saveError ? { ok: false, saved: false, error: state.saveError } : { ok: true, saved: true, filePath: "SYNTHETIC-parameters.pcapng" }; }
      if (req.action !== "status") throw Error("unexpected operation");
      state.statusCalls++;
      if (state.statusFailures > 0) { state.statusFailures--; throw Error("SYNTHETIC status IPC failure"); }
      if (state.invalidStatuses > 0) { state.invalidStatuses--; return { ok: true, state: "running", events: null, cursor: req.cursor }; }
      const finished = state.stopped || state.expired;
      return { ok: true, state: finished ? state.deferRelease && !state.terminalWithoutRelease ? "closing" : "finished" : "running",
        released: finished && !state.deferRelease, error: finished && !state.deferRelease ? state.finalError : null,
        events: state.samples.slice(req.cursor), cursor: state.samples.length,
        hasMore: false, connectionEvents: state.connections.slice(req.connectionCursor), connectionCursor: state.connections.length,
        hasMoreConnectionEvents: false, awaitingRetry: !finished && state.connections.at(-1)?.phase === "awaiting-retry",
        result: finished ? { ok: false, error: state.expired ? "deadline-expired" : "cancelled", stopReason: state.expired ? "time-limit" : "user-stopped" } : null };
    });
  });
  const matchedProfile = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
  await installRealtimeUiIdentityModel(app, { 1: matchedProfile, 9: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1" });
  await page.locator('nav.side [data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId: matchedProfile });
  const id = "a".repeat(64);
  await page.getByTestId(`offline-select-${id}`).check();
  await page.getByTestId("manufacturer-read").click();
  assert.equal(await page.getByTestId("offline-selection").isVisible(), true);
  assert.equal(await page.getByTestId("offline-selection").locator("summary").count(), 0);
  assert.ok(await page.getByTestId("offline-parameters").locator("input").evaluateAll(xs => xs.every(x => x.disabled)));
  assert.equal(await page.getByTestId("manufacturer-display-text").getAttribute("aria-pressed"), "true");
  assert.equal(await page.getByTestId("live-curve-groups").count(), 0);
  const row = page.getByTestId("manufacturer-rehearsal").locator(`div[data-parameter-id="${id}"]`).first();
  await page.getByTestId("manufacturer-recovery").waitFor();
  assert.match(await row.innerText(), /41.*未更新/s);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.retries), 0);
  checks.push("interruption shows manual choices, retains last value and time with stale marker, no automatic retry");
  await page.getByTestId("manufacturer-retry").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("2 个数值"));
  assert.match(await row.innerText(), /42/); assert.doesNotMatch(await row.innerText(), /未更新/);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.retries), 1);
  checks.push("one click continues same batch; verified fresh value clears stale marker and adds actual sample only");
  await page.getByTestId("manufacturer-display-graph").click();
  assert.equal(await page.getByTestId("live-curve-groups").count(), 1);
  assert.equal(await row.count(), 0);
  await page.getByTestId("manufacturer-display-text").click();
  await app.evaluate(() => globalThis.manualRecoveryAcceptance.interrupt());
  await page.getByTestId("manufacturer-recovery").waitFor();
  await page.getByTestId("manufacturer-terminate").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  const beforeSave = await row.innerText();
  for (const code of ["ENOSPC", "SYNTHETIC_UNKNOWN_SAVE_FAILURE"]) {
    await app.evaluate((_e, code) => { globalThis.manualRecoveryAcceptance.saveError = code; }, code);
    await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-error").waitFor();
    assert.match(await page.getByTestId("manufacturer-error").innerText(), /已有数据保留.*重试/);
    assert.doesNotMatch(await page.getByTestId("manufacturer-error").innerText(), /SYNTHETIC_UNKNOWN/);
    assert.equal(await page.getByTestId("manufacturer-save-error-details").count(), 0);
    assert.equal(await row.innerText(), beforeSave);
    assert.equal(await page.getByTestId("manufacturer-save").isEnabled(), true);
  }
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.saves), 2);
  await app.evaluate(() => { globalThis.manualRecoveryAcceptance.saveError = null; });
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  assert.equal(await page.getByTestId("manufacturer-save-error-details").count(), 0);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.saves), 3);
  checks.push("manufacturer save failure preserves data, shows a remedy and no technical detail element, and retries the same retained batch only on user click");
  assert.match(await row.innerText(), /42.*未更新/s);
  checks.push("terminate remains on realtime page with prior values and explicit save, saved data stays visible");
  await page.getByTestId("manufacturer-read").click(); await page.getByTestId("manufacturer-recovery").waitFor();
  await app.evaluate(() => { globalThis.manualRecoveryAcceptance.expired = true; });
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("十分钟"));
  assert.equal(await page.getByTestId("manufacturer-recovery").count(), 0);
  assert.equal(await page.getByTestId("manufacturer-save").isEnabled(), true);
  assert.equal(await page.getByTestId("manufacturer-error").count(), 0);
  checks.push("deadline ends pending choice without generic failure, retains samples and enables explicit save/restart");
  assert.deepEqual(await app.evaluate(() => globalThis.manualRecoveryAcceptance.startSelections), [[id], [id]]);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /1 个数值/);
  assert.match(await row.innerText(), /41.*未更新/s);
  assert.doesNotMatch(await row.innerText(), /42/);
  checks.push("manufacturer restart retains the same parameter selection and clears the prior batch before accepting new observations");
  // Force an unsupported-model render before each supported family so this
  // cannot pass while the renderer still has the prior connection snapshot.
  await app.evaluate(({ ipcMain }) => {
    const state = globalThis.manualRecoveryAcceptance;
    state.adapterModel = "PT3G";
    ipcMain.removeHandler("diagnostics:connection");
    ipcMain.handle("diagnostics:connection", (_event, request) => request.action === "status"
      ? { ok: true, model: state.adapterModel, connected: state.adapterModel !== "PT3G", linkState: state.adapterModel !== "PT3G" ? "connected" : "idle", purpose: "diagnostic", selectedDeviceId: "explicit-ui-" + state.adapterModel }
      : { ok: true, devices: [], errors: [] });
  });
  for (const model of ["ELM327", "OBDKey", "OBDLink"]) {
    await app.evaluate(() => { globalThis.manualRecoveryAcceptance.adapterModel = "PT3G"; });
    await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-read"]')?.textContent.includes("未完成"));
    const before = await app.evaluate(() => globalThis.manualRecoveryAcceptance.starts);
    await app.evaluate((_electron, model) => { globalThis.manualRecoveryAcceptance.adapterModel = model; }, model);
    // The completed batch is retained across disconnect; explicitly identify
    // the new head's unit before discarding that batch and starting again.
    await page.getByTestId("manufacturer-identify-retry").click();
    await page.waitForFunction(profileId => document.querySelector('[data-testid="offline-realtime"]')?.dataset.profileId === profileId, matchedProfile);
    await page.getByTestId(`offline-select-${id}`).check();
    await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]')?.disabled);
    assert.doesNotMatch(await page.getByTestId(`offline-select-${id}`).locator("..").innerText(), /未完成/);
    await page.getByTestId("manufacturer-read").click();
    await page.getByTestId("manufacturer-recovery").waitFor();
    assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.starts), before + 1);
    await page.getByTestId("manufacturer-terminate").click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]')?.disabled);
    checks.push(`${model}: existing named manufacturer read route is reachable, retains manual retry/terminate and does not claim physical qualification`);
  }
  await page.getByTestId("manufacturer-read").click(); await page.getByTestId("manufacturer-recovery").waitFor();
  let stops = await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.deferRelease = true; return s.stopCalls; });
  await page.getByTestId("manufacturer-terminate").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes("等待诊断头释放"));
  await page.waitForTimeout(600);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-read").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-stop").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-error").count(), 0);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.stopCalls), stops + 1);
  await app.evaluate(() => { globalThis.manualRecoveryAcceptance.deferRelease = false; });
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]')?.disabled);
  assert.equal(await page.getByTestId("manufacturer-error").count(), 0);
  checks.push("normal manufacturer stop retains data and locks save/restart until explicit release, sends stop once and shows no cancellation error");

  await page.getByTestId("manufacturer-read").click(); await page.getByTestId("manufacturer-recovery").waitFor();
  stops = await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.deferRelease = true; s.statusFailures = 3; return s.stopCalls; });
  await page.getByTestId("manufacturer-error").waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes("等待诊断头释放"));
  await page.waitForTimeout(700);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.statusFailures), 0);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.stopCalls), stops + 1);
  assert.match(await row.innerText(), /41/);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-read").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-error-details").count(), 0);
  assert.doesNotMatch(await page.getByTestId("manufacturer-error").innerText(), /SYNTHETIC|IPC/);
  await app.evaluate(() => { globalThis.manualRecoveryAcceptance.terminalWithoutRelease = true; });
  await page.waitForTimeout(600);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-read").isDisabled(), true);
  checks.push("repeated status failures keep the same owned batch and data, request stop once, and terminal state alone cannot unlock without release evidence");
  await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.finalError = "adapter_release_not_verified"; s.deferRelease = false; });
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /释放未通过核对/);
  await page.getByTestId("manufacturer-save").click(); await page.getByTestId("manufacturer-saved").waitFor();
  assert.match(await page.getByTestId("manufacturer-error").innerText(), /释放未通过核对/);
  assert.match(await row.innerText(), /41/);
  checks.push("release failure is shown after worker exit without technical detail elements; saving retained values does not erase the connection failure");

  await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.finalError = null; s.terminalWithoutRelease = false; });
  await page.getByTestId("manufacturer-read").click(); await page.getByTestId("manufacturer-recovery").waitFor();
  stops = await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.invalidStatuses = 3; s.deferRelease = true; return s.stopCalls; });
  await page.getByTestId("manufacturer-error").waitFor(); await page.waitForTimeout(700);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.invalidStatuses), 0);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.stopCalls), stops + 1);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.match(await row.innerText(), /41/);
  await app.evaluate(() => { globalThis.manualRecoveryAcceptance.deferRelease = false; });
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]')?.disabled);
  checks.push("malformed status replies preserve accepted samples and keep polling the same job through release, without repeated stop requests");
  stops = await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.deferStart = true; s.stopFailures = 1; return s.stopCalls; });
  await page.getByTestId("manufacturer-read").click();
  await page.getByTestId("manufacturer-stop").click();
  await app.evaluate(() => { const s = globalThis.manualRecoveryAcceptance; s.finishStart(); s.deferStart = false; });
  await page.getByTestId("manufacturer-error").waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes("等待诊断头释放"));
  await page.waitForTimeout(600);
  assert.equal(await app.evaluate(() => globalThis.manualRecoveryAcceptance.stopCalls), stops + 1);
  assert.equal(await page.getByTestId("manufacturer-read").isDisabled(), true);
  assert.equal(await page.getByTestId("manufacturer-save").isDisabled(), true);
  assert.match(await row.innerText(), /41/);
  await app.evaluate(() => { globalThis.manualRecoveryAcceptance.stopped = true; });
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-read"]')?.disabled);
  checks.push("stop during start IPC waits for the returned owned job; stop IPC rejection preserves data and locks through explicit release");
  await app.evaluate(({ ipcMain }) => {
    ipcMain.removeHandler("diagnostics:connection");
    ipcMain.handle("diagnostics:connection", (_event, request) => request.action === "status"
      ? { ok: true, model: "PT3G", connected: false, linkState: "idle", purpose: "diagnostic" }
      : { ok: true, devices: [], errors: [] });
  });
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-read"]')?.disabled
    && document.querySelector('[data-testid="manufacturer-read"]')?.textContent.includes("未完成"));
  assert.equal(await page.getByTestId("eng-data-source").count(), 0);
  assert.equal(await page.getByTestId("offline-selection").count(), 0);
  checks.push("unfinished head/function remains visible with unfinished label and cannot start a vehicle operation");
  await page.screenshot({ path: path.join(output, "manual-recovery.png"), fullPage: true });
  assert.deepEqual(errors, []); passed = true;
} finally {
  if (!passed && app) {
    const page = await app.firstWindow();
    await fs.writeFile(path.join(output, "failure-state.json"), JSON.stringify({
      renderer: await page.evaluate(() => [...document.querySelectorAll('[data-testid^="manufacturer-"], [data-testid="offline-version"]')].map(node => ({
        id: node.getAttribute("data-testid"), text: node.textContent, disabled: node.disabled, checked: node.checked,
      }))),
      adapterModel: await app.evaluate(() => globalThis.manualRecoveryAcceptance?.adapterModel),
    }, null, 2) + "\n");
    await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
  }
  clearTimeout(watchdog); await app?.close();
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, errors, fixtureOnly: true,
    vehicleIO: false, deviceIO: false, windowsExecuted: false, installed: Boolean(installed) }, null, 2) + "\n");
}
console.log(JSON.stringify({ passed, checks }));
