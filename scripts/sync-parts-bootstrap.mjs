/**
 * Upsert data/seed/parts/bootstrap.json into garage DB (by sku).
 * Does NOT change seedIfEmpty: empty DB still seeds once on App start;
 * this is the explicit path when bootstrap hotspots/rows change on a non-empty DB.
 *
 * Usage:
 *   npm run sync:parts-bootstrap
 *   PORSCHE981_DB=.local/garage.db npm run sync:parts-bootstrap
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const EXPECTED_HOTSPOTS = {
  "cabin-filter": "int-cabin-filter",
  battery: "front-trunk",
  "wiper-blades": "wipers",
};

function resolveDb(argv) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--db") return path.resolve(argv[++i]);
  }
  if (process.env.PORSCHE981_DB) return path.resolve(process.env.PORSCHE981_DB);
  return path.join(root, ".local", "garage.db");
}

const dbPath = resolveDb(process.argv.slice(2));
const seedPath = path.join(root, "data", "seed", "parts", "bootstrap.json");
const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
if (!Array.isArray(seed.parts) || seed.parts.length === 0) {
  throw new Error(`bootstrap empty or invalid: ${seedPath}`);
}

fs.mkdirSync(path.dirname(dbPath), { recursive: true });
const db = new GarageDb(dbPath);
const before = db.listParts().length;
const { inserted, updated } = db.upsertParts(seed.parts);
const after = db.listParts().length;

const hotspots = {};
for (const [sku, want] of Object.entries(EXPECTED_HOTSPOTS)) {
  const row = db.getPartBySku(sku);
  if (!row) throw new Error(`selfcheck: missing sku ${sku} after sync`);
  if (row.locator_hotspot !== want) {
    throw new Error(
      `selfcheck: ${sku} hotspot=${row.locator_hotspot} want=${want}`,
    );
  }
  hotspots[sku] = row.locator_hotspot;
}

db.close();

console.log(
  JSON.stringify(
    {
      ok: true,
      db: dbPath,
      bootstrap: seed.parts.length,
      before,
      inserted,
      updated,
      total: after,
      hotspots,
    },
    null,
    2,
  ),
);
