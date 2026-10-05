import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const hex = (value, max = 8192) => typeof value === "string" && value.length <= max && /^(?:[0-9A-F]{2})+$/.test(value);
const text = (value) => typeof value === "string" && value.length > 0 && value.length <= 240;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const idFields = ["generation", "vin", "ecu", "hardware", "software"];
export function normalizeCodingBackup(input) {
  if (!input || input.schemaVersion !== 1 || input.kind !== "ecu-coding-backup" || !input.identity
    || !idFields.every((key) => text(input.identity[key])) || !["981", "982"].includes(input.identity.generation)
    || !/^[A-HJ-NPR-Z0-9]{17}$/.test(input.identity.vin) || !text(input.profileId)
    || !Array.isArray(input.blocks) || !input.blocks.length || input.blocks.length > 256
    || !Array.isArray(input.expectedDids) || input.expectedDids.length !== input.blocks.length
    || !input.expectedDids.every((did) => /^[0-9A-F]{4}$/.test(did))) throw new Error("backup-identity-or-coverage-invalid");
  const expected = [...input.expectedDids].sort();
  const blocks = input.blocks.map((block) => {
    if (!block || !/^[0-9A-F]{4}$/.test(block.did) || !hex(block.dataHex)) throw new Error("backup-block-invalid");
    return { did: block.did, dataHex: block.dataHex };
  }).sort((a, b) => a.did.localeCompare(b.did));
  if (new Set(expected).size !== expected.length || !same(expected, blocks.map((b) => b.did))) throw new Error("backup-incomplete");
  if (!text(input.capturedUtc) || !Number.isFinite(Date.parse(input.capturedUtc))) throw new Error("backup-time-invalid");
  // Imported completeness is a declared block list. It never authorizes a live write.
  return { schemaVersion: 1, kind: input.kind, identity: Object.fromEntries(idFields.map((key) => [key, input.identity[key]])),
    profileId: input.profileId, expectedDids: expected, blocks, capturedUtc: input.capturedUtc,
    provenance: input.provenance === "simulation" ? "simulation" : "imported", freshReadProven: false, completeness: "declared-block-list", liveVerified: false };
}

export function codingRestorePlan(original, current) {
  const before = normalizeCodingBackup(current), baseline = normalizeCodingBackup(original);
  if (!same(before.identity, baseline.identity) || before.profileId !== baseline.profileId) throw new Error("restore-identity-version-mismatch");
  if (!same(before.expectedDids, baseline.expectedDids)) throw new Error("restore-coverage-mismatch");
  const blocks = baseline.blocks.map((block, i) => {
    const prior = before.blocks[i];
    if (prior.dataHex.length !== block.dataHex.length) throw new Error("restore-block-length-mismatch");
    return { did: block.did, beforeHex: prior.dataHex, targetHex: block.dataHex, changed: prior.dataHex !== block.dataHex };
  });
  return { kind: "current-ecu-restore-plan", identity: baseline.identity, profileId: baseline.profileId, blocks,
    changedBlocks: blocks.filter((b) => b.changed).length, originalHash: hash(JSON.stringify(baseline)),
    currentHash: hash(JSON.stringify(before)), executionEnabled: false, writePayload: null,
    blockers: ["需要本次完整实车读取并持久化备份", "需要该版本已核实的写入、回读与恢复定义", "需要实车验收"] };
}

/** Executable offline state machine. Dependencies are application-owned simulation
 * objects, never a renderer command, live adapter or imported write recipe. */
export async function runCodingRehearsal(plan, dependencies) {
  const stages = [], readbacks = []; let writeAttemptCount = 0, backupId = null;
  if (dependencies.simulation !== true || plan.executionEnabled !== false || !Array.isArray(plan.blocks) || !plan.blocks.length || plan.blocks.length > 256
    || !plan.blocks.every((block) => /^[0-9A-F]{4}$/.test(block.did) && hex(block.beforeHex) && hex(block.targetHex) && block.beforeHex.length === block.targetHex.length)) throw new Error("rehearsal-only");
  try {
    stages.push("read-identity");
    const identity = await dependencies.readIdentity();
    if (!idFields.every((key) => identity?.[key] === plan.identity?.[key])) throw new Error("pre-write-identity-mismatch");
    stages.push("read-complete-current-coding");
    const current = normalizeCodingBackup(await dependencies.readCurrent());
    if (!same(current.identity, plan.identity) || current.profileId !== plan.profileId
      || !same(current.blocks.map((block) => ({ did: block.did, dataHex: block.dataHex })), plan.blocks.map((block) => ({ did: block.did, dataHex: block.beforeHex })))) throw new Error("pre-write-coding-changed");
    stages.push("persist-complete-current-backup");
    const persisted = await dependencies.persistBackup(current);
    if (!persisted?.id) throw new Error("pre-write-backup-failed");
    backupId = persisted.id;
    for (const block of plan.blocks) {
      if (!block.changed) continue;
      stages.push(`write-once:${block.did}`); writeAttemptCount++;
      await dependencies.writeBlock(block.did, block.targetHex);
      stages.push(`readback:${block.did}`);
      const read = await dependencies.readBlock(block.did);
      readbacks.push({ did: block.did, dataHex: read });
      if (read !== block.targetHex) throw new Error("readback-mismatch");
    }
    return { ok: true, simulation: true, liveVerified: false, stages, readbacks, backupId, writeAttemptCount, automaticWriteRetry: false };
  } catch (error) {
    return { ok: false, error: String(error.message || error), simulation: true, liveVerified: false,
      stages, readbacks, backupId, writeAttemptCount, automaticWriteRetry: false, recoveryRequired: writeAttemptCount > 0 };
  }
}

export function firmwarePreparation(manifest, file, current, transport) {
  if (!manifest || manifest.schemaVersion !== 1 || manifest.kind !== "oem-ecu-firmware-manifest"
    || manifest.origin !== "original" || !/^[a-f0-9]{64}$/.test(manifest.sha256) || !Number.isSafeInteger(manifest.bytes)
    || manifest.bytes <= 0 || !text(manifest.manufacturerSource) || !text(manifest.targetSoftware)
    || !manifest.identity || !idFields.every((key) => text(manifest.identity[key]))
    || !["981", "982"].includes(manifest.identity.generation) || !/^[A-HJ-NPR-Z0-9]{17}$/.test(manifest.identity.vin)) throw new Error("firmware-manifest-invalid");
  const blockers = [];
  if (file.sha256 !== manifest.sha256 || file.bytes !== manifest.bytes) blockers.push("固件内容与清单哈希或长度不匹配");
  if (!current || !idFields.every((key) => current[key] === manifest.identity[key])) blockers.push("车辆、控制单元或当前硬件软件版本不匹配");
  if (!transport?.connected || !transport.commOk || transport.purpose !== "diagnostic") blockers.push("尚未连接并核实诊断头");
  if (!transport?.selectedDeviceId?.startsWith("vnci:")) blockers.push("需要已核实的有线诊断头；蓝牙及连接类型未知均不允许刷写");
  // A self-supplied manifest and matching hash do not prove manufacturer authenticity.
  blockers.push("需要核实原厂来源与文件适用规则", "需要本次车辆身份、电压及会话检查", "需要该文件已核实的刷写、校验和故障恢复定义", "需要实车验收");
  return { kind: "oem-ecu-firmware-preparation", file, targetSoftware: manifest.targetSoftware,
    manufacturerSource: manifest.manufacturerSource, identity: manifest.identity,
    hashMatches: file.sha256 === manifest.sha256 && file.bytes === manifest.bytes, origin: "original-declared",
    executionEnabled: false, writePayload: null, blockers };
}

export function createDiagnosticPreparation({ directory, chooseOpenFile, saveFile, connectionStatus, previewField }) {
  let firmware = null;
  let serial = Promise.resolve();
  const queued = (fn) => { const next = serial.then(fn, fn); serial = next.catch(() => {}); return next; };
  async function readJson(file) {
    const h = await fs.open(file, "r");
    try { if ((await h.stat()).size > 4 * 1024 * 1024) throw new Error("preparation-file-too-large"); return JSON.parse(await h.readFile("utf8")); }
    finally { await h.close(); }
  }
  async function load(id) {
    if (!/^[a-f0-9]{64}$/.test(id || "")) throw new Error("backup-id-invalid");
    const stored = await readJson(path.join(directory, `${id}.json`));
    const backup = normalizeCodingBackup(stored.backup);
    if (hash(JSON.stringify(backup)) !== stored.sha256 || stored.sha256 !== id
      || hash(JSON.stringify({ identity: backup.identity, profileId: backup.profileId })) !== stored.identityKey) throw new Error("backup-integrity-failed");
    return { ...stored, backup };
  }
  async function persist(backup) {
    await fs.mkdir(directory, { recursive: true });
    const sha256 = hash(JSON.stringify(backup)), identityKey = hash(JSON.stringify({ identity: backup.identity, profileId: backup.profileId }));
    const stored = { backup, sha256, identityKey };
    async function durableExclusive(file, content) {
      const handle = await fs.open(file, "wx");
      try { await handle.writeFile(content); await handle.sync(); } finally { await handle.close(); }
    }
    try { await durableExclusive(path.join(directory, `${sha256}.json`), JSON.stringify(stored)); }
    catch (error) { if (error.code !== "EEXIST") throw error; await load(sha256); }
    // First complete imported baseline is immutable; later imports only add snapshots.
    try { await durableExclusive(path.join(directory, `baseline-${identityKey}.json`), JSON.stringify({ id: sha256 })); }
    catch (error) { if (error.code !== "EEXIST") throw error; }
    return { id: sha256, identityKey };
  }
  async function list(ecu) {
    let files; try { files = await fs.readdir(directory); } catch (error) { if (error.code === "ENOENT") return []; throw error; }
    const rows = [];
    for (const name of files.filter((n) => /^[a-f0-9]{64}\.json$/.test(n))) {
      const stored = await load(name.slice(0, -5));
      if (ecu && stored.backup.identity.ecu !== ecu) continue;
      const baseline = await readJson(path.join(directory, `baseline-${stored.identityKey}.json`));
      rows.push({ id: name.slice(0, -5), identity: stored.backup.identity, profileId: stored.backup.profileId,
        capturedUtc: stored.backup.capturedUtc, blockCount: stored.backup.blocks.length,
        original: baseline.id === name.slice(0, -5), provenance: stored.backup.provenance, freshReadProven: false });
    }
    return rows.sort((a, b) => b.capturedUtc.localeCompare(a.capturedUtc));
  }
  async function perform(req) {
    if (!req || typeof req !== "object" || Array.isArray(req) || Object.keys(req).some((k) =>
      !["action", "ecu", "id", "did", "recordAt", "rawValue", "scenario"].includes(k))) throw new Error("preparation-request-invalid");
    if (req.ecu !== undefined && !text(req.ecu)) throw new Error("preparation-ecu-invalid");
    if (req.action === "list") return { ok: true, backups: await list(req.ecu) };
    if (req.action === "import-backup") {
      const file = await chooseOpenFile("backup"); if (!file) return { ok: true, canceled: true };
      const backup = normalizeCodingBackup({ ...await readJson(file), provenance: "imported" });
      if (backup.identity.ecu !== req.ecu) throw new Error("backup-current-ecu-mismatch");
      return { ok: true, ...await persist(backup), backups: await list(req.ecu) };
    }
    if (["backup", "export-backup", "restore-plan", "simulate-restore", "coding-options", "coding-preview", "simulate-coding"].includes(req.action)) {
      const stored = await load(req.id);
      if (stored.backup.identity.ecu !== req.ecu) throw new Error("backup-current-ecu-mismatch");
      if (req.action === "backup") return { ok: true, backup: stored.backup };
      if (req.action === "export-backup") return saveFile("ecu-coding-backup.json", stored.backup);
      if (req.action.startsWith("coding-") || req.action === "simulate-coding") {
        const block = stored.backup.blocks.find((b) => b.did === req.did);
        if (!block || !Number.isInteger(req.recordAt) || req.recordAt < 0 || (req.action !== "coding-options" && !Number.isInteger(req.rawValue))) throw new Error("coding-field-invalid");
        const result = await previewField({ action: req.action === "coding-options" ? "coding-options" : "preview", category: "coding",
          generation: stored.backup.identity.generation, profileId: stored.backup.profileId, recordAt: req.recordAt,
          dataHex: block.dataHex, ...(req.action !== "coding-options" ? { rawValue: req.rawValue } : {}) });
        if (req.action === "simulate-coding" && result.ok) {
          if (result.beforeHex !== block.dataHex || !hex(result.afterHex) || result.afterHex.length !== block.dataHex.length) throw new Error("coding-preview-invalid");
          const target = { ...stored.backup, blocks: stored.backup.blocks.map((item) => item.did === block.did ? { ...item, dataHex: result.afterHex } : item) };
          const { originalHash, ...base } = codingRestorePlan(target, stored.backup);
          const plan = { ...base, kind: "current-ecu-coding-preview-plan", targetHash: originalHash };
          return { ok: true, plan, result: await rehearse(plan, stored.backup, req.scenario) };
        }
        return { ...result, did: block.did, backupId: req.id, executionEnabled: false, writePayload: null };
      }
      const baseId = (await readJson(path.join(directory, `baseline-${stored.identityKey}.json`))).id;
      const baseline = (await load(baseId)).backup;
      const plan = codingRestorePlan(baseline, stored.backup);
      if (req.action === "simulate-restore") return { ok: true, plan, result: await rehearse(plan, stored.backup, req.scenario) };
      return { ok: true, plan };
    }
    if (req.action === "prepare-firmware") {
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
      firmware = firmwarePreparation(manifest, { name: path.basename(file), bytes, sha256: digest.digest("hex") }, current, connectionStatus());
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
    const memory = new Map(current.blocks.map((block) => [block.did, block.dataHex]));
    return runCodingRehearsal(plan, { simulation: true,
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
