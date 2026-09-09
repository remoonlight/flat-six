/**
 * 车库模型块 ↔ OEM sku 关联。落盘 `.local/model-oem-links.json`。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ensureLocalFromSeed } from "./ensure-local-snapshot.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const LOCAL_ROOT = path.join(repoRoot, ".local");
const LINKS_PATH = path.join(LOCAL_ROOT, "model-oem-links.json");
const LINKS_SEED = path.join(
  repoRoot,
  "data",
  "seed",
  "xray",
  "model-oem-links.seed.json",
);

function emptyFile() {
  return {
    version: 1,
    note: "garage model block → part sku(s); 装配优先，mesh 可补",
    updated_at: null,
    links: [],
  };
}

function linkId(kind, ref, assemblyId) {
  if (kind === "mesh") {
    const asm = String(assemblyId || "").trim() || "_";
    return `mesh:${asm}:${ref}`;
  }
  return `${kind}:${ref}`;
}

function sameMeshSlot(link, ref, assemblyId) {
  if (link.kind !== "mesh" || link.ref !== ref) return false;
  const want = String(assemblyId || "").trim();
  const have = String(link.assemblyId || "").trim();
  if (want && have) return want === have;
  if (!want && !have) return true;
  return false;
}

function migrateMeshIds(file) {
  let changed = false;
  file.links = (file.links || []).map((l) => {
    if (l.kind !== "mesh") return l;
    const id = linkId("mesh", l.ref, l.assemblyId);
    if (l.id === id) return l;
    changed = true;
    return { ...l, id };
  });
  return changed;
}

function readLinksFile() {
  ensureLocalFromSeed(LINKS_PATH, LINKS_SEED);
  if (!fs.existsSync(LINKS_PATH)) return emptyFile();
  try {
    const raw = JSON.parse(fs.readFileSync(LINKS_PATH, "utf8"));
    return {
      ...emptyFile(),
      ...raw,
      links: Array.isArray(raw.links) ? raw.links : [],
    };
  } catch {
    return emptyFile();
  }
}

export function loadModelOemLinks() {
  const file = readLinksFile();
  if (migrateMeshIds(file)) return saveModelOemLinks(file);
  return file;
}

export function saveModelOemLinks(file) {
  const next = {
    version: 1,
    note: file?.note || emptyFile().note,
    updated_at: new Date().toISOString(),
    links: Array.isArray(file?.links) ? file.links : [],
  };
  fs.mkdirSync(path.dirname(LINKS_PATH), { recursive: true });
  fs.writeFileSync(LINKS_PATH, JSON.stringify(next, null, 2) + "\n", "utf8");
  return next;
}

/**
 * @param {{ kind: string; ref: string; assemblyId?: string | null; skus?: string[]; note?: string | null }} link
 */
export function upsertModelOemLink(link) {
  const kind = String(link.kind || "");
  const ref = String(link.ref || "");
  if (!["assembly", "mesh", "flow"].includes(kind) || !ref) {
    throw new Error("model_oem_link_incomplete");
  }
  const skus = Array.isArray(link.skus)
    ? [...new Set(link.skus.map((s) => String(s).trim()).filter(Boolean))]
    : [];
  const assemblyId = link.assemblyId ? String(link.assemblyId) : null;
  const id = linkId(kind, ref, assemblyId);
  const file = loadModelOemLinks();
  const row = {
    id,
    kind,
    ref,
    assemblyId,
    skus,
    note: link.note ? String(link.note) : null,
  };
  const idx = file.links.findIndex((l) =>
    l.id === id
      ? true
      : kind === "mesh"
        ? sameMeshSlot(l, ref, assemblyId)
        : l.kind === kind && l.ref === ref,
  );
  if (idx >= 0) file.links[idx] = row;
  else file.links.push(row);
  if (skus.length === 0) {
    file.links = file.links.filter((l) => l.id !== id);
  }
  return saveModelOemLinks(file);
}

/**
 * @param {string} kind
 * @param {string} ref
 * @param {string} [sku] 省略则删整条
 * @param {string | null} [assemblyId] mesh 必须带装配，否则会误删同名 mesh
 */
export function removeModelOemLink(kind, ref, sku, assemblyId) {
  const id = linkId(kind, ref, assemblyId);
  const file = loadModelOemLinks();
  const match = (l) =>
    l.id === id
      ? true
      : kind === "mesh"
        ? sameMeshSlot(l, ref, assemblyId)
        : l.kind === kind && l.ref === ref;
  if (!sku) {
    file.links = file.links.filter((l) => !match(l));
    return saveModelOemLinks(file);
  }
  const row = file.links.find(match);
  if (!row) return file;
  row.skus = (row.skus || []).filter((s) => s !== sku);
  if (row.skus.length === 0) {
    file.links = file.links.filter((l) => l.id !== row.id);
  }
  return saveModelOemLinks(file);
}

/** 从 glb 抽 node/mesh 名（不依赖 three）。 */
export function listGlbNodeNames(absPath) {
  if (!fs.existsSync(absPath)) return [];
  const buf = fs.readFileSync(absPath);
  if (buf.length < 20) return [];
  const jsonLen = buf.readUInt32LE(12);
  const json = JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
  const names = new Set();
  for (const n of json.nodes || []) {
    if (n?.name) names.add(String(n.name));
  }
  for (const m of json.meshes || []) {
    if (m?.name) names.add(String(m.name));
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

/**
 * 车库可关联目录：父块（车身 + 装配 + flow）→ 子 mesh。
 * @param {{
 *   bodyRel?: string | null;
 *   layers?: { id: string; label?: string; label_zh?: string; glbRel?: string | null }[];
 *   flows?: { id: string; label?: string }[];
 * }} scene
 */
export function listGarageModelCatalog(scene = {}) {
  /** @type {{ kind: string; ref: string; label: string; assemblyId: string | null }[]} */
  const parents = [];
  /** @type {Record<string, { kind: string; ref: string; label: string; assemblyId: string | null }[]>} */
  const childrenByParent = {};

  function absFromRel(rel) {
    if (!rel) return null;
    return path.isAbsolute(rel) ? rel : path.join(LOCAL_ROOT, rel);
  }

  const bodyRel = scene.bodyRel;
  if (bodyRel) {
    parents.push({
      kind: "assembly",
      ref: "body",
      label: "车身",
      assemblyId: "body",
    });
    const abs = absFromRel(bodyRel);
    childrenByParent.body = abs
      ? listGlbNodeNames(abs).map((name) => ({
          kind: "mesh",
          ref: name,
          label: name,
          assemblyId: "body",
        }))
      : [];
  }

  for (const l of scene.layers || []) {
    const id = String(l.id || "");
    if (!id || id === "body") continue;
    parents.push({
      kind: "assembly",
      ref: id,
      label: l.label_zh || l.label || id,
      assemblyId: id,
    });
    const abs = absFromRel(l.glbRel);
    childrenByParent[id] = abs
      ? listGlbNodeNames(abs).map((name) => ({
          kind: "mesh",
          ref: name,
          label: name,
          assemblyId: id,
        }))
      : [];
  }

  for (const f of scene.flows || []) {
    const id = String(f.id || "");
    if (!id) continue;
    parents.push({
      kind: "flow",
      ref: id,
      label: f.label || id,
      assemblyId: null,
    });
    // 管路无独立子 mesh：自身作为可填 OEM 的叶子
    childrenByParent[id] = [
      {
        kind: "flow",
        ref: id,
        label: f.label || id,
        assemblyId: null,
      },
    ];
  }

  const assemblies = parents.filter((p) => p.kind === "assembly");
  const bodyMeshes = childrenByParent.body ?? [];
  const flows = parents.filter((p) => p.kind === "flow");
  return {
    parents,
    childrenByParent,
    assemblies,
    bodyMeshes,
    flows,
    path: LINKS_PATH,
  };
}

export const MODEL_OEM_LINKS_PATH = LINKS_PATH;
