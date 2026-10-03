/**
 * Topology UI accept: logic selfcheck + Playwright harness + production dist check.
 * Deny live. Never touches .local/garage.db.
 * node scripts/topology-ui-accept.mjs
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";
import { chromium, _electron as electron } from "playwright";
import { createReadOnlySessionManager } from "../apps/desktop/electron/read-only-session.mjs";
import { classifySessionFinal } from "../apps/desktop/src/can-topology-logic.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const shotDir = path.join(root, ".local", "obd-topology", "ui");
const require = createRequire(path.join(appRoot, "electron", "main.mjs"));
const electronBin = require("electron");

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

  async function assertMapFits(surface, tag) {
    await surface.evaluate((label) => {
      const panel = document.querySelector(".topo-map-panel");
      const map = document.querySelector("[data-testid='topo-diagram']");
      if (panel.scrollWidth > panel.clientWidth + 1 || map.scrollWidth > map.clientWidth + 1) throw new Error(label + " horizontal map overflow");
      const bounds = panel.getBoundingClientRect();
      for (const chip of map.querySelectorAll(".topo-chip")) {
        const b = chip.getBoundingClientRect();
        if (b.left < bounds.left || b.right > bounds.right || chip.scrollWidth > chip.clientWidth + 1) throw new Error(label + " clipped node " + chip.textContent);
      }
    }, tag);
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

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const envDeny = {
  ...process.env,
  PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_HEADLESS: "1",
  PW_TEST_SCREENSHOT_NO_FONTS_READY: "1",
};

const logic = await run(process.execPath, [path.join(appRoot, "src", "can-topology-logic.selfcheck.mjs")], {
  cwd: root,
  env: envDeny,
});
console.log("logic", logic.out.trim());

const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");
const port = 5187;
const vite = spawn(process.execPath, [viteBin, "--mode", "topology-test", "--port", String(port), "--strictPort", "--host", "127.0.0.1"], {
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
  await waitHttp(`http://127.0.0.1:${port}/topology-harness.html`);
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`http://127.0.0.1:${port}/topology-harness.html`, { waitUntil: "networkidle" });
  await page.waitForSelector("[data-page='topology']");
  const tab = await page.getAttribute("[data-obd-tab='topology']", "class");
  if (!tab?.includes("active")) throw new Error("topology tab not default");
  if (!(await page.locator("[data-testid='topo-trunk']").count())) throw new Error("missing trunk");
  if (!(await page.locator("[data-testid='topo-rail-drive']").count())) throw new Error("missing drive rail");
  const kls = await page.locator("[data-testid='topo-node-steering-column']").count();
  if (kls < 2) throw new Error(`steering-column appearances ${kls}`);
  if (!(await page.locator("[data-testid='topo-node-shaker']").count())) throw new Error("missing union shaker");
  if (await page.locator("[data-testid='topo-generation']").count()) throw new Error("generation selector visible");
  if (await page.locator("[data-testid='topo-search']").count()) throw new Error("search visible");
  if (await page.locator("[data-testid='topo-mode']").count()) throw new Error("mode visible");
  if (await page.locator("[data-testid='topo-scenario']").count()) throw new Error("scenario visible");
  if (await page.locator("[data-testid='topo-scan-adapted']").count()) throw new Error("old scan visible");
  if (await page.locator("[data-testid='topo-overview']").count()) throw new Error("removed vehicle heading visible");
  if ((await page.textContent("body")).includes("未连接")) throw new Error("hard-coded unconnected");

  const headerIdle = await page.textContent("[data-testid='obd-task-status']");
  if (!headerIdle.includes("空闲")) throw new Error(`header idle ${headerIdle}`);

  function assertSwitchAboveDetail(tag) {
    return page.evaluate((label) => {
      const sw = document.querySelector("[data-testid='topo-view']");
      const detail = document.querySelector("[data-testid='topo-detail']");
      const col = document.querySelector("[data-testid='topo-detail-col']");
      const map = document.querySelector("[data-testid='topo-diagram']");
      if (!sw || !detail || !col) throw new Error(label + " missing");
      if (!col.contains(sw) || !col.contains(detail)) throw new Error(label + " switch not in detail column");
      const sb = sw.getBoundingClientRect();
      const db = detail.getBoundingClientRect();
      if (sb.bottom - db.top > 2) throw new Error(label + ` switch not above detail ${sb.bottom} ${db.top}`);
      if (Math.abs(sb.left - db.left) > 80) throw new Error(label + ` switch not aligned to detail ${sb.left} ${db.left}`);
      if (map) {
        const mb = map.getBoundingClientRect();
        const stacked = mb.bottom <= sb.top + 8;
        if (!stacked && sb.left < mb.right - 40 && sb.right < mb.right) {
          throw new Error(label + ` switch still over map ${JSON.stringify({ sb, mb })}`);
        }
      }
    }, tag);
  }
  await assertSwitchAboveDetail("wide");
  await assertMapFits(page, "wide");
  await page.screenshot({ path: path.join(shotDir, "topology-diagram.png"), fullPage: true });
  await page.setViewportSize({ width: 900, height: 1000 });
  await sleep(200);
  await assertSwitchAboveDetail("narrow");
  await assertMapFits(page, "narrow");
  await page.screenshot({ path: path.join(shotDir, "topology-diagram-narrow.png"), fullPage: true });
  for (const width of [1100, 800, 700, 600]) {
    await page.setViewportSize({ width, height: 1000 });
    await assertMapFits(page, `width ${width}`);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });

  const dmeKind = await page.getAttribute("[data-testid='topo-node-dme']", "data-kind");
  const gwActions = await page.locator("[data-testid='topo-detail'] button").allTextContents();
  if (JSON.stringify(gwActions) !== JSON.stringify(["读取所有单元故障码", "清除所有单元故障码"])) throw new Error(`GW actions ${gwActions}`);
  if (await page.locator("[data-testid='topo-detail'] dl").count()) throw new Error("GW detail fields still visible");
  if (!(await page.textContent("[data-testid='topo-detail'] h3")).includes("GW")) throw new Error("GW module name missing");
  const removedNotes = ["仅读取 GW、DME 的身份与故障码；其他系统跳过。", "此模块尚未接入车辆诊断，可先查看离线资料。"];
  const documentBody = await page.textContent("body");
  if (removedNotes.some((note) => (documentBody || "").includes(note))) throw new Error("removed topology note visible");
  if ((await page.getAttribute("[data-testid='topo-node-shaker']", "data-reference")) !== "1") throw new Error("982 reference missing");
  if (dmeKind !== "unscanned") throw new Error(`dme default ${dmeKind}`);
  const pdkText = await page.locator("[data-testid='topo-node-pdk'] .sub").first().textContent();
  if (!pdkText.includes("待适配")) throw new Error(`pdk ${pdkText}`);

  const startsBeforeVisit = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  if (await page.locator("[data-testid='topo-obd']").count()) throw new Error("removed OBD node still visible");
  await page.locator("[data-testid='topo-node-dme']").first().click();
  const detail = await page.textContent("[data-testid='topo-detail']");
  if (!detail.includes("实时数据") || !detail.includes("读取故障码") || !detail.includes("清除故障码") || !detail.includes("DME")) {
    throw new Error(`detail ${detail}`);
  }
  if (detail.includes("porsche-981-2014-dme") || detail.includes("981.pdf") || detail.includes("会话档案")) {
    throw new Error(`research detail leaked ${detail}`);
  }
  if (detail.includes("982 仅") || detail.includes("车型差异")) throw new Error("model-diff in detail");
  const startsAfterSelect = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  if (startsAfterSelect !== startsBeforeVisit) throw new Error("select issued start");

  await page.click("[data-testid='topo-view-list']");
  await page.waitForSelector("[data-testid='topo-list']");
  const listDetail = await page.textContent("[data-testid='topo-detail']");
  if (!listDetail.includes("DME") || !listDetail.includes("读取故障码")) throw new Error("selection not preserved");
  await page.screenshot({ path: path.join(shotDir, "topology-list.png"), fullPage: true });
  await page.click("[data-testid='topo-view-diagram']");
  await page.waitForSelector("[data-testid='topo-diagram']");

  const startsBeforeRead = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  await page.click("[data-testid='topo-read-selected']");
  if (await page.locator("[data-testid='topo-confirm']").count() || await page.locator("[data-testid='topo-cancel']").count()) throw new Error("read confirmation/cancel still visible");
  await page.waitForFunction(() => document.querySelector("[data-testid='topo-node-dme']")?.getAttribute("data-kind") === "dtc-present");
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-read-selected']").disabled);
  const startsAfterRead = await page.evaluate(() => window.__FAKE_SESSION_CALLS__.filter((c) => c.action === "start").length);
  if (startsAfterRead - startsBeforeRead !== 1) throw new Error("single click did not execute exactly one read");
  await page.locator("[data-testid='topo-node-gateway']").first().click();
  await page.click("[data-testid='topo-read-all']");
  await page.waitForFunction(() => {
    const dme = document.querySelector("[data-testid='topo-node-dme']");
    const gw = document.querySelector("[data-testid='topo-node-gateway']");
    return dme?.getAttribute("data-kind") === "dtc-present" && gw?.getAttribute("data-kind") === "no-dtc";
  });
  const readReq = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).find((c) => c && c.action === "start" && c.sessionTask === "read"),
  );
  if (!readReq || readReq.confirmedReadOnly !== true || readReq.x431Inactive !== true || readReq.confirmedClearDtc === true) {
    throw new Error(`read flags ${JSON.stringify(readReq)}`);
  }
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-read-all']").disabled);
  const batchCalls = () => page.evaluate(() => window.__FAKE_SESSION_CALLS__.filter((c) => c.action === "start"));
  const beforeBatchClear = await batchCalls();
  await page.click("[data-testid='topo-clear-all']");
  if (await page.locator("[data-testid='topo-confirm']").count() || await page.locator("[data-testid='topo-cancel']").count()) throw new Error("clear confirmation/cancel still visible");
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-clear-all']").disabled && document.querySelector("[data-testid='topo-progress']")?.textContent.includes("成功 2"));
  const batchClear = (await batchCalls()).slice(beforeBatchClear.length);
  if (batchClear.length !== 2 || batchClear[0].profileId !== "porsche-981-2014-gateway" || batchClear[1].profileId !== "porsche-981-2014-dme" || batchClear.some((c) => c.sessionTask !== "clear" || c.confirmedClearDtc !== true || c.confirmedReadOnly === true || c.x431Inactive !== true)) throw new Error(`batch clear sequence/flags ${JSON.stringify(batchClear)}`);
  await page.evaluate(() => { window.__TOPO_SCENARIO__ = "disconnect"; });
  const beforeBatchFailure = (await batchCalls()).length;
  await page.click("[data-testid='topo-clear-all']");
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-clear-all']").disabled && document.querySelector("[data-testid='topo-progress']")?.textContent.includes("失败 1"));
  if ((await batchCalls()).length - beforeBatchFailure !== 1 || !(await page.textContent("[data-testid='topo-progress']")).includes("未处理 1")) throw new Error("failed batch continued to next ECU");
  await page.evaluate(() => { window.__TOPO_SCENARIO__ = "success"; });
  await page.click("[data-testid='topo-read-all']");
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-read-all']").disabled);
  await page.locator("[data-testid='topo-node-dme']").first().click();
  const clearCountBeforeRequest = (await batchCalls()).filter((c) => c.sessionTask === "clear").length;
  await page.click("[data-testid='topo-clear-selected']");
  if (await page.locator("[data-testid='topo-confirm']").count()) throw new Error("single clear confirmation still visible");
  await page.waitForFunction(() => document.querySelector("[data-testid='topo-node-dme']")?.getAttribute("data-kind") === "no-dtc");
  if ((await batchCalls()).filter((c) => c.sessionTask === "clear").length - clearCountBeforeRequest !== 1) throw new Error("single clear did not execute exactly once");
  const clearFlag = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).find((c) => c && c.action === "start" && c.sessionTask === "clear"),
  );
  if (!clearFlag || clearFlag.confirmedClearDtc !== true || clearFlag.confirmedReadOnly === true || clearFlag.x431Inactive !== true) {
    throw new Error(`clear flags ${JSON.stringify(clearFlag)}`);
  }

  await page.click("[data-testid='topo-read-selected']");
  await page.waitForFunction(() => {
    const dme = document.querySelector("[data-testid='topo-node-dme']");
    const gw = document.querySelector("[data-testid='topo-node-gateway']");
    return dme?.getAttribute("data-kind") === "dtc-present" && gw?.getAttribute("data-kind") === "no-dtc";
  });
  const dmeSt = await page.locator("[data-testid='topo-node-dme'] .sub").first().textContent();
  if (!dmeSt.includes("有故障码")) throw new Error(`dme scanned ${dmeSt}`);
  const gwSt = await page.locator("[data-testid='topo-node-gateway'] .sub").first().textContent();
  if (!gwSt.includes("无故障码")) throw new Error(`gw scanned ${gwSt}`);
  await page.locator("[data-testid='topo-node-dme']").first().click();
  const dmeDetail = await page.textContent("[data-testid='topo-detail']");
  if (!dmeDetail.includes("U0447") || !dmeDetail.includes("U0412")) throw new Error(`dtc labels ${dmeDetail}`);
  const dmeBadge = await page.locator("[data-testid='topo-node-dme'] .badge").first().textContent();
  if (dmeBadge !== "2") throw new Error(`badge ${dmeBadge}`);
  await page.screenshot({ path: path.join(shotDir, "topology-scanned.png"), fullPage: true });
  const readStarts = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start" && c.sessionTask !== "clear").length,
  );
  if (readStarts < 3) throw new Error(`read starts ${readStarts}`);

  await page.evaluate(() => {
    window.__TOPO_SCENARIO__ = "disconnect";
  });
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.click("[data-testid='topo-clear-selected']");
  await page.waitForFunction(() => {
    const dme = document.querySelector("[data-testid='topo-node-dme']");
    return dme?.getAttribute("data-stale") === "1" && dme.querySelector(".badge")?.textContent === "2";
  });
  const staleDetail = await page.textContent("[data-testid='topo-detail']");
  if (!staleDetail.includes("上次读取") || staleDetail.includes("无故障码")) throw new Error(`stale detail ${staleDetail}`);
  const prog = await page.textContent("[data-testid='topo-progress']");
  if (!prog.includes("失败") || !prog.includes("通讯中断") || !prog.includes("未适配")) throw new Error(`progress ${prog}`);
  await page.evaluate(() => {
    window.__TOPO_SCENARIO__ = "success";
  });

  await page.click("[data-obd-tab='live']");
  const headerOther = await page.textContent("[data-testid='obd-task-status']");
  if (!headerOther.includes("空闲") && !headerOther.includes("运行中") && !headerOther.includes("离线")) {
    throw new Error(`header other tab ${headerOther}`);
  }
  await page.click("[data-obd-tab='topology']");

  if (await page.locator("[data-testid='topo-clear-all']").count()) throw new Error("batch clear visible outside GW");
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.click("[data-testid='topo-clear-selected']");
  await page.waitForFunction(() => {
    const dme = document.querySelector("[data-testid='topo-node-dme']");
    return dme?.getAttribute("data-kind") === "no-dtc";
  });
  const clearStarts = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start" && c.sessionTask === "clear").length,
  );
  if (clearStarts < 3) throw new Error(`clear starts ${clearStarts}`);
  const clearReq = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).find((c) => c && c.action === "start" && c.sessionTask === "clear"),
  );
  if (clearReq && clearReq.confirmedReadOnly === true) throw new Error("clear sent confirmedReadOnly");

  await page.locator("[data-testid='topo-node-pdk']").first().click();
  const pdkDetail = await page.textContent("[data-testid='topo-detail']");
  if (!pdkDetail.includes("PDK") || !(await page.isDisabled("[data-testid='topo-read-selected']"))) throw new Error(`pdk detail/actions ${pdkDetail}`);
  const pdkCalls = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  await page.click("[data-testid='topo-read-selected']", { force: true }).catch(() => {});
  const pdkCallsAfter = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  if (pdkCallsAfter !== pdkCalls) throw new Error("unsupported node sent");

  await page.evaluate(() => {
    window.porsche981.obdConnection = async () => ({ ok: true, model: "VNCI", connected: false,
      executionEnabled: false, liveVerified: false, writePayload: null });
  });
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.waitForFunction(() => document.querySelector("[data-testid='topo-clear-selected']")?.disabled);
  if (!(await page.isDisabled("[data-testid='topo-clear-selected']"))) throw new Error("VNCI clear enabled");
  if (await page.isDisabled("[data-testid='topo-read-selected']")) throw new Error("VNCI named read disabled");
  await page.locator("[data-testid='topo-node-gateway']").first().click();
  if (!(await page.isDisabled("[data-testid='topo-clear-all']")) || await page.isDisabled("[data-testid='topo-read-all']")) throw new Error("VNCI batch gate incorrect");
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.evaluate(() => {
    window.porsche981.obdConnection = async () => ({ ok: true, model: "vLinker", connected: false,
      executionEnabled: false, liveVerified: false, writePayload: null });
  });
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-clear-selected']").disabled);

  await page.evaluate(() => { window.__TOPO_SCENARIO__ = "slow"; window.__TOPO_DELAY_START_MS__ = 1800; });
  const beforeBatchRead = await page.evaluate(() => window.__FAKE_SESSION_CALLS__.filter((c) => c.action === "start").length);
  await page.locator("[data-testid='topo-node-gateway']").first().click();
  await page.click("[data-testid='topo-read-all']");
  await page.waitForFunction(() => document.querySelector("[data-testid='topo-read-all']")?.disabled);
  if (await page.locator("[data-testid='topo-confirm']").count() || await page.locator("[data-testid='topo-cancel']").count()) throw new Error("batch read confirmation/cancel still visible");
  await page.waitForFunction(() => !document.querySelector("[data-testid='topo-read-all']").disabled);
  const afterBatchRead = await page.evaluate(() => window.__FAKE_SESSION_CALLS__.filter((c) => c.action === "start").length);
  if (afterBatchRead - beforeBatchRead !== 2 || !(await page.textContent("[data-testid='topo-progress']")).includes("成功 2")) throw new Error("batch read did not finish both units");

  await page.evaluate(() => {
    window.__TOPO_SCENARIO__ = "slow";
    window.__TOPO_DELAY_START_MS__ = 400;
  });
  const startsBeforeDup = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  await page.locator("[data-testid='topo-node-dme']").first().click();
  await page.click("[data-testid='topo-read-selected']");
  await page.click("[data-testid='topo-read-selected']", { force: true });
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-task-status']")?.getAttribute("data-state") === "running");
  const startsDuring = await page.evaluate(
    () => (window.__FAKE_SESSION_CALLS__ || []).filter((c) => c && c.action === "start").length,
  );
  if (startsDuring - startsBeforeDup !== 1) throw new Error(`duplicate-click starts ${startsDuring - startsBeforeDup}`);
  if (!(await page.isDisabled("[data-obd-tab='connection']"))) throw new Error("connection tab enabled during topology capture");
  const runningAcross = await page.getAttribute("[data-testid='obd-task-status']", "data-state");
  if (runningAcross !== "running") throw new Error(`running lost across tabs ${runningAcross}`);
  await page.click("[data-obd-tab='topology']");
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-task-status']")?.getAttribute("data-state") === "idle", null, {
    timeout: 30_000,
  });

  await page.evaluate(() => {
    window.__TOPO_SCENARIO__ = "success";
    window.__TOPO_DELAY_START_MS__ = 0;
    window.__FAKE_OVERVIEW__ = { ok: false };
  });
  await page.waitForFunction(() => document.querySelector("[data-testid='obd-task-status']")?.getAttribute("data-state") === "offline", null, {
    timeout: 4000,
  });
  await page.evaluate(() => {
    window.__FAKE_OVERVIEW__ = { ok: true, taskState: "nope" };
  });
  await sleep(900);
  if ((await page.getAttribute("[data-testid='obd-task-status']", "data-state")) !== "offline") {
    throw new Error("malformed overview not offline");
  }
  await page.evaluate(() => {
    window.__OVERVIEW_DELAY_MS__ = 1600;
    window.__FAKE_OVERVIEW__ = { ok: true, taskState: "running" };
  });
  await sleep(100);
  await page.evaluate(() => {
    window.__OVERVIEW_DELAY_MS__ = 0;
    window.__FAKE_OVERVIEW__ = { ok: true, taskState: "idle" };
  });
  await sleep(1000);
  await sleep(1700);
  if ((await page.getAttribute("[data-testid='obd-task-status']", "data-state")) !== "idle") {
    throw new Error("stale overview overrode newer idle");
  }
  await page.evaluate(() => {
    delete window.__FAKE_OVERVIEW__;
    window.__FAKE_OVERVIEW_THROW__ = false;
    window.__OVERVIEW_DELAY_MS__ = 0;
  });

  if (consoleErrors.length) throw new Error(consoleErrors.join(" | "));
  await browser.close();
  results.push({ name: "playwright-harness", ok: true });
} finally {
  vite.kill();
}

await run(npm, ["run", "build", "-w", "@porsche981/desktop"], { cwd: root, shell: true, env: envDeny });
for (const f of ["topology-harness.html", "read-only-session-harness.html", "offline-diagnostics-harness.html"]) {
  if (fs.existsSync(path.join(appRoot, "dist", f))) throw new Error("production dist contains " + f);
}
results.push({ name: "production-dist", ok: true });

const testDbDir = path.join(shotDir, "test-db");
const testDb = path.join(testDbDir, "garage-topo.db");
let electronOk = false;
let electronErr = null;
try {
  await run(npm, ["run", "build", "-w", "@porsche981/db"], { cwd: root, shell: true, env: envDeny });
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
  if (path.normalize(testDb).toLowerCase().endsWith(`${path.sep}.local${path.sep}garage.db`)) {
    throw new Error("refused production garage.db");
  }
  const app = await electron.launch({
    executablePath: electronBin,
    args: [appRoot],
    env: {
      ...envDeny,
      PORSCHE981_DB: testDb,
      PORSCHE981_DEVTOOLS: "",
      VITE_DEV_SERVER_URL: "",
      ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
      PW_TEST_SCREENSHOT_NO_FONTS_READY: "1",
    },
    timeout: 90_000,
  });
  try {
    const win = await app.firstWindow({ timeout: 90_000 });
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1440, 1200));
    await win.waitForSelector("[data-tab='obd']", { timeout: 60_000 });
    await win.click("[data-tab='obd']");
    await win.waitForSelector("[data-page='topology']", { timeout: 30_000 });
    async function shot(name) {
      await win.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const data = await app.evaluate(async ({ BrowserWindow }) => {
        const window = BrowserWindow.getAllWindows()[0];
        return (await window.webContents.capturePage()).toPNG().toString("base64");
      });
      fs.writeFileSync(path.join(shotDir, name), Buffer.from(data, "base64"));
      if (!fs.statSync(path.join(shotDir, name)).size) throw new Error("empty screenshot " + name);
    }
    await shot("topology-electron.png");
    await assertMapFits(win, "electron wide");
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(900, 1000));
    await shot("topology-electron-narrow.png");
    await assertMapFits(win, "electron narrow");
    for (const width of [1100, 800, 700, 600]) {
      await app.evaluate(({ BrowserWindow }, w) => BrowserWindow.getAllWindows()[0].setSize(w, 1000), width);
      await win.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await assertMapFits(win, `electron width ${width}`);
    }
    await shot("topology-electron-compact.png");
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1440, 1200));
    const header = await win.textContent("[data-testid='obd-task-status']");
    if (!header || !/空闲|运行中|离线/.test(header)) throw new Error(`electron header ${header}`);
    if (await win.locator("[data-testid='topo-mode']").count()) throw new Error("electron production sim controls");
    await win.locator("[data-testid='topo-node-dme']").first().click();
    await sleep(400);
    const detail = await win.textContent("[data-testid='topo-detail']");
    if (!detail.includes("读取故障码")) throw new Error("electron missing selected actions");
    electronOk = true;
  } finally {
    await app.close();
  }
} catch (e) {
  electronErr = String(e);
}
results.push({ name: "electron-nav", ok: electronOk, error: electronErr });

async function waitJob(mgr, start) {
  if (!start.ok || !start.jobId) throw new Error(JSON.stringify(start));
  let doc = start;
  const t0 = Date.now();
  while (Date.now() - t0 < 110_000) {
    doc = await mgr.handle({ action: "status", jobId: start.jobId });
    if (doc.state && doc.state !== "running" && doc.state !== "cancelling") return doc;
    await sleep(200);
  }
  throw new Error("job timeout");
}

let pyOk = false;
let pyErr = null;
try {
  const mgr = createReadOnlySessionManager({ repoRoot: root, env: envDeny });
  const dmeDoc = await waitJob(
    mgr,
    await mgr.handle({
      action: "start",
      profileId: "porsche-981-2014-dme",
      mode: "simulation",
      scenario: "success",
    }),
  );
  const gwDoc = await waitJob(
    mgr,
    await mgr.handle({
      action: "start",
      profileId: "porsche-981-2014-gateway",
      mode: "simulation",
      scenario: "success",
    }),
  );
  fs.writeFileSync(path.join(shotDir, "scratch-dme-final.json"), JSON.stringify(dmeDoc.final, null, 2));
  fs.writeFileSync(path.join(shotDir, "scratch-gw-final.json"), JSON.stringify(gwDoc.final, null, 2));
  for (const [pid, doc] of [
    ["porsche-981-2014-dme", dmeDoc],
    ["porsche-981-2014-gateway", gwDoc],
  ]) {
    const f = doc.final;
    if (!f || f.ok !== true || f.status !== "completed" || f.simulation !== true || f.profileId !== pid) {
      throw new Error(`python ${pid} ${JSON.stringify({ ok: f?.ok, status: f?.status, sim: f?.simulation, profile: f?.profileId })}`);
    }
    const classified = classifySessionFinal(f, { profileId: pid, mode: "simulation" });
    if (classified.kind !== "no-dtc" && classified.kind !== "dtc-present") {
      throw new Error(`classify ${pid} ${classified.kind} ${classified.error}`);
    }
    const dtc = (f.results || []).find((r) => r.role === "dependent" && (r.operationId === "dme-dtc" || r.operationId === "gw-dtc"));
    if (!dtc?.decoded || dtc.decoded.ok !== true || !Array.isArray(dtc.decoded.records)) {
      throw new Error(`decoded ${pid}`);
    }
  }
  pyOk = true;
  await mgr.shutdown?.();
} catch (e) {
  pyErr = String(e);
}
results.push({ name: "python-sim", ok: pyOk, error: pyErr });

const reportShot = path.join(root, ".local", "task-topology-unified");
fs.mkdirSync(reportShot, { recursive: true });
for (const name of fs.readdirSync(shotDir)) {
  if (name.endsWith(".png")) fs.copyFileSync(path.join(shotDir, name), path.join(reportShot, name));
}

const failed = results.filter((r) => !r.ok);
console.log(JSON.stringify({ results }, null, 2));
if (failed.length) {
  console.error(failed);
  process.exit(1);
}
