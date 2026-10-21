import fs from "node:fs/promises";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { createCodingBackupStore } from "./coding-backup-store.mjs";
import { normalizeSourceCodingBackup } from './source-coding-backup.mjs';
import { createCodingOperationJournal, codingJournalHash } from "./coding-operation-journal.mjs";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const hex = (value, max = 8192) => typeof value === "string" && value.length <= max && /^(?:[0-9A-F]{2})+$/.test(value);
const text = (value) => typeof value === "string" && value.length > 0 && value.length <= 240;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const idFields = ["generation", "vin", "ecu", "hardware", "software"];

/** Read-only version selection. Jobs and fresh vehicle state must come from
 * application-owned, source-qualified clients, never imported manifests or IPC.
 * The default registry is empty. A review is not a write authorization. */
export function createFirmwareVersionSelection({ getJobs = () => [], getVehicleState = async () => null, isVehicleBusy = () => false, canExecute = () => false } = {}) {
  const listings = new Map(), revisions = new Map();
  const sha = value => /^[a-f0-9]{64}$/.test(value || "");
  const strings = (value, required = false) => Array.isArray(value) && (!required || value.length > 0)
    && value.every(text) && new Set(value).size === value.length;
  const closed = (value, keys) => value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).every(key => keys.includes(key));
  const failure = error => ({ ok: false, error, executionEnabled: false });
  function validJob(job) {
    const condition = job?.conditions;
    return closed(job, ["id", "ecu", "origin", "sourceQualified", "sourceHash", "conditions", "participants", "releaseNotes"])
      && closed(condition, ["qualified", "generation", "productKeys", "modelYears", "requiredOptions", "excludedOptions"])
      && job?.origin === "original" && job.sourceQualified === true && sha(job.sourceHash)
      && text(job.id) && text(job.ecu) && ["981", "982"].includes(condition?.generation)
      && condition.qualified === true && strings(condition.productKeys, true)
      && Array.isArray(condition.modelYears) && condition.modelYears.length > 0
      && condition.modelYears.every(year => Number.isInteger(year) && year >= 1980 && year <= 2200)
      && strings(condition.requiredOptions) && strings(condition.excludedOptions)
      && Array.isArray(job.participants) && job.participants.length > 0 && job.participants.length <= 128
      && new Set(job.participants.map(unit => unit.ecu)).size === job.participants.length
      && job.participants.some(unit => unit.ecu === job.ecu)
      && job.participants.every(unit => closed(unit, ["ecu", "label", "targetVersion", "targetSoftware", "executionQualified", "recoveryQualified", "routes", "files"])
        && text(unit.ecu) && text(unit.label) && text(unit.targetVersion)
        && (unit.targetSoftware === undefined || text(unit.targetSoftware))
        && unit.executionQualified === true && unit.recoveryQualified === true
        && Array.isArray(unit.routes) && unit.routes.length > 0 && unit.routes.every(route =>
          closed(route, ["hardware", "software", "currentVersion", "operation"]) && text(route.hardware) && text(route.software) && text(route.currentVersion)
          && ["upgrade", "reflash", "downgrade"].includes(route.operation)
          && (route.operation === "reflash") === (route.currentVersion === unit.targetVersion))
        && Array.isArray(unit.files) && unit.files.length > 0 && unit.files.length <= 64
        && unit.files.every(file => closed(file, ["path", "sha256", "bytes", "sourceQualified", "containerQualified"])
          && text(file.path) && path.isAbsolute(file.path) && sha(file.sha256)
          && Number.isSafeInteger(file.bytes) && file.bytes > 0 && file.sourceQualified === true && file.containerQualified === true));
  }
  function normalizeState(state) {
    const device = state?.device, vehicle = state?.vehicle;
    if (state?.qualified !== true || state.freshRead !== true || !text(state.readId)
      || device?.connected !== true || device.purpose !== "diagnostic" || device.wiredVerified !== true
      || !["VNCI", "PT3G"].includes(device.family) || !text(device.id) || !text(device.connectionEpoch)
      || !(device.family === "VNCI" ? /^vnci:[0-9]{1,16}$/.test(device.id) : /^pt3g:[0-9]{1,16}$/.test(device.id))
      || !["981", "982"].includes(vehicle?.generation) || !/^[A-HJ-NPR-Z0-9]{17}$/.test(vehicle.vin || "")
      || !text(vehicle.productKey) || !Number.isInteger(vehicle.modelYear) || !strings(vehicle.options)
      || !Array.isArray(state.units) || state.units.length > 256
      || !state.units.every(unit => unit?.qualified === true && text(unit.ecu) && text(unit.hardware)
        && text(unit.software) && text(unit.version)) || new Set(state.units.map(unit => unit.ecu)).size !== state.units.length)
      throw new Error("firmware-current-unavailable");
    return { readId: state.readId, device: { id: device.id, family: device.family, connectionEpoch: device.connectionEpoch },
      vehicle: { generation: vehicle.generation, vin: vehicle.vin, productKey: vehicle.productKey,
        modelYear: vehicle.modelYear, options: [...vehicle.options].sort() },
      units: state.units.map(({ ecu, hardware, software, version }) => ({ ecu, hardware, software, version })).sort((a, b) => a.ecu.localeCompare(b.ecu)) };
  }
  function match(job, state) {
    const condition = job.conditions, vehicle = state.vehicle;
    if (condition.generation !== vehicle.generation || !condition.productKeys.includes(vehicle.productKey)
      || !condition.modelYears.includes(vehicle.modelYear) || !condition.requiredOptions.every(option => vehicle.options.includes(option))
      || condition.excludedOptions.some(option => vehicle.options.includes(option))) return null;
    const rows = [];
    for (const unit of job.participants) {
      const current = state.units.find(item => item.ecu === unit.ecu);
      const routes = unit.routes.filter(route => current && route.hardware === current.hardware && route.software === current.software && route.currentVersion === current.version);
      if (routes.length !== 1) return null;
      rows.push({ ecu: unit.ecu, label: unit.label, currentVersion: current.version, targetVersion: unit.targetVersion, operation: routes[0].operation });
    }
    return rows;
  }
  async function filesMatch(job) {
    for (const file of job.participants.flatMap(unit => unit.files)) {
      const handle = await fs.open(file.path, "r");
      try {
        const stat = await handle.stat(); if (!stat.isFile() || stat.size !== file.bytes) return false;
        const digest = createHash("sha256"), buffer = Buffer.alloc(65536); let bytes = 0;
        for (;;) {
          const read = await handle.read(buffer, 0, Math.min(buffer.length, file.bytes - bytes + 1), null);
          if (!read.bytesRead) break;
          bytes += read.bytesRead; if (bytes > file.bytes) return false;
          digest.update(buffer.subarray(0, read.bytesRead));
        }
        if (bytes !== file.bytes || digest.digest("hex") !== file.sha256) return false;
      } finally { await handle.close(); }
    }
    return true;
  }
  return {
    clearOwned(owner) { listings.delete(owner); revisions.set(owner, (revisions.get(owner) || 0) + 1); },
    /** Application-only handoff; IPC cannot request it. A successful review is
     * consumed once, and the executor must recheck the plan after acquiring the
     * actual vehicle lease, before running any source operation. */
    takeReviewed(request, owner) {
      const listing = listings.get(owner), reviewed = listing?.reviewed;
      if (!reviewed || reviewed.consumed || listing.ecu !== request.ecu || listing.id !== request.listId
        || reviewed.targetId !== request.targetId || reviewed.id !== request.reviewId) return null;
      reviewed.consumed = true;
      const option = listing.options.find(item => item.id === reviewed.targetId);
      const baseline = structuredClone(reviewed.state);
      const reads = new Set([baseline.readId]);
      return { job: structuredClone(option.job), rows: structuredClone(reviewed.rows),
        async verifyCurrent(raw) {
          const state = normalizeState(raw), { readId: freshId, ...fresh } = state, { readId: priorId, ...prior } = baseline;
          if (reads.has(freshId) || freshId === priorId || !same(fresh, prior)) return { ok: false, error: "firmware-current-changed" };
          reads.add(freshId);
          const registry = await getJobs(), jobs = Array.isArray(registry) ? registry.filter(job => job?.id === option.job.id) : [];
          if (jobs.length !== 1 || !validJob(jobs[0]) || !same(jobs[0], option.job)) return { ok: false, error: "firmware-source-changed" };
          try { if (!await filesMatch(option.job)) return { ok: false, error: "firmware-file-changed" }; }
          catch { return { ok: false, error: "firmware-file-changed" }; }
          return { ok: true };
        },
        inspectResult(raw) {
          const state = normalizeState(raw);
          const stable = !reads.has(state.readId) && same(state.device, baseline.device) && same(state.vehicle, baseline.vehicle);
          reads.add(state.readId);
          const rows = option.job.participants.map(unit => {
            const current = state.units.find(item => item.ecu === unit.ecu), before = baseline.units.find(item => item.ecu === unit.ecu);
            const bound = stable && !!current && !!before && current.hardware === before.hardware;
            return { ecu: unit.ecu, actualVersion: bound ? current.version : null,
              matched: bound && text(unit.targetSoftware)
                && current.software === unit.targetSoftware && current.version === unit.targetVersion };
          });
          return { ok: rows.every(row => row.matched), rows };
        } };
    },
    async handle(request, owner) {
      if (!Number.isInteger(owner) || owner < 1 || !request || typeof request !== "object" || Array.isArray(request)
        || !["list", "review", "clear"].includes(request.action)) return failure("firmware-selection-invalid");
      const keys = request.action === "list" ? ["action", "ecu"] : request.action === "review" ? ["action", "ecu", "listId", "targetId"] : ["action", "listId"];
      if (Object.keys(request).some(key => !keys.includes(key))
        || request.action !== "clear" && (typeof request.ecu !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(request.ecu))) return failure("firmware-selection-invalid");
      if (request.action === "clear") {
        if (request.listId !== listings.get(owner)?.id) return failure("firmware-selection-stale");
        this.clearOwned(owner); return { ok: true, executionEnabled: false };
      }
      const revision = (revisions.get(owner) || 0) + 1; revisions.set(owner, revision);
      const listing = listings.get(owner);
      if (request.action === "list") listings.delete(owner);
      try {
        if (request.action === "list") {
          const registry = await getJobs();
          if (revisions.get(owner) !== revision) return failure("firmware-selection-stale");
          if (!Array.isArray(registry) || registry.length > 4096) return failure("firmware-registry-invalid");
          const copied = structuredClone(registry);
          const jobs = copied.filter(job => job?.ecu === request.ecu && validJob(job) && copied.filter(other => other?.id === job.id).length === 1);
          if (!jobs.length) return { ok: true, ecu: request.ecu, versions: [], catalogueState: "source-unavailable", executionEnabled: false, reason: "缺少已核实的原厂版本、适用条件及完整作业绑定。" };
          if (isVehicleBusy()) return failure("vehicle-busy");
          const state = normalizeState(await getVehicleState());
          const candidates = [];
          for (const job of jobs) {
            const rows = match(job, state); if (!rows) continue;
            try { if (!await filesMatch(job)) continue; } catch { continue; }
            candidates.push({ id: randomUUID(), job: structuredClone(job), rows, version: rows.find(row => row.ecu === job.ecu).targetVersion });
          }
          // Multiple different jobs with the same label are ambiguous. No
          // filename, date or lexical version ordering chooses one for the user.
          const options = candidates.filter(item => candidates.filter(other => other.version === item.version).length === 1);
          if (revisions.get(owner) !== revision) return failure("firmware-selection-stale");
          const entry = { id: randomUUID(), ecu: request.ecu, state, options }; listings.set(owner, entry);
          return { ok: true, ecu: entry.ecu, listId: entry.id, executionEnabled: false, catalogueState: options.length ? "matched" : "no-match",
            versions: options.map(item => ({ id: item.id, version: item.version,
              ...(closed(item.job.releaseNotes, ["qualified", "sourceHash", "text"]) && item.job.releaseNotes.qualified === true && sha(item.job.releaseNotes.sourceHash)
                && typeof item.job.releaseNotes.text === "string" && item.job.releaseNotes.text.length > 0 && item.job.releaseNotes.text.length <= 4096 ? { notes: item.job.releaseNotes.text } : {}) })),
            ...(!options.length ? { reason: "当前车辆及全部联动单元没有核对通过的原厂版本。" } : {}) };
        }
        const option = listing?.options.find(item => item.id === request.targetId);
        if (!listing || listing.id !== request.listId || listing.ecu !== request.ecu || !option) return failure("firmware-selection-stale");
        delete listing.reviewed;
        if (isVehicleBusy()) return failure("vehicle-busy");
        const registry = await getJobs();
        if (revisions.get(owner) !== revision) return failure("firmware-selection-stale");
        const jobs = Array.isArray(registry) && registry.length <= 4096 ? registry.filter(job => job?.id === option.job.id) : [];
        if (jobs.length !== 1 || !validJob(jobs[0]) || !same(jobs[0], option.job)) {
          listings.delete(owner); return failure("firmware-source-changed");
        }
        const state = normalizeState(await getVehicleState());
        if (revisions.get(owner) !== revision) return failure("firmware-selection-stale");
        if (state.readId === listing.state.readId) return failure("firmware-current-unavailable");
        const { readId: _fresh, ...fresh } = state, { readId: _prior, ...prior } = listing.state;
        if (!same(fresh, prior)) { listings.delete(owner); return failure("firmware-current-changed"); }
        const rows = match(option.job, state);
        let verified = false; try { verified = await filesMatch(option.job); } catch { /* Private file errors stay out of IPC. */ }
        if (revisions.get(owner) !== revision) return failure("firmware-selection-stale");
        if (!rows || !verified) { listings.delete(owner); return failure("firmware-file-changed"); }
        if (revisions.get(owner) !== revision) return failure("firmware-selection-stale");
        const reviewed = { id: randomUUID(), targetId: option.id, state, rows, consumed: false }; listing.reviewed = reviewed;
        const canStart = canExecute({ job: structuredClone(option.job) }) === true;
        return { ok: true, ecu: listing.ecu, listId: listing.id, targetId: option.id, reviewId: reviewed.id, rows,
          canStart, executionEnabled: false, reason: canStart ? "版本及完整联动范围核对通过，请核对后确认刷写。" : "版本及完整联动范围核对通过；刷写执行、回读及恢复客户端尚未接入。" };
      } catch {
        if (revisions.get(owner) === revision) listings.delete(owner);
        return failure("firmware-current-unavailable");
      }
    },
  };
}

export function codingBlockKey(block) {
  if (typeof block?.did === "string" && block?.identifierKind === undefined && block?.identifierHex === undefined && /^[0-9A-F]{4}$/.test(block.did)) return `DID:${block.did}`;
  if (block?.did === undefined && typeof block?.identifierHex === "string" && ((block?.identifierKind === "LID" && /^[0-9A-F]{2}$/.test(block.identifierHex))
    || (block?.identifierKind === "DID" && /^[0-9A-F]{4}$/.test(block.identifierHex)))) return `${block.identifierKind}:${block.identifierHex}`;
  throw new Error("backup-block-identifier-invalid");
}
function blockLocator(block) {
  codingBlockKey(block);
  return block.did !== undefined ? { did: block.did } : { identifierKind: block.identifierKind, identifierHex: block.identifierHex };
}
function blockReadRequest(block) {
  const [kind, identifier] = codingBlockKey(block).split(":");
  return (kind === "LID" ? "21" : "22") + identifier;
}
export function normalizeCodingBackup(input) {
  if (!input || ![1, 2].includes(input.schemaVersion) || input.kind !== "ecu-coding-backup" || !input.identity
    || !idFields.every((key) => text(input.identity[key])) || !["981", "982"].includes(input.identity.generation)
    || !/^[A-HJ-NPR-Z0-9]{17}$/.test(input.identity.vin) || !text(input.profileId)
    || !Array.isArray(input.blocks) || !input.blocks.length || input.blocks.length > 256) throw new Error("backup-identity-or-coverage-invalid");
  const legacy = input.schemaVersion === 1;
  const declared = legacy ? input.expectedDids : input.expectedBlocks;
  if (!Array.isArray(declared) || declared.length !== input.blocks.length
    || (legacy ? input.expectedBlocks !== undefined : input.expectedDids !== undefined)) throw new Error("backup-identity-or-coverage-invalid");
  const expected = legacy ? declared.map((did) => { codingBlockKey({ did }); return did; }).sort()
    : declared.map((block) => { if (block?.did !== undefined) throw new Error("backup-block-identifier-invalid"); return blockLocator(block); })
      .sort((a, b) => codingBlockKey(a).localeCompare(codingBlockKey(b)));
  const blocks = input.blocks.map((block) => {
    if (!block || !hex(block.dataHex) || (legacy ? block.did === undefined : block.did !== undefined)) throw new Error("backup-block-invalid");
    return { ...blockLocator(block), dataHex: block.dataHex };
  }).sort((a, b) => codingBlockKey(a).localeCompare(codingBlockKey(b)));
  const expectedKeys = legacy ? expected.map((did) => codingBlockKey({ did })) : expected.map(codingBlockKey);
  if (new Set(expectedKeys).size !== expectedKeys.length || !same(expectedKeys, blocks.map(codingBlockKey))) throw new Error("backup-incomplete");
  if (!text(input.capturedUtc) || !Number.isFinite(Date.parse(input.capturedUtc))) throw new Error("backup-time-invalid");
  // Imported completeness is a declared block list. It never authorizes a live write.
  // Keep v1 normalized bytes stable: existing immutable files/hashes remain readable.
  return { schemaVersion: input.schemaVersion, kind: input.kind, identity: Object.fromEntries(idFields.map((key) => [key, input.identity[key]])),
    profileId: input.profileId, ...(legacy ? { expectedDids: expected } : { expectedBlocks: expected }), blocks, capturedUtc: input.capturedUtc,
    provenance: input.provenance === "simulation" ? "simulation" : "imported", freshReadProven: false, completeness: "declared-block-list", liveVerified: false };
}

export function normalizeStoredCodingBackup(input) {
  return input?.schemaVersion === 3 ? normalizeSourceCodingBackup(input) : normalizeCodingBackup(input);
}

export function codingRestorePlan(original, current) {
  const before = normalizeStoredCodingBackup(current), baseline = normalizeStoredCodingBackup(original);
  if ((before.schemaVersion === 3) !== (baseline.schemaVersion === 3)) throw new Error("restore-provenance-mismatch");
  if (!same(before.identity, baseline.identity) || before.profileId !== baseline.profileId) throw new Error("restore-identity-version-mismatch");
  if (before.schemaVersion === 3 && (before.sourceHash !== baseline.sourceHash || before.scopeHash !== baseline.scopeHash
    || before.targetEcuId !== baseline.targetEcuId)) throw new Error("restore-source-scope-mismatch");
  if (!same(before.blocks.map(codingBlockKey), baseline.blocks.map(codingBlockKey))) throw new Error("restore-coverage-mismatch");
  const blocks = baseline.blocks.map((block, i) => {
    const prior = before.blocks[i];
    if (prior.dataHex.length !== block.dataHex.length) throw new Error("restore-block-length-mismatch");
    return { ...blockLocator(block), beforeHex: prior.dataHex, targetHex: block.dataHex, changed: prior.dataHex !== block.dataHex };
  });
  return { kind: "current-ecu-restore-plan", identity: baseline.identity, profileId: baseline.profileId, blocks,
    changedBlocks: blocks.filter((b) => b.changed).length, originalHash: hash(JSON.stringify(baseline)),
    currentHash: hash(JSON.stringify(before)), executionEnabled: false, writePayload: null,
    blockers: ["需要本次完整实车读取并持久化备份", "需要该版本已核实的写入、回读与恢复定义", "需要实车验收"] };
}

/** Compose named, source-validated edits only. Reapplying every target to the
 * final bytes detects overlapping fields whose later edit undoes an earlier one. */
async function codingBatchPreview(current, edits, previewField) {
  if (!Array.isArray(edits) || !edits.length || edits.length > 512) throw new Error("coding-batch-invalid");
  const seen = new Set(), effects = new Map(), blocks = new Map(current.blocks.map(b => [codingBlockKey(b), b.dataHex]));
  for (const edit of edits) {
    if (!edit || typeof edit !== "object" || Array.isArray(edit)
      || Object.keys(edit).some(k => !["blockKey", "recordAt", "rawValue", "physicalValue"].includes(k))
      || typeof edit.blockKey !== "string" || !/^(LID:[0-9A-F]{2}|DID:[0-9A-F]{4})$/.test(edit.blockKey)
      || !blocks.has(edit.blockKey) || !Number.isSafeInteger(edit.recordAt) || edit.recordAt < 0
      || (Object.hasOwn(edit, "rawValue") === Object.hasOwn(edit, "physicalValue"))
      || (Object.hasOwn(edit, "rawValue") ? !Number.isSafeInteger(edit.rawValue)
        : typeof edit.physicalValue !== "string" || !edit.physicalValue.length || edit.physicalValue.length > 128))
      throw new Error("coding-batch-edit-invalid");
    const key = `${edit.blockKey}:${edit.recordAt}`;
    if (seen.has(key)) throw new Error("coding-batch-duplicate-field");
    seen.add(key);
  }
  const preview = async (edit, dataHex) => {
    const [kind, identifier] = edit.blockKey.split(":");
    const result = await previewField({ action: "preview", category: "coding", generation: current.identity.generation,
      profileId: current.profileId, recordAt: edit.recordAt, dataHex,
      expectedReadRequestHex: (kind === "LID" ? "21" : "22") + identifier,
      ...(Object.hasOwn(edit, "rawValue") ? { rawValue: edit.rawValue } : { physicalValue: edit.physicalValue }) });
    if (!result?.ok) throw new Error(result?.error || "coding-batch-preview-failed");
    if (result.beforeHex !== dataHex || !hex(result.afterHex) || result.afterHex.length !== dataHex.length
      || !Array.isArray(result.effects)) throw new Error("coding-batch-preview-invalid");
    const targetBytes = Buffer.from(result.afterHex, "hex");
    const xor = Buffer.from(dataHex, "hex").map((byte, i) => byte ^ targetBytes[i]).toString("hex").toUpperCase();
    if (result.changedBitMaskHex !== xor) throw new Error("coding-batch-preview-invalid");
    return result;
  };
  for (const edit of edits) {
    const result = await preview(edit, blocks.get(edit.blockKey));
    blocks.set(edit.blockKey, result.afterHex);
    for (const effect of result.effects) {
      if (!Number.isSafeInteger(effect.sourceOffset) || typeof effect.name !== "string" || !effect.before || !effect.after)
        throw new Error("coding-batch-preview-invalid");
      const key = `${edit.blockKey}:${effect.sourceOffset}`, previous = effects.get(key);
      if (previous && !same(previous.after, effect.before)) throw new Error("coding-batch-effects-invalid");
      effects.set(key, { ...effect, blockKey: edit.blockKey, before: previous?.before || effect.before,
        selected: seen.has(key) });
    }
  }
  for (const edit of edits) {
    const final = blocks.get(edit.blockKey), checked = await preview(edit, final);
    if (checked.afterHex !== final) throw new Error("coding-batch-target-conflict");
  }
  const target = { ...current, blocks: current.blocks.map(b => ({ ...b, dataHex: blocks.get(codingBlockKey(b)) })) };
  const { originalHash, ...base } = codingRestorePlan(target, current);
  return { plan: { ...base, kind: "current-ecu-coding-preview-plan", targetHash: originalHash },
    effects: [...effects.values()].filter(effect => !same(effect.before, effect.after)), editCount: edits.length };
}

/** Executable offline state machine. Dependencies are application-owned simulation
 * objects, never a renderer command, live adapter or imported write recipe. */
export async function runCodingRehearsal(plan, dependencies) {
  const stages = [], readbacks = []; let writeAttemptCount = 0, backupId = null, operationId = null, operationState = null;
  if (dependencies.simulation !== true || plan.executionEnabled !== false || !Array.isArray(plan.blocks) || !plan.blocks.length || plan.blocks.length > 256
    || !plan.blocks.every((block) => hex(block.beforeHex) && hex(block.targetHex) && block.beforeHex.length === block.targetHex.length)) throw new Error("rehearsal-only");
  const keys = plan.blocks.map(codingBlockKey);
  if (new Set(keys).size !== keys.length) throw new Error("rehearsal-only");
  try {
    stages.push("read-identity");
    const identity = await dependencies.readIdentity();
    if (!idFields.every((key) => identity?.[key] === plan.identity?.[key])) throw new Error("pre-write-identity-mismatch");
    stages.push("read-complete-current-coding");
    const current = normalizeCodingBackup(await dependencies.readCurrent());
    if (!same(current.identity, plan.identity) || current.profileId !== plan.profileId
      || !same(current.blocks.map((block) => [codingBlockKey(block), block.dataHex]), plan.blocks.map((block) => [codingBlockKey(block), block.beforeHex]))) throw new Error("pre-write-coding-changed");
    stages.push("read-identity-after-complete-read");
    const identityAfterRead = await dependencies.readIdentity();
    if (!idFields.every((key) => identityAfterRead?.[key] === plan.identity?.[key])) throw new Error("pre-write-identity-mismatch");
    stages.push("persist-complete-current-backup");
    const persisted = await dependencies.persistBackup(current);
    if (!persisted?.id) throw new Error("pre-write-backup-failed");
    backupId = persisted.id;
    if (dependencies.journal) {
      stages.push("persist-operation-after-backup");
      const operation = await dependencies.journal.begin({ mode: "simulation", ecu: plan.identity.ecu,
        scopeKey: hash(JSON.stringify({ identity: plan.identity, profileId: plan.profileId })), backupId,
        targets: plan.blocks.map(block => ({ key: codingBlockKey(block), changed: !!block.changed,
          sha256: codingJournalHash(Buffer.from(block.targetHex, "hex")) })) });
      operationId = operation.operationId; operationState = operation.phase;
    }
    for (const block of plan.blocks) {
      if (!block.changed) continue;
      const key = codingBlockKey(block);
      stages.push(`read-identity-before-write:${key}`);
      const identityBeforeWrite = await dependencies.readIdentity();
      if (!idFields.every((field) => identityBeforeWrite?.[field] === plan.identity?.[field])) throw new Error("pre-write-identity-mismatch");
      const targetSha256 = codingJournalHash(Buffer.from(block.targetHex, "hex"));
      if (operationId) {
        stages.push(`persist-write-intent:${key}`);
        operationState = (await dependencies.journal.append(operationId, "write-intent", { key, targetSha256 })).phase;
      }
      stages.push(`write-once:${key}`); writeAttemptCount++;
      await dependencies.writeBlock(key, block.targetHex);
      if (operationId) operationState = (await dependencies.journal.append(operationId, "write-returned", { key, targetSha256 })).phase;
      stages.push(`readback-immediate:${key}`);
      const read = await dependencies.readBlock(key);
      if (read !== block.targetHex) throw new Error("readback-mismatch");
      if (operationId) operationState = (await dependencies.journal.append(operationId, "block-verified", { key, targetSha256 })).phase;
    }
    // Final full-scope reread also catches a later write altering an earlier block.
    for (const block of plan.blocks) {
      stages.push(`readback:${codingBlockKey(block)}`);
      const read = await dependencies.readBlock(codingBlockKey(block));
      readbacks.push({ ...blockLocator(block), dataHex: read });
      if (read !== block.targetHex) throw new Error("readback-mismatch");
    }
    stages.push("read-identity-after-complete-readback");
    const finalIdentity = await dependencies.readIdentity();
    if (!idFields.every(field => finalIdentity?.[field] === plan.identity?.[field])) throw new Error("post-write-identity-mismatch");
    if (operationId) {
      stages.push("persist-complete-readback-result");
      const scopeTargetSha256 = hash(JSON.stringify(readbacks.map(block => ({ key: codingBlockKey(block),
        sha256: codingJournalHash(Buffer.from(block.dataHex, "hex")) }))));
      operationState = (await dependencies.journal.append(operationId, "completed", { scopeTargetSha256 })).phase;
    }
    return { ok: true, simulation: true, liveVerified: false, stages, readbacks, backupId, writeAttemptCount,
      operationId, operationState, automaticWriteRetry: false };
  } catch (error) {
    let journalError = null;
    if (operationId) {
      try {
        const current = await dependencies.journal.inspect(operationId);
        operationState = current.phase;
        operationState = (await dependencies.journal.append(operationId, current.intended.length ? "outcome-unknown" : "failed-before-write",
          { error: String(error.message || error).slice(0, 4000) || "operation-failed" })).phase;
      } catch (failure) { journalError = String(failure.message || failure); }
    }
    return { ok: false, error: String(error.message || error), simulation: true, liveVerified: false,
      stages, readbacks, backupId, writeAttemptCount, operationId, operationState, journalError,
      automaticWriteRetry: false, recoveryRequired: writeAttemptCount > 0 || ["write-intent", "write-returned", "block-verified", "outcome-unknown"].includes(operationState) };
  }
}

/** A new, owned full source read resolves an old outcome without retrying it.
 * No renderer blocks, imported backup or partial read can close the operation.
 * Only hashes of the observation are journaled; no extra desktop file is saved. */
export async function reconcileCodingReadback(journal, operationId, input, loadBackup, checkpoint) {
  const current = normalizeSourceCodingBackup(structuredClone(input));
  const state = await journal.inspect(operationId);
  if (!checkpoint || checkpoint.lastSha256 !== state.lastSha256
    || typeof checkpoint.readStartedUtc !== "string" || !Number.isFinite(Date.parse(checkpoint.readStartedUtc))
    || Date.parse(checkpoint.readStartedUtc) < Date.parse(state.updatedUtc)
    || Date.parse(current.capturedUtc) < Date.parse(checkpoint.readStartedUtc))
    throw new Error("operation-reconciliation-read-stale");
  if (state.metadata.mode !== "vehicle" || !state.intended.length
    || !["write-intent", "write-returned", "block-verified", "outcome-unknown", "readback-reconciled"].includes(state.phase))
    throw new Error("operation-reconciliation-unavailable");
  const scopeKey = hash(JSON.stringify({ identity: current.identity, profileId: current.profileId }));
  if (state.metadata.scopeKey !== scopeKey || state.metadata.ecu !== current.identity.ecu
    || Date.parse(current.capturedUtc) < Date.parse(state.updatedUtc)) throw new Error("operation-reconciliation-read-stale");
  const stored = await loadBackup(state.metadata.backupId);
  const before = normalizeSourceCodingBackup(stored?.backup);
  if (stored.sha256 !== state.metadata.backupId || hash(JSON.stringify(before)) !== stored.sha256
    || !Number.isFinite(Date.parse(state.createdUtc)) || Date.parse(before.capturedUtc) > Date.parse(state.createdUtc))
    throw new Error("operation-reconciliation-backup-invalid");
  codingRestorePlan(before, current); // Exact identity/version/source/complete scope.
  if (current.blocks.length !== state.metadata.targets.length || current.blocks.some((b, i) =>
    codingBlockKey(b) !== state.metadata.targets[i].key
    || state.metadata.targets[i].changed !== (state.metadata.targets[i].sha256 !== codingJournalHash(Buffer.from(before.blocks[i].dataHex, "hex")))))
    throw new Error("operation-reconciliation-scope-invalid");
  return journal.reconcile(operationId, { capturedUtc: current.capturedUtc,
    blocks: current.blocks.map(b => ({ key: codingBlockKey(b), sha256: codingJournalHash(Buffer.from(b.dataHex, "hex")) })) }, state.lastSha256);
}

export function firmwarePreparation(manifest, file, current, transport, container = null) {
  if (!manifest || manifest.schemaVersion !== 1 || manifest.kind !== "oem-ecu-firmware-manifest"
    || manifest.origin !== "original" || !/^[a-f0-9]{64}$/.test(manifest.sha256) || !Number.isSafeInteger(manifest.bytes)
    || manifest.bytes <= 0 || !text(manifest.manufacturerSource) || !text(manifest.targetSoftware)
    || !manifest.identity || !idFields.every((key) => text(manifest.identity[key]))
    || !["981", "982"].includes(manifest.identity.generation) || !/^[A-HJ-NPR-Z0-9]{17}$/.test(manifest.identity.vin)) throw new Error("firmware-manifest-invalid");
  const blockers = [];
  const selectedDeviceId = typeof transport?.selectedDeviceId === "string" ? transport.selectedDeviceId : "";
  const selectedFamily = /^vnci:[0-9]{1,16}$/.test(selectedDeviceId) ? "VNCI"
    : /^pt3g:[0-9]{1,16}$/.test(selectedDeviceId) ? "PT3G" : null;
  const transportRequirement = { targetFamilies: ["VNCI", "PT3G"], selectedFamily,
    targetFamilySelected: selectedFamily !== null,
    connectedForDiagnostics: transport?.connected === true && transport?.commOk === true && transport?.purpose === "diagnostic",
    connectionType: /^bt:[0-9A-F]{12}$/.test(selectedDeviceId) ? "bluetooth" : "unverified",
    // Current connection status has no evidence of the physical link type.
    // A module ID, registry label or supplied Boolean cannot prove a wired link.
    wiredConnectionVerified: false };
  if (file.sha256 !== manifest.sha256 || file.bytes !== manifest.bytes) blockers.push("固件内容与清单哈希或长度不匹配");
  if (!current || !idFields.every((key) => current[key] === manifest.identity[key])) blockers.push("车辆、控制单元或当前硬件软件版本不匹配");
  if (!transportRequirement.connectedForDiagnostics) blockers.push("尚未连接并核实诊断头");
  if (!transportRequirement.targetFamilySelected) blockers.push("原厂控制单元固件刷写目标诊断头为 VNCI 或 PT3G");
  blockers.push("尚未核实本次实际有线连接；蓝牙及连接类型未知均不允许刷写");
  if (!container?.complete) blockers.push(container?.recognized ? "固件容器的引用、载荷或格式尚未完整核对" : "固件文件格式尚未解析，不能建立容器准备结果");
  // A self-supplied manifest and matching hash do not prove manufacturer authenticity.
  blockers.push("需要核实原厂来源与文件适用规则", "需要本次车辆身份、电压及会话检查", "需要该文件已核实的刷写、校验和故障恢复定义", "需要实车验收");
  return { kind: "oem-ecu-firmware-preparation", file, targetSoftware: manifest.targetSoftware,
    manufacturerSource: manifest.manufacturerSource, identity: manifest.identity,
    hashMatches: file.sha256 === manifest.sha256 && file.bytes === manifest.bytes, origin: "original-declared",
    container, transportRequirement, executionEnabled: false, writePayload: null, blockers };
}

export function createDiagnosticPreparation({ directory, desktopDirectory, chooseOpenFile, saveFile, connectionStatus, previewField, inspectFirmware }) {
  let firmware = null;
  let serial = Promise.resolve();
  const queued = (fn) => { const next = serial.then(fn, fn); serial = next.catch(() => {}); return next; };
  async function readJson(file) {
    const h = await fs.open(file, "r");
    try { if ((await h.stat()).size > 4 * 1024 * 1024) throw new Error("preparation-file-too-large"); return JSON.parse(await h.readFile("utf8")); }
    finally { await h.close(); }
  }
  const store = createCodingBackupStore({ directory, normalizeBackup: normalizeCodingBackup, normalizeStoredBackup: normalizeStoredCodingBackup });
  const journal = createCodingOperationJournal({ directory: path.join(directory, "operations") });
  const { load, persist, list } = store;
  async function perform(req) {
    if (!req || typeof req !== "object" || Array.isArray(req) || Object.keys(req).some((k) =>
      !["action", "ecu", "id", "did", "blockKey", "recordAt", "rawValue", "physicalValue", "scenario", "offset", "edits"].includes(k))) throw new Error("preparation-request-invalid");
    const batchAction = ["coding-batch-preview", "simulate-coding-batch"].includes(req.action);
    if (Object.hasOwn(req, "edits") && !batchAction) throw new Error("preparation-request-invalid");
    if (batchAction && Object.keys(req).some(k => !["action", "ecu", "id", "edits", "scenario"].includes(k))) throw new Error("preparation-request-invalid");
    if (Object.hasOwn(req, "offset") && req.action !== "operation-history") throw new Error("preparation-request-invalid");
    if (Object.hasOwn(req, "physicalValue") && (!["coding-preview", "simulate-coding"].includes(req.action)
      || Object.hasOwn(req, "rawValue") || typeof req.physicalValue !== "string" || !req.physicalValue.length
      || req.physicalValue.length > 128)) throw new Error("numeric-target-invalid");
    if (req.ecu !== undefined && !text(req.ecu)) throw new Error("preparation-ecu-invalid");
    if (req.action === "operation-history") {
      if (!text(req.ecu) || Object.keys(req).some(k => !["action", "ecu", "offset"].includes(k))
        || (req.offset !== undefined && (!Number.isSafeInteger(req.offset) || req.offset < 0))) throw new Error("preparation-history-request-invalid");
      const offset = req.offset ?? 0;
      const rows = (await journal.list()).filter(row => row.integrityError || row.metadata.ecu === req.ecu).map(row => row.integrityError
        ? { operationId: row.operationId, scope: "unassigned", integrityError: row.integrityError,
          requiresReconciliation: true, automaticWriteRetry: false, executionEnabled: false }
        : { operationId: row.operationId, scope: "current-ecu", ecu: row.metadata.ecu, mode: row.metadata.mode,
          phase: row.phase, backupId: row.metadata.backupId, createdUtc: row.createdUtc, updatedUtc: row.updatedUtc,
          targetBlocks: row.metadata.targets.length, changedBlocks: row.metadata.targets.filter(t => t.changed).length,
          intendedBlocks: row.intended.length, verifiedBlocks: row.verified.length,
          requiresReconciliation: row.requiresReconciliation, automaticWriteRetry: false, executionEnabled: false });
      rows.sort((a, b) => Number(b.requiresReconciliation) - Number(a.requiresReconciliation)
        || (b.updatedUtc || "").localeCompare(a.updatedUtc || "") || a.operationId.localeCompare(b.operationId));
      return { ok: true, operations: rows.slice(offset, offset + 40), history: { offset, limit: 40, total: rows.length,
        currentEcuTotal: rows.filter(row => row.scope === "current-ecu").length,
        unassignedTotal: rows.filter(row => row.scope === "unassigned").length,
        requiresReconciliation: rows.filter(row => row.requiresReconciliation).length } };
    }
    if (req.action === "list") return { ok: true, backups: await list(req.ecu) };
    if (req.action === "import-backup") {
      const file = await chooseOpenFile("backup"); if (!file) return { ok: true, canceled: true };
      const backup = normalizeCodingBackup({ ...await readJson(file), provenance: "imported" });
      if (backup.identity.ecu !== req.ecu) throw new Error("backup-current-ecu-mismatch");
      return { ok: true, ...await persist(backup), backups: await list(req.ecu) };
    }
    if (batchAction) {
      const stored = await load(req.id);
      if (stored.backup.identity.ecu !== req.ecu) throw new Error("backup-current-ecu-mismatch");
      const preview = await codingBatchPreview(stored.backup, req.edits, previewField);
      return { ok: true, ...preview, backupId: req.id,
        ...(req.action === "simulate-coding-batch" ? { result: await rehearse(preview.plan, stored.backup, req.scenario) } : {}) };
    }
    if (["backup", "export-backup", "restore-plan", "simulate-restore", "coding-options", "coding-preview", "simulate-coding"].includes(req.action)) {
      const stored = await load(req.id);
      if (stored.backup.identity.ecu !== req.ecu) throw new Error("backup-current-ecu-mismatch");
      if (req.action === "backup") return { ok: true, backup: stored.backup };
      if (req.action === "export-backup") {
        if (Object.keys(req).some(key => !["action", "ecu", "id"].includes(key))) throw new Error("preparation-request-invalid");
        return store.publishOriginal(stored.identityKey, typeof desktopDirectory === "function" ? desktopDirectory() : desktopDirectory);
      }
      if (req.action.startsWith("coding-") || req.action === "simulate-coding") {
        if (req.blockKey !== undefined && (typeof req.blockKey !== "string" || !/^(LID:[0-9A-F]{2}|DID:[0-9A-F]{4})$/.test(req.blockKey)
          || req.did !== undefined)) throw new Error("coding-field-invalid");
        const selectedKey = req.blockKey ?? (req.did !== undefined ? codingBlockKey({ did: req.did }) : null);
        const block = stored.backup.blocks.find((b) => codingBlockKey(b) === selectedKey);
        if (!block || !Number.isInteger(req.recordAt) || req.recordAt < 0 || (req.action !== "coding-options"
          && !Number.isInteger(req.rawValue) && req.physicalValue === undefined)) throw new Error("coding-field-invalid");
        const result = await previewField({ action: req.action === "coding-options" ? "coding-options" : "preview", category: "coding",
          generation: stored.backup.identity.generation, profileId: stored.backup.profileId, recordAt: req.recordAt,
          dataHex: block.dataHex, expectedReadRequestHex: blockReadRequest(block),
          ...(req.action !== "coding-options" ? req.physicalValue !== undefined
            ? { physicalValue: req.physicalValue } : { rawValue: req.rawValue } : {}) });
        if (req.action === "simulate-coding" && result.ok) {
          if (result.beforeHex !== block.dataHex || !hex(result.afterHex) || result.afterHex.length !== block.dataHex.length) throw new Error("coding-preview-invalid");
          const target = { ...stored.backup, blocks: stored.backup.blocks.map((item) => codingBlockKey(item) === selectedKey ? { ...item, dataHex: result.afterHex } : item) };
          const { originalHash, ...base } = codingRestorePlan(target, stored.backup);
          const plan = { ...base, kind: "current-ecu-coding-preview-plan", targetHash: originalHash };
          return { ...result, ...blockLocator(block), backupId: req.id, ok: true, plan,
            result: await rehearse(plan, stored.backup, req.scenario), executionEnabled: false, writePayload: null };
        }
        return { ...result, ...blockLocator(block), backupId: req.id, executionEnabled: false, writePayload: null };
      }
      const baseline = await store.firstOriginal(stored.identityKey);
      if (!baseline) throw new Error("backup-baseline-missing");
      const plan = codingRestorePlan(baseline.backup, stored.backup);
      if (req.action === "simulate-restore") return { ok: true, plan, result: await rehearse(plan, stored.backup, req.scenario) };
      return { ok: true, plan };
    }
    if (req.action === "prepare-firmware") {
      firmware = null;
      const mf = await chooseOpenFile("manifest"); if (!mf) return { ok: true, canceled: true };
      const manifest = await readJson(mf);
      firmwarePreparation(manifest, { sha256: manifest.sha256, bytes: manifest.bytes }, null, null);
      const file = await chooseOpenFile("firmware"); if (!file) return { ok: true, canceled: true };
      const digest = createHash("sha256"); let bytes = 0;
      const handle = await fs.open(file, "r");
      try {
        const size = (await handle.stat()).size;
        if (size <= 0 || size > 512 * 1024 * 1024) throw new Error("firmware-size-invalid");
        for await (const chunk of handle.createReadStream({ autoClose: false })) { bytes += chunk.length; digest.update(chunk); }
      } finally { await handle.close(); }
      const current = req.id ? (await load(req.id)).backup.identity : null;
      if (manifest.identity?.ecu !== req.ecu || (current && current.ecu !== req.ecu)) throw new Error("firmware-current-ecu-mismatch");
      const inspected = await inspectFirmware?.(file);
      if (!inspected?.ok || !inspected.container) throw new Error(inspected?.error || "firmware-container-inspector-unavailable");
      const selected = { name: path.basename(file), bytes, sha256: digest.digest("hex") };
      if (inspected.container.kind !== "ecu-firmware-container-inspection" || inspected.container.schemaVersion !== 1
        || inspected.container.file?.sha256 !== selected.sha256 || inspected.container.file?.bytes !== bytes
        || inspected.container.file?.name !== selected.name || inspected.container.executionEnabled !== false
        || inspected.container.writePayload !== null) throw new Error("firmware-container-file-changed");
      firmware = firmwarePreparation(manifest, selected, current, connectionStatus(), inspected.container);
      return { ok: true, preparation: firmware };
    }
    if (req.action === "export-firmware") {
      if (!firmware || firmware.identity.ecu !== req.ecu) throw new Error("firmware-not-prepared");
      return saveFile("ecu-firmware-preparation.json", firmware);
    }
    throw new Error("preparation-action-invalid");
  }
  async function rehearse(plan, current, scenario = "success") {
    if (!["success", "readback-mismatch", "disconnect", "identity-mismatch", "backup-failed"].includes(scenario)) throw new Error("simulation-scenario-invalid");
    const memory = new Map(current.blocks.map((block) => [codingBlockKey(block), block.dataHex]));
    return runCodingRehearsal(plan, { simulation: true, journal,
      readIdentity: async () => scenario === "identity-mismatch" ? { ...current.identity, software: "mismatch-simulated" } : current.identity,
      readCurrent: async () => ({ ...current, capturedUtc: new Date().toISOString(), provenance: "simulation" }),
      persistBackup: async (backup) => { if (scenario === "backup-failed") throw new Error("pre-write-backup-failed"); return persist(backup); },
      writeBlock: async (did, value) => { memory.set(did, value); if (scenario === "disconnect") throw new Error("connection-lost-outcome-unknown"); },
      readBlock: async (did) => scenario === "readback-mismatch" ? "mismatch-simulated" : memory.get(did),
    });
  }
  return { handle: (req) => queued(async () => {
    try { return { executionEnabled: false, liveVerified: false, writePayload: null, ...await perform(req) }; }
    catch (error) { return { ok: false, error: String(error.message || error), executionEnabled: false, liveVerified: false, writePayload: null }; }
  }) };
}
