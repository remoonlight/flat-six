/** Core docs must be distributable without local evidence, devices or credentials. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = path.resolve(import.meta.dirname, "..");
const indexOnly = process.argv.slice(2).includes("--index");
const ignore = fs.readFileSync(path.join(root, ".gitignore"), "utf8");
const core = [...ignore.matchAll(/^!\/(docs\/[^\n]+\.md)$/gm)].map((m) => m[1]);
assert.equal(core.length, 9, "core docs allowlist changed: update the reviewed distribution scope");
const indexed = new Set(execFileSync("git", ["ls-files"], { cwd: root, encoding: "utf8" }).trim().split(/\r?\n/));
for (const rel of core) assert.ok(indexed.has(rel), `${rel}: core document is not in the Git index`);
const available = indexed;
const clean = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-core-docs-"));
let links = 0;
try {
  for (const rel of core) {
    const original = path.join(root, rel);
    const body = indexOnly
      ? execFileSync("git", ["show", `:${rel}`], { cwd: root, encoding: "utf8" })
      : fs.readFileSync(original, "utf8");
    const destination = path.join(clean, rel);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.writeFileSync(destination, body);
    const checks = [
      [/(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b/, "private network address"],
      [/[A-Z]:[\\/]Users[\\/](?!%)[^\s`]+/i, "private user path"],
      [/\b(?:[A-F0-9]{2}[:-]){5}[A-F0-9]{2}\b/i, "private device MAC"],
      [/(?:MAC|序列号|serial\s+)[\s:=`]*[A-F0-9]{6,}\b/i, "private device identity"],
      [/-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----/, "private key"],
    ];
    // Public source URLs may contain numeric identifiers, not private environment data.
    const privateText = body.replace(/https?:\/\/[^\s)<>]+/g, "<public-source>");
    for (const [pattern, reason] of checks) assert.ok(!pattern.test(privateText), `${rel}: ${reason}`);
    const prose = body.replace(/```[\s\S]*?```/g, "");
    for (const match of prose.matchAll(/!?\[[^\]\n]*\]\(([^)\n]+)\)/g)) {
      const target = match[1].split(' "')[0].replace(/^<|>$/g, "");
      if (target.startsWith("#") || /^[a-z][\w+.-]*:/i.test(target)) continue;
      const local = decodeURIComponent(target.split("#")[0]);
      const resolved = path.resolve(path.dirname(original), local);
      const relTarget = path.relative(root, resolved).replaceAll(path.sep, "/");
      const existsInDistribution = available.has(relTarget) || [...available].some((p) => p.startsWith(relTarget.replace(/\/$/, "") + "/"));
      assert.ok(existsInDistribution, `${rel}: link depends on local-only/unindexed file ${target}`);
      assert.ok(fs.existsSync(resolved), `${rel}: missing local target ${target}`);
      links += 1;
    }
  }
  // In the isolated export, all authority entrypoints exist without .local/.
  for (const rel of core) assert.ok(fs.statSync(path.join(clean, rel)).size > 0);
  assert.equal(fs.existsSync(path.join(clean, ".local")), false);
  console.log(`PASS core docs (${indexOnly ? "Git index" : "working tree"}): ${core.length} allowlisted Markdown files, ${links} distributable file links, private-data scan and isolated authority export`);
} finally {
  for (const rel of core) {
    const target = path.join(clean, rel);
    if (fs.existsSync(target)) fs.unlinkSync(target);
  }
  for (const dir of ["docs/adr", "docs"]) {
    const target = path.join(clean, dir);
    if (fs.existsSync(target)) fs.rmdirSync(target);
  }
  fs.rmdirSync(clean);
}
