/** All project docs, including historical files, must stay outside Git distribution. */
import assert from "node:assert/strict";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = path.resolve(import.meta.dirname, "..");
const indexed = execFileSync("git", ["ls-files", "-z", "--", "docs"], { cwd: root, encoding: "utf8" });
assert.equal(indexed, "", "docs/: tracked documents must be removed from the Git index");
const probes = [
  "docs/README.md",
  "docs/requirements.md",
  "docs/project-results.md",
  "docs/adr/001-no-ecu-write.md",
  "docs/research/can-code/x431-dme-bench-candidate.json",
  "docs/history/old-report.md",
  "docs/new-directory/future-document.md",
];
for (const rel of probes) {
  const ignored = execFileSync("git", ["check-ignore", "--no-index", "--", rel], { cwd: root, encoding: "utf8" }).trim();
  assert.equal(ignored, rel, `${rel}: project documentation must remain ignored`);
}
console.log(`PASS local-only docs: no indexed files; all ${probes.length} current, historical and future paths ignored`);
