/**
 * Apply PETKA hand-copied prices from .local/petka-handcopy.json (and optional
 * .local/petka-prices.tsv) into data/petka/{zone}/parts.csv, then run ingest:petka.
 *
 * Pure file/process background — never touches PETKA UI / focus / screenshots.
 *
 * Usage:
 *   npm run apply:petka-handcopy
 *   node scripts/apply-petka-handcopy.mjs
 *
 * Fill prices (easiest):
 *   1. Copy data/petka/_template/prices.tsv -> .local/petka-prices.tsv
 *   2. Fill oem_price (tab-separated); optional price_as_of / notes
 *   3. npm run apply:petka-handcopy  (or watch:petka-handcopy)
 *
 * Or fill .local/petka-handcopy.json parts[].oem_price.
 * fuel-filter: oem_price is always forced empty (never written).
 * Leave oem_price null/empty to skip that SKU (e.g. tire-fl).
 * Writing a non-empty oem_price clears pending notes markers and adds 「PETKA价」.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const HANDCOPY = path.join(root, ".local", "petka-handcopy.json");
const PRICES_TSV = path.join(root, ".local", "petka-prices.tsv");
const ZONES = ["engine-bay", "brakes", "chassis"];
const FORCE_EMPTY_PRICE = new Set(["fuel-filter"]);

/** Load markPetkaPriceVerified from domain dist (build if missing). */
async function loadMarkPetkaPriceVerified() {
  const dist = path.join(root, "packages", "domain", "dist", "price-verify.js");
  if (!fs.existsSync(dist)) {
    const r = spawnSync(
      process.platform === "win32" ? "npm.cmd" : "npm",
      ["run", "build", "-w", "@porsche981/domain"],
      { cwd: root, encoding: "utf8", shell: true },
    );
    if (r.status !== 0) {
      throw new Error(
        `domain build failed before apply: ${(r.stderr || r.stdout || "").slice(0, 500)}`,
      );
    }
  }
  const mod = await import(pathToFileURL(dist).href);
  return mod.markPetkaPriceVerified;
}

function parseCsvRow(line) {
  const out = [];
  let cur = "";
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === '"') {
      if (q && line[i + 1] === '"') {
        cur += '"';
        i++;
        continue;
      }
      q = !q;
      continue;
    }
    if (c === "," && !q) {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += c;
  }
  out.push(cur);
  return out;
}

function escapeCsvField(val) {
  const s = val == null ? "" : String(val);
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function rowToLine(fields) {
  return fields.map(escapeCsvField).join(",");
}

function todayAsOf() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function isNonEmptyPrice(v) {
  if (v == null) return false;
  if (typeof v === "number") return Number.isFinite(v);
  const s = String(v).trim();
  return s !== "" && s.toLowerCase() !== "null";
}

function formatPrice(v) {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(v);
  return String(v).trim();
}

function loadZoneIndex() {
  const bySku = new Map();
  const files = {};
  for (const zone of ZONES) {
    const file = path.join(root, "data", "petka", zone, "parts.csv");
    if (!fs.existsSync(file)) continue;
    const text = fs.readFileSync(file, "utf8");
    const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
    while (lines.length && lines[lines.length - 1] === "") lines.pop();
    if (lines.length === 0) continue;
    const header = parseCsvRow(lines[0]);
    const skuIdx = header.indexOf("sku");
    const rows = lines.slice(1).map(parseCsvRow);
    files[zone] = { zone, file, header, rows, skuIdx };
    for (const row of rows) {
      const sku = row[skuIdx];
      if (sku) bySku.set(sku, files[zone]);
    }
  }
  return { bySku, files };
}

/** Parse .local/petka-prices.tsv -> rows */
function loadPricesTsv() {
  if (!fs.existsSync(PRICES_TSV)) return { path: PRICES_TSV, present: false, rows: [] };
  const text = fs.readFileSync(PRICES_TSV, "utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (lines.length === 0) return { path: PRICES_TSV, present: true, rows: [] };
  const header = lines[0].split("\t").map((h) => h.trim());
  const idx = {
    sku: header.indexOf("sku"),
    oem: header.indexOf("oem"),
    oem_price: header.indexOf("oem_price"),
    price_as_of: header.indexOf("price_as_of"),
    notes: header.indexOf("notes"),
  };
  if (idx.sku < 0 || idx.oem_price < 0) {
    throw new Error(
      `invalid TSV header in ${PRICES_TSV}; need sku and oem_price (got: ${header.join(",")})`,
    );
  }
  const rows = [];
  for (const line of lines.slice(1)) {
    const cols = line.split("\t");
    const sku = (cols[idx.sku] || "").trim();
    if (!sku) continue;
    rows.push({
      sku,
      oem: idx.oem >= 0 ? (cols[idx.oem] || "").trim() : "",
      oem_price: (cols[idx.oem_price] || "").trim(),
      price_as_of: idx.price_as_of >= 0 ? (cols[idx.price_as_of] || "").trim() : "",
      notes: idx.notes >= 0 ? (cols[idx.notes] || "").trim() : "",
    });
  }
  return { path: PRICES_TSV, present: true, rows };
}

/**
 * Merge non-empty TSV oem_price into handcopy.parts (by sku).
 * fuel-filter: force empty price even if TSV has a value.
 */
function mergeTsvIntoHandcopy(handcopy, tsv) {
  if (!tsv.present || tsv.rows.length === 0) {
    return { merged: 0, forced_empty: [] };
  }
  if (!Array.isArray(handcopy.parts)) handcopy.parts = [];
  const bySku = new Map(handcopy.parts.map((p) => [p.sku, p]));
  let merged = 0;
  const forced_empty = [];

  for (const row of tsv.rows) {
    let part = bySku.get(row.sku);
    if (!part) {
      part = { sku: row.sku, oem: row.oem || "", oem_price: null };
      handcopy.parts.push(part);
      bySku.set(row.sku, part);
    }

    if (FORCE_EMPTY_PRICE.has(row.sku)) {
      if (isNonEmptyPrice(row.oem_price)) forced_empty.push(row.sku);
      part.oem_price = null;
      continue;
    }

    if (!isNonEmptyPrice(row.oem_price)) continue;

    part.oem_price = formatPrice(row.oem_price);
    if (row.price_as_of) part.price_as_of = row.price_as_of;
    if (row.notes) part.notes_append = row.notes;
    if (row.oem && !part.oem) part.oem = row.oem;
    merged++;
  }

  return { merged, forced_empty };
}

function applyUpdates(handcopy, markPetkaPriceVerified) {
  const { bySku, files } = loadZoneIndex();
  const asOfDefault = handcopy.as_of || todayAsOf();
  const updated = [];
  const skipped = [];
  const dirtyZones = new Set();

  for (const part of handcopy.parts || []) {
    const sku = part.sku;
    if (!sku) continue;

    if (FORCE_EMPTY_PRICE.has(sku)) {
      if (isNonEmptyPrice(part.oem_price)) {
        skipped.push({ sku, reason: "fuel-filter_price_forced_empty" });
      } else {
        skipped.push({ sku, reason: "fuel-filter_price_must_stay_empty" });
      }
      continue;
    }

    if (!isNonEmptyPrice(part.oem_price)) {
      skipped.push({ sku, reason: "oem_price_empty" });
      continue;
    }

    const zoneFile = bySku.get(sku);
    if (!zoneFile) {
      skipped.push({ sku, reason: "sku_not_in_csv" });
      continue;
    }

    const { header, rows } = zoneFile;
    const idx = {
      oem_price: header.indexOf("oem_price"),
      currency: header.indexOf("currency"),
      price_as_of: header.indexOf("price_as_of"),
      notes: header.indexOf("notes"),
      sku: header.indexOf("sku"),
    };
    const row = rows.find((r) => r[idx.sku] === sku);
    if (!row) {
      skipped.push({ sku, reason: "row_missing" });
      continue;
    }

    const price = formatPrice(part.oem_price);
    const asOf = part.price_as_of || asOfDefault;
    row[idx.oem_price] = price;
    if (idx.currency >= 0) row[idx.currency] = "GBP";
    if (idx.price_as_of >= 0) row[idx.price_as_of] = asOf;
    if (idx.notes >= 0) {
      // Clear pending markers; write formal PETKA价 (not bare "PETKA" / "PETKA inbox")
      row[idx.notes] = markPetkaPriceVerified(
        row[idx.notes],
        part.notes_append || null,
      );
    }

    dirtyZones.add(zoneFile.zone);
    updated.push({ sku, zone: zoneFile.zone, oem_price: price, price_as_of: asOf });
  }

  for (const zone of dirtyZones) {
    const z = files[zone];
    const body = [rowToLine(z.header), ...z.rows.map(rowToLine)].join("\n") + "\n";
    fs.writeFileSync(z.file, body, "utf8");
  }

  return { updated, skipped, dirtyZones: [...dirtyZones] };
}

function runIngest() {
  const db =
    process.env.PORSCHE981_DB || path.join(root, ".local", "garage.db");
  const env = { ...process.env, PORSCHE981_DB: db };
  const r = spawnSync(
    process.platform === "win32" ? "npm.cmd" : "npm",
    ["run", "ingest:petka"],
    { cwd: root, env, encoding: "utf8", shell: true },
  );
  let ingestJson = null;
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/\{[\s\S]*\}\s*$/);
  if (m) {
    try {
      ingestJson = JSON.parse(m[0]);
    } catch {
      ingestJson = { raw: m[0].slice(0, 2000) };
    }
  }
  return {
    ok: r.status === 0,
    status: r.status,
    db,
    stdout: (r.stdout || "").trim(),
    stderr: (r.stderr || "").trim(),
    result: ingestJson,
  };
}

async function main() {
  if (!fs.existsSync(HANDCOPY)) {
    console.log(
      JSON.stringify(
        { ok: false, error: `missing ${HANDCOPY}`, updated: [], ingest: null },
        null,
        2,
      ),
    );
    process.exit(1);
  }

  const markPetkaPriceVerified = await loadMarkPetkaPriceVerified();
  const handcopy = JSON.parse(fs.readFileSync(HANDCOPY, "utf8"));
  const tsv = loadPricesTsv();
  const tsvMerge = mergeTsvIntoHandcopy(handcopy, tsv);
  const { updated, skipped, dirtyZones } = applyUpdates(
    handcopy,
    markPetkaPriceVerified,
  );

  let ingest = null;
  if (updated.length > 0) {
    ingest = runIngest();
  } else {
    ingest = { ok: true, skipped: true, message: "no csv changes; ingest skipped" };
  }

  const summary = {
    ok: updated.length === 0 ? true : Boolean(ingest?.ok),
    updated_count: updated.length,
    updated_skus: updated.map((u) => u.sku),
    updated,
    skipped,
    dirty_zones: dirtyZones,
    currency: "GBP",
    prices_tsv: {
      path: PRICES_TSV,
      present: tsv.present,
      rows: tsv.rows?.length ?? 0,
      merged_nonempty: tsvMerge.merged,
      fuel_filter_forced_empty: tsvMerge.forced_empty,
    },
    ingest_ok: Boolean(ingest?.ok),
    ingest,
    handcopy: HANDCOPY,
  };

  console.log(JSON.stringify(summary, null, 2));
  process.exit(summary.ok ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
