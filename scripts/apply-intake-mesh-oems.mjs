/**
 * 105-020 子件 OEM → pm-intake mesh 关联；从 pm-engine 移除 _105 迁移项。
 *
 * Usage: npm run apply:intake-oems
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";
import { modelOemLinkId } from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const SEED = path.join(root, "data", "seed", "xray", "intake-mesh-oems.json");
const LINKS_PATH = path.join(root, ".local", "model-oem-links.json");
const DB_PATH = path.join(root, ".local", "garage.db");

const dryRun = process.argv.includes("--dry-run");

function normOem(s) {
  return String(s || "")
    .replace(/[-\s.]/g, "")
    .toUpperCase();
}

function resolveSku(db, oem, alts = []) {
  const want = [oem, ...alts].map(normOem).filter(Boolean);
  const rows = db.prepare("SELECT sku, oem_number FROM parts").all();
  for (const w of want) {
    const hit = rows.find(
      (r) =>
        normOem(r.oem_number).includes(w) ||
        normOem(r.sku).includes(w) ||
        normOem(r.sku).endsWith(w),
    );
    if (hit) return hit.sku;
  }
  return null;
}

function readLinks() {
  if (!fs.existsSync(LINKS_PATH)) {
    return {
      version: 1,
      note: "garage model block → part sku(s)",
      updated_at: null,
      links: [],
    };
  }
  return JSON.parse(fs.readFileSync(LINKS_PATH, "utf8"));
}

function writeLinks(file) {
  file.updated_at = new Date().toISOString();
  fs.mkdirSync(path.dirname(LINKS_PATH), { recursive: true });
  fs.writeFileSync(LINKS_PATH, JSON.stringify(file, null, 2) + "\n", "utf8");
}

const seed = JSON.parse(fs.readFileSync(SEED, "utf8"));
const asmId = seed.assemblyId || "pm-intake";
const db = new DatabaseSync(DB_PATH);

const file = readLinks();
let links = Array.isArray(file.links) ? [...file.links] : [];

const engine105Refs = new Set(
  links
    .filter((l) => l.kind === "mesh" && l.assemblyId === "pm-engine")
    .filter((l) => l.ref.includes("_105"))
    .map((l) => l.ref),
);
engine105Refs.add("tripo_part_0_R");

links = links.filter((l) => {
  if (l.kind !== "mesh" || l.assemblyId !== "pm-engine") return true;
  if (l.ref.includes("_105")) return false;
  if (l.ref === "tripo_part_0_R") return false;
  return true;
});

const added = [];
const missing = [];

for (const row of seed.links || []) {
  const sku = resolveSku(db, row.oem, row.oemAlt || []);
  if (!sku) {
    missing.push(row);
    continue;
  }
  const id = modelOemLinkId("mesh", row.mesh, asmId);
  links = links.filter((l) => l.id !== id);
  links.push({
    id,
    kind: "mesh",
    ref: row.mesh,
    assemblyId: asmId,
    skus: [sku],
    note: row.note || `105-020 pos ${row.pos}`,
  });
  added.push({ mesh: row.mesh, sku, pos: row.pos });
}

if (!dryRun) writeLinks({ ...file, links });

console.log(
  JSON.stringify(
    {
      ok: true,
      dryRun,
      assemblyId: asmId,
      removedEngine105: [...engine105Refs],
      added,
      missing,
      skip: seed.skip || [],
      consumables: seed.consumables || [],
      linkCount: links.length,
    },
    null,
    2,
  ),
);

if (missing.length) {
  console.warn("missing sku for", missing.map((m) => m.oem).join(", "));
}
