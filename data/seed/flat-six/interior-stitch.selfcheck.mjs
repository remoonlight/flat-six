/**
 * Self-check: interior-stitch.plan.json vs cabin-981-boxster hints + zones.json interior.
 * Run: node data/seed/flat-six/interior-stitch.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../../..");
const planPath = path.join(__dirname, "interior-stitch.plan.json");
const cabinsPath = path.join(__dirname, "cabins.json");
const zonesPath = path.join(root, "data", "seed", "locator", "zones.json");

const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
const cabins = JSON.parse(fs.readFileSync(cabinsPath, "utf8"));
const zonesDoc = JSON.parse(fs.readFileSync(zonesPath, "utf8"));

if (plan.status !== "draft") throw new Error("status_must_be_draft");
if (plan.assetId !== "cabin981") throw new Error("assetId_must_be_cabin981");
if (plan.cabinId !== "cabin-981-boxster") throw new Error("cabinId_mismatch");

const blob = JSON.stringify(plan);
if (blob.includes("porsche991_body")) throw new Error("must_not_reference_porsche991_body");

const primary = (cabins.cabins || []).find((c) => c.id === "cabin-981-boxster");
if (!primary) throw new Error("cabin_981_boxster_missing");
const hints = new Set(primary.cabinMeshHints || []);

const links = Array.isArray(plan.proposedLinks) ? plan.proposedLinks : [];
if (!links.length) throw new Error("proposedLinks_empty");

const hotspotIds = new Set((plan.hotspots || []).map((h) => h.id));
for (const link of links) {
  if (!hints.has(link.meshName)) {
    throw new Error(`mesh_not_in_cabinMeshHints:${link.meshName}`);
  }
  if (!hotspotIds.has(link.hotspotId)) {
    throw new Error(`hotspot_undeclared:${link.hotspotId}`);
  }
}

for (const ctrl of plan.controlHints || []) {
  if ((ctrl.meshes || []).some((m) => links.some((l) => l.meshName === m))) {
    throw new Error(`control_mesh_leaked_into_proposed:${ctrl.cabinId}`);
  }
}

const interior = (zonesDoc.zones || []).find((z) => z.id === "interior");
if (!interior) throw new Error("zones_missing_interior");
const zoneHotspotIds = new Set((interior.hotspots || []).map((h) => h.id));
if (zoneHotspotIds.size !== hotspotIds.size) {
  throw new Error(
    `interior_hotspot_count_mismatch:plan=${hotspotIds.size} zones=${zoneHotspotIds.size}`,
  );
}
for (const id of hotspotIds) {
  if (!zoneHotspotIds.has(id)) throw new Error(`zones_missing_hotspot:${id}`);
}

console.log(
  `INTERIOR STITCH SELFCHECK PASS; zone=interior hotspots=${[...hotspotIds].join(",")} links=${links.length}`,
);
