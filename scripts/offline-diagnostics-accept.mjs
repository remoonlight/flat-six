/**
 * Offline workbench accept: production build (no harness) + isolated Electron nav
 * + offline-test harness. Never touches .local/garage.db.
 * node scripts/offline-diagnostics-accept.mjs
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, _electron as electron } from "playwright";
import { GarageDb } from "../packages/db/dist/index.js";
import { runWorkbench } from "../apps/desktop/electron/offline-diagnostics.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const require = createRequire(path.join(appRoot, "electron", "main.mjs"));
const electronBin = require("electron");
const outDir = path.join(root, ".local", "x431-re", "2026-09-27-offline-completion", "desktop");
const testDbDir = path.join(outDir, "test-db");
const testDb = path.join(testDbDir, "garage.db");
fs.mkdirSync(outDir, { recursive: true });

const EU5 = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
const children = [];

function unusedPort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => {
      const addr = s.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      s.close((err) => (err ? reject(err) : resolve(port)));
    });
    s.on("error", reject);
  });
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
        await new Promise((r) => setTimeout(r, 250));
      }
    }
    reject(new Error("vite_not_ready"));
  });
}

function httpStatus(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (r) => {
      r.resume();
      resolve(r.statusCode || 0);
    });
    req.on("error", reject);
  });
}

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
      else resolve({ out, err });
    });
  });
}

function shutdown() {
  for (const c of children) {
    try {
      if (c.pid) process.kill(c.pid);
    } catch {
      /* */
    }
  }
}

const mockSource = `window.__OFFLINE_DIAG_MOCK__ = async (req) => {
  const flags = { executionEnabled: false, liveVerified: false, writePayload: null };
  const menuEcus = Array.from({ length: 35 }, (_, i) => ({ ecuId: i === 0 ? 1 : i + 1, label: i === 0 ? "DME" : "ECU " + (i + 1) }));
  if (req.action === "summary") {
    return { ok: true, ...flags, menuEcuCount: 35, menuEcus, protocolInventory: { present: true, ready: true, counts: { streamedVariants: 2, menuGroups: 35 }, groups: menuEcus }, valueSupport: { present: true, ready: true, decodeCounts: { coding: { supported: 1 } } }, manualEvidence: { present: true, dtcEntryCount: 1 }, protocolNotReady: false, valueSupportNotReady: false };
  }
  if (req.action === "variants") {
    const all = [{ profileId: "${EU5}", name: "SDI9_1_981_3_4L_EU5", membership: "confirmed", module: "DME_BDE_Continental", ecuIds: [1] }];
    const items = req.generation === "982" ? [] : all;
    return { ok: true, ...flags, present: true, total: items.length, items };
  }
  if (req.action === "records") {
    if (req.generation === "982") return { ok: false, error: "generation_profile_mismatch", ...flags };
    const recs = [
      { at: 4047899, category: "coding", displayName: "巡航控制", unit: null, labelSource: "present", enumSource: "present", requestCandidate: { disabled: true, payloadHex: "22" } },
      { at: 9, category: "dtc", displayName: "P000A", dtc: { code: "P000A", textStatus: "present" }, manual: { exactHits: [], relatedBaseCodeHits: [{ relation: "base_code_only", rawCode: "P000A00", docName: "Boxster.pdf", pages: [3841], bodyEvidenceStatus: "body", bodyApplicability: "boxster", caymanBody: true, needs: [{ sourceLabel: "诊断条", itemsPreview: ["发动机转速> 608"], uncertainGlyph: true }] }] } },
    ];
    const items = recs.filter((r) => r.category === req.category && (!req.search || JSON.stringify(r).includes(req.search)));
    return { ok: true, ...flags, total: items.length, items, systemEvidence: { registry: { locator: "981.pdf", pages: [13], wm: "033500" } } };
  }
  if (req.action === "decode") {
    await new Promise((r) => setTimeout(r, 350));
    return req.dataHex === "01" ? { ok: true, ...flags, text: "是" } : { ok: false, error: "missing_binary", ...flags };
  }
  if (req.action === "preview") return req.dataHex === "A5" && req.rawValue === 0 ? { ok: true, ...flags, beforeHex: "A5", afterHex: "A4", changedBitMaskHex: "01" } : { ok: false, error: "preview_failed", ...flags };
  if (req.action === "match") return { ok: true, ...flags, identityQualification: { status: req.identity?.generation === "982" ? "wrong_generation" : "incomplete" } };
  if (req.action === "plan") return { ok: true, ...flags, groupCount: 35, sourceEvidence: {}, carChecklist: { vehicleActionsDisabled: true } };
  if (req.action === "replay") return { ok: true, ...flags, summary: { realCount: 0, syntheticCount: 8, vinRedacted: true } };
  return { ok: false, error: "invalid_action", ...flags };
};`;

async function openHarness(browser, url, extraInit) {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on("pageerror", (err) => consoleErrors.push(err.stack || String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  await page.addInitScript(() => {
    window.__consoleErrors = [];
    window.addEventListener("error", (e) => window.__consoleErrors.push(String(e.error?.stack || e.message)));
  });
  if (extraInit) await extraInit(page);
  await page.goto(url, { waitUntil: "domcontentloaded" });
  try {
    await page.waitForSelector("[data-page='offline-diagnostics']", { timeout: 30_000 });
  } catch (e) {
    const boot = await page.evaluate(() => ({
      text: document.body.innerText.slice(0, 800),
      html: document.documentElement.outerHTML.slice(0, 1500),
      errors: window.__consoleErrors || [],
    }));
    fs.writeFileSync(
      path.join(outDir, "boot.json"),
      JSON.stringify({ boot, consoleErrors, error: String(e) }, null, 2),
    );
    throw new Error(`harness_boot: ${consoleErrors.join(" | ") || boot.text || e}`);
  }
  return { page, consoleErrors };
}

async function clickFlow(page, { expectReplay, expectVariantsMin, real, staleDecode }) {
  await page.waitForFunction(() => document.querySelectorAll("[data-testid='od-ecu'] option").length >= 36);
  const ecuOpts = await page.locator("[data-testid='od-ecu'] option").count();
  if (ecuOpts < 36) throw new Error(`ecu options ${ecuOpts}`);
  await page.selectOption("[data-testid='od-generation']", "982");
  await page.waitForSelector("[data-testid='od-variant-total']");
  if (real) {
    const txt = await page.textContent("[data-testid='od-variant-total']");
    if (txt.includes("SDI9_1_981_3_4L_EU5")) throw new Error("982 listed 981 EU5");
  }
  await page.selectOption("[data-testid='od-generation']", "981");
  await page.selectOption("[data-testid='od-ecu']", "1");
  await page.fill("[data-testid='od-variant-search']", "EU5");
  await page.click("text=筛选");
  await page.waitForSelector("text=SDI9_1_981_3_4L_EU5");
  const member = await page.textContent("[data-testid='od-membership']");
  if (!member.includes("源车型名称已确认") || member.includes("实车核验")) {
    throw new Error(`membership ${member}`);
  }
  const total = await page.textContent("[data-testid='od-variant-total']");
  if (expectVariantsMin && !/\d+ 条/.test(total || "")) throw new Error(`variant total ${total}`);
  await page.click("text=SDI9_1_981_3_4L_EU5");
  await page.selectOption("[data-testid='od-category']", "dtc");
  await page.fill("[data-testid='od-record-search']", "P000A");
  await page.click("text=查询");
  await page.waitForSelector("[data-testid='od-record-list'] li button");
  await page.locator("[data-testid='od-record-list'] li button").first().click();
  await page.locator("[data-testid='od-manual']").waitFor();
  const man = await page.textContent("[data-testid='od-manual']");
  if (!man.includes("3841") || !man.includes("P000A00")) throw new Error(`manual ${man}`);
  if (real && !man.includes("Cayman")) throw new Error(`cayman ${man}`);
  if (real) await page.waitForSelector("[data-testid='od-system-evidence']");
  await page.selectOption("[data-testid='od-category']", "coding");
  await page.fill("[data-testid='od-record-search']", "4047899");
  await page.click("text=查询");
  const cruise = page.locator("[data-testid='od-record-list'] li button", { hasText: "巡航" });
  await cruise.first().waitFor();
  await cruise.first().click();
  await page.locator("[data-testid='od-selected']").getByText("巡航").waitFor();
  const codingMan = await page.textContent("[data-testid='od-manual']");
  if (codingMan.includes("无精确 rawCode")) throw new Error(`coding manual ${codingMan}`);
  const reqCand = await page.textContent("[data-testid='od-request-candidate']");
  if (!reqCand.includes("禁用") || reqCand.includes("{")) throw new Error(`request ${reqCand}`);
  await page.fill("[data-testid='od-decode-hex']", "01");
  if (staleDecode) {
    await page.click("[data-testid='od-decode']");
    await page.selectOption("[data-testid='od-generation']", "982");
    await page.waitForTimeout(500);
    const leftover = await page.locator("[data-testid='od-decode-out']").count();
    if (leftover) throw new Error("stale decode survived generation change");
    await page.selectOption("[data-testid='od-generation']", "981");
    await page.selectOption("[data-testid='od-ecu']", "1");
    await page.fill("[data-testid='od-variant-search']", "EU5");
    await page.click("text=筛选");
    await page.waitForSelector("text=SDI9_1_981_3_4L_EU5");
    await page.click("text=SDI9_1_981_3_4L_EU5");
    await page.selectOption("[data-testid='od-category']", "coding");
    await page.fill("[data-testid='od-record-search']", "4047899");
    await page.click("text=查询");
    const cruise2 = page.locator("[data-testid='od-record-list'] li button", { hasText: "巡航" });
    await cruise2.first().waitFor();
    await cruise2.first().click();
    await page.fill("[data-testid='od-decode-hex']", "01");
  }
  await page.click("[data-testid='od-decode']");
  await page.waitForSelector("[data-testid='od-decode-out']");
  const decodeText = await page.textContent("[data-testid='od-decode-out']");
  if (!decodeText.includes("是")) throw new Error(`decode ${decodeText}`);
  await page.fill("[data-testid='od-preview-hex']", "A5");
  await page.fill("[data-testid='od-preview-raw']", "1.5");
  await page.click("[data-testid='od-preview']");
  await page.waitForSelector("[data-testid='od-error']");
  const rawErr = await page.textContent("[data-testid='od-error']");
  if (!rawErr.includes("整数")) throw new Error(`raw ${rawErr}`);
  await page.fill("[data-testid='od-preview-raw']", "0");
  await page.click("[data-testid='od-preview']");
  await page.waitForSelector("[data-testid='od-preview-out']");
  const after = await page.textContent("[data-testid='od-preview-out']");
  if (!after.includes("A4")) throw new Error(`preview ${after}`);
  await page.click("text=填入示例");
  await page.click("[data-testid='od-match']");
  await page.waitForSelector("[data-testid='od-match-out']");
  await page.click("[data-testid='od-plan']");
  await page.waitForSelector("[data-testid='od-plan-out']");
  const planText = await page.textContent("[data-testid='od-plan-out']");
  if (!planText.includes("35")) throw new Error(`plan ${planText}`);
  await page.click("[data-testid='od-replay']");
  await page.waitForSelector("[data-testid='od-replay-out']");
  const replayText = await page.textContent("[data-testid='od-replay-out']");
  if (expectReplay) {
    if (!replayText.includes("17") || !replayText.includes("17")) throw new Error(`replay ${replayText}`);
  }
}

function seedTestDb() {
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
  if (path.normalize(testDb).toLowerCase().includes(`${path.sep}garage.db`) && !testDb.includes("test-db")) {
    throw new Error("refused production garage.db");
  }
}

async function acceptElectronNav() {
  seedTestDb();
  const app = await electron.launch({
    executablePath: electronBin,
    args: [appRoot],
    env: {
      ...process.env,
      PORSCHE981_DB: testDb,
      PORSCHE981_HEADLESS: "1",
      PORSCHE981_DEVTOOLS: "",
      VITE_DEV_SERVER_URL: "",
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
    },
    timeout: 90_000,
  });
  try {
    const page = await app.firstWindow({ timeout: 90_000 });
    await page.waitForSelector("[data-tab='obd']", { timeout: 60_000 });
    await page.click("[data-tab='obd']");
    await page.waitForSelector("[data-page='obd']");
    await page.click("[data-obd-tab='faults']");
    await page.waitForSelector("[data-page='obd']");
    await page.click("[data-obd-tab='coding']");
    await page.waitForSelector("[data-page='coding']");
    await page.click("[data-obd-tab='offline']");
    await page.waitForSelector("[data-page='offline-diagnostics']", { timeout: 60_000 });
    const bound = await page.evaluate(() => typeof window.porsche981?.offlineDiagnostics === "function");
    if (!bound) throw new Error("preload offlineDiagnostics missing");
    const summary = await page.evaluate(() => window.porsche981.offlineDiagnostics({ action: "summary" }));
    if (!summary?.ok || summary.menuEcuCount !== 35) throw new Error(`ipc summary ${JSON.stringify(summary).slice(0, 240)}`);
    await page.waitForFunction(() => document.querySelectorAll("[data-testid='od-ecu'] option").length >= 36, null, {
      timeout: 60_000,
    });
    try {
      await page.screenshot({
        path: path.join(outDir, "offline-workbench-electron-nav.png"),
        timeout: 8_000,
        animations: "disabled",
      });
    } catch {
      fs.writeFileSync(path.join(outDir, "electron-nav-screenshot.txt"), "screenshot_timeout_after_nav_ok");
    }
    fs.writeFileSync(
      path.join(outDir, "electron-nav.json"),
      JSON.stringify({ db: testDb, menuEcuCount: summary.menuEcuCount, bound: true }, null, 2),
    );
  } finally {
    await app.close();
  }
}

async function preview(modeLabel, port, check) {
  const child = spawn(
    process.execPath,
    [
      path.join(root, "node_modules", "vite", "bin", "vite.js"),
      "preview",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    { cwd: appRoot, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, BROWSER: "none" } },
  );
  children.push(child);
  fs.writeFileSync(path.join(outDir, `vite-${modeLabel}.pid`), String(child.pid));
  let viteErr = "";
  child.stderr.on("data", (c) => {
    viteErr += c.toString();
  });
  try {
    await check(child, viteErr);
  } finally {
    try {
      if (child.pid) process.kill(child.pid);
    } catch {
      /* */
    }
  }
}

let browser;
try {
  await run(process.execPath, [path.join(root, "node_modules", "vite", "bin", "vite.js"), "build"], {
    cwd: appRoot,
  });
  if (fs.existsSync(path.join(appRoot, "dist", "offline-diagnostics-harness.html"))) {
    throw new Error("production build contains offline harness");
  }
  const prodPort = await unusedPort();
  await preview("production", prodPort, async () => {
    await waitHttp(`http://127.0.0.1:${prodPort}/index.html`);
    const html = await new Promise((resolve, reject) => {
      http
        .get(`http://127.0.0.1:${prodPort}/offline-diagnostics-harness.html`, (r) => {
          let body = "";
          r.setEncoding("utf8");
          r.on("data", (c) => {
            body += c;
          });
          r.on("end", () => resolve(body));
        })
        .on("error", reject);
    });
    if (html.includes("离线工作台测试夹具") || html.includes("offline-harness")) {
      throw new Error("production preview served harness entry");
    }
  });

  await acceptElectronNav();

  await run(
    process.execPath,
    [path.join(root, "node_modules", "vite", "bin", "vite.js"), "build", "--mode", "offline-test"],
    { cwd: appRoot },
  );
  if (!fs.existsSync(path.join(appRoot, "dist", "offline-diagnostics-harness.html"))) {
    throw new Error("offline-test build missing harness");
  }

  const port = await unusedPort();
  const vite = spawn(
    process.execPath,
    [
      path.join(root, "node_modules", "vite", "bin", "vite.js"),
      "preview",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    { cwd: appRoot, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, BROWSER: "none" } },
  );
  children.push(vite);
  fs.writeFileSync(path.join(outDir, "vite.pid"), String(vite.pid));
  fs.writeFileSync(path.join(outDir, "vite.port"), String(port));
  let viteErr = "";
  vite.stderr.on("data", (c) => {
    viteErr += c.toString();
  });
  try {
    await waitHttp(`http://127.0.0.1:${port}/offline-diagnostics-harness.html`);
  } catch (e) {
    throw new Error(`vite failed pid=${vite.pid}: ${e} ${viteErr}`);
  }

  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const harness = `http://127.0.0.1:${port}/offline-diagnostics-harness.html`;

  const mock = await openHarness(browser, `${harness}?offline-diag-fixture=1`, async (page) => {
    await page.addInitScript({ content: mockSource });
  });
  await clickFlow(mock.page, { expectReplay: false, expectVariantsMin: true, real: false, staleDecode: true });
  await mock.page.screenshot({ path: path.join(outDir, "offline-workbench-mock.png"), fullPage: true });
  fs.writeFileSync(path.join(outDir, "console-mock.json"), JSON.stringify({ errors: mock.consoleErrors }, null, 2));
  await mock.page.close();

  const real = await openHarness(browser, `${harness}?offline-diag-fixture=1`, async (page) => {
    await page.exposeFunction("__offlineWorkbench", (req) =>
      runWorkbench(req, { repoRoot: root, timeoutMs: 180_000 }),
    );
    await page.addInitScript(() => {
      window.__OFFLINE_DIAG_MOCK__ = (req) => window.__offlineWorkbench(req);
    });
  });
  await clickFlow(real.page, { expectReplay: true, expectVariantsMin: true, real: true, staleDecode: false });
  const status = await real.page.textContent("[data-testid='od-source-status']");
  if (!status.includes("35") || !status.includes("291") || !status.includes("条目")) throw new Error(`status ${status}`);
  await real.page.screenshot({ path: path.join(outDir, "offline-workbench.png"), fullPage: true });
  fs.writeFileSync(path.join(outDir, "console.json"), JSON.stringify({ errors: real.consoleErrors }, null, 2));
  const fatal = [...mock.consoleErrors, ...real.consoleErrors].filter(
    (e) => !/favicon|React DevTools|porsche981 API unavailable|Failed to load resource|404/i.test(e),
  );
  if (fatal.length) throw new Error(`console errors: ${fatal.join(" | ")}`);
  await real.page.close();
  console.log("offline-diagnostics-accept: ok");
  console.log("pid", vite.pid, "port", port);
  console.log("screenshots", path.join(outDir, "offline-workbench.png"));
} finally {
  if (browser) await browser.close().catch(() => {});
  shutdown();
}
