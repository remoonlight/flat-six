/**
 * Interval seed + no_baseline acceptance.
 * - community-draft.json: audited/pending; pending must explain why; pending may be 0 after full audit
 * - draft intervals must match parts bootstrap km/months
 * - part without service_record → status no_baseline, null remainings
 * - after service_record → normal dual-constraint interval
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const seedPath = path.join(root, "data/seed/intervals/community-draft.json");
const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
if (!Array.isArray(seed.intervals) || seed.intervals.length === 0) {
  throw new Error("intervals seed empty");
}

const parts = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/parts/bootstrap.json"), "utf8"),
);
const partsBySku = new Map(parts.parts.map((p) => [p.sku, p]));

let audited = 0;
let pending = 0;

for (const row of seed.intervals) {
  if (!row.sku) throw new Error("interval row missing sku");
  if (row.audit_status !== "pending" && row.audit_status !== "audited") {
    throw new Error(`bad audit_status for ${row.sku}: ${row.audit_status}`);
  }
  if (row.interval_km == null && row.interval_months == null) {
    throw new Error(`interval row ${row.sku} has no km/months`);
  }
  if (row.audit_status === "audited") {
    audited += 1;
  } else {
    pending += 1;
    if (typeof row.notes !== "string" || !row.notes.trim()) {
      throw new Error(`pending ${row.sku} must include notes reason`);
    }
  }

  const part = partsBySku.get(row.sku);
  if (!part) {
    throw new Error(`draft sku ${row.sku} missing from parts bootstrap`);
  }
  const partKm = part.interval_km ?? null;
  const partMo = part.interval_months ?? null;
  const seedKm = row.interval_km ?? null;
  const seedMo = row.interval_months ?? null;
  if (partKm !== seedKm || partMo !== seedMo) {
    throw new Error(
      `bootstrap/draft mismatch for ${row.sku}: parts=(${partKm},${partMo}) draft=(${seedKm},${seedMo}) — run npm run sync:intervals`,
    );
  }

  const draftKind = row.interval_kind === "soft" ? "soft" : "hard";
  const notes = part.notes ?? "";
  const hasSoftMarker = /\binterval_kind=soft\b/i.test(notes);
  if (draftKind === "soft" && !hasSoftMarker) {
    throw new Error(
      `soft ${row.sku} missing interval_kind=soft in parts.notes — run npm run sync:intervals`,
    );
  }
  if (draftKind === "hard" && hasSoftMarker) {
    throw new Error(
      `hard ${row.sku} must not carry interval_kind=soft in parts.notes`,
    );
  }
}

if (audited < 1) {
  throw new Error("expected at least one audited interval after human audit");
}
// pending may be 0 after full human audit; pending rows must still carry notes (checked above)

const softRows = seed.intervals.filter((r) => r.interval_kind === "soft");
if (softRows.length < 1) {
  throw new Error("expected at least one soft interval_kind in community-draft");
}
const softSku = softRows[0].sku;
const softPart = partsBySku.get(softSku);
if (!/\binterval_kind=soft\b/i.test(softPart?.notes ?? "")) {
  throw new Error(`soft SKU ${softSku} bootstrap notes missing interval_kind=soft`);
}
console.log(`soft marker ok: ${softSku}`);

const hardOil = partsBySku.get("engine-oil");
if (!hardOil) throw new Error("missing engine-oil");
if (/\binterval_kind=soft\b/i.test(hardOil.notes ?? "")) {
  throw new Error("engine-oil must remain hard (no soft marker)");
}

console.log(
  `interval seed: ${seed.intervals.length} rows, ${audited} audited, ${pending} pending, soft=${softRows.length}`,
);

const dbPath = path.join(root, ".local", "interval-accept.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
try {
  fs.unlinkSync(dbPath);
} catch {
  /* */
}

const faults = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/faults/bootstrap.json"), "utf8"),
);

const db = new GarageDb(dbPath);
db.seedIfEmpty({ parts: parts.parts, faults: faults.faults });
db.setMileage(45_000);
db.setAvgKmPerDay(40);

const oil = db.listParts().find((p) => p.sku === "engine-oil");
if (!oil) throw new Error("missing engine-oil");

const before = db.partIntervalStatus(oil.id);
console.log("no service_record:", JSON.stringify(before, null, 2));
if (!before) throw new Error("expected IntervalResult, got null");
if (before.status !== "no_baseline") {
  throw new Error(`expected no_baseline, got ${before.status}`);
}
if (
  before.remainingKm != null ||
  before.remainingDays != null ||
  before.nextDueKm != null ||
  before.nextDueDate != null
) {
  throw new Error("no_baseline must leave due/remaining null");
}

db.addServiceRecord({
  part_id: oil.id,
  title: "机油更换",
  replaced_at: "2026-01-01",
  odometer_km: 40_000,
  brand: "Castrol",
  cost: 800,
});
const after = db.partIntervalStatus(oil.id);
console.log("with service_record:", JSON.stringify(after, null, 2));
if (!after || after.status === "no_baseline") {
  throw new Error("expected computed interval after service");
}
if (after.nextDueKm !== 50_000) throw new Error(`nextDueKm ${after.nextDueKm}`);
if (after.remainingKm !== 5_000) {
  throw new Error(`remainingKm ${after.remainingKm}`);
}

const plugs = db.listParts().find((p) => p.sku === "spark-plugs");
if (!plugs) throw new Error("missing spark-plugs");
db.addServiceRecord({
  part_id: plugs.id,
  title: "火花塞",
  replaced_at: "2025-01-01",
  odometer_km: 30_000,
});
const plugStatus = db.partIntervalStatus(plugs.id);
if (!plugStatus || plugStatus.nextDueKm !== 90_000) {
  throw new Error(
    `spark-plugs nextDueKm expected 90000 (30k+60k), got ${plugStatus?.nextDueKm}`,
  );
}
console.log("spark-plugs audited interval:", JSON.stringify(plugStatus, null, 2));

const seedSkus = new Set(seed.intervals.map((r) => r.sku));
const partSkusWithInterval = parts.parts
  .filter((p) => p.interval_km != null || p.interval_months != null)
  .map((p) => p.sku);
for (const sku of partSkusWithInterval) {
  if (!seedSkus.has(sku)) {
    throw new Error(`parts bootstrap has interval for ${sku} but seed missing`);
  }
}

console.log("INTERVAL ACCEPT PASS");
db.close();

