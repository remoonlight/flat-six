/**
 * Sync clipboard dumps from PETKA host drop folder → .local/petka-bulk/
 *
 * Requires:
 *   PORSCHE981_PETKA_SSH=user@host
 * Optional:
 *   PORSCHE981_PETKA_DROP=C:/petka-bulk-drop
 *
 * Usage:
 *   PORSCHE981_PETKA_SSH=user@host node scripts/sync-petka-bulk-remote.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const BULK = path.join(root, ".local", "petka-bulk");
const sshHost = process.env.PORSCHE981_PETKA_SSH;
if (!sshHost) {
  console.error("set PORSCHE981_PETKA_SSH=user@host");
  process.exit(2);
}
const remoteDrop =
  process.env.PORSCHE981_PETKA_DROP || "C:/petka-bulk-drop";

function scpPull(gen) {
  const dest = path.join(BULK, gen);
  fs.mkdirSync(dest, { recursive: true });
  const remote = `${sshHost}:${remoteDrop}/${gen}/*.txt`;
  const r = spawnSync(
    "scp",
    ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", remote, dest],
    { encoding: "utf8" },
  );
  return {
    gen,
    status: r.status,
    stderr: (r.stderr || "").trim(),
    stdout: (r.stdout || "").trim(),
    files: fs.existsSync(dest)
      ? fs.readdirSync(dest).filter((f) => f.endsWith(".txt"))
      : [],
  };
}

fs.mkdirSync(BULK, { recursive: true });
const report = {
  checked_at: new Date().toISOString(),
  sshHost,
  remoteDrop,
  gens: ["981", "982"].map(scpPull),
};
fs.writeFileSync(
  path.join(BULK, "sync-remote-last.json"),
  JSON.stringify(report, null, 2),
);
console.log(JSON.stringify(report, null, 2));
const anyFiles = report.gens.some((g) => g.files.length);
process.exit(anyFiles ? 0 : 2);
