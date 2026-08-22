/**
 * Bake 202-000 + 202-005 + 201-000 → 燃油系统 GLB.
 *
 * - 若已有 merged/fuel.glb：以其为底（保留层 TRS），再叠 201-000
 * - 否则从 202-* 源 GLB 重编
 * - 201-000 冲突名 _201
 * - Output: .local/petka-models/merged/fuel.glb（pm-fuel）
 *
 * Usage: node scripts/merge-fuel-system.mjs
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
        .catch((err) => {
          this.onerror?.(err);
        });
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

const OUT_ID = "pm-fuel";
const PREBAKE_ID = "pm-fuel__prebake";
const SRC_201 = "pm-201-000";
const OUT_REL = "petka-models/merged/fuel.glb";
const OUT_ABS = path.join(root, ".local", OUT_REL.replace(/\//g, path.sep));
const TF_PATH = path.join(root, ".local", "xray-transforms.json");
const MS_PATH = path.join(root, ".local", "xray-mesh-state.json");

const IDENTITY = {
  position: [0, 0, 0],
  rotationEuler: [0, 0, 0],
  scale: [1, 1, 1],
};

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
        side: m?.side ?? THREE.DoubleSide,
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
  if (!used.has(srcName)) return srcName;
  const tag = collideTag || "_x";
  let out = `${srcName}${tag}`;
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

function has201Suffix(name) {
  return String(name).includes("_201");
}

{
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  if (fs.existsSync(MS_PATH)) {
    fs.copyFileSync(
      MS_PATH,
      path.join(root, ".local", `xray-mesh-state.pre-fuel-merge.${stamp}.json`),
    );
  }
  if (fs.existsSync(TF_PATH)) {
    fs.copyFileSync(
      TF_PATH,
      path.join(root, ".local", `xray-transforms.pre-fuel-merge.${stamp}.json`),
    );
  }
}

const transforms = loadJson(TF_PATH, { layers: {} });
const meshState = loadJson(MS_PATH, { layers: {} });

const liveFuel = transforms.layers?.[OUT_ID];
const preFuel = transforms.layers?.[PREBAKE_ID];
const sourceFuelTf =
  preFuel && layerHasPose(preFuel)
    ? structuredClone(preFuel)
    : liveFuel && layerHasPose(liveFuel)
      ? structuredClone(liveFuel)
      : preFuel
        ? structuredClone(preFuel)
        : liveFuel
          ? structuredClone(liveFuel)
          : { ...IDENTITY };

const source201Tf = transforms.layers?.[SRC_201]
  ? structuredClone(transforms.layers[SRC_201])
  : { ...IDENTITY };

const preMeshes = meshState.layers?.[PREBAKE_ID]?.meshes || {};
const liveMeshes = meshState.layers?.[OUT_ID]?.meshes || {};
const sourceFuelMeshes =
  Object.keys(preMeshes).length &&
  !Object.keys(preMeshes).some(has201Suffix)
    ? structuredClone(preMeshes)
    : Object.keys(liveMeshes).length &&
        !Object.keys(liveMeshes).some(has201Suffix)
      ? structuredClone(liveMeshes)
      : structuredClone(preMeshes);

const source201Meshes = meshState.layers?.[SRC_201]?.meshes || {};

const useMergedBase =
  fs.existsSync(OUT_ABS) &&
  (Object.keys(sourceFuelMeshes).length > 0 || layerHasPose(sourceFuelTf));

/** @type {Array<{ id: string, glb: string, meshStateId: string, collideTag: string | null }>} */
const PARTS = useMergedBase
  ? [
      {
        id: OUT_ID,
        glb: OUT_REL,
        meshStateId: OUT_ID,
        collideTag: null,
      },
      {
        id: SRC_201,
        glb: "petka-models/201-000.glb",
        meshStateId: SRC_201,
        collideTag: "_201",
      },
    ]
  : [
      {
        id: "pm-202-000",
        glb: "petka-models/202-000.glb",
        meshStateId: "pm-202-000",
        collideTag: null,
      },
      {
        id: "pm-202-005",
        glb: "petka-models/202-005.glb",
        meshStateId: "pm-202-005",
        collideTag: "_205",
      },
      {
        id: SRC_201,
        glb: "petka-models/201-000.glb",
        meshStateId: SRC_201,
        collideTag: "_201",
      },
    ];

function layerTfFor(id) {
  if (id === OUT_ID) return sourceFuelTf;
  if (id === SRC_201) return source201Tf;
  return transforms.layers?.[id] || IDENTITY;
}

function meshesFor(meshStateId) {
  if (meshStateId === OUT_ID) return sourceFuelMeshes;
  if (meshStateId === SRC_201) return source201Meshes;
  return meshState.layers?.[meshStateId]?.meshes || {};
}

const rootGroup = new THREE.Group();
rootGroup.name = "燃油系统";

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
    // 重跑时 merged 底已含 _201，跳过以免加倍
    if (part.id === OUT_ID && has201Suffix(srcName)) return;
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
  !Object.keys(ms.layers[PREBAKE_ID]?.meshes || {}).length ||
  Object.keys(ms.layers[PREBAKE_ID]?.meshes || {}).some(has201Suffix)
) {
  ms.layers[PREBAKE_ID] = {
    visible: true,
    meshes: structuredClone(sourceFuelMeshes),
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
  tf.layers[PREBAKE_ID] = structuredClone(sourceFuelTf);
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
      useMergedBase,
      sourceFuel: sourceFuelTf.position,
      source201: source201Tf.position,
    },
    null,
    2,
  ),
);
