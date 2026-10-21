import fs from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const validId = (id) => typeof id === "string" && /^[a-f0-9]{64}$/.test(id);
const vehicleUnitKey = (backup) => hash(JSON.stringify({ generation: backup.identity.generation,
  vin: backup.identity.vin, ecu: backup.identity.ecu }));
const originalFileName = (backup) => {
  const ecu = backup.identity.ecu.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim().replace(/[. ]+$/, "").slice(0, 60) || "ECU";
  const time = new Date(backup.capturedUtc).toISOString().replace(/[:.]/g, "-");
  return `${backup.identity.vin}_${ecu}_${time}.json`;
};

/** Immutable snapshots shared by preparation and the original-coding reader.
 * Imports remain declared scopes, and simulations never establish a baseline.
 * Caller-owned definitions/transport qualification must precede a vehicle read;
 * this store cannot promote imported values to fresh vehicle data. */
export function createCodingBackupStore({ directory, normalizeBackup, normalizeStoredBackup = normalizeBackup, filesystem = fs }) {
  async function readJson(file) {
    const handle = await filesystem.open(file, "r");
    try {
      if ((await handle.stat()).size > 4 * 1024 * 1024) throw new Error("preparation-file-too-large");
      return JSON.parse(await handle.readFile("utf8"));
    } finally { await handle.close(); }
  }
  async function load(id) {
    if (!validId(id)) throw new Error("backup-id-invalid");
    const stored = await readJson(path.join(directory, `${id}.json`));
    const backup = normalizeStoredBackup(stored.backup);
    if (hash(JSON.stringify(backup)) !== stored.sha256 || stored.sha256 !== id
      || hash(JSON.stringify({ identity: backup.identity, profileId: backup.profileId })) !== stored.identityKey) {
      throw new Error("backup-integrity-failed");
    }
    return { ...stored, backup };
  }
  async function baseline(identityKey) {
    if (!validId(identityKey)) throw new Error("backup-identity-key-invalid");
    let pointer;
    try { pointer = await readJson(path.join(directory, `baseline-${identityKey}.json`)); }
    catch (error) { if (error.code === "ENOENT") return null; throw error; }
    if (!pointer || Object.keys(pointer).length !== 1 || !validId(pointer.id)) throw new Error("backup-baseline-invalid");
    const stored = await load(pointer.id);
    if (stored.identityKey !== identityKey || !['imported', 'source-vehicle-read'].includes(stored.backup.provenance)) throw new Error("backup-baseline-mismatch");
    return stored;
  }
  async function unitOriginal(backup) {
    const key = vehicleUnitKey(backup);
    let pointer;
    try { pointer = await readJson(path.join(directory, `original-${key}.json`)); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    if (pointer !== undefined) {
      if (!pointer || Object.keys(pointer).length !== 1 || !validId(pointer.id)) throw new Error("backup-original-invalid");
      const stored = await load(pointer.id);
      if (vehicleUnitKey(stored.backup) !== key || (await baseline(stored.identityKey))?.sha256 !== stored.sha256)
        throw new Error("backup-original-mismatch");
      return stored;
    }
    // Older stores reserved the first Desktop file, but kept one internal
    // baseline per software/profile. Preserve that explicit reservation.
    try { pointer = await readJson(path.join(directory, `desktop-original-${key}.json`)); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    if (pointer !== undefined) {
      if (!pointer || Object.keys(pointer).length !== 2 || !validId(pointer.id)) throw new Error("backup-desktop-pointer-invalid");
      const stored = await load(pointer.id);
      if (vehicleUnitKey(stored.backup) !== key || pointer.fileName !== originalFileName(stored.backup)
        || (await baseline(stored.identityKey))?.sha256 !== stored.sha256) throw new Error("backup-desktop-pointer-invalid");
      return stored;
    }
    // Imported capture times do not establish publication order. With several
    // legacy version baselines and no first-file reservation, do not guess.
    const matches = [];
    for (const name of await filesystem.readdir(directory)) {
      if (!/^baseline-[a-f0-9]{64}\.json$/.test(name)) continue;
      const stored = await baseline(name.slice(9, -5));
      if (stored && vehicleUnitKey(stored.backup) === key) matches.push(stored);
    }
    if (matches.length > 1) throw new Error("backup-original-ambiguous");
    return matches[0] || null;
  }
  async function firstOriginal(identityKey) {
    const requested = await baseline(identityKey);
    return requested ? unitOriginal(requested.backup) : null;
  }
  async function findOriginal(input) {
    const backup = normalizeBackup(input);
    try { return await unitOriginal(backup); }
    catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }
  async function reserveOriginal(stored) {
    const key = vehicleUnitKey(stored.backup);
    const file = path.join(directory, `original-${key}.json`);
    let exists = true;
    try { await readJson(file); }
    catch (error) { if (error.code === "ENOENT") exists = false; else throw error; }
    if (exists) { await unitOriginal(stored.backup); return; }
    try { await publish(file, JSON.stringify({ id: stored.sha256 })); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
    if (!await unitOriginal(stored.backup)) throw new Error("backup-baseline-missing");
  }
  async function publish(file, content) {
    // A failed write/fsync never exposes a partial final file. link is exclusive
    // on both NTFS and POSIX; rename could overwrite the first immutable baseline.
    const temporary = path.join(path.dirname(file), `.pending-${randomUUID()}.json`);
    let created = false;
    try {
      const handle = await filesystem.open(temporary, "wx"); created = true;
      try { await handle.writeFile(content); await handle.sync(); } finally { await handle.close(); }
      await filesystem.link(temporary, file);
    } finally { if (created) await filesystem.unlink(temporary); }
  }
  async function persist(input) {
    const backup = normalizeBackup(input);
    await filesystem.mkdir(directory, { recursive: true });
    const sha256 = hash(JSON.stringify(backup));
    const identityKey = hash(JSON.stringify({ identity: backup.identity, profileId: backup.profileId }));
    const stored = { backup, sha256, identityKey };
    // A malformed or foreign pointer must fail before being used for recovery.
    await baseline(identityKey);
    const eligible = ['imported', 'source-vehicle-read'].includes(backup.provenance);
    const original = eligible ? await unitOriginal(backup) : null;
    if (original) await reserveOriginal(original);
    try { await publish(path.join(directory, `${sha256}.json`), JSON.stringify(stored)); }
    catch (error) { if (error.code !== "EEXIST") throw error; await load(sha256); }
    if (eligible) {
      try { await publish(path.join(directory, `baseline-${identityKey}.json`), JSON.stringify({ id: sha256 })); }
      catch (error) { if (error.code !== "EEXIST") throw error; }
      // Verify the winner even when another store instance published it first.
      const versionBaseline = await baseline(identityKey);
      if (!versionBaseline) throw new Error("backup-baseline-missing");
      await reserveOriginal(original || versionBaseline);
    }
    return { id: sha256, identityKey };
  }
  async function list(ecu) {
    let files;
    try { files = await filesystem.readdir(directory); }
    catch (error) { if (error.code === "ENOENT") return []; throw error; }
    const rows = [];
    for (const name of files.filter((n) => /^[a-f0-9]{64}\.json$/.test(n))) {
      const stored = await load(name.slice(0, -5));
      if (ecu && stored.backup.identity.ecu !== ecu) continue;
      const original = await firstOriginal(stored.identityKey);
      rows.push({ id: stored.sha256, identity: stored.backup.identity, profileId: stored.backup.profileId,
        capturedUtc: stored.backup.capturedUtc, blockCount: stored.backup.blocks.length,
        original: original?.sha256 === stored.sha256, provenance: stored.backup.provenance, freshReadProven: stored.backup.freshReadProven === true });
    }
    return rows.sort((a, b) => b.capturedUtc.localeCompare(a.capturedUtc));
  }
  async function publishOriginal(identityKey, desktopDirectory) {
    const requested = await firstOriginal(identityKey);
    if (!requested) throw new Error("backup-baseline-missing");
    if (typeof desktopDirectory !== "string" || !path.isAbsolute(desktopDirectory)
      || !(await filesystem.stat(desktopDirectory)).isDirectory()) throw new Error("backup-desktop-invalid");
    const key = vehicleUnitKey(requested.backup);
    await reserveOriginal(requested);
    const pointerFile = path.join(directory, `desktop-original-${key}.json`);
    // Reserve the first vehicle/unit baseline before writing the desktop file.
    // Competing callers (including different software versions) then publish
    // the same immutable bytes, rather than creating two "first" desktop files.
    try { await publish(pointerFile, JSON.stringify({ id: requested.sha256, fileName: originalFileName(requested.backup) })); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
    const pointer = await readJson(pointerFile);
    if (!pointer || Object.keys(pointer).length !== 2 || !validId(pointer.id) || typeof pointer.fileName !== "string")
      throw new Error("backup-desktop-pointer-invalid");
    const original = await load(pointer.id);
    if (original.sha256 !== requested.sha256 || vehicleUnitKey(original.backup) !== key || pointer.fileName !== originalFileName(original.backup)
      || (await baseline(original.identityKey))?.sha256 !== original.sha256)
      throw new Error("backup-desktop-pointer-invalid");
    const filePath = path.join(desktopDirectory, pointer.fileName);
    const bytes = Buffer.from(JSON.stringify(original.backup, null, 2) + "\n");
    try { await publish(filePath, bytes); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
    // Check an existing file as well as a newly published one. A damaged file
    // cannot satisfy the desktop-save prerequisite and is never overwritten.
    const file = await filesystem.open(filePath, "r");
    try {
      if ((await file.stat()).size !== bytes.length || hash(await file.readFile()) !== hash(bytes))
        throw new Error("backup-desktop-integrity-failed");
    } finally { await file.close(); }
    return { ok: true, saved: true, filePath, id: original.sha256,
      provenance: original.backup.provenance, freshReadProven: original.backup.freshReadProven };
  }
  return { load, persist, list, baseline, firstOriginal, findOriginal, publishOriginal };
}
