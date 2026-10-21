/** Real immutable backup storage failures must prevent even a simulated dispatch.
 * Native filesystem operations are faulted only in this test's private directory.
 * Imported fixtures declare scope; they are not fresh vehicle reads or write authority.
 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const appRoot = path.resolve(process.env.FLATSIX_ACCEPT_APP_ROOT || path.join(import.meta.dirname, ".."));
const moduleAt = name => pathToFileURL(path.join(appRoot, "apps/desktop/electron", name)).href;
const { createCodingBackupStore } = await import(moduleAt("coding-backup-store.mjs"));
const { normalizeCodingBackup, runCodingRehearsal } = await import(moduleAt("diagnostic-preparation.mjs"));
const directory = await fs.mkdtemp(path.join(os.tmpdir(), "flat-six-backup-failure-"));
const identity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "SYNTHETIC-HW", software: "SYNTHETIC-SW" };
const makeBackup = dataHex => ({ schemaVersion: 2, kind: "ecu-coding-backup", identity, profileId: "SYNTHETIC-only",
  capturedUtc: "2026-10-12T00:00:00Z", provenance: "imported",
  expectedBlocks: [{ identifierKind: "DID", identifierHex: "F100" }],
  blocks: [{ identifierKind: "DID", identifierHex: "F100", dataHex }] });
const current = makeBackup("02FF");
const plan = { identity, profileId: current.profileId, executionEnabled: false,
  blocks: [{ identifierKind: "DID", identifierHex: "F100", beforeHex: "02FF", targetHex: "03FF", changed: true }] };
const cases = [];
try {
  for (const existing of [false, true]) for (const stage of (existing ? ["snapshot", "baseline"] : ["snapshot", "baseline", "unit-original"]))
    for (const fault of ["open", "writeFile", "sync", "close", "link", "unlink"]) {
      const label = `${existing ? "existing" : "first"}-${stage}-${fault}`, scope = path.join(directory, label);
      const plain = createCodingBackupStore({ directory: scope, normalizeBackup: normalizeCodingBackup });
      const initial = existing ? await plain.persist(makeBackup("01FF")) : null;
      const before = initial ? await fs.readFile(path.join(scope, `baseline-${initial.identityKey}.json`)) : null;
      let publication = 0, selected = false, injected = 0;
      const target = stage === "snapshot" ? 1 : stage === "baseline" ? 2 : 3;
      const fail = () => { injected++; throw new Error(`injected-${label}`); };
      const filesystem = { ...fs,
        open: async (file, flags) => {
          if (flags !== "wx") return fs.open(file, flags);
          selected = ++publication === target;
          const thisSelected = selected;
          if (thisSelected && fault === "open") fail();
          const handle = await fs.open(file, flags);
          return {
            writeFile: async content => {
              if (thisSelected && fault === "writeFile") { await handle.writeFile(content.slice(0, 13)); fail(); }
              return handle.writeFile(content);
            },
            sync: async () => { if (thisSelected && fault === "sync") fail(); return handle.sync(); },
            close: async () => { await handle.close(); if (thisSelected && fault === "close") fail(); },
          };
        },
        link: async (source, destination) => { if (selected && fault === "link") fail(); return fs.link(source, destination); },
        unlink: async file => { if (selected && fault === "unlink") fail(); return fs.unlink(file); },
      };
      const store = createCodingBackupStore({ directory: scope, normalizeBackup: normalizeCodingBackup, filesystem });
      let writes = 0, journalBegins = 0, memory = "02FF";
      const dependencies = { simulation: true, readIdentity: async () => identity, readCurrent: async () => current,
        persistBackup: value => store.persist(value), journal: { begin: async () => { journalBegins++; throw new Error("unexpected-journal"); } },
        writeBlock: async (_key, value) => { writes++; memory = value; }, readBlock: async () => memory };
      const result = await runCodingRehearsal(plan, dependencies);
      assert.equal(injected, 1, label);
      assert.equal(result.ok, false, label); assert.equal(result.error, `injected-${label}`, label);
      assert.equal(result.writeAttemptCount, 0, label); assert.equal(writes, 0, label);
      assert.equal(memory, "02FF", label); assert.equal(journalBegins, 0, label);
      if (initial) {
        assert.deepEqual(await fs.readFile(path.join(scope, `baseline-${initial.identityKey}.json`)), before, label);
        assert.equal((await plain.baseline(initial.identityKey)).sha256, initial.id, label);
        assert.equal((await plain.load(initial.id)).backup.blocks[0].dataHex, "01FF", label);
      }
      // A separate explicit retry uses the unmodified real filesystem and must
      // verify existing immutable files rather than overwrite the first baseline.
      const baselineBeforeRetry = (await plain.list("dme")).find(row => row.original)?.id;
      const { journal: _journal, ...retryDependencies } = dependencies;
      const retried = await runCodingRehearsal(plan, { ...retryDependencies, persistBackup: value => plain.persist(value) });
      assert.equal(retried.ok, true, label); assert.equal(retried.writeAttemptCount, 1, label);
      assert.equal(writes, 1, label); assert.equal(memory, "03FF", label);
      const original = (await plain.list("dme")).find(row => row.original);
      assert.ok(original, label);
      if (initial || baselineBeforeRetry) assert.equal(original.id, initial?.id || baselineBeforeRetry, label);
      cases.push({ label, failedOperationWrites: 0, existingBaselinePreserved: initial ? true : null, explicitRetryWrites: 1 });
    }
  console.log(JSON.stringify({ passed: true, appRoot, cases, caseCount: cases.length,
    actualFilesystem: true, filesystemFaultInjected: true, simulation: true,
    freshVehicleRead: false, deviceIO: false, vehicleIO: false, windowsExecuted: false }));
} finally { await fs.rm(directory, { recursive: true, force: true }); }
