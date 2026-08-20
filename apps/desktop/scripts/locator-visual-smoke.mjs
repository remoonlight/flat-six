/**
 * Headless-ish visual smoke: launch Electron, open Locator, capture PNG.
 * Run: node apps/desktop/scripts/locator-visual-smoke.mjs
 * Requires Vite on 127.0.0.1:5173 (or starts it).
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import electronPath from "electron";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(appRoot, "../..");
const outDir = path.join(repoRoot, ".local");
const outPng = path.join(outDir, "locator-visual-smoke.png");
const DEV = "http://127.0.0.1:5173";

async function viteUp() {
  try {
    const r = await fetch(DEV);
    return r.ok || r.status === 404;
  } catch {
    return false;
  }
}

async function waitVite(ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await viteUp()) return;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("vite_not_ready");
}

let viteProc = null;
if (!(await viteUp())) {
  viteProc = spawn(
    "npx",
    ["vite", "--port", "5173", "--host", "127.0.0.1"],
    { cwd: appRoot, shell: true, stdio: "ignore" },
  );
  await waitVite();
}

const child = spawn(
  electronPath,
  ["."],
  {
    cwd: appRoot,
    env: {
      ...process.env,
      VITE_DEV_SERVER_URL: DEV,
      PORSCHE981_VISUAL_SMOKE: outPng,
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);

let log = "";
child.stdout.on("data", (d) => {
  log += d.toString();
  process.stdout.write(d);
});
child.stderr.on("data", (d) => {
  log += d.toString();
  process.stderr.write(d);
});

const ok = await new Promise((resolve) => {
  const timer = setTimeout(() => resolve(false), 45000);
  child.on("exit", (code) => {
    clearTimeout(timer);
    resolve(code === 0);
  });
});

if (viteProc) {
  try {
    viteProc.kill();
  } catch {
    /* ignore */
  }
}

if (!ok || !fs.existsSync(outPng)) {
  console.error("LOCATOR VISUAL SMOKE FAIL");
  console.error(log.slice(-2000));
  process.exit(1);
}

const st = fs.statSync(outPng);
console.log(`LOCATOR VISUAL SMOKE PASS; png=${outPng} bytes=${st.size}`);
process.exit(0);
