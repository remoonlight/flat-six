/** Actual Electron/preload/IPC/Python DTC review. Synthetic inputs only. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { randomUUID, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createDiagnosticPduWriter } from "../apps/desktop/electron/diagnostic-pdu-data.mjs";

const root = path.resolve(import.meta.dirname, "..");
const scratch = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-dtc-capture-ui-accept"));
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(scratch, "ui-")), library = path.join(temp, "library");
const catalog = "f6ca04ff9572ba77feba428c3eb05d4f589369d2d31662d03da171e3b12a0896";
const gatewayProfileId = "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1";
const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", runId = "ui-dtc-" + randomUUID();
const bundle = process.env.PORSCHE981_DTC_SOURCE_BUNDLE || path.join(root, ".local/obd-native-pdu-context-20261010/definitions-live-current.zip");
const python = process.env.PORSCHE981_PYTHON || "python";
const missing = JSON.parse(execFileSync(python, ["-B", "-c", String.raw`
import hashlib,json,pathlib,sys,zipfile
bundle,library,catalog,pid=sys.argv[1:];prefix='.local/diagnostics/x431-source-references/'+catalog+'/'
assert hashlib.sha256(pathlib.Path(bundle).read_bytes()).hexdigest()=='beed9c6c7c3b7fd66d9c0e754aca03818af8d1e72e7bf3a645fa29201f09bbb2'
with zipfile.ZipFile(bundle) as z:
 m=json.loads(z.read(prefix+'manifest.json'));assert hashlib.sha256(z.read(prefix+'manifest.json')).hexdigest()==catalog
 pins={r['name']:r for r in m['files']}
 def get(name):
  b=z.read(prefix+name);assert len(b)==pins[name]['bytes'] and hashlib.sha256(b).hexdigest()==pins[name]['sha256'];return b
 index=json.loads(get('all-units.json'));chosen=None;names=['manifest.json','all-units.json']
 for u in index['units']:
  if u['generation']!='981':continue
  for r in u['variants']:
   if r['profileId'] in (pid,'9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1'):names.append(r['file'])
   if chosen is None:
    p=json.loads(get(r['file']));refs=[s for s in p['diagnosticSources'] if s['requestedEcuId']==u['ecuId'] and s['variantName']==p['variant']]
    if len(refs)==1 and not refs[0]['links']:chosen={'ecuId':u['ecuId'],'profileId':p['profileId']};names.append(r['file'])
 assert chosen
 for name in set(names):
  b=z.read(prefix+name) if name=='manifest.json' else get(name);out=pathlib.Path(library)/prefix/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(b)
 print(json.dumps(chosen))
`, bundle, library, catalog, profileId], { encoding: "utf8", timeout: 45000 }));
const capture = path.join(temp, "synthetic-dtc.pcapng"), saved = path.join(temp, "full-result.zip");
const writer = createDiagnosticPduWriter(capture, true);
const pdus = [...Array(24).fill("5802C44728C41221"), "5800", "7F1831", "7F1878", "5802C44728", "5801C44728", "5801C44728"];
for (const [i, pduHex] of pdus.entries()) writer.append({ requestHex: "1800FF00", pduHex, timestampUs: 1000000 + i,
  timestampSource: "host-pdu-observed", transportKind: "d-pdu-iso-tp", observation: "native-result-data",
  duringCleanup: false, uniqueId: 1, flagsHex: "00000000", primitive: i === 28 ? 88 : 1,
  ...(i === 29 ? {} : { expectedPrimitive: 1 }) }, { runId, targetProfileId: profileId, simulation: true });
writer.finish("SYNTHETIC DTC UI fixture only; no device IO");
const hash = async file => createHash("sha256").update(await fs.readFile(file)).digest("hex");
const originalHash = await hash(capture);
await fs.writeFile(path.join(temp, "devices.json"), JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DEFINITION_ROOT: library, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"),
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, workspace; const errors = [];
try {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ dialog }, files) => {
    let started; globalThis.__dtcOpenStarted = new Promise(resolve => { started = resolve; });
    dialog.showOpenDialog = () => { started(); return new Promise(resolve => { globalThis.__dtcFinishOpen = () => resolve({ canceled: false, filePaths: [files.capture] }); }); };
    let saves = 0; dialog.showSaveDialog = async () => !saves++ ? { canceled: true } : { canceled: false, filePath: saves === 2 ? files.capture : files.saved };
  }, { capture, saved });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="topology"]').click();
  const selection = page.getByTestId("dtc-capture-selection"); await selection.locator("summary").click();
  await selection.getByLabel("故障核对控制单元").selectOption("1");
  await selection.getByLabel("故障核对资料版本").selectOption(profileId);
  const panel = page.getByTestId("dtc-capture-panel"), result = page.getByTestId("dtc-capture-result");
  const open = panel.getByRole("button", { name: "选择应用响应捕获", exact: true });
  assert.equal(await open.isDisabled(), true, "source link must be selected explicitly");
  await panel.getByLabel("故障核对诊断链接").selectOption("0");
  await open.click(); await app.evaluate(async () => globalThis.__dtcOpenStarted);
  const gated = await page.evaluate(async () => Promise.all([
    window.porsche981.obdConnection({ action: "connect" }), window.porsche981.readOnlySession({ action: "start" }),
    window.porsche981.canCapture({ action: "start" }), window.porsche981.diagnosticCanRecording({ action: "start" }),
    window.porsche981.manufacturerStream({ action: "start" }), window.porsche981.diagnosticDefinitionBundle({ action: "export" }),
  ]));
  assert.deepEqual(gated.map(d => d.error), ["capture-busy", "capture-busy", "capture-busy", "capture-busy", "capture-busy", "bundle-busy-disconnect-first"]);
  await app.evaluate(() => globalThis.__dtcFinishOpen());
  await panel.getByLabel("故障响应捕获目标").selectOption(profileId);
  await panel.getByLabel("故障响应采集会话").selectOption(runId);
  await panel.getByRole("button", { name: "离线核对本次响应", exact: true }).click();
  await result.waitFor();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="dtc-capture-result"] ol > li').length === 20);
  assert.match(await result.innerText(), /495 条来源故障定义 · 1 组读取 · 缺少 1 组/);
  assert.match(await result.innerText(), /实际观察故障记录 0 条；模拟故障记录 48 条/);
  assert.match(await result.innerText(), /响应来自其他通信句柄 1/);
  assert.match(await result.innerText(), /旧捕获缺少当次请求通信句柄 1/);
  await page.screenshot({ path: path.join(temp, "dtc-faults.png"), fullPage: true });
  for (const name of await fs.readdir(os.tmpdir())) {
    if (!name.startsWith("flat-six-dtc-capture-")) continue;
    const dir = path.join(os.tmpdir(), name);
    const report = await fs.readFile(path.join(dir, "result/report.json"), "utf8").then(JSON.parse).catch(() => null);
    if (report?.runId === runId) { assert.equal(workspace, undefined); workspace = dir; }
  }
  assert.ok(workspace);
  const save = result.getByRole("button", { name: "保存完整核对结果 ZIP", exact: true });
  await save.click(); await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === "保存完整核对结果 ZIP")?.disabled);
  await assert.rejects(fs.stat(saved), { code: "ENOENT" });
  await save.click(); await panel.getByRole("alert").filter({ hasText: "保存位置与原始捕获是同一文件" }).waitFor();
  assert.equal(await hash(capture), originalHash);
  await save.click(); await result.getByRole("status").waitFor();
  const inspected = JSON.parse(execFileSync(python, ["-B", "-c", "import json,sys,zipfile;z=zipfile.ZipFile(sys.argv[1]);print(json.dumps({'names':z.namelist(),'report':json.loads(z.read('report.json')),'responses':len(z.read('responses.jsonl').splitlines()),'faults':len(z.read('faults.jsonl').splitlines()),'dictionary':len(json.loads(z.read('dictionary.json'))['records'])}))", saved], { encoding: "utf8" }));
  assert.deepEqual(inspected.names.sort(), ["dictionary.json", "faults.jsonl", "report.json", "responses.jsonl"]);
  assert.equal(inspected.responses, 30); assert.equal(inspected.faults, 48); assert.equal(inspected.dictionary, 495);
  for (const flag of ["identityQualified", "executionEnabled", "baselineCreated", "vehicleValuesQualified"]) assert.equal(inspected.report[flag], false);
  if (process.env.PORSCHE981_DTC_ACCEPT_FAIL_AFTER_EXPORT === "1") throw new Error("dtc-accept-injected-after-export");
  await result.getByRole("button", { name: "继续查看后续响应", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="dtc-capture-result"] ol > li').length === 40);
  assert.equal(await result.locator("ol > li").count(), 40);
  await result.getByLabel("故障核对内容").selectOption("responses");
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="dtc-capture-result"] ol > li').length === 20);
  await result.getByRole("button", { name: "继续查看后续响应", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="dtc-capture-result"] ol > li').length === 30);
  assert.equal(await result.locator("ol > li").count(), 30); assert.match(await result.innerText(), /包含 0 条故障记录/);
  await page.screenshot({ path: path.join(temp, "dtc-responses.png"), fullPage: true });
  await selection.getByLabel("故障核对资料版本").selectOption("");
  for (let i = 0; i < 100 && await fs.stat(workspace).then(() => true).catch(() => false); i++) await new Promise(resolve => setTimeout(resolve, 50));
  await assert.rejects(fs.stat(workspace), { code: "ENOENT" });
  // Produce a fresh capture through the production Node/Python session path,
  // then review both actual runtime target IDs with explicitly selected sources.
  const topologyCapture = path.join(temp, "synthetic-topology.pcapng");
  await app.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, topologyCapture);
  const recordedRuns = await page.evaluate(async () => {
    const api = window.porsche981;
    const started = await api.diagnosticCanRecording({ action: "start", simulation: true, layer: "application" });
    if (!started.ok) throw new Error(JSON.stringify(started));
    const runs = [];
    for (const profileId of ["porsche-981-2014-dme", "porsche-981-2014-gateway"]) {
      const start = await api.readOnlySession({ action: "start", profileId, mode: "simulation", scenario: "success" });
      if (!start.jobId) throw new Error(JSON.stringify(start));
      let done;
      for (let i = 0; i < 600; i++) {
        const result = await api.readOnlySession({ action: "status", jobId: start.jobId });
        if (["completed", "failed", "cancelled"].includes(result.state)) { done = result; break; }
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      if (!done?.final?.ok) throw new Error(JSON.stringify(done));
      runs.push({ profileId, runId: done.final.runId });
    }
    const stopped = await api.diagnosticCanRecording({ action: "stop" });
    if (stopped.recording?.frameCount !== 16) throw new Error(JSON.stringify(stopped));
    return runs;
  });
  const topologyHash = await hash(topologyCapture), comparisons = [];
  for (const [i, sourceId, ecuId, expectedResponses, expectedFaults, expectedDictionary] of [
    [0, profileId, "1", 7, 2, 495], [1, gatewayProfileId, "9", 9, 1, 170],
  ]) {
    await selection.getByLabel("故障核对控制单元").selectOption(ecuId);
    await selection.getByLabel("故障核对资料版本").selectOption(sourceId);
    await panel.getByLabel("故障核对诊断链接").selectOption("0");
    await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, topologyCapture);
    await open.click();
    const target = panel.getByLabel("故障响应捕获目标"), sessions = panel.getByLabel("故障响应采集会话");
    assert.equal(await target.inputValue(), "", "captured target requires explicit selection");
    assert.equal(await sessions.isDisabled(), true);
    const other = recordedRuns[1 - i].profileId;
    assert.equal(await target.locator(`option[value="${other}"]`).evaluate(option => option.disabled), true, "cross ECU target blocked");
    await target.selectOption(recordedRuns[i].profileId);
    assert.equal(await sessions.locator("option").count(), 2, "only the selected target session");
    await sessions.selectOption(recordedRuns[i].runId);
    await panel.getByRole("button", { name: "离线核对本次响应", exact: true }).click();
    await result.waitFor();
    assert.match(await page.getByTestId("dtc-comparison-context").innerText(), new RegExp(recordedRuns[i].profileId));
    assert.ok((await page.getByTestId("dtc-comparison-context").innerText()).includes(sourceId));
    assert.match(await result.innerText(), new RegExp(`模拟故障记录 ${expectedFaults} 条`));
    const full = path.join(temp, `topology-${ecuId}-full.zip`);
    await app.evaluate(({ dialog }, file) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath: file }); }, full);
    await result.getByRole("button", { name: "保存完整核对结果 ZIP", exact: true }).click();
    await result.getByRole("status").filter({ hasText: full }).waitFor();
    const checked = JSON.parse(execFileSync(python, ["-B", "-c", String.raw`
import json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
 r=json.loads(z.read('report.json'));responses=[json.loads(l) for l in z.read('responses.jsonl').splitlines()];faults=[json.loads(l) for l in z.read('faults.jsonl').splitlines()]
 assert all(x['context']['targetProfileId']==r['captureTargetProfileId'] and x['reviewContext']['sourceProfileId']==r['sourceProfileId'] for x in responses)
 assert all(x['captureTargetProfileId']==r['captureTargetProfileId'] and x['sourceProfileId']==r['sourceProfileId'] and x['runId']==r['runId'] for x in faults)
 assert not r['identityQualified'] and not r['reviewContext']['sourceSelectionIsVersionProof'] and not r['ecuVersionQualified']
 assert r['reviewContext']['association']=='explicit-named-runtime-comparison'
 print(json.dumps({'report':r,'responses':len(responses),'faults':len(faults),'dictionary':len(json.loads(z.read('dictionary.json'))['records'])}))
`, full], { encoding: "utf8", timeout: 15000 }));
    assert.equal(checked.responses, expectedResponses); assert.equal(checked.faults, expectedFaults); assert.equal(checked.dictionary, expectedDictionary);
    assert.equal(checked.report.captureTargetProfileId, recordedRuns[i].profileId); assert.equal(checked.report.sourceProfileId, sourceId);
    assert.equal(checked.report.capture.sha256, topologyHash); assert.equal(checked.report.observedRequestCount, 0);
    comparisons.push(checked);
    await page.screenshot({ path: path.join(temp, `topology-${ecuId}-comparison.png`), fullPage: true });
    await selection.getByLabel("故障核对资料版本").selectOption("");
  }
  assert.equal(await hash(topologyCapture), topologyHash, "raw target metadata untouched");
  await selection.getByLabel("故障核对控制单元").selectOption(String(missing.ecuId));
  await selection.getByLabel("故障核对资料版本").selectOption(missing.profileId);
  await panel.getByText("此版本没有可核对的来源诊断链接", { exact: false }).waitFor();
  assert.equal(await open.isDisabled(), true);
  await selection.getByLabel("故障核对车型").selectOption("982");
  await selection.getByLabel("故障核对控制单元").selectOption("1");
  const ids = await selection.getByLabel("故障核对资料版本").locator("option").evaluateAll(rows => rows.map(r => r.value).filter(Boolean));
  assert.ok(ids.length > 0); assert.ok(!ids.includes(profileId));
  assert.deepEqual(errors, []); assert.equal(await hash(capture), originalHash);
  await fs.writeFile(path.join(temp, "verification.json"), JSON.stringify({ ok: true, runId, initiallyDisplayed: 20, fullResult: inspected,
    sourceOverwritePrevented: true, mainIpcMutualExclusion: true, cleanupVerified: true, explicitSourceLink: true,
    explicitCapturedTargets: true, topologyComparisons: comparisons, topologyCaptureSha256: topologyHash, topologyPdus: 16,
    missingLinkBlocked: missing, bothGenerationViews: true, captureSha256: originalHash, noDeviceIO: true, laptopVerified: false }, null, 2));
  console.log("PASS actual DTC UI/preload/IPC/Python: source catalog/ECU/version/link, both full streams, complete dictionary export, invalid native contexts, source protection, shared gate and cleanup; synthetic only");
} finally {
  if (app) {
    // Let main's actual-close cleanup finish even when a UI assertion fails.
    const closed = app.waitForEvent("close", { timeout: 10000 }).then(() => true, () => false);
    await app.evaluate(({ app }) => app.quit()).catch(() => {});
    if (!await closed) await app.close();
  }
}
