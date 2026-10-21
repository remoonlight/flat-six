/** Docs, hidden files/directories and local status/instructions stay outside Git. */
import assert from "node:assert/strict";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = path.resolve(import.meta.dirname, "..");
const indexed = execFileSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" }).split("\0").filter(Boolean);
const excluded = indexed.filter((rel) => rel.startsWith("docs/")
  || rel.split("/").some((part) => part.startsWith("."))
  || ["agents.md", "obd_status.md"].includes(rel.toLowerCase()));
assert.deepEqual(excluded, [], "local-only paths must be removed from the Git index");
const probes = [
  "docs/README.md",
  "docs/requirements.md",
  "docs/project-results.md",
  "docs/adr/001-no-ecu-write.md",
  "docs/research/can-code/x431-dme-bench-candidate.json",
  "docs/history/old-report.md",
  "docs/new-directory/future-document.md",
  ".gitignore",
  ".gitattributes",
  ".agents/skills/example/SKILL.md",
  ".codex/config.toml",
  ".local/garage-models/boxster-primary.glb",
  "data/petka/brakes/.gitkeep",
  "nested/.future-file",
  "nested/.future-directory/file.txt",
  "AGENTS.md",
  "agents.md",
  "OBD_STATUS.md",
  "obd_status.md",
];
for (const rel of probes) {
  const ignored = execFileSync("git", ["check-ignore", "--no-index", "--", rel], { cwd: root, encoding: "utf8" }).trim();
  assert.equal(ignored, rel, `${rel}: local-only files must remain ignored`);
}
console.log(`PASS local-only exclusions: no docs, hidden paths or root instructions/status indexed; all ${probes.length} current, historical and future paths ignored`);
