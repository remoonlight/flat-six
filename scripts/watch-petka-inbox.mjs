/**
 * Watch .local/petka-inbox/ for new paste/save files; debounce then run
 * ingest-petka-inbox.mjs. Pure file watcher — never touches PETKA UI.
 *
 * Usage: npm run watch:petka-inbox
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const INBOX = path.join(root, ".local", "petka-inbox");
const INGEST = path.join(root, "scripts", "ingest-petka-inbox.mjs");
const DEBOUNCE_MS = Number(process.env.PETKA_INBOX_DEBOUNCE_MS || 800);
const SKIP_PREFIX = new Set(["_", "."]);

let timer = null;
let running = false;
let pending = false;

function log(msg) {
  console.error(`[watch-petka-inbox] ${msg}`);
}

function shouldHandle(filename) {
  if (!filename) return false;
  // ignore done/ and nested paths from some platforms
  if (filename.includes("done") || filename.includes(`${path.sep}done`)) return false;
  const base = path.basename(filename);
  if (SKIP_PREFIX.has(base[0])) return false;
  if (/^readme/i.test(base)) return false;
  const ext = path.extname(base).toLowerCase();
  return ext === ".txt" || ext === ".tsv" || ext === ".csv";
}

function runIngest() {
  if (running) {
    pending = true;
    return;
  }
  running = true;
  log("ingest starting…");
  const child = spawn(process.execPath, [INGEST], {
    cwd: root,
    env: process.env,
    stdio: "inherit",
  });
  child.on("exit", (code) => {
    log(`ingest exit ${code}`);
    running = false;
    if (pending) {
      pending = false;
      runIngest();
    }
  });
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    runIngest();
  }, DEBOUNCE_MS);
}

fs.mkdirSync(path.join(INBOX, "done"), { recursive: true });
log(`watching ${path.relative(root, INBOX)} (debounce ${DEBOUNCE_MS}ms)`);

fs.watch(INBOX, { persistent: true }, (_event, filename) => {
  if (!shouldHandle(filename)) return;
  schedule();
});

if (process.env.PETKA_INBOX_WATCH_RUN_ONCE === "1") schedule();
