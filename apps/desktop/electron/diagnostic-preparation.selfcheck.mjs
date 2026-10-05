import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { createDiagnosticPreparation, normalizeCodingBackup, codingRestorePlan, firmwarePreparation, runCodingRehearsal, codingBlockKey } from "./diagnostic-preparation.mjs";
import { createEngineAcquisition } from "../src/engine-acquisition.mjs";
import { createDiagnosticCanRecorder } from "./diagnostic-can-recording.mjs";

const scratch = path.join(process.cwd(), ".local", "obd-offline-implementation-20261005");
await fs.mkdir(scratch, { recursive: true });
const directory = await fs.mkdtemp(path.join(scratch, "preparation-test-"));
const identity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "synthetic-HW", software: "synthetic-SW" };
const backup = (dataHex = "A5", capturedUtc = "2026-10-05T00:00:00Z") => ({ schemaVersion: 1, kind: "ecu-coding-backup", identity,
  profileId: "synthetic-offline-only", expectedDids: ["F001"], blocks: [{ did: "F001", dataHex }], capturedUtc });
assert.throws(() => normalizeCodingBackup({ ...backup(), expectedDids: ["F001", "F002"] }), /coverage|incomplete/);
assert.throws(() => normalizeCodingBackup({ ...backup(), identity: { ...identity, vin: "unknown" } }), /identity/);
const original = normalizeCodingBackup(backup());
const current = normalizeCodingBackup(backup("A4"));
assert.equal(original.freshReadProven, false);
assert.deepEqual(original, { ...backup(), provenance: "imported", freshReadProven: false, completeness: "declared-block-list", liveVerified: false },
  "v1 normalized representation remains byte-compatible with existing immutable hashes");
const plan = codingRestorePlan(original, current);
assert.equal(plan.blocks[0].targetHex, "A5");
assert.equal(plan.changedBlocks, 1);
assert.equal(plan.executionEnabled, false);
assert.throws(() => codingRestorePlan(original, { ...backup(), identity: { ...identity, software: "other" } }), /mismatch/);
assert.throws(() => codingRestorePlan(original, backup("A400")), /length/);
let simulatedWrites = 0, persistedFirst = false, memoryValue = "A4";
const dependencies = { simulation: true, readIdentity: async () => ({ software: identity.software, ...identity }), readCurrent: async () => current,
  persistBackup: async () => { persistedFirst = true; return { id: "persisted-test" }; },
  writeBlock: async (_did, value) => { assert.equal(persistedFirst, true); simulatedWrites++; memoryValue = value; }, readBlock: async () => memoryValue };
const exercised = await runCodingRehearsal(plan, dependencies);
assert.equal(exercised.ok, true); assert.equal(memoryValue, "A5"); assert.equal(simulatedWrites, 1);
simulatedWrites = 0;
const failedBackup = await runCodingRehearsal(plan, { ...dependencies, persistBackup: async () => { throw new Error("pre-write-backup-failed"); } });
assert.equal(failedBackup.error, "pre-write-backup-failed"); assert.equal(simulatedWrites, 0);
const failedIdentity = await runCodingRehearsal(plan, { ...dependencies, readIdentity: async () => ({ ...identity, software: "changed" }) });
assert.equal(failedIdentity.error, "pre-write-identity-mismatch"); assert.equal(simulatedWrites, 0);
simulatedWrites = 0;
const interrupted = await runCodingRehearsal(plan, { ...dependencies, writeBlock: async () => { simulatedWrites++; throw new Error("connection-lost-outcome-unknown"); } });
assert.equal(interrupted.error, "connection-lost-outcome-unknown"); assert.equal(simulatedWrites, 1);
assert.equal(interrupted.automaticWriteRetry, false); assert.equal(interrupted.recoveryRequired, true);
const mismatch = await runCodingRehearsal(plan, { ...dependencies, readBlock: async () => "FF" });
assert.equal(mismatch.error, "readback-mismatch"); assert.equal(mismatch.automaticWriteRetry, false);
await assert.rejects(runCodingRehearsal(plan, { ...dependencies, simulation: false }), /rehearsal-only/);
const inputs = [path.join(directory, "input-original.json"), path.join(directory, "input-current.json")];
await fs.writeFile(inputs[0], JSON.stringify(backup())); await fs.writeFile(inputs[1], JSON.stringify(backup("A4", "2026-10-05T00:01:00Z")));
let inputIndex = 0;
const vault = createDiagnosticPreparation({ directory: path.join(directory, "vault"), chooseOpenFile: async () => inputs[inputIndex++],
  saveFile: async (_name, data) => ({ ok: true, saved: true, data }), connectionStatus: () => null,
  previewField: async () => ({ ok: true, afterHex: "A4", beforeHex: "A5", changedBitMaskHex: "01" }) });
const first = await vault.handle({ action: "import-backup", ecu: "dme" });
const second = await vault.handle({ action: "import-backup", ecu: "dme" });
assert.equal(first.ok, true); assert.equal(second.ok, true);
assert.equal(second.backups.find((row) => row.original).id, first.id, "later imports never replace original baseline");
assert.equal(second.backups.length, 2);
const restored = await vault.handle({ action: "restore-plan", ecu: "dme", id: second.id });
assert.equal(restored.plan.changedBlocks, 1);
const simulatedRestore = await vault.handle({ action: "simulate-restore", ecu: "dme", id: second.id });
assert.equal(simulatedRestore.result.ok, true); assert.ok(simulatedRestore.result.backupId);
const operationBackup = await vault.handle({ action: "backup", ecu: "dme", id: simulatedRestore.result.backupId });
assert.equal(operationBackup.backup.provenance, "simulation"); assert.equal(operationBackup.backup.blocks[0].dataHex, "A4");
assert.equal((await vault.handle({ action: "restore-plan", ecu: "gateway", id: second.id })).error, "backup-current-ecu-mismatch");
assert.equal((await vault.handle({ action: "backup", ecu: "dme", id: "../../other" })).error, "backup-id-invalid");
assert.equal((await vault.handle({ action: "backup", ecu: "dme", id: second.id, path: "elsewhere" })).error, "preparation-request-invalid");
const file = path.join(directory, "vault", `${second.id}.json`);
const altered = JSON.parse(await fs.readFile(file, "utf8")); altered.backup.blocks[0].dataHex = "FF";
await fs.writeFile(file, JSON.stringify(altered));
assert.equal((await vault.handle({ action: "restore-plan", ecu: "dme", id: second.id })).error, "backup-integrity-failed");
// LID 10 and DID 0010 are different blocks, even when their numeric IDs match.
const typedBackup = (dataHex, capturedUtc) => ({ schemaVersion: 2, kind: "ecu-coding-backup", identity,
  profileId: "typed-test-only", capturedUtc,
  expectedBlocks: [{ identifierKind: "LID", identifierHex: "10" }, { identifierKind: "DID", identifierHex: "0010" }],
  blocks: [{ identifierKind: "LID", identifierHex: "10", dataHex }, { identifierKind: "DID", identifierHex: "0010", dataHex: "7E" }] });
const typed = normalizeCodingBackup(typedBackup("A5", "2026-10-05T01:00:00Z"));
assert.deepEqual(typed.blocks.map(codingBlockKey), ["DID:0010", "LID:10"]);
assert.throws(() => normalizeCodingBackup({ ...typed, expectedBlocks: [typed.expectedBlocks[0], typed.expectedBlocks[0]] }), /incomplete/);
assert.throws(() => normalizeCodingBackup({ ...typed, blocks: [{ did: "0010", identifierKind: "LID", identifierHex: "10", dataHex: "A5" }, typed.blocks[1]] }), /block/);
assert.throws(() => normalizeCodingBackup({ ...typed, expectedDids: ["0010"] }), /coverage/);
assert.throws(() => codingBlockKey({ identifierKind: "LID", identifierHex: "0010" }), /identifier/);
assert.throws(() => codingBlockKey({ identifierKind: "DID", identifierHex: "10" }), /identifier/);
const typedInputs = ["typed-original.json", "typed-current.json"].map((name) => path.join(directory, name));
await fs.writeFile(typedInputs[0], JSON.stringify(typedBackup("A5", "2026-10-05T01:00:00Z")));
await fs.writeFile(typedInputs[1], JSON.stringify(typedBackup("A4", "2026-10-05T01:01:00Z")));
let typedInput = 0, previewCalls = [];
const typedVault = createDiagnosticPreparation({ directory: path.join(directory, "typed-vault"),
  chooseOpenFile: async () => typedInputs[typedInput++], saveFile: async (_name, data) => ({ ok: true, saved: true, data }),
  connectionStatus: () => null, previewField: async (request) => {
    previewCalls.push(request);
    return { ok: true, beforeHex: request.dataHex, afterHex: "A4", changedBitMaskHex: "01" };
  } });
const typedFirst = await typedVault.handle({ action: "import-backup", ecu: "dme" });
const typedSecond = await typedVault.handle({ action: "import-backup", ecu: "dme" });
assert.equal(typedFirst.ok, true); assert.equal(typedSecond.ok, true);
assert.equal(typedSecond.backups.find((row) => row.original).id, typedFirst.id);
const typedPreview = await typedVault.handle({ action: "simulate-coding", ecu: "dme", id: typedFirst.id,
  blockKey: "LID:10", recordAt: 10, rawValue: 0 });
assert.equal(typedPreview.result.ok, true);
assert.deepEqual(typedPreview.result.readbacks.map(codingBlockKey).sort(), ["DID:0010", "LID:10"]);
assert.equal(previewCalls[0].expectedReadRequestHex, "2110");
assert.equal(typedPreview.plan.blocks.find((block) => codingBlockKey(block) === "DID:0010").targetHex, "7E");
await typedVault.handle({ action: "coding-options", ecu: "dme", id: typedFirst.id, blockKey: "DID:0010", recordAt: 11 });
assert.equal(previewCalls[1].expectedReadRequestHex, "220010");
const typedRestore = await typedVault.handle({ action: "simulate-restore", ecu: "dme", id: typedSecond.id });
assert.equal(typedRestore.result.ok, true); assert.equal(typedRestore.plan.changedBlocks, 1);
assert.equal((await typedVault.handle({ action: "simulate-restore", ecu: "dme", id: typedSecond.id, scenario: "backup-failed" })).result.writeAttemptCount, 0);
const typedInterrupted = await typedVault.handle({ action: "simulate-restore", ecu: "dme", id: typedSecond.id, scenario: "disconnect" });
assert.equal(typedInterrupted.result.writeAttemptCount, 1); assert.equal(typedInterrupted.result.automaticWriteRetry, false);
assert.equal((await typedVault.handle({ action: "coding-preview", ecu: "dme", id: typedFirst.id,
  blockKey: "LID:10", did: "0010", recordAt: 10, rawValue: 0 })).error, "coding-field-invalid");
const typedPlan = codingRestorePlan(typed, typedBackup("A4", "2026-10-05T01:01:00Z"));
const typedMemory = new Map([["DID:0010", "7E"], ["LID:10", "A4"]]);
const unintended = await runCodingRehearsal(typedPlan, { simulation: true,
  readIdentity: async () => identity, readCurrent: async () => typedBackup("A4", "2026-10-05T01:01:00Z"),
  persistBackup: async () => ({ id: "simulated-durable-backup" }),
  writeBlock: async (key, value) => { typedMemory.set(key, value); typedMemory.set("DID:0010", "FF"); },
  readBlock: async (key) => typedMemory.get(key) });
assert.equal(unintended.error, "readback-mismatch", "changes outside the selected field must be detected");
const multiTarget = { ...typed, blocks: typed.blocks.map((block) => ({ ...block, dataHex: "A5" })) };
const multiCurrent = { ...typed, blocks: typed.blocks.map((block) => ({ ...block, dataHex: "A4" })) };
const multiPlan = codingRestorePlan(multiTarget, multiCurrent);
const multiMemory = new Map(multiCurrent.blocks.map((block) => [codingBlockKey(block), block.dataHex]));
const crossWrite = await runCodingRehearsal(multiPlan, { simulation: true,
  readIdentity: async () => identity, readCurrent: async () => multiCurrent,
  persistBackup: async () => ({ id: "simulated-durable-backup" }),
  writeBlock: async (key, value) => { multiMemory.set(key, value); if (key === "LID:10") multiMemory.set("DID:0010", "FF"); },
  readBlock: async (key) => multiMemory.get(key) });
assert.equal(crossWrite.error, "readback-mismatch", "final full reread must detect a later write corrupting an already checked block");
const digest = createHash("sha256").update("test firmware bytes").digest("hex");
const manifest = { schemaVersion: 1, kind: "oem-ecu-firmware-manifest", origin: "original", identity,
  sha256: digest, bytes: 19, manufacturerSource: "synthetic test declaration, not original evidence", targetSoftware: "synthetic-new" };
const firmware = firmwarePreparation(manifest, { sha256: digest, bytes: 19, name: "test.bin" }, identity,
  { connected: true, commOk: true, purpose: "diagnostic", selectedDeviceId: "bt:AABBCCDDEEFF" });
assert.equal(firmware.hashMatches, true); assert.equal(firmware.executionEnabled, false);
assert.ok(firmware.blockers.some((block) => block.includes("蓝牙")));
const wired = firmwarePreparation(manifest, { sha256: digest, bytes: 19 }, identity,
  { connected: true, commOk: true, purpose: "diagnostic", selectedDeviceId: "vnci:verified-test" });
assert.ok(wired.blockers.length >= 4, "wired is necessary; file authenticity and programming definitions remain required");
assert.equal(firmwarePreparation(manifest, { sha256: "0".repeat(64), bytes: 19 }, identity, null).hashMatches, false);
assert.throws(() => firmwarePreparation({ ...manifest, origin: "third-party" }, {}, identity, null), /manifest/);
const acquisition = createEngineAcquisition({ limit: 120 });
const run = (runId, stamp, cycle = 1) => ({ runId, simulation: true, engine: { samples: [{ pid: "0C", value: 1000, cycle, capturedUtc: stamp }] } });
acquisition.append(run("one", "2026-10-05T00:00:00Z"));
assert.equal(acquisition.append(run("one", "2026-10-05T00:00:00Z")), false);
acquisition.append(run("two", "2026-10-05T00:00:10Z"));
assert.equal(acquisition.samples[1].elapsedMs, 10000); assert.equal(acquisition.samples[1].cycle, 2);
assert.equal(acquisition.export().samples.length, 2); assert.equal(acquisition.export().liveVerified, false);
const captureFile = path.join(directory, "simulated.pcapng");
const frame = { canId: 0x7e8, extended: false, dataHex: "03410C1F40000000", timestampUs: Date.now() * 1000, timestampSource: "host-chunk-arrival" };
const event = { simulation: true, profileId: "synthetic", runId: "synthetic-run", frame };
const recorder = createDiagnosticCanRecorder({ chooseFile: async () => captureFile });
recorder.onFrame(event);
await assert.rejects(fs.stat(captureFile), /ENOENT/, "recording is off by default");
assert.equal((await recorder.handle({ action: "start", simulation: false })).error, "raw_can_frames_unavailable");
assert.equal((await recorder.handle({ action: "start", simulation: true })).ok, true);
recorder.onFrame({ ...event, frame: { ...frame, timestampUs: 1 } });
assert.equal(recorder.snapshot().recording.frameCount, 0, "old received frames are not retrospectively saved");
recorder.onFrame({ ...event, frame: { ...frame, timestampUs: Date.now() * 1000 + 1000 } });
assert.equal(recorder.snapshot().recording.frameCount, 1);
assert.equal((await recorder.handle({ action: "start", simulation: true })).error, "already-recording");
await recorder.handle({ action: "stop" });
recorder.onFrame(event);
const captured = await fs.readFile(captureFile);
assert.ok(captured.includes(Buffer.from("SYNTHETIC")));
assert.ok(captured.includes(Buffer.from("synthetic-run")), "packet contains actual source context");
assert.equal(recorder.snapshot().recording.frameCount, 1, "frames after stop are excluded");
assert.equal((await recorder.handle({ action: "start", simulation: true })).error, "EEXIST");
const changed = createDiagnosticCanRecorder({ chooseFile: async () => path.join(directory, "changed.pcapng") });
await changed.handle({ action: "start", simulation: true });
changed.onFrame({ ...event, simulation: false });
assert.equal(changed.snapshot().recording.reason, "source-changed");
let choose;
const pending = createDiagnosticCanRecorder({ chooseFile: () => new Promise((resolve) => { choose = resolve; }) });
const opening = pending.handle({ action: "start", simulation: true });
await new Promise((resolve) => setImmediate(resolve));
pending.shutdown(); choose(path.join(directory, "late.pcapng"));
assert.equal((await opening).error, "recording-start-interrupted");
await assert.rejects(fs.stat(path.join(directory, "late.pcapng")), /ENOENT/);
console.log("diagnostic preparation PASS: immutable baseline, integrity, version/coverage rejection, fsynced rehearsal backup, write-once/readback, wired firmware gate, complete batches, explicit recording window and shutdown");
