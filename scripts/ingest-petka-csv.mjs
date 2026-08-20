/**
 * Ingest data/petka/<zone>/parts.csv into garage DB (upsert by sku).
 * Skips _template and *-example / 示例 rows.
 *
 * Usage:
 *   npm run ingest:petka
 *   PORSCHE981_DB=.local/garage.db npm run ingest:petka
 *   node scripts/ingest-petka-csv.mjs --file data/petka/engine-bay/parts.csv
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parsePetkaPartsCsv } from "../packages/domain/dist/index.js";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const ZONES = ["engine-bay", "brakes", "chassis"];

function parseArgs(argv) {
  const out = { file: null, db: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--file") out.file = argv[++i];
    else if (argv[i] === "--db") out.db = argv[++i];
  }
  return out;
}

function resolveDb(cliDb) {
  if (cliDb) return path.resolve(cliDb);
  if (process.env.PORSCHE981_DB) return path.resolve(process.env.PORSCHE981_DB);
  return path.join(root, ".local", "garage.db");
}

function collectCsvFiles(fileArg) {
  if (fileArg) {
    const p = path.resolve(root, fileArg);
    if (!fs.existsSync(p)) throw new Error(`file not found: ${p}`);
    return [p];
  }
  const found = [];
  for (const zone of ZONES) {
    const p = path.join(root, "data", "petka", zone, "parts.csv");
    if (fs.existsSync(p)) found.push(p);
  }
  return found;
}

const args = parseArgs(process.argv.slice(2));
const dbPath = resolveDb(args.db);
const files = collectCsvFiles(args.file);

if (files.length === 0) {
  console.log(
    JSON.stringify(
      {
        ok: true,
        message: "no zone parts.csv found (engine-bay/brakes/chassis)",
        db: dbPath,
      },
      null,
      2,
    ),
  );
  process.exit(0);
}

const db = new GarageDb(dbPath);
let inserted = 0;
let updated = 0;
const report = [];

for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  const parsed = parsePetkaPartsCsv(text);
  if (parsed.errors.length) {
    console.error(
      JSON.stringify(
        { ok: false, file, errors: parsed.errors },
        null,
        2,
      ),
    );
    db.close();
    process.exit(1);
  }
  const r = db.upsertParts(parsed.drafts);
  inserted += r.inserted;
  updated += r.updated;
  report.push({
    file: path.relative(root, file),
    drafts: parsed.drafts.length,
    skipped: parsed.skipped,
    inserted: r.inserted,
    updated: r.updated,
  });
}

db.close();
console.log(
  JSON.stringify(
    { ok: true, db: dbPath, inserted, updated, files: report },
    null,
    2,
  ),
);
