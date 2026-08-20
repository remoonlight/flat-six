/**
 * Remote/local PETKA bulk capture helper.
 *
 * Default EtStart (PETKA host, if installed): 
 *   C:\Program Files (x86)\Digital-Eliteboard\PETKA\Program\EtStart.exe
 *
 * Modes:
 *   --status          check EtStart + Etka7 (local or via PORSCHE981_PETKA_HOST note)
 *   --drop-sample     write fixture hg into .local/petka-bulk/981/ for pipeline demo
 *   --from-clipboard  (Windows) save clipboard text as .local/petka-bulk/<gen>/<hg>.txt
 *
 * Full GUI sweep on 192 requires interactive desktop (session with visible Etka7).
 * SSH session 0 often sees Etka7 with MainWindowHandle=0 — paste inbox instead:
 *   copy parts list in PETKA → save as .local/petka-bulk/981/<hg>.txt → parse:petka-bulk
 *
 * Usage:
 *   node scripts/capture-petka-bulk.mjs --status
 *   node scripts/capture-petka-bulk.mjs --drop-sample
 *   node scripts/capture-petka-bulk.mjs --from-clipboard --gen 981 --hg engine
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const BULK = path.join(root, ".local", "petka-bulk");
const ETSTART =
  process.env.PORSCHE981_ETSTART ||
  "C:\\Program Files (x86)\\Digital-Eliteboard\\PETKA\\Program\\EtStart.exe";

function parseArgs(argv) {
  const out = {
    status: false,
    dropSample: false,
    fromClipboard: false,
    gen: "981",
    hg: "hg01",
  };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--status") out.status = true;
    else if (argv[i] === "--drop-sample") out.dropSample = true;
    else if (argv[i] === "--from-clipboard") out.fromClipboard = true;
    else if (argv[i] === "--gen") out.gen = argv[++i];
    else if (argv[i] === "--hg") out.hg = argv[++i];
  }
  return out;
}

function statusLocal() {
  const present = fs.existsSync(ETSTART);
  const ps = spawnSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      "Get-Process -Name Etka7,EtStart -ErrorAction SilentlyContinue | Select-Object Name,Id,MainWindowTitle,MainWindowHandle,SessionId | ConvertTo-Json -Compress",
    ],
    { encoding: "utf8" },
  );
  let processes = [];
  try {
    const raw = (ps.stdout || "").trim();
    if (raw) processes = JSON.parse(raw);
    if (!Array.isArray(processes) && processes) processes = [processes];
  } catch {
    processes = [];
  }
  const report = {
    checked_at: new Date().toISOString(),
    host_hint: process.env.PORSCHE981_PETKA_HOST || "local-or-unset",
    etstart: ETSTART,
    present,
    processes,
    note:
      "If present=false here but PETKA is on another host, run capture/status there or paste clipboard dumps into .local/petka-bulk/",
  };
  const out = path.join(root, ".local", "petka-install-status.json");
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  return present || processes.length > 0;
}

function dropSample() {
  const fixture = path.join(
    root,
    "data",
    "petka",
    "_template",
    "bulk-fixtures",
    "hg-engine.txt",
  );
  const destDir = path.join(BULK, "981");
  fs.mkdirSync(destDir, { recursive: true });
  const dest = path.join(destDir, "hg-engine.txt");
  fs.copyFileSync(fixture, dest);
  // minimal 982 twin for dual-gen ingest path
  const dest982 = path.join(BULK, "982");
  fs.mkdirSync(dest982, { recursive: true });
  fs.copyFileSync(fixture, path.join(dest982, "hg-engine.txt"));
  console.log(
    JSON.stringify(
      { ok: true, wrote: [dest, path.join(dest982, "hg-engine.txt")] },
      null,
      2,
    ),
  );
}

function fromClipboard(gen, hg) {
  if (gen !== "981" && gen !== "982") throw new Error("gen must be 981|982");
  const ps = spawnSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::GetText()",
    ],
    { encoding: "utf8", maxBuffer: 20 * 1024 * 1024 },
  );
  const text = (ps.stdout || "").trim();
  if (!text) throw new Error("clipboard empty");
  const dir = path.join(BULK, gen);
  fs.mkdirSync(dir, { recursive: true });
  const dest = path.join(dir, `${hg}.txt`);
  fs.writeFileSync(dest, text, "utf8");
  console.log(JSON.stringify({ ok: true, gen, hg, bytes: text.length, dest }, null, 2));
}

function writeSweepState(patch) {
  const p = path.join(BULK, "sweep-state.json");
  fs.mkdirSync(BULK, { recursive: true });
  let cur = { gens: { "981": { done: [], pending: [] }, "982": { done: [], pending: [] } } };
  if (fs.existsSync(p)) {
    try {
      cur = JSON.parse(fs.readFileSync(p, "utf8"));
    } catch {
      /* */
    }
  }
  const next = { ...cur, ...patch, updated_at: new Date().toISOString() };
  fs.writeFileSync(p, JSON.stringify(next, null, 2));
  return next;
}

const args = parseArgs(process.argv.slice(2));
if (args.status) {
  const ok = statusLocal();
  process.exit(ok ? 0 : 2);
}
if (args.dropSample) {
  dropSample();
  writeSweepState({
    last_mode: "drop-sample",
    note: "fixture only — replace with real PETKA clipboard dumps",
  });
  process.exit(0);
}
if (args.fromClipboard) {
  fromClipboard(args.gen, args.hg);
  const statePath = path.join(BULK, "sweep-state.json");
  let state = { gens: { "981": { done: [] }, "982": { done: [] } } };
  if (fs.existsSync(statePath)) {
    try {
      state = JSON.parse(fs.readFileSync(statePath, "utf8"));
    } catch {
      /* */
    }
  }
  const done = state.gens?.[args.gen]?.done ?? [];
  if (!done.includes(args.hg)) done.push(args.hg);
  writeSweepState({
    gens: {
      ...state.gens,
      [args.gen]: { ...(state.gens?.[args.gen] ?? {}), done },
    },
    last_mode: "from-clipboard",
  });
  process.exit(0);
}

console.log(`Usage:
  node scripts/capture-petka-bulk.mjs --status
  node scripts/capture-petka-bulk.mjs --drop-sample
  node scripts/capture-petka-bulk.mjs --from-clipboard --gen 981 --hg engine
`);
process.exit(1);
