/** Summarize plaintext menu evidence; source labels never qualify ECU compatibility. */
import fs from "node:fs";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const items = new Map();
const sources = [];
for (const generation of ["981", "982"]) {
  const source = `data/seed/x431/plaintext-archive/03-sim-981/${generation}_Boxster${generation}_rows.json`;
  sources.push(source);
  for (const row of JSON.parse(fs.readFileSync(path.join(root, source), "utf8"))) {
    if (row.chassis !== generation || !["设码", "编程", "特殊功能"].includes(row.function)) continue;
    const key = JSON.stringify([row.system, row.function, row.subFunction ?? null]);
    const item = items.get(key) ?? { id: `menu-${items.size + 1}`, system: row.system, function: row.function, subFunction: row.subFunction ?? null, evidence: [] };
    let evidence = item.evidence.find((e) => e.generation === generation);
    if (!evidence) { evidence = { generation, source, years: [], rowIds: [] }; item.evidence.push(evidence); }
    if (!evidence.years.includes(row.year)) evidence.years.push(row.year);
    evidence.rowIds.push(row.ROW_ID);
    items.set(key, item);
  }
}
fs.writeFileSync(path.join(root, "data/seed/coding-guide/workspace-menu.json"), JSON.stringify({ schemaVersion: 1, note: "源菜单标签，年款和具体 ECU 适用性未核实；不包含车辆请求。", sources, items: [...items.values()] }, null, 2) + "\n");
console.log(`Summarized ${items.size} menu entries from 981/982 plaintext evidence.`);
