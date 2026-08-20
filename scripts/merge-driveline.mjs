/**
 * Bake 302-000 + 501-005(+镜像) → 传动系统 GLB.
 *
 * - Layer TRS from .local/xray-transforms.json
 * - Sub-mesh TRS from .local/xray-mesh-state.json
 * - 501-005 冲突名 _5015；镜像 *_R
 * - Output: .local/petka-models/merged/driveline.glb（装配 id 仍为 pm-302-000）
 *
 * Usage: node scripts/merge-driveline.mjs
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

const OUT_ID = "pm-302-000";
const PREBAKE_ID = "pm-302-000__prebake";
const OUT_REL = "petka-models/merged/driveline.glb";
const OUT_ABS = path.join(root, ".local", OUT_REL.replace(/\//g, path.sep));
const TF_PATH = path.join(root, ".local", "xray-transforms.json");
const MS_PATH = path.join(root, ".local", "xray-mesh-state.json");

const IDENTITY = {
  position: [0, 0, 0],
  rotationEuler: [0, 0, 0],
  scale: [1, 1, 1],
};

/** @type {Array<{ id: string, glb: string, meshStateId: string, mirrorX: boolean, renameSuffix: string | null, collideTag: string | null }>} */
const PARTS = [
  {
    id: "pm-302-000",
    glb: "petka-models/302-000.glb",
    meshStateId: "pm-302-000",
    mirrorX: false,
    renameSuffix: null,
    collideTag: null,
  },
  {
    id: "pm-501-005",
    glb: "petka-models/501-005.glb",
    meshStateId: "pm-501-005",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_5015",
  },
  {
    id: "pm-501-005-mirror",
    glb: "petka-models/501-005.glb",
    meshStateId: "pm-501-005",
    mirrorX: true,
    renameSuffix: "_R",
    collideTag: "_5015",
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

function uniqueName(srcName, renameSuffix, collideTag, used) {
  let out = srcName;
  if (renameSuffix && !out.endsWith(renameSuffix)) out = `${out}${renameSuffix}`;
  if (!used.has(out)) return out;
  const tag = collideTag || "_x";
  const base = srcName;
  out = renameSuffix ? `${base}${tag}${renameSuffix}` : `${base}${tag}`;
  let i = 2;
  while (used.has(out)) {
    out = renameSuffix
      ? `${base}${tag}${i}${renameSuffix}`
      : `${base}${tag}${i}`;
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
      path.join(root, ".local", `xray-mesh-state.pre-driveline-merge.${stamp}.json`),
    );
  }
  if (fs.existsSync(TF_PATH)) {
    fs.copyFileSync(
      TF_PATH,
      path.join(root, ".local", `xray-transforms.pre-driveline-merge.${stamp}.json`),
    );
  }
}

const transforms = loadJson(TF_PATH, { layers: {} });
const meshState = loadJson(MS_PATH, { layers: {} });

const live302 = transforms.layers?.[OUT_ID];
const pre302 = transforms.layers?.[PREBAKE_ID];
const source302Tf =
  pre302 && layerHasPose(pre302)
    ? structuredClone(pre302)
    : live302 && layerHasPose(live302)
      ? structuredClone(live302)
      : pre302
        ? structuredClone(pre302)
        : live302
          ? structuredClone(live302)
          : { ...IDENTITY };

const preMeshes = meshState.layers?.[PREBAKE_ID]?.meshes || {};
const liveMeshes = meshState.layers?.[OUT_ID]?.meshes || {};
const source302Meshes =
  Object.keys(preMeshes).length &&
  !Object.keys(preMeshes).some((n) => n.includes("_5015"))
    ? structuredClone(preMeshes)
    : Object.keys(liveMeshes).length &&
        !Object.keys(liveMeshes).some((n) => n.includes("_5015"))
      ? structuredClone(liveMeshes)
      : structuredClone(preMeshes);

function layerTfFor(id) {
  if (id === OUT_ID) return source302Tf;
  return transforms.layers?.[id] || IDENTITY;
}

function meshesFor(meshStateId) {
  if (meshStateId === OUT_ID) return source302Meshes;
  return meshState.layers?.[meshStateId]?.meshes || {};
}

const rootGroup = new THREE.Group();
rootGroup.name = "传动系统";

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
    const outName = uniqueName(
      srcName,
      part.renameSuffix,
      part.collideTag,
      usedNames,
    );
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

  const layerTf = layerTfFor(part.id);
  if (part.mirrorX) {
    wrap.scale.x = -Math.abs(wrap.scale.x || 1);
  }
  applyManualTransform(wrap, layerTf);

  rootGroup.add(wrap);
  console.log("baked", part.id, {
    mirrorX: part.mirrorX,
    rename: part.renameSuffix,
    layer: layerTf.position,
  });
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
if (Object.keys(source302Meshes).length) {
  ms.layers[PREBAKE_ID] = { visible: true, meshes: source302Meshes };
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
tf.layers[PREBAKE_ID] = source302Tf;
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
      sample5015: Object.keys(outMeshes)
        .filter((n) => n.includes("_5015") || n.endsWith("_R"))
        .slice(0, 12),
    },
    null,
    2,
  ),
);
