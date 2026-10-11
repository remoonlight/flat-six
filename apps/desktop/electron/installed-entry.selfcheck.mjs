import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { installedAppPackage, verifyInstalledEntry, INSTALLED_ENTRY } from "./installed-entry.mjs";

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-installed-entry-"));
const root = path.join(scratch, "app"); fs.mkdirSync(root);
const packageFile = path.join(root, "package.json"), correct = installedAppPackage();
const reset = () => fs.writeFileSync(packageFile, JSON.stringify(correct));
try {
  for (const relative of [INSTALLED_ENTRY, "apps/desktop/electron/installed-entry.mjs", "apps/desktop/electron/main.mjs",
    "apps/desktop/electron/preload.cjs", "apps/desktop/dist/index.html"]) {
    const file = path.join(root, relative); fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, "SYNTHETIC static layout only");
  }
  reset(); const result = verifyInstalledEntry(root);
  assert.equal(result.entry, INSTALLED_ENTRY); assert.equal(result.files.length, 6);
  const manifest = { files: result.files };
  assert.deepEqual(verifyInstalledEntry(root, manifest).files, result.files);
  // The actual regression: a perfectly hashable development package has no installed main.
  const development = { name: "porsche981", version: "0.1.0", workspaces: ["apps/*", "packages/*"], scripts: { dev: "npm run dev" } };
  for (const bad of [development, { ...correct, main: "apps/desktop/electron/main.mjs" },
    { ...correct, main: "../elsewhere.cjs" }, { ...correct, type: undefined }, { ...correct, scripts: {} }, { ...correct, workspaces: [] }]) {
    fs.writeFileSync(packageFile, JSON.stringify(bad)); assert.throws(() => verifyInstalledEntry(root), /installed-entry-package-invalid/);
  }
  reset();
  assert.throws(() => verifyInstalledEntry(root, { files: [...manifest.files, manifest.files[0]] }), /manifest-mismatch/);
  assert.throws(() => verifyInstalledEntry(root, { files: manifest.files.map(row => ({ ...row, sha256: "0".repeat(64) })) }), /manifest-mismatch/);
  const main = path.join(root, "apps/desktop/electron/main.mjs"), old = fs.readFileSync(main);
  fs.appendFileSync(main, "changed"); assert.throws(() => verifyInstalledEntry(root, manifest), /manifest-mismatch/); fs.writeFileSync(main, old);
  for (const relative of [INSTALLED_ENTRY, "apps/desktop/electron/preload.cjs", "apps/desktop/dist/index.html"]) {
    const file = path.join(root, relative), bytes = fs.readFileSync(file); fs.unlinkSync(file);
    assert.throws(() => verifyInstalledEntry(root), /ENOENT/); fs.writeFileSync(file, bytes);
  }
  fs.renameSync(main, main + ".original"); fs.symlinkSync(main + ".original", main);
  assert.throws(() => verifyInstalledEntry(root), /symlink/); fs.unlinkSync(main); fs.renameSync(main + ".original", main);
  assert.equal(verifyInstalledEntry(root, manifest).entry, INSTALLED_ENTRY);
  console.log("installed-entry PASS: development package regression, CJS installed bootstrap, required files, hash binding and symlink rejection (static fixtures; no Windows/device execution)");
} finally { fs.rmSync(scratch, { recursive: true, force: true }); }
