/**
 * model-oem-links 落盘自检。
 * 跑：node apps/desktop/electron/model-oem-links.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import os from "node:os";
import { ensureLocalFromSeed } from "./ensure-local-snapshot.mjs";
const isolatedRoot = fs.mkdtempSync(path.join(os.tmpdir(), "p981-oem-check-"));
process.env.PORSCHE981_LOCAL_ROOT = isolatedRoot;
const {
  listGarageModelCatalog,
  listGlbNodeNames,
  loadModelOemLinks,
  removeModelOemLink,
  upsertModelOemLink,
  MODEL_OEM_LINKS_PATH,
} = await import("./model-oem-links.mjs");

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
assert(JSON.stringify(before.links) === JSON.stringify(seedLinks.links), "fresh install gets every bundled link");

// Reproduce a pre-existing installation: the old first-run copy skipped this file.
const custom = { ...seedLinks.links[0], skus: ["spark-plugs"] };
const extra = { id: "mesh:body:custom", kind: "mesh", ref: "custom", assemblyId: "body", skus: ["oil-filter"] };
fs.writeFileSync(MODEL_OEM_LINKS_PATH, JSON.stringify({ version: 1, links: [custom, extra] }));
const upgraded = loadModelOemLinks();
assert(upgraded.links.length === seedLinks.links.length + 1, "existing installation gains missing bundled links");
assert(upgraded.links.find(l => l.id === custom.id).skus[0] === "spark-plugs", "local override wins");
assert(upgraded.links.some(l => l.id === extra.id), "custom link survives update");
const once = fs.readFileSync(MODEL_OEM_LINKS_PATH, "utf8");
loadModelOemLinks();
assert(fs.readFileSync(MODEL_OEM_LINKS_PATH, "utf8") === once, "unchanged load does not rewrite state");

for (const remove of ["whole", "sku", "empty-upsert"]) {
  const target = seedLinks.links.find(l => l.kind === "mesh");
  if (remove === "whole") removeModelOemLink(target.kind, target.ref, undefined, target.assemblyId);
  if (remove === "sku") removeModelOemLink(target.kind, target.ref, target.skus[0], target.assemblyId);
  if (remove === "empty-upsert") upsertModelOemLink({ ...target, skus: [] });
  assert(!loadModelOemLinks().links.some(l => l.id === target.id), `${remove}: explicit unlink stays removed after reload`);
  upsertModelOemLink(target);
  assert(loadModelOemLinks().links.some(l => l.id === target.id), `${remove}: explicit relink works`);
}

fs.writeFileSync(MODEL_OEM_LINKS_PATH, JSON.stringify({ version: 1, links: [] }));
assert(loadModelOemLinks().links.length === seedLinks.links.length, "old empty local file receives seed");
fs.writeFileSync(MODEL_OEM_LINKS_PATH, "broken-json");
let invalid = false;
try { loadModelOemLinks(); } catch { invalid = true; }
assert(invalid && fs.readFileSync(MODEL_OEM_LINKS_PATH, "utf8") === "broken-json", "invalid local file is preserved and reported");
fs.writeFileSync(MODEL_OEM_LINKS_PATH, JSON.stringify(seedLinks));

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
    bodyRel: bodyGlb,
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
assert(path.dirname(isolatedRoot) === path.resolve(os.tmpdir()) && path.basename(isolatedRoot).startsWith("p981-oem-check-"), "cleanup stays in own temporary directory");
fs.rmSync(isolatedRoot, { recursive: true, force: true });
