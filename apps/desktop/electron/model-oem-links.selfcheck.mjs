/**
 * model-oem-links 落盘自检。
 * 跑：node apps/desktop/electron/model-oem-links.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";
import { ensureLocalFromSeed } from "./ensure-local-snapshot.mjs";
import {
  listGarageModelCatalog,
  listGlbNodeNames,
  loadModelOemLinks,
  removeModelOemLink,
  upsertModelOemLink,
  MODEL_OEM_LINKS_PATH,
} from "./model-oem-links.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../../..");
const tmpMarker = `__selfcheck_${Date.now()}`;

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "p981-snap-"));
  const dest = path.join(dir, "xray-transforms.json");
  const seed = path.join(
    root,
    "data",
    "seed",
    "xray",
    "transforms.template.json",
  );
  try {
    assert(ensureLocalFromSeed(dest, seed) === true, "bootstrap copies seed");
    const n = Object.keys(
      JSON.parse(fs.readFileSync(dest, "utf8")).layers || {},
    ).length;
    assert(n > 0, "bootstrapped transforms have layers");
    assert(ensureLocalFromSeed(dest, seed) === false, "existing dest not overwritten");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const seedLinks = JSON.parse(
  fs.readFileSync(
    path.join(root, "data", "seed", "xray", "model-oem-links.seed.json"),
    "utf8",
  ),
);
assert(
  Array.isArray(seedLinks.links) && seedLinks.links.length > 0,
  "oem seed has links",
);

const before = loadModelOemLinks();
assert(Array.isArray(before.links), "links array");
assert(
  before.links.length > 0,
  "local oem links after load (missing .local is seeded from git snapshot)",
);

const after = upsertModelOemLink({
  kind: "assembly",
  ref: tmpMarker,
  skus: ["oil-filter", "spark-plugs"],
});
assert(
  after.links.some((l) => l.id === `assembly:${tmpMarker}`),
  "upsert assembly",
);

const trimmed = removeModelOemLink("assembly", tmpMarker, "oil-filter");
const row = trimmed.links.find((l) => l.id === `assembly:${tmpMarker}`);
assert(row?.skus?.length === 1 && row.skus[0] === "spark-plugs", "remove one sku");

removeModelOemLink("assembly", tmpMarker);
assert(
  !loadModelOemLinks().links.some((l) => l.id === `assembly:${tmpMarker}`),
  "remove link",
);

const meshA = upsertModelOemLink({
  kind: "mesh",
  ref: tmpMarker,
  assemblyId: "pm-cooling",
  skus: ["oil-filter"],
});
const meshB = upsertModelOemLink({
  kind: "mesh",
  ref: tmpMarker,
  assemblyId: "pm-engine",
  skus: ["spark-plugs"],
});
assert(
  meshA.links.some((l) => l.id === `mesh:pm-cooling:${tmpMarker}`) &&
    meshB.links.find((l) => l.id === `mesh:pm-cooling:${tmpMarker}`)?.skus[0] ===
      "oil-filter",
  "mesh cooling independent",
);
assert(
  meshB.links.find((l) => l.id === `mesh:pm-engine:${tmpMarker}`)?.skus[0] ===
    "spark-plugs",
  "mesh engine independent",
);
removeModelOemLink("mesh", tmpMarker, undefined, "pm-cooling");
removeModelOemLink("mesh", tmpMarker, undefined, "pm-engine");
assert(
  !loadModelOemLinks().links.some((l) => l.ref === tmpMarker),
  "remove mesh links",
);

const bodyGlb = path.join(root, ".local", "flat-six", "boxster-real.glb");
if (fs.existsSync(bodyGlb)) {
  const names = listGlbNodeNames(bodyGlb);
  assert(names.length > 0, "glb node names");
  console.log("glb nodes sample:", names.slice(0, 5).join(", "));
  const cat = listGarageModelCatalog({
    bodyRel: "flat-six/boxster-real.glb",
    layers: [],
    flows: [{ id: "flow-selfcheck", label: "selfcheck flow" }],
  });
  assert(cat.parents.some((p) => p.ref === "body"), "parent body");
  assert((cat.childrenByParent.body?.length ?? 0) > 0, "body children");
  assert(
    cat.childrenByParent["flow-selfcheck"]?.[0]?.kind === "flow",
    "flow leaf",
  );
} else {
  console.log("skip glb parse (no boxster-real.glb)");
}

console.log("ok model-oem-links →", MODEL_OEM_LINKS_PATH);
