/** Actual Electron/preload/IPC/Python with a synthetic PCAPNG; no device or car. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { randomUUID, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createDiagnosticPduWriter } from "../apps/desktop/electron/diagnostic-pdu-data.mjs";

const root = path.resolve(import.meta.dirname, "..");
const scratch = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-coding-capture-ui-accept"));
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(scratch, "ui-"));
const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", runId = "ui-coding-" + randomUUID();
const captureTarget = "porsche-981-2014-dme", library = path.join(temp, "library");
const capture = path.join(temp, "synthetic-responses.pcapng"), saved = path.join(temp, "explicit-full-result.zip");
const writer = createDiagnosticPduWriter(capture, true);
for (let i = 0; i < 24; i++) writer.append({ requestHex: "21A4", pduHex: "61A4000001", timestampUs: 1000000 + i,
  timestampSource: "host-pdu-observed", transportKind: "d-pdu-iso-tp", observation: "native-result-data",
  duringCleanup: false, primitive: 1, expectedPrimitive: 1, uniqueId: 1, flagsHex: "00000000" },
{ runId, targetProfileId: captureTarget, simulation: true });
writer.finish("synthetic-ui-test-no-device");
const hash = async file => createHash("sha256").update(await fs.readFile(file)).digest("hex");
const captureHash = await hash(capture);
const nativeCapture = path.join(temp, "injected-native-context.pcapng"), nativeRun = "ui-native-" + randomUUID();
const nativeWriter = createDiagnosticPduWriter(nativeCapture, false);
for (const metadata of [{ primitive: 88, expectedPrimitive: 1 }, { primitive: 1 }]) nativeWriter.append({ requestHex: "21A4",
  pduHex: "61A4000001", timestampUs: 2000000, timestampSource: "host-pdu-observed", transportKind: "d-pdu-iso-tp",
  observation: "native-result-data", duringCleanup: false, uniqueId: 1, flagsHex: "00000000", ...metadata },
{ runId: nativeRun, targetProfileId: captureTarget, simulation: false });
nativeWriter.finish("injected software context checks; no device IO");
await fs.writeFile(path.join(temp, "devices.json"), JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DEFINITION_ROOT: library, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"),
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection-state.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, workspace; const errors = [];
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000); page.on("pageerror", e => errors.push(e.message));
  const bundle = path.join(root, ".local/obd-coding-readiness-20261009/definitions-live-current.zip");
  assert.equal(await hash(bundle), "bc8a964e8fc8595aad8c74d4f95f36f19c038b3e7e910f4fea4031def70b8296");
  await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, bundle);
  const imported = await page.evaluate(() => window.porsche981.diagnosticDefinitionBundle({ action: "import" }));
  assert.equal(imported.ok, true, JSON.stringify(imported)); assert.equal(imported.files, 660);
  await app.evaluate(({ dialog }, files) => {
    let started;
    globalThis.__codingCaptureOpenStarted = new Promise(resolve => { started = resolve; });
    dialog.showOpenDialog = () => { started(); return new Promise(resolve => {
      globalThis.__codingCaptureFinishOpen = () => resolve({ canceled: false, filePaths: [files.capture] });
    }); };
    let saves = 0;
    dialog.showSaveDialog = async () => !saves++ ? { canceled: true }
      : { canceled: false, filePath: saves === 2 ? files.capture : files.saved };
  }, { capture, saved });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.locator('[data-coding-category="coding"]').click();
  await page.getByLabel("原码范围版本").selectOption(profileId);
  const panel = page.getByTestId("coding-capture-panel"), result = page.getByTestId("coding-capture-result");
  await panel.getByRole("button", { name: "选择应用响应捕获", exact: true }).click();
  await app.evaluate(async () => globalThis.__codingCaptureOpenStarted);
  const gated = await page.evaluate(async () => Promise.all([
    window.porsche981.obdConnection({ action: "connect" }),
    window.porsche981.readOnlySession({ action: "start" }),
    window.porsche981.canCapture({ action: "start" }),
    window.porsche981.diagnosticCanRecording({ action: "start" }),
    window.porsche981.manufacturerStream({ action: "start" }),
    window.porsche981.diagnosticDefinitionBundle({ action: "export" }),
  ]));
  assert.deepEqual(gated.map(doc => doc.error), ["capture-busy", "capture-busy", "capture-busy", "capture-busy", "capture-busy", "bundle-busy-disconnect-first"]);
  await app.evaluate(() => globalThis.__codingCaptureFinishOpen());
  await panel.getByLabel("设码响应捕获目标").selectOption(captureTarget);
  await panel.getByLabel("设码响应采集会话").selectOption(runId);
  const analyze = panel.getByRole("button", { name: "离线核对本次响应", exact: true });
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent === "离线核对本次响应" && !b.disabled));
  await analyze.click(); await result.waitFor();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="coding-capture-result"] ol > li').length === 20);
  assert.match(await result.innerText(), /39 个来源字段 · 28 组读取 · 缺少 28 组/);
  assert.match(await result.innerText(), /模拟响应，单列保留 24/);
  // Track only this test's private workspace, never other sessions' directories.
  for (const name of await fs.readdir(os.tmpdir())) {
    if (!name.startsWith("flat-six-coding-capture-")) continue;
    const folder = path.join(os.tmpdir(), name);
    const report = await fs.readFile(path.join(folder, "result/report.json"), "utf8").then(JSON.parse).catch(() => null);
    if (report?.runId === runId) { assert.equal(workspace, undefined); workspace = folder; }
  }
  assert.ok(workspace, "actual main-process temporary artifact exists");
  const save = result.getByRole("button", { name: "保存完整核对结果 ZIP", exact: true });
  await save.click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === "保存完整核对结果 ZIP")?.disabled);
  await assert.rejects(fs.stat(saved), { code: "ENOENT" });
  assert.equal(await result.locator("ol > li").count(), 20, "cancelled save preserves current preview");
  await save.click();
  await panel.getByRole("alert").filter({ hasText: "保存位置与原始捕获是同一文件" }).waitFor();
  assert.equal(await hash(capture), captureHash); await assert.rejects(fs.stat(saved), { code: "ENOENT" });
  await save.click(); await result.getByRole("status").waitFor();
  const inspected = JSON.parse(execFileSync(process.env.PORSCHE981_PYTHON || "python", ["-B", "-c",
    "import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps({'names':z.namelist(),'report':json.loads(z.read('report.json')),'rows':[json.loads(x) for x in z.read('responses.jsonl').splitlines()],'blocks':[json.loads(x) for x in z.read('blocks.jsonl').splitlines()]}))", saved], { encoding: "utf8" }));
  assert.deepEqual(inspected.names.sort(), ["blocks.jsonl", "report.json", "responses.jsonl"]);
  assert.equal(inspected.report.captureTargetProfileId, captureTarget);
  assert.equal(inspected.report.sourceProfileId, profileId);
  assert.equal(inspected.blocks.length, 56, "all 28 groups in both provenance classes saved before paging");
  const block = inspected.blocks.find(b => b.source === "synthetic" && b.requestHex === "21A4");
  assert.equal(block.status, "consistent"); assert.equal(block.observations, 24);
  assert.equal(block.representative.dataHex, "000001"); assert.equal(block.completeLengthQualified, false);
  assert.equal(inspected.report.blockSet.reports[0].consistentReadSetAvailable, false);
  assert.equal(inspected.rows.length, 24, "explicit ZIP includes rows never paged into renderer");
  for (const flag of ["baselineCreated", "freshReadProven", "completeOriginalBackup", "executionEnabled", "identityQualified"]) assert.equal(inspected.report[flag], false);
  assert.equal(inspected.report.counts["synthetic-capture-excluded"], 24);
  assert.equal(inspected.report.sourceVariantsSha256, "8a9e0ee94c25e98b11affbc083615bbd4c8113fe9c2a820ca8e4147aa16d5c20");
  await result.getByRole("button", { name: "继续查看后续响应", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="coding-capture-result"] ol > li').length === 24);
  assert.equal(await result.getByRole("button", { name: "继续查看后续响应", exact: true }).count(), 0);
  await result.locator("ol > li").first().getByText("查看完整原始响应", { exact: true }).click();
  assert.match(await result.innerText(), /PDU：61A4000001/);
  await result.getByLabel("设码核对内容").selectOption("blocks");
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="coding-capture-result"] ol > li').length === 20);
  let expected = 20;
  while (await result.getByRole("button", { name: "继续查看后续响应", exact: true }).count()) {
    await result.getByRole("button", { name: "继续查看后续响应", exact: true }).click(); expected = Math.min(expected + 20, 56);
    await page.waitForFunction(n => document.querySelectorAll('[data-testid="coding-capture-result"] ol > li').length === n, expected);
  }
  assert.equal(expected, 56); assert.match(await result.innerText(), /模拟响应 · 重复响应一致/);
  await page.screenshot({ path: path.join(temp, "coding-capture.png"), fullPage: true });
  await result.getByLabel("设码核对内容").selectOption("responses");
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="coding-capture-result"] ol > li').length === 20);
  await panel.getByLabel("设码响应采集会话").selectOption("");
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === "选择应用响应捕获")?.disabled);
  assert.equal(await result.count(), 0); await assert.rejects(fs.stat(workspace), { code: "ENOENT" });
  assert.equal(await hash(capture), captureHash); assert.ok((await fs.stat(saved)).size > 0);
  await app.evaluate(({ dialog }, file) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [file] }); }, nativeCapture);
  await panel.getByRole("button", { name: "选择应用响应捕获", exact: true }).click();
  await panel.getByLabel("设码响应捕获目标").selectOption(captureTarget);
  await panel.getByLabel("设码响应采集会话").selectOption(nativeRun);
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent === "离线核对本次响应")?.disabled);
  await analyze.click(); await result.waitFor();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="coding-capture-result"] ol > li').length === 2);
  assert.match(await result.innerText(), /响应来自其他通信句柄 1/);
  assert.match(await result.innerText(), /旧捕获缺少当次请求通信句柄 1/);
  assert.match(await result.innerText(), /缺少 28 组/);
  let nativeWorkspace;
  for (const name of await fs.readdir(os.tmpdir())) {
    if (!name.startsWith("flat-six-coding-capture-")) continue;
    const folder = path.join(os.tmpdir(), name);
    const report = await fs.readFile(path.join(folder, "result/report.json"), "utf8").then(JSON.parse).catch(() => null);
    if (report?.runId === nativeRun) nativeWorkspace = folder;
  }
  assert.ok(nativeWorkspace);
  await page.getByLabel("原码范围版本").selectOption("");
  assert.equal(await panel.count(), 0);
  // Discard is intentionally asynchronous until actual worker closure.
  const cleanupDeadline = Date.now() + 3000;
  while (await fs.stat(nativeWorkspace).then(() => true, error => { if (error.code === "ENOENT") return false; throw error; })) {
    assert.ok(Date.now() < cleanupDeadline, "native view temporary files are removed after unmount");
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  await assert.rejects(fs.stat(nativeWorkspace), { code: "ENOENT" }); assert.deepEqual(errors, []);
  await fs.writeFile(path.join(temp, "verification.json"), JSON.stringify({ ok: true, runId, rows: 24, initiallyDisplayed: 20,
    savedBeforePaging: true, captureSha256: captureHash, sourceOverwritePrevented: true, cleanupVerified: true,
    mainIpcMutualExclusion: true, nativeForeignAndLegacyContextRejected: true, nativeViewCleanupVerified: true,
    sourceAndRuntimeTargetsSeparated: true, fullBlockRows: 56, sourceCatalogImported: 660, noDeviceIO: true, liveVerified: false }, null, 2));
  console.log("PASS actual Electron UI/preload/IPC/Python: pinned DME 39 fields / 28 groups, 24 synthetic responses excluded, 20+4 paging, cancelled save, full ZIP before paging, selection cleanup; no device IO");
} finally { if (app) await app.close(); }
