import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { createCodingBackupStore } from "./coding-backup-store.mjs";
import { normalizeCodingBackup } from "./diagnostic-preparation.mjs";

const directory = await fs.mkdtemp(path.join(os.tmpdir(), "flat-six-coding-store-"));
const identity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "fixture-hw", software: "fixture-sw" };
const backup = (value = "01", provenance = "imported") => ({ schemaVersion: 2, kind: "ecu-coding-backup", identity,
  profileId: "fixture-only", capturedUtc: "2026-10-10T00:00:00Z", provenance,
  expectedBlocks: [{ identifierKind: "LID", identifierHex: "10" }],
  blocks: [{ identifierKind: "LID", identifierHex: "10", dataHex: value }] });
const create = (name, filesystem = fs) => createCodingBackupStore({ directory: path.join(directory, name), normalizeBackup: normalizeCodingBackup, filesystem });
try {
  const store = create("normal");
  const simulation = await store.persist(backup("FF", "simulation"));
  assert.equal(await store.baseline(simulation.identityKey), null, "a rehearsal cannot occupy the first imported baseline");
  assert.equal((await store.list("dme"))[0].original, false);
  const first = await store.persist(backup());
  const later = await store.persist(backup("02"));
  assert.equal((await store.baseline(first.identityKey)).sha256, first.id);
  assert.equal((await store.load(first.id)).backup.blocks[0].dataHex, "01");
  assert.equal((await store.load(later.id)).backup.blocks[0].dataHex, "02");
  assert.deepEqual(await store.persist(backup()), first, "identical retries verify an existing snapshot");

  // Exercise competing independent writers, rather than just one serialized IPC.
  const a = create("race"), b = create("race");
  const contenders = await Promise.all([a.persist(backup("11")), b.persist(backup("22"))]);
  const winner = await a.baseline(contenders[0].identityKey);
  assert.ok(contenders.some((row) => row.id === winner.sha256));
  assert.equal((await b.list("dme")).filter((row) => row.original).length, 1);
  assert.equal((await a.list("dme")).length, 2);

  // Fail after writing real partial bytes and before publication; retry must work.
  for (const fault of ["writeFile", "sync"]) {
    let fail = true;
    const filesystem = { ...fs, open: async (file, flags) => {
      const handle = await fs.open(file, flags);
      if (flags !== "wx" || !fail) return handle;
      return {
        writeFile: async (content) => { if (fault === "writeFile") { await handle.writeFile(content.slice(0, 13)); fail = false; throw new Error("injected-disk-full"); } await handle.writeFile(content); },
        sync: async () => { if (fault === "sync") { fail = false; throw new Error("injected-fsync-failed"); } await handle.sync(); },
        close: () => handle.close(),
      };
    } };
    const broken = create(fault, filesystem);
    await assert.rejects(broken.persist(backup()), /injected-/);
    assert.deepEqual(await fs.readdir(path.join(directory, fault)), [], "failed writes leave neither partial final files nor owned temporary files");
    const retried = await broken.persist(backup());
    assert.equal((await broken.baseline(retried.identityKey)).sha256, retried.id);
  }

  // A complete snapshot survives a baseline-publication failure, but is not an original.
  let failPointer = true;
  const interrupted = create("pointer-failure", { ...fs, link: async (source, target) => {
    if (path.basename(target).startsWith("baseline-") && failPointer) { failPointer = false; throw new Error("injected-pointer-failure"); }
    return fs.link(source, target);
  } });
  await assert.rejects(interrupted.persist(backup()), /injected-pointer-failure/);
  const retained = await interrupted.list("dme");
  assert.equal(retained.length, 1); assert.equal(retained[0].original, false);
  assert.equal((await interrupted.load(retained[0].id)).backup.blocks[0].dataHex, "01");
  const recovered = await interrupted.persist(backup());
  assert.equal((await interrupted.baseline(recovered.identityKey)).sha256, recovered.id);

  // A valid snapshot from another ECU/version cannot be used via a forged pointer.
  const foreign = await store.persist({ ...backup(), identity: { ...identity, ecu: "gateway" } });
  const pointer = path.join(directory, "normal", `baseline-${first.identityKey}.json`);
  const pointerBefore = await fs.readFile(pointer);
  await fs.writeFile(pointer, JSON.stringify({ id: foreign.id }));
  await assert.rejects(store.baseline(first.identityKey), /backup-baseline-mismatch/);
  await assert.rejects(store.persist(backup("03")), /backup-baseline-mismatch/);
  assert.deepEqual(await fs.readFile(pointer), Buffer.from(JSON.stringify({ id: foreign.id })), "invalid pointers are never silently overwritten");
  await fs.writeFile(pointer, pointerBefore);
  await fs.writeFile(pointer, JSON.stringify({ id: simulation.id }));
  await assert.rejects(store.baseline(first.identityKey), /backup-baseline-mismatch/);
  await fs.writeFile(pointer, JSON.stringify({ id: "../escape" }));
  await assert.rejects(store.baseline(first.identityKey), /backup-baseline-invalid/);
  await fs.writeFile(pointer, pointerBefore);
  assert.equal((await store.baseline(first.identityKey)).sha256, first.id);
  assert.ok(!(await fs.readdir(path.join(directory, "normal"))).some((name) => name.startsWith(".pending-")));
  console.log(JSON.stringify({ ok: true, scope: "coding-backup-storage", deviceIO: false, vehicleIO: false,
    checks: ["simulation-exclusion", "immutable-first-baseline", "concurrent-publication", "partial-write", "fsync-failure",
      "pointer-failure-recovery", "foreign-identity-pointer", "simulation-pointer", "invalid-pointer", "retry-integrity"] }));
} finally { await fs.rm(directory, { recursive: true, force: true }); }
