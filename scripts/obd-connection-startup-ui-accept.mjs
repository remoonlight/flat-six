/** Production startup/selection plus explicitly injected connection errors; no device I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-connection-convergence-20261012/startup-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const stateFile = path.join(temp, "connection.json"), fixture = path.join(temp, "devices.json");
const deviceId = "bt:AABBCCDDEEFF";
await fs.writeFile(fixture, JSON.stringify({ devices: [{ id: deviceId, brand: "OBDLink MX+", available: true, paired: true }], errors: [] }));
await fs.writeFile(stateFile, JSON.stringify({ deviceId, model: "OBDLink MX+", purpose: "internal", canNetwork: "drive", serialBaud: 230400 }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: stateFile };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false;
const errors = [], checks = [];
const watchdog = setTimeout(() => { console.error("startup acceptance total timeout"); void app?.close(); }, 90000);
try {
  for (let run = 0; run < 2; run++) {
    app = await electron.launch({ args: [appRoot || path.join(root, "apps/desktop")], env, timeout: 30000 });
    const page = await app.firstWindow(); page.setDefaultTimeout(12000);
    page.on("pageerror", error => errors.push(error.message));
    await page.locator('nav.side [data-tab="obd"]').click();
    await page.locator(`[data-testid="obd-device-${deviceId}"]`).waitFor();
    assert.match(await page.locator('[data-obd-tab="connection"]').getAttribute("class"), /active/);
    assert.equal(await page.getByTestId("obd-purpose-diagnostic").isChecked(), true);
    assert.equal(await page.locator('input[name="obd-device"]:checked').count(), 0);
    assert.equal(await page.getByTestId("obd-conn-connect").isDisabled(), true);
    const state = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
    assert.equal(state.selectedDeviceId, null); assert.equal(state.connected, false);
    assert.equal(state.purpose, "diagnostic"); assert.equal(state.canNetwork, null);
    assert.equal(await page.getByTestId("obd-device-support-info").count(), 0);
    assert.equal(await page.getByTestId("obd-advanced-settings").count(), 0);
    await page.getByTestId(`obd-device-${deviceId}`).check();
    await page.waitForFunction(id => document.querySelector(`[data-testid="obd-device-${id}"]`)?.checked
      && !document.querySelector('[data-testid="obd-conn-connect"]')?.disabled, deviceId);
    assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, false);
    assert.equal(await page.getByTestId("obd-serial-baud").count(), 0);
    await page.getByTestId("obd-purpose-drive").check();
    await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose-drive"]')?.checked);
    assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, false);
    await page.screenshot({ path: path.join(output, `manual-selection-${run + 1}.png`), fullPage: true });
    checks.push(run === 0 ? "historical internal/device selection ignored; explicit choice enables connect without opening"
      : "application restart again requires selection and defaults to diagnostic");
    if (run === 0) {
      await page.getByTestId("obd-conn-refresh").click();
      await page.waitForFunction(() => !document.querySelector('[data-testid="obd-purpose"]')?.disabled);
      assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).selectedDeviceId, deviceId);
      assert.equal(await page.getByTestId(`obd-device-${deviceId}`).isChecked(), true);
      const replacementId = "bt:112233445566";
      await fs.writeFile(fixture, JSON.stringify({ devices: [{ id: replacementId, brand: "vLinker", available: true, paired: true }], errors: [] }));
      await page.getByTestId("obd-conn-refresh").click();
      await page.getByTestId(`obd-device-${replacementId}`).waitFor();
      const removed = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
      assert.equal(removed.selectedDeviceId, null); assert.equal(removed.model, null);
      assert.equal(removed.purpose, "diagnostic"); assert.equal(removed.canNetwork, null);
      assert.equal(await page.locator('input[name="obd-device"]:checked').count(), 0);
      assert.equal(await page.getByTestId("obd-conn-connect").isDisabled(), true);
      assert.equal(removed.monitorDiagnostics.starts, 0);
      await fs.writeFile(fixture, JSON.stringify({ devices: [{ id: deviceId, brand: "OBDLink MX+", available: true, paired: true }], errors: [] }));
      await page.getByTestId("obd-conn-refresh").click();
      await page.getByTestId(`obd-device-${deviceId}`).waitFor();
      assert.equal(await page.getByTestId(`obd-device-${deviceId}`).isChecked(), false);
      await page.getByTestId(`obd-device-${deviceId}`).check();
      await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-connect"]')?.disabled);
      checks.push("production manual refresh retains an available selected head, clears a vanished head and its model/wiring without choosing a replacement, and never opens a monitor");
      const snapshot = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
      // Only the following presentation cases replace IPC. Genuine production
      // startup and selection above, and the restart below, use the real manager.
      await app.evaluate(({ ipcMain }, snapshot) => {
        const state = globalThis.__connectionPresentation = { failures: 1, calls: [],
          snapshot: { ...snapshot, purpose: "diagnostic", canNetwork: null, connectionError: null, linkState: "idle" } };
        ipcMain.removeHandler("diagnostics:connection");
        ipcMain.handle("diagnostics:connection", (_event, request) => {
          state.calls.push(request);
          if (request.action === "connect") {
            if (state.failures-- > 0) return { ...state.snapshot, ok: false, error: "synthetic_unclassified_failure" };
            state.snapshot = { ...state.snapshot, connected: true, linkState: "connected", connectionError: null };
          }
          if (request.action === "select" && state.switchMode) {
            state.pendingId = request.deviceId;
            return new Promise(resolve => { state.finishSelection = result => {
              state.snapshot = { ...state.snapshot, ...result }; state.pendingId = null;
              resolve({ ...state.snapshot, ok: result.ok });
            }; });
          }
          if (request.action === "disconnect") {
            if (state.delayRelease) return new Promise(resolve => { state.finishRelease = result => {
              state.snapshot = { ...state.snapshot, ...result }; resolve(state.snapshot);
            }; });
            state.snapshot = { ...state.snapshot, connected: false, linkState: "idle", connectionError: null };
          }
          if (request.action === "configure") {
            if (state.configureFailure) return { ...state.snapshot, ok: false, error: "synthetic_configure_failure" };
            state.snapshot = { ...state.snapshot, purpose: request.purpose,
              canNetwork: request.purpose === "internal" ? request.canNetwork : null };
          }
          if (request.action === "list") state.snapshot.listErrors = ["synthetic_inventory_failure"];
          return state.snapshot;
        });
      }, snapshot);
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose-diagnostic"]')?.checked);
      await page.getByTestId("obd-conn-connect").click();
      await page.getByTestId("obd-conn-retry-choice").waitFor();
      assert.doesNotMatch(await page.getByTestId("obd-conn-note").locator("p").innerText(), /synthetic_unclassified_failure/);
      assert.equal(await page.getByTestId("obd-conn-error-details").count(), 0);
      await page.waitForTimeout(1800); // Two background status polls must not erase the failed-action notice.
      assert.equal(await page.getByTestId("obd-conn-note").isVisible(), true);
      await page.getByTestId("obd-conn-retry").click();
      await page.getByTestId("obd-conn-retry-choice").waitFor({ state: "detached" });
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action === "connect").map(r => r.deviceId)), [deviceId, deviceId]);
      assert.match(await page.locator('[data-obd-tab="connection"]').getAttribute("class"), /active/);
      assert.match(await page.getByTestId("obd-connection").innerText(), /已连接/);
      const connectedCalls = await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").length);
      await page.locator('[data-obd-tab="live"]').click();
      await page.locator('[data-obd-tab="connection"]').click();
      await page.waitForTimeout(900);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").length), connectedCalls,
        "browsing back to settings must not enumerate or mutate the connected head");
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.snapshot.connected), true);
      checks.push("connected settings re-entry only reads status; no enumeration, disconnect, reconnect or settings mutation");
      await app.evaluate(() => { Object.assign(globalThis.__connectionPresentation.snapshot,
        { connected: false, linkState: "retry-required", connectionError: "synthetic_idle_connection_loss" }); });
      await page.getByTestId("obd-conn-retry-choice").waitFor();
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action === "connect").length), 2);
      await page.getByTestId("obd-conn-terminate").click();
      await page.getByTestId("obd-conn-retry-choice").waitFor({ state: "detached" });
      assert.match(await page.locator('[data-obd-tab="connection"]').getAttribute("class"), /active/);
      await page.getByTestId("obd-conn-refresh").click();
      await page.getByTestId("obd-conn-errors").waitFor();
      assert.equal(await page.getByTestId("obd-list-error-details").count(), 0);
      await page.screenshot({ path: path.join(output, "connection-errors.png"), fullPage: true });
      checks.push("advanced settings and technical error elements absent; injected failures remain readable across polling; retry same head and terminate without closing app");
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation;
        s.calls = []; s.delayRelease = true;
        Object.assign(s.snapshot, { ok: true, purpose: "diagnostic", canNetwork: null, connected: true, linkState: "connected", error: null });
      });
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose"]')?.disabled === false && document.querySelector('[data-testid="obd-connection"]')?.textContent.includes("已连接")
        && document.querySelector('[data-testid="obd-purpose-diagnostic"]')?.checked);
      await page.getByTestId("obd-purpose-drive").click();
      await fs.writeFile(path.join(output, "purpose-dispatch.json"), JSON.stringify({
        state: await app.evaluate(() => ({ snapshot: globalThis.__connectionPresentation.snapshot,
          calls: globalThis.__connectionPresentation.calls, delayRelease: globalThis.__connectionPresentation.delayRelease,
          pendingRelease: typeof globalThis.__connectionPresentation.finishRelease === "function" })),
        text: await page.getByTestId("obd-connection").innerText(),
      }, null, 2));
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose"]')?.disabled, null, { timeout: 3000 }).catch(async error => {
        await fs.writeFile(path.join(output, "purpose-release-diagnosis.json"), JSON.stringify({
          state: await app.evaluate(() => ({ snapshot: globalThis.__connectionPresentation.snapshot,
            calls: globalThis.__connectionPresentation.calls, delayRelease: globalThis.__connectionPresentation.delayRelease,
            pendingRelease: typeof globalThis.__connectionPresentation.finishRelease === "function" })),
          text: await page.getByTestId("obd-connection").innerText(),
        }, null, 2));
        throw error;
      });
      assert.equal(await page.getByTestId("obd-purpose-drive").isDisabled(), true);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => !["status", "list"].includes(r.action)).map(r => r.action)), ["disconnect"]);
      await app.evaluate(() => globalThis.__connectionPresentation.finishRelease({ ok: false, connected: false, linkState: "close_failed", error: "adapter_release_not_verified" }));
      await page.getByTestId("obd-conn-note").getByText(/诊断头原生释放未确认/).waitFor();
      assert.equal(await page.getByTestId("obd-purpose-diagnostic").isChecked(), true);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => !["status", "list"].includes(r.action)).map(r => r.action)), ["disconnect"]);
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation; s.calls = []; s.delayRelease = false; s.configureFailure = true;
        Object.assign(s.snapshot, { ok: true, connected: true, linkState: "connected", error: null });
      });
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose"]')?.disabled === false && document.querySelector('[data-testid="obd-connection"]')?.textContent.includes("已连接"));
      await page.getByTestId("obd-purpose-drive").click();
      await page.getByTestId("obd-conn-note").getByText(/本次连接操作未能完成/).waitFor();
      assert.equal(await page.getByTestId("obd-conn-error-details").count(), 0);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => !["status", "list"].includes(r.action)).map(r => r.action)), ["disconnect", "configure"]);
      assert.equal(await page.getByTestId("obd-purpose-diagnostic").isChecked(), true);
      checks.push("purpose change waits for release while controls stay disabled; failed release or configuration never connects or changes the accepted purpose (injected IPC)");
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation; s.calls = []; s.configureFailure = false;
        Object.assign(s.snapshot, { ok: true, connected: true, linkState: "connected", error: null });
      });
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose"]')?.disabled === false && document.querySelector('[data-testid="obd-connection"]')?.textContent.includes("已连接"));
      await page.getByTestId("obd-purpose-drive").click();
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose-drive"]')?.checked
        && !document.querySelector('[data-testid="obd-purpose"]')?.disabled);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => !["status", "list"].includes(r.action)).map(r => r.action)), ["disconnect", "configure", "connect"]);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.snapshot.connected), true);
      await app.evaluate(() => { globalThis.__connectionPresentation.failures = 1; globalThis.__connectionPresentation.calls = []; });
      await page.getByTestId("obd-purpose-diagnostic").click();
      await page.getByTestId("obd-conn-retry-choice").waitFor();
      assert.equal(await page.getByTestId("obd-purpose-diagnostic").isChecked(), true);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => !["status", "list"].includes(r.action)).map(r => r.action)), ["disconnect", "configure", "connect"]);
      await page.waitForTimeout(900);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action === "connect").length), 1);
      await page.getByTestId("obd-conn-retry").click();
      await page.getByTestId("obd-conn-retry-choice").waitFor({ state: "detached" });
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action === "connect").map(r => r.deviceId)), [deviceId, deviceId]);
      assert.match(await page.locator('[data-obd-tab="connection"]').getAttribute("class"), /active/);
      checks.push("connected purpose switch disconnects, configures, and connects the same head once; failure waits for explicit retry of the new purpose; unconnected purpose selection never auto-connects (injected IPC)");
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation; s.calls = []; s.delayRelease = true;
      });
      await page.getByTestId("obd-conn-refresh").click();
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-conn-refresh"]')?.disabled);
      assert.equal(await page.getByTestId("obd-purpose-diagnostic").isDisabled(), true);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").map(r => r.action)), ["disconnect"]);
      await app.evaluate(() => globalThis.__connectionPresentation.finishRelease({ ok: false, connected: false, linkState: "close_failed", error: "adapter_release_not_verified" }));
      await page.getByTestId("obd-conn-note").getByText(/诊断头原生释放未确认/).waitFor();
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").map(r => r.action)), ["disconnect"]);
      // Retrying refresh must finish the failed release before enumerating.
      await app.evaluate(() => { globalThis.__connectionPresentation.calls = []; });
      await page.getByTestId("obd-conn-refresh").click();
      await app.evaluate(() => globalThis.__connectionPresentation.finishRelease({ ok: true, connected: false, linkState: "idle", error: null }));
      await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-refresh"]')?.disabled);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").map(r => r.action)), ["disconnect", "list"]);
      assert.equal(await page.getByTestId(`obd-device-${deviceId}`).isChecked(), true);
      assert.equal(await page.getByTestId("obd-conn-connect").isDisabled(), false);
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation; s.calls = []; s.delayRelease = false;
        Object.assign(s.snapshot, { ok: true, connected: true, linkState: "connected", error: null });
      });
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-connection"]')?.textContent.includes("已连接"));
      await page.getByTestId("obd-conn-refresh").click();
      await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-refresh"]')?.disabled
        && !document.querySelector('[data-testid="obd-conn-connect"]')?.disabled);
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").map(r => r.action)), ["disconnect", "list"]);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.snapshot.connected), false);
      checks.push("connected refresh waits for release, blocks enumeration on release failure, retries failed release, then lists without reconnecting; available selection retained (injected IPC; inventory selection uses production checks above)");
      await app.evaluate(() => { globalThis.__connectionPresentation.calls = []; });
      const nextDeviceId = "bt:112233445566";
      await app.evaluate((_electron, { deviceId, nextDeviceId }) => {
        const s = globalThis.__connectionPresentation;
        s.switchMode = true;
        Object.assign(s.snapshot, { ok: true, selectedDeviceId: deviceId, model: "OBDLink MX+", connected: true, purpose: "internal", canNetwork: "drive",
          linkState: "connected", connectionError: null, listErrors: [], devices: [
            { id: deviceId, brand: "OBDLink MX+", available: true, paired: true },
            { id: nextDeviceId, brand: "vLinker", available: true, paired: true }] });
      }, { deviceId, nextDeviceId });
      await page.getByTestId(`obd-device-${nextDeviceId}`).waitFor();
      assert.equal(await page.getByTestId("obd-purpose-drive").isChecked(), true);
      await page.getByTestId(`obd-device-${nextDeviceId}`).click();
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.pendingId), nextDeviceId);
      assert.equal(await page.getByTestId(`obd-device-${deviceId}`).isChecked(), true);
      assert.equal(await page.getByTestId(`obd-device-${nextDeviceId}`).isChecked(), false);
      await app.evaluate(() => globalThis.__connectionPresentation.finishSelection({ ok: false, connected: false,
        linkState: "close_failed", error: "adapter_release_not_verified" }));
      await page.getByTestId("obd-conn-note").getByText(/诊断头原生释放未确认/).waitFor();
      assert.equal(await page.getByTestId(`obd-device-${deviceId}`).isChecked(), true);
      await page.getByTestId(`obd-device-${nextDeviceId}`).click();
      await app.evaluate((_electron, nextDeviceId) => globalThis.__connectionPresentation.finishSelection({ ok: true,
        selectedDeviceId: nextDeviceId, model: "vLinker", purpose: "diagnostic", canNetwork: null, connected: false, linkState: "idle", error: null }), nextDeviceId);
      await page.waitForFunction(id => document.querySelector(`[data-testid="obd-device-${id}"]`)?.checked, nextDeviceId);
      assert.equal(await page.getByTestId("obd-purpose-diagnostic").isChecked(), true);
      assert.match(await page.locator('[data-obd-tab="connection"]').getAttribute("class"), /active/);
      assert.equal(await page.getByTestId("obd-conn-connect").isDisabled(), false);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action === "connect").length), 0);
      checks.push("head selection remains on the old head while release is pending or fails; switches only after a successful result and still requires explicit connect (injected IPC; lifecycle tested in production manager selfcheck)");
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation;
        s.calls = []; s.delayRelease = true;
        Object.assign(s.snapshot, { connected: false, linkState: "connecting", connectionError: null });
      });
      await page.getByTestId("obd-conn-disconnect").getByText("终止连接", { exact: true }).waitFor();
      assert.equal(await page.getByTestId("obd-conn-disconnect").isEnabled(), true);
      await page.getByTestId("obd-conn-disconnect").click();
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-conn-disconnect"]')?.disabled);
      for (const id of [`obd-device-${nextDeviceId}`, "obd-purpose-diagnostic", "obd-conn-refresh", "obd-conn-connect"]) {
        assert.equal(await page.getByTestId(id).isDisabled(), true, `${id} waits for connection termination release`);
      }
      assert.deepEqual(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => !["status", "list"].includes(r.action)).map(r => r.action)), ["disconnect"]);
      await app.evaluate(() => {
        const s = globalThis.__connectionPresentation;
        s.delayRelease = false;
        s.finishRelease({ ok: true, connected: false, linkState: "idle", connectionError: null });
      });
      await page.waitForFunction(() => !document.querySelector('[data-testid="obd-conn-connect"]')?.disabled);
      assert.equal(await page.getByTestId(`obd-device-${nextDeviceId}`).isChecked(), true);
      assert.equal(await page.getByTestId("obd-purpose-diagnostic").isChecked(), true);
      assert.match(await page.locator('[data-obd-tab="connection"]').getAttribute("class"), /active/);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action === "connect").length), 0);
      checks.push("waiting handshake exposes terminate; head/purpose/refresh/reconnect stay locked until release; selected head and purpose remain, no automatic reconnect or app exit (injected IPC)");
      const active = await page.evaluate(() => window.porsche981.readOnlySession({ action: "start",
        profileId: "porsche-981-2014-dme", mode: "simulation", sessionTask: "engine", selectedPids: ["0C"],
        sampleCycles: 10, intervalMs: 1000 }));
      assert.equal(active.ok, true, JSON.stringify(active));
      await page.waitForFunction(() => document.querySelector('[data-testid="obd-purpose"]')?.disabled
        && document.querySelector('[data-testid="obd-conn-disconnect"]')?.disabled);
      for (const id of [`obd-device-${nextDeviceId}`, "obd-purpose-diagnostic", "obd-purpose-drive",
        "obd-conn-refresh", "obd-conn-connect", "obd-conn-disconnect"]) {
        assert.equal(await page.getByTestId(id).isDisabled(), true, `${id} waits for the active task`);
      }
      assert.equal(await page.getByTestId(`obd-device-${nextDeviceId}`).isChecked(), true);
      assert.equal(await page.getByTestId("obd-header-device").isVisible(), true);
      const beforeBusy = await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").length);
      await page.locator('[data-obd-tab="live"]').click();
      await page.locator('[data-obd-tab="connection"]').click();
      await page.waitForTimeout(900);
      assert.equal(await app.evaluate(() => globalThis.__connectionPresentation.calls.filter(r => r.action !== "status").length), beforeBusy,
        "browsing settings during a task must not refresh or operate the device");
      await page.evaluate(jobId => window.porsche981.readOnlySession({ action: "cancel", jobId }), active.jobId);
      await page.waitForFunction(async jobId => (await window.porsche981.readOnlySession({ action: "status", jobId })).released === true, active.jobId);
      await page.waitForFunction(() => !document.querySelector('[data-testid="obd-purpose"]')?.disabled
        && !document.querySelector('[data-testid="obd-conn-disconnect"]')?.disabled);
      assert.equal(await page.getByTestId(`obd-device-${nextDeviceId}`).isChecked(), true);
      checks.push("actual production simulation task locks head/purpose/refresh/connect/disconnect while connection status stays visible; controls return only after actual task release, selection unchanged");
      await page.locator('[data-obd-tab="topology"]').click();
      assert.equal(await page.getByTestId("topo-diagram").isVisible(), true);
      assert.equal(await page.locator('[data-testid^="topo-node-"][data-kind]:not([data-kind="unscanned"]):not([data-kind="pending-adapt"]):not([data-kind="assembly-unconfirmed"])').count(), 0, "opening topology does not start a vehicle scan");
      assert.equal(await page.getByTestId("topo-save-result").count(), 0);
      await page.getByTestId("topo-view-list").click();
      const rows = page.getByTestId("topo-list").locator("tbody tr");
      const order = await rows.locator("button").allTextContents();
      assert.ok(order.length > 10);
      await rows.locator("button").filter({ hasText: "DME" }).first().click();
      assert.equal(await page.getByTestId("topo-unit-details").getAttribute("open"), null);
      assert.equal(await page.getByTestId("topo-unit-details").locator("dl").isVisible(), false);
      assert.equal(await page.evaluate(() => {
        const fault = document.querySelector('[data-testid="topo-fault-info"]');
        const action = document.querySelector('[data-testid="topo-read-selected"]');
        const details = document.querySelector('[data-testid="topo-unit-details"]');
        return !!fault && !!action && !!details
          && !!(fault.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING)
          && !!(action.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING);
      }), true, "fault information and actions precede unit details");
      await page.getByTestId("topo-unit-details").locator("summary").click();
      assert.equal(await page.getByTestId("topo-unit-details").locator("dl").isVisible(), true);
      await page.getByTestId("topo-view-diagram").click();
      await page.getByTestId("topo-view-list").click();
      assert.deepEqual(await rows.locator("button").allTextContents(), order);
      await page.screenshot({ path: path.join(output, "topology-fault-first.png"), fullPage: true });
      checks.push("topology defaults to diagram; list order survives selection/view changes; faults/actions precede collapsed details; no ordinary fault save");
    }
    await app.close(); app = null;
  }
  assert.deepEqual(errors, []); passed = true;
} finally {
  if (app) await app.close();
  clearTimeout(watchdog);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, rendererErrors: errors,
    inventory: "local fixture", errorsAndRetryResponses: "injected IPC after genuine startup selection",
    installedEntryOnMac: Boolean(appRoot), deviceIO: false, vehicleIO: false, scope: "Mac production startup/selection; renderer error/retry behavior with injected IPC" }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
