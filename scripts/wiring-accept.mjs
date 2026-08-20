/**
 * Wiring R7 / §6.6 smoke: seed systems stubs + locator bridges; optional local 981.pdf.
 * Asserts at least one system has locatorZoneId present in zones.json.
 * Missing seed.pdf.path file → WARN + soft PASS (exit 0); set WIRING_REQUIRE_PDF=1 for hard FAIL.
 * Does not open PDF GUI or Electron.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const seedPath = path.join(root, "data", "seed", "wiring", "index.json");
const zonesPath = path.join(root, "data", "seed", "locator", "zones.json");

if (!fs.existsSync(seedPath)) {
  console.error(`FAIL: missing seed ${seedPath}`);
  process.exit(1);
}

const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const systems = seed.systems;
if (!Array.isArray(systems) || systems.length === 0) {
  console.error("FAIL: systems stub list empty or missing in seed");
  process.exit(1);
}

console.log(`seed: ${seedPath}`);
console.log(`systems: ${systems.length}  — ${systems.map((s) => s.id).join(", ")}`);

const withZone = systems.filter((s) => typeof s.locatorZoneId === "string" && s.locatorZoneId);
if (withZone.length === 0) {
  console.error("FAIL: no system has locatorZoneId");
  process.exit(1);
}

if (!fs.existsSync(zonesPath)) {
  console.error(`FAIL: missing zones ${zonesPath}`);
  process.exit(1);
}
const zonesDoc = JSON.parse(fs.readFileSync(zonesPath, "utf8"));
const zoneIds = new Set((zonesDoc.zones || []).map((z) => z.id));
for (const s of withZone) {
  if (!zoneIds.has(s.locatorZoneId)) {
    console.error(`FAIL: system ${s.id} locatorZoneId=${s.locatorZoneId} not in zones.json`);
    process.exit(1);
  }
}
console.log(
  `locator bridges: ${withZone.length}  — ${withZone.map((s) => `${s.id}→${s.locatorZoneId}`).join(", ")}`,
);

const pdfRel = seed.pdf?.path;
if (!pdfRel || typeof pdfRel !== "string") {
  console.error("FAIL: seed.pdf.path missing");
  process.exit(1);
}
const pdfPath = path.isAbsolute(pdfRel) ? pdfRel : path.join(root, pdfRel);

if (!fs.existsSync(pdfPath)) {
  const requirePdf = process.env.WIRING_REQUIRE_PDF === "1";
  const msg = `WARN: PDF missing (soft-skip): ${pdfPath}`;
  if (requirePdf) {
    console.error(`FAIL: main PDF not found: ${pdfPath}`);
    process.exit(1);
  }
  console.warn(msg);
  console.log("WIRING ACCEPT PASS (pdf soft-skip)");
  process.exit(0);
}

const st = fs.statSync(pdfPath);
console.log(`pdf: ${seed.pdf.id} (${seed.pdf.label})`);
console.log(`pdf path OK: ${pdfPath} (${st.size} bytes)`);
console.log("WIRING ACCEPT PASS");
process.exit(0);