/**
 * Guided troubleshooting + repair compare on CURRENT ObdPage (topology-test harness).
 * Isolated simulation; no .local/garage.db; no vehicle hardware.
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
const shotDir = path.join(root, ".local", "cursor-coordination", "steuer14-981-20260930");
fs.mkdirSync(shotDir, { recursive: true });

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
    reject(new Error("vite_not_ready " + url + "\n" + viteOut));
  });
}

function killTree(child) {
  if (!child?.pid) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGKILL");
  }
}

const envDeny = { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_HEADLESS: "1" };
const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");
const port = 5191;
let viteOut = "";
const vite = spawn(process.execPath, [viteBin, "--mode", "topology-test", "--port", String(port), "--strictPort", "--host", "127.0.0.1"], {
  cwd: appRoot,
  env: envDeny,
  stdio: ["ignore", "pipe", "pipe"],
});
vite.stdout.on("data", (c) => {
  viteOut += c;
});
vite.stderr.on("data", (c) => {
  viteOut += c;
});

let browser;
try {
  await waitHttp(`http://127.0.0.1:${port}/topology-harness.html`);
  browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage();
  page.on("pageerror", (e) => {
    throw new Error("pageerror " + String(e));
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`http://127.0.0.1:${port}/topology-harness.html`, { waitUntil: "networkidle" });
  await page.waitForSelector("[data-page='obd']");

  await page.click("[data-obd-tab='compare']");
  await page.waitForSelector("[data-testid='obd-compare-empty']");
  await page.screenshot({ path: path.join(shotDir, "compare-empty.png") });

  await page.click("[data-obd-tab='topology']");
  await page.click("[data-testid='topo-read-all']");
  await page.waitForFunction(() => document.querySelector("[data-testid='topo-node-dme']")?.getAttribute("data-kind") === "dtc-present");
  await page.waitForFunction(async () => {
    const rows = await window.porsche981.obdDiag({ op: "snapshot:list" });
    return Array.isArray(rows) && rows.length >= 1;
  });
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.click("[data-testid='topo-open-guide']");
  await page.waitForSelector("[data-testid='obd-guide']");
  await page.click("[data-testid='obd-guide-open']");
  await page.waitForSelector("[data-testid='obd-guide-steps']");
  await page.locator("[data-testid='obd-guide-step']").first().click();
  await page.waitForSelector("[data-testid='obd-guide-step'][data-selected='1']");
  await page.click("[data-testid='obd-guide-result-normal']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-guide-progress']")?.textContent?.includes("进度 1/"));
  await page.screenshot({ path: path.join(shotDir, "guide-open.png") });

  await page.click("[data-obd-tab='faults']");
  await page.click("[data-obd-tab='guide']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-guide-progress']")?.textContent?.includes("进度 1/"));

  await page.reload({ waitUntil: "networkidle" });
  await page.click("[data-obd-tab='guide']");
  await page.click("[data-testid='obd-guide-resume']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-guide-progress']")?.textContent?.includes("进度 1/"));

  await page.click("[data-obd-tab='topology']");
  await page.evaluate(() => {
    window.__TOPO_SCENARIO__ = "disconnect";
  });
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.click("[data-testid='topo-read-selected']");
  await page.waitForFunction(() => document.querySelector("[data-testid='topo-node-dme']")?.getAttribute("data-kind") === "comm-fail" || document.querySelector("[data-testid='topo-node-dme']")?.getAttribute("data-stale") === "1");
  await page.waitForFunction(async () => {
    const rows = await window.porsche981.obdDiag({ op: "snapshot:list" });
    return Array.isArray(rows) && rows.length >= 2;
  });

  await page.click("[data-obd-tab='compare']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-compare-before']")?.querySelectorAll("option").length > 2);
  const ids = await page.$$eval("[data-testid='obd-compare-before'] option", (opts) => opts.map((o) => o.value).filter(Boolean));
  if (ids.length < 2) throw new Error("need two snapshots, got " + ids.join(","));
  await page.selectOption("[data-testid='obd-compare-before']", ids[ids.length - 1]);
  await page.selectOption("[data-testid='obd-compare-after']", ids[0]);
  await page.click("[data-testid='obd-compare-bind-before']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-compare-info']")?.textContent?.includes("声明绑定"));
  await page.click("[data-testid='obd-compare-bind-after']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-compare-info']")?.textContent?.includes("声明绑定"));
  await page.click("[data-testid='obd-compare-run']");
  await page.waitForSelector("[data-testid='obd-compare-result']");
  const body = await page.textContent("[data-testid='obd-compare-result']");
  if (!body.includes("不可比")) throw new Error("expected not-comparable for failed later unit, got " + body);
  const rowText = (await page.$$eval("[data-testid='obd-compare-result'] li", (els) => els.map((e) => e.textContent || ""))).join("\n");
  if (rowText.includes("未再观察到") || /\bgone\b/i.test(rowText)) {
    throw new Error("failed later must not report gone rows: " + rowText);
  }
  if (body.includes("已修好")) throw new Error("repaired wording " + body);
  await page.fill("[data-testid='obd-compare-note']", "更换接头后复测（观察记录）");
  await page.click("[data-testid='obd-compare-save']");
  await page.screenshot({ path: path.join(shotDir, "compare-result.png") });

  await page.click("[data-obd-tab='insights']");
  await page.click("[data-testid='obd-insights-guide']");
  await page.waitForSelector("[data-testid='obd-guide']");

  console.log("PASS steuer14 ui: guide persist, assign, failed-later zero gone");
} finally {
  if (browser) await browser.close().catch(() => {});
  killTree(vite);
}
