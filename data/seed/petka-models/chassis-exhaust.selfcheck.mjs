/**
 * Self-check: 981 chassis/exhaust petka-models seed + catalog wiring.
 * Run: node data/seed/petka-models/chassis-exhaust.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  listCmsAssets,
  ZONE_DEFAULT_ASSET,
} from "../../../apps/desktop/electron/cms-assets.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../../..");
const seedPath = path.join(__dirname, "chassis-exhaust.json");
const localDir = path.join(root, ".local", "petka-models");

const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const models = Array.isArray(seed.models) ? seed.models : [];
if (models.length !== 21) throw new Error(`expected_21_got_${models.length}`);

const assets = listCmsAssets();
const zoneDefaults = new Set(Object.values(ZONE_DEFAULT_ASSET));

for (const m of models) {
  if (!m.browseOnly) throw new Error(`must_browse_only:${m.id}`);
  if (!String(m.rel || "").startsWith("petka-models/")) {
    throw new Error(`rel_root:${m.id}`);
  }
  if (!["chassis", "exhaust", "engine", "driveline", "other"].includes(m.system)) {
    throw new Error(`system:${m.id}`);
  }
  const a = assets.find((x) => x.id === m.id);
  if (!a || a.rel !== m.rel || !a.browseOnly) {
    throw new Error(`catalog_missing:${m.id}`);
  }
  if (zoneDefaults.has(m.id)) {
    throw new Error(`zone_default_forbidden:${m.id}`);
  }
  const abs = path.join(localDir, m.file);
  if (!fs.existsSync(abs)) {
    throw new Error(`glb_missing:${m.file}`);
  }
}

console.log("chassis-exhaust.selfcheck: OK", { models: models.length });
