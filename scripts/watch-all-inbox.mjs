/**
 * Start the four background inbox/handcopy watchers (no PETKA GUI).
 * Spawns detached children and exits. Check with: npm run status:petka
 *
 * Usage: npm run watch:all-inbox
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const SCRIPTS = [
  "watch-petka-inbox.mjs",
  "watch-locator-inbox.mjs",
  "watch-locator-shots.mjs",
  "watch-petka-handcopy.mjs",
];

for (const name of SCRIPTS) {
  const child = spawn(
    process.execPath,
    [path.join(root, "scripts", name)],
    {
      cwd: root,
      detached: true,
      stdio: "ignore",
      env: process.env,
    },
  );
  child.unref();
  console.log(`spawned ${name} pid=${child.pid}`);
}

console.log("done. verify: npm run status:petka");
