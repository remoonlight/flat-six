/**
 * P-Loc-2 navigation-loop smoke (no full 3D).
 * Asserts zones, fluids bridgeJumps, bootstrap hotspot anchors, engine-bay eng-* tree.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

function fail(msg) {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
}

const zonesPath = path.join(root, "data", "seed", "locator", "zones.json");
const draftPath = path.join(
  root,
  "data",
  "seed",
  "locator",
  "systems-draft.plan.json",
);
const bootstrapPath = path.join(root, "data", "seed", "parts", "bootstrap.json");
const wiringPath = path.join(root, "data", "seed", "wiring", "index.json");

for (const p of [zonesPath, draftPath, bootstrapPath, wiringPath]) {
  if (!fs.existsSync(p)) fail(`missing ${p}`);
}

const zonesDoc = JSON.parse(fs.readFileSync(zonesPath, "utf8"));
const draft = JSON.parse(fs.readFileSync(draftPath, "utf8"));
const bootstrap = JSON.parse(fs.readFileSync(bootstrapPath, "utf8"));
const wiring = JSON.parse(fs.readFileSync(wiringPath, "utf8"));

const zoneIds = new Set((zonesDoc.zones || []).map((z) => z.id));
const requiredZones = [
  "engine-bay",
  "brakes",
  "chassis",
  "interior",
  "front-trunk",
  "electronics",
  "fluids",
];
for (const id of requiredZones) {
  if (!zoneIds.has(id)) fail(`zones.json missing zone ${id}`);
}
console.log(`zones: ${[...zoneIds].join(", ")}`);

const wiringBridges = (wiring.systems || []).filter(
  (s) => typeof s.locatorZoneId === "string" && s.locatorZoneId,
);
if (wiringBridges.length < 1) fail("wiring index needs >=1 locatorZoneId bridge");
console.log(
  `wiring bridges: ${wiringBridges.length} — ${wiringBridges.map((s) => `${s.id}→${s.locatorZoneId}`).join(", ")}`,
);

const parts = bootstrap.parts || [];
const expectHotspot = {
  coolant: "engine-bay",
  "engine-oil": "engine-bay",
  "fuel-filter": "underbody",
};
for (const [sku, hotspot] of Object.entries(expectHotspot)) {
  const p = parts.find((x) => x.sku === sku);
  if (!p) fail(`bootstrap missing sku ${sku}`);
  if (p.locator_hotspot !== hotspot) {
    fail(
      `bootstrap ${sku} locator_hotspot=${p.locator_hotspot} want ${hotspot}`,
    );
  }
  console.log(`bootstrap ${sku} → ${p.locator_hotspot}`);
}

const engineBay = (zonesDoc.zones || []).find((z) => z.id === "engine-bay");
if (!engineBay) fail("engine-bay zone missing");
const engIds = new Set((engineBay.hotspots || []).map((h) => h.id));
for (const id of ["engine-bay", "eng-intake", "eng-block", "eng-exhaust"]) {
  if (!engIds.has(id)) fail(`engine-bay missing hotspot ${id}`);
}
console.log(
  `engine-bay hotspots: ${[...engIds].join(", ")}`,
);

const jumps = draft.bridgeJumps || [];
const expectJumps = [
  ["fluid-coolant", "engine-bay", "engine-bay"],
  ["fluid-oil", "engine-bay", "engine-bay"],
  ["fluid-fuel", "chassis", "underbody"],
];
for (const [from, toZone, toHot] of expectJumps) {
  const hit = jumps.find(
    (j) =>
      j.fromHotspotId === from &&
      j.toZoneId === toZone &&
      j.toHotspotId === toHot,
  );
  if (!hit) {
    fail(
      `systems-draft bridgeJumps missing ${from}→${toZone}/${toHot}`,
    );
  }
}
console.log(
  `bridgeJumps: ${jumps.length} — ${jumps.map((j) => `${j.fromHotspotId}→${j.toZoneId}/${j.toHotspotId}`).join(", ")}`,
);

console.log("PLOC2 ACCEPT PASS");
process.exit(0);
