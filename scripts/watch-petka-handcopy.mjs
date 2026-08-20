/**
 * Watch .local/petka-handcopy.json and .local/petka-prices.tsv;
 * re-run apply-petka-handcopy.mjs (debounced).
 * Default: not resident — start via `npm run watch:petka-handcopy`.
 * Pure file watcher — never touches PETKA UI.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const LOCAL_DIR = path.join(root, ".local");
const HANDCOPY = path.join(LOCAL_DIR, "petka-handcopy.json");
const PRICES_TSV = path.join(LOCAL_DIR, "petka-prices.tsv");
const APPLY = path.join(root, "scripts", "apply-petka-handcopy.mjs");
const DEBOUNCE_MS = Number(process.env.PETKA_HANDCOPY_DEBOUNCE_MS || 800);
const WATCH_NAMES = new Set(["petka-handcopy.json", "petka-prices.tsv"]);

let timer = null;
let running = false;
let pending = false;

function log(msg) {
  console.error(`[watch-petka-handcopy] ${msg}`);
}

function runApply() {
  if (running) {
    pending = true;
    return;
  }
  running = true;
  log("apply starting…");
  const child = spawn(process.execPath, [APPLY], {
    cwd: root,
    env: process.env,
    stdio: "inherit",
  });
  child.on("exit", (code) => {
    log(`apply exit ${code}`);
    running = false;
    if (pending) {
      pending = false;
      runApply();
    }
  });
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    runApply();
  }, DEBOUNCE_MS);
}

if (!fs.existsSync(LOCAL_DIR)) {
  fs.mkdirSync(LOCAL_DIR, { recursive: true });
}
if (!fs.existsSync(HANDCOPY)) {
  log(`waiting for ${HANDCOPY}`);
}
if (!fs.existsSync(PRICES_TSV)) {
  log(`optional TSV not present yet: ${PRICES_TSV}`);
}

log(`watching petka-handcopy.json + petka-prices.tsv (debounce ${DEBOUNCE_MS}ms)`);
fs.watch(LOCAL_DIR, { persistent: true }, (_event, filename) => {
  if (!filename || !WATCH_NAMES.has(filename)) return;
  schedule();
});

if (process.env.PETKA_HANDCOPY_WATCH_RUN_ONCE === "1") schedule();
