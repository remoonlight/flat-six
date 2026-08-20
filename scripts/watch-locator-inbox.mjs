/**
 * Watch .local/locator-inbox/ for overview drops; debounce then run
 * ingest-locator-inbox.mjs. Pure file watcher — never touches PETKA UI.
 *
 * Usage: npm run watch:locator-inbox
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const INBOX = path.join(root, ".local", "locator-inbox");
const INGEST = path.join(root, "scripts", "ingest-locator-inbox.mjs");
const DEBOUNCE_MS = Number(process.env.LOCATOR_INBOX_DEBOUNCE_MS || 800);
const ZONES = new Set(["engine-bay", "brakes", "chassis"]);
const EXTS = new Set([".png", ".jpg", ".jpeg"]);

let timer = null;
let running = false;
let pending = false;

function log(msg) {
  console.error(`[watch-locator-inbox] ${msg}`);
}

function shouldHandle(filename) {
  if (!filename) return true;
  const norm = String(filename).replace(/\\/g, "/");
  if (norm === "done" || norm.startsWith("done/")) return false;
  const base = path.basename(norm);
  if (base.startsWith(".") || base.startsWith("_")) return false;
  const ext = path.extname(base).toLowerCase();
  if (EXTS.has(ext)) return true;
  if (ZONES.has(base.toLowerCase())) return true;
  return false;
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
  child.on("error", (err) => {
    log(`ingest error: ${err}`);
    running = false;
  });
}

function schedule(reason) {
  log(`change: ${reason}; debounce ${DEBOUNCE_MS}ms`);
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    runIngest();
  }, DEBOUNCE_MS);
}

fs.mkdirSync(path.join(INBOX, "done"), { recursive: true });
for (const z of ZONES) {
  fs.mkdirSync(path.join(INBOX, z), { recursive: true });
}
log(`watching ${path.relative(root, INBOX)} (debounce ${DEBOUNCE_MS}ms)`);

const watchers = [];
watchers.push(
  fs.watch(INBOX, { persistent: true }, (_event, filename) => {
    if (!shouldHandle(filename)) return;
    schedule(filename || _event);
  }),
);
for (const z of ZONES) {
  const dir = path.join(INBOX, z);
  watchers.push(
    fs.watch(dir, { persistent: true }, (_event, filename) => {
      if (!shouldHandle(filename)) return;
      schedule(`${z}/${filename || _event}`);
    }),
  );
}

if (process.env.LOCATOR_INBOX_WATCH_RUN_ONCE === "1") schedule("startup");

function shutdown() {
  for (const w of watchers) w.close();
  if (timer) clearTimeout(timer);
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);