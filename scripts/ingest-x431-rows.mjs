/**
 * Read-only ingest: data/seed/x431/plaintext-archive → 981-2014-coding-menu.json
 * Filters: year=2014, function in {设码, 编程, 特殊功能}
 *
 * Override: X431_ROWS_PATH=/abs/path/to/rows.json
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const SRC_REL = path.join(
  "data",
  "seed",
  "x431",
  "plaintext-archive",
  "03-sim-981",
  "981_Boxster981_rows.json",
);
const SRC = process.env.X431_ROWS_PATH || path.join(root, SRC_REL);

const OUT = path.join(root, "data", "seed", "x431", "981-2014-coding-menu.json");
const FUNCS = new Set(["设码", "编程", "特殊功能"]);

if (!fs.existsSync(SRC)) {
  console.error("Source not found:", SRC);
  process.exit(1);
}

const rows = JSON.parse(fs.readFileSync(SRC, "utf8"));
const filtered = rows.filter(
  (r) => String(r.year) === "2014" && FUNCS.has(r.function),
);

/** @type {Map<string, { system: string, items: any[] }>} */
const bySystem = new Map();
for (const r of filtered) {
  const key = r.system;
  if (!bySystem.has(key)) bySystem.set(key, { system: key, items: [] });
  bySystem.get(key).items.push({
    function: r.function,
    subFunction: r.subFunction ?? null,
    rowId: r.ROW_ID,
    ed: r.ed,
    playbook: {
      risk:
        r.function === "设码" || r.function === "编程"
          ? "high"
          : "medium",
      steps: [
        "在 X431 选择：保时捷 → Cayman/Boxster（981）→ 年款 2014 → 对应系统",
        `进入功能：${r.function}${r.subFunction ? " / " + r.subFunction : ""}`,
        "先读取/截图当前值作为 before",
        "按需修改后再次读取作为 after",
        "回到本应用保存 before/after 快照（本应用不写车）",
      ],
      x431Path: `保时捷 / Boxster(981) / 2014 / ${r.system} / ${r.function}`,
    },
  });
}

const systems = [...bySystem.values()].sort((a, b) =>
  a.system.localeCompare(b.system, "zh"),
);

const payload = {
  source: process.env.X431_ROWS_PATH || SRC_REL.replace(/\\/g, "/"),
  year: "2014",
  model: "Boxster(981)",
  generatedAt: new Date().toISOString(),
  rowCount: filtered.length,
  systemCount: systems.length,
  systems,
};

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(payload, null, 2), "utf8");
console.log(
  `Wrote ${OUT} — ${filtered.length} rows, ${systems.length} systems`,
);
