import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bulkRowsToPetkaCsv } from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const files = process.argv.slice(2);
if (files.length < 2) {
  console.error("usage: node merge-teile-cdp.mjs <gen> <cdp.json>...");
  process.exit(1);
}
const gen = files[0];
const parts = [];
for (const f of files.slice(1)) {
  const j = JSON.parse(fs.readFileSync(f, "utf8"));
  const arr = JSON.parse(j.result.value);
  parts.push(...arr);
}

function toIngestCsv(rows, generation) {
  let csv = bulkRowsToPetkaCsv(
    rows.map((r) => ({
      oem_number: r.oem_number,
      name: r.name,
      oem_price: r.oem_price,
    })),
    { generation, system: "bulk", zone: "bulk", nameLang: "en" },
  );
  return csv
    .split("\n")
    .map((line, idx) => {
      if (idx === 0 || !line.trim()) return line;
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
      return cells.join(",");
    })
    .join("\n");
}

const dir = path.join(root, ".local", "teile-bulk", gen);
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, "parts.json"), JSON.stringify(parts, null, 2));
const lines = ["Pos\tPart No.\tDescription\tQty\tPrice EUR"];
parts.forEach((r, i) => {
  lines.push(
    `${String(i + 1).padStart(3, "0")}\t${r.oem_number}\t${r.name}\t1\t${r.oem_price ?? ""}`,
  );
});
fs.writeFileSync(path.join(dir, "teile-dump.txt"), lines.join("\n") + "\n");
fs.writeFileSync(path.join(dir, "parts.csv"), "\uFEFF" + toIngestCsv(parts, gen));
const bulkDir = path.join(root, ".local", "petka-bulk", gen);
fs.mkdirSync(bulkDir, { recursive: true });
fs.writeFileSync(path.join(bulkDir, "teile-all.txt"), lines.join("\n") + "\n");
console.log(
  JSON.stringify({
    gen,
    total: parts.length,
    with_price: parts.filter((p) => p.oem_price != null).length,
  }),
);
