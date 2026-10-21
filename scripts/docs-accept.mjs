/** Docs and hidden metadata stay local; reviewed model assets and prices are shared. */
import assert from "node:assert/strict";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

const root = path.resolve(import.meta.dirname, "..");
const indexed = execFileSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8" }).split("\0").filter(Boolean);
const isSharedModel = (rel) => (rel.startsWith(".local/flat-six/") || rel.startsWith(".local/petka-models/"))
  && !rel.split("/").slice(1).some((part) => part.startsWith("."))
  && (/\.(glb|json)$/.test(rel) || rel === ".local/flat-six/NOTICE-source.md")
  || [".local/garage-models/boxster-primary.glb", ".local/garage-models/boxster-primary.manifest.json"].includes(rel);
const excluded = indexed.filter((rel) => rel.startsWith("docs/")
  || (rel.split("/").some((part) => part.startsWith(".")) && !isSharedModel(rel))
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
  ".local/garage-models/other-model.glb",
  ".local/flat-six/unreviewed-model.glb",
  ".local/flat-six/.private-record.json",
  ".local/garage.db",
  ".local/cms-rip/private-model.glb",
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
const shared = [
  ".local/flat-six/boxster-real.glb",
  ".local/petka-models/010-000.glb",
  ".local/garage-models/boxster-primary.glb",
  ".local/garage-models/boxster-primary.manifest.json",
  "data/petka/brakes/parts.csv",
  "data/petka/chassis/parts.csv",
  "data/petka/engine-bay/parts.csv",
  "data/seed/parts/catalog/981.csv",
  "data/seed/parts/catalog/982.csv",
  "data/seed/parts/catalog/teile-oem-exact.json",
  "data/seed/fx.json",
];
for (const rel of shared) {
  assert.ok(indexed.includes(rel), `${rel}: shared model/price file must be indexed`);
  const result = spawnSync("git", ["check-ignore", "--quiet", "--no-index", "--", rel], { cwd: root });
  assert.equal(result.status, 1, `${rel}: shared model/price file must not be ignored`);
}
console.log(`PASS distribution scope: ${probes.length} local-only paths ignored; ${indexed.filter(isSharedModel).length} shared model files indexed; ${shared.length} representative model/price files indexed and allowed`);
