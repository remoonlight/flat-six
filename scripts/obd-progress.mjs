import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const source = "data/seed/diagnostics/obd-progress.v2.json";
const checklist = JSON.parse(fs.readFileSync(path.join(root, source), "utf8"));
const expected = ["live", "coding", "x431", "adaptation"];
const levels = new Set(["source", "software"]);
function requireThat(condition, message) {
  if (!condition) throw new Error(`OBD progress: ${message}`);
}
requireThat(checklist.schemaVersion === 2 && checklist.policyVersion === "development-48-v2", "unsupported policy");
requireThat(checklist.acceptanceRequirementsPreserved === true && checklist.excludedFromPercent.length === 4,
  "acceptance must be tracked separately, not removed from requirements");
requireThat(/^\d{4}-\d{2}-\d{2}$/.test(checklist.asOf), "missing review date");
requireThat(checklist.categories.length === 4, "four categories required");
const categories = checklist.categories.map((category, index) => {
  requireThat(category.id === expected[index] && category.weightPercent === 25, "category identity/weight changed");
  requireThat(category.milestones.length === 12, `${category.id}: fixed denominator must remain 12`);
  category.milestones.forEach((item, i) => {
    requireThat(item.id === `${category.id}-${String(i + 1).padStart(2, "0")}`, "milestone identity changed");
    requireThat(typeof item.done === "boolean" && levels.has(item.level) && item.criterion?.trim(), `${item.id}: incomplete criterion`);
    requireThat(!item.criterion.includes("验收") && !item.criterion.includes("测试通过"), `${item.id}: acceptance is excluded from development percentage`);
    requireThat(Array.isArray(item.evidence) && (!item.done || item.evidence.length > 0), `${item.id}: passed item lacks references`);
    for (const reference of item.evidence) {
      requireThat(typeof reference.file === "string" && !path.isAbsolute(reference.file), `${item.id}: reference must be relative`);
      const resolved = path.resolve(root, reference.file);
      requireThat(resolved.startsWith(root) && reference.locator?.trim(), `${item.id}: invalid reference`);
      requireThat(fs.readFileSync(resolved, "utf8").includes(reference.locator), `${item.id}: reference locator not found`);
    }
  });
  const completed = category.milestones.filter(item => item.done).length;
  return { id: category.id, label: category.label, completed, total: 12,
    pending: category.milestones.filter(item => !item.done).map(({ id, criterion, level }) => ({ id, criterion, level })) };
});
const completed = categories.reduce((sum, item) => sum + item.completed, 0);
const report = { asOf: checklist.asOf, policyVersion: checklist.policyVersion, source,
  meaning: checklist.meaning, excludedFromPercent: checklist.excludedFromPercent,
  acceptanceRequirementsPreserved: true, scopeChange: checklist.scopeChange, completed, total: 48,
  percent: Number((completed / 48 * 100).toFixed(1)), categories,
  referenceCheck: "reference-presence-only; not a new software, device or vehicle acceptance" };
const args = process.argv.slice(2);
requireThat(args.length === 0 || (args.length === 1 && args[0] === "--json"), "usage: node scripts/obd-progress.mjs [--json]");
if (args[0] === "--json") console.log(JSON.stringify(report, null, 2));
else {
  console.log(`实时 OBD 总进度：${report.percent.toFixed(1)}%（${completed}/48；核对日期 ${report.asOf}）`);
  for (const item of categories) console.log(`${item.label}：${item.completed}/${item.total}`);
  console.log(checklist.meaning);
}
