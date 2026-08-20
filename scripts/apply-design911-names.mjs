/**
 * 浠?`.local/design911-names/{gen}.json` 鍐欏叆 garage.db锛? * - name_en = Design911 鑻辨枃鏍囬
 * - name_zh = 淇濈暀宸叉湁涓枃锛涘崰浣嶅悕鍒欐満缈昏嫳鏂囷紙MyMemory锛? *
 * Usage:
 *   node scripts/apply-design911-names.mjs --gen 981
 *   node scripts/apply-design911-names.mjs --gen 981 --no-translate
 *   node scripts/apply-design911-names.mjs --gen 981 --max-translate 100
 *   node scripts/apply-design911-names.mjs --gen 981 --delay-ms 100 --concurrency 6
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const OUT_DIR = path.join(root, ".local", "design911-names");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = {
    gen: "981",
    translate: true,
    maxTranslate: 0,
    delayMs: 400,
    concurrency: 4,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--gen") out.gen = argv[++i];
    else if (a === "--no-translate") out.translate = false;
    else if (a === "--max-translate") out.maxTranslate = Number(argv[++i]);
    else if (a === "--delay-ms") out.delayMs = Number(argv[++i]);
    else if (a === "--concurrency")
      out.concurrency = Math.max(1, Number(argv[++i]) || 1);
  }
  return out;
}

function compactOem(oem) {
  return String(oem || "")
    .replace(/[.\s-]/g, "")
    .toUpperCase();
}

function hasHan(text) {
  return /[\u4e00-\u9fff]/.test(String(text || ""));
}

function isPlaceholderName(name, oem) {
  const n = String(name || "").trim();
  if (!n) return true;
  if (hasHan(n)) return false;
  const o = compactOem(oem);
  if (!o) return true;
  return compactOem(n) === o;
}

async function translateEnToZh(text) {
  void text;
  const err = new Error(
    "MyMemory disabled — use scripts/ai-part-names-zh.mjs / ai-part-names-zh-rules.cjs",
  );
  err.rateLimited = true;
  throw err;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const file = path.join(OUT_DIR, `${opts.gen}.json`);
  if (!fs.existsSync(file)) throw new Error(`missing ${file}`);
  const state = JSON.parse(fs.readFileSync(file, "utf8"));
  const byOem = state.by_oem || {};

  const dbPath = path.join(root, ".local", "garage.db");
  const db = new GarageDb(dbPath);
  const parts = db.listParts().filter((p) => p.generation === opts.gen);

  let enSet = 0;
  let zhSet = 0;
  let zhSkipped = 0;
  let translateTried = 0;
  let rateLimited = 0;
  const jobs = [];

  for (const p of parts) {
    const compact = compactOem(p.oem_number);
    if (!compact) continue;
    const hit = byOem[compact];
    if (!hit?.name_en) continue;

    db.updatePartNames(p.sku, { name_en: hit.name_en });
    enSet++;

    // skip existing Chinese name_zh
    if (!isPlaceholderName(p.name_zh, p.oem_number)) {
      zhSkipped++;
      continue;
    }
    if (!opts.translate) continue;
    jobs.push({ sku: p.sku, compact, name_en: hit.name_en });
  }

  const slice =
    opts.maxTranslate > 0 ? jobs.slice(0, opts.maxTranslate) : jobs;
  const conc = Math.max(1, opts.concurrency || 1);
  if (opts.translate) {
    console.error(
      `[zh] need=${jobs.length} doing=${slice.length} concurrency=${conc} delayMs=${opts.delayMs}`,
    );
  }

  let next = 0;
  async function one(job, i) {
    translateTried++;
    process.stderr.write("[zh " + i + "/" + slice.length + "] " + job.compact + " ...");
    try {
      const { zh, rateLimited: rl } = await translateEnToZh(job.name_en);
      if (rl) {
        rateLimited++;
        console.error("rate-limit");
      } else if (zh && hasHan(zh)) {
        db.updatePartNames(job.sku, { name_zh: zh });
        zhSet++;
        console.error(zh.slice(0, 60));
      } else {
        console.error("no-zh");
      }
    } catch (e) {
      if (e.rateLimited) rateLimited++;
      console.error("ERR " + (e.message || e));
    }
    await sleep(opts.delayMs);
  }

  async function worker() {
    while (true) {
      const i = next++;
      if (i >= slice.length) return;
      await one(slice[i], i + 1);
    }
  }

  if (opts.translate && slice.length) {
    await Promise.all(Array.from({ length: conc }, () => worker()));
  }

  db.close();
  console.log(
    JSON.stringify(
      {
        ok: true,
        gen: opts.gen,
        en_set: enSet,
        zh_set: zhSet,
        zh_kept_existing: zhSkipped,
        translate_tried: translateTried,
        rate_limited: rateLimited,
        concurrency: conc,
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