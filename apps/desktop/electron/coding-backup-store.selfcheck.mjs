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
  const desktop = path.join(directory, "Desktop"); await fs.mkdir(desktop);
  const exported = await store.publishOriginal(later.identityKey, desktop);
  assert.equal(exported.id, first.id, "export the first baseline, even when requested with a later snapshot");
  assert.equal(path.dirname(exported.filePath), desktop);
  assert.match(path.basename(exported.filePath), /^WP0ZZZ98ZES000000_dme_2026-10-10T00-00-00-000Z\.json$/);
  const exportedBytes = await fs.readFile(exported.filePath);
  assert.equal(JSON.parse(exportedBytes).blocks[0].dataHex, "01");
  assert.equal(exported.freshReadProven, false, "an imported baseline never becomes a fresh vehicle read through export");
  const updatedVersion = await store.persist({ ...backup("33"), identity: { ...identity, software: "later-sw" },
    capturedUtc: "2026-10-11T01:02:03Z" });
  assert.equal((await store.publishOriginal(updatedVersion.identityKey, desktop)).id, first.id,
    "a later software version cannot create a second first desktop backup for this vehicle/unit");
  assert.equal((await store.firstOriginal(updatedVersion.identityKey)).sha256, first.id,
    "restore must resolve the unit's first original, not a newer version's baseline");
  assert.equal((await store.list("dme")).filter(row => row.original).length, 1);
  assert.deepEqual(await fs.readdir(desktop), [path.basename(exported.filePath)]);
  const separate = await store.publishOriginal(foreign.identityKey, desktop);
  assert.equal(separate.id, foreign.id); assert.equal((await fs.readdir(desktop)).length, 2);
  const desktopPointerName = (await fs.readdir(path.join(directory, "normal"))).filter(name => name.startsWith("desktop-original-"));
  let originalPointer;
  for (const name of desktopPointerName) {
    const candidate = path.join(directory, "normal", name);
    if (JSON.parse(await fs.readFile(candidate)).id === first.id) originalPointer = candidate;
  }
  assert.ok(originalPointer);
  const exportPointerBytes = await fs.readFile(originalPointer);
  for (const forged of [
    { id: first.id, fileName: "../outside.json" },
    { id: foreign.id, fileName: path.basename(separate.filePath) },
    { id: later.id, fileName: path.basename(exported.filePath) },
  ]) {
    await fs.writeFile(originalPointer, JSON.stringify(forged));
    await assert.rejects(store.publishOriginal(first.identityKey, desktop), /backup-desktop-pointer-invalid/);
    assert.deepEqual(await fs.readFile(exported.filePath), exportedBytes);
  }
  await fs.writeFile(originalPointer, exportPointerBytes);
  await fs.writeFile(exported.filePath, "damaged original");
  await assert.rejects(store.publishOriginal(first.identityKey, desktop), /backup-desktop-integrity-failed/);
  assert.equal(await fs.readFile(exported.filePath, "utf8"), "damaged original", "a damaged first file is never overwritten");
  await fs.writeFile(exported.filePath, exportedBytes);
  await fs.unlink(exported.filePath);
  assert.equal((await store.publishOriginal(updatedVersion.identityKey, desktop)).filePath, exported.filePath);
  assert.deepEqual(await fs.readFile(exported.filePath), exportedBytes, "retry republishes exactly the reserved first baseline");

  const simulatedOnly = create("simulated-desktop"), simulated = await simulatedOnly.persist(backup("11", "simulation"));
  await assert.rejects(simulatedOnly.publishOriginal(simulated.identityKey, desktop), /backup-baseline-missing/);
  await assert.rejects(store.publishOriginal(first.identityKey, path.join(directory, "missing-desktop")), /ENOENT/);
  await assert.rejects(store.publishOriginal(first.identityKey, exported.filePath), /backup-desktop-invalid/);

  for (const fault of ["writeFile", "sync"]) {
    const desk = path.join(directory, `Desktop-${fault}`); await fs.mkdir(desk);
    let failDesktop = true;
    const resilient = create(`store-desktop-${fault}`, { ...fs, open: async (file, flags) => {
      const handle = await fs.open(file, flags);
      if (path.dirname(file) !== desk || flags !== "wx" || !failDesktop) return handle;
      return { writeFile: async content => {
        if (fault === "writeFile") { await handle.writeFile(content.subarray(0, 13)); failDesktop = false; throw new Error("injected-desktop-full"); }
        await handle.writeFile(content);
      }, sync: async () => {
        if (fault === "sync") { failDesktop = false; throw new Error("injected-desktop-full"); }
        await handle.sync();
      }, close: () => handle.close() };
    } });
    const old = await resilient.persist(backup("44"));
    await assert.rejects(resilient.publishOriginal(old.identityKey, desk), /injected-desktop-full/);
    assert.deepEqual(await fs.readdir(desk), [], "a partial desktop save leaves no final file or temporary residue");
    const newer = await resilient.persist({ ...backup("55"), identity: { ...identity, software: "newer" } });
    const retry = await resilient.publishOriginal(newer.identityKey, desk);
    assert.equal(retry.id, old.id, "failed desktop publication keeps its original reservation for retry");
    assert.equal(JSON.parse(await fs.readFile(retry.filePath)).blocks[0].dataHex, "44");
  }

  const raceDesktop = path.join(directory, "Desktop-race"); await fs.mkdir(raceDesktop);
  const raceFirst = create("store-desktop-race"), raceSecond = create("store-desktop-race");
  const raceA = await raceFirst.persist(backup("66"));
  const raceB = await raceSecond.persist({ ...backup("77"), identity: { ...identity, software: "race-sw" },
    capturedUtc: "2026-10-11T01:02:03Z" });
  const winners = await Promise.all([raceFirst.publishOriginal(raceA.identityKey, raceDesktop), raceSecond.publishOriginal(raceB.identityKey, raceDesktop)]);
  assert.equal(winners[0].id, winners[1].id); assert.equal((await fs.readdir(raceDesktop)).length, 1);
  assert.ok([raceA.id, raceB.id].includes(winners[0].id));
  assert.equal(winners[0].id, raceA.id, "the first completed persist wins before either Desktop export");
  const concurrentA = create("concurrent-versions"), concurrentB = create("concurrent-versions");
  const versions = await Promise.all([concurrentA.persist(backup("71")), concurrentB.persist({ ...backup("72"),
    identity: { ...identity, software: "concurrent-sw" } })]);
  const originals = await Promise.all(versions.map(row => concurrentA.firstOriginal(row.identityKey)));
  assert.equal(originals[0].sha256, originals[1].sha256);
  assert.ok(versions.some(row => row.id === originals[0].sha256));
  assert.equal((await concurrentB.list("dme")).filter(row => row.original).length, 1);

  const unitPointer = async name => {
    const folder = path.join(directory, name);
    return path.join(folder, (await fs.readdir(folder)).find(file => /^original-[a-f0-9]{64}\.json$/.test(file)));
  };
  const deferred = create("deferred-first-export");
  const deferredFirst = await deferred.persist(backup("81"));
  const deferredNew = await deferred.persist({ ...backup("82"), identity: { ...identity, software: "changed" },
    capturedUtc: "2025-01-01T00:00:00Z" });
  const deferredDesktop = path.join(directory, "Desktop-deferred"); await fs.mkdir(deferredDesktop);
  assert.equal((await deferred.publishOriginal(deferredNew.identityKey, deferredDesktop)).id, deferredFirst.id,
    "neither a later version nor an earlier imported timestamp can change the first original");
  const otherVehicle = await deferred.persist({ ...backup("83"), identity: { ...identity, vin: "WP0ZZZ98ZES000001" } });
  assert.equal((await deferred.firstOriginal(otherVehicle.identityKey)).sha256, otherVehicle.id);
  assert.equal((await deferred.list("dme")).filter(row => row.original).length, 2, "first originals remain vehicle-specific");

  // Upgrade old stores without inventing the order of imported timestamps.
  const legacy = create("legacy-single"), old = await legacy.persist(backup("91"));
  await fs.unlink(await unitPointer("legacy-single"));
  const migrated = await legacy.persist({ ...backup("92"), identity: { ...identity, software: "next" } });
  assert.equal((await legacy.firstOriginal(migrated.identityKey)).sha256, old.id);
  const legacyDesktop = create("legacy-desktop");
  const oldDesktop = await legacyDesktop.persist(backup("A1"));
  const desk = path.join(directory, "Desktop-legacy"); await fs.mkdir(desk);
  const reserved = await legacyDesktop.publishOriginal(oldDesktop.identityKey, desk);
  const newDesktop = await legacyDesktop.persist({ ...backup("A2"), identity: { ...identity, software: "next" } });
  await fs.unlink(await unitPointer("legacy-desktop"));
  assert.equal((await legacyDesktop.firstOriginal(newDesktop.identityKey)).sha256, oldDesktop.id);
  assert.equal((await legacyDesktop.publishOriginal(newDesktop.identityKey, desk)).filePath, reserved.filePath);
  assert.equal(JSON.parse(await fs.readFile(await unitPointer("legacy-desktop"))).id, oldDesktop.id);

  const ambiguous = create("legacy-ambiguous");
  const one = await ambiguous.persist(backup("B1"));
  const two = await ambiguous.persist({ ...backup("B2"), identity: { ...identity, software: "next" } });
  await fs.unlink(await unitPointer("legacy-ambiguous"));
  const legacyBefore = (await fs.readdir(path.join(directory, "legacy-ambiguous"))).sort();
  await assert.rejects(ambiguous.firstOriginal(one.identityKey), /backup-original-ambiguous/);
  await assert.rejects(ambiguous.publishOriginal(two.identityKey, desk), /backup-original-ambiguous/);
  await assert.rejects(ambiguous.persist(backup("B3")), /backup-original-ambiguous/);
  assert.deepEqual((await fs.readdir(path.join(directory, "legacy-ambiguous"))).sort(), legacyBefore);

  const canonical = await unitPointer("normal"), canonicalBytes = await fs.readFile(canonical);
  for (const forged of [{ id: foreign.id }, { id: simulation.id }, { id: "../escape" }, { id: first.id, extra: true }]) {
    await fs.writeFile(canonical, JSON.stringify(forged));
    await assert.rejects(store.firstOriginal(first.identityKey), /backup-original-/);
    await assert.rejects(store.persist(backup("C1")), /backup-original-/);
  }
  await fs.writeFile(canonical, canonicalBytes);
  assert.equal((await store.firstOriginal(updatedVersion.identityKey)).sha256, first.id);
  assert.ok(!(await fs.readdir(path.join(directory, "normal"))).some((name) => name.startsWith(".pending-")));
  console.log(JSON.stringify({ ok: true, scope: "coding-backup-storage", deviceIO: false, vehicleIO: false,
    checks: ["simulation-exclusion", "immutable-first-baseline", "concurrent-publication", "partial-write", "fsync-failure",
      "pointer-failure-recovery", "foreign-identity-pointer", "simulation-pointer", "invalid-pointer", "retry-integrity",
      "desktop-first-vehicle-unit", "desktop-version-reuse", "desktop-damage-no-overwrite", "desktop-retry-reservation",
      "desktop-write-sync-failure", "desktop-concurrent-publication", "desktop-pointer-scope-and-path", "desktop-no-fresh-read-promotion",
      "unit-first-across-versions", "deferred-first-desktop-export", "imported-time-not-publication-order", "separate-vehicle-original",
      "single-legacy-baseline-migration", "legacy-desktop-reservation-migration", "ambiguous-legacy-order-rejected", "canonical-pointer-integrity",
      "concurrent-version-first-original"] }));
} finally { await fs.rm(directory, { recursive: true, force: true }); }
