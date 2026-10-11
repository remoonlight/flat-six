/** Production Electron/preload/IPC/Python identity comparison; synthetic only. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createDiagnosticPduWriter } from "../apps/desktop/electron/diagnostic-pdu-data.mjs";

const root = path.resolve(import.meta.dirname, "..");
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-identity-capture-ui-accept"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "ui-")), library = path.join(temp, "library");
const python = process.env.PORSCHE981_PYTHON || "python";
const bundle = process.env.PORSCHE981_IDENTITY_SOURCE_BUNDLE || path.join(root, ".local/obd-coding-readiness-20261009/definitions-live-current.zip");
const originalArchive = path.join(root, ".local/obd-identity-application-20261010/evidence-and-rollback.zip");
const fixture = JSON.parse(execFileSync(python, ["-B", "-c", String.raw`
import hashlib,json,pathlib,sys,zipfile
from scripts.diagnostics.coding_capture import scan_capture
bundle,library,original,out=sys.argv[1:]
with open(bundle,'rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='bc8a964e8fc8595aad8c74d4f95f36f19c038b3e7e910f4fea4031def70b8296'
with zipfile.ZipFile(bundle) as z:
 manifests=[n for n in z.namelist() if n.startswith('.local/diagnostics/x431-source-references/') and n.endswith('/manifest.json')];assert len(manifests)==1
 prefix=manifests[0].removesuffix('manifest.json');catalog=prefix.rstrip('/').split('/')[-1]
 raw=z.read(prefix+'manifest.json');assert hashlib.sha256(raw).hexdigest()==catalog;m=json.loads(raw)
 for row in [{'name':'manifest.json','bytes':len(raw),'sha256':catalog}]+m['files']:
  b=z.read(prefix+row['name']);assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256']
  p=pathlib.Path(library)/prefix/row['name'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
 language='.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP';b=z.read(language)
 assert hashlib.sha256(b).hexdigest()=='7caf1c27e475511e4a75db8fe92008684f5632eeb9fbb4484cbc16fa0eec7de3'
 p=pathlib.Path(library)/language;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
with open(original,'rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='528235bb6e9ebb3e0a30f3a0881255e5c038003cca2958d90d6a9364db3c7f28'
with zipfile.ZipFile(original) as z:
 index=json.loads(z.read('index.json'));row=next(r for r in index['aliases'] if r['path']=='captured-topology.pcapng')
 raw=z.read('objects/'+row['sha256']);assert hashlib.sha256(raw).hexdigest()==row['sha256'];pathlib.Path(out).write_bytes(raw)
rows=[];r=scan_capture(out,rows.append);assert r['prefixComplete'] and len(rows)==16 and not r['packetErrors']
assert all(x['synthetic'] for x in rows);print(json.dumps(dict(records=rows,catalog=catalog)))
`, bundle, library, originalArchive, path.join(temp, "original-synthetic.pcapng")], { cwd: root, encoding: "utf8", timeout: 45000 }));
const { records, catalog } = fixture;
const capture = path.join(temp, "synthetic-identity.pcapng");
const targets = ["porsche-981-2014-dme", "porsche-981-2014-gateway"], runs = targets.map(() => "ui-identity-" + randomUUID());
const writer = createDiagnosticPduWriter(capture, true);
const repeated = records.find(r => r.context.targetProfileId === targets[0] && r.context.requestHex === "1A9F");
assert.ok(repeated);
for (const [i, r] of [...records, ...Array(24).fill(repeated)].entries()) {
  const n = targets.indexOf(r.context.targetProfileId); assert.ok(n >= 0);
  writer.append({ ...r.context, pduHex: r.pduHex, timestampUs: 1000000 + i, startedTimestampUs: 1000000 + i },
    { targetProfileId: targets[n], runId: runs[n], simulation: true });
}
writer.finish("SYNTHETIC identity UI fixture; no device IO");
const hash = async file => createHash("sha256").update(await fs.readFile(file)).digest("hex");
const captureHash = await hash(capture), comparisons = [], workspaces = new Set(), errors = [];
await fs.writeFile(path.join(temp, "devices.json"), JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DEFINITION_ROOT: library, PORSCHE981_DB: path.join(temp, "ui.db"),
  PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"), PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app;
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000); page.on("pageerror", e => errors.push(e.message));
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="topology"]').click();
  const selection = page.getByTestId("identity-capture-selection"); await selection.locator("summary").click();
  assert.equal(await selection.getByLabel("故障核对资料版本").count(), 0, "identity comparison never selects one source version");
  const panel = page.getByTestId("identity-capture-panel"), result = page.getByTestId("identity-capture-result");
  for (const [i, ecu, candidates, fields, packets] of [[0, "1", 44, 934, 31], [1, "9", 19, 439, 9]]) {
    await selection.getByLabel("身份核对控制单元").selectOption(ecu);
    await app.evaluate(({ dialog }, file) => {
      let started; globalThis.__identityOpenStarted = new Promise(resolve => { started = resolve; });
      dialog.showOpenDialog = () => { started(); return new Promise(resolve => { globalThis.__identityFinishOpen = () => resolve({ canceled: false, filePaths: [file] }); }); };
    }, capture);
    await panel.getByRole("button", { name: "选择应用响应捕获", exact: true }).click();
    await app.evaluate(async () => globalThis.__identityOpenStarted);
    const gate = await page.evaluate(async () => Promise.all([
      window.porsche981.obdConnection({ action: "connect" }), window.porsche981.readOnlySession({ action: "start" }),
      window.porsche981.canCapture({ action: "start" }), window.porsche981.diagnosticCanRecording({ action: "start" }),
      window.porsche981.manufacturerStream({ action: "start" }), window.porsche981.diagnosticDefinitionBundle({ action: "export" }),
    ]));
    assert.deepEqual(gate.map(d => d.error), ["capture-busy", "capture-busy", "capture-busy", "capture-busy", "capture-busy", "bundle-busy-disconnect-first"]);
    await app.evaluate(() => globalThis.__identityFinishOpen());
    const target = panel.getByLabel("身份响应捕获目标"), sessions = panel.getByLabel("身份响应采集会话");
    await target.waitFor(); assert.equal(await target.inputValue(), ""); assert.equal(await sessions.isDisabled(), true);
    assert.equal(await target.locator(`option[value="${targets[1 - i]}"]`).evaluate(o => o.disabled), true);
    await target.selectOption(targets[i]); await sessions.selectOption(runs[i]);
    await panel.getByRole("button", { name: "离线核对本次响应", exact: true }).click();
    await result.waitFor();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="identity-capture-result"] ol > li').length === 20);
    assert.match(await result.innerText(), new RegExp(`${fields} 个来源字段`));
    assert.match(await result.innerText(), new RegExp(`${candidates} 个候选版本；实际观察独立解码一致 0 条`));
    assert.match(await result.innerText(), /模拟 · /);
    await result.getByRole("button", { name: "继续查看后续响应", exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="identity-capture-result"] ol > li').length === 40);
    await result.getByLabel("身份核对内容").selectOption("responses");
    await page.waitForFunction(n => document.querySelectorAll('[data-testid="identity-capture-result"] ol > li').length === n, Math.min(20, packets));
    if (packets > 20) {
      await result.getByRole("button", { name: "继续查看后续响应", exact: true }).click();
      await page.waitForFunction(n => document.querySelectorAll('[data-testid="identity-capture-result"] ol > li').length === n, packets);
    }
    const saved = path.join(temp, `identity-${ecu}-full.zip`);
    await app.evaluate(({ dialog }, files) => {
      let saves = 0;
      dialog.showSaveDialog = async () => !saves++ ? { canceled: true } : { canceled: false, filePath: saves === 2 ? files.capture : files.saved };
    }, { capture, saved });
    const save = result.getByRole("button", { name: "保存完整核对结果 ZIP", exact: true });
    await save.click(); await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === "保存完整核对结果 ZIP")?.disabled);
    await assert.rejects(fs.stat(saved), { code: "ENOENT" });
    await save.click(); await panel.getByRole("alert").filter({ hasText: "保存位置与原始捕获是同一文件" }).waitFor();
    assert.equal(await hash(capture), captureHash);
    await save.click(); await result.getByRole("status").filter({ hasText: saved }).waitFor();
    const checked = JSON.parse(execFileSync(python, ["-B", "-c", String.raw`
import json,sys,zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
 assert set(z.namelist())=={'report.json','responses.jsonl','identity-values.jsonl','identity-profiles.json'}
 r=json.loads(z.read('report.json'));raw=[json.loads(l) for l in z.read('responses.jsonl').splitlines()];values=[json.loads(l) for l in z.read('identity-values.jsonl').splitlines()]
 profiles=json.loads(z.read('identity-profiles.json'))['profiles']
 assert len(profiles)==r['candidateVersionCount'] and len(raw)==r['matchedPackets']
 assert all(v['synthetic'] and v['runId']==r['runId'] and v['captureTargetProfileId']==r['captureTargetProfileId'] for v in values)
 assert all(not p['observedFieldsWithAgreement'] for p in r['profiles'])
 assert r['versionRuleComparison']['captureIntegrityComplete']
 assert all(not c['calculationComplete'] for c in r['versionRuleComparison']['reports'] if c['source']=='observed')
 assert all(c['selectedVehicleVariant'] is None and not c['ecuVersionQualified'] for c in r['versionRuleComparison']['reports'])
 if r['ecuId']==1:assert any(c['calculationComplete'] and c['matchingCandidateCount']==1 for c in r['versionRuleComparison']['reports'] if c['source']=='synthetic')
 assert r['selectedVehicleVariant'] is None and not r['completeIdentityQualified'] and not r['ecuVersionQualified']
 assert all(not p['ecuVersionQualified'] for p in profiles)
 print(json.dumps({'report':r,'responses':len(raw),'values':len(values),'sourceFields':sum(p['sourceFieldCount'] for p in r['profiles'])}))
`, saved], { encoding: "utf8", timeout: 15000 }));
    assert.equal(checked.responses, packets); assert.equal(checked.report.candidateVersionCount, candidates);
    assert.equal(checked.sourceFields, fields); assert.ok(checked.values > 40); assert.equal(checked.report.capture.sha256, captureHash);
    comparisons.push({ ...checked, saved });
    await result.getByTestId("identity-version-rules").locator("summary").click();
    assert.ok(await result.getByTestId("identity-version-rules").getByText("模拟响应", { exact: false }).count());
    await page.screenshot({ path: path.join(temp, `identity-${ecu}-result.png`), fullPage: true });
    for (const name of await fs.readdir(os.tmpdir())) {
      if (!name.startsWith("flat-six-identity-capture-")) continue;
      const folder = path.join(os.tmpdir(), name);
      const r = await fs.readFile(path.join(folder, "result/report.json"), "utf8").then(JSON.parse).catch(() => null);
      if (r?.runId === runs[i]) workspaces.add(folder);
    }
    await target.selectOption(""); await result.waitFor({ state: "detached" });
    assert.equal(await sessions.inputValue(), "");
  }
  await selection.getByLabel("身份核对车型").selectOption("982");
  await selection.getByLabel("身份核对控制单元").selectOption("9");
  await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, capture);
  await panel.getByRole("button", { name: "选择应用响应捕获", exact: true }).click();
  const target = panel.getByLabel("身份响应捕获目标");
  await target.waitFor();
  for (const id of targets) assert.equal(await target.locator(`option[value="${id}"]`).evaluate(o => o.disabled), true, "981 runtime capture cannot become 982 identity");
  assert.equal(await result.count(), 0);
  assert.deepEqual(errors, []); assert.equal(await hash(capture), captureHash);
  await fs.writeFile(path.join(temp, "verification.json"), JSON.stringify({ ok: true, captureHash, comparisons,
    sourceCatalogId: catalog, workspaces: [...workspaces], errors, syntheticOnly: true, deviceIO: false, windowsLaptopExecuted: false }, null, 2) + "\n");
  console.log(`PASS identity desktop: full candidate sets, values/responses paging, complete ZIP, target/run separation, source protection, gates and 982 rejection (${temp})`);
} finally {
  if (app) await app.close();
  for (const folder of workspaces) await assert.rejects(fs.stat(folder), { code: "ENOENT" });
}
