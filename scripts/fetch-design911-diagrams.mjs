/**
 * Download Design911 981 exploded diagram JPGs from a local manifest.
 * Images are publicly fetchable; HTML listing needs browser (Cloudflare).
 *
 * Usage:
 *   node scripts/fetch-design911-diagrams.mjs
 *   node scripts/fetch-design911-diagrams.mjs --only 981   # skip 981sp
 *   node scripts/fetch-design911-diagrams.mjs --only 981sp
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const OUT = path.join(root, ".local", "design911-981-diagrams");
const IMG_BASE = "https://www.design911.com";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = { only: "all", delayMs: 120, concurrency: 4 };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--only") out.only = argv[++i];
    else if (argv[i] === "--delay-ms") out.delayMs = Number(argv[++i]);
    else if (argv[i] === "--concurrency") out.concurrency = Number(argv[++i]);
  }
  return out;
}

function absImg(img) {
  if (!img) return null;
  const clean = img.replace(/\?.*$/, "");
  return clean.startsWith("http") ? clean : `${IMG_BASE}${clean}`;
}

function fileKey(item) {
  const name = path.basename(absImg(item.img) || "") || `d-${item.href.match(/\/d\/(\d+)/)?.[1] || "x"}.jpg`;
  return name;
}

async function downloadOne(item, destDir) {
  const url = absImg(item.img);
  if (!url) return { ok: false, error: "no img", item };
  const name = fileKey(item);
  const dest = path.join(destDir, name);
  if (fs.existsSync(dest) && fs.statSync(dest).size > 1000) {
    return { ok: true, skipped: true, bytes: fs.statSync(dest).size, dest, url };
  }
  const r = await fetch(url, {
    headers: {
      "User-Agent": UA,
      Accept: "image/jpeg,image/*,*/*",
      Referer: item.href || "https://www.design911.com/diagrams/",
    },
  });
  if (!r.ok) return { ok: false, error: `HTTP ${r.status}`, url, item };
  const buf = Buffer.from(await r.arrayBuffer());
  fs.writeFileSync(dest, buf);
  return { ok: true, bytes: buf.length, dest, url };
}

async function mapPool(items, concurrency, fn) {
  const results = [];
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      results[idx] = await fn(items[idx], idx);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  return results;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const manifestPath = path.join(OUT, "manifest.json");
  if (!fs.existsSync(manifestPath)) {
    console.error("missing", manifestPath, "— crawl via browser first");
    process.exit(1);
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  let items = manifest.items || [];
  if (opts.only === "981") {
    items = items.filter((it) => /\/981_/.test(it.img) || (/981 Boxster/.test(it.title) && !/SP|Spyder/i.test(it.title)));
  } else if (opts.only === "981sp") {
    items = items.filter((it) => /981sp_|981\.SP|Spyder/i.test(it.img + " " + it.title));
  }

  const imgDir = path.join(OUT, "images");
  fs.mkdirSync(imgDir, { recursive: true });

  console.error(`[d911] downloading ${items.length} diagrams → ${imgDir}`);
  const report = await mapPool(items, opts.concurrency, async (item, idx) => {
    try {
      const res = await downloadOne(item, imgDir);
      if (opts.delayMs) await sleep(opts.delayMs);
      if ((idx + 1) % 25 === 0 || idx === 0) {
        console.error(`[d911] ${idx + 1}/${items.length} ${res.ok ? (res.skipped ? "skip" : "ok") : "FAIL"} ${fileKey(item)}`);
      }
      return { ...res, title: item.title, href: item.href, cat: item.cat };
    } catch (e) {
      return { ok: false, error: String(e), item };
    }
  });

  const ok = report.filter((r) => r.ok);
  const fail = report.filter((r) => !r.ok);
  const skipped = report.filter((r) => r.skipped);
  const summary = {
    finished_at: new Date().toISOString(),
    requested: items.length,
    ok: ok.length,
    skipped: skipped.length,
    failed: fail.length,
    bytes: ok.reduce((s, r) => s + (r.bytes || 0), 0),
    failures: fail.slice(0, 30),
  };
  fs.writeFileSync(path.join(OUT, "download-report.json"), JSON.stringify(summary, null, 2));
  console.error(`[d911] done ok=${summary.ok} skip=${summary.skipped} fail=${summary.failed} bytes=${summary.bytes}`);
  if (fail.length) process.exitCode = 2;
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
