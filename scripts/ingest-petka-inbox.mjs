/**
 * Passive PETKA price inbox: user pastes/saves text into .local/petka-inbox/;
 * this script extracts OEM/sku + price, merges .local/petka-prices.tsv, then
 * runs apply:petka-handcopy. Never touches PETKA GUI.
 *
 * Usage:
 *   npm run ingest:petka-inbox
 *   node scripts/ingest-petka-inbox.mjs --dry-run
 *   DRY_RUN=1 node scripts/ingest-petka-inbox.mjs
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL = path.join(root, ".local");
const INBOX = path.join(LOCAL, "petka-inbox");
const DONE = path.join(INBOX, "done");
const PRICES_TSV = path.join(LOCAL, "petka-prices.tsv");
const HANDCOPY = path.join(LOCAL, "petka-handcopy.json");
const TEMPLATE_TSV = path.join(root, "data", "petka", "_template", "prices.tsv");
const REPORT = path.join(LOCAL, "petka-inbox-last.json");
const ZONES = ["engine-bay", "brakes", "chassis"];
const FORCE_EMPTY_PRICE = new Set(["fuel-filter"]);
const TSV_HEADER = "sku\toem\toem_price\tprice_as_of\tnotes";

const dryRun =
  process.argv.includes("--dry-run") ||
  process.env.DRY_RUN === "1" ||
  process.env.PETKA_INBOX_DRY_RUN === "1";

function todayAsOf() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function normalizeOem(s) {
  return String(s || "")
    .replace(/[.\s\-_]/g, "")
    .toUpperCase();
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

function isPriceToken(s) {
  const t = String(s).trim().replace(/^£/, "").replace(/,/g, "");
  if (!/^\d+(\.\d{1,4})?$/.test(t)) return false;
  const n = Number(t);
  return Number.isFinite(n) && n > 0 && n < 100000;
}

function formatPrice(s) {
  const t = String(s).trim().replace(/^£/, "").replace(/,/g, "");
  const n = Number(t);
  if (!Number.isFinite(n)) return t;
  return Number.isInteger(n) ? String(n) : String(n);
}

/** Load known sku/oem from parts.csv + handcopy + template TSV */
function loadKnownParts() {
  const bySku = new Map();
  const byOemNorm = new Map();

  function upsert({ sku, oem }) {
    if (!sku) return;
    const prev = bySku.get(sku) || { sku, oem: "" };
    if (oem && !prev.oem) prev.oem = oem;
    bySku.set(sku, prev);
    if (prev.oem) byOemNorm.set(normalizeOem(prev.oem), prev);
  }

  for (const zone of ZONES) {
    const file = path.join(root, "data", "petka", zone, "parts.csv");
    if (!fs.existsSync(file)) continue;
    const lines = fs
      .readFileSync(file, "utf8")
      .replace(/^\uFEFF/, "")
      .split(/\r?\n/)
      .filter((l) => l.trim());
    if (lines.length < 2) continue;
    const header = parseCsvRow(lines[0]);
    const iSku = header.indexOf("sku");
    const iOem = header.indexOf("oem_number");
    if (iSku < 0) continue;
    for (const line of lines.slice(1)) {
      const cols = parseCsvRow(line);
      upsert({ sku: (cols[iSku] || "").trim(), oem: iOem >= 0 ? (cols[iOem] || "").trim() : "" });
    }
  }

  if (fs.existsSync(HANDCOPY)) {
    try {
      const hc = JSON.parse(fs.readFileSync(HANDCOPY, "utf8"));
      for (const p of hc.parts || []) {
        upsert({ sku: p.sku, oem: p.oem || "" });
      }
    } catch {
      /* ignore */
    }
  }

  const tsvPath = fs.existsSync(PRICES_TSV) ? PRICES_TSV : TEMPLATE_TSV;
  if (fs.existsSync(tsvPath)) {
    const lines = fs
      .readFileSync(tsvPath, "utf8")
      .replace(/^\uFEFF/, "")
      .split(/\r?\n/)
      .filter((l) => l.trim());
    if (lines.length) {
      const header = lines[0].split("\t").map((h) => h.trim());
      const iSku = header.indexOf("sku");
      const iOem = header.indexOf("oem");
      for (const line of lines.slice(1)) {
        const cols = line.split("\t");
        upsert({
          sku: iSku >= 0 ? (cols[iSku] || "").trim() : "",
          oem: iOem >= 0 ? (cols[iOem] || "").trim() : "",
        });
      }
    }
  }

  return { bySku, byOemNorm };
}

const OEM_TOKEN_RE =
  /\b([0-9A-Za-z]{2,4}(?:[.\-][0-9A-Za-z]{2,4}){2,5}|[0-9A-Za-z]{8,14})\b/g;
const PRICE_RE = /£?\s*(\d{1,5}(?:\.\d{1,4})?)/g;

/**
 * Extract {sku,oem,oem_price,source} from inbox text.
 */
function extractFromText(text, known) {
  const hits = [];
  const seenSku = new Set();
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);

  for (let li = 0; li < lines.length; li++) {
    const raw = lines[li];
    const line = raw.trim();
    if (!line || line.startsWith("#") || line.startsWith("//")) continue;

    // sku\tprice  OR  oem\tprice
    {
      const m = line.match(/^([^\t]+)\t+([^\t]+?)(?:\t.*)?$/);
      if (m && isPriceToken(m[2])) {
        const key = m[1].trim();
        const price = formatPrice(m[2]);
        if (known.bySku.has(key)) {
          const part = known.bySku.get(key);
          pushHit(hits, seenSku, {
            sku: part.sku,
            oem: part.oem,
            oem_price: price,
            source: `line:${li + 1}:sku-tab`,
          });
          continue;
        }
        const oemNorm = normalizeOem(key);
        if (known.byOemNorm.has(oemNorm)) {
          const part = known.byOemNorm.get(oemNorm);
          pushHit(hits, seenSku, {
            sku: part.sku,
            oem: part.oem || key,
            oem_price: price,
            source: `line:${li + 1}:oem-tab`,
          });
          continue;
        }
      }
    }

    // oem space/price  e.g. "9A1.107.224.00 24.58" or "9A110722400 £24.58"
    {
      const m = line.match(
        /^([0-9A-Za-z][0-9A-Za-z.\-]{6,})\s+£?\s*(\d{1,5}(?:\.\d{1,4})?)\s*$/,
      );
      if (m && isPriceToken(m[2])) {
        const oemNorm = normalizeOem(m[1]);
        if (known.byOemNorm.has(oemNorm)) {
          const part = known.byOemNorm.get(oemNorm);
          pushHit(hits, seenSku, {
            sku: part.sku,
            oem: part.oem || m[1],
            oem_price: formatPrice(m[2]),
            source: `line:${li + 1}:oem-space`,
          });
          continue;
        }
        if (known.bySku.has(m[1].trim())) {
          const part = known.bySku.get(m[1].trim());
          pushHit(hits, seenSku, {
            sku: part.sku,
            oem: part.oem,
            oem_price: formatPrice(m[2]),
            source: `line:${li + 1}:sku-space`,
          });
          continue;
        }
      }
    }

    // Free-text: known OEM token + nearby price on same line (or next)
    const windowText = [line, lines[li + 1] || ""].join(" ");
    OEM_TOKEN_RE.lastIndex = 0;
    let om;
    while ((om = OEM_TOKEN_RE.exec(line)) !== null) {
      const oemNorm = normalizeOem(om[1]);
      const part = known.byOemNorm.get(oemNorm);
      if (!part) continue;
      // price after OEM on same/next line
      const after = windowText.slice(om.index + om[0].length);
      PRICE_RE.lastIndex = 0;
      let pm;
      let price = null;
      while ((pm = PRICE_RE.exec(after)) !== null) {
        if (isPriceToken(pm[1])) {
          // skip pure OEM digit chunks mistaken as price (too many digits without dot when looking like part of OEM)
          if (!pm[0].includes(".") && pm[1].length >= 6) continue;
          price = formatPrice(pm[1]);
          break;
        }
      }
      if (!price) continue;
      pushHit(hits, seenSku, {
        sku: part.sku,
        oem: part.oem,
        oem_price: price,
        source: `line:${li + 1}:heuristic`,
      });
    }
  }

  return hits;
}

function pushHit(hits, seenSku, hit) {
  if (!hit.sku || !hit.oem_price) return;
  if (FORCE_EMPTY_PRICE.has(hit.sku)) {
    hits.push({ ...hit, oem_price: null, skipped: "fuel-filter_price_forced_empty" });
    return;
  }
  if (seenSku.has(hit.sku)) return;
  seenSku.add(hit.sku);
  hits.push(hit);
}

function listInboxFiles() {
  if (!fs.existsSync(INBOX)) return [];
  return fs
    .readdirSync(INBOX, { withFileTypes: true })
    .filter((d) => d.isFile())
    .map((d) => d.name)
    .filter((name) => {
      if (name.startsWith("_") || name.startsWith(".")) return false;
      if (/^readme/i.test(name)) return false;
      const ext = path.extname(name).toLowerCase();
      return ext === ".txt" || ext === ".tsv" || ext === ".csv";
    })
    .map((name) => path.join(INBOX, name))
    .sort();
}

function loadPricesTsvRows() {
  const bySku = new Map();
  const src = fs.existsSync(PRICES_TSV)
    ? PRICES_TSV
    : fs.existsSync(TEMPLATE_TSV)
      ? TEMPLATE_TSV
      : null;
  if (!src) return { bySku, header: TSV_HEADER.split("\t") };

  const lines = fs
    .readFileSync(src, "utf8")
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .filter((l) => l.trim());
  if (!lines.length) return { bySku, header: TSV_HEADER.split("\t") };
  const header = lines[0].split("\t").map((h) => h.trim());
  const iSku = header.indexOf("sku");
  const iOem = header.indexOf("oem");
  const iPrice = header.indexOf("oem_price");
  const iAsOf = header.indexOf("price_as_of");
  const iNotes = header.indexOf("notes");
  for (const line of lines.slice(1)) {
    const cols = line.split("\t");
    const sku = iSku >= 0 ? (cols[iSku] || "").trim() : "";
    if (!sku) continue;
    bySku.set(sku, {
      sku,
      oem: iOem >= 0 ? (cols[iOem] || "").trim() : "",
      oem_price: iPrice >= 0 ? (cols[iPrice] || "").trim() : "",
      price_as_of: iAsOf >= 0 ? (cols[iAsOf] || "").trim() : "",
      notes: iNotes >= 0 ? (cols[iNotes] || "").trim() : "",
    });
  }
  return { bySku, header };
}

function writePricesTsv(bySku, header) {
  const cols = header.length ? header : TSV_HEADER.split("\t");
  const need = ["sku", "oem", "oem_price", "price_as_of", "notes"];
  for (const h of need) {
    if (!cols.includes(h)) cols.push(h);
  }
  const lines = [cols.join("\t")];
  // stable order: template order if present, else alpha
  const order = [];
  if (fs.existsSync(TEMPLATE_TSV)) {
    const tLines = fs
      .readFileSync(TEMPLATE_TSV, "utf8")
      .split(/\r?\n/)
      .filter((l) => l.trim());
    for (const line of tLines.slice(1)) {
      const sku = (line.split("\t")[0] || "").trim();
      if (sku) order.push(sku);
    }
  }
  const seen = new Set();
  for (const sku of order) {
    const row = bySku.get(sku);
    if (!row) continue;
    seen.add(sku);
    lines.push(
      [row.sku, row.oem || "", row.oem_price || "", row.price_as_of || "", row.notes || ""].join(
        "\t",
      ),
    );
  }
  for (const [sku, row] of [...bySku.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    if (seen.has(sku)) continue;
    lines.push(
      [row.sku, row.oem || "", row.oem_price || "", row.price_as_of || "", row.notes || ""].join(
        "\t",
      ),
    );
  }
  fs.mkdirSync(LOCAL, { recursive: true });
  fs.writeFileSync(PRICES_TSV, lines.join("\n") + "\n", "utf8");
}

function mergeHitsIntoTsv(hits) {
  const { bySku, header } = loadPricesTsvRows();
  const asOf = todayAsOf();
  const written = [];
  const skipped = [];

  for (const hit of hits) {
    if (hit.skipped || FORCE_EMPTY_PRICE.has(hit.sku)) {
      skipped.push({
        sku: hit.sku,
        reason: hit.skipped || "fuel-filter_price_forced_empty",
        source: hit.source,
      });
      continue;
    }
    if (!hit.oem_price) {
      skipped.push({ sku: hit.sku, reason: "empty_price", source: hit.source });
      continue;
    }
    let row = bySku.get(hit.sku);
    if (!row) {
      row = { sku: hit.sku, oem: hit.oem || "", oem_price: "", price_as_of: "", notes: "" };
      bySku.set(hit.sku, row);
    }
    row.oem_price = hit.oem_price;
    row.price_as_of = asOf;
    if (hit.oem && !row.oem) row.oem = hit.oem;
    // Do not append "PETKA inbox" — it does not satisfy isPetkaPriceVerified.
    // apply:petka-handcopy clears pending markers and writes formal 「PETKA价」.
    written.push({
      sku: row.sku,
      oem: row.oem,
      oem_price: row.oem_price,
      price_as_of: row.price_as_of,
      source: hit.source,
    });
  }

  return { bySku, header, written, skipped };
}

function runApply() {
  const r = spawnSync(
    process.platform === "win32" ? "npm.cmd" : "npm",
    ["run", "apply:petka-handcopy"],
    { cwd: root, env: process.env, encoding: "utf8", shell: true },
  );
  let applyJson = null;
  const out = (r.stdout || "") + (r.stderr || "");
  const m = out.match(/\{[\s\S]*\}\s*$/);
  if (m) {
    try {
      applyJson = JSON.parse(m[0]);
    } catch {
      applyJson = { raw: m[0].slice(0, 2000) };
    }
  }
  return {
    ok: r.status === 0,
    status: r.status,
    stdout: (r.stdout || "").trim().slice(0, 4000),
    stderr: (r.stderr || "").trim().slice(0, 1000),
    result: applyJson,
  };
}

function moveToDone(filePath) {
  fs.mkdirSync(DONE, { recursive: true });
  const base = path.basename(filePath);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const dest = path.join(DONE, `${stamp}__${base}`);
  fs.renameSync(filePath, dest);
  return dest;
}

function main() {
  fs.mkdirSync(INBOX, { recursive: true });
  fs.mkdirSync(DONE, { recursive: true });

  const known = loadKnownParts();
  const files = listInboxFiles();
  const perFile = [];
  const allHits = [];

  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    const hits = extractFromText(text, known);
    perFile.push({
      file: path.relative(root, file).replace(/\\/g, "/"),
      hits: hits.map((h) => ({
        sku: h.sku,
        oem: h.oem,
        oem_price: h.oem_price,
        source: h.source,
        skipped: h.skipped || null,
      })),
    });
    for (const h of hits) allHits.push(h);
  }

  const merge = mergeHitsIntoTsv(allHits);
  const wouldWrite = merge.written;
  const skipped = merge.skipped;

  let apply = null;
  let moved = [];

  if (dryRun) {
    // no TSV write, no apply, no move
  } else if (wouldWrite.length > 0) {
    writePricesTsv(merge.bySku, merge.header);
    apply = runApply();
    for (const file of files) {
      try {
        moved.push(path.relative(root, moveToDone(file)).replace(/\\/g, "/"));
      } catch (e) {
        moved.push({ file, error: String(e.message || e) });
      }
    }
  } else if (files.length > 0) {
    // no prices extracted — still move to done to avoid reprocessing loops
    for (const file of files) {
      try {
        moved.push(path.relative(root, moveToDone(file)).replace(/\\/g, "/"));
      } catch (e) {
        moved.push({ file, error: String(e.message || e) });
      }
    }
  }

  const report = {
    ok: dryRun ? true : apply ? Boolean(apply.ok) : true,
    dry_run: dryRun,
    as_of: todayAsOf(),
    inbox: path.relative(root, INBOX).replace(/\\/g, "/"),
    files_scanned: files.map((f) => path.relative(root, f).replace(/\\/g, "/")),
    files_moved: moved,
    known_skus: known.bySku.size,
    extracted: allHits.map((h) => ({
      sku: h.sku,
      oem: h.oem,
      oem_price: h.oem_price,
      source: h.source,
      skipped: h.skipped || null,
    })),
    would_write_tsv: wouldWrite,
    skipped,
    prices_tsv: path.relative(root, PRICES_TSV).replace(/\\/g, "/"),
    apply: dryRun
      ? { skipped: true, reason: "dry_run" }
      : wouldWrite.length === 0
        ? { skipped: true, reason: "no_prices" }
        : apply,
    per_file: perFile,
  };

  if (!dryRun) {
    fs.mkdirSync(LOCAL, { recursive: true });
    fs.writeFileSync(REPORT, JSON.stringify(report, null, 2) + "\n", "utf8");
  }

  console.log(JSON.stringify(report, null, 2));
  process.exit(report.ok ? 0 : 1);
}

main();
