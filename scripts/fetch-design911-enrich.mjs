/**
 * Enrich teile bulk OEMs with Design911 aftermarket GBP (search by PN).
 * Reads .local/teile-bulk/{gen}/parts.json → writes aftermarket into notes/json.
 *
 * Usage:
 *   node scripts/fetch-design911-enrich.mjs --gen 981 --max 20
 *   node scripts/fetch-design911-enrich.mjs --gen all --max 50
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const TEILE = path.join(root, ".local", "teile-bulk");

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36 Porsche981Garage/0.1";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = { gen: "all", max: 30, delayMs: 500 };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--gen") out.gen = argv[++i];
    else if (argv[i] === "--max") out.max = Number(argv[++i]);
    else if (argv[i] === "--delay-ms") out.delayMs = Number(argv[++i]);
  }
  return out;
}

async function fetchText(url) {
  const r = await fetch(url, {
    headers: {
      "User-Agent": UA,
      Accept: "text/html",
      "Accept-Language": "en-GB,en;q=0.9",
    },
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}

/** Parse first GBP price from Design911 search/product HTML. */
function parseGbp(html) {
  const m =
    html.match(/£\s*([0-9]+(?:\.[0-9]{2})?)/) ||
    html.match(/GBP\s*([0-9]+(?:\.[0-9]{2})?)/i) ||
    html.match(/"price"\s*:\s*"?([0-9]+(?:\.[0-9]{2})?)"?/i);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

async function lookupOem(oemCompact) {
  const q = oemCompact.replace(/\./g, "");
  const url = `https://www.design911.co.uk/search/?search=${encodeURIComponent(q)}`;
  const html = await fetchText(url);
  const price = parseGbp(html);
  const product =
    html.match(/href="(https?:\/\/www\.design911\.co\.uk\/p\/[^"]+)"/i)?.[1] ||
    html.match(/href="(\/p\/[^"]+)"/i)?.[1];
  return {
    oem_compact: oemCompact,
    aftermarket_price: price,
    url: product
      ? product.startsWith("http")
        ? product
        : `https://www.design911.co.uk${product}`
      : url,
  };
}

async function enrichGen(gen, opts) {
  const partsPath = path.join(TEILE, gen, "parts.json");
  if (!fs.existsSync(partsPath)) {
    return { gen, ok: false, error: `missing ${partsPath}` };
  }
  const parts = JSON.parse(fs.readFileSync(partsPath, "utf8"));
  const slice = parts.slice(0, opts.max);
  const enriched = [];
  for (const p of slice) {
    const compact = (p.oem_compact || p.oem_number || "").replace(/\./g, "");
    if (!compact) continue;
    process.stderr.write(`[d911 ${gen}] ${compact} … `);
    try {
      const hit = await lookupOem(compact);
      console.error(hit.aftermarket_price ?? "no-price");
      enriched.push({ ...p, design911: hit });
    } catch (e) {
      console.error(`ERR ${e.message || e}`);
      enriched.push({ ...p, design911: { error: String(e) } });
    }
    await sleep(opts.delayMs);
  }
  const outPath = path.join(TEILE, gen, "design911-enrich.json");
  fs.writeFileSync(outPath, JSON.stringify(enriched, null, 2));
  const withAm = enriched.filter((r) => r.design911?.aftermarket_price != null);
  return {
    gen,
    ok: true,
    looked_up: enriched.length,
    with_aftermarket: withAm.length,
    out: path.relative(root, outPath),
  };
}

const args = parseArgs(process.argv.slice(2));
const gens =
  args.gen === "all" ? ["981", "982"] : [args.gen].filter((g) => g === "981" || g === "982");

const reports = [];
for (const g of gens) reports.push(await enrichGen(g, args));
fs.writeFileSync(
  path.join(TEILE, "design911-enrich-last.json"),
  JSON.stringify({ checked_at: new Date().toISOString(), reports }, null, 2),
);
console.log(JSON.stringify({ ok: reports.every((r) => r.ok), reports }, null, 2));
