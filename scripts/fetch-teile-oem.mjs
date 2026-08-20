/**
 * teile.com OEM 价 → garage.db（存 EUR；UI 经 fx.json 折 CNY）。
 * 非 PETKA 核。不覆盖已 PETKA 核价。
 *
 * 查价走 Cursor 浏览器同源 POST /api/dynamic-search（Node/Playwright 会被 CF 拦）。
 *
 * Usage:
 *   node scripts/fetch-teile-oem.mjs --apply-cache
 *   node scripts/fetch-teile-oem.mjs --queue
 *   node scripts/fetch-teile-oem.mjs --apply .local/teile-oem/hits.json
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";
import { isPetkaPriceVerified } from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const OUT = path.join(root, ".local", "teile-oem");
const QUEUE = path.join(OUT, "queue.json");
const HITS = path.join(OUT, "hits.json");
const EUR_CNY = 7.9;

function compactOem(oem) {
  return String(oem || "")
    .replace(/[.\s\-_]/g, "")
    .toUpperCase();
}

function parseEur(raw) {
  if (raw == null || raw === "") return null;
  if (typeof raw === "number") return Number.isFinite(raw) && raw > 0 ? raw : null;
  const google = Number(raw);
  if (Number.isFinite(google) && google > 0 && !String(raw).includes(",")) {
    return google;
  }
  const t = String(raw)
    .trim()
    .replace(/\s/g, "")
    .replace(/\./g, "")
    .replace(",", ".");
  const n = Number(t);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function teileNote(existing) {
  const segs = String(existing || "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
    .filter(
      (s) =>
        !/^currency=/i.test(s) &&
        !/^petka_verified=/i.test(s) &&
        !/^source=/i.test(s),
    );
  const alts = segs.filter((s) => /^design911=/i.test(s));
  const rest = segs.filter((s) => !/^design911=/i.test(s));
  return ["currency=EUR", ...alts, "petka_verified=0", "source=teile.com", ...rest]
    .filter(Boolean)
    .join("; ");
}

function loadCachePrices() {
  const by = new Map();
  for (const gen of ["981", "982"]) {
    const f = path.join(root, ".local", "teile-bulk", gen, "parts.json");
    if (!fs.existsSync(f)) continue;
    const rows = JSON.parse(fs.readFileSync(f, "utf8"));
    for (const r of rows) {
      const c = compactOem(r.oem_compact || r.oem_number);
      const p = parseEur(r.oem_price);
      if (!c || p == null) continue;
      const prev = by.get(c);
      if (!prev || p < prev.price) {
        by.set(c, { price: p, url: r.url || null, source: `teile-bulk-${gen}` });
      }
    }
  }
  return by;
}

function targetParts(db) {
  return db
    .listParts()
    .filter((p) => p.generation === "981" || p.generation == null)
    .filter((p) => compactOem(p.oem_number));
}

function applyHit(db, part, price, asOf, extraNote) {
  if (isPetkaPriceVerified(part.notes, part.price_note)) return "skip_petka";
  db.updatePartPrices(
    part.id,
    price,
    part.aftermarket_price,
    teileNote(part.price_note),
    asOf,
  );
  if (extraNote && part.notes && !/teile\.com/i.test(part.notes)) {
    /* keep names; prices only */
  }
  return "ok";
}

function applyByCompact(db, compact, price, asOf, stats) {
  const parts = targetParts(db).filter(
    (p) => compactOem(p.oem_number) === compact,
  );
  if (!parts.length) {
    stats.no_row++;
    return;
  }
  for (const p of parts) {
    const r = applyHit(db, p, price, asOf);
    if (r === "skip_petka") stats.skip_petka++;
    else stats.updated++;
  }
}

function knownMissCompacts() {
  if (!fs.existsSync(HITS)) return new Set();
  const j = JSON.parse(fs.readFileSync(HITS, "utf8"));
  const skip = new Set();
  for (const [c, h] of Object.entries(j.by_oem || {})) {
    if (!h || h.err) continue;
    if (h.price == null || Number(h.price) <= 0) skip.add(compactOem(c));
  }
  return skip;
}

function uniqueMissing(db) {
  /** @type {Map<string, { compact: string, oem: string, sku: string, anyMissing: boolean }>} */
  const by = new Map();
  const knownMiss = knownMissCompacts();
  for (const p of targetParts(db)) {
    const c = compactOem(p.oem_number);
    if (!c) continue;
    if (isPetkaPriceVerified(p.notes, p.price_note)) continue;
    if (knownMiss.has(c)) continue;
    const has = p.oem_price != null && Number(p.oem_price) > 0;
    const prev = by.get(c);
    if (!prev) {
      by.set(c, { compact: c, oem: p.oem_number, sku: p.sku, anyMissing: !has });
    } else if (!has) {
      prev.anyMissing = true;
    }
  }
  return [...by.values()].filter((x) => x.anyMissing);
}

function cmdApplyCache() {
  fs.mkdirSync(OUT, { recursive: true });
  const db = new GarageDb(path.join(root, ".local", "garage.db"));
  const cache = loadCachePrices();
  const asOf = new Date().toISOString().slice(0, 10);
  const stats = {
    updated: 0,
    skip_petka: 0,
    no_row: 0,
    cache: cache.size,
    sibling: 0,
    filled_oem: 0,
  };
  const priced = new Map();
  for (const p of targetParts(db)) {
    const c = compactOem(p.oem_number);
    if (!c) continue;
    if (p.oem_price != null && Number(p.oem_price) > 0 && !priced.has(c)) {
      priced.set(c, p.oem_price);
    }
  }
  const seenCache = new Set();
  for (const p of targetParts(db)) {
    if (p.oem_price != null && Number(p.oem_price) > 0) continue;
    const c = compactOem(p.oem_number);
    if (!c) continue;
    const hit = cache.get(c);
    if (hit && !seenCache.has(c)) {
      seenCache.add(c);
      applyByCompact(db, c, hit.price, asOf, stats);
      stats.filled_oem++;
      continue;
    }
    const sib = priced.get(c);
    if (sib != null) {
      applyHit(db, p, sib, asOf);
      stats.sibling++;
      stats.updated++;
    }
  }
  const miss = uniqueMissing(db);
  db.close();
  const report = { ok: true, mode: "cache", ...stats, still_miss: miss.length };
  fs.writeFileSync(
    path.join(OUT, "apply-cache-last.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report, null, 2));
}

function cmdQueue() {
  fs.mkdirSync(OUT, { recursive: true });
  const db = new GarageDb(path.join(root, ".local", "garage.db"));
  const miss = uniqueMissing(db);
  db.close();
  const batch = miss.slice(0, 250).map((x) => x.compact);
  fs.writeFileSync(QUEUE, JSON.stringify({ as_of: new Date().toISOString(), miss }, null, 2));
  fs.writeFileSync(path.join(OUT, "next-batch.json"), JSON.stringify(batch));
  console.log(
    JSON.stringify(
      {
        ok: true,
        queue: miss.length,
        next_batch: batch.length,
        first: batch[0] || null,
        last: batch[batch.length - 1] || null,
        file: QUEUE,
      },
      null,
      2,
    ),
  );
}

function loadHitsFile(file) {
  const j = JSON.parse(fs.readFileSync(file, "utf8"));
  return Array.isArray(j) ? j : j.hits || j.results || [];
}

function cmdApply(file) {
  const db = new GarageDb(path.join(root, ".local", "garage.db"));
  const hits = loadHitsFile(file);
  const asOf = new Date().toISOString().slice(0, 10);
  const stats = {
    updated: 0,
    skip_petka: 0,
    no_row: 0,
    miss: 0,
    hits: 0,
    mismatch: 0,
    sample: [],
  };
  const merged = fs.existsSync(HITS) ? JSON.parse(fs.readFileSync(HITS, "utf8")) : { by_oem: {} };
  merged.by_oem = merged.by_oem || {};
  for (const h of hits) {
    const c = compactOem(h.compact || h.oem || h.priceNumber);
    const price = parseEur(h.price ?? h.priceConsumerBruttoNormalGoogle ?? h.eur);
    const pn = compactOem(h.pn || h.priceNumber);
    if (pn && pn !== c) {
      stats.mismatch++;
      continue;
    }
    merged.by_oem[c] = { ...h, compact: c, price, at: asOf };
    if (price == null) {
      stats.miss++;
      continue;
    }
    stats.hits++;
    const before = stats.updated;
    applyByCompact(db, c, price, asOf, stats);
    if (stats.sample.length < 6 && stats.updated > before) {
      stats.sample.push({
        compact: c,
        eur: price,
        cny: Math.round(price * EUR_CNY * 100) / 100,
        name: h.name || h.productName || null,
      });
    }
  }
  fs.mkdirSync(OUT, { recursive: true });
  merged.updated_at = new Date().toISOString();
  fs.writeFileSync(HITS, JSON.stringify(merged, null, 2));
  const still = uniqueMissing(db);
  const oil = db.getPartBySku("981-9A110722400");
  db.close();
  const report = {
    ok: true,
    mode: "apply",
    file,
    ...stats,
    still_miss: still.length,
    oil_filter: oil
      ? {
          eur: oil.oem_price,
          cny: oil.oem_price != null ? Math.round(oil.oem_price * EUR_CNY * 100) / 100 : null,
          note: oil.price_note,
        }
      : null,
    fx: { EUR: EUR_CNY },
  };
  fs.writeFileSync(path.join(OUT, "apply-last.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}

function cmdStatus() {
  const db = new GarageDb(path.join(root, ".local", "garage.db"));
  const parts = targetParts(db);
  let withP = 0;
  const miss = uniqueMissing(db);
  for (const p of parts) {
    if (p.oem_price != null && Number(p.oem_price) > 0) withP++;
  }
  const oil = db.getPartBySku("981-9A110722400");
  db.close();
  console.log(
    JSON.stringify(
      {
        rows: parts.length,
        with_price: withP,
        unique_miss: miss.length,
        oil_eur: oil?.oem_price,
        oil_cny:
          oil?.oem_price != null
            ? Math.round(oil.oem_price * EUR_CNY * 100) / 100
            : null,
      },
      null,
      2,
    ),
  );
}

const argv = process.argv.slice(2);
if (argv.includes("--apply-cache")) cmdApplyCache();
else if (argv.includes("--queue")) cmdQueue();
else if (argv.includes("--status")) cmdStatus();
else if (argv[0] === "--apply" && argv[1]) cmdApply(argv[1]);
else {
  console.error(
    "usage: --apply-cache | --queue | --status | --apply <hits.json>",
  );
  process.exit(1);
}
