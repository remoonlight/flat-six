/**
 * Bake 105-015 + 105-015-mirror + 105-017 → 水冷系统 GLB.
 *
 * - Layer TRS from .local/xray-transforms.json
 * - Sub-mesh TRS/visible from .local/xray-mesh-state.json
 *   (mirror used meshStateOf → pm-105-015; meshes renamed tripo_part_N → tripo_part_N_R)
 * - Output: .local/petka-models/merged/cooling.glb
 * - Seeds initial mesh-state for pm-cooling (visibility only; TRS already baked)
 *
 * Usage: node scripts/merge-cooling-system.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

// Three GLTFLoader/Exporter expect browser globals
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

const OUT_ID = "pm-cooling";
const OUT_REL = "petka-models/merged/cooling.glb";
const OUT_ABS = path.join(root, ".local", OUT_REL.replace(/\//g, path.sep));
const TF_PATH = path.join(root, ".local", "xray-transforms.json");
const MS_PATH = path.join(root, ".local", "xray-mesh-state.json");

const IDENTITY = {
  position: [0, 0, 0],
  rotationEuler: [0, 0, 0],
  scale: [1, 1, 1],
};

/** @type {Array<{ id: string, glb: string, meshStateId: string, mirrorX: boolean, renameSuffix: string | null }>} */
const PARTS = [
  {
    id: "pm-105-015",
    glb: "petka-models/105-015.glb",
    meshStateId: "pm-105-015",
    mirrorX: false,
    renameSuffix: null,
  },
  {
    id: "pm-105-015-mirror",
    glb: "petka-models/105-015.glb",
    meshStateId: "pm-105-015",
    mirrorX: true,
    renameSuffix: "_R",
  },
  {
    id: "pm-105-017",
    glb: "petka-models/105-017.glb",
    meshStateId: "pm-105-017",
    mirrorX: false,
    renameSuffix: null,
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

function applyManualTransform(root, t) {
  if (!t) return;
  const [px, py, pz] = t.position || [0, 0, 0];
  const [rx, ry, rz] = t.rotationEuler || [0, 0, 0];
  const [sx, sy, sz] = t.scale || [1, 1, 1];
  root.position.x += px || 0;
  root.position.y += py || 0;
  root.position.z += pz || 0;
  root.rotation.x += rx || 0;
  root.rotation.y += ry || 0;
  root.rotation.z += rz || 0;
  root.scale.x *= sx ?? 1;
  root.scale.y *= sy ?? 1;
  root.scale.z *= sz ?? 1;
}

function stripMaps(root) {
  root.traverse((o) => {
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

function renameWithSuffix(name, suffix) {
  if (!suffix) return name;
  if (name.endsWith(suffix)) return name;
  return `${name}${suffix}`;
}

const transforms = loadJson(TF_PATH, { layers: {} });
const meshState = loadJson(MS_PATH, { layers: {} });

const rootGroup = new THREE.Group();
rootGroup.name = "水冷系统";

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

  const layerMeshes = meshState.layers?.[part.meshStateId]?.meshes || {};
  const usedNames = new Set(Object.keys(outMeshes));
  scene.traverse((o) => {
    if (!o.isMesh) return;
    const srcName = o.name || `unnamed:${o.uuid.slice(0, 8)}`;
    const entry = layerMeshes[srcName];
    if (entry?.transform) applyManualTransform(o, entry.transform);
    let outName = renameWithSuffix(srcName, part.renameSuffix);
    // 017 与 015 重名时加后缀，否则 mesh-state 键冲突
    if (!part.renameSuffix && usedNames.has(outName)) {
      outName = `${outName}_017`;
    }
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

  const layerTf = transforms.layers?.[part.id] || IDENTITY;
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

// Seed mesh-state for new assembly (visibility preserved; TRS baked → identity)
const ms = loadJson(MS_PATH, {
  version: 1,
  note: "per-mesh TRS/visible; layer × mesh; do not commit",
  layers: {},
});
ms.layers = ms.layers || {};
ms.layers[OUT_ID] = {
  visible: true,
  meshes: outMeshes,
};
ms.version = 1;
fs.writeFileSync(MS_PATH, JSON.stringify(ms, null, 2) + "\n", "utf8");

// Clear layer TRS for new id (baked); keep old keys for history
const tf = loadJson(TF_PATH, {
  version: 1,
  note: "hand overrides; do not commit",
  layers: {},
});
tf.layers = tf.layers || {};
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
      sample: Object.keys(outMeshes).filter((n) => n.includes("_R")).slice(0, 5),
    },
    null,
    2,
  ),
);
