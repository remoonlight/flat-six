/**
 * flat-six X-ray assembly registry + local transform overrides under .local/.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../../..");
const LOCAL_ROOT = path.join(repoRoot, ".local");
const SEED_PATH = path.join(repoRoot, "data", "seed", "xray", "assemblies.json");
const GARAGE_SEED_PATH = path.join(
  repoRoot,
  "data",
  "seed",
  "xray",
  "garage-assemblies.json",
);
const GARAGE_FLOWS_PATH = path.join(
  repoRoot,
  "data",
  "seed",
  "xray",
  "garage-flows.json",
);
const TRANSFORMS_PATH = path.join(LOCAL_ROOT, "xray-transforms.json");
const MESH_STATE_PATH = path.join(LOCAL_ROOT, "xray-mesh-state.json");

/** @typedef {{ position?: number[]; rotationEuler?: number[]; scale?: number[] }} XrayTransform */
/** @typedef {Record<string, XrayTransform>} XrayTransformLayers */
/** @typedef {{ visible?: boolean, transform?: XrayTransform }} XrayMeshEntry */
/** @typedef {{ visible?: boolean, meshes?: Record<string, XrayMeshEntry> }} XrayLayerMeshState */
/** @typedef {{ version: number, note: string, layers: Record<string, XrayLayerMeshState> }} XrayMeshStateFile */

function absForRel(rel) {
  const normalized = String(rel || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  if (
    !normalized ||
    normalized.includes("..") ||
    path.isAbsolute(normalized)
  ) {
    throw new Error("xray_rel_invalid");
  }
  if (
    !normalized.startsWith("cms-rip/") &&
    !normalized.startsWith("flat-six/") &&
    !normalized.startsWith("petka-models/")
  ) {
    throw new Error("xray_rel_root");
  }
  const abs = path.normalize(path.join(LOCAL_ROOT, normalized));
  if (!abs.startsWith(LOCAL_ROOT)) throw new Error("xray_rel_escape");
  return { normalized, abs };
}

function loadSeedFrom(seedPath) {
  const raw = JSON.parse(fs.readFileSync(seedPath, "utf8"));
  if (!raw || !Array.isArray(raw.assemblies)) {
    throw new Error("xray_seed_invalid");
  }
  return raw;
}

function loadSeed() {
  return loadSeedFrom(SEED_PATH);
}

function loadGarageSeed() {
  return loadSeedFrom(GARAGE_SEED_PATH);
}

function loadGarageFlows() {
  if (!fs.existsSync(GARAGE_FLOWS_PATH)) return [];
  try {
    const raw = JSON.parse(fs.readFileSync(GARAGE_FLOWS_PATH, "utf8"));
    return Array.isArray(raw?.flows) ? raw.flows : [];
  } catch {
    return [];
  }
}

function emptyTransforms() {
  return { version: 1, note: "hand overrides; do not commit", layers: {} };
}

function normalizeTransform(t) {
  if (!t || typeof t !== "object") return null;
  const position = Array.isArray(t.position)
    ? t.position.map(Number).slice(0, 3)
    : [0, 0, 0];
  while (position.length < 3) position.push(0);
  const rotationEuler = Array.isArray(t.rotationEuler)
    ? t.rotationEuler.map(Number).slice(0, 3)
    : [0, 0, 0];
  while (rotationEuler.length < 3) rotationEuler.push(0);
  const scale = Array.isArray(t.scale)
    ? t.scale.map(Number).slice(0, 3)
    : [1, 1, 1];
  while (scale.length < 3) scale.push(1);
  return { position, rotationEuler, scale };
}

export function loadXrayTransforms() {
  if (!fs.existsSync(TRANSFORMS_PATH)) return emptyTransforms();
  try {
    const raw = JSON.parse(fs.readFileSync(TRANSFORMS_PATH, "utf8"));
    const layers = {};
    const src = raw?.layers && typeof raw.layers === "object" ? raw.layers : {};
    for (const [id, t] of Object.entries(src)) {
      const n = normalizeTransform(t);
      if (n) layers[id] = n;
    }
    return {
      version: 1,
      note: raw?.note || emptyTransforms().note,
      layers,
    };
  } catch {
    return emptyTransforms();
  }
}

/**
 * @param {string} assemblyId
 * @param {XrayTransform} transform
 */
export function setXrayTransform(assemblyId, transform) {
  const id = String(assemblyId || "");
  if (!id) throw new Error("xray_transform_id");
  const n = normalizeTransform(transform);
  if (!n) throw new Error("xray_transform_invalid");
  const cur = loadXrayTransforms();
  cur.layers[id] = n;
  cur.version = 1;
  fs.mkdirSync(path.dirname(TRANSFORMS_PATH), { recursive: true });
  fs.writeFileSync(
    TRANSFORMS_PATH,
    JSON.stringify(cur, null, 2) + "\n",
    "utf8",
  );
  return cur;
}

function sceneFromSeed(seed) {
  const transforms = loadXrayTransforms();
  const assemblies = seed.assemblies.map((a) => {
    const { abs, normalized } = absForRel(a.glbRel);
    const present = fs.existsSync(abs);
    const loadLayer = !a.aliasOfGlb;
    return {
      id: a.id,
      label: a.label,
      label_zh: a.label_zh || a.label,
      glbRel: normalized,
      manifestRel: a.manifestRel || null,
      hotspot3d: a.hotspot3d || "0 0 0",
      displayRadius: a.displayRadius ?? null,
      carSpace: Boolean(a.carSpace),
      worldScale: a.worldScale ?? 1,
      bilateral: Boolean(a.bilateral),
      lateralOffset: a.lateralOffset ?? 0.75,
      hideInUnified: Array.isArray(a.hideInUnified) ? a.hideInUnified : [],
      source: a.source || "manual",
      bridgeZone: a.bridgeZone || null,
      bridgeHotspot: a.bridgeHotspot || null,
      aliasOfGlb: a.aliasOfGlb || null,
      meshStateOf: a.meshStateOf || a.aliasOfGlb || null,
      duplicateGlb: Boolean(a.duplicateGlb),
      mirrorX: Boolean(a.mirrorX),
      xrayGroup: a.xrayGroup || null,
      xrayGroupZh: a.xrayGroupZh || null,
      garageStructure: a.garageStructure || null,
      loadLayer,
      present,
      bytes: present ? fs.statSync(abs).size : 0,
      transform: transforms.layers[a.id] || null,
    };
  });

  let bodyShell = null;
  if (seed.bodyShell?.rel) {
    const { abs, normalized } = absForRel(seed.bodyShell.rel);
    const present = fs.existsSync(abs);
    bodyShell = {
      rel: normalized,
      opacity: Number(seed.bodyShell.opacity ?? 0.16),
      attribution: seed.bodyShell.attribution || null,
      present,
      bytes: present ? fs.statSync(abs).size : 0,
      transform: transforms.layers.body || null,
    };
  }

  return {
    version: seed.version || 1,
    note: seed.note || "",
    caveatZh: seed.caveatZh || "",
    axle: seed.axle || null,
    bodyShell,
    assemblies,
    transformsPath: path.relative(repoRoot, TRANSFORMS_PATH).replace(/\\/g, "/"),
  };
}

function layersFromScene(scene) {
  const seen = new Set();
  const layers = [];
  for (const a of scene.assemblies) {
    if (!a.present || !a.loadLayer) continue;
    if (seen.has(a.glbRel) && !a.duplicateGlb) continue;
    if (!a.duplicateGlb) seen.add(a.glbRel);
    layers.push(a);
  }
  return { ...scene, layers };
}

export function listXrayAssemblies() {
  return sceneFromSeed(loadSeed());
}

/** Layers actually loaded into the unified scene (deduped by glbRel). */
export function listXrayLayers() {
  return layersFromScene(listXrayAssemblies());
}

/** 车库·零件浏览器专用：flat-six components + flow 层（不影响 Locator）。 */
export function listGarageXrayLayers() {
  const scene = layersFromScene(sceneFromSeed(loadGarageSeed()));
  return { ...scene, flows: loadGarageFlows() };
}

export function bridgeForAssembly(assemblyId) {
  const scene = listXrayAssemblies();
  if (assemblyId === "body") {
    return { assemblyId: "body", zoneId: "body", hotspotId: "body" };
  }
  const a = scene.assemblies.find((x) => x.id === assemblyId);
  if (!a) return null;
  return {
    assemblyId: a.id,
    zoneId: a.bridgeZone || a.id,
    hotspotId: a.bridgeHotspot || a.id,
  };
}

function emptyMeshState() {
  return {
    version: 1,
    note: "per-mesh TRS/visible; layer × mesh; do not commit",
    layers: {},
  };
}

/**
 * Layer TRS keys in xray-transforms.json — always the assembly id (never meshStateOf).
 * @param {string} assemblyId
 */
export function layerTransformAssemblyId(assemblyId) {
  return String(assemblyId || "");
}

function assemblyById(assemblyId) {
  const id = String(assemblyId || "");
  for (const seed of [loadSeed(), loadGarageSeed()]) {
    const a = seed.assemblies.find((x) => x.id === id);
    if (a) return a;
  }
  return null;
}

/**
 * Load-layer id for mesh state keys (aliases / mirrors share source meshes).
 * @param {string} assemblyId
 */
export function meshStateAssemblyId(assemblyId) {
  const id = String(assemblyId || "");
  if (id === "body") return "body";
  const a = assemblyById(id);
  if (!a) return id;
  return a.meshStateOf || a.aliasOfGlb || id;
}

export function loadXrayMeshState() {
  if (!fs.existsSync(MESH_STATE_PATH)) return emptyMeshState();
  try {
    const raw = JSON.parse(fs.readFileSync(MESH_STATE_PATH, "utf8"));
    const layers = {};
    const src = raw?.layers && typeof raw.layers === "object" ? raw.layers : {};
    for (const [id, layer] of Object.entries(src)) {
      if (!layer || typeof layer !== "object") continue;
      /** @type {Record<string, XrayMeshEntry>} */
      const meshes = {};
      const meshSrc =
        layer.meshes && typeof layer.meshes === "object" ? layer.meshes : {};
      for (const [meshName, entry] of Object.entries(meshSrc)) {
        if (!entry || typeof entry !== "object") continue;
        meshes[meshName] = {
          visible: entry.visible !== false,
          transform: normalizeTransform(entry.transform) || {
            position: [0, 0, 0],
            rotationEuler: [0, 0, 0],
            scale: [1, 1, 1],
          },
        };
      }
      layers[id] = {
        visible: layer.visible !== false,
        meshes,
      };
    }
    return {
      version: 1,
      note: raw?.note || emptyMeshState().note,
      layers,
    };
  } catch {
    return emptyMeshState();
  }
}

function writeMeshState(state) {
  fs.mkdirSync(path.dirname(MESH_STATE_PATH), { recursive: true });
  fs.writeFileSync(
    MESH_STATE_PATH,
    JSON.stringify(state, null, 2) + "\n",
    "utf8",
  );
  return state;
}

/**
 * @param {string} assemblyId load-layer id (engine|susp|body) or alias (resolved)
 * @param {string} meshName
 * @param {{ visible?: boolean, transform?: XrayTransform }} patch
 */
export function setXrayMeshEntry(assemblyId, meshName, patch) {
  const id = meshStateAssemblyId(assemblyId);
  const name = String(meshName || "");
  if (!id || !name) throw new Error("xray_mesh_id");
  const cur = loadXrayMeshState();
  if (!cur.layers[id]) cur.layers[id] = { visible: true, meshes: {} };
  const prev = cur.layers[id].meshes[name] || {
    visible: true,
    transform: {
      position: [0, 0, 0],
      rotationEuler: [0, 0, 0],
      scale: [1, 1, 1],
    },
  };
  const next = { ...prev };
  if (patch && typeof patch.visible === "boolean") {
    next.visible = patch.visible;
  }
  if (patch && patch.transform) {
    const n = normalizeTransform(patch.transform);
    if (!n) throw new Error("xray_mesh_transform_invalid");
    next.transform = n;
  }
  cur.layers[id].meshes[name] = next;
  cur.version = 1;
  return writeMeshState(cur);
}

/**
 * @param {string} assemblyId
 * @param {boolean} visible
 */
export function setXrayLayerVisible(assemblyId, visible) {
  const id = String(assemblyId || "");
  if (!id) throw new Error("xray_layer_id");
  const cur = loadXrayMeshState();
  if (!cur.layers[id]) cur.layers[id] = { visible: true, meshes: {} };
  cur.layers[id].visible = Boolean(visible);
  cur.version = 1;
  return writeMeshState(cur);
}

/** Exported for selfcheck / tests. */
export const _test = {
  absForRel,
  loadSeed,
  loadGarageSeed,
  loadGarageFlows,
  normalizeTransform,
  SEED_PATH,
  GARAGE_SEED_PATH,
  GARAGE_FLOWS_PATH,
  TRANSFORMS_PATH,
  MESH_STATE_PATH,
  LOCAL_ROOT,
};
