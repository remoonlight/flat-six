/**
 * Design911 名称抓取（Doofinder API，按页批量）。
 * 落盘 `.local/design911-names/981.json`（可断点续跑）。
 *
 * Usage:
 *   node scripts/fetch-design911-names.mjs --gen 981
 *   node scripts/fetch-design911-names.mjs --gen 981 --max-lookup 50
 *   node scripts/fetch-design911-names.mjs --gen 981 --catalog-only
 *   node scripts/fetch-design911-names.mjs --gen 981 --delay-ms 120 --concurrency 4
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const OUT_DIR = path.join(root, ".local", "design911-names");
const HASHID = "a42ceb32760722f5a7f8b75f7dd9fa9d";
const SEARCH =
  "https://eu1-search.doofinder.com/5/search";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = {
    gen: "981",
    delayMs: 350,
    rpp: 50,
    maxPages: 20,
    maxLookup: 0,
    catalogOnly: false,
    concurrency: 4,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--gen") out.gen = argv[++i];
    else if (a === "--delay-ms") out.delayMs = Number(argv[++i]);
    else if (a === "--rpp") out.rpp = Number(argv[++i]);
    else if (a === "--max-pages") out.maxPages = Number(argv[++i]);
    else if (a === "--max-lookup") out.maxLookup = Number(argv[++i]);
    else if (a === "--catalog-only") out.catalogOnly = true;
    else if (a === "--concurrency") out.concurrency = Math.max(1, Number(argv[++i]) || 1);
  }
  return out;
}

function compactOem(oem) {
  return String(oem || "")
    .replace(/[.\s-]/g, "")
    .toUpperCase();
}

function cleanTitle(title) {
  return String(title || "")
    .replace(/\s+/g, " ")
    .replace(/\s*[·•]\s*[0-9A-Z.]+$/i, "")
    .trim();
}

async function doofinderSearch(query, page, rpp) {
  const url = `${SEARCH}?hashid=${HASHID}&query=${encodeURIComponent(query)}&rpp=${rpp}&page=${page}`;
  const r = await fetch(url, {
    headers: {
      Accept: "application/json",
      Origin: "https://www.design911.co.uk",
      Referer: "https://www.design911.co.uk/",
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
    },
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function pickHit(results, wantCompact) {
  const list = Array.isArray(results) ? results : [];
  if (!list.length) return null;
  const exact = list.filter((x) => {
    const g = compactOem(x.gtin || "");
    const id = compactOem(String(x.id || "").split("-").pop() || "");
    return g === wantCompact || id === wantCompact;
  });
  const pool = exact.length ? exact : list;
  const named = pool.find(
    (x) => x.title && !/^Original Porsche Part$/i.test(String(x.title)),
  );
  return named || pool[0] || null;
}

function loadState(gen) {
  const file = path.join(OUT_DIR, `${gen}.json`);
  if (!fs.existsSync(file)) {
    return {
      version: 1,
      generation: gen,
      updated_at: null,
      by_oem: {},
      catalog_queries: {},
      lookups_done: [],
    };
  }
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function saveState(gen, state) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  state.updated_at = new Date().toISOString();
  const file = path.join(OUT_DIR, `${gen}.json`);
  fs.writeFileSync(file, JSON.stringify(state, null, 2) + "\n");
  return file;
}

function ingestResult(state, hit) {
  if (!hit) return;
  const title = cleanTitle(hit.title);
  if (!title || /^Original Porsche Part$/i.test(title)) return;
  const gtin = compactOem(hit.gtin || "");
  const fromId = compactOem(String(hit.id || "").split("-").pop() || "");
  for (const key of [gtin, fromId]) {
    if (!key || key.length < 8) continue;
    if (!/^[0-9A-Z]+$/.test(key)) continue;
    const prev = state.by_oem[key];
    if (prev?.name_en && !/^Original Porsche Part$/i.test(prev.name_en)) continue;
    state.by_oem[key] = {
      name_en: title,
      gtin: hit.gtin || null,
      link: hit.link || null,
      brand: hit.brand || null,
      source: "design911-doofinder",
    };
  }
}

async function dumpCatalog(state, opts) {
  const queries = [
    "Boxster 981",
    "Cayman 981",
    "981 Boxster",
    "981 Cayman",
    "Porsche 981",
  ];
  const catConc = Math.min(2, Math.max(1, opts.concurrency || 1));

  async function fetchQueryPages(q) {
    const donePages = state.catalog_queries[q] || 0;
    for (let page = donePages + 1; page <= opts.maxPages; page++) {
      process.stderr.write(`[catalog] ${q} p${page} …`);
      try {
        const json = await doofinderSearch(q, page, opts.rpp);
        const results = json.results || [];
        console.error(`${results.length} hits (total_found=${json.total_found ?? "?"})`);
        for (const hit of results) ingestResult(state, hit);
        state.catalog_queries[q] = page;
        saveState(opts.gen, state);
        if (results.length === 0) break;
        // Doofinder often caps visible total ~1000
        if (page * opts.rpp >= Math.min(json.total || 1000, 1000)) break;
      } catch (e) {
        console.error(`ERR ${e.message || e}`);
        break;
      }
      await sleep(opts.delayMs);
    }
  }

  // optional: up to 2 catalog queries in parallel
  for (let i = 0; i < queries.length; i += catConc) {
    const batch = queries.slice(i, i + catConc);
    await Promise.all(batch.map((q) => fetchQueryPages(q)));
  }
}

function list981OemsFromDb() {
  const dbPath = path.join(root, ".local", "garage.db");
  const db = new DatabaseSync(dbPath);
  // ensure column exists by opening via no-op; migration is in GarageDb
  try {
    db.exec(`ALTER TABLE parts ADD COLUMN name_en TEXT`);
  } catch {
    /* already */
  }
  const rows = db
    .prepare(
      `SELECT sku, oem_number, name_zh, name_en, generation
       FROM parts
       WHERE generation = '981' AND oem_number IS NOT NULL AND trim(oem_number) != ''`,
    )
    .all();
  db.close();
  return rows;
}

async function lookupMissing(state, opts) {
  const rows = list981OemsFromDb();
  const need = [];
  for (const row of rows) {
    const compact = compactOem(row.oem_number);
    if (!compact) continue;
    const hit = state.by_oem[compact];
    if (hit?.name_en && !/^Original Porsche Part$/i.test(hit.name_en)) continue;
    if (state.lookups_done.includes(compact)) continue;
    need.push({ sku: row.sku, oem: row.oem_number, compact });
  }
  const slice =
    opts.maxLookup > 0 ? need.slice(0, opts.maxLookup) : need;
  const conc = Math.max(1, opts.concurrency || 1);
  console.error(
    `[lookup] need=${need.length} doing=${slice.length} concurrency=${conc}`,
  );

  let next = 0;
  let done = 0;
  const doneSet = new Set(state.lookups_done);

  async function one(item, i) {
    process.stderr.write(`[lookup ${i}/${slice.length}] ${item.oem} …`);
    try {
      const json = await doofinderSearch(item.compact, 1, 20);
      const hit = pickHit(json.results || [], item.compact);
      if (hit) {
        ingestResult(state, hit);
        const title = cleanTitle(hit.title);
        if (title && !/^Original Porsche Part$/i.test(title)) {
          state.by_oem[item.compact] = {
            name_en: title,
            gtin: hit.gtin || item.compact,
            link: hit.link || null,
            brand: hit.brand || null,
            source: "design911-doofinder-lookup",
            sku: item.sku,
          };
          console.error(title.slice(0, 80));
        } else {
          console.error("no-title");
        }
      } else {
        console.error("miss");
      }
    } catch (e) {
      console.error(`ERR ${e.message || e}`);
    }
    if (!doneSet.has(item.compact)) {
      doneSet.add(item.compact);
      state.lookups_done.push(item.compact);
    }
    done++;
    if (done % 20 === 0) saveState(opts.gen, state);
    await sleep(opts.delayMs);
  }

  async function worker() {
    while (true) {
      const i = next++;
      if (i >= slice.length) return;
      await one(slice[i], i + 1);
    }
  }

  await Promise.all(Array.from({ length: conc }, () => worker()));
  saveState(opts.gen, state);
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.gen !== "981" && opts.gen !== "982") {
    throw new Error("--gen must be 981 or 982");
  }
  const state = loadState(opts.gen);
  await dumpCatalog(state, opts);
  if (!opts.catalogOnly) await lookupMissing(state, opts);
  const out = saveState(opts.gen, state);
  const n = Object.keys(state.by_oem).length;
  console.log(
    JSON.stringify(
      {
        ok: true,
        out: path.relative(root, out),
        oem_names: n,
        catalog_queries: state.catalog_queries,
        lookups_done: state.lookups_done.length,
        concurrency: opts.concurrency,
      },
      null,
      2,
    ),
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});