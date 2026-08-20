/**
 * Run all accept scripts sequentially; print PASS/FAIL matrix.
 * Builds domain+db once, then runs each accept via node (no per-suite rebuild).
 * Core: interval, fault, mileage, locator, wiring, coding, obd-phase1, ploc2.
 * Extra (existing): p0, p123, bridge - also run; any failure => exit 1.
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const npm = process.platform === "win32" ? "npm.cmd" : "npm";

const jobs = [
  { id: "interval", kind: "core", script: "scripts/interval-accept.mjs" },
  { id: "fault", kind: "core", script: "scripts/fault-accept.mjs" },
  { id: "mileage", kind: "core", script: "scripts/mileage-accept.mjs" },
  { id: "locator", kind: "core", script: "scripts/locator-accept.mjs" },
  { id: "wiring", kind: "core", script: "scripts/wiring-accept.mjs" },
  { id: "coding", kind: "core", script: "scripts/coding-accept.mjs" },
  { id: "obd-phase1", kind: "core", script: "scripts/obd-phase1-accept.mjs" },
  { id: "ploc2", kind: "core", script: "scripts/ploc2-accept.mjs" },
  { id: "p0", kind: "extra", script: "scripts/p0-accept.mjs" },
  { id: "p123", kind: "extra", script: "scripts/p123-accept.mjs" },
  { id: "bridge", kind: "extra", script: "scripts/bridge-lifecycle-accept.mjs" },
];

function npmRun(args) {
  const t0 = Date.now();
  const r = spawnSync(npm, args, {
    cwd: root,
    encoding: "utf8",
    shell: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: process.env,
  });
  return {
    status: r.status ?? 1,
    ms: Date.now() - t0,
    stdout: r.stdout || "",
    stderr: r.stderr || "",
  };
}

function nodeRun(rel) {
  const t0 = Date.now();
  const r = spawnSync(process.execPath, [path.join(root, rel)], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: process.env,
  });
  return {
    status: r.status ?? 1,
    ms: Date.now() - t0,
    stdout: r.stdout || "",
    stderr: r.stderr || "",
  };
}

function writeOut(out) {
  if (out.stdout) process.stdout.write(out.stdout);
  if (out.stderr) process.stderr.write(out.stderr);
}

process.stdout.write("\n======== build domain+db (once) ========\n");
const buildDomain = npmRun(["run", "build", "-w", "@porsche981/domain"]);
writeOut(buildDomain);
if (buildDomain.status !== 0) {
  process.stderr.write("FAIL: domain build\n");
  process.exit(1);
}
const buildDb = npmRun(["run", "build", "-w", "@porsche981/db"]);
writeOut(buildDb);
if (buildDb.status !== 0) {
  process.stderr.write("FAIL: db build\n");
  process.exit(1);
}
process.stdout.write(
  `-> build OK (domain ${(buildDomain.ms / 1000).toFixed(1)}s, db ${(buildDb.ms / 1000).toFixed(1)}s)\n`,
);

const results = [];

for (const job of jobs) {
  process.stdout.write("\n======== accept:" + job.id + " (" + job.kind + ") ========\n");
  const out = nodeRun(job.script);
  writeOut(out);
  const pass = out.status === 0;
  results.push({
    id: job.id,
    kind: job.kind,
    pass,
    status: out.status,
    ms: out.ms,
  });
  process.stdout.write(
    "-> " + (pass ? "PASS" : "FAIL") + " (exit " + out.status + ", " + (out.ms / 1000).toFixed(1) + "s)\n",
  );
}

const idW = Math.max(...results.map((r) => r.id.length), 8);
const kindW = Math.max(...results.map((r) => r.kind.length), 4);

process.stdout.write("\n======== ACCEPT MATRIX ========\n");
process.stdout.write("suite".padEnd(idW) + "  " + "kind".padEnd(kindW) + "  result  time\n");
process.stdout.write("-".repeat(idW) + "  " + "-".repeat(kindW) + "  ------  ----\n");
for (const r of results) {
  const mark = r.pass ? "PASS" : "FAIL";
  process.stdout.write(
    r.id.padEnd(idW) + "  " + r.kind.padEnd(kindW) + "  " + mark.padEnd(6) + "  " + (r.ms / 1000).toFixed(1) + "s\n",
  );
}
const failed = results.filter((r) => !r.pass);
process.stdout.write(
  "\n" +
    (results.length - failed.length) +
    "/" +
    results.length +
    " passed" +
    (failed.length ? " - failed: " + failed.map((f) => f.id).join(", ") : "") +
    "\n",
);

process.exit(failed.length ? 1 : 0);
