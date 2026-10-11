/** Removed live-page panel and retained offline IPC; synthetic data, no hardware. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { randomUUID, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createDiagnosticPduWriter } from "../apps/desktop/electron/diagnostic-pdu-data.mjs";

const root = path.resolve(import.meta.dirname, "..");
const scratch = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-measurement-capture-ui-accept"));
await fs.mkdir(scratch, { recursive: true });
const temp = await fs.mkdtemp(path.join(scratch, "ui-"));
const profileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", runId = "ui-measurement-" + randomUUID();
const capture = path.join(temp, "synthetic-measurement.pcapng"), saved = path.join(temp, "full-result.zip");
const writer = createDiagnosticPduWriter(capture, true);
const pdus = [...Array(24).fill("6110" + "00".repeat(256)), "7F2178", "611000", "611000", "611000"];
for (const [i, pduHex] of pdus.entries()) writer.append({ requestHex: "2110", pduHex, timestampUs: 1000000 + i,
  timestampSource: "host-pdu-observed", transportKind: "d-pdu-iso-tp", observation: "native-result-data",
  duringCleanup: false, uniqueId: 1, flagsHex: "00000000", primitive: i === 26 ? 88 : 1,
  ...(i === 27 ? {} : { expectedPrimitive: 1 }) }, { runId, targetProfileId: profileId, simulation: true });
writer.finish("synthetic UI fixture only; no device IO");
const hash = async file => createHash("sha256").update(await fs.readFile(file)).digest("hex");
const captureHash = await hash(capture);
await fs.writeFile(path.join(temp, "devices.json"), JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_CONNECTION_FIXTURE: path.join(temp, "devices.json"),
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection-state.json") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, workspace; const errors = [];
try {
  app = await electron.launch({ args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(30000); page.on("pageerror", e => errors.push(e.message));
  await app.evaluate(({ dialog }, files) => {
    let started; globalThis.__measurementOpenStarted = new Promise(resolve => { started = resolve; });
    dialog.showOpenDialog = () => { started(); return new Promise(resolve => {
      globalThis.__measurementFinishOpen = () => resolve({ canceled: false, filePaths: [files.capture] });
    }); };
    let saves = 0; dialog.showSaveDialog = async () => !saves++ ? { canceled: true }
      : { canceled: false, filePath: saves === 2 ? files.capture : files.saved };
  }, { capture, saved });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  assert.equal(await page.getByTestId("measurement-capture-selection").count(), 0);
  assert.equal(await page.getByTestId("measurement-capture-panel").count(), 0);
  assert.equal(await page.getByText("断车后：完整实时数据核对", { exact: true }).count(), 0);
  const clientId = "measurement-accept-" + runId;
  async function invoke(request) {
    return page.evaluate(request => window.porsche981.measurementCapture(request), { clientId, ...request });
  }
  // Exercise the existing offline API separately from the removed product panel.
  await page.evaluate(request => {
    window.__measurementOpen = window.porsche981.measurementCapture(request);
  }, { action: "open", clientId });
  await app.evaluate(async () => globalThis.__measurementOpenStarted);
  const gated = await page.evaluate(async () => Promise.all([
    window.porsche981.obdConnection({ action: "connect" }), window.porsche981.readOnlySession({ action: "start" }),
    window.porsche981.canCapture({ action: "start" }), window.porsche981.diagnosticCanRecording({ action: "start" }),
    window.porsche981.manufacturerStream({ action: "start" }), window.porsche981.diagnosticDefinitionBundle({ action: "export" }),
  ]));
  assert.deepEqual(gated.map(doc => doc.error), ["capture-busy", "capture-busy", "capture-busy", "capture-busy", "capture-busy", "bundle-busy-disconnect-first"]);
  await app.evaluate(() => globalThis.__measurementFinishOpen());
  const opened = await page.evaluate(() => window.__measurementOpen);
  assert.equal(opened.ok, true);
  const analysis = await invoke({ action: "analyze", captureId: opened.captureId, generation: "981", profileId, runId });
  assert.equal(analysis.ok, true, JSON.stringify(analysis));
  const firstPage = await invoke({ action: "page", analysisId: analysis.analysisId, cursor: 0, stream: "values" });
  assert.equal(firstPage.ok, true); assert.equal(firstPage.rows.length, 20);
  for (const name of await fs.readdir(os.tmpdir())) {
    if (!name.startsWith("flat-six-measurement-capture-")) continue;
    const folder = path.join(os.tmpdir(), name);
    const report = await fs.readFile(path.join(folder, "result/report.json"), "utf8").then(JSON.parse).catch(() => null);
    if (report?.runId === runId) { assert.equal(workspace, undefined); workspace = folder; }
  }
  assert.ok(workspace);
  const saveRequest = { action: "save", analysisId: analysis.analysisId };
  const cancelled = await invoke(saveRequest); assert.equal(cancelled.ok, true);
  await assert.rejects(fs.stat(saved), { code: "ENOENT" });
  const overwrite = await invoke(saveRequest); assert.equal(overwrite.ok, false);
  assert.equal(await hash(capture), captureHash);
  const exported = await invoke(saveRequest); assert.equal(exported.ok, true);
  const inspected = JSON.parse(execFileSync(process.env.PORSCHE981_PYTHON || "python", ["-B", "-c",
    "import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps({'names':z.namelist(),'report':json.loads(z.read('report.json')),'responses':len(z.read('responses.jsonl').splitlines()),'values':len(z.read('values.jsonl').splitlines()),'fields':len(json.loads(z.read('fields.json'))['fields'])}))", saved], { encoding: "utf8" }));
  assert.deepEqual(inspected.names.sort(), ["fields.json", "report.json", "responses.jsonl", "values.jsonl"]);
  assert.equal(inspected.responses, 28); assert.equal(inspected.values, 28 * 94); assert.equal(inspected.fields, 691);
  assert.equal(inspected.report.decodedSourceFieldCount, 0); assert.ok(inspected.report.syntheticDecodedSourceFieldCount > 12);
  assert.ok(inspected.report.fieldCounts["synthetic-failed"] > 0);
  for (const flag of ["identityQualified", "vehicleValuesQualified", "executionEnabled", "baselineCreated"]) assert.equal(inspected.report[flag], false);
  const nextValues = await invoke({ action: "page", analysisId: analysis.analysisId, cursor: firstPage.nextCursor, stream: "values" });
  assert.equal(nextValues.ok, true); assert.equal(nextValues.rows.length, 20);
  const responses = await invoke({ action: "page", analysisId: analysis.analysisId, cursor: 0, stream: "responses" });
  assert.equal(responses.ok, true); assert.equal(responses.rows.length, 20);
  const nextResponses = await invoke({ action: "page", analysisId: analysis.analysisId, cursor: responses.nextCursor, stream: "responses" });
  assert.equal(nextResponses.ok, true); assert.equal(nextResponses.rows.length, 8);
  await page.screenshot({ path: path.join(temp, "live-panel-removed.png"), fullPage: true });
  await invoke({ action: "discard" });
  const deadline = Date.now() + 3000;
  while (await fs.stat(workspace).then(() => true, error => { if (error.code === "ENOENT") return false; throw error; })) {
    assert.ok(Date.now() < deadline, "own temporary workspace removed after view change"); await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(await hash(capture), captureHash); assert.deepEqual(errors, []);
  await fs.writeFile(path.join(temp, "verification.json"), JSON.stringify({ ok: true, runId, initiallyDisplayed: 20,
    fullResult: inspected, sourceOverwritePrevented: true, mainIpcMutualExclusion: true, cleanupVerified: true,
    livePanelRemoved: true, captureSha256: captureHash, noDeviceIO: true, laptopVerified: false }, null, 2));
  console.log("PASS live measurement panel removed; retained offline IPC/Python: full DME catalog, both streams, complete export, source protection, shared gate and cleanup; synthetic only");
} finally { if (app) await app.close(); }
