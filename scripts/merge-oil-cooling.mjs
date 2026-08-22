/**
 * Bake 360-000 + 104-005 + 105-005 → 机油冷却系统 GLB.
 *
 * - Layer TRS from .local/xray-transforms.json
 * - Sub-mesh TRS from .local/xray-mesh-state.json
 * - 104-005 冲突名 _1045；105-005 冲突名 _1055
 * - Output: .local/petka-models/merged/oil-cooling.glb（装配 id 仍为 pm-360-000）
 *
 * Usage: node scripts/merge-oil-cooling.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

if (typeof globalThis.self === "undefined") globalThis.self = globalThis;
if (typeof globalThis.FileReader === "undefined") {
  globalThis.FileReader = class FileReader {
    result = null;
    onloadend = null;
    onerror = null;
    readAsArrayBuffer(blob) {
      Promise.resolve(blob.arrayBuffer())
        .then((buf) => {
          this.result = buf;
          this.onloadend?.({ target: this });
        })
        .catch((err) => this.onerror?.(err));
    }
  };
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const desktopRequire = createRequire(
  path.join(root, "apps", "desktop", "package.json"),
);
const threeEntry = desktopRequire.resolve("three");
const threeRoot = path.resolve(path.dirname(threeEntry), "..");
const THREE = await import(
  pathToFileURL(path.join(threeRoot, "build/three.module.js")).href
);
const { GLTFLoader } = await import(
  pathToFileURL(path.join(threeRoot, "examples/jsm/loaders/GLTFLoader.js")).href
);
const { GLTFExporter } = await import(
  pathToFileURL(
    path.join(threeRoot, "examples/jsm/exporters/GLTFExporter.js"),
  ).href
);

const OUT_ID = "pm-360-000";
const PREBAKE_ID = "pm-360-000__prebake";
const SRC_104 = "pm-104-005";
const SRC_105 = "pm-105-005";
const OUT_REL = "petka-models/merged/oil-cooling.glb";
const OUT_ABS = path.join(root, ".local", OUT_REL.replace(/\//g, path.sep));
const TF_PATH = path.join(root, ".local", "xray-transforms.json");
const MS_PATH = path.join(root, ".local", "xray-mesh-state.json");

const IDENTITY = {
  position: [0, 0, 0],
  rotationEuler: [0, 0, 0],
  scale: [1, 1, 1],
};

/** @type {Array<{ id: string, glb: string, meshStateId: string, collideTag: string | null }>} */
const PARTS = [
  {
    id: OUT_ID,
    glb: "petka-models/360-000.glb",
    meshStateId: OUT_ID,
    collideTag: null,
  },
  {
    id: SRC_104,
    glb: "petka-models/104-005.glb",
    meshStateId: SRC_104,
    collideTag: "_1045",
  },
  {
    id: SRC_105,
    glb: "petka-models/105-005.glb",
    meshStateId: SRC_105,
    collideTag: "_1055",
  },
];

function loadJson(p, fallback) {
  if (!fs.existsSync(p)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return fallback;
  }
}

function loadGlb(abs) {
  const buf = fs.readFileSync(abs);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(
      ab,
      "",
      (gltf) => resolve(gltf.scene),
      (err) => reject(err instanceof Error ? err : new Error(String(err))),
    );
  });
}

function applyManualTransform(rootObj, t) {
  if (!t) return;
  const [px, py, pz] = t.position || [0, 0, 0];
  const [rx, ry, rz] = t.rotationEuler || [0, 0, 0];
  const [sx, sy, sz] = t.scale || [1, 1, 1];
  rootObj.position.x += px || 0;
  rootObj.position.y += py || 0;
  rootObj.position.z += pz || 0;
  rootObj.rotation.x += rx || 0;
  rootObj.rotation.y += ry || 0;
  rootObj.rotation.z += rz || 0;
  rootObj.scale.x *= sx ?? 1;
  rootObj.scale.y *= sy ?? 1;
  rootObj.scale.z *= sz ?? 1;
}

function stripMaps(rootObj) {
  rootObj.traverse((o) => {
    if (!o.isMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    const next = mats.map((m) => {
      const color = m?.color?.clone?.() ?? new THREE.Color(0x888888);
      const mat = new THREE.MeshStandardMaterial({
        color,
        metalness: typeof m?.metalness === "number" ? m.metalness : 0.2,
        roughness: typeof m?.roughness === "number" ? m.roughness : 0.6,
        side: THREE.DoubleSide,
        transparent: Boolean(m?.transparent),
        opacity: typeof m?.opacity === "number" ? m.opacity : 1,
      });
      m?.dispose?.();
      return mat;
    });
    o.material = next.length === 1 ? next[0] : next;
  });
}

function uniqueName(srcName, collideTag, used) {
  let out = srcName;
  if (!used.has(out)) return out;
  const tag = collideTag || "_x";
  out = `${srcName}${tag}`;
  let i = 2;
  while (used.has(out)) {
    out = `${srcName}${tag}${i}`;
    i += 1;
  }
  return out;
}

function isIdentityTf(t) {
  if (!t) return true;
  const [px, py, pz] = t.position || [0, 0, 0];
  const [rx, ry, rz] = t.rotationEuler || [0, 0, 0];
  const [sx, sy, sz] = t.scale || [1, 1, 1];
  return (
    px === 0 &&
    py === 0 &&
    pz === 0 &&
    rx === 0 &&
    ry === 0 &&
    rz === 0 &&
    sx === 1 &&
    sy === 1 &&
    sz === 1
  );
}

function layerHasPose(t) {
  return !isIdentityTf(t);
}

{
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  if (fs.existsSync(MS_PATH)) {
    fs.copyFileSync(
      MS_PATH,
      path.join(root, ".local", `xray-mesh-state.pre-oil-cooling-merge.${stamp}.json`),
    );
  }
  if (fs.existsSync(TF_PATH)) {
    fs.copyFileSync(
      TF_PATH,
      path.join(root, ".local", `xray-transforms.pre-oil-cooling-merge.${stamp}.json`),
    );
  }
}

const transforms = loadJson(TF_PATH, { layers: {} });
const meshState = loadJson(MS_PATH, { layers: {} });

const live360 = transforms.layers?.[OUT_ID];
const pre360 = transforms.layers?.[PREBAKE_ID];
const source360Tf =
  pre360 && layerHasPose(pre360)
    ? structuredClone(pre360)
    : live360 && layerHasPose(live360)
      ? structuredClone(live360)
      : pre360
        ? structuredClone(pre360)
        : live360
          ? structuredClone(live360)
          : { ...IDENTITY };

const source104Tf = transforms.layers?.[SRC_104]
  ? structuredClone(transforms.layers[SRC_104])
  : { ...IDENTITY };

const source105Tf = transforms.layers?.[SRC_105]
  ? structuredClone(transforms.layers[SRC_105])
  : { ...IDENTITY };

function hasBakedSuffix(name) {
  return name.includes("_1045") || name.includes("_1055");
}

const preMeshes = meshState.layers?.[PREBAKE_ID]?.meshes || {};
const liveMeshes = meshState.layers?.[OUT_ID]?.meshes || {};
const source360Meshes =
  Object.keys(preMeshes).length &&
  !Object.keys(preMeshes).some(hasBakedSuffix)
    ? structuredClone(preMeshes)
    : Object.keys(liveMeshes).length &&
        !Object.keys(liveMeshes).some(hasBakedSuffix)
      ? structuredClone(liveMeshes)
      : structuredClone(preMeshes);

const source104Meshes = meshState.layers?.[SRC_104]?.meshes || {};
const source105Meshes = meshState.layers?.[SRC_105]?.meshes || {};

function layerTfFor(id) {
  if (id === OUT_ID) return source360Tf;
  if (id === SRC_104) return source104Tf;
  if (id === SRC_105) return source105Tf;
  return transforms.layers?.[id] || IDENTITY;
}

function meshesFor(meshStateId) {
  if (meshStateId === OUT_ID) return source360Meshes;
  if (meshStateId === SRC_104) return source104Meshes;
  if (meshStateId === SRC_105) return source105Meshes;
  return meshState.layers?.[meshStateId]?.meshes || {};
}

const rootGroup = new THREE.Group();
rootGroup.name = "机油冷却系统";

/** @type {Record<string, { visible: boolean, transform: typeof IDENTITY }>} */
const outMeshes = {};
let meshCount = 0;

for (const part of PARTS) {
  const abs = path.join(root, ".local", part.glb.replace(/\//g, path.sep));
  if (!fs.existsSync(abs)) {
    console.error("missing", abs);
    process.exit(1);
  }
  const scene = await loadGlb(abs);
  scene.name = part.id;

  const layerMeshes = meshesFor(part.meshStateId);
  const usedNames = new Set(Object.keys(outMeshes));
  scene.traverse((o) => {
    if (!o.isMesh) return;
    const srcName = o.name || `unnamed:${o.uuid.slice(0, 8)}`;
    const entry = layerMeshes[srcName];
    if (entry?.transform) applyManualTransform(o, entry.transform);
    const outName = uniqueName(srcName, part.collideTag, usedNames);
    usedNames.add(outName);
    o.name = outName;
    const visible = entry?.visible !== false;
    o.visible = visible;
    outMeshes[outName] = {
      visible,
      transform: {
        position: [0, 0, 0],
        rotationEuler: [0, 0, 0],
        scale: [1, 1, 1],
      },
    };
    meshCount += 1;
  });

  const wrap = new THREE.Group();
  wrap.name = part.id;
  wrap.add(scene);
  applyManualTransform(wrap, layerTfFor(part.id));

  rootGroup.add(wrap);
  console.log("baked", part.id, { layer: layerTfFor(part.id).position });
}

rootGroup.updateMatrixWorld(true);
stripMaps(rootGroup);

fs.mkdirSync(path.dirname(OUT_ABS), { recursive: true });
const exporter = new GLTFExporter();
const glb = await new Promise((resolve, reject) => {
  exporter.parse(
    rootGroup,
    (result) => resolve(Buffer.from(result)),
    (err) => reject(err),
    { binary: true, onlyVisible: false },
  );
});
fs.writeFileSync(OUT_ABS, glb);

const ms = loadJson(MS_PATH, {
  version: 1,
  note: "per-mesh TRS/visible; layer × mesh; do not commit",
  layers: {},
});
ms.layers = ms.layers || {};
if (
  !ms.layers[PREBAKE_ID] ||
  !Object.keys(ms.layers[PREBAKE_ID]?.meshes || {}).length
) {
  ms.layers[PREBAKE_ID] = {
    visible: true,
    meshes: structuredClone(source360Meshes),
  };
}
ms.layers[OUT_ID] = { visible: true, meshes: outMeshes };
ms.version = 1;
fs.writeFileSync(MS_PATH, JSON.stringify(ms, null, 2) + "\n", "utf8");

const tf = loadJson(TF_PATH, {
  version: 1,
  note: "hand overrides; do not commit",
  layers: {},
});
tf.layers = tf.layers || {};
if (!tf.layers[PREBAKE_ID] || !layerHasPose(tf.layers[PREBAKE_ID])) {
  tf.layers[PREBAKE_ID] = structuredClone(source360Tf);
}
tf.layers[OUT_ID] = { ...IDENTITY };
tf.version = 1;
fs.writeFileSync(TF_PATH, JSON.stringify(tf, null, 2) + "\n", "utf8");

console.log(
  JSON.stringify(
    {
      ok: true,
      id: OUT_ID,
      out: OUT_REL,
      bytes: glb.length,
      meshes: meshCount,
      named: Object.keys(outMeshes).length,
      source360: source360Tf.position,
      source104: source104Tf.position,
      source105: source105Tf.position,
    },
    null,
    2,
  ),
);
