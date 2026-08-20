/**
 * Crawl teile.com Boxster 981 / 982-718 catalog.
 *
 * NOTE: bare `fetch()` from Node often gets HTTP 403. Working path used 2026-08-02:
 * open teile model page in Cursor browser → Runtime.evaluate same-origin fetch all
 * diagram pages → merge via scripts/merge-teile-cdp.mjs → ingest:petka --file
 *
 * Usage (when IP not blocked):
 *   node scripts/fetch-teile-bulk.mjs --gen 981 --max-diagrams 2
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bulkRowsToPetkaCsv } from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const OUT = path.join(root, ".local", "teile-bulk");

const MODELS = {
  "981": {
    generation: "981",
    index: "https://www.teile.com/en/porsche-parts-shop/model-Boxster-981/30",
    prefix: "/en/porsche-parts-shop/model-Boxster-981/30/",
  },
  "982": {
    generation: "982",
    index: "https://www.teile.com/en/porsche-parts-shop/model-Boxster-982-718/36",
    prefix: "/en/porsche-parts-shop/model-Boxster-982-718/36/",
  },
};

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36 Porsche981Garage/0.1";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = {
    gen: "all",
    maxDiagrams: Infinity,
    delayMs: 350,
  };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--gen") out.gen = argv[++i];
    else if (argv[i] === "--max-diagrams") out.maxDiagrams = Number(argv[++i]);
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
    redirect: "follow",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return r.text();
}

function abs(href) {
  return href.startsWith("http") ? href : `https://www.teile.com${href}`;
}

/** Diagram pages: …/System/Diagram-name/NN (7 path segments). */
function extractDiagramUrls(html, prefix) {
  const re = new RegExp(
    `href="(${prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^"#?]+)"`,
    "gi",
  );
  const out = new Set();
  for (const m of html.matchAll(re)) {
    const href = m[1].replace(/&amp;/g, "&");
    const segs = href.split("/").filter(Boolean);
    if (segs.length === 7 && /^\d+$/.test(segs[6])) out.add(abs(href));
  }
  return [...out];
}

function formatOem(compact) {
  const c = compact.replace(/[.\s]/g, "").toUpperCase();
  // 9A110705013 → 9A1.107.050.13 ; 00004330522 → 000.043.305.22
  if (/^[0-9A-Z]{11}$/.test(c)) {
    return `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6, 9)}.${c.slice(9)}`;
  }
  if (/^[0-9A-Z]{12,14}$/.test(c)) {
    return `${c.slice(0, 3)}.${c.slice(3, 6)}.${c.slice(6, 9)}.${c.slice(9)}`;
  }
  return compact.toUpperCase();
}

/**
 * Parse product cards from diagram HTML.
 * Card text pattern: Name … PN … gross: 950,60 €
 */
function parseDiagramProducts(html, pageUrl) {
  const rows = [];
  const seen = new Set();

  // Split on product hrefs that end with /digits
  const linkRe =
    /href="([^"]+\/\d+)"[^>]*>([^<]{0,120})<\/a>[\s\S]{0,1200}?([0-9A-Z]{9,14})\s*(?:Year:[\s\S]{0,200}?)?gross:\s*([0-9]+,[0-9]{2})\s*€/gi;

  for (const m of html.matchAll(linkRe)) {
    const href = m[1];
    const name = (m[2] || "").replace(/\s+/g, " ").trim() || m[3];
    const oemCompact = m[3].toUpperCase();
    const price = Number(m[4].replace(",", "."));
    const key = oemCompact;
    if (seen.has(key)) continue;
    if (!/porsche-parts-shop/i.test(href)) continue;
    seen.add(key);
    rows.push({
      oem_number: formatOem(oemCompact),
      oem_compact: oemCompact,
      name,
      oem_price: price,
      url: abs(href),
      pageUrl,
    });
  }

  // Fallback: looser PN + gross in window
  if (rows.length === 0) {
    for (const m of html.matchAll(
      /([0-9A-Z]{9,14})\s*Year:[\s\S]{0,120}?gross:\s*([0-9]+,[0-9]{2})\s*€/gi,
    )) {
      const oemCompact = m[1].toUpperCase();
      if (seen.has(oemCompact)) continue;
      seen.add(oemCompact);
      rows.push({
        oem_number: formatOem(oemCompact),
        oem_compact: oemCompact,
        name: oemCompact,
        oem_price: Number(m[2].replace(",", ".")),
        url: pageUrl,
        pageUrl,
      });
    }
  }

  return rows;
}

function toBulkTxt(rows) {
  const lines = ["Pos\tPart No.\tDescription\tQty\tPrice EUR"];
  rows.forEach((r, i) => {
    lines.push(
      `${String(i + 1).padStart(3, "0")}\t${r.oem_number}\t${r.name}\t1\t${r.oem_price ?? ""}`,
    );
  });
  return lines.join("\n") + "\n";
}

function toIngestCsv(rows, generation) {
  const bulkRows = rows.map((r) => ({
    oem_number: r.oem_number,
    name: r.name,
    oem_price: r.oem_price,
  }));
  let csv = bulkRowsToPetkaCsv(bulkRows, {
    generation,
    system: "bulk",
    zone: "bulk",
    nameLang: "en",
  });
  // currency GBP → EUR; strip PETKA价; add 非PETKA
  const out = [];
  for (const [idx, line] of csv.split("\n").entries()) {
    if (idx === 0 || !line.trim()) {
      out.push(line);
      continue;
    }
    const cells = [];
    let cur = "";
    let q = false;
    for (const ch of line) {
      if (ch === '"') {
        q = !q;
        cur += ch;
        continue;
      }
      if (ch === "," && !q) {
        cells.push(cur);
        cur = "";
        continue;
      }
      cur += ch;
    }
    cells.push(cur);
    if (cells[6] === "GBP") cells[6] = "EUR";
    if (cells[14] != null) {
      let n = cells[14].replace(/^"|"$/g, "");
      n = n.replace(/PETKA\s*价;?\s*/gi, "");
      n = `非PETKA价，teile.com 快照，待复核；${n}`;
      cells[14] = `"${n.replace(/"/g, '""')}"`;
    }
    out.push(cells.join(","));
  }
  return out.join("\n");
}

async function crawlGen(gen, opts) {
  const cfg = MODELS[gen];
  const dir = path.join(OUT, gen);
  fs.mkdirSync(dir, { recursive: true });

  console.error(`[teile ${gen}] index ${cfg.index}`);
  const indexHtml = await fetchText(cfg.index);
  fs.writeFileSync(path.join(dir, "_index.html"), indexHtml);
  await sleep(opts.delayMs);

  let diagrams = extractDiagramUrls(indexHtml, cfg.prefix);
  console.error(`[teile ${gen}] diagrams ${diagrams.length}`);
  diagrams = diagrams.slice(0, opts.maxDiagrams);

  const all = [];
  const seen = new Set();
  const report = { gen, diagrams: diagrams.length, pages: [] };

  for (const url of diagrams) {
    const slug = url.split("/").slice(-2).join("/");
    process.stderr.write(`[teile ${gen}] ${slug} … `);
    let html;
    try {
      html = await fetchText(url);
    } catch (e) {
      console.error(`ERR ${e.message || e}`);
      report.pages.push({ url, error: String(e) });
      continue;
    }
    await sleep(opts.delayMs);
    const rows = parseDiagramProducts(html, url);
    let added = 0;
    for (const r of rows) {
      const key = r.oem_compact || r.oem_number.replace(/\./g, "");
      if (seen.has(key)) continue;
      seen.add(key);
      all.push({ ...r, diagram: slug });
      added++;
    }
    console.error(`+${added} (total ${all.length})`);
    report.pages.push({ url, parsed: rows.length, added });
  }

  fs.writeFileSync(path.join(dir, "parts.json"), JSON.stringify(all, null, 2));
  fs.writeFileSync(path.join(dir, "teile-dump.txt"), toBulkTxt(all));
  fs.writeFileSync(path.join(dir, "parts.csv"), "\uFEFF" + toIngestCsv(all, gen));

  const bulkDir = path.join(root, ".local", "petka-bulk", gen);
  fs.mkdirSync(bulkDir, { recursive: true });
  fs.writeFileSync(path.join(bulkDir, "teile-all.txt"), toBulkTxt(all));

  report.total = all.length;
  report.with_price = all.filter((r) => r.oem_price != null).length;
  report.out = {
    json: path.relative(root, path.join(dir, "parts.json")),
    csv: path.relative(root, path.join(dir, "parts.csv")),
  };
  return report;
}

const args = parseArgs(process.argv.slice(2));
const gens =
  args.gen === "all" ? ["981", "982"] : [args.gen].filter((g) => MODELS[g]);
if (!gens.length) {
  console.error("use --gen 981|982|all");
  process.exit(1);
}

fs.mkdirSync(OUT, { recursive: true });
const reports = [];
for (const g of gens) reports.push(await crawlGen(g, args));
fs.writeFileSync(
  path.join(OUT, "fetch-last.json"),
  JSON.stringify({ checked_at: new Date().toISOString(), reports }, null, 2),
);
console.log(JSON.stringify({ ok: true, reports }, null, 2));
