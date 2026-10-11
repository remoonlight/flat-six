import fs from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const validId = (id) => typeof id === "string" && /^[a-f0-9]{64}$/.test(id);

/** Immutable snapshots shared by preparation and the original-coding reader.
 * Imports remain declared scopes, and simulations never establish a baseline.
 * Caller-owned definitions/transport qualification must precede a vehicle read;
 * this store cannot promote imported values to fresh vehicle data. */
export function createCodingBackupStore({ directory, normalizeBackup, filesystem = fs }) {
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
    const backup = normalizeBackup(stored.backup);
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
    if (stored.identityKey !== identityKey || stored.backup.provenance !== "imported") throw new Error("backup-baseline-mismatch");
    return stored;
  }
  async function publish(file, content) {
    // A failed write/fsync never exposes a partial final file. link is exclusive
    // on both NTFS and POSIX; rename could overwrite the first immutable baseline.
    const temporary = path.join(directory, `.pending-${randomUUID()}.json`);
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
    try { await publish(path.join(directory, `${sha256}.json`), JSON.stringify(stored)); }
    catch (error) { if (error.code !== "EEXIST") throw error; await load(sha256); }
    if (backup.provenance === "imported") {
      try { await publish(path.join(directory, `baseline-${identityKey}.json`), JSON.stringify({ id: sha256 })); }
      catch (error) { if (error.code !== "EEXIST") throw error; }
      // Verify the winner even when another store instance published it first.
      if (!await baseline(identityKey)) throw new Error("backup-baseline-missing");
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
      const original = await baseline(stored.identityKey);
      rows.push({ id: stored.sha256, identity: stored.backup.identity, profileId: stored.backup.profileId,
        capturedUtc: stored.backup.capturedUtc, blockCount: stored.backup.blocks.length,
        original: original?.sha256 === stored.sha256, provenance: stored.backup.provenance, freshReadProven: false });
    }
    return rows.sort((a, b) => b.capturedUtc.localeCompare(a.capturedUtc));
  }
  return { load, persist, list, baseline };
}
