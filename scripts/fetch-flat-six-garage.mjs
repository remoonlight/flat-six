/**
 * Download flat-six.org/garage exterior GLBs → .local/flat-six/
 * Inventory: data/seed/flat-six/garage-models.json
 * Run: node scripts/fetch-flat-six-garage.mjs
 * Optional: --only=982  | --only=browse  (skip productRole entries already in cabins fetch)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const outDir = path.join(root, ".local", "flat-six");
const seedPath = path.join(root, "data", "seed", "flat-six", "garage-models.json");

const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const base = String(seed.downloadBase || "").replace(/\/$/, "");
const only = (process.argv.find((a) => a.startsWith("--only=")) || "")
  .slice("--only=".length)
  .trim();

fs.mkdirSync(outDir, { recursive: true });

let models = Array.isArray(seed.models) ? seed.models : [];
if (only === "browse") {
  models = models.filter((m) => m.browseOnly === true);
} else if (only) {
  models = models.filter((m) => String(m.generation) === only);
}

let ok = 0;
let skipped = 0;
let failed = 0;

for (const m of models) {
  const remote = m.sourceFile;
  const localName = m.localName || m.sourceFile;
  if (!remote || !localName) {
    console.log(`skip bad entry ${m.id}`);
    failed += 1;
    continue;
  }
  const dest = path.join(outDir, localName);
  const url = `${base}/${remote}`;
  if (fs.existsSync(dest) && fs.statSync(dest).size > 1000) {
    console.log(`skip ${localName} (${fs.statSync(dest).size} bytes)`);
    skipped += 1;
    continue;
  }
  process.stdout.write(`GET ${url} → ${localName} ... `);
  try {
    const res = await fetch(url);
    if (!res.ok) {
      console.log(`FAIL ${res.status}`);
      failed += 1;
      process.exitCode = 1;
      continue;
    }
    const buf = Buffer.from(await res.arrayBuffer());
    fs.writeFileSync(dest, buf);
    console.log(`${buf.length} bytes`);
    ok += 1;
  } catch (e) {
    console.log(`ERR ${e?.message || e}`);
    failed += 1;
    process.exitCode = 1;
  }
}

console.log(
  `done → ${path.relative(root, outDir)} (ok=${ok} skip=${skipped} fail=${failed})`,
);
