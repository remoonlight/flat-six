/**
 * Bake 501-000 + 501-000-mirror → 单层 GLB。
 *
 * - Layer TRS from .local/xray-transforms.json
 * - Sub-mesh TRS from .local/xray-mesh-state.json（镜像共用 pm-501-000）
 * - 镜像子件名 → *_R
 * - Output: .local/petka-models/merged/501-000.glb（装配 id 仍为 pm-501-000）
 *
 * Usage: node scripts/merge-501-000.mjs
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

const OUT_ID = "pm-501-000";
const OUT_REL = "petka-models/merged/501-000.glb";
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
    id: "pm-501-000",
    glb: "petka-models/501-000.glb",
    meshStateId: "pm-501-000",
    mirrorX: false,
    renameSuffix: null,
  },
  {
    id: "pm-501-000-mirror",
    glb: "petka-models/501-000.glb",
    meshStateId: "pm-501-000",
    mirrorX: true,
    renameSuffix: "_R",
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

function meshMapHasPose(meshes) {
  return Object.values(meshes || {}).some((e) => !isIdentityTf(e?.transform));
}

function layerHasPose(t) {
  return !isIdentityTf(t);
}

// Snapshot source layer TRS / mesh-state (OUT_ID === left id — never clobber real prebake)
const transforms = loadJson(TF_PATH, { layers: {} });
const meshState = loadJson(MS_PATH, { layers: {} });

// Always backup before mutate
{
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const bakMs = path.join(root, ".local", `xray-mesh-state.pre-501-merge.${stamp}.json`);
  const bakTf = path.join(root, ".local", `xray-transforms.pre-501-merge.${stamp}.json`);
  if (fs.existsSync(MS_PATH)) fs.copyFileSync(MS_PATH, bakMs);
  if (fs.existsSync(TF_PATH)) fs.copyFileSync(TF_PATH, bakTf);
  console.log("backup", path.basename(bakMs), path.basename(bakTf));
}

const prebakeTf = transforms.layers?.["pm-501-000__prebake"];
const liveTf = transforms.layers?.["pm-501-000"];
const leftLayerTf =
  prebakeTf && layerHasPose(prebakeTf)
    ? structuredClone(prebakeTf)
    : liveTf && layerHasPose(liveTf)
      ? structuredClone(liveTf)
      : prebakeTf
        ? structuredClone(prebakeTf)
        : liveTf
          ? structuredClone(liveTf)
          : { ...IDENTITY };

const mirrorLayerTf = transforms.layers?.["pm-501-000-mirror"]
  ? structuredClone(transforms.layers["pm-501-000-mirror"])
  : { ...IDENTITY };

const prebakeMeshes = meshState.layers?.["pm-501-000__prebake"]?.meshes || {};
const liveMeshesRaw = meshState.layers?.["pm-501-000"]?.meshes || {};
const liveMeshes = Object.fromEntries(
  Object.entries(liveMeshesRaw).filter(([n]) => !n.endsWith("_R")),
);

let srcMeshes;
if (meshMapHasPose(prebakeMeshes)) {
  srcMeshes = structuredClone(prebakeMeshes);
} else if (meshMapHasPose(liveMeshes)) {
  srcMeshes = structuredClone(liveMeshes);
} else if (Object.keys(prebakeMeshes).length) {
  srcMeshes = structuredClone(prebakeMeshes);
} else {
  srcMeshes = structuredClone(liveMeshes);
}

if (!meshMapHasPose(srcMeshes) && Object.keys(srcMeshes).length) {
  console.warn(
    "warn: source mesh-state looks identity — sub-part poses may already be lost; abort with --force if unsure",
  );
  if (!process.argv.includes("--force")) {
    console.error("refusing to re-bake without mesh poses (pass --force to override)");
    process.exit(2);
  }
}

const rootGroup = new THREE.Group();
rootGroup.name = "501-000";

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

  const layerMeshes = srcMeshes;
  const usedNames = new Set(Object.keys(outMeshes));
  scene.traverse((o) => {
    if (!o.isMesh) return;
    const srcName = o.name || `unnamed:${o.uuid.slice(0, 8)}`;
    const entry = layerMeshes[srcName];
    if (entry?.transform) applyManualTransform(o, entry.transform);
    let outName = renameWithSuffix(srcName, part.renameSuffix);
    if (usedNames.has(outName)) {
      outName = `${outName}_dup`;
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

  const layerTf = part.mirrorX ? mirrorLayerTf : leftLayerTf;
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
// Never replace a posed prebake with identity
if (
  !ms.layers["pm-501-000__prebake"] ||
  meshMapHasPose(srcMeshes) ||
  !meshMapHasPose(ms.layers["pm-501-000__prebake"]?.meshes)
) {
  ms.layers["pm-501-000__prebake"] = {
    visible: true,
    meshes: srcMeshes,
  };
} else {
  console.warn("keep existing posed pm-501-000__prebake meshes");
}
ms.layers[OUT_ID] = {
  visible: true,
  meshes: outMeshes,
};
ms.version = 1;
fs.writeFileSync(MS_PATH, JSON.stringify(ms, null, 2) + "\n", "utf8");

const tf = loadJson(TF_PATH, {
  version: 1,
  note: "hand overrides; do not commit",
  layers: {},
});
tf.layers = tf.layers || {};
tf.layers["pm-501-000__prebake"] = leftLayerTf;
tf.layers["pm-501-000-mirror"] = mirrorLayerTf;
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
      sampleR: Object.keys(outMeshes).filter((n) => n.endsWith("_R")),
    },
    null,
    2,
  ),
);
