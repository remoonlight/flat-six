/**
 * First-run: copy committed seed/template into .local if the runtime file is missing.
 * Never overwrites an existing .local file (hand edits stay authoritative).
 */
import fs from "node:fs";
import path from "node:path";

/**
 * @param {string} dest runtime path under .local/
 * @param {string} seedPath committed snapshot
 * @returns {boolean} true if dest was created
 */
export function ensureLocalFromSeed(dest, seedPath) {
  if (fs.existsSync(dest)) return false;
  const alt = path.join(path.dirname(dest), path.basename(seedPath));
  const src = [alt, seedPath].find((p) => p && p !== dest && fs.existsSync(p));
  if (!src) return false;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
  return true;
}
