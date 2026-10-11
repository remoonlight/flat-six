import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

export const INSTALLED_ENTRY = "apps/desktop/electron/installed-bootstrap.cjs";
export function installedAppPackage() {
  return { name: "flat-six", version: "0.1.0", type: "module", main: INSTALLED_ENTRY };
}

/** App metadata is generated for installation; the development workspace package
 * is never an interchangeable update. File hashes alone cannot prove this entry. */
export function verifyInstalledEntry(appRoot, deliveryManifest) {
  const root = path.resolve(appRoot);
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error("installed-entry-symlink");
  function file(relative) {
    let current = root;
    for (const part of relative.split("/")) {
      current = path.join(current, part);
      if (fs.lstatSync(current).isSymbolicLink()) throw new Error("installed-entry-symlink");
    }
    if (!fs.statSync(current).isFile()) throw new Error("installed-entry-file-missing");
    return current;
  }
  const manifest = JSON.parse(fs.readFileSync(file("package.json"), "utf8"));
  if (manifest.name !== "flat-six" || manifest.type !== "module" || manifest.main !== INSTALLED_ENTRY
    || typeof manifest.version !== "string" || !manifest.version
    || "workspaces" in manifest || "scripts" in manifest) throw new Error("installed-entry-package-invalid");
  const required = ["package.json", INSTALLED_ENTRY, "apps/desktop/electron/installed-entry.mjs",
    "apps/desktop/electron/main.mjs", "apps/desktop/electron/preload.cjs", "apps/desktop/dist/index.html"];
  const files = required.map(relative => {
    const actual = file(relative), data = fs.readFileSync(actual);
    const result = { path: "resources/app/" + relative, bytes: data.length,
      sha256: createHash("sha256").update(data).digest("hex") };
    if (deliveryManifest) {
      const rows = deliveryManifest.files?.filter(row => row.path === result.path);
      if (rows?.length !== 1 || rows[0].bytes !== result.bytes || rows[0].sha256 !== result.sha256)
        throw new Error("installed-entry-manifest-mismatch:" + relative);
    }
    return result;
  });
  return { entry: INSTALLED_ENTRY, package: manifest, files };
}
