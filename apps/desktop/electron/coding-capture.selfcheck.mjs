import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createCodingCapture, startCodingCaptureWorker } from "./coding-capture.mjs";

const flags = { executionEnabled: false, liveVerified: false, writePayload: null, baselineCreated: false, completeOriginalBackup: false, freshReadProven: false };
const inventory = { ok: true, ...flags, capture: { sha256: "a".repeat(64), packets: 1 }, sessions: [{ targetProfileId: "known", runId: "run", synthetic: true, packets: 1 }] };
const scratch = await fs.mkdtemp(path.join(os.tmpdir(), "flat-six-capture-selfcheck-"));
const calls = []; let chooseCount = 0, vehicleBusy = false, hold = null, savePath = path.join(scratch, "chosen.zip");
await fs.writeFile(path.join(scratch, "selected.pcapng"), "original synthetic source");
function fakeWorker(request) {
  calls.push(request); let terminal = false, resolveResult, resolveClosed;
  const result = new Promise(resolve => { resolveResult = resolve; }), closed = new Promise(resolve => { resolveClosed = resolve; });
  const job = { result, closed, cancel() { job.cancelled = true; }, get terminal() { return terminal; },
    close(doc) { terminal = true; resolveResult(doc); resolveClosed(); } };
  void (async () => {
    if (request.action === "scope") job.close({ ok: true, links: [{ linkIndex: 0, requests: ["190208"], missing: [] }] });
    else if (request.action === "inventory") job.close(inventory);
    else if (request.action === "analyze") {
      await fs.mkdir(request.output); await fs.writeFile(path.join(request.output, "report.json"), "private synthetic fixture");
      if (hold) { hold.job = job; resolveResult({ ok: false, error: "capture-worker-close-timeout" }); }
      else job.close({ ok: true, report: { requestGroupCount: 1 }, artifact: { "report.json": "fixture", "responses.jsonl": "fixture" } });
    } else job.close({ ok: true, rows: [], nextCursor: null, saved: true });
  })();
  return job;
}
const manager = createCodingCapture({ repoRoot: process.cwd(), startWorker: fakeWorker, temporaryRoot: scratch,
  isVehicleBusy: () => vehicleBusy, chooseOpen: async () => { chooseCount++; return path.join(scratch, "selected.pcapng"); },
  chooseSave: async () => savePath });
const base = { clientId: "panel-one" };
const open = () => manager.handle({ ...base, action: "open" }, 1);
try {
  assert.equal((await manager.handle({ ...base, action: "open", path: "renderer-supplied" }, 1)).error, "capture-request-invalid");
  assert.equal(chooseCount, 0); vehicleBusy = true;
  assert.equal((await open()).error, "capture-finish-vehicle-first"); assert.equal(chooseCount, 0); vehicleBusy = false;
  const selected = await open(); assert.equal(selected.ok, true);
  const request = { ...base, action: "analyze", captureId: selected.captureId, profileId: "known", generation: "981", runId: "run" };
  assert.equal((await manager.handle(request, 2)).error, "capture-selection-expired");
  assert.equal((await manager.handle({ ...request, runId: "other" }, 1)).error, "capture-session-mismatch");
  const analyzed = await manager.handle(request, 1); assert.equal(analyzed.ok, true);
  const directory = calls.find(c => c.action === "analyze").output;
  assert.equal(await fs.readFile(path.join(directory, "report.json"), "utf8"), "private synthetic fixture");
  assert.equal((await manager.handle({ ...base, action: "save", analysisId: analyzed.analysisId }, 2)).ok, false);
  assert.equal((await manager.handle({ ...base, action: "save", analysisId: analyzed.analysisId }, 1)).saved, true);
  savePath = path.join(scratch, "source-alias.pcapng");
  await fs.link(path.join(scratch, "selected.pcapng"), savePath);
  const beforeExports = calls.filter(c => c.action === "export").length;
  assert.equal((await manager.handle({ ...base, action: "save", analysisId: analyzed.analysisId }, 1)).error, "capture-export-overwrites-source");
  assert.equal(calls.filter(c => c.action === "export").length, beforeExports);
  assert.equal(await fs.readFile(savePath, "utf8"), "original synthetic source");
  savePath = path.join(scratch, "chosen.zip");
  await manager.handle({ ...base, action: "clear" }, 1);
  await assert.rejects(fs.stat(directory), /ENOENT/);
  assert.equal((await manager.handle({ ...base, action: "save", analysisId: analyzed.analysisId }, 1)).error, "capture-analysis-expired");
  const beforeCancel = calls.filter(c => c.action === "analyze").length;
  const preStartup = manager.handle(request, 1);
  await manager.handle({ ...base, action: "cancel" }, 1);
  assert.equal((await preStartup).error, "capture-cancelled");
  assert.equal(calls.filter(c => c.action === "analyze").length, beforeCancel, "cancel during preparation never launches a late worker");

  hold = {}; const timeout = await manager.handle(request, 1); assert.equal(timeout.error, "capture-worker-close-timeout");
  const deferredDirectory = calls.filter(c => c.action === "analyze").at(-1).output;
  const discarded = manager.cancelOwned(1); assert.equal(manager.isIdle(), false);
  assert.equal((await open()).error, "capture-busy"); assert.ok(await fs.stat(deferredDirectory));
  hold.job.close({ ok: false }); await discarded;
  await assert.rejects(fs.stat(deferredDirectory), /ENOENT/); assert.equal(manager.isIdle(), true);
  hold = null;

  const measurementBase = { clientId: "measurement-panel" };
  const measurementOpen = await manager.handle({ ...measurementBase, action: "open" }, 5, "measurement");
  assert.equal(measurementOpen.ok, true); assert.equal(calls.at(-1).kind, "measurement");
  const measurementRequest = { ...measurementBase, action: "analyze", captureId: measurementOpen.captureId, profileId: "known", generation: "982", runId: "run" };
  assert.equal((await manager.handle(measurementRequest, 5)).error, "capture-selection-expired", "coding channel cannot use measurement selection");
  assert.equal((await manager.handle({ ...measurementBase, action: "discard" }, 5)).error, "capture-selection-expired", "coding channel cannot discard measurement selection");
  const measurementAnalysis = await manager.handle(measurementRequest, 5, "measurement");
  assert.equal(measurementAnalysis.ok, true);
  assert.equal(calls.at(-1).kind, "measurement");
  for (const stream of ["values", "responses"]) {
    assert.equal((await manager.handle({ ...measurementBase, action: "page", analysisId: measurementAnalysis.analysisId, cursor: 0, stream }, 5, "measurement")).ok, true);
    assert.equal(calls.at(-1).stream, stream); assert.equal(calls.at(-1).kind, "measurement");
  }
  assert.equal((await manager.handle({ ...measurementBase, action: "page", analysisId: measurementAnalysis.analysisId, cursor: 0, stream: "../report" }, 5, "measurement")).error, "capture-stream-invalid");
  assert.equal((await manager.handle({ ...measurementBase, action: "open", kind: "coding" }, 5, "measurement")).error, "capture-request-invalid", "renderer cannot choose worker kind");
  await manager.cancelOwned(5);

  const dtcBase = { clientId: "dtc-panel" }, context = { sourceCatalogId: "a".repeat(64), ecuId: 9, generation: "981", profileId: "known" };
  const scope = await manager.handle({ ...dtcBase, action: "scope", ...context }, 6, "dtc");
  assert.equal(scope.links[0].linkIndex, 0); assert.equal(calls.at(-1).kind, "dtc");
  const dtcOpen = await manager.handle({ ...dtcBase, action: "open" }, 6, "dtc");
  const dtcRequest = { ...dtcBase, action: "analyze", ...context, captureId: dtcOpen.captureId, linkIndex: 0, runId: "run", captureTargetProfileId: "known" };
  for (const bad of [{ linkIndex: -1 }, { ecuId: true }, { sourceCatalogId: "bad" }])
    assert.equal((await manager.handle({ ...dtcRequest, ...bad }, 6, "dtc")).error, "capture-request-invalid");
  assert.equal((await manager.handle({ ...dtcBase, action: "discard" }, 6, "measurement")).error, "capture-selection-expired");
  for (const captureTargetProfileId of ["", 2, "x".repeat(241)]) assert.equal((await manager.handle({ ...dtcRequest, captureTargetProfileId }, 6, "dtc")).error, "capture-request-invalid");
  assert.equal((await manager.handle({ ...dtcRequest, captureTargetProfileId: "not-in-capture" }, 6, "dtc")).error, "capture-session-mismatch");
  const dtcAnalysis = await manager.handle({ ...dtcRequest, profileId: "explicit-source-version" }, 6, "dtc"); assert.equal(dtcAnalysis.ok, true);
  assert.equal(calls.at(-1).captureTargetProfileId, "known"); assert.equal(calls.at(-1).profileId, "explicit-source-version");
  assert.equal(calls.at(-1).ecuId, 9); assert.equal(calls.at(-1).linkIndex, 0); assert.equal(calls.at(-1).sourceCatalogId, context.sourceCatalogId);
  for (const stream of ["faults", "responses"]) assert.equal((await manager.handle({ ...dtcBase, action: "page", analysisId: dtcAnalysis.analysisId, cursor: 0, stream }, 6, "dtc")).ok, true);
  assert.equal((await manager.handle({ ...dtcBase, action: "page", analysisId: dtcAnalysis.analysisId, cursor: 0, stream: "values" }, 6, "dtc")).error, "capture-stream-invalid");
  await manager.cancelOwned(6);

  const identityBase = { clientId: "identity-panel" }, identityContext = { sourceCatalogId: "b".repeat(64), generation: "981", ecuId: 1 };
  assert.equal((await manager.handle({ ...identityBase, ...identityContext, action: "scope" }, 7, "identity")).ok, true);
  assert.equal(calls.at(-1).kind, "identity");
  assert.equal((await manager.handle({ ...identityBase, ...identityContext, action: "scope", profileId: "cannot-choose-one-version" }, 7, "identity")).error, "capture-request-invalid");
  const identityOpen = await manager.handle({ ...identityBase, action: "open" }, 7, "identity");
  const identityRequest = { ...identityBase, ...identityContext, action: "analyze", captureId: identityOpen.captureId, captureTargetProfileId: "known", runId: "run" };
  assert.equal((await manager.handle(identityRequest, 8, "identity")).error, "capture-selection-expired");
  assert.equal((await manager.handle(identityRequest, 7, "dtc")).error, "capture-request-invalid");
  assert.equal((await manager.handle({ ...identityRequest, captureTargetProfileId: "other" }, 7, "identity")).error, "capture-session-mismatch");
  assert.equal((await manager.handle({ ...identityRequest, ecuId: true }, 7, "identity")).error, "capture-request-invalid");
  const identityAnalysis = await manager.handle(identityRequest, 7, "identity"); assert.equal(identityAnalysis.ok, true);
  assert.equal(calls.at(-1).kind, "identity"); assert.equal(calls.at(-1).captureTargetProfileId, "known");
  assert.equal(Object.hasOwn(calls.at(-1), "profileId"), false);
  for (const stream of ["values", "responses"]) assert.equal((await manager.handle({ ...identityBase, action: "page", analysisId: identityAnalysis.analysisId, cursor: 0, stream }, 7, "identity")).ok, true);
  assert.equal((await manager.handle({ ...identityBase, action: "page", analysisId: identityAnalysis.analysisId, cursor: 0, stream: "faults" }, 7, "identity")).error, "capture-stream-invalid");
  assert.equal((await manager.handle({ ...identityBase, action: "save", analysisId: identityAnalysis.analysisId }, 7, "identity")).saved, true);
  await manager.cancelOwned(7);

  const codingBase = { clientId: "source-coding-panel" };
  assert.equal((await manager.handle({ ...codingBase, action: "units", generation: "981", sourceCatalogId: "a".repeat(64), systemId: "dme" }, 8)).ok, true);
  assert.equal(calls.at(-1).kind, "coding"); assert.equal(calls.at(-1).systemId, "dme");
  assert.equal((await manager.handle({ ...codingBase, action: "units", generation: "981", sourceCatalogId: "a".repeat(64), systemId: "../../guess" }, 8)).error, "capture-request-invalid");
  assert.equal((await manager.handle({ ...codingBase, action: "scope", generation: "981", profileId: "source-version",
    sourceCatalogId: "a".repeat(64), ecuId: 1 }, 8)).ok, true);
  assert.equal(calls.at(-1).kind, "coding");
  const codingOpen = await manager.handle({ ...codingBase, action: "open" }, 8);
  const codingRequest = { ...codingBase, action: "analyze", captureId: codingOpen.captureId, profileId: "source-version",
    generation: "981", runId: "run", sourceCatalogId: "a".repeat(64), ecuId: 1, captureTargetProfileId: "known" };
  for (const mutation of [{ ecuId: true }, { sourceCatalogId: "bad" }, { captureTargetProfileId: "" }]) {
    assert.equal((await manager.handle({ ...codingRequest, ...mutation }, 8)).error, "capture-request-invalid");
  }
  const incomplete = { ...codingRequest }; delete incomplete.ecuId;
  assert.equal((await manager.handle(incomplete, 8)).error, "capture-request-invalid");
  assert.equal((await manager.handle({ ...codingRequest, captureTargetProfileId: "other" }, 8)).error, "capture-session-mismatch");
  const codingAnalysis = await manager.handle(codingRequest, 8); assert.equal(codingAnalysis.ok, true);
  assert.equal(calls.at(-1).profileId, "source-version"); assert.equal(calls.at(-1).captureTargetProfileId, "known");
  assert.equal((await manager.handle({ ...codingBase, action: "page", analysisId: codingAnalysis.analysisId, cursor: 0, stream: "blocks" }, 8)).ok, true);
  assert.equal(calls.at(-1).stream, "blocks");
  assert.equal((await manager.handle({ ...codingBase, action: "page", analysisId: codingAnalysis.analysisId, cursor: 0, stream: "values" }, 8)).error, "capture-stream-invalid");
  await manager.cancelOwned(8);

  let finishDialog, workers = 0;
  const dialogManager = createCodingCapture({ repoRoot: process.cwd(), temporaryRoot: scratch,
    chooseOpen: () => new Promise(resolve => { finishDialog = resolve; }), chooseSave: async () => null,
    startWorker: request => { workers++; return fakeWorker(request); } });
  const pending = dialogManager.handle({ ...base, action: "open" }, 3);
  assert.equal((await dialogManager.handle({ ...base, action: "open" }, 3)).error, "capture-busy");
  await dialogManager.cancelOwned(3); finishDialog("late native choice");
  assert.equal((await pending).error, "capture-cancelled"); assert.equal(workers, 0); await dialogManager.shutdown();

  const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.kill = () => { child.kills = (child.kills || 0) + 1; };
  const runner = startCodingCaptureWorker({ action: "inventory", capture: "main-owned" }, { repoRoot: process.cwd(), timeoutMs: 1000, closeWaitMs: 30,
    spawnFn: (_exe, args, options) => { assert.equal(args.at(-1), "scripts.diagnostics.coding_capture_bridge"); assert.equal(options.shell, false); return child; } });
  let settled = false; runner.result.then(() => { settled = true; }); runner.cancel();
  await new Promise(resolve => setTimeout(resolve, 5)); assert.equal(settled, false); assert.equal(runner.terminal, false);
  child.stdout.write(JSON.stringify(inventory)); child.emit("close", 0);
  assert.equal((await runner.result).error, "capture-cancelled"); await runner.closed; assert.equal(runner.terminal, true);
  await manager.shutdown();
  console.log("PASS coding capture: native paths only, window/client ownership, vehicle gate, selection reset, explicit save, concurrent dialogs, cancellation and actual-close cleanup (injected software only)");
} finally { await manager.shutdown(); await fs.rm(scratch, { recursive: true, force: true }); }
