/**
 * Bake 401… + 402-000(+镜像) + 403-000/006 + 501… + 502(+镜像) + 99134104301(+镜像) + 801-020 → 底盘悬架 GLB.
 *
 * - Layer TRS from .local/xray-transforms.json
 * - Sub-mesh TRS from .local/xray-mesh-state.json
 * - 冲突名：_405 / _4205 / _402 / _403 / _4036 / _5010 / _5011 / _5013 / _502 / _9431
 * - Output: .local/petka-models/merged/suspension.glb
 *
 * Usage: node scripts/merge-suspension.mjs
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

const OUT_ID = "pm-suspension";
const OUT_REL = "petka-models/merged/suspension.glb";
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
    id: "pm-401-000",
    glb: "petka-models/401-000.glb",
    meshStateId: "pm-401-000",
    mirrorX: false,
    renameSuffix: null,
    collideTag: null,
  },
  {
    id: "pm-401-005",
    glb: "petka-models/401-005.glb",
    meshStateId: "pm-401-005",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_405",
  },
  {
    id: "pm-401-005-mirror",
    glb: "petka-models/401-005.glb",
    meshStateId: "pm-401-005",
    mirrorX: true,
    renameSuffix: "_R",
    collideTag: "_405",
  },
  {
    id: "pm-402-005",
    glb: "petka-models/402-005.glb",
    meshStateId: "pm-402-005",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_4205",
  },
  {
    id: "pm-402-000",
    glb: "petka-models/402-000.glb",
    meshStateId: "pm-402-000",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_402",
  },
  {
    id: "pm-402-000-mirror",
    glb: "petka-models/402-000.glb",
    meshStateId: "pm-402-000",
    mirrorX: true,
    renameSuffix: "_R",
    collideTag: "_402",
  },
  {
    id: "pm-403-000",
    glb: "petka-models/403-000.glb",
    meshStateId: "pm-403-000",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_403",
  },
  {
    id: "pm-403-006",
    glb: "petka-models/403-006.glb",
    meshStateId: "pm-403-006",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_4036",
  },
  {
    id: "pm-501-000",
    glb: "petka-models/merged/501-000.glb",
    meshStateId: "pm-501-000",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_5010",
  },
  {
    id: "pm-501-001",
    glb: "petka-models/501-001.glb",
    meshStateId: "pm-501-001",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_5011",
  },
  {
    id: "pm-501-003",
    glb: "petka-models/501-003.glb",
    meshStateId: "pm-501-003",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_5013",
  },
  {
    id: "pm-502-000",
    glb: "petka-models/502-000.glb",
    meshStateId: "pm-502-000",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_502",
  },
  {
    id: "pm-502-000-mirror",
    glb: "petka-models/502-000.glb",
    meshStateId: "pm-502-000",
    mirrorX: true,
    renameSuffix: "_R",
    collideTag: "_502",
  },
  {
    id: "pm-99134104301",
    glb: "petka-models/99134104301.glb",
    meshStateId: "pm-99134104301",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_9431",
  },
  {
    id: "pm-99134104301-mirror",
    glb: "petka-models/99134104301.glb",
    meshStateId: "pm-99134104301",
    mirrorX: true,
    renameSuffix: "_R",
    collideTag: "_9431",
  },
  {
    id: "pm-801-020",
    glb: "petka-models/801-020.glb",
    meshStateId: "pm-801-020",
    mirrorX: false,
    renameSuffix: null,
    collideTag: "_8010",
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

function applyManualTransform(obj, t) {
  if (!t) return;
  const [px, py, pz] = t.position || [0, 0, 0];
  const [rx, ry, rz] = t.rotationEuler || [0, 0, 0];
  const [sx, sy, sz] = t.scale || [1, 1, 1];
  obj.position.x += px || 0;
  obj.position.y += py || 0;
  obj.position.z += pz || 0;
  obj.rotation.x += rx || 0;
  obj.rotation.y += ry || 0;
  obj.rotation.z += rz || 0;
  obj.scale.x *= sx ?? 1;
  obj.scale.y *= sy ?? 1;
  obj.scale.z *= sz ?? 1;
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

const transforms = loadJson(TF_PATH, { layers: {} });
const meshState = loadJson(MS_PATH, { layers: {} });

const rootGroup = new THREE.Group();
rootGroup.name = "底盘悬架";

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
    // mesh-state key is pre-rename source name (mirror shares 401-005)
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

const ms = loadJson(MS_PATH, {
  version: 1,
  note: "per-mesh TRS/visible; layer × mesh; do not commit",
  layers: {},
});
ms.layers = ms.layers || {};
ms.layers[OUT_ID] = { visible: true, meshes: outMeshes };
ms.version = 1;
fs.writeFileSync(MS_PATH, JSON.stringify(ms, null, 2) + "\n", "utf8");

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
      names: Object.keys(outMeshes).sort(),
    },
    null,
    2,
  ),
);
