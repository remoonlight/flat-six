import { formatPetkaPrLabel } from "./model-oem-links.js";

export type PetkaEpcRecord = {
  oem: string;
  name_zh: string;
  name_en: string;
  hg: string;
  petka_note: string;
  pr_label: string;
};

export type ParsePetkaEpcResult = {
  byCompact: Map<string, PetkaEpcRecord>;
  partRows: number;
  dataRows: number;
};

export function compactOem(oem: string | null | undefined): string {
  return String(oem || "")
    .replace(/[.\s\-_]/g, "")
    .toUpperCase();
}

/** PETKA 空格分组 → 点分，保留原分组（不以 11 位硬切）。 */
export function petkaOemCanonical(raw: string | null | undefined): string {
  return String(raw || "")
    .trim()
    .replace(/\s+/g, ".")
    .toUpperCase();
}

function uniqueJoin(vals: string[]): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of vals) {
    const t = String(v || "").trim();
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out.length ? out.join(" / ") : "";
}

function hasHan(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(String(text || ""));
}

/** PETKA 常把总成明细/also-use 拼进名称；取冒号前的零件名。 */
export function cleanPetkaName(raw: string | null | undefined): string {
  let s = String(raw || "")
    .replace(/\s+/g, " ")
    .trim();
  if (!s) return "";
  s = s.replace(/\s*(配合使用|also use)\s*:.*$/i, "");
  s = s.replace(/\s*(由如下组成|comprising)\s*:.*$/i, "");
  s = s.replace(/\s+/g, " ").trim();
  return s.replace(/[:：]\s*$/, "").trim();
}

function namePickScore(text: string, type: string, model: string): number {
  const t = String(text || "").trim();
  if (!t) return Number.NEGATIVE_INFINITY;
  let s = 800 - Math.min(t.length, 500);
  if (type === "part") s += 8;
  if (model === "981") s += 4;
  if (hasHan(t)) s += 12;
  return s;
}

export function parseQuotedCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let q = false;
  const s = String(text).replace(/^\uFEFF/, "");
  for (let i = 0; i < s.length; i++) {
    const c = s[i]!;
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

type EpcRaw = {
  oem: string;
  name_zh: string;
  name_en: string;
  hg: string;
  type: string;
  model: string;
  note: string;
  pr_label: string;
};

export function parsePetkaEpcCsv(text: string): ParsePetkaEpcResult {
  const rows = parseQuotedCsv(text);
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
  const groups = new Map<string, EpcRaw[]>();
  let partRows = 0;
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i]!;
    const type = String(r[idx.record_type] || "").trim();
    if (type !== "part" && type !== "order_form") continue;
    const oem = petkaOemCanonical(r[idx.part_number]);
    const compact = compactOem(oem);
    if (!compact || compact.length < 5) continue;
    if (type === "part") partRows++;
    const rec: EpcRaw = {
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

  const byCompact = new Map<string, PetkaEpcRecord>();
  for (const [compact, arr] of groups) {
    const bestZh = arr.reduce(
      (best, cur) =>
        namePickScore(cur.name_zh, cur.type, cur.model) >
        namePickScore(best.name_zh, best.type, best.model)
          ? cur
          : best,
      arr[0]!,
    );
    const bestEn = arr.reduce(
      (best, cur) =>
        namePickScore(cur.name_en, cur.type, cur.model) >
        namePickScore(best.name_en, best.type, best.model)
          ? cur
          : best,
      arr[0]!,
    );
    const hgPart = arr.find((x) => x.type === "part" && x.hg);
    byCompact.set(compact, {
      oem: (arr.find((x) => x.type === "part") || arr[0]!).oem,
      name_zh: bestZh.name_zh,
      name_en: bestEn.name_en,
      hg: (hgPart || arr[0]!).hg,
      petka_note: uniqueJoin(arr.map((x) => x.note)),
      pr_label: uniqueJoin(arr.map((x) => x.pr_label)),
    });
  }
  return { byCompact, partRows, dataRows: Math.max(0, rows.length - 1) };
}

export function assertPetkaEpcFixtures(
  byCompact: Map<string, PetkaEpcRecord>,
): void {
  const oil = byCompact.get("9A110722400");
  const seal = byCompact.get("99711110731");
  const bolt = byCompact.get("WHT008240");
  const fails: string[] = [];
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
  if (fails.length) throw new Error(`epc fixtures failed: ${fails.join(" | ")}`);
}
