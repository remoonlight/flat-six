/** Production UI/preload and real coordinator. Recipes, connection and vehicle
 * callbacks are private models; no production recipe or vehicle is enabled. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
const source = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const root = appRoot || source, output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, ".local/obd-maintenance-flow-20261015/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), devices = path.join(temp, "devices.json");
await fs.writeFile(devices, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_USER_DATA: path.join(temp, "user-data"), PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: devices,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PYTHONDONTWRITEBYTECODE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const checks = [], errors = [];
const watchdog = setTimeout(() => { console.error("maintenance UI acceptance timeout"); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [appRoot || path.join(source, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", error => errors.push(error.message));
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="cluster"]').click(); await page.locator('[data-coding-category="maintenance"]').click();
  const selectedFunction = () => page.locator('[data-coding-source-ids~="workshop-service-reset"]');
  await selectedFunction().click(); await page.getByTestId("workshop-execution-start").waitFor();
  assert.equal(await page.getByTestId("workshop-execution-start").isDisabled(), true);
  const capability = await page.evaluate(() => window.porsche981.workshopExecution({ action: "capability", functionId: "service-reset", generation: "981" }));
  assert.equal(capability.ok, true); assert.equal(capability.ready, false);
  const denied = await page.evaluate(async () => [
    await window.porsche981.workshopExecution({ action: "start", functionId: "service-reset", generation: "981" }),
    await window.porsche981.workshopExecution({ action: "start", functionId: "service-reset", generation: "981", steps: [] }),
  ]);
  assert.equal(denied[0].error, "workshop-recipe-unavailable"); assert.equal(denied[1].error, "workshop-request-invalid");
  checks.push("actual production IPC has no qualified recipe; unsupported start and renderer-supplied steps cannot dispatch");
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule("vm"), load = url => vm.runInThisContext(`import(${JSON.stringify(url)})`,
      { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const { createWorkshopExecution } = await load(args.workshop);
    const { createTransportGate } = await load(args.connection);
    const state = globalThis.__maintenanceUi = { mode: "safe", resumeCondition: false, resumeIdentity: "1".repeat(64), sourceHash: "b".repeat(64), attempts: 0, events: [], releases: 0, releaseConfirmed: true };
    const gate = createTransportGate(); let connected = true, reserved = false;
    const connection = {
      snapshot: () => ({ ok: true, purpose: "diagnostic", connected, selectedDeviceId: "usb:" + "a".repeat(64), model: "vLinker", linkState: "diagnostic", voltageVolts: 12.6 }),
      reserveDispatch: () => { if (reserved) return false; reserved = true; return true; }, releaseDispatch: () => { reserved = false; },
      suspendForSession: async () => { connected = false; return { ok: true }; },
      resumeAfterSession: async () => { connected = true; reserved = false; return { ok: true }; },
    };
    const software = (id, label, execute, safeStopAfter = false) => ({ id, label, kind: "software", safeStopBefore: false, safeStopAfter, execute });
    const executor = createWorkshopExecution({ connection, gate, getRecipe: id => id !== "service-reset" ? null : ({
      id, title: "保养复位（测试定义）", sourceQualified: true, sourceHash: state.sourceHash, generations: ["981"],
      open: async () => ({ qualified: true, context: { mode: state.mode } }), preflight: async () => ({ ok: true, resumeIdentity: state.resumeIdentity }),
      close: async () => { state.releases++; return { released: state.releaseConfirmed }; }, observeRelease: async () => state.releaseConfirmed,
      stop: async () => { state.events.push("safe-stop"); return { ok: true }; }, resumeSupported: state.mode === "failure",
      checkResume: async () => ({ ok: state.resumeCondition, resumeIdentity: state.resumeIdentity, cause: "续执行条件尚未满足", remedy: "恢复条件后重新核对。" }),
      steps: [software("initial", "软件读取当前状态", async () => { state.events.push("initial"); return { ok: true }; }),
        { id: "human", label: "请完成当前人工操作", kind: "manual", safeStopBefore: false, safeStopAfter: false },
        software("operation", "软件执行当前动作", async (ctx, options) => {
          state.attempts++; state.events.push("operation");
          if (ctx.mode === "failure" && !options.resuming) return { ok: false, cause: "当前操作条件不满足", remedy: "检查条件后核对能否继续。" };
          if (ctx.mode === "failure" || ctx.mode === "release") return { ok: true };
          await new Promise(resolve => { state.releaseHeld = resolve; }); return { ok: true };
        }, state.mode === "safe"),
        software("verify", "软件核对最终状态", async () => { state.events.push("verify"); return { ok: true }; })],
    }) });
    state.executor = executor; state.gate = gate;
    ipcMain.removeHandler("workshop:execution"); ipcMain.handle("workshop:execution", (event, request) => executor.handle(request, event.sender.id));
    ipcMain.removeHandler("diagnostics:session"); ipcMain.handle("diagnostics:session", (_event, request) => request.action === "overview"
      ? { ok: true, taskState: executor.isIdle() ? "idle" : "running" } : { ok: false, error: "busy" });
    ipcMain.removeHandler("diagnostics:connection"); ipcMain.handle("diagnostics:connection", () => connection.snapshot());
  }, { workshop: pathToFileURL(path.join(root, "apps/desktop/electron/piwis-workshop.mjs")).href,
    connection: pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")).href });
  const waitPhase = phase => page.waitForFunction(async phase => (await window.porsche981.workshopExecution({ action: "status" })).job?.phase === phase, phase);
  await page.waitForFunction(() => !document.querySelector('[data-testid="workshop-execution-start"]')?.disabled);
  await page.getByTestId("workshop-execution-start").click(); await page.getByTestId("workshop-manual-continue").waitFor();
  assert.match(await page.getByTestId("workshop-current-step").innerText(), /人工操作/);
  assert.doesNotMatch(await page.getByTestId("workshop-detail").innerText(), /软件核对最终状态/);
  assert.equal(await page.getByTestId("workshop-detail").getByRole("heading", { name: "操作步骤", exact: true }).count(), 0);
  assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 0);
  await page.locator('nav.side [data-tab="settings"]').click();
  assert.equal(await app.evaluate(() => __maintenanceUi.gate.tryAcquire("other-vehicle-operation")), false);
  await page.locator('nav.side [data-tab="obd"]').click();
  await page.getByTestId("workshop-manual-continue").waitFor();
  await page.waitForFunction(() => document.querySelector('[data-testid="obd-task-status"]')?.getAttribute("data-state") === "running");
  await page.locator('[data-obd-tab="topology"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.getByTestId("workshop-manual-continue").waitFor();
  await page.getByTestId("workshop-manual-continue").click(); await waitPhase("running");
  checks.push("software steps advance; only the current step is shown; manual continuation is explicit; navigation retains the active flow and vehicle lease");
  await page.getByTestId("workshop-cancel").click();
  assert.match(await page.getByTestId("workshop-execution-state").innerText(), /等待安全停止/);
  assert.equal(await page.getByTestId("workshop-execution-result").count(), 0);
  await app.evaluate(() => __maintenanceUi.releaseHeld());
  await waitPhase("cancelled"); await page.getByTestId("workshop-execution-result").waitFor();
  assert.equal(await page.locator('[data-step-id="verify"]').getAttribute("data-step-state"), "pending");
  assert.deepEqual(await app.evaluate(() => __maintenanceUi.events), ["initial", "operation", "safe-stop"]);
  await page.screenshot({ path: path.join(output, "safe-stop-result.png"), fullPage: true });
  checks.push("cancel waits for the declared safe point, retains completed/unexecuted results and waits for explicit return");
  await page.getByTestId("workshop-return").click();
  await app.evaluate(() => { __maintenanceUi.mode = "no-safe"; __maintenanceUi.events = []; });
  await page.getByTestId("workshop-execution-start").click(); await page.getByTestId("workshop-manual-continue").click(); await waitPhase("running");
  await page.getByTestId("workshop-cancel").click(); await app.evaluate(() => __maintenanceUi.releaseHeld());
  await waitPhase("completed");
  await page.getByTestId("workshop-execution-result").filter({ hasText: "取消未能提前生效" }).waitFor();
  assert.deepEqual(await app.evaluate(() => __maintenanceUi.events), ["initial", "operation", "verify"]);
  checks.push("a flow with no safe stopping point completes and explains that cancellation could not take effect early");
  await page.getByTestId("workshop-return").click();
  await app.evaluate(() => { __maintenanceUi.mode = "failure"; __maintenanceUi.attempts = 0; });
  await page.getByTestId("workshop-execution-start").click(); await page.getByTestId("workshop-manual-continue").click(); await waitPhase("failed");
  await page.getByTestId("workshop-execution-failure").filter({ hasText: "条件不满足" }).waitFor();
  assert.equal(await page.locator('[data-step-id="initial"]').getAttribute("data-step-state"), "completed");
  assert.equal(await page.locator('[data-step-id="operation"]').getAttribute("data-step-state"), "failed");
  assert.equal(await page.locator('[data-step-id="verify"]').getAttribute("data-step-state"), "pending");
  await page.getByTestId("workshop-check-resume").click(); await waitPhase("failed");
  assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 1);
  await app.evaluate(() => { __maintenanceUi.resumeCondition = true; __maintenanceUi.resumeIdentity = "2".repeat(64); });
  await page.getByTestId("workshop-check-resume").click(); await waitPhase("failed");
  await page.getByTestId("workshop-resume-failure").filter({ hasText: "不一致" }).waitFor();
  assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 1);
  assert.equal(await page.locator('[data-step-id="initial"]').getAttribute("data-step-state"), "completed");
  assert.equal(await page.locator('[data-step-id="operation"]').getAttribute("data-step-state"), "failed");
  assert.doesNotMatch(await page.getByTestId("workshop-detail").innerText(), /22222222|resumeIdentity/);
  await app.evaluate(() => { __maintenanceUi.resumeIdentity = "1".repeat(64); });
  await page.getByTestId("workshop-check-resume").click(); await waitPhase("resume-ready");
  await page.getByTestId("workshop-resume").waitFor(); assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 1);
  await app.evaluate(() => { __maintenanceUi.resumeIdentity = "3".repeat(64); });
  await page.getByTestId("workshop-resume").click(); await waitPhase("failed");
  await page.getByTestId("workshop-resume-failure").filter({ hasText: "不一致" }).waitFor();
  assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 1);
  await app.evaluate(() => { __maintenanceUi.resumeIdentity = "1".repeat(64); __maintenanceUi.sourceHash = "c".repeat(64); });
  await page.getByTestId("workshop-check-resume").click(); await waitPhase("failed");
  await page.getByTestId("workshop-resume-failure").filter({ hasText: "作业已变化" }).waitFor();
  assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 1);
  checks.push("resume rejects a different vehicle/unit identity at check and execution, rejects a changed source job, preserves original failure/steps and keeps private fingerprints out of the page");
  await app.evaluate(() => { __maintenanceUi.sourceHash = "b".repeat(64); });
  await page.getByTestId("workshop-check-resume").click(); await waitPhase("resume-ready");
  await page.getByTestId("workshop-resume").click(); await waitPhase("completed");
  assert.equal(await app.evaluate(() => __maintenanceUi.attempts), 2);
  checks.push("failure lists completed/failed/unexecuted steps; source-supported continuation checks conditions and waits for a separate explicit continue");
  await page.getByTestId("workshop-return").click();
  await app.evaluate(() => { __maintenanceUi.mode = "release"; __maintenanceUi.releaseConfirmed = false; });
  await page.getByTestId("workshop-execution-start").click(); await page.getByTestId("workshop-manual-continue").click(); await waitPhase("release-pending");
  await page.getByTestId("workshop-execution-failure").filter({ hasText: "释放尚未确认" }).waitFor();
  assert.equal(await page.getByTestId("workshop-execution-result").count(), 0);
  assert.equal(await app.evaluate(() => __maintenanceUi.executor.isIdle()), false);
  await app.evaluate(() => { __maintenanceUi.releaseConfirmed = true; }); await waitPhase("completed");
  await page.getByTestId("workshop-return").click();
  checks.push("release remains busy and retains the lease until a source client confirms completion; polling does not replay vehicle actions");
  assert.deepEqual(errors, []); passed = true;
} catch (error) { errors.push(error.stack || String(error)); throw error; }
finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(() => globalThis.__maintenanceUi?.releaseHeld?.()).catch(() => {}); await app.close().catch(() => {}); }
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, errors,
    productionRecipeEnabled: false, recipesAndConnectionSynthetic: true, realActiveJobWindowExitExercised: false,
    deviceIO: false, vehicleIO: false }, null, 2) + "\n");
}
