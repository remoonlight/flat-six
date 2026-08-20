/**
 * Sync interval_km / interval_months / interval_kind from community-draft.json → parts bootstrap.json.
 * Soft kind is stored as notes marker `interval_kind=soft` (no DB migration).
 * Usage: node scripts/sync-interval-seed.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

/** Keep in sync with packages/domain/src/interval-kind.ts */
function applyIntervalKindMarker(notes, kind) {
  const cleaned = (notes ?? "")
    .replace(/\s*;?\s*interval_kind=(soft|hard)\b/gi, "")
    .replace(/^;\s*/, "")
    .replace(/;\s*;/g, ";")
    .trim();
  if (kind === "soft") {
    return cleaned ? `interval_kind=soft; ${cleaned}` : "interval_kind=soft";
  }
  return cleaned || null;
}

const draftPath = path.join(root, "data/seed/intervals/community-draft.json");
const partsPath = path.join(root, "data/seed/parts/bootstrap.json");

const draft = JSON.parse(fs.readFileSync(draftPath, "utf8"));
const parts = JSON.parse(fs.readFileSync(partsPath, "utf8"));

const bySku = new Map(draft.intervals.map((r) => [r.sku, r]));
let updated = 0;
let missing = [];

for (const part of parts.parts) {
  const row = bySku.get(part.sku);
  if (!row) continue;
  const nextKm = row.interval_km ?? null;
  const nextMo = row.interval_months ?? null;
  const kind = row.interval_kind === "soft" ? "soft" : "hard";
  const nextNotes = applyIntervalKindMarker(part.notes, kind);
  const changed =
    part.interval_km !== nextKm ||
    part.interval_months !== nextMo ||
    (part.notes ?? null) !== nextNotes;
  if (changed) {
    part.interval_km = nextKm;
    part.interval_months = nextMo;
    part.notes = nextNotes;
    updated += 1;
    console.log(
      `updated ${part.sku}: km=${nextKm} months=${nextMo} kind=${kind} (${row.audit_status})`,
    );
  }
}

for (const row of draft.intervals) {
  if (!parts.parts.some((p) => p.sku === row.sku)) {
    missing.push(row.sku);
  }
}

fs.writeFileSync(partsPath, `${JSON.stringify(parts, null, 2)}\n`, "utf8");
console.log(
  `sync:intervals done — ${updated} part(s) changed, draft=${draft.intervals.length}`,
);
if (missing.length) {
  console.warn(`warn: draft SKUs missing in parts bootstrap: ${missing.join(", ")}`);
  process.exitCode = 1;
}
