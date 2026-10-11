/** Local unpacked Windows artifact. Exclusive fresh output; no install/registry/device changes. */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { verifyRuntimeSource, copyVerifiedRuntime, copyDiagnosticScriptEntries } from "./windows-runtime-source.mjs";
import { installedAppPackage, verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";

const root = path.resolve(import.meta.dirname, "..");
const target = path.resolve(process.argv[2] || "");
const sourceFlag = process.argv[3];
const runtimeSource = sourceFlag === "--runtime-source" && process.argv.length === 5 ? process.argv[4] : null;
if (sourceFlag && !runtimeSource) throw new Error("Expected --runtime-source <retained-Windows-delivery>");
const relative = path.relative(path.join(root, ".local"), target);
if ((!runtimeSource && process.platform !== "win32") || !process.argv[2] || relative.startsWith("..") || path.isAbsolute(relative) || !relative)
  throw new Error("Use a new .local output on Windows, or explicitly supply a retained Windows runtime source");
if (fs.existsSync(target)) throw new Error("Output already exists; old delivery is preserved");
let python, verified;
if (runtimeSource) {
  const electronVersion = JSON.parse(fs.readFileSync(path.join(root, "node_modules/electron/package.json"), "utf8")).version;
  verified = verifyRuntimeSource(runtimeSource, electronVersion);
} else {
  const py = spawnSync(process.env.PORSCHE981_PYTHON || "python", ["-B", "-c", "import sys,json;print(json.dumps({'exe':sys.executable,'base':sys.base_prefix}))"], { encoding: "utf8", windowsHide: true, timeout: 10000 });
  if (py.status !== 0) throw new Error("Python discovery failed");
  python = JSON.parse(py.stdout);
}
const app = path.join(target, "resources", "app");
for (const required of ["apps/desktop/dist/index.html", "packages/db/dist/index.js", "packages/domain/dist/index.js"])
  if (!fs.existsSync(path.join(root, required))) throw new Error(`Build required: ${required}`);
fs.mkdirSync(target);
function copy(from, to, filter = () => true) {
  fs.cpSync(from, to, { recursive: true, dereference: false,
    filter: (source) => {
      if (fs.lstatSync(source).isSymbolicLink()) throw new Error(`Symlink excluded: ${source}`);
      return filter(source);
    } });
}
if (verified) copyVerifiedRuntime(verified, target);
else {
  copy(path.join(root, "node_modules/electron/dist"), target);
  // Electron's packaged-state check uses the executable name on Windows.
  fs.renameSync(path.join(target, "electron.exe"), path.join(target, "FlatSix.exe"));
}
fs.mkdirSync(app, { recursive: true });
fs.writeFileSync(path.join(app, "package.json"), JSON.stringify(installedAppPackage()));
for (const rel of ["apps/desktop/electron", "apps/desktop/dist", "scripts/diagnostics", "scripts/x431_re"])
  copy(path.join(root, rel), path.join(app, rel), (p) => !p.includes("__pycache__") && path.basename(p) !== "tests" && !p.endsWith(".pyc"));
copy(path.join(root, "apps/desktop/src"), path.join(app, "apps/desktop/src"),
  (p) => fs.statSync(p).isDirectory() || p.endsWith(".mjs"));
copyDiagnosticScriptEntries(root, app);
// Product seed data, without machine-local captures, DBs, backups or firmware.
copy(path.join(root, "data/seed"), path.join(app, "data/seed"));
for (const pkg of ["domain", "db", "obd"]) {
  const dest = path.join(app, "packages", pkg);
  fs.mkdirSync(dest, { recursive: true });
  copy(path.join(root, "packages", pkg, "package.json"), path.join(dest, "package.json"));
  const dist = path.join(root, "packages", pkg, "dist");
  if (fs.existsSync(dist)) copy(dist, path.join(dest, "dist"));
  if (pkg === "obd") copy(path.join(root, "packages/obd/src"), path.join(dest, "src"));
  // Resolve the workspace import from the child Node without workspace links.
  copy(dest, path.join(app, "node_modules", "@porsche981", pkg));
}
const runtime = path.join(app, "runtime"), pyRoot = path.join(runtime, "python");
if (!verified) {
  fs.mkdirSync(pyRoot, { recursive: true });
  copy(process.execPath, path.join(runtime, "node.exe"));
  for (const name of fs.readdirSync(python.base))
    if (/^(python(?:w|\d+)?\.exe|python\d*\.dll|vcruntime.*\.dll|LICENSE\.txt)$/i.test(name))
      copy(path.join(python.base, name), path.join(pyRoot, name));
  copy(path.join(python.base, "DLLs"), path.join(pyRoot, "DLLs"));
  copy(path.join(python.base, "Lib"), path.join(pyRoot, "Lib"),
    (p) => !p.includes("__pycache__") && !p.endsWith(".pyc") && !p.includes("site-packages") && path.basename(p) !== "test");
  copy(path.join(python.base, "Lib/site-packages/serial"), path.join(pyRoot, "Lib/site-packages/serial"),
    (p) => !p.includes("__pycache__") && !p.endsWith(".pyc"));
  const dll = fs.readdirSync(pyRoot).find((name) => /^python\d{2,3}\.dll$/i.test(name));
  if (!dll) throw new Error("Versioned Python DLL missing");
  fs.writeFileSync(path.join(pyRoot, dll.replace(/\.dll$/i, "._pth")), "Lib\nDLLs\nLib\\site-packages\n..\\..\n.\nimport site\n");
  const nodeLicense = path.join(path.dirname(process.execPath), "LICENSE");
  if (fs.existsSync(nodeLicense)) copy(nodeLicense, path.join(runtime, "NODE-LICENSE.txt"));
}
let pythonRuntimeProbed = false;
if (process.platform === "win32") {
  const probe = spawnSync(path.join(pyRoot, "python.exe"), ["-B", "-c", "import sqlite3,serial,scripts.diagnostics.workbench; print('runtime-ok')"],
    { cwd: app, encoding: "utf8", windowsHide: true, timeout: 30000, env: { ...process.env, PYTHONPATH: "", PYTHONDONTWRITEBYTECODE: "1" } });
  if (probe.status !== 0 || !probe.stdout.includes("runtime-ok")) throw new Error(`Bundled Python failed: ${probe.stderr}`);
  pythonRuntimeProbed = true;
}
const rows = [];
const installedEntry = verifyInstalledEntry(app);
fs.writeFileSync(path.join(target, "使用说明.txt"), "启动 FlatSix.exe。诊断资料在应用内显式导入；无需系统 Node/Python。诊断头驱动及蓝牙配对另行安装。此包没有私人抓包、业务库、车辆原码或固件正文，也不证明实车能力。\r\n");
function inventory(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) inventory(file);
    else rows.push({ path: path.relative(target, file).replaceAll("\\", "/"), bytes: fs.statSync(file).size,
      sha256: createHash("sha256").update(fs.readFileSync(file)).digest("hex") });
  }
}
inventory(target);
fs.writeFileSync(path.join(target, "delivery-manifest.json"), JSON.stringify({ kind: "local-windows-unpacked-delivery", files: rows,
  bundledRuntimes: true, privateDefinitionsIncluded: false, driversIncluded: false, vehicleVerified: false,
  stagedPlatform: process.platform, pythonRuntimeProbed,
  installedEntry: installedEntry.entry,
  ...(verified ? { runtimeSourceManifestSha256: verified.manifestSha256, runtimeSourceFiles: verified.files.length,
    electronVersion: verified.electronVersion } : {}) }, null, 2));
console.log(JSON.stringify({ ok: true, output: target, files: rows.length, bytes: rows.reduce((n, r) => n + r.bytes, 0),
  pythonRuntimeProbed, WindowsExecutionVerified: false }));
