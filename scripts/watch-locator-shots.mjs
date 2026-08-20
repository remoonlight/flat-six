/**
 * Watch data/petka/<zone>/shots for overview drops; debounce → apply-locator-shots.
 * Background-only; never touches PETKA GUI.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const ZONES = ["engine-bay", "brakes", "chassis"];
const DEBOUNCE_MS = 800;
const applyScript = path.join(__dirname, "apply-locator-shots.mjs");

let timer = null;
let running = false;
let pending = false;

function scheduleApply(reason) {
  console.log(`[watch-locator-shots] change: ${reason}; debounce ${DEBOUNCE_MS}ms`);
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    void runApply();
  }, DEBOUNCE_MS);
}

function runApply() {
  if (running) {
    pending = true;
    return Promise.resolve();
  }
  running = true;
  console.log("[watch-locator-shots] running apply-locator-shots…");
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [applyScript], {
      cwd: root,
      stdio: "inherit",
      env: process.env,
    });
    child.on("close", (code) => {
      console.log(`[watch-locator-shots] apply exit=${code ?? 1}`);
      running = false;
      if (pending) {
        pending = false;
        void runApply().then(resolve);
      } else {
        resolve();
      }
    });
    child.on("error", (err) => {
      console.error("[watch-locator-shots] apply error:", err);
      running = false;
      resolve();
    });
  });
}

const watchers = [];
for (const zoneId of ZONES) {
  const dir = path.join(root, "data", "petka", zoneId, "shots");
  fs.mkdirSync(dir, { recursive: true });
  const w = fs.watch(dir, { persistent: true }, (eventType, filename) => {
    const name = filename ? String(filename).toLowerCase() : "";
    if (name && !/^overview\.(png|jpe?g)$/.test(name) && name !== "overview") {
      // still schedule — editors may use temp names then rename
    }
    scheduleApply(`${zoneId}/${filename || eventType}`);
  });
  watchers.push(w);
  console.log(`[watch-locator-shots] watching ${path.relative(root, dir)}`);
}

console.log(
  "[watch-locator-shots] idle; drop overview.{png,jpg,jpeg} into the dirs above",
);

// Initial scan once at start
scheduleApply("startup");

function shutdown() {
  for (const w of watchers) w.close();
  if (timer) clearTimeout(timer);
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
