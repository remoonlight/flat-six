/**
 * Real Electron + isolated TEMP DB + Python SIMULATION.
 * Capture → assign → guide → compare → close/reopen. No .local/garage.db.
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const shotDir = path.join(root, ".local", "cursor-coordination", "steuer14-981-20260930", "scratch");
fs.mkdirSync(shotDir, { recursive: true });
const testDbDir = fs.mkdtempSync(path.join(shotDir, "test-db-"));
const testDb = path.join(testDbDir, "garage-steuer14.db");
const require = createRequire(path.join(appRoot, "electron", "main.mjs"));
const electronBin = require("electron");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";

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

const envDeny = {
  ...process.env,
  PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_HEADLESS: "1",
  PW_TEST_SCREENSHOT_NO_FONTS_READY: "1",
};

if (path.normalize(testDb).toLowerCase().endsWith(`${path.sep}.local${path.sep}garage.db`)) {
  throw new Error("refused production garage.db");
}

await run(npm, ["run", "build", "-w", "@porsche981/domain"], { cwd: root, shell: true, env: envDeny });
await run(npm, ["run", "build", "-w", "@porsche981/db"], { cwd: root, shell: true, env: envDeny });
await run(npm, ["run", "build", "-w", "@porsche981/desktop"], { cwd: root, shell: true, env: envDeny });

const { GarageDb } = await import("../packages/db/dist/index.js");
fs.mkdirSync(testDbDir, { recursive: true });
try {
  fs.unlinkSync(testDb);
} catch {
  /* */
}
const parts = JSON.parse(fs.readFileSync(path.join(root, "data/seed/parts/bootstrap.json"), "utf8"));
const faults = JSON.parse(fs.readFileSync(path.join(root, "data/seed/faults/bootstrap.json"), "utf8"));
const dtcs = JSON.parse(fs.readFileSync(path.join(root, "data/seed/dtc/bootstrap.json"), "utf8"));
const db = new GarageDb(testDb);
db.seedIfEmpty({ parts: parts.parts, faults: faults.faults, dtcs: dtcs.dtcs });
if (typeof db.seedDtcIfEmpty === "function") db.seedDtcIfEmpty(dtcs.dtcs);
db.close();

function launchApp() {
  return electron.launch({
    executablePath: electronBin,
    args: [appRoot],
    env: {
      ...envDeny,
      PORSCHE981_DB: testDb,
      PORSCHE981_DEVTOOLS: "",
      VITE_DEV_SERVER_URL: "",
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
    },
    timeout: 90_000,
  });
}

async function shot(app, name) {
  await sleep(300);
  const data = await app.evaluate(async ({ BrowserWindow }) => {
    const window = BrowserWindow.getAllWindows()[0];
    return (await window.webContents.capturePage()).toPNG().toString("base64");
  });
  const p = path.join(shotDir, name);
  fs.writeFileSync(p, Buffer.from(data, "base64"));
  if (!fs.statSync(p).size) throw new Error("empty screenshot " + name);
}

async function runSimRead(win) {
  const prior = await win.evaluate(async () => (await window.porsche981.obdDiag({ op: "snapshot:list" })).length);
  await win.click("[data-obd-tab='session']");
  await win.selectOption("[data-testid='ros-mode']", "simulation");
  await win.click("[data-testid='ros-prepare']");
  await win.waitForFunction(() => !document.querySelector("[data-testid='ros-prepare']")?.disabled);
  await win.waitForFunction(() => (document.querySelector("[data-testid='ros-plan']")?.textContent || "").length > 8, null, { timeout: 90_000 });
  await win.click("[data-testid='ros-start']");
  await win.waitForFunction(() => {
    const t = document.querySelector("[data-testid='ros-kind']")?.textContent || "";
    return t.includes("模拟结果") || t.includes("采集失败");
  }, null, { timeout: 120_000 });
  const kind = await win.textContent("[data-testid='ros-kind']");
  if (!kind || kind.includes("采集失败")) {
    throw new Error("python sim failed: " + kind + " / " + (await win.textContent("[data-testid='ros-progress']")));
  }
  const deadline = Date.now() + 60_000;
  let saved = false;
  while (Date.now() < deadline) {
    const count = await win.evaluate(async () => (await window.porsche981.obdDiag({ op: "snapshot:list" })).length);
    if (count > prior) { saved = true; break; }
    await sleep(250);
  }
  if (!saved) throw new Error("snapshot not saved: " + await win.textContent("[data-page='read-only-session']"));
  console.log("capture saved", JSON.stringify(await win.evaluate(async () => (await window.porsche981.obdDiag({ op: "snapshot:list" })).map((s) => s.id))));
}

let app = await launchApp();
try {
  const win = await app.firstWindow({ timeout: 90_000 });
  console.log("isolated DB", await app.evaluate(() => process.env.PORSCHE981_DB));
  await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1440, 1200));
  await win.waitForSelector("[data-tab='obd']", { timeout: 60_000 });
  await win.click("[data-tab='obd']");
  await runSimRead(win);
  await win.click("[data-obd-tab='faults']");
  await win.fill("input[placeholder='P0300']", "U0447");
  await win.selectOption("[data-testid='obd-guide-ecu']", "dme");
  await win.click("[data-testid='obd-fault-open-guide']");
  await win.waitForSelector("[data-testid='obd-guide-open']");
  await win.click("[data-testid='obd-guide-open']");
  await win.waitForSelector("[data-testid='obd-guide-steps']");
  await win.locator("[data-testid='obd-guide-step']").first().click();
  await win.waitForSelector("[data-testid='obd-guide-step'][data-selected='1']");
  await win.fill("input[aria-label='检查备注']", "仿真观察备注");
  await win.click("[data-testid='obd-guide-result-normal']");
  await sleep(2000);
  const dump = await win.evaluate(() => ({
    progress: document.querySelector("[data-testid='obd-guide-progress']")?.textContent,
    err: document.querySelector("[data-testid='obd-guide'] .error")?.textContent,
    selected: document.querySelector("[data-testid='obd-guide-step'][data-selected='1']")?.disabled,
    btn: document.querySelector("[data-testid='obd-guide-result-normal']")?.getAttribute("disabled"),
  }));
  if (!String(dump.progress || "").includes("进度 1/")) throw new Error("guide step not saved " + JSON.stringify(dump));
  await win.click("[data-testid='obd-guide-fault-log']");
  await shot(app, "electron-guide.png");

  await runSimRead(win);

  await win.click("[data-obd-tab='compare']");
  await win.waitForFunction(() => document.querySelector("[data-testid='obd-compare-before']")?.querySelectorAll("option").length > 2, null, { timeout: 30_000 }).catch(async (e) => {
    console.log("compare failure", JSON.stringify(await win.evaluate(async () => ({ snaps: await window.porsche981.obdDiag({ op: "snapshot:list" }), body: document.body.innerText }))));
    throw e;
  });
  const ids = await win.$$eval("[data-testid='obd-compare-before'] option", (opts) => opts.map((o) => o.value).filter(Boolean));
  if (ids.length < 2) throw new Error("electron need two snapshots, got " + ids.join(","));
  await win.selectOption("[data-testid='obd-compare-before']", ids[ids.length - 1]);
  await win.selectOption("[data-testid='obd-compare-after']", ids[0]);
  await win.click("[data-testid='obd-compare-bind-before']");
  await win.waitForSelector("[data-testid='obd-compare-info']");
  await win.click("[data-testid='obd-compare-bind-after']");
  await win.click("[data-testid='obd-compare-run']");
  await win.waitForSelector("[data-testid='obd-compare-result']");
  const body = await win.textContent("[data-testid='obd-compare-result']");
  if (body.includes("已修好")) throw new Error("repaired wording " + body);
  await win.fill("[data-testid='obd-compare-note']", "二次仿真扫描对比");
  await win.click("[data-testid='obd-compare-save']");
  await win.waitForFunction(() => document.body.innerText.includes("已保存"));
  await win.click("[data-testid='obd-compare-fault-log']");
  await shot(app, "electron-compare.png");
} finally {
  await app.close();
}

app = await launchApp();
try {
  const win = await app.firstWindow({ timeout: 90_000 });
  await win.waitForSelector("[data-tab='obd']", { timeout: 60_000 });
  await win.click("[data-tab='obd']");
  await win.click("[data-obd-tab='guide']");
  await win.click("[data-testid='obd-guide-resume']");
  await win.waitForFunction(() => document.querySelector("[data-testid='obd-guide-progress']")?.textContent?.includes("进度 1/"));
  const g = await win.textContent("[data-testid='obd-guide-progress']");
  if (!g.includes("进度 1/")) throw new Error("guide not restored " + g);
  if (!g.includes("已关联台账")) throw new Error("guide log not restored " + g);
  await win.locator("[data-testid='obd-guide-step']").first().click();
  if (await win.inputValue("input[aria-label='检查备注']") !== "仿真观察备注") throw new Error("guide note not restored");
  await win.click("[data-obd-tab='compare']");
  await win.waitForSelector("[data-testid='obd-compare-open-saved']");
  await win.click("[data-testid='obd-compare-open-saved']");
  await win.waitForSelector("[data-testid='obd-compare-result']");
  if (await win.inputValue("[data-testid='obd-compare-note']") !== "二次仿真扫描对比") throw new Error("report note not restored");
  if (!(await win.textContent("[data-testid='obd-compare-result']")).includes("C447")) throw new Error("report raw DTC missing");
  if (!(await win.textContent("[data-testid='obd-compare-result']")).includes("模拟")) throw new Error("report source missing");
  await win.click("[data-testid='obd-compare-fault-log']");
  await win.waitForFunction(() => document.querySelector("[data-testid='obd-compare-info']")?.textContent?.includes("已关联台账"));
  await shot(app, "electron-reopen.png");
  console.log("PASS steuer14 electron: persist across restart, python simulation, temp db");
} finally {
  await app.close();
}
