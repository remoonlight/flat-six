/** Static validation only; does not launch Windows or touch devices. */
import fs from "node:fs";
import path from "node:path";
import { sha256, safeFile, verifyRuntimeSource } from "./windows-runtime-source.mjs";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";

export function inventory(root, excluded = new Set()) {
  if (fs.lstatSync(root).isSymbolicLink()) throw new Error("accept-root-symlink");
  const rows = [];
  function walk(directory, prefix = "") {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const relative = path.posix.join(prefix, entry.name);
      if (excluded.has(relative)) continue;
      if (entry.isSymbolicLink()) throw new Error("accept-file-symlink");
      if (entry.isDirectory()) walk(path.join(directory, entry.name), relative);
      else {
        const file = safeFile(root, relative);
        rows.push({ path: relative, bytes: fs.statSync(file).size, sha256: sha256(file) });
      }
    }
  }
  walk(root);
  return rows.sort((a, b) => a.path.localeCompare(b.path, "en"));
}

function verifyRows(root, rows, excluded) {
  if (!Array.isArray(rows) || new Set(rows.map(row => row.path)).size !== rows.length) throw new Error("accept-manifest-duplicate");
  for (const row of rows) {
    const file = safeFile(root, row.path);
    if (!Number.isSafeInteger(row.bytes) || row.bytes < 0 || !/^[a-f0-9]{64}$/.test(row.sha256 || "")
      || fs.statSync(file).size !== row.bytes || sha256(file) !== row.sha256) throw new Error("accept-file-integrity:" + row.path);
  }
  const actual = inventory(root, excluded).map(row => row.path).sort();
  if (JSON.stringify(actual) !== JSON.stringify(rows.map(row => row.path).sort())) throw new Error("accept-files-unexpected");
}

export function verifyAcceptanceKit(root) {
  const file = safeFile(root, "laptop-accept-manifest.json");
  const kit = JSON.parse(fs.readFileSync(file, "utf8"));
  if (kit.kind !== "flat-six-laptop-acceptance-v1" || kit.programIncluded !== false || kit.privateDefinitionsIncluded !== false
    || !/^[a-f0-9]{64}$/.test(kit.deliveryManifestSha256 || "") || !/^[a-f0-9]{64}$/.test(kit.definitionZipSha256 || "")) throw new Error("accept-kit-invalid");
  verifyRows(root, kit.files, new Set(["laptop-accept-manifest.json", ".local"]));
  for (const required of ["accept-laptop.cmd", "README.md", "package.json", "scripts/windows-runtime-accept.mjs",
    "scripts/windows-runtime-source.mjs", "scripts/laptop-accept-inputs.mjs", "apps/desktop/electron/offline-diagnostics.mjs",
    "apps/desktop/electron/installed-entry.mjs", "node_modules/playwright/index.mjs", "node_modules/playwright-core/index.mjs"])
    if (!kit.files.some(row => row.path === required)) throw new Error("accept-kit-file-missing:" + required);
  for (const name of ["playwright", "playwright-core"]) {
    const meta = JSON.parse(fs.readFileSync(safeFile(root, `node_modules/${name}/package.json`), "utf8"));
    if (meta.name !== name || meta.version !== kit.playwrightVersion) throw new Error("accept-kit-dependency-version");
  }
  return kit;
}

export function verifyLaptopAcceptanceInputs(delivery, bundle, kitRoot) {
  const file = safeFile(delivery, "delivery-manifest.json");
  const manifest = JSON.parse(fs.readFileSync(file, "utf8"));
  verifyRuntimeSource(delivery, manifest.electronVersion);
  verifyRows(delivery, manifest.files, new Set(["delivery-manifest.json"]));
  verifyInstalledEntry(path.join(delivery, "resources/app"), manifest);
  if (!fs.lstatSync(bundle).isFile() || fs.lstatSync(bundle).isSymbolicLink()) throw new Error("accept-bundle-file-invalid");
  const deliveryManifestSha256 = sha256(file), definitionZipSha256 = sha256(bundle);
  const kit = kitRoot ? verifyAcceptanceKit(kitRoot) : null;
  if (kit && (kit.deliveryManifestSha256 !== deliveryManifestSha256 || kit.definitionZipSha256 !== definitionZipSha256
    || kit.electronVersion !== manifest.electronVersion)) throw new Error("accept-kit-input-mismatch");
  return { deliveryManifestSha256, definitionZipSha256, programFiles: manifest.files.length,
    kitBound: !!kit, kitFiles: kit?.files.length ?? null, electronVersion: manifest.electronVersion,
    staticOnly: true, windowsExecutionVerified: false, definitionContentsImported: false, deviceIO: false };
}
