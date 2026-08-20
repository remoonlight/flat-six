/**
 * Download free 981/982 cabin GLBs from flat-six into .local/flat-six/.
 * Run: node scripts/fetch-flat-six-cabins.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const outDir = path.join(root, ".local", "flat-six");
const seedPath = path.join(root, "data", "seed", "flat-six", "cabins.json");

const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const base = seed.downloadBase.replace(/\/$/, "");

fs.mkdirSync(outDir, { recursive: true });

for (const c of seed.cabins) {
  const remote = c.sourceFile;
  const localName = c.localName || c.sourceFile;
  const dest = path.join(outDir, localName);
  const url = `${base}/${remote}`;
  if (fs.existsSync(dest) && fs.statSync(dest).size > 1000) {
    console.log(`skip ${localName} (${fs.statSync(dest).size} bytes)`);
    continue;
  }
  process.stdout.write(`GET ${url} → ${localName} ... `);
  const res = await fetch(url);
  if (!res.ok) {
    console.log(`FAIL ${res.status}`);
    process.exitCode = 1;
    continue;
  }
  const buf = Buffer.from(await res.arrayBuffer());
  fs.writeFileSync(dest, buf);
  console.log(`${buf.length} bytes`);
}

console.log(`done → ${path.relative(root, outDir)}`);
