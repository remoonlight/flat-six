/**
 * Design911 → 981 补充（语义）：
 * A) 缓存里有、车库库没有的 OEM 插入 parts（无 OEM 价）
 * B) 副厂 = 同 OEM 在 Design911 的**平替品牌**（Mahle/Mann/…），不是 /parts/{oem}/ 原厂件页价
 *
 * OEM 价权威：teile / PETKA（多为 EUR）。Design911 原厂件 GBP **不写** oem_price / aftermarket_price。
 *
 * Usage:
 *   node scripts/apply-design911-supplement.mjs --gen 981 --insert-only
 *   node scripts/apply-design911-supplement.mjs --gen 981 --prices --max 50
 *   node scripts/apply-design911-supplement.mjs --gen 981 --prices --concurrency 2
 *
 * 平替缓存：`.local/design911-names/{gen}-alts.json`
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { GarageDb } from "../packages/db/dist/index.js";
import { bulkPartSku } from "../packages/domain/dist/petka-csv.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const OUT_DIR = path.join(root, ".local", "design911-names");
const HASHID = "a42ceb32760722f5a7f8b75f7dd9fa9d";
const SEARCH = "https://eu1-search.doofinder.com/5/search";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const flags = new Set(argv.filter((a) => a.startsWith("--")));
  const out = {
    gen: "981",
    insert: true,
    prices: true,
    max: 0,
    delayMs: 280,
    concurrency: 2,
    headed: false,
    oem: "",
  };
  if (flags.has("--insert-only") && !flags.has("--prices")) {
    out.insert = true;
    out.prices = false;
  } else if (flags.has("--prices") && !flags.has("--insert-only")) {
    out.insert = false;
    out.prices = true;
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--gen") out.gen = argv[++i];
    else if (a === "--max") out.max = Number(argv[++i]);
    else if (a === "--delay-ms") out.delayMs = Number(argv[++i]);
    else if (a === "--concurrency")
      out.concurrency = Math.max(1, Number(argv[++i]) || 1);
    else if (a === "--headed") out.headed = true;
    else if (a === "--oem") out.oem = String(argv[++i] || "");
  }
  return out;
}

function compactOem(oem) {
  return String(oem || "")
    .replace(/[.\s-]/g, "")
    .toUpperCase();
}

function toDottedOem(compact) {
  const c = compactOem(compact);
  if (/^[0-9A-Z]{11}$/.test(c)) {
    return `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6, 9)}.${c.slice(9)}`;
  }
  if (/^[0-9A-Z]{10}$/.test(c)) {
    return `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6, 8)}.${c.slice(8)}`;
  }
  if (/^[0-9A-Z]{9}$/.test(c)) {
    return `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6)}`;
  }
  return c;
}

function looksPorscheOem(k) {
  if (!k || k.length < 8 || k.length > 20) return false;
  if (!/^[0-9A-Z]+$/.test(k)) return false;
  if (/BRE$|DOT$/.test(k)) return false;
  return /^(9A[0-9]|98[12]|99[167]|997|996|970|999|958|000|0PB|1J0|N0|WHT|06[EFH]|8E0|4H0)/.test(
    k,
  );
}

function collectMissingFromCache(gen, dbOems) {
  const file = path.join(OUT_DIR, `${gen}.json`);
  if (!fs.existsSync(file)) throw new Error(`missing ${file}`);
  const state = JSON.parse(fs.readFileSync(file, "utf8"));
  const by = state.by_oem || {};
  /** @type {Map<string, { name_en: string, link: string|null }>} */
  const miss = new Map();
  for (const [k, v] of Object.entries(by)) {
    const g = compactOem(v.gtin || k);
    if (!looksPorscheOem(g)) continue;
    if (dbOems.has(g)) continue;
    const name = String(v.name_en || "").trim();
    if (!name || /^Original Porsche Part$/i.test(name)) continue;
    const prev = miss.get(g);
    if (prev && !/^Original Porsche Part$/i.test(prev.name_en)) continue;
    miss.set(g, { name_en: name, link: v.link || null });
  }
  return miss;
}

function insertMissing(db, gen, missing) {
  let inserted = 0;
  let skipped = 0;
  const asOf = new Date().toISOString().slice(0, 10);
  for (const [compact, meta] of missing) {
    const sku = bulkPartSku(/** @type {"981"|"982"} */ (gen), compact);
    if (db.getPartBySku(sku)) {
      skipped++;
      continue;
    }
    db.upsertPart({
      sku,
      name_zh: meta.name_en,
      oem_number: toDottedOem(compact),
      system: "Design911",
      generation: gen,
      interval_km: null,
      interval_months: null,
      oem_price: null,
      aftermarket_price: null,
      price_note: "currency=EUR; design911=pending",
      price_as_of: asOf,
      locator_hotspot: null,
      notes: meta.link
        ? `design911-catalog:${meta.link}`
        : "source=design911-catalog",
    });
    db.updatePartNames(sku, {
      name_en: meta.name_en,
      name_zh: meta.name_en,
    });
    inserted++;
  }
  return { inserted, skipped, candidates: missing.size };
}

function loadAltState(gen) {
  const file = path.join(OUT_DIR, `${gen}-alts.json`);
  if (!fs.existsSync(file)) {
    return { version: 2, generation: gen, by_oem: {}, done: [] };
  }
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function saveAltState(gen, state) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  state.updated_at = new Date().toISOString();
  fs.writeFileSync(
    path.join(OUT_DIR, `${gen}-alts.json`),
    JSON.stringify(state, null, 2) + "\n",
  );
}

function parsePriceFromHtml(html) {
  const m =
    html.match(
      /itemprop=["']price["'][^>]*content=["']([0-9]+(?:\.[0-9]+)?)["']/i,
    ) ||
    html.match(
      /content=["']([0-9]+(?:\.[0-9]+)?)["'][^>]*itemprop=["']price["']/i,
    );
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function isOemPorscheHit(hit, wantCompact) {
  const title = String(hit.title || "");
  const brand = String(hit.brand || "").trim();
  const gtin = compactOem(hit.gtin || "");
  if (/^Original Porsche Part$/i.test(title)) return true;
  if (/^porsche$/i.test(brand) && gtin === wantCompact) return true;
  if (String(hit.link || "").startsWith("/parts/") && gtin === wantCompact) {
    return true;
  }
  return false;
}

function isAltBrandHit(hit, wantCompact) {
  if (isOemPorscheHit(hit, wantCompact)) return false;
  const brand = String(hit.brand || "").trim();
  if (!brand) return false;
  if (/^porsche$/i.test(brand)) return false;
  const title = String(hit.title || "");
  if (/^Original Porsche Part$/i.test(title)) return false;
  const desc = Array.isArray(hit.description)
    ? hit.description.filter(Boolean).join(" ")
    : String(hit.description || "");
  const blob =
    `${title} ${desc} ${hit.gtin || ""} ${hit.link || ""}`.toUpperCase();
  return blob.includes(wantCompact);
}

async function doofinderSearch(query) {
  const url = `${SEARCH}?hashid=${HASHID}&query=${encodeURIComponent(query)}&rpp=20&page=1`;
  const r = await fetch(url, {
    headers: {
      Accept: "application/json",
      Origin: "https://www.design911.co.uk",
      Referer: "https://www.design911.co.uk/",
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
    },
  });
  if (!r.ok) throw new Error(`Doofinder HTTP ${r.status}`);
  return r.json();
}

function absDesign911Link(link) {
  if (!link) return null;
  if (link.startsWith("http")) return link;
  return `https://www.design911.co.uk${link}`;
}

/**
 * Keep price_note currency (teile EUR / PETKA GBP); only flip design911 flag.
 */
function noteWithAlts(existing) {
  const cur =
    String(existing || "").match(/currency=([A-Z]{3})/i)?.[1]?.toUpperCase() ||
    "EUR";
  const rest = String(existing || "")
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
    .filter((s) => !/^currency=/i.test(s) && !/^design911=/i.test(s));
  return [`currency=${cur}`, "design911=alts", ...rest].join("; ");
}

function applyAlts(db, part, quotes, asOf) {
  if (!quotes.length) return;
  const amPrice = Math.min(...quotes.map((q) => q.price));
  db.updatePartPrices(
    part.id,
    part.oem_price,
    amPrice,
    noteWithAlts(part.price_note),
    asOf,
    quotes,
  );
}

async function fetchAltPrices(db, gen, opts) {
  const state = loadAltState(gen);
  const done = new Set(state.done || []);
  const parts = db
    .listParts()
    .filter((p) => p.generation === gen && p.oem_number);

  let fromCache = 0;
  const need = [];
  for (const p of parts) {
    const c = compactOem(p.oem_number);
    if (!c) continue;
    const hasRealAlts =
      p.aftermarket_quotes?.some(
        (q) => q.brand && !/^Design911$/i.test(q.brand),
      ) ?? false;
    if (hasRealAlts) continue;

    const cached = state.by_oem[c];
    if (cached?.quotes?.length) {
      applyAlts(db, p, cached.quotes, cached.as_of || null);
      fromCache++;
      continue;
    }
    if (done.has(c) && cached?.miss) continue;
    if (opts.oem && c !== compactOem(opts.oem)) continue;
    need.push({ sku: p.sku, oem: p.oem_number, compact: c });
  }

  const slice = opts.max > 0 ? need.slice(0, opts.max) : need;
  console.error(
    `[alts] needFetch=${need.length} doing=${slice.length} appliedCache=${fromCache}`,
  );
  if (slice.length === 0) {
    return { looked: 0, withAlts: 0, miss: 0, fromCache };
  }

  const browser = await chromium.launch({
    headless: !opts.headed,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    locale: "en-GB",
  });
  const page = await context.newPage();
  await page.goto("https://www.design911.co.uk/", {
    waitUntil: "domcontentloaded",
    timeout: 120000,
  });
  await sleep(2500);
  try {
    await page
      .getByRole("button", { name: /allow all/i })
      .click({ timeout: 5000 });
  } catch {
    /* */
  }

  let next = 0;
  let withAlts = 0;
  let miss = 0;
  let looked = 0;
  const asOf = new Date().toISOString().slice(0, 10);

  async function priceUrl(url) {
    const html = await page.evaluate(async (u) => {
      const r = await fetch(u, { credentials: "include" });
      if (!r.ok) return JSON.stringify({ __err: r.status });
      return r.text();
    }, url);
    if (html.startsWith('{"__err"')) return null;
    return parsePriceFromHtml(html);
  }

  async function one(item, i) {
    looked++;
    process.stderr.write(`[alt ${i}/${slice.length}] ${item.oem} …`);
    try {
      const json = await doofinderSearch(item.compact);
      const hits = (json.results || []).filter((h) =>
        isAltBrandHit(h, item.compact),
      );
      /** @type {{ brand: string, price: number }[]} */
      const quotes = [];
      const seenBrand = new Set();
      for (const h of hits.slice(0, 6)) {
        const brand = String(h.brand).trim();
        if (seenBrand.has(brand.toLowerCase())) continue;
        const link = absDesign911Link(h.link);
        if (!link || link.includes("/parts/")) continue; // skip OEM part hub
        const price = await priceUrl(link);
        await sleep(80);
        if (price == null) continue;
        seenBrand.add(brand.toLowerCase());
        quotes.push({ brand, price });
      }
      if (quotes.length) {
        state.by_oem[item.compact] = { quotes, as_of: asOf };
        const part = db.getPartBySku(item.sku);
        if (part) applyAlts(db, part, quotes, asOf);
        withAlts++;
        console.error(
          quotes.map((q) => `${q.brand}£${q.price}`).join(" · "),
        );
      } else {
        state.by_oem[item.compact] = { miss: true, as_of: asOf };
        miss++;
        console.error("no-alt");
      }
    } catch (e) {
      console.error(`ERR ${e.message || e}`);
      state.by_oem[item.compact] = {
        miss: true,
        error: String(e.message || e),
        at: asOf,
      };
      miss++;
    }
    if (!done.has(item.compact)) {
      done.add(item.compact);
      state.done.push(item.compact);
    }
    if (looked % 15 === 0) saveAltState(gen, state);
    await sleep(opts.delayMs);
  }

  async function worker() {
    while (true) {
      const i = next++;
      if (i >= slice.length) return;
      await one(slice[i], i + 1);
    }
  }

  // Doofinder 可并行；页内 fetch 共用一 cookie，concurrency 宜小
  const conc = Math.min(opts.concurrency, 2);
  await Promise.all(Array.from({ length: conc }, () => worker()));
  saveAltState(gen, state);
  await browser.close();
  return { looked, withAlts, miss, fromCache };
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.gen !== "981" && opts.gen !== "982") {
    throw new Error("--gen must be 981 or 982");
  }
  const db = new GarageDb(path.join(root, ".local", "garage.db"));
  const report = { gen: opts.gen, insert: null, alts: null };

  if (opts.insert) {
    const dbOems = new Set(
      db
        .listParts()
        .filter((p) => p.generation === opts.gen)
        .map((p) => compactOem(p.oem_number)),
    );
    const missing = collectMissingFromCache(opts.gen, dbOems);
    report.insert = insertMissing(db, opts.gen, missing);
    console.error(
      `[insert] candidates=${report.insert.candidates} inserted=${report.insert.inserted} skipped=${report.insert.skipped}`,
    );
  }

  if (opts.prices) {
    report.alts = await fetchAltPrices(db, opts.gen, opts);
  }

  const rows = db.listParts().filter((p) => p.generation === opts.gen);
  const withAm = rows.filter(
    (p) =>
      p.aftermarket_quotes?.some((q) => !/^Design911$/i.test(q.brand)) ||
      (p.aftermarket_price != null && Number(p.aftermarket_price) > 0),
  ).length;
  console.log(
    JSON.stringify(
      {
        ok: true,
        ...report,
        total_gen: rows.length,
        with_alt_quotes: withAm,
      },
      null,
      2,
    ),
  );
  db.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
