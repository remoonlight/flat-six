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
const shotDir = path.join(root, ".local", "mxplus-support", "scratch", "ui");
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
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
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
  await page.waitForSelector("[data-testid='obd-conn-empty']");
  if (await page.getByRole("radio").count()) throw new Error("paired device without transport shown");
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

  for (const scenario of ["mx=1", "mx=1&unresolved=1"]) {
    await page.goto(`http://127.0.0.1:${port}/obd-connection-harness.html?${scenario}`, { waitUntil: "networkidle" });
    await page.click("[data-obd-tab='connection']");
    await page.click("[data-testid='obd-device-bt:000000000000']");
    if (scenario.includes("unresolved")) {
      await page.waitForFunction(() => document.querySelector("[data-testid='obd-conn-connect']")?.disabled === true);
      await page.selectOption("[data-testid='obd-model-pick']", "OBDLink MX+");
      await page.click("[data-testid='obd-conn-refresh']");
      if (await page.inputValue("[data-testid='obd-model-pick']") !== "OBDLink MX+") throw new Error("MX+ model not retained after refresh");
    } else {
      await page.locator("[data-testid='obd-conn-list'] strong").getByText("OBDLink MX+", { exact: true }).waitFor();
    }
    await page.click("[data-testid='obd-conn-connect']");
    await page.waitForFunction(() => (document.querySelector("[data-testid='obd-header-voltage']")?.textContent || "").includes("12.6"));
    await page.screenshot({ path: path.join(shotDir, scenario.includes("unresolved") ? "mx-manual-model.png" : "mx-connected.png") });
    await page.click("[data-testid='obd-conn-disconnect']");
    await page.waitForFunction(() => (document.querySelector("[data-testid='obd-header-voltage']")?.textContent || "").includes("--"));
    if (!(await page.isChecked("[data-testid='obd-device-bt:000000000000']"))) throw new Error("disconnect lost device selection");
    results.push(scenario.includes("unresolved") ? "mx-manual-model" : "mx-bluetooth");
  }
  if (errors.length) throw new Error(`browser errors: ${errors.join("; ")}`);

  for (const unpowered of [false, true]) {
    await page.goto(`http://127.0.0.1:${port}/obd-connection-harness.html?vnci=1${unpowered ? "&unpowered=1" : ""}`, { waitUntil: "networkidle" });
    await page.click("[data-obd-tab='connection']");
    await page.locator("[data-testid='obd-device-vnci:10001']").click();
    const list = await page.textContent("[data-testid='obd-conn-list']");
    if (!list.includes("序列号 10001") || list.includes("无 COM")) throw new Error("VNCI USB displayed as Bluetooth COM");
    await page.click("[data-testid='obd-conn-connect']");
    if (unpowered) {
      await page.getByTestId("obd-conn-note").getByText("VNCI 已识别，但 OBD 供电异常。", { exact: false }).waitFor();
      if (!(await page.textContent("[data-testid='obd-header-voltage']")).includes("--")) throw new Error("unpowered VNCI displayed a voltage");
    } else {
      await page.waitForFunction(() => (document.querySelector("[data-testid='obd-header-voltage']")?.textContent || "").includes("12.6"));
      await page.click("[data-testid='obd-conn-refresh']");
      if (!(await page.isChecked("[data-testid='obd-device-vnci:10001']"))) throw new Error("VNCI selection lost after refresh");
      await page.click("[data-testid='obd-conn-disconnect']");
      await page.waitForFunction(() => (document.querySelector("[data-testid='obd-header-voltage']")?.textContent || "").includes("--"));
    }
    await page.screenshot({ path: path.join(shotDir, unpowered ? "vnci-unpowered.png" : "vnci-disconnected.png") });
    results.push(unpowered ? "vnci-unpowered" : "vnci-usb");
  }
  if (errors.length) throw new Error(`browser errors: ${errors.join("; ")}`);

  await page.goto(`http://127.0.0.1:${port}/obd-connection-harness.html?registry=1`, { waitUntil: "networkidle" });
  await page.click("[data-obd-tab='connection']");
  const registry = page.getByTestId("obd-device-registry");
  await registry.waitFor();
  if (await page.locator("[data-testid='obd-conn-list'] > li").count() !== 1) throw new Error("only the current available supported head may be listed");
  const buttons = await page.getByTestId("obd-connection").getByRole("button").allTextContents();
  if (JSON.stringify(buttons) !== JSON.stringify(["刷新", "连接设备", "断开设备"])) throw new Error("unexpected connection controls " + buttons);
  for (const family of ["vLinker"]) {
    await page.getByTestId(`obd-registered-${family}`).waitFor();
  }
  for (const family of ["X431", "X431-tablet", "Espressif", "OBDLink MX+", "VNCI", "PT3G"]) {
    if (await page.getByTestId(`obd-registered-${family}`).count()) throw new Error(`hidden connection device shown: ${family}`);
  }
  if (!await page.getByTestId("obd-purpose-diagnostic").isChecked()) throw new Error("purpose was guessed");
  for (const network of ["drive", "chassis", "comfort", "crash"]) {
    await page.getByTestId(`obd-purpose-${network}`).check();
    await page.getByTestId("obd-conn-refresh").click();
    if (!await page.getByTestId(`obd-purpose-${network}`).isChecked()) throw new Error("CAN selection lost on refresh");
  }
  await page.getByTestId("obd-purpose-diagnostic").check();
  await page.getByTestId("obd-registered-vLinker").getByRole("radio").check();
  await page.waitForFunction(() => document.querySelector("input[name='obd-device']")?.checked);
  if (!(await page.getByTestId("obd-header-voltage").textContent()).includes("--")) throw new Error("selecting a registry device started hardware");
  if (await page.getByTestId("obd-registered-OBDLink MX+").getByRole("button", { name: "广播回放" }).count()) throw new Error("removed broadcast route shown");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(shotDir, "device-registry-mobile.png"), fullPage: true });
  const overflows = await registry.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
  if (overflows) throw new Error("device registry overflows on mobile");
  if (errors.length) throw new Error(`browser errors: ${errors.join("; ")}`);
  results.push("compact-deduplicated-devices, three-controls, excluded-reference-devices, current-available-only, manual-CAN, select-without-open, mobile");
  await browser.close();
} finally {
  vite.kill();
}

console.log("obd-connection-accept: ok", results.join(", "), shotDir);
