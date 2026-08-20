/**
 * Download flat-six 981 X-RAY component GLBs → .local/flat-six/components/
 * Source: https://github.com/dmitry-grechko/flat-six (MIT / bundled CC assets)
 * Run: node scripts/fetch-flat-six-components.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const outDir = path.join(root, ".local", "flat-six", "components");
const base =
  "https://raw.githubusercontent.com/dmitry-grechko/flat-six/main/public/models/components";

const IDS = [
  "engine",
  "trans",
  "exhaust",
  "fbrakes",
  "rbrakes",
  "cooling",
  "oil",
  "airfilter",
  "plugs",
  "susp",
  "elec",
  "driveline",
  "fuel",
];

fs.mkdirSync(outDir, { recursive: true });

async function get(name) {
  const dest = path.join(outDir, name);
  if (fs.existsSync(dest) && fs.statSync(dest).size > 500) {
    console.log(`skip ${name} (${fs.statSync(dest).size} bytes)`);
    return;
  }
  const url = `${base}/${name}`;
  process.stdout.write(`GET ${name} ... `);
  const res = await fetch(url);
  if (!res.ok) {
    console.log(`FAIL ${res.status}`);
    process.exitCode = 1;
    return;
  }
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(dest, buf);
  console.log(`${buf.length} bytes`);
}

for (const id of IDS) {
  await get(`${id}.glb`);
  await get(`${id}-parts.json`);
}
await get("manifest.json");

console.log(`done → ${path.relative(root, outDir)}`);
