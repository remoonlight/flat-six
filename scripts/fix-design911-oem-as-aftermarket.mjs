/**
 * 纠正：把误写入的 Design911「原厂件页」价从 aftermarket_* 清掉；
 * OEM 币种以 teile CSV（EUR）/ 原语义恢复，不以 Design911 GBP 覆盖。
 *
 * Usage: node scripts/fix-design911-oem-as-aftermarket.mjs --gen 981
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

function parseArgs(argv) {
  const out = { gen: "981", dry: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--gen") out.gen = argv[++i];
    else if (argv[i] === "--dry") out.dry = true;
  }
  return out;
}

function loadTeileCurrency(gen) {
  const csv = path.join(root, ".local", "teile-bulk", gen, "parts.csv");
  /** @type {Map<string, string>} */
  const map = new Map();
  if (!fs.existsSync(csv)) return map;
  const lines = fs.readFileSync(csv, "utf8").split(/\r?\n/);
  for (const line of lines.slice(1)) {
    if (!line.trim()) continue;
    // notes 含逗号，只取首列 sku；teile 快照一律 EUR + design911=no
    const sku = line.split(",", 1)[0]?.trim();
    if (!sku || sku === "sku") continue;
    map.set(sku, "currency=EUR; design911=no");
  }
  return map;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const teileNote = loadTeileCurrency(opts.gen);
  const dbPath = path.join(root, ".local", "garage.db");
  const db = new DatabaseSync(dbPath);

  const rows = db
    .prepare(
      `SELECT id, sku, system, oem_price, aftermarket_price, price_note, notes, aftermarket_quotes
       FROM parts WHERE generation = ?`,
    )
    .all(opts.gen);

  let cleared = 0;
  let notesFixed = 0;
  const upd = db.prepare(
    `UPDATE parts SET aftermarket_price = ?, aftermarket_quotes = ?, price_note = ? WHERE id = ?`,
  );

  db.exec("BEGIN");
  try {
    for (const r of rows) {
      const quotes = String(r.aftermarket_quotes || "");
      const note = String(r.price_note || "");
      const badAm =
        r.aftermarket_price != null ||
        /"brand"\s*:\s*"Design911"/i.test(quotes) ||
        /design911=yes/i.test(note);
      if (!badAm) continue;

      let nextNote = note;
      if (teileNote.has(r.sku)) {
        nextNote = teileNote.get(r.sku);
      } else if (r.system === "Design911" && r.oem_price == null) {
        nextNote = "currency=GBP; design911=pending";
      } else if (/teile\.com|PETKA bulk/i.test(String(r.notes || ""))) {
        nextNote = "currency=EUR; design911=no";
      } else if (/currency=GBP/i.test(note) && !/teile/i.test(String(r.notes || ""))) {
        // 保养子集等本机 GBP：清副厂误标，保留 GBP，design911=no
        nextNote = "currency=GBP; design911=no";
      } else if (/design911=yes|design911=pending/i.test(note)) {
        nextNote = "currency=EUR; design911=no";
      }

      if (!opts.dry) {
        upd.run(null, null, nextNote, r.id);
      }
      cleared++;
      if (nextNote !== note) notesFixed++;
    }
    if (!opts.dry) db.exec("COMMIT");
    else db.exec("ROLLBACK");
  } catch (e) {
    db.exec("ROLLBACK");
    throw e;
  }

  const left = db
    .prepare(
      `SELECT COUNT(*) AS c FROM parts WHERE generation = ? AND aftermarket_price IS NOT NULL`,
    )
    .get(opts.gen).c;
  const eur = db
    .prepare(
      `SELECT COUNT(*) AS c FROM parts WHERE generation = ? AND price_note LIKE 'currency=EUR%'`,
    )
    .get(opts.gen).c;

  console.log(
    JSON.stringify(
      {
        ok: true,
        dry: opts.dry,
        cleared_am_rows: cleared,
        notes_rewritten: notesFixed,
        teile_sku_map: teileNote.size,
        remaining_with_am: left,
        currency_eur_rows: eur,
      },
      null,
      2,
    ),
  );
  db.close();
}

main();
