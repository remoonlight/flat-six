/**
 * 将 AI 产出的中文零件名写入 garage.db（不调用任何在线翻译 API）。
 *
 * Usage:
 *   node scripts/ai-part-names-zh.mjs --list
 *   node scripts/ai-part-names-zh.mjs --apply .local/design911-names/zh-ai-batches/batch-001.json
 *   node scripts/ai-part-names-zh.mjs --stats
 *
 * batch JSON: [{ "sku": "...", "name_zh": "..." }, ...]
 * 或 { "by_sku": { "sku": "中文名" } }
 * 或按英文词干 { "by_en": { "Engine radiator": "发动机散热器" } }（匹配 stem 后的 name_en）
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const PROG = path.join(root, ".local", "design911-names", "zh-ai-progress.json");
const BATCH_DIR = path.join(root, ".local", "design911-names", "zh-ai-batches");

const hasHan = (t) => /[\u4e00-\u9fff]/.test(String(t || ""));
const compactOem = (oem) =>
  String(oem || "")
    .replace(/[.\s-]/g, "")
    .toUpperCase();

function isPlaceholderName(name, oem) {
  const n = String(name || "").trim();
  if (!n) return true;
  if (hasHan(n)) return false;
  const o = compactOem(oem);
  if (!o) return true;
  return compactOem(n) === o;
}

/** 去掉车型罗列，便于 by_en 匹配 */
export function stemEn(en) {
  return String(en || "")
    .replace(/\s*[.\-]?\s*Porsche\b.*/i, "")
    .replace(/\s+for\s+Porsche\b.*/i, "")
    .replace(/\s*[-–—]\s*Several Applications.*/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function loadProgress() {
  if (!fs.existsSync(PROG)) return { applied: {}, batches: [] };
  return JSON.parse(fs.readFileSync(PROG, "utf8"));
}

function saveProgress(p) {
  fs.mkdirSync(path.dirname(PROG), { recursive: true });
  fs.writeFileSync(PROG, JSON.stringify(p, null, 2));
}

function listPending(db, gen = "981") {
  return db
    .listParts()
    .filter(
      (p) =>
        p.generation === gen &&
        p.name_en?.trim() &&
        isPlaceholderName(p.name_zh, p.oem_number),
    );
}

function parseArgs(argv) {
  const out = { cmd: "stats", file: null, gen: "981", dry: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--list") out.cmd = "list";
    else if (a === "--stats") out.cmd = "stats";
    else if (a === "--apply") {
      out.cmd = "apply";
      out.file = argv[++i];
    } else if (a === "--gen") out.gen = argv[++i];
    else if (a === "--dry") out.dry = true;
  }
  return out;
}

function normalizeBatch(raw) {
  /** @type {{ sku?: string, name_zh: string, en?: string }[]} */
  const rows = [];
  if (Array.isArray(raw)) {
    for (const r of raw) {
      if (r?.sku && r?.name_zh) rows.push({ sku: r.sku, name_zh: r.name_zh });
      else if (r?.en && r?.name_zh) rows.push({ en: r.en, name_zh: r.name_zh });
    }
  } else if (raw?.by_sku && typeof raw.by_sku === "object") {
    for (const [sku, name_zh] of Object.entries(raw.by_sku)) {
      rows.push({ sku, name_zh });
    }
  } else if (raw?.by_en && typeof raw.by_en === "object") {
    for (const [en, name_zh] of Object.entries(raw.by_en)) {
      rows.push({ en, name_zh });
    }
  }
  return rows;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const dbPath = path.join(root, ".local", "garage.db");
  const db = new GarageDb(dbPath);
  const all = db.listParts().filter((p) => p.generation === opts.gen);
  const pending = listPending(db, opts.gen);
  const withHan = all.filter((p) => hasHan(p.name_zh)).length;

  if (opts.cmd === "stats" || opts.cmd === "list") {
    const payload = {
      gen: opts.gen,
      parts: all.length,
      with_name_en: all.filter((p) => p.name_en?.trim()).length,
      name_zh_han: withHan,
      pending_translate: pending.length,
    };
    console.log(JSON.stringify(payload, null, 2));
    if (opts.cmd === "list") {
      fs.mkdirSync(BATCH_DIR, { recursive: true });
      const out = path.join(
        root,
        ".local",
        "design911-names",
        "zh-ai-pending.json",
      );
      fs.writeFileSync(
        out,
        JSON.stringify(
          pending.map((p) => ({
            sku: p.sku,
            oem: p.oem_number,
            en: p.name_en,
            zh: p.name_zh,
          })),
        ),
      );
      console.error(`wrote ${out} (${pending.length})`);
    }
    db.close();
    return;
  }

  if (opts.cmd === "apply") {
    if (!opts.file) throw new Error("--apply requires file");
    const abs = path.resolve(opts.file);
    const raw = JSON.parse(fs.readFileSync(abs, "utf8"));
    const rows = normalizeBatch(raw);
    const bySku = new Map();
    const byEn = new Map();
    for (const r of rows) {
      if (r.sku) bySku.set(r.sku, String(r.name_zh).trim());
      if (r.en) byEn.set(stemEn(r.en).toLowerCase(), String(r.name_zh).trim());
    }

    let updated = 0;
    let skippedHan = 0;
    let skippedMissing = 0;
    let skippedNoMap = 0;

    const progress = loadProgress();
    const touch = [];

    for (const p of pending) {
      let zh = bySku.get(p.sku);
      if (!zh && p.name_en) {
        zh = byEn.get(stemEn(p.name_en).toLowerCase());
      }
      if (!zh || !hasHan(zh)) {
        skippedNoMap++;
        continue;
      }
      if (hasHan(p.name_zh) && !isPlaceholderName(p.name_zh, p.oem_number)) {
        skippedHan++;
        continue;
      }
      if (!opts.dry) {
        db.updatePartNames(p.sku, { name_zh: zh });
        progress.applied[p.sku] = {
          name_zh: zh,
          at: new Date().toISOString(),
          batch: path.basename(abs),
        };
      }
      touch.push({ sku: p.sku, name_zh: zh });
      updated++;
    }

    if (!opts.dry) {
      progress.batches = progress.batches || [];
      progress.batches.push({
        file: path.basename(abs),
        updated,
        at: new Date().toISOString(),
      });
      saveProgress(progress);
    }

    // recount
    const after = db.listParts().filter((p) => p.generation === opts.gen);
    const han = after.filter((p) => hasHan(p.name_zh)).length;
    const still = listPending(db, opts.gen).length;

    console.log(
      JSON.stringify(
        {
          ok: true,
          dry: opts.dry,
          file: path.basename(abs),
          map_rows: rows.length,
          updated,
          skipped_han: skippedHan,
          skipped_no_map: skippedNoMap,
          skipped_missing: skippedMissing,
          name_zh_han: han,
          pending_left: still,
          sample: touch.slice(0, 5),
        },
        null,
        2,
      ),
    );
    db.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
