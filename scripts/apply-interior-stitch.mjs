/**
 * Upsert interior-stitch.plan.json proposedLinks into .local/cms-mesh-map.json.
 * Run: node scripts/apply-interior-stitch.mjs  |  npm run apply:interior-stitch
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { upsertMeshLink } from "../apps/desktop/electron/cms-assets.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const planPath = path.join(root, "data", "seed", "flat-six", "interior-stitch.plan.json");

const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
const links = Array.isArray(plan.proposedLinks) ? plan.proposedLinks : [];
if (!links.length) throw new Error("proposedLinks_empty");

const assetId = plan.assetId || "cabin981";
let written = 0;
for (const link of links) {
  upsertMeshLink({
    zoneId: "interior",
    meshName: link.meshName,
    hotspotId: link.hotspotId,
    assetId,
    note: link.label_zh || null,
  });
  written++;
}

console.log(
  `APPLY INTERIOR STITCH: wrote ${written} link(s) zone=interior assetId=${assetId}`,
);
