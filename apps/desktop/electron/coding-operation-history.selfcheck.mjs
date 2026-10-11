import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createDiagnosticPreparation } from "./diagnostic-preparation.mjs";
import { createCodingOperationJournal, codingJournalHash } from "./coding-operation-journal.mjs";

const directory = await fs.mkdtemp(path.join(os.tmpdir(), "coding-history-check-"));
try {
  const operations = path.join(directory, "operations");
  const preparation = () => createDiagnosticPreparation({ directory,
    chooseOpenFile: async () => { throw new Error("History must not open files through a dialog"); },
    saveFile: async () => { throw new Error("History must not export or modify files"); },
    connectionStatus: () => { throw new Error("History must not use a connection"); },
    previewField: () => { throw new Error("History must not create an edit"); } });
  const first = preparation();
  const empty = await first.handle({ action: "operation-history", ecu: "dme" });
  assert.equal(empty.ok, true); assert.equal(empty.history.total, 0);
  for (const request of [
    { action: "operation-history" }, { action: "operation-history", ecu: "dme", id: "forged" },
    ...[-1, 0.5, "0", Number.MAX_SAFE_INTEGER + 1].map(offset => ({ action: "operation-history", ecu: "dme", offset })),
    { action: "list", ecu: "dme", offset: 0 },
  ]) assert.equal((await first.handle(request)).ok, false);
  const journal = createCodingOperationJournal({ directory: operations });
  const metadata = (ecu, changed = false) => ({ mode: "simulation", ecu,
    scopeKey: "a".repeat(64), backupId: "b".repeat(64),
    targets: [{ key: "DID:0001", changed, sha256: "c".repeat(64) }] });
  const complete = async ecu => {
    const data = metadata(ecu), row = await journal.begin(data);
    return journal.append(row.operationId, "completed", {
      scopeTargetSha256: codingJournalHash(JSON.stringify(data.targets.map(({ key, sha256 }) => ({ key, sha256 })))),
    });
  };
  for (let n = 0; n < 45; n++) await complete("dme");
  const other = await complete("gateway");
  const pending = await journal.begin(metadata("dme", true));
  await journal.append(pending.operationId, "write-intent", { key: "DID:0001", targetSha256: "c".repeat(64) });
  const corrupt = await journal.begin(metadata("gateway"));
  await fs.writeFile(path.join(operations, corrupt.operationId, "000000.json"), "{broken");
  const snapshot = async () => {
    const rows = [];
    for (const id of (await fs.readdir(operations)).sort()) {
      for (const name of (await fs.readdir(path.join(operations, id))).sort()) {
        rows.push([id, name, codingJournalHash(await fs.readFile(path.join(operations, id, name)))]);
      }
    }
    return rows;
  };
  const before = await snapshot();
  const page = await first.handle({ action: "operation-history", ecu: "dme" });
  assert.equal(page.ok, true); assert.equal(page.operations.length, 40);
  assert.deepEqual(page.history, { offset: 0, limit: 40, total: 47, currentEcuTotal: 46, unassignedTotal: 1, requiresReconciliation: 2 });
  assert.equal(page.executionEnabled, false); assert.equal(page.writePayload, null);
  const unknown = page.operations.find(row => row.operationId === pending.operationId);
  assert.equal(unknown.phase, "write-intent"); assert.equal(unknown.requiresReconciliation, true);
  assert.equal(unknown.automaticWriteRetry, false); assert.equal(unknown.intendedBlocks, 1); assert.equal(unknown.verifiedBlocks, 0);
  assert.equal(unknown.createdUtc, (await journal.inspect(pending.operationId)).createdUtc);
  assert.equal(unknown.updatedUtc, (await journal.inspect(pending.operationId)).updatedUtc);
  assert.equal(page.operations.some(row => row.operationId === other.operationId), false);
  const broken = page.operations.find(row => row.operationId === corrupt.operationId);
  assert.equal(broken.scope, "unassigned"); assert.equal(broken.ecu, undefined); assert.equal(broken.mode, undefined);
  assert.equal(broken.requiresReconciliation, true); assert.ok(broken.integrityError);
  const tail = await first.handle({ action: "operation-history", ecu: "dme", offset: 40 });
  assert.equal(tail.operations.length, 7);
  assert.equal(new Set([...page.operations, ...tail.operations].map(row => row.operationId)).size, 47);
  assert.ok(page.operations.every(row => row.metadata === undefined && row.targets === undefined && row.lastSha256 === undefined));
  assert.deepEqual(await preparation().handle({ action: "operation-history", ecu: "dme" }), page, "A new production factory reloads the same persisted state");
  const gateway = await first.handle({ action: "operation-history", ecu: "gateway" });
  assert.equal(gateway.history.currentEcuTotal, 1); assert.equal(gateway.history.unassignedTotal, 1);
  assert.equal(gateway.operations.some(row => row.operationId === pending.operationId), false);
  assert.deepEqual(await snapshot(), before, "All history reads leave every persisted event unchanged");
  console.log(JSON.stringify({ ok: true, checks: 9, persistedOperations: 48, pagedRows: 47, currentEcuIsolation: true,
    unassignedIntegrityFailureVisible: true, restartedFactoryStatePreserved: true, readOnly: true, vehicleAccess: false }));
} finally { await fs.rm(directory, { recursive: true, force: true }); }
