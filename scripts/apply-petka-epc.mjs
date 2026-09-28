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
import {
  assertPetkaEpcFixtures,
  compactOem,
  parsePetkaEpcCsv,
} from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL_DIR = path.join(root, ".local", "petka-epc");
const SEED_DIR = path.join(root, "data", "seed", "petka", "plaintext-archive");
const CSV_NAME = "PETKA_Porsche_981_EPC.csv";
const REPORT = path.join(LOCAL_DIR, "apply-last.json");

const dryRun = process.argv.includes("--dry-run");

function findCsv() {
  const candidates = [
    path.join(SEED_DIR, CSV_NAME),
    path.join(LOCAL_DIR, CSV_NAME),
  ];
  for (const p of candidates) {
    if (p && fs.existsSync(p)) return p;
  }
  throw new Error(`missing ${CSV_NAME}`);
}

function main() {
  const csvPath = findCsv();
  const { byCompact, partRows, dataRows } = parsePetkaEpcCsv(
    fs.readFileSync(csvPath, "utf8"),
  );
  assertPetkaEpcFixtures(byCompact);
  const dbPath = path.join(root, ".local", "garage.db");
  const db = new GarageDb(dbPath);
  const parts = db.listParts();

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

  for (const [compact, rec] of byCompact) {
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
    unique_oem: byCompact.size,
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
