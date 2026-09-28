/**
 * OBD connection UI accept: harness only. Isolated. Live disabled. No garage.db.
 * node scripts/obd-connection-accept.mjs
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const shotDir = path.join(root, ".local", "cursor-coordination", "obd-x431-cadence-20260927", "scratch", "ui");
fs.mkdirSync(shotDir, { recursive: true });

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], ...opts });
    let out = "";
    let err = "";
    child.stdout.on("data", (c) => {
      out += c;
    });
    child.stderr.on("data", (c) => {
      err += c;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(`${cmd} ${args.join(" ")} exit ${code}\n${err || out}`));
      else resolve({ out, err, code: 0 });
    });
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function waitHttp(url, tries = 80) {
  return new Promise(async (resolve, reject) => {
    for (let i = 0; i < tries; i++) {
      try {
        await new Promise((res, rej) => {
          const req = http.get(url, (r) => {
            r.resume();
            r.statusCode && r.statusCode < 500 ? res() : rej(new Error(String(r.statusCode)));
          });
          req.on("error", rej);
        });
        return resolve();
      } catch {
        await sleep(250);
      }
    }
    reject(new Error("vite_not_ready " + url));
  });
}

const envDeny = {
  ...process.env,
  PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_HEADLESS: "1",
  PW_TEST_SCREENSHOT_NO_FONTS_READY: "1",
};

const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");
const port = 5191;
const vite = spawn(process.execPath, [viteBin, "--mode", "connection-test", "--port", String(port), "--strictPort", "--host", "127.0.0.1"], {
  cwd: appRoot,
  env: envDeny,
  stdio: ["ignore", "pipe", "pipe"],
});
let viteOut = "";
vite.stdout.on("data", (c) => {
  viteOut += c;
});
vite.stderr.on("data", (c) => {
  viteOut += c;
});

const results = [];
try {
  await waitHttp(`http://127.0.0.1:${port}/obd-connection-harness.html`);
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.goto(`http://127.0.0.1:${port}/obd-connection-harness.html?empty=1`, { waitUntil: "networkidle" });
  await page.click("[data-obd-tab='connection']");
  await page.waitForSelector("[data-testid='obd-conn-empty']");
  const emptyV = await page.textContent("[data-testid='obd-header-voltage']");
  if (!emptyV.includes("--")) throw new Error(`empty voltage ${emptyV}`);
  await page.screenshot({ path: path.join(shotDir, "empty.png") });
  results.push("empty");

  await page.goto(`http://127.0.0.1:${port}/obd-connection-harness.html?nocom=1`, { waitUntil: "networkidle" });
  await page.click("[data-obd-tab='connection']");
  await page.waitForSelector("[data-testid='obd-conn-list']");
  await page.click("[data-testid='obd-device-bt:000000000000']");
  await page.waitForFunction(() => (document.querySelector("[data-testid='obd-conn-note']")?.textContent || "").includes("串口"));
  await page.screenshot({ path: path.join(shotDir, "no-com.png") });
  results.push("no-com");

  await page.goto(`http://127.0.0.1:${port}/obd-connection-harness.html`, { waitUntil: "networkidle" });
  await page.click("[data-obd-tab='connection']");
  await page.waitForSelector("[data-testid='obd-conn-list']");
  const tabLabel = await page.textContent("[data-obd-tab='connection']");
  if (!tabLabel.includes("连接设置")) throw new Error(`tab ${tabLabel}`);
  await page.click("[data-testid='obd-device-bt:000000000000']");
  await page.click("[data-testid='obd-conn-connect']");
  await page.waitForFunction(() => (document.querySelector("[data-testid='obd-header-voltage']")?.textContent || "").includes("12.6"));
  await page.waitForFunction(() => (document.querySelector("[data-testid='obd-conn-state']")?.textContent || "").includes("已连接"));
  const btn = await page.textContent("[data-testid='obd-conn-connect']");
  if (!btn.includes("连接设备")) throw new Error(`connect label ${btn}`);
  const liveTab = await page.textContent("[data-obd-tab='live']");
  await page.click("[data-obd-tab='live']");
  const v2 = await page.textContent("[data-testid='obd-header-voltage']");
  if (!v2.includes("12.6")) throw new Error(`other tab voltage ${v2}`);
  await page.click("[data-obd-tab='connection']");
  await page.screenshot({ path: path.join(shotDir, "connected.png") });
  results.push("connected " + liveTab);

  await page.evaluate(() => {
    window.porsche981.readOnlySession({
      action: "start",
      profileId: "porsche-981-2014-dme",
      mode: "simulation",
    });
  });
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-conn-connect']")?.disabled === true);
  await page.waitForFunction(() => (document.querySelector("[data-testid='obd-conn-state']")?.textContent || "").includes("诊断占用"));
  await page.screenshot({ path: path.join(shotDir, "session-busy.png") });
  results.push("busy");

  await page.waitForTimeout(900);
  await page.click("[data-testid='obd-conn-disconnect']");
  await page.waitForFunction(() => (document.querySelector("[data-testid='obd-header-voltage']")?.textContent || "").includes("--"));
  await page.screenshot({ path: path.join(shotDir, "disconnect.png") });
  results.push("disconnect");

  await browser.close();
} finally {
  vite.kill();
}

console.log("obd-connection-accept: ok", results.join(", "), shotDir);
