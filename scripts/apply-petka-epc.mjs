/**
 * PETKA 981 EPC 明文 CSV → garage.db
 * 权威：OEM 号 + name_zh / name_en + petka_note / pr_label。不改 oem_price / 副厂价。
 *
 * Usage:
 *   npm run apply:petka-epc
 *   node scripts/apply-petka-epc.mjs --dry-run
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";
import { formatPetkaPrLabel } from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL_DIR = path.join(root, ".local", "petka-epc");
const SEED_DIR = path.join(root, "data", "seed", "petka", "plaintext-archive");
const CSV_NAME = "PETKA_Porsche_981_EPC.csv";
const REPORT = path.join(LOCAL_DIR, "apply-last.json");

const dryRun = process.argv.includes("--dry-run");

function compactOem(oem) {
  return String(oem || "")
    .replace(/[.\s\-_]/g, "")
    .toUpperCase();
}

/** PETKA 空格分组 → 点分，保留原分组（不以 11 位硬切）。 */
function petkaOemCanonical(raw) {
  return String(raw || "")
    .trim()
    .replace(/\s+/g, ".")
    .toUpperCase();
}

function uniqueJoin(vals) {
  const seen = new Set();
  const out = [];
  for (const v of vals) {
    const t = String(v || "").trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out.length ? out.join(" / ") : "";
}

function hasHan(text) {
  return /[\u4e00-\u9fff]/.test(String(text || ""));
}

/** PETKA 常把总成明细/also-use 拼进名称；取冒号前的零件名。 */
function cleanPetkaName(raw) {
  let s = String(raw || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  s = s.replace(/\s*(配合使用|also use)\s*:.*$/i, "");
  s = s.replace(/\s*(由如下组成|comprising)\s*:.*$/i, "");
  s = s.replace(/\s+/g, " ").trim();
  return s.replace(/[:：]\s*$/, "").trim();
}

function namePickScore(text, type, model) {
  const t = String(text || "").trim();
  if (!t) return Number.NEGATIVE_INFINITY;
  let s = 800 - Math.min(t.length, 500);
  if (type === "part") s += 8;
  if (model === "981") s += 4;
  if (hasHan(t)) s += 12;
  return s;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cur = "";
  let q = false;
  const s = String(text).replace(/^\uFEFF/, "");
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '"') {
      if (q && s[i + 1] === '"') {
        cur += '"';
        i++;
        continue;
      }
      q = !q;
      continue;
    }
    if (!q && c === ",") {
      row.push(cur);
      cur = "";
      continue;
    }
    if (!q && (c === "\n" || c === "\r")) {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(cur);
      if (row.some((x) => x.length)) rows.push(row);
      row = [];
      cur = "";
      continue;
    }
    cur += c;
  }
  if (cur.length || row.length) {
    row.push(cur);
    if (row.some((x) => x.length)) rows.push(row);
  }
  return rows;
}

function findCsv() {
  const candidates = [
    path.join(LOCAL_DIR, CSV_NAME),
    path.join(SEED_DIR, CSV_NAME),
    path.join(process.env.USERPROFILE || "", "Desktop", CSV_NAME),
  ];
  for (const p of candidates) {
    if (p && fs.existsSync(p)) return p;
  }
  throw new Error(`missing ${CSV_NAME}`);
}

function loadPetkaParts(csvPath) {
  const rows = parseCsv(fs.readFileSync(csvPath, "utf8"));
  const header = rows[0] || [];
  const idx = Object.fromEntries(header.map((h, i) => [h.trim(), i]));
  const need = [
    "record_type",
    "part_number",
    "name_zh",
    "name_en",
    "hg",
    "vehicle_model",
  ];
  for (const k of need) {
    if (idx[k] == null) throw new Error(`csv missing column ${k}`);
  }
  /** @type {Map<string, Array<{ oem: string, name_zh: string, name_en: string, hg: string, type: string, model: string, note: string, pr_label: string }>>} */
  const groups = new Map();
  let partRows = 0;
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    const type = String(r[idx.record_type] || "").trim();
    if (type !== "part" && type !== "order_form") continue;
    const oem = petkaOemCanonical(r[idx.part_number]);
    const compact = compactOem(oem);
    if (!compact || compact.length < 5) continue;
    if (type === "part") partRows++;
    const rec = {
      oem,
      name_zh: cleanPetkaName(r[idx.name_zh]),
      name_en: cleanPetkaName(r[idx.name_en]),
      hg: String(r[idx.hg] || "").trim(),
      type,
      model: String(r[idx.vehicle_model] || "").trim(),
      note: idx.note != null ? String(r[idx.note] || "").trim() : "",
      pr_label:
        formatPetkaPrLabel(
          idx.model_codes != null ? r[idx.model_codes] : "",
          idx.model_meaning != null ? r[idx.model_meaning] : "",
        ) || "",
    };
    const arr = groups.get(compact) || [];
    arr.push(rec);
    groups.set(compact, arr);
  }

  /** @type {Map<string, { oem: string, name_zh: string, name_en: string, hg: string, petka_note: string, pr_label: string }>} */
  const by = new Map();
  for (const [compact, arr] of groups) {
    const bestZh = arr.reduce(
      (best, cur) =>
        namePickScore(cur.name_zh, cur.type, cur.model) >
        namePickScore(best?.name_zh, best?.type, best?.model)
          ? cur
          : best,
      arr[0],
    );
    const bestEn = arr.reduce(
      (best, cur) =>
        namePickScore(cur.name_en, cur.type, cur.model) >
        namePickScore(best?.name_en, best?.type, best?.model)
          ? cur
          : best,
      arr[0],
    );
    const hgPart = arr.find((x) => x.type === "part" && x.hg);
    by.set(compact, {
      oem: (arr.find((x) => x.type === "part") || arr[0]).oem,
      name_zh: bestZh.name_zh,
      name_en: bestEn.name_en,
      hg: (hgPart || arr[0]).hg,
      petka_note: uniqueJoin(arr.map((x) => x.note)),
      pr_label: uniqueJoin(arr.map((x) => x.pr_label)),
    });
  }
  return { by, partRows, dataRows: rows.length - 1 };
}

function selfcheck(by) {
  const oil = by.get("9A110722400");
  const seal = by.get("99711110731");
  const bolt = by.get("WHT008240");
  const fails = [];
  if (!oil?.name_zh.includes("机油滤芯") || /配合使用/.test(oil.name_zh)) {
    fails.push(`oil zh=${oil?.name_zh}`);
  }
  if (!/^Oil filter insert$/i.test(oil?.name_en || "")) {
    fails.push(`oil en=${oil?.name_en}`);
  }
  if (seal?.name_zh !== "密封件" || seal?.name_en !== "Seal") {
    fails.push(`seal ${seal?.name_zh}/${seal?.name_en}`);
  }
  if (bolt?.name_zh !== "组合螺栓" || bolt?.name_en !== "Bolt with washer") {
    fails.push(`bolt ${bolt?.name_zh}/${bolt?.name_en}`);
  }
  const withNote = [...by.values()].find((r) => /左侧|右侧/.test(r.petka_note));
  if (!withNote) fails.push("no directional petka_note");
  const withPr = [...by.values()].find((r) => r.pr_label);
  if (!withPr) fails.push("no pr_label");
  if (fails.length) throw new Error(`selfcheck failed: ${fails.join(" | ")}`);
}

function main() {
  const csvPath = findCsv();
  const { by, partRows, dataRows } = loadPetkaParts(csvPath);
  selfcheck(by);
  const dbPath = path.join(root, ".local", "garage.db");
  const db = new GarageDb(dbPath);
  const parts = db.listParts();

  /** @type {Map<string, typeof parts>} */
  const dbByCompact = new Map();
  for (const p of parts) {
    const c = compactOem(p.oem_number);
    if (!c) continue;
    const arr = dbByCompact.get(c) || [];
    arr.push(p);
    dbByCompact.set(c, arr);
  }

  const asOf = new Date().toISOString().slice(0, 10);
  let updated = 0;
  let unchanged = 0;
  let inserted = 0;
  let skipped982 = 0;
  const samples = [];

  for (const [compact, rec] of by) {
    const hits = (dbByCompact.get(compact) || []).filter(
      (p) => p.generation === "981" || p.generation == null,
    );
    const only982 = (dbByCompact.get(compact) || []).filter(
      (p) => p.generation === "982",
    );
    if (!hits.length && only982.length) skipped982++;

    if (hits.length) {
      for (const p of hits) {
        const patch = {
          oem_number: rec.oem,
          petka_note: rec.petka_note || null,
          pr_label: rec.pr_label || null,
        };
        if (rec.name_zh) patch.name_zh = rec.name_zh;
        if (rec.name_en) patch.name_en = rec.name_en;
        const sameOem = p.oem_number === rec.oem;
        const sameZh = !rec.name_zh || p.name_zh === rec.name_zh;
        const sameEn = !rec.name_en || p.name_en === rec.name_en;
        const sameNote = (p.petka_note || "") === (rec.petka_note || "");
        const samePr = (p.pr_label || "") === (rec.pr_label || "");
        if (sameOem && sameZh && sameEn && sameNote && samePr) {
          unchanged++;
          continue;
        }
        if (!dryRun) db.updatePartNames(p.sku, patch);
        updated++;
        if (samples.length < 8) {
          samples.push({
            sku: p.sku,
            oem: rec.oem,
            name_zh: rec.name_zh || p.name_zh,
            name_en: rec.name_en || p.name_en,
            petka_note: rec.petka_note || null,
            pr_label: rec.pr_label || null,
          });
        }
      }
      continue;
    }

    const sku = `981-${compact}`;
    if (db.getPartBySku(sku)) {
      unchanged++;
      continue;
    }
    if (!dryRun) {
      const name_zh = rec.name_zh || rec.name_en || sku;
      db.upsertPart({
        sku,
        name_zh,
        oem_number: rec.oem,
        system: rec.hg ? `HG-${rec.hg}` : "PETKA",
        generation: "981",
        interval_km: null,
        interval_months: null,
        oem_price: null,
        aftermarket_price: null,
        price_note: "currency=EUR; petka_verified=0; source=petka-epc",
        price_as_of: asOf,
        locator_hotspot: null,
        notes: "source=petka-epc",
      });
      db.updatePartNames(sku, {
        name_zh,
        name_en: rec.name_en || null,
        oem_number: rec.oem,
        petka_note: rec.petka_note || null,
        pr_label: rec.pr_label || null,
      });
    }
    inserted++;
  }

  const oil = db.getPartBySku("oil-filter") || db.getPartBySku("981-9A110722400");
  const oilBulk = db.getPartBySku("981-9A110722400");
  const report = {
    csv: csvPath,
    dry_run: dryRun,
    csv_data_rows: dataRows,
    csv_part_rows: partRows,
    unique_oem: by.size,
    db_parts_before: parts.length,
    db_parts_after: db.listParts().length,
    updated,
    unchanged,
    inserted,
    skipped_982_only: skipped982,
    oil_filter: oil
      ? {
          sku: oil.sku,
          oem_number: oil.oem_number,
          name_zh: oil.name_zh,
          name_en: oil.name_en,
          oem_price: oil.oem_price,
        }
      : null,
    oil_filter_bulk: oilBulk
      ? {
          sku: oilBulk.sku,
          oem_number: oilBulk.oem_number,
          name_zh: oilBulk.name_zh,
          name_en: oilBulk.name_en,
        }
      : null,
    samples,
  };
  fs.mkdirSync(LOCAL_DIR, { recursive: true });
  if (!dryRun) {
    fs.writeFileSync(REPORT, JSON.stringify(report, null, 2) + "\n");
  }
  db.close();
  console.log(JSON.stringify(report, null, 2));
}

main();
