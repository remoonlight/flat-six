/** Production desktop session -> recorder -> PCAPNG; simulation only, no device IO. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-topology-pdu-ui-accept"));
await fs.mkdir(output, { recursive: true });
const scratch = await fs.mkdtemp(path.join(output, "ui-"));
const file = path.join(scratch, "synthetic-session-rx.pcapng");
const python = process.env.PORSCHE981_PYTHON || "python";
await fs.writeFile(path.join(scratch, "devices.json"), JSON.stringify({ devices: [
  { id: "vnci:10001", brand: "VNCI", name: "Synthetic VNCI", available: true, transport: "d-pdu-usb" },
  { id: "pt3g:20002", brand: "PT3G", name: "Synthetic PT3G", available: true, transport: "d-pdu-usb" },
], errors: [] }));
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1",
  PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_DB: path.join(scratch, "ui.db"),
  PORSCHE981_USER_DATA: path.join(scratch, "user-data"), PORSCHE981_LOCAL_ROOT: path.join(scratch, "local-assets"),
  PORSCHE981_CAN_CAPTURE_ROOT: path.join(scratch, "raw-captures"),
  PORSCHE981_CONNECTION_FIXTURE: path.join(scratch, "devices.json"), PORSCHE981_CONNECTION_STATE: path.join(scratch, "connection.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app; const errors = [], runs = [];
try {
  // A delivery contains the main module and built UI, without the workspace
  // package.json. On macOS this checks those files, not the Windows executable.
  const entry = process.env.PORSCHE981_ACCEPT_APP_ROOT ? path.join(root, "apps/desktop/electron/main.mjs") : path.join(root, "apps/desktop");
  app = await electron.launch({ args: [entry], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000); page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(({ dialog }, file) => {
    globalThis.__recordingChoosers = 0;
    dialog.showSaveDialog = async () => { globalThis.__recordingChoosers++; return { canceled: false, filePath: file }; };
  }, file);
  await page.locator('nav.side button[data-tab="obd"]').click();
  async function recording() { return page.evaluate(() => window.porsche981.diagnosticCanRecording({ action: "status" })); }
  async function run(profileId, scenario = "success", extra = {}) {
    const result = await page.evaluate(async ({ profileId, scenario, extra }) => {
      const start = await window.porsche981.readOnlySession({ action: "start", profileId, scenario, mode: "simulation", ...extra });
      if (!start.jobId) throw new Error(JSON.stringify(start));
      for (let i = 0; i < 600; i++) {
        const status = await window.porsche981.readOnlySession({ action: "status", jobId: start.jobId });
        if (["completed", "failed", "cancelled"].includes(status.state)) return status;
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      throw new Error("session did not terminate within 15 seconds");
    }, { profileId, scenario, extra });
    assert.ok(result.final); assert.equal(result.final.liveVerified, false);
    assert.ok(!JSON.stringify(result.events).includes("diagnostic-pdu"), "raw PDU must stay out of session history");
    return { profileId, scenario, runId: result.final.runId, ok: result.final.ok, sessionTask: extra.sessionTask || "read" };
  }
  assert.equal((await recording()).recording, null, "default recording off");
  const before = await run("porsche-981-2014-dme");
  assert.equal(before.ok, true); assert.equal((await recording()).recording, null);
  await assert.rejects(fs.stat(file), { code: "ENOENT" });
  for (const tab of ["parts", "maintenance", "obd", "settings", "locator"]) {
    await page.locator(`nav.side button[data-tab="${tab}"]`).click();
    assert.equal(await page.getByTestId("obd-recording-controls").count(), 0, `${tab}: raw recording element removed`);
    assert.equal(await page.getByText("原始记录", { exact: true }).count(), 0);
  }
  await page.locator('nav.side button[data-tab="obd"]').click();
  for (const tab of ["connection", "topology"]) {
    await page.locator(`[data-obd-tab="${tab}"]`).click();
    assert.equal(await page.getByTestId("obd-recording-controls").count(), 0, "global recording entry removed");
    assert.equal(await page.locator('[data-page="obd"] [data-testid="diagnostic-can-recording"]').count(), 0, "no restored connection/topology recording blocks");
    assert.equal(await page.getByText("需要保留完整诊断响应时，请连接后先开始记录，再进入系统拓扑自动读取。", { exact: true }).count(), 0);
  }
  const denied = await page.evaluate(() => window.porsche981.diagnosticCanRecording({ action: "start", simulation: false, layer: "application" }));
  assert.equal(denied.ok, false);
  assert.equal(await app.evaluate(() => globalThis.__recordingChoosers), 0, "unqualified source denied before native chooser");
  // The production build intentionally has no simulation topology buttons.
  // Use its real preload/IPC simulation API, with no injected transport worker.
  assert.equal((await page.evaluate(() => window.porsche981.diagnosticCanRecording({ action: "start", simulation: true, layer: "application" }))).ok, true);
  const start = await recording();
  assert.equal(start.ok, true); assert.equal(start.recording.active, true);
  const startedAt = start.recording.startedAt;
  runs.push(await run("porsche-981-2014-dme"));
  await page.locator('[data-obd-tab="coding"]').click();
  runs.push(await run("porsche-981-2014-gateway"));
  await page.reload();
  await page.locator('nav.side button[data-tab="parts"]').waitFor();
  assert.equal((await recording()).recording.simulation, true);
  assert.equal(await page.getByTestId("obd-recording-controls").count(), 0);
  await page.locator('nav.side button[data-tab="settings"]').click();
  runs.push(await run("porsche-981-2014-dme", "identity-mismatch"));
  await page.locator('nav.side button[data-tab="parts"]').click();
  runs.push(await run("porsche-981-2014-dme", "negative"));
  runs.push(await run("porsche-981-2014-dme", "success", { sessionTask: "engine", sampleCycles: 1, selectedPids: ["0C", "0D"] }));
  assert.deepEqual(runs.map(row => row.ok), [true, true, false, false, true]);
  assert.equal((await recording()).recording.startedAt, startedAt, "page changes and renderer reload never restart the recorder");
  assert.equal(await app.evaluate(() => globalThis.__recordingChoosers), 1);
  await page.locator('nav.side button[data-tab="settings"]').click();
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  assert.equal(await page.getByTestId("obd-recording-controls").count(), 0);
  await page.screenshot({ path: path.join(scratch, "settings-without-recording.png") });
  assert.equal((await page.evaluate(() => window.porsche981.diagnosticCanRecording({ action: "stop" }))).ok, true);
  const stopped = await recording(); assert.equal(stopped.recording.active, false); assert.equal(stopped.recording.reason, "user-stopped");
  const bytes = await fs.readFile(file);
  const after = await run("porsche-981-2014-gateway"); assert.equal(after.ok, true);
  assert.deepEqual(await fs.readFile(file), bytes, "no appends after explicit stop");
  await fs.writeFile(path.join(scratch, "runs.json"), JSON.stringify({ before, runs, after }));
  const checked = JSON.parse(execFileSync(python, ["-B", "-c", String.raw`
import collections,json,pathlib,sys
from scripts.diagnostics.coding_capture import scan_capture
from scripts.diagnostics.session_simulator import catalog_success_map
from scripts.diagnostics.sessions import run_session
from tempfile import TemporaryDirectory
file,runs=sys.argv[1:];runs=json.loads(pathlib.Path(runs).read_text());rows=[]
summary=scan_capture(file,rows.append)
assert summary['prefixComplete'] and summary['packetErrors']==0 and summary['endStatisticsPresent'],summary
by_run=collections.defaultdict(list)
for row in rows:
 assert row['synthetic'] is True and row['context']['simulation'] is True
 assert row['context']['transportKind']=='elm-iso-tp'
 by_run[row['context']['runId']].append(row)
assert set(by_run)=={r['runId'] for r in runs['runs']}
assert runs['before']['runId'] not in by_run and runs['after']['runId'] not in by_run
counts=[]
for run in runs['runs']:
 observed=by_run[run['runId']];events=[]
 with TemporaryDirectory() as temp:
  result=run_session(profile_id=run['profileId'],mode='simulation',scenario=run['scenario'],
   session_task=run['sessionTask'],retain_artifacts=False,artifact_root=pathlib.Path(temp),progress_sink=events.append,
   **({'sample_cycles':1,'selected_pids':['0C','0D']} if run['sessionTask']=='engine' else {}))
 expected=[e['pdu'] for e in events if e['type']=='diagnostic-pdu']
 assert [(r['context']['requestHex'],r['pduHex']) for r in observed]==[(p['requestHex'],p['pduHex']) for p in expected]
 assert all(r['context']['targetProfileId']==run['profileId'] for r in observed),'no source-profile alias'
 assert all(r['context']['startedTimestampUs']<=r['timestampUs'] for r in observed)
 counts.append({'runId':run['runId'],'profileId':run['profileId'],'scenario':run['scenario'],'packets':len(observed)})
print(json.dumps({'ok':True,'capture':summary,'runs':counts,'runtimeTargetIdsPreserved':True,'sourceIdentityPromoted':False}))
`, file, path.join(scratch, "runs.json")], { cwd: root, env, encoding: "utf8", timeout: 30000 }));
  assert.equal(checked.capture.packets, stopped.recording.frameCount); assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(scratch, "global-recording-settings.png"), fullPage: true });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.screenshot({ path: path.join(scratch, "topology-recording.png"), fullPage: true });
  await page.locator('[data-obd-tab="connection"]').click();
  await page.screenshot({ path: path.join(scratch, "connection-recording.png"), fullPage: true });
  for (const [id, unfinished] of [["vnci:10001", false], ["pt3g:20002", true]]) {
    await page.getByTestId(`obd-device-${id}`).check();
    await page.waitForFunction(id => document.querySelector(`input[data-testid="obd-device-${id}"]`)?.checked, id);
    await page.locator('[data-obd-tab="live"]').click();
    await page.getByTestId("eng-system").selectOption("dme");
    await page.getByTestId("eng-data-source").selectOption("standard");
    const labels = await page.getByTestId("eng-parameters").locator("label span").allTextContents();
    assert.equal(labels.length, 6);
    assert.ok(labels.every(label => label.endsWith("(未完成)") === unfinished), JSON.stringify({ id, labels }));
    assert.ok(labels.every(label => !label.includes("完成未测试")), "vehicle status stays out of menu");
    await page.locator('[data-obd-tab="connection"]').click();
  }
  assert.equal((await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }))).connected, false,
    "display checks select synthetic heads without opening them");
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(scratch, "verification.json"), JSON.stringify({ ...checked, defaultOff: true,
    explicitWindow: true, connectionAndTopologyControlsRemoved: true, allFiveRecordingElementsRemoved: true,
    recordingControlViaTestIpc: true, rendererReloadKeepsRecording: true, stoppedBytesUnchanged: true, noRendererPdu: true,
    vnciStandardReadImplementedLabel: true, pt3gStandardReadUnfinishedLabel: true,
    syntheticOnly: true, deviceIO: false, vehicleIO: false, piwisAccess: false, laptopVerified: false }, null, 2));
  console.log(`PASS production Electron/preload/IPC/Python full session capture: ${checked.capture.packets} synthetic PDUs; raw recording elements absent on all five pages; retained backend recording tested via IPC across pages/reload, target IDs unchanged`);
} finally {
  if (app) {
    const closed = app.waitForEvent("close", { timeout: 10000 }).then(() => true, () => false);
    await app.evaluate(({ app }) => app.quit()).catch(() => {});
    if (!await closed) await app.close();
  }
}
