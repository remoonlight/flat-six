/**
 * Open PETKA price + locator overview inbox folders in Explorer.
 * Never touches PETKA / Etka7 GUI.
 *
 * Usage: npm run open:petka-inbox
 *        node scripts/open-petka-inbox.mjs
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const DIRS = [
  path.join(root, ".local", "petka-inbox"),
  path.join(root, ".local", "locator-inbox"),
];

const LOCATOR_SUB = ["engine-bay", "brakes", "chassis", "done"];

for (const dir of DIRS) {
  fs.mkdirSync(dir, { recursive: true });
}
fs.mkdirSync(path.join(DIRS[0], "done"), { recursive: true });
for (const sub of LOCATOR_SUB) {
  fs.mkdirSync(path.join(DIRS[1], sub), { recursive: true });
}

function openExplorer(dir) {
  // explorer.exe <path> — brief window popup is OK
  const child = spawn("explorer.exe", [dir], {
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  console.log(`opened: ${dir}`);
}

for (const dir of DIRS) {
  openExplorer(dir);
}
