/**
 * Parse `.local/petka-bulk/{981|982}/*.txt` → merged parts.csv per generation.
 *
 * Usage:
 *   npm run parse:petka-bulk
 *   node scripts/parse-petka-bulk.mjs --gen 981
 *   node scripts/parse-petka-bulk.mjs --selfcheck
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  bulkRowsToPetkaCsv,
  parsePetkaBulkText,
} from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const BULK_ROOT = path.join(root, ".local", "petka-bulk");

function parseArgs(argv) {
  const out = { gen: null, selfcheck: false, nameLang: "unknown" };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--gen") out.gen = argv[++i];
    else if (argv[i] === "--selfcheck") out.selfcheck = true;
    else if (argv[i] === "--name-lang") out.nameLang = argv[++i];
  }
  return out;
}

function runSelfcheck() {
  const fixtureDir = path.join(root, "data", "petka", "_template", "bulk-fixtures");
  const sample = fs.readFileSync(path.join(fixtureDir, "hg-engine.txt"), "utf8");
  const rows = parsePetkaBulkText(sample);
  if (rows.length < 3) throw new Error(`expected >=3 rows, got ${rows.length}`);
  const oil = rows.find((r) => /9A1\.107\.224\.00/i.test(r.oem_number));
  if (!oil) throw new Error("missing oil filter OEM");
  if (oil.oem_price !== 28.61) throw new Error(`oil price want 28.61 got ${oil.oem_price}`);
  const csv = bulkRowsToPetkaCsv(rows, { generation: "981", system: "engine", zone: "engine-bay", nameLang: "en" });
  if (!csv.includes("generation")) throw new Error("csv missing generation col");
  if (!csv.includes("981-9A110722400")) throw new Error("csv missing bulk sku");
  console.log(JSON.stringify({ ok: true, selfcheck: true, rows: rows.length }, null, 2));
}

function mergeGen(gen, nameLang) {
  const dir = path.join(BULK_ROOT, gen);
  if (!fs.existsSync(dir)) {
    return { gen, ok: false, error: `missing dir ${dir}` };
  }
  const files = fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".txt") && !f.startsWith("."))
    .sort();
  const all = [];
  const seen = new Set();
  const perFile = [];
  for (const f of files) {
    const text = fs.readFileSync(path.join(dir, f), "utf8");
    const rows = parsePetkaBulkText(text);
    let added = 0;
    for (const r of rows) {
      const key = r.oem_number.replace(/[.\s]/g, "").toUpperCase();
      if (seen.has(key)) continue;
      seen.add(key);
      all.push(r);
      added++;
    }
    perFile.push({ file: f, parsed: rows.length, added });
  }
  const csv = bulkRowsToPetkaCsv(all, {
    generation: gen,
    system: "bulk",
    zone: "bulk",
    nameLang,
  });
  const outCsv = path.join(dir, "parts.csv");
  fs.writeFileSync(outCsv, "\uFEFF" + csv, "utf8");
  return {
    gen,
    ok: true,
    files: perFile,
    total_unique: all.length,
    with_price: all.filter((r) => r.oem_price != null).length,
    out: path.relative(root, outCsv),
  };
}

const args = parseArgs(process.argv.slice(2));
if (args.selfcheck) {
  runSelfcheck();
  process.exit(0);
}

const gens = args.gen ? [args.gen] : ["981", "982"];
for (const g of gens) {
  if (g !== "981" && g !== "982") {
    console.error(JSON.stringify({ ok: false, error: `bad gen ${g}` }));
    process.exit(1);
  }
}

fs.mkdirSync(BULK_ROOT, { recursive: true });
const report = gens.map((g) => mergeGen(g, args.nameLang));
console.log(JSON.stringify({ ok: report.every((r) => r.ok), report }, null, 2));
if (!report.every((r) => r.ok)) process.exit(1);
