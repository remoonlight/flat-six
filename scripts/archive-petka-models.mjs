/**
 * Move data/model/*.glb → .local/petka-models/ per chassis-exhaust seed.
 * Run: node scripts/archive-petka-models.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const seedPath = path.join(
  root,
  "data",
  "seed",
  "petka-models",
  "chassis-exhaust.json",
);
const dropDir = path.join(root, "data", "model");
const localDir = path.join(root, ".local", "petka-models");

const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const models = Array.isArray(seed.models) ? seed.models : [];
fs.mkdirSync(localDir, { recursive: true });
fs.mkdirSync(dropDir, { recursive: true });

let moved = 0;
let already = 0;
let missing = 0;

for (const m of models) {
  const file = String(m.file || "");
  if (!file.endsWith(".glb")) continue;
  const src = path.join(dropDir, file);
  const dest = path.join(localDir, file);
  if (fs.existsSync(dest)) {
    already += 1;
    if (fs.existsSync(src)) {
      // drop copy leftover — remove after local has authority
      fs.unlinkSync(src);
    }
    continue;
  }
  if (!fs.existsSync(src)) {
    missing += 1;
    console.warn(`missing drop: ${file}`);
    continue;
  }
  fs.renameSync(src, dest);
  moved += 1;
  console.log(`archived ${file}`);
}

// orphan glbs in drop still move if not in seed? keep strict — only seed list
const present = models.filter((m) =>
  fs.existsSync(path.join(localDir, String(m.file || ""))),
).length;

console.log(
  JSON.stringify(
    {
      ok: true,
      seed: models.length,
      present,
      moved,
      already,
      missing,
      localDir: ".local/petka-models",
    },
    null,
    2,
  ),
);
