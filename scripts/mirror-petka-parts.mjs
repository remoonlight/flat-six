/**
 * Clone named meshes inside a petka GLB as *_R (X-mirror).
 * Seeds independent mesh-state from current layer transforms.
 *
 * Usage:
 *   node scripts/mirror-petka-parts.mjs --id pm-402-005 --parts tripo_part_1
 *   node scripts/mirror-petka-parts.mjs --id pm-401-000 --parts tripo_part_1,tripo_part_2
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

function arg(name, fallback = "") {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? String(process.argv[i + 1] || "").trim() : fallback;
}

const ASM_ID = arg("id");
const PARTS_RAW = arg("parts");
if (!ASM_ID || !PARTS_RAW) {
  console.error(
    "usage: node scripts/mirror-petka-parts.mjs --id pm-402-005 --parts tripo_part_1",
  );
  process.exit(1);
}
const WANT = PARTS_RAW.split(",").map((s) => s.trim()).filter(Boolean);
const bild = ASM_ID.replace(/^pm-/, "");
const GLB_REL = `petka-models/${bild}.glb`;
const GLB_ABS = path.join(root, ".local", GLB_REL.replace(/\//g, path.sep));
const MS_PATH = path.join(root, ".local", "xray-mesh-state.json");

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

function mirrorTransform(t) {
  const [px, py, pz] = t?.position || [0, 0, 0];
  const [rx, ry, rz] = t?.rotationEuler || [0, 0, 0];
  const [sx, sy, sz] = t?.scale || [1, 1, 1];
  return {
    position: [-px, py, pz],
    rotationEuler: [rx, -ry, -rz],
    scale: [-Math.abs(sx || 1), sy || 1, sz || 1],
  };
}

if (!fs.existsSync(GLB_ABS)) {
  console.error("missing", GLB_ABS);
  process.exit(1);
}

const ms = loadJson(MS_PATH, {
  version: 1,
  note: "per-mesh TRS/visible; layer × mesh; do not commit",
  layers: {},
});
ms.layers = ms.layers || {};
if (!ms.layers[ASM_ID]) ms.layers[ASM_ID] = { visible: true, meshes: {} };
const layerMeshes = ms.layers[ASM_ID].meshes;

const scene = await loadGlb(GLB_ABS);

/** @type {Map<string, import('three').Mesh>} */
const found = new Map();
scene.traverse((o) => {
  if (!o.isMesh) return;
  if (WANT.includes(o.name)) found.set(o.name, o);
});

for (const name of WANT) {
  if (!found.has(name)) {
    console.error("mesh not found:", name);
    process.exit(1);
  }
}

const added = [];
for (const name of WANT) {
  const src = found.get(name);
  const rName = `${name}_R`;
  const stale = [];
  scene.traverse((o) => {
    if (o.isMesh && o.name === rName) stale.push(o);
  });
  for (const o of stale) {
    o.parent?.remove(o);
    o.geometry?.dispose?.();
  }

  const clone = src.clone(true);
  clone.name = rName;
  src.parent.add(clone);
  added.push(rName);

  const srcTf = layerMeshes[name]?.transform || {
    position: [0, 0, 0],
    rotationEuler: [0, 0, 0],
    scale: [1, 1, 1],
  };
  layerMeshes[rName] = {
    visible: layerMeshes[name]?.visible !== false,
    transform: mirrorTransform(srcTf),
  };
  console.log("clone", name, "→", rName, layerMeshes[rName].transform.position);
}

stripMaps(scene);

const exporter = new GLTFExporter();
const glb = await new Promise((resolve, reject) => {
  exporter.parse(
    scene,
    (result) => resolve(Buffer.from(result)),
    (err) => reject(err),
    { binary: true, onlyVisible: false },
  );
});
fs.writeFileSync(GLB_ABS, glb);

ms.version = 1;
fs.writeFileSync(MS_PATH, JSON.stringify(ms, null, 2) + "\n", "utf8");

console.log(
  JSON.stringify(
    {
      ok: true,
      id: ASM_ID,
      glb: GLB_REL,
      bytes: glb.length,
      added,
      meshState: Object.fromEntries(
        added.map((n) => [n, layerMeshes[n].transform.position]),
      ),
    },
    null,
    2,
  ),
);
