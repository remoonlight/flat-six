/** Production renderer; explicit IPC fixtures, no vehicle or adapter I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
const root = path.resolve(import.meta.dirname, ".."), installed = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || ".local/obd-standard-manual-recovery-20261012/source-ui");
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
  await app.evaluate(async ({ ipcMain }, modulePath) => {
    const vm = process.getBuiltinModule("vm"), { pathToFileURL } = process.getBuiltinModule("url"), { EventEmitter } = process.getBuiltinModule("events");
    const { createReadOnlySessionManager } = await vm.runInThisContext(`import(${JSON.stringify(pathToFileURL(modulePath).href)})`,
      { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const state = { requests: [], commands: [], saves: [], clock: 0, failRetry: false, child: null, engine: null,
      sequence: 0, epoch: 1, attempt: 0, runId: "", deferClose: false, statusFailures: 0, statusCalls: 0 };
    globalThis.standardRecoveryAcceptance = state;
    const send = event => state.child.stdout.emit("data", Buffer.from(JSON.stringify(event)+"\n"));
    const connection = (phase, reason = null) => { state.phase = phase; send({ type: "connection-state", runId: state.runId, profileId: "porsche-981-2014-dme", connection: {
      sequence: ++state.sequence, episode: 1, phase, attempt: state.attempt, connectionEpoch: state.epoch, reason,
      elapsedMs: state.sequence * 100, observedUtc: new Date().toISOString(), sourceKind: "standard-engine-read",
      simulation: false, executionEnabled: true, liveVerified: false, writePayload: null,
      ...(phase === "awaiting-retry" ? { linkReleased: true } : {}) } }); };
    const progress = () => send({ type: "progress", stage: "engine", runId: state.runId, profileId: "porsche-981-2014-dme",
      completed: state.engine.samples.length, total: 2, engine: state.engine });
    const sample = (cycle, epoch) => ({ pid: "0C", label: "转速", value: 700+cycle*100, unit: "rpm", capturedUtc: new Date().toISOString(),
      elapsedMs: cycle === 1 ? 100 : 70100, cycle, synthetic: false, connectionEpoch: epoch });
    state.finish = () => {
      if (state.phase === "awaiting-retry") connection("failed", "cancelled");
      send({ type: "result", ok: false, mode: "live", runId: state.runId, profileId: "porsche-981-2014-dme",
        status: "cancelled", error: "cancelled", sessionTask: "engine", results: [], engine: state.engine,
        simulation: false, liveVerified: false, writePayload: null, connectionEventCount: state.sequence,
        restoration: { adapterClose: "ATPC", errors: [] } });
      if (!state.deferClose) state.close();
    };
    state.close = () => { state.child.exitCode=1;state.child.emit("close",1); };
    const manager = createReadOnlySessionManager({ repoRoot: process.cwd(), env: {}, allowInjectedLive: true,
      getLiveDeviceId: () => "usb:"+"a".repeat(64), getLiveAdapterModel: () => "ELM327", getLiveSerialBaud: () => 38400,
      nowFn: () => state.clock, gracefulMs: 10000, killWatchMs: 10000,
      spawnFn: () => {
        const child = new EventEmitter();child.stdin=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();
        child.stdin.end=()=>{};child.kill=()=>{child.killed=true;};state.child=child;
        child.stdin.write = text => {
          const body=JSON.parse(text);
          if (body.action==="run") {
            state.requests.push(body);state.sequence=0;state.epoch=1;state.attempt=0;state.runId="standard_ui_run_"+state.requests.length;
            state.engine={ selectedPids:body.selectedPids,supportedPids:body.selectedPids,unsupportedPids:[],sampleCycles:body.sampleCycles,
              completedCycles:1,intervalMs:body.intervalMs,samples:[sample(1,1)] };
            queueMicrotask(()=>{progress();connection("interrupted","transport-disconnected");connection("awaiting-retry");});
          } else if (body.action==="retry") {
            state.commands.push(body);state.attempt++;connection("retrying");
            if (state.failRetry) { connection("retry-failed","transport-disconnected");connection("awaiting-retry"); }
            else { state.epoch++;connection("qualified");connection("recovered");state.engine.samples.push(sample(2,state.epoch));
              state.engine.completedCycles=2;progress(); }
          } else if (body.action==="cancel") { state.commands.push(body);state.finish(); }
          else throw Error("unexpected control");
          return true;
        };return child;
      }, saveEngineRecording: async recording => { state.saves.push(recording);return {ok:true,saved:true,filePath:"SYNTHETIC-standard.pcapng"}; } });
    state.failUnclosed = () => [...manager._jobs.values()].at(-1).failUnclosed("timeout");
    ipcMain.removeHandler("diagnostics:session");ipcMain.handle("diagnostics:session",(event,request)=>{
      if (request.action === "status") {
        state.statusCalls++;
        if (state.statusFailures > 0) { state.statusFailures--; throw new Error("injected-status-transport-error"); }
      }
      return manager.handle(request,{ownerId:event.sender.id});
    });
  }, path.join(installed || path.join(root,"apps/desktop"),installed ? "apps/desktop/electron/read-only-session.mjs":"electron/read-only-session.mjs"));
  // Current renderer requires fresh vehicle recognition before standard sampling.
  // Reuse the explicit Python vehicle model; no adapter or vehicle is contacted.
  const vehicleModel = JSON.parse(await fs.readFile(path.resolve(process.env.PORSCHE981_VEHICLE_MODELS || ".local/obd-ecm-uds-reader-20261016", "vehicle-source-981.json"), "utf8"));
  assert.equal(vehicleModel.syntheticPort, true);
  await app.evaluate(({ipcMain}, result) => {
    ipcMain.removeHandler("diagnostics:connection");
    ipcMain.handle("diagnostics:connection", () => ({ok:true, connected:true, linkState:"connected", purpose:"diagnostic", model:"ELM327", selectedDeviceId:"usb:"+"a".repeat(64), serialBaud:38400}));
    ipcMain.removeHandler("diagnostics:manufacturerStream");
    ipcMain.handle("diagnostics:manufacturerStream", (_event, request) => {
      if(request.action === "identify-vehicle") return {ok:true, sessionId:"standard-vehicle-model"};
      if(request.action === "status" && request.sessionId === "standard-vehicle-model") return {ok:true, state:"finished", released:true, events:[], cursor:0, result};
      return {ok:false, error:"unexpected-model-action"};
    });
  }, vehicleModel.frames.at(-1).result);
  await page.locator('nav.side [data-tab="obd"]').click();await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-system").selectOption("dme");
  assert.equal(await page.getByTestId("eng-selection-count").innerText(),"已选 0 项 · 最多 12 项");
  await page.getByTestId("eng-select-0C").check();await page.getByTestId("eng-mode").selectOption("live");
  assert.equal(await page.getByTestId("eng-x431").count(), 0);
  assert.equal(await page.getByTestId("eng-generation").count(), 0);
  assert.equal(await page.getByText("尚未开始", {exact:true}).count(), 0);
  await page.getByTestId("eng-readonly").check();
  assert.equal(await page.getByTestId("eng-mode").inputValue(), "live");
  for (const id of ["eng-continuous", "eng-cycles", "eng-interval", "eng-prepare", "eng-restart"]) assert.equal(await page.getByTestId(id).count(), 0);
  await page.getByTestId("eng-start").click();
  assert.equal(await page.getByTestId("eng-selection").getAttribute("open"), null);
  assert.equal(await page.getByTestId("eng-display-text").getAttribute("aria-pressed"), "true");
  await page.getByTestId("eng-display-both").click();
  const row=page.getByTestId("eng-pids").locator('[data-pid="0C"]');
  await page.getByTestId("eng-recovery").waitFor();assert.match(await row.innerText(),/800.*未更新/s);
  assert.equal(await app.evaluate(()=>globalThis.standardRecoveryAcceptance.commands.length),0);
  await page.locator('[data-obd-tab="topology"]').click();await page.locator('[data-obd-tab="live"]').click();
  await page.getByTestId("eng-recovery").waitFor();assert.match(await row.innerText(),/800/);
  checks.push("no preselection; manual interruption retains value/time and pending choice across navigation, without automatic retry");
  await page.getByTestId("eng-retry").click();
  await page.waitForFunction(()=>document.querySelector('[data-pid="0C"]')?.textContent?.includes("900"));
  assert.doesNotMatch(await row.innerText(),/未更新/);
  assert.equal(await page.locator('[data-curve-id="0C"] polyline').count(),1);
  assert.equal(await page.locator('[data-curve-id="0C"] circle').count(),1);
  assert.doesNotMatch(await row.innerText(), /上次更新/);
  const axis = await page.locator(".eng-chart > text").allTextContents();
  assert.deepEqual(axis.slice(0, 2), ["10.1 s", "70.1 s"]);
  assert.equal(await app.evaluate(()=>globalThis.standardRecoveryAcceptance.requests.length),1);
  checks.push("one explicit retry continues same worker/batch, shows fresh actual value and separates curve at connection epoch");
  await app.evaluate(()=>{globalThis.standardRecoveryAcceptance.deferClose=true;});
  await page.getByTestId("eng-cancel").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="eng-progress"]')?.textContent?.includes("等待诊断头释放"));
  assert.equal(await page.getByTestId("eng-export").isDisabled(),true);
  assert.equal(await page.getByTestId("eng-start").isDisabled(),true);
  assert.match(await row.innerText(),/900/);assert.equal(await page.getByTestId("eng-error").count(),0);
  await page.waitForTimeout(900);
  assert.equal(await page.getByTestId("eng-export").isDisabled(),true, "multiple polls cannot unlock before actual close");
  assert.equal(await app.evaluate(()=>globalThis.standardRecoveryAcceptance.commands.filter(r=>r.action==="cancel").length),1);
  await app.evaluate(()=>{globalThis.standardRecoveryAcceptance.close();globalThis.standardRecoveryAcceptance.deferClose=false;});
  await page.waitForFunction(()=>!document.querySelector('[data-testid="eng-export"]')?.disabled);
  assert.equal(await page.getByTestId("eng-error").count(),0);
  await page.getByTestId("eng-export").click();await page.getByTestId("eng-saved").waitFor();
  assert.equal(await app.evaluate(()=>globalThis.standardRecoveryAcceptance.saves[0].samples.length),2);
  assert.match(await row.innerText(),/900/);
  checks.push("manual terminate waits across polls for actual child release, blocks restart/save while waiting, reports no cancelled error and then saves all retained values");
  await app.evaluate(()=>{globalThis.standardRecoveryAcceptance.failRetry=true;});
  await page.getByTestId("eng-start").click();await page.getByTestId("eng-recovery").waitFor();await page.getByTestId("eng-retry").click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="eng-recovery"]')&&document.querySelector('[data-testid="eng-connection-state"]')?.textContent?.includes("等待"));
  await app.evaluate(()=>{globalThis.standardRecoveryAcceptance.clock=600001;});
  await page.waitForFunction(()=>document.querySelector('[data-testid="eng-progress"]')?.textContent?.includes("十分钟"));
  assert.equal(await page.getByTestId("eng-recovery").count(),0);assert.equal(await page.getByTestId("eng-export").isEnabled(),true);
  assert.equal(await app.evaluate(()=>globalThis.standardRecoveryAcceptance.requests.length),2);
  checks.push("failed explicit attempt returns to manual choice; original deadline ends waiting and keeps partial data, no automatic new batch");
  await app.evaluate(()=>{const s=globalThis.standardRecoveryAcceptance;s.failRetry=false;s.deferClose=true;s.clock=0;});
  await page.getByTestId("eng-start").click();await page.getByTestId("eng-recovery").waitFor();
  const beforeStatus = await app.evaluate(()=>{const s=globalThis.standardRecoveryAcceptance;s.statusFailures=2;return {calls:s.statusCalls,cancels:s.commands.filter(r=>r.action==="cancel").length};});
  await page.getByTestId("eng-error").waitFor();
  assert.equal(await page.getByTestId("eng-error-details").count(), 0);
  assert.doesNotMatch(await page.getByTestId("eng-error").innerText(),/injected-status-transport-error/);
  assert.equal(await page.getByTestId("eng-start").isDisabled(),true);assert.equal(await page.getByTestId("eng-export").isDisabled(),true);
  await page.waitForFunction(()=>document.querySelector('[data-testid="eng-progress"]')?.textContent?.includes("等待诊断头释放"));
  await page.waitForTimeout(1100);
  const afterStatus=await app.evaluate(()=>{const s=globalThis.standardRecoveryAcceptance;return {calls:s.statusCalls,cancels:s.commands.filter(r=>r.action==="cancel").length};});
  assert.ok(afterStatus.calls>=beforeStatus.calls+3);assert.equal(afterStatus.cancels,beforeStatus.cancels+1);
  await app.evaluate(()=>{const s=globalThis.standardRecoveryAcceptance;s.close();s.deferClose=false;});
  await page.waitForFunction(()=>!document.querySelector('[data-testid="eng-export"]')?.disabled);
  assert.equal(await page.getByTestId("eng-error").count(),0);
  checks.push("status IPC errors request cancel once, keep polling/locks/data through repeated failures and finish on verified close; no technical detail element");
  await app.evaluate(()=>{globalThis.standardRecoveryAcceptance.deferClose=true;});
  await page.getByTestId("eng-start").click();await page.getByTestId("eng-recovery").waitFor();
  await app.evaluate(()=>globalThis.standardRecoveryAcceptance.failUnclosed());
  await page.waitForFunction(()=>document.querySelector('[data-testid="eng-error"]')?.textContent?.includes("采集超时"));
  assert.equal(await page.getByTestId("eng-start").isDisabled(),true);assert.equal(await page.getByTestId("eng-export").isDisabled(),true);
  await page.waitForTimeout(900);
  assert.equal(await page.getByTestId("eng-export").isDisabled(),true, "reported timeout is not a closed worker");
  await app.evaluate(()=>globalThis.standardRecoveryAcceptance.close());
  await page.waitForFunction(()=>!document.querySelector('[data-testid="eng-export"]')?.disabled);
  assert.match(await page.getByTestId("eng-error").innerText(),/释放未通过核对/);
  assert.match(await row.innerText(),/800/);
  assert.equal(await page.locator('[data-pid="0C"]').count(),1);
  await page.getByTestId("eng-export").click();await page.getByTestId("eng-saved").waitFor();
  const partial=await app.evaluate(()=>{const r=globalThis.standardRecoveryAcceptance.saves.at(-1);return {count:r.samples.length,batchComplete:r.batchComplete,kind:r.runs[0].type,partial:r.runs[0].partial};});
  assert.deepEqual(partial,{count:1,batchComplete:false,kind:"progress",partial:true});
  assert.match(await page.getByTestId("eng-error").innerText(),/释放未通过核对/);
  checks.push("reported timeout before actual close retains locks; missing final retains actual progress once and permits explicit partial save after close, with failed physical cleanup visible");
  await page.screenshot({path:path.join(output,"standard-recovery.png"),fullPage:true});assert.deepEqual(errors,[]);passed=true;
} finally {
  clearTimeout(watchdog);await app?.close();
  await fs.writeFile(path.join(output,"result.json"),JSON.stringify({passed,checks,errors,injectedStandardWorker:true,fixtureOnly:true,
    installed:Boolean(installed),windowsExecuted:false,deviceIO:false,vehicleIO:false,goalComplete:false},null,2)+"\n");
}
console.log(JSON.stringify({passed,checks}));
