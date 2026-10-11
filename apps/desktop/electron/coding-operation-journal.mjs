/** Durable operation history; no transport, write payload or execution authority.
 * Publish intent before dispatch. An unfinished/unknown vehicle operation blocks
 * another operation in its scope; a new rehearsal is always a distinct simulation.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";

export const codingJournalHash = value => createHash("sha256").update(value).digest("hex");
const digest = value => codingJournalHash(JSON.stringify(value));
const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const validId = value => typeof value === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
const text = value => typeof value === "string" && value.length > 0 && value.length <= 240;
const exact = (value, keys) => value && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));

function initial(data) {
  if (!exact(data, ["mode", "ecu", "scopeKey", "backupId", "targets"])
    || !["simulation", "vehicle"].includes(data.mode) || !text(data.ecu) || !hash(data.scopeKey) || !hash(data.backupId)
    || !Array.isArray(data.targets) || !data.targets.length || data.targets.length > 256
    || !data.targets.every(t => exact(t, ["key", "sha256", "changed"]) && text(t.key) && hash(t.sha256) && typeof t.changed === "boolean")
    || new Set(data.targets.map(t => t.key)).size !== data.targets.length) throw new Error("operation-metadata-invalid");
  return { metadata: data, phase: "backed-up", intended: [], verified: [], currentKey: null };
}

function transition(prior, phase, data) {
  const state = structuredClone(prior), expected = state.metadata.targets;
  const current = expected.find(t => t.key === state.currentKey);
  if (phase === "write-intent") {
    const target = expected.find(t => t.key === data?.key);
    if (!["backed-up", "block-verified"].includes(state.phase) || !exact(data, ["key", "targetSha256"])
      || !target?.changed || target.sha256 !== data.targetSha256 || state.intended.includes(target.key)) throw new Error("operation-write-replay-or-order");
    state.intended.push(target.key); state.currentKey = target.key;
  } else if (phase === "write-returned" || phase === "block-verified") {
    if (state.phase !== (phase === "write-returned" ? "write-intent" : "write-returned")
      || !exact(data, ["key", "targetSha256"]) || data.key !== state.currentKey || data.targetSha256 !== current?.sha256) throw new Error("operation-verification-order");
    if (phase === "block-verified") state.verified.push(data.key);
  } else if (phase === "completed") {
    if (!["backed-up", "block-verified"].includes(state.phase) || !exact(data, ["scopeTargetSha256"])
      || data.scopeTargetSha256 !== digest(expected.map(({ key, sha256 }) => ({ key, sha256 })))
      || expected.filter(t => t.changed).some(t => !state.verified.includes(t.key))) throw new Error("operation-scope-unverified");
  } else if (phase === "failed-before-write" || phase === "outcome-unknown") {
    if (["completed", "failed-before-write", "outcome-unknown"].includes(state.phase) || !exact(data, ["error"])
      || typeof data.error !== "string" || !data.error.length || data.error.length > 4000
      || (phase === "failed-before-write") !== (state.intended.length === 0)) throw new Error("operation-failure-state-invalid");
  } else throw new Error("operation-phase-invalid");
  state.phase = phase;
  return state;
}

export function createCodingOperationJournal({ directory, filesystem = fs }) {
  const folder = id => { if (!validId(id)) throw new Error("operation-id-invalid"); return path.join(directory, id); };
  const scopeLock = metadata => path.join(directory, `scope-${metadata.scopeKey}.lock`);
  async function checkLock(state) {
    if (state.metadata.mode !== "vehicle") return;
    const file = scopeLock(state.metadata);
    if ((await filesystem.lstat(file)).isSymbolicLink() || (await filesystem.readFile(file, "utf8")) !== state.operationId) throw new Error("operation-scope-lock-invalid");
  }
  async function publish(dir, event) {
    const temp = path.join(dir, `.pending-${randomUUID()}.json`), final = path.join(dir, `${String(event.sequence).padStart(6, "0")}.json`);
    let created = false;
    try {
      const handle = await filesystem.open(temp, "wx"); created = true;
      try { await handle.writeFile(JSON.stringify({ event, sha256: digest(event) })); await handle.sync(); } finally { await handle.close(); }
      // Exclusive link arbitrates concurrent appenders and cannot replace history.
      await filesystem.link(temp, final);
    } finally { if (created) await filesystem.unlink(temp); }
  }
  async function inspect(id) {
    const dir = folder(id);
    if ((await filesystem.lstat(dir)).isSymbolicLink()) throw new Error("operation-integrity-failed");
    const entries = await filesystem.readdir(dir);
    if (entries.some(n => !/^\d{6}\.json$/.test(n) && !/^\.pending-[a-f0-9-]+\.json$/.test(n))) throw new Error("operation-integrity-failed");
    const names = entries.filter(n => /^\d{6}\.json$/.test(n)).sort();
    if (!names.length || names.length > 771) throw new Error("operation-integrity-failed");
    let previous = null, state, last, createdUtc;
    for (const [sequence, name] of names.entries()) {
      if (name !== `${String(sequence).padStart(6, "0")}.json`) throw new Error("operation-integrity-failed");
      const file = path.join(dir, name);
      if ((await filesystem.lstat(file)).isSymbolicLink()) throw new Error("operation-integrity-failed");
      const handle = await filesystem.open(file, "r"); let stored;
      try { if ((await handle.stat()).size > 1048576) throw new Error("operation-integrity-failed"); stored = JSON.parse(await handle.readFile("utf8")); } finally { await handle.close(); }
      if (!exact(stored, ["event", "sha256"]) || !exact(stored.event, ["operationId", "sequence", "previousSha256", "phase", "data", "createdUtc"])
        || !hash(stored.sha256) || stored.sha256 !== digest(stored.event) || stored.event.operationId !== id
        || stored.event.sequence !== sequence || stored.event.previousSha256 !== previous
        || typeof stored.event.createdUtc !== "string" || !Number.isFinite(Date.parse(stored.event.createdUtc))) throw new Error("operation-integrity-failed");
      const event = stored.event;
      if (!sequence && event.phase !== "backed-up") throw new Error("operation-integrity-failed");
      if (!sequence) createdUtc = event.createdUtc;
      state = sequence ? transition(state, event.phase, event.data) : initial(event.data);
      previous = stored.sha256; last = event;
    }
    return { operationId: id, ...state, createdUtc, updatedUtc: last.createdUtc, sequence: last.sequence, lastSha256: previous,
      requiresReconciliation: state.intended.length > 0 && state.phase !== "completed",
      automaticWriteRetry: false, executionEnabled: false };
  }
  async function list() {
    let names;
    try { names = await filesystem.readdir(directory); } catch (error) { if (error.code === "ENOENT") return []; throw error; }
    const operations = [];
    for (const id of names.filter(validId).sort()) {
      try { operations.push(await inspect(id)); }
      catch (error) { operations.push({ operationId: id, integrityError: String(error.message || error), requiresReconciliation: true, automaticWriteRetry: false, executionEnabled: false }); }
    }
    return operations;
  }
  async function begin(metadata) {
    // Snapshot before any await so callers cannot change the intended scope.
    const data = structuredClone(metadata); initial(data);
    await filesystem.mkdir(directory, { recursive: true });
    if (data.mode === "vehicle") {
      const outstanding = (await list()).some(o => o.integrityError || (o.metadata.mode === "vehicle"
        && o.metadata.scopeKey === data.scopeKey && o.requiresReconciliation));
      if (outstanding) throw new Error("operation-unresolved-prior-write");
    }
    const id = randomUUID(), dir = folder(id); await filesystem.mkdir(dir);
    await publish(dir, { operationId: id, sequence: 0, previousSha256: null, phase: "backed-up", data, createdUtc: new Date().toISOString() });
    if (data.mode === "vehicle") {
      // A crash leaves this lock in place. Never expire it or guess a dead owner.
      const handle = await filesystem.open(scopeLock(data), "wx");
      try { await handle.writeFile(id); await handle.sync(); } finally { await handle.close(); }
    }
    return inspect(id);
  }
  async function append(id, phase, input) {
    const data = structuredClone(input), state = await inspect(id);
    transition(state, phase, data);
    await checkLock(state);
    await publish(folder(id), { operationId: id, sequence: state.sequence + 1, previousSha256: state.lastSha256,
      phase, data, createdUtc: new Date().toISOString() });
    const next = await inspect(id);
    if (state.metadata.mode === "vehicle" && ["completed", "failed-before-write"].includes(phase)) {
      await checkLock(state); await filesystem.unlink(scopeLock(state.metadata));
    }
    return next;
  }
  return { begin, append, inspect, list };
}
