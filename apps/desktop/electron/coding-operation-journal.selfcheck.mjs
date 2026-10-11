import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import { execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { createCodingOperationJournal, codingJournalHash as hash } from "./coding-operation-journal.mjs";
import { runCodingRehearsal, codingBlockKey } from "./diagnostic-preparation.mjs";

const root = await fs.mkdtemp(path.join(os.tmpdir(), "flat-six-operation-journal-"));
const identity = { generation: "981", vin: "WP0ZZZ98ZES123456", ecu: "dme", hardware: "hw", software: "sw" };
const backup = { schemaVersion: 2, kind: "ecu-coding-backup", identity, profileId: "known", capturedUtc: "2026-10-11T00:00:00Z", provenance: "simulation",
  expectedBlocks: [{ identifierKind: "DID", identifierHex: "F100" }, { identifierKind: "LID", identifierHex: "A4" }],
  blocks: [{ identifierKind: "DID", identifierHex: "F100", dataHex: "A4" },
    { identifierKind: "LID", identifierHex: "A4", dataHex: "01FF" }] };
const plan = { executionEnabled: false, profileId: "known", identity, blocks: backup.blocks.map((b, i) => ({
  identifierKind: b.identifierKind, identifierHex: b.identifierHex, beforeHex: b.dataHex, targetHex: i ? "02FF" : "A5", changed: true })) };
const shaData = hex => hash(Buffer.from(hex, "hex"));
const metadata = { mode: "simulation", ecu: "dme", scopeKey: "a".repeat(64), backupId: "b".repeat(64),
  targets: plan.blocks.map(b => ({ key: codingBlockKey(b), sha256: shaData(b.targetHex), changed: true })) };
const forKey = (data, i) => ({ key: data.targets[i].key, targetSha256: data.targets[i].sha256 });
const complete = data => ({ scopeTargetSha256: hash(JSON.stringify(data.targets.map(({ key, sha256 }) => ({ key, sha256 })))) });
const journalAt = (name, filesystem) => createCodingOperationJournal({ directory: path.join(root, name), ...(filesystem ? { filesystem } : {}) });
let cases = 0;
try {
  const journal = journalAt("successful"), memory = new Map(backup.blocks.map(b => [codingBlockKey(b), b.dataHex]));
  let writes = 0, backedUp = false;
  const dependencies = { simulation: true, journal, readIdentity: async () => identity, readCurrent: async () => backup,
    persistBackup: async () => { backedUp = true; return { id: metadata.backupId }; },
    writeBlock: async (key, value) => {
      assert.equal(backedUp, true); const [record] = await journal.list(); assert.equal(record.phase, "write-intent");
      assert.equal(record.currentKey, key); assert.equal(record.metadata.backupId, metadata.backupId);
      writes++; memory.set(key, value);
    }, readBlock: async key => memory.get(key) };
  const result = await runCodingRehearsal(plan, dependencies);
  assert.equal(result.ok, true); assert.equal(writes, 2); assert.equal(result.operationState, "completed");
  const reopened = await journalAt("successful").inspect(result.operationId);
  assert.equal(reopened.phase, "completed"); assert.equal(reopened.requiresReconciliation, false);
  assert.deepEqual(reopened.intended, plan.blocks.map(codingBlockKey));
  await assert.rejects(journal.append(result.operationId, "write-intent", forKey(metadata, 0)), /replay-or-order/); cases++;

  const lost = journalAt("lost"); let attempts = 0;
  const interrupted = await runCodingRehearsal(plan, { ...dependencies, journal: lost,
    writeBlock: async () => { attempts++; throw new Error("connection-lost-outcome-unknown"); } });
  assert.equal(interrupted.ok, false); assert.equal(attempts, 1); assert.equal(interrupted.operationState, "outcome-unknown");
  assert.equal(interrupted.recoveryRequired, true); assert.equal(interrupted.automaticWriteRetry, false);
  assert.equal((await journalAt("lost").inspect(interrupted.operationId)).requiresReconciliation, true);
  await assert.rejects(lost.append(interrupted.operationId, "write-intent", forKey(metadata, 0)), /replay-or-order/); cases++;

  const finalIdentityJournal = journalAt("final-identity"); let identities = 0;
  const changedIdentity = await runCodingRehearsal(plan, { ...dependencies, journal: finalIdentityJournal,
    writeBlock: async (key, value) => memory.set(key, value),
    readIdentity: async () => ++identities === 5 ? { ...identity, software: "changed" } : identity });
  assert.equal(changedIdentity.error, "post-write-identity-mismatch"); assert.equal(changedIdentity.operationState, "outcome-unknown"); cases++;

  let failIntentSync = true, writesAfterFailure = 0;
  const failSync = { ...fs, open: async (...args) => {
    const handle = await fs.open(...args); let phase;
    return new Proxy(handle, { get(target, key) {
      if (key === "writeFile") return async (value, ...rest) => { phase = JSON.parse(value).event?.phase; return target.writeFile(value, ...rest); };
      if (key === "sync") return async () => { if (phase === "write-intent" && failIntentSync) { failIntentSync = false; throw new Error("injected-intent-fsync-failure"); } return target.sync(); };
      const value = target[key]; return typeof value === "function" ? value.bind(target) : value;
    } });
  } };
  const blocked = await runCodingRehearsal(plan, { ...dependencies, journal: journalAt("fsync", failSync), writeBlock: async () => { writesAfterFailure++; } });
  assert.equal(writesAfterFailure, 0); assert.equal(blocked.writeAttemptCount, 0); assert.equal(blocked.operationState, "failed-before-write"); cases++;

  let failUnlink = true;
  const failAfterPublish = { ...fs, unlink: async file => {
    if (failUnlink && String(file).includes(".pending-")) {
      const stored = JSON.parse(await fs.readFile(file, "utf8"));
      if (stored.event.phase === "write-intent") { failUnlink = false; throw new Error("injected-post-publish-cleanup-failure"); }
    }
    return fs.unlink(file);
  } };
  const ambiguous = await runCodingRehearsal(plan, { ...dependencies, journal: journalAt("published", failAfterPublish), writeBlock: async () => { throw new Error("must-not-dispatch"); } });
  assert.equal(ambiguous.writeAttemptCount, 0); assert.equal(ambiguous.operationState, "outcome-unknown");
  assert.equal(ambiguous.recoveryRequired, true); assert.equal((await journalAt("published").inspect(ambiguous.operationId)).intended.length, 1); cases++;

  const vehicle = journalAt("vehicle-software-only"), vehicleMetadata = { ...metadata, mode: "vehicle" };
  const contenders = await Promise.allSettled([vehicle.begin(vehicleMetadata), journalAt("vehicle-software-only").begin(vehicleMetadata)]);
  assert.equal(contenders.filter(r => r.status === "fulfilled").length, 1);
  const winner = contenders.find(r => r.status === "fulfilled").value;
  await vehicle.append(winner.operationId, "write-intent", forKey(metadata, 0));
  await assert.rejects(journalAt("vehicle-software-only").begin(vehicleMetadata), /unresolved-prior-write/);
  await vehicle.append(winner.operationId, "outcome-unknown", { error: "software-only abandoned request; no transport" });
  await assert.rejects(journalAt("vehicle-software-only").begin(vehicleMetadata), /unresolved-prior-write/); cases++;

  const racing = journalAt("append-race"), initial = await racing.begin(metadata);
  const appenders = await Promise.allSettled([racing.append(initial.operationId, "write-intent", forKey(metadata, 0)),
    journalAt("append-race").append(initial.operationId, "write-intent", forKey(metadata, 1))]);
  assert.equal(appenders.filter(r => r.status === "fulfilled").length, 1);
  assert.equal((await racing.inspect(initial.operationId)).intended.length, 1); cases++;

  const premature = journalAt("premature"), operation = await premature.begin(metadata);
  await assert.rejects(premature.append(operation.operationId, "completed", complete(metadata)), /scope-unverified/);
  await assert.rejects(premature.append(operation.operationId, "block-verified", forKey(metadata, 0)), /verification-order/);
  await premature.append(operation.operationId, "write-intent", forKey(metadata, 0));
  await premature.append(operation.operationId, "write-returned", forKey(metadata, 0));
  await premature.append(operation.operationId, "block-verified", forKey(metadata, 0));
  await assert.rejects(premature.append(operation.operationId, "write-intent", forKey(metadata, 0)), /replay-or-order/); cases++;

  const unchanged = { ...vehicleMetadata, targets: metadata.targets.map(t => ({ ...t, changed: false })) };
  const noWrites = journalAt("no-writes"), noop = await noWrites.begin(unchanged);
  await noWrites.append(noop.operationId, "completed", complete(unchanged));
  assert.equal((await noWrites.begin(unchanged)).phase, "backed-up"); cases++;

  const damaged = journalAt("damaged"), original = await damaged.begin(metadata);
  const eventFile = path.join(root, "damaged", original.operationId, "000000.json");
  const originalBytes = await fs.readFile(eventFile); const bad = JSON.parse(originalBytes); bad.event.data.backupId = "c".repeat(64);
  await fs.writeFile(eventFile, JSON.stringify(bad)); await assert.rejects(damaged.inspect(original.operationId), /integrity/);
  assert.equal((await damaged.list())[0].requiresReconciliation, true);
  await assert.rejects(damaged.begin(vehicleMetadata), /unresolved-prior-write/);
  await fs.writeFile(eventFile, originalBytes); await damaged.append(original.operationId, "write-intent", forKey(metadata, 0));
  await fs.unlink(eventFile); await assert.rejects(damaged.inspect(original.operationId), /integrity/); cases++;

  const snapshot = journalAt("snapshot"), input = structuredClone(metadata), started = snapshot.begin(input);
  input.targets[0].sha256 = "d".repeat(64); const stored = await started;
  assert.equal(stored.metadata.targets[0].sha256, metadata.targets[0].sha256); cases++;

  const moduleFile = process.env.FLATSIX_INSTALLED_APP
    ? path.join(process.env.FLATSIX_INSTALLED_APP, "apps/desktop/electron/coding-operation-journal.mjs")
    : path.join(import.meta.dirname, "coding-operation-journal.mjs");
  const childSource = 'const {createCodingOperationJournal}=await import(process.argv[1]); console.log(JSON.stringify(await createCodingOperationJournal({directory:process.argv[2]}).list()));';
  const cold = JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", childSource,
    pathToFileURL(moduleFile).href, path.join(root, "vehicle-software-only")], { encoding: "utf8", timeout: 10000 }));
  assert.equal(cold.find(o => o.operationId === winner.operationId).phase, "outcome-unknown");
  assert.equal(cold.find(o => o.operationId === winner.operationId).automaticWriteRetry, false); cases++;

  const maximum = journalAt("maximum-metadata"), many = { ...metadata, targets: Array.from({ length: 256 }, (_, i) => ({
    key: String(i).padStart(3, "0") + "测".repeat(237), sha256: "a".repeat(64), changed: false })) };
  const large = await maximum.begin(many);
  assert.equal((await journalAt("maximum-metadata").inspect(large.operationId)).metadata.targets.length, 256);
  await maximum.append(large.operationId, "completed", complete(many)); cases++;

  assert.equal(result.simulation, true); assert.equal(result.liveVerified, false);
  await assert.rejects(runCodingRehearsal(plan, { ...dependencies, simulation: false }), /rehearsal-only/); cases++;
  console.log(JSON.stringify({ ok: true, cases, durableIntentBeforeDispatch: true, restartStateVerified: true,
    concurrencyArbitrated: true, separateProcessReadVerified: true, sourceVehicleIO: false, actualVehicleWrite: false, powerLossDurabilityVerified: false }));
} finally { await fs.rm(root, { recursive: true, force: true }); }
