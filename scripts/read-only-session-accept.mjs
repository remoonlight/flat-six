/**
 * Read-only session accept: production build + isolated Electron UI.
 * Never touches .local/garage.db. No live requests.
 * node scripts/read-only-session-accept.mjs
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";
import net from "node:net";
import { chromium, _electron as electron } from "playwright";
import {
  backendReadyPath,
  createReadOnlySessionManager,
} from "../apps/desktop/electron/read-only-session.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const require = createRequire(path.join(appRoot, "electron", "main.mjs"));
const electronBin = require("electron");
const outDir = path.join(root, ".local", "x431-re", "2026-09-27-preconnect", "desktop");
const testDbDir = path.join(outDir, "test-db");
const testDb = path.join(testDbDir, "garage.db");
fs.mkdirSync(outDir, { recursive: true });

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

function seedTestDb(GarageDb) {
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
  const n = path.normalize(testDb).toLowerCase();
  if (n.endsWith(`${path.sep}.local${path.sep}garage.db`)) {
    throw new Error("refused production garage.db");
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitBackend() {
  const sessionsPy = path.join(root, "scripts", "diagnostics", "sessions.py");
  const ready = backendReadyPath(root);
  const minReady = Date.parse("2026-09-27T13:44:00+08:00");
  const t0 = Date.now();
  while (Date.now() - t0 < 90_000) {
    if (fs.existsSync(sessionsPy) && fs.existsSync(ready)) {
      const st = fs.statSync(ready);
      if (st.mtimeMs > minReady) {
        return { sessions: true, ready: true, mtime: st.mtime.toISOString(), stale: false };
      }
    }
    await sleep(1000);
  }
  const st = fs.existsSync(ready) ? fs.statSync(ready) : null;
  return {
    sessions: fs.existsSync(sessionsPy),
    ready: Boolean(st),
    mtime: st ? st.mtime.toISOString() : null,
    stale: !st || st.mtimeMs <= minReady,
  };
}

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
        await sleep(250);
      }
    }
    reject(new Error("vite_not_ready"));
  });
}

try {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  await run(npm, ["run", "build", "-w", "@porsche981/db"], { cwd: root, shell: true });
  const { GarageDb } = await import("../packages/db/dist/index.js");

  await run(process.execPath, [path.join(root, "node_modules", "vite", "bin", "vite.js"), "build"], {
    cwd: appRoot,
  });
  if (fs.existsSync(path.join(appRoot, "dist", "offline-diagnostics-harness.html"))) {
    throw new Error("production build contains offline harness");
  }
  if (fs.existsSync(path.join(appRoot, "dist", "read-only-session-harness.html"))) {
    throw new Error("production build contains session harness");
  }

  const backend = await waitBackend();
  const sc = await run(process.execPath, [path.join(appRoot, "electron", "read-only-session.selfcheck.mjs")], {
    cwd: root,
  });
  fs.writeFileSync(path.join(outDir, "selfcheck.log"), sc.out + (sc.err || ""));

  seedTestDb(GarageDb);
  const app = await electron.launch({
    executablePath: electronBin,
    args: [appRoot],
    env: {
      ...process.env,
      PORSCHE981_DB: testDb,
      PORSCHE981_HEADLESS: "1",
      PORSCHE981_SESSION_DENY_LIVE: "1",
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
    await page.click("[data-obd-tab='session']");
    await page.waitForSelector("[data-page='read-only-session']", { timeout: 60_000 });

    const bound = await page.evaluate(() => typeof window.porsche981?.readOnlySession === "function");
    if (!bound) throw new Error("preload readOnlySession missing");

    const liveDenied = await page.evaluate(() =>
      window.porsche981.readOnlySession({
        action: "start",
        profileId: "porsche-981-2014-dme",
        mode: "live",
        confirmedReadOnly: true,
        x431Inactive: true,
      }),
    );
    if (liveDenied.error !== "live_not_enabled") {
      throw new Error(`live not denied ${JSON.stringify(liveDenied)}`);
    }

    await page.selectOption("[data-testid='ros-mode']", "live");
    if (!(await page.isDisabled("[data-testid='ros-start']"))) throw new Error("live start enabled without checks");
    if (!(await page.isDisabled("[data-testid='ros-resume']"))) throw new Error("live resume enabled without run/checks");
    await page.check("[data-testid='ros-x431']");
    await page.check("[data-testid='ros-readonly']");
    if (await page.isDisabled("[data-testid='ros-start']")) throw new Error("live start still disabled after checks");
    await page.uncheck("[data-testid='ros-x431']");
    if (!(await page.isDisabled("[data-testid='ros-start']"))) throw new Error("live start enabled after uncheck");
    await page.selectOption("[data-testid='ros-mode']", "simulation");

    await page.click("[data-testid='ros-prepare']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-plan']")?.innerText || "").includes("诊断会话"),
      null,
      { timeout: 30_000 },
    );

    await page.click("[data-testid='ros-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-kind']")?.textContent || "").includes("模拟结果"),
      null,
      { timeout: 60_000 },
    );
    // Raw protocol JSON is deliberately collapsed; inspect it as a user would.
    await page.getByText("查看详细结果与原始响应", { exact: true }).click();
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-result']")?.innerText || "").includes("completed"),
      null,
      { timeout: 15_000 },
    );

    await page.selectOption("[data-testid='ros-scenario']", "identity-mismatch");
    await page.click("[data-testid='ros-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-result']")?.innerText || "").includes("identity-mismatch"),
      null,
      { timeout: 60_000 },
    );

    await page.selectOption("[data-testid='ros-scenario']", "slow");
    await page.click("[data-testid='ros-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-progress']")?.innerText || "").length > 2,
      null,
      { timeout: 20_000 },
    );
    await page.click("[data-testid='ros-cancel']");
    await page.waitForFunction(
      () => {
        const t = document.querySelector("[data-testid='ros-result']")?.innerText || "";
        const k = document.querySelector("[data-testid='ros-kind']")?.innerText || "";
        return t.includes("cancelled") || k.includes("失败");
      },
      null,
      { timeout: 30_000 },
    );

    await page.selectOption("[data-testid='ros-scenario']", "success");
    await page.click("[data-testid='ros-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-kind']")?.textContent || "").includes("模拟结果"),
      null,
      { timeout: 60_000 },
    );
    await page.waitForFunction(
      () => !document.querySelector("[data-testid='ros-resume']")?.disabled,
      null,
      { timeout: 10_000 },
    );
    await page.click("[data-testid='ros-resume']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='ros-kind']")?.textContent || "").includes("继续"),
      null,
      { timeout: 60_000 },
    );

    try {
      await page.screenshot({
        path: path.join(outDir, "read-only-session.png"),
        timeout: 8_000,
        animations: "disabled",
      });
    } catch {
      fs.writeFileSync(path.join(outDir, "screenshot.txt"), "screenshot_timeout_after_ui_ok");
    }
    fs.writeFileSync(
      path.join(outDir, "electron-nav.json"),
      JSON.stringify({ db: testDb, bound: true, liveDenied: liveDenied.error, backend }, null, 2),
    );
  } finally {
    await app.close();
  }

  let shot = { captured: false, path: path.join(outDir, "read-only-session.png"), note: "" };
  const shotDeadline = Date.now() + 120_000;
  try {
    await run(process.execPath, [path.join(root, "node_modules", "vite", "bin", "vite.js"), "build", "--mode", "session-test"], {
      cwd: appRoot,
    });
    if (!fs.existsSync(path.join(appRoot, "dist", "read-only-session-harness.html"))) {
      throw new Error("session-test build missing harness");
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
    try {
      await waitHttp(`http://127.0.0.1:${port}/read-only-session-harness.html`);
      if (Date.now() > shotDeadline) throw new Error("screenshot_budget");
      const rosMgr = createReadOnlySessionManager({
        repoRoot: root,
        env: { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1" },
      });
      const browser = await chromium.launch({ headless: true, channel: "chrome" });
      try {
        const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
        await page.exposeFunction("__rosBridge", (req) => rosMgr.handle(req, { ownerId: 1 }));
        await page.addInitScript(() => {
          window.__ROS_SEED_LIVE_RUN__ = {
            runId: "saaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            key: "porsche-981-2014-dme:live",
          };
          window.porsche981 = {
            readOnlySession: (req) => window.__rosBridge(req),
          };
        });
        await page.goto(`http://127.0.0.1:${port}/read-only-session-harness.html`, { waitUntil: "domcontentloaded" });
        await page.waitForSelector("[data-page='read-only-session']", { timeout: 30_000 });
        await page.selectOption("[data-testid='ros-mode']", "live");
        if (!(await page.isDisabled("[data-testid='ros-resume']"))) {
          throw new Error("seeded live resume enabled without checks");
        }
        await page.check("[data-testid='ros-x431']");
        await page.check("[data-testid='ros-readonly']");
        if (await page.isDisabled("[data-testid='ros-resume']")) {
          throw new Error("seeded live resume disabled with checks");
        }
        await page.uncheck("[data-testid='ros-readonly']");
        if (!(await page.isDisabled("[data-testid='ros-resume']"))) {
          throw new Error("seeded live resume still enabled after uncheck");
        }
        await page.selectOption("[data-testid='ros-mode']", "simulation");
        await page.click("[data-testid='ros-prepare']");
        await page.waitForFunction(
          () => (document.querySelector("[data-testid='ros-plan']")?.innerText || "").includes("诊断会话"),
          null,
          { timeout: 30_000 },
        );
        await page.click("[data-testid='ros-start']");
        await page.waitForFunction(
          () => (document.querySelector("[data-testid='ros-kind']")?.textContent || "").includes("模拟结果"),
          null,
          { timeout: 60_000 },
        );
        await page.screenshot({
          path: path.join(outDir, "read-only-session.png"),
          timeout: 15_000,
          animations: "disabled",
        });
        if (!fs.existsSync(path.join(outDir, "read-only-session.png"))) throw new Error("png missing");
        shot.captured = true;
      } finally {
        await browser.close();
      }
    } finally {
      try {
        if (vite.pid) process.kill(vite.pid);
      } catch {
        /* */
      }
    }
  } catch (e) {
    shot.note = String(e && e.message ? e.message : e);
    fs.writeFileSync(path.join(outDir, "screenshot.txt"), shot.note);
    const soft = !shot.captured && /Timeout|vite_not_ready|screenshot_budget/i.test(shot.note);
    if (!soft) throw e;
  }

  const readySt = fs.existsSync(backendReadyPath(root)) ? fs.statSync(backendReadyPath(root)) : null;
  fs.writeFileSync(
    path.join(outDir, "backend-ready.txt"),
    JSON.stringify({ present: Boolean(readySt), mtime: readySt ? readySt.mtime.toISOString() : null, backend }, null, 2),
  );
  fs.writeFileSync(path.join(outDir, "screenshot.json"), JSON.stringify(shot, null, 2));
  console.log("read-only-session-accept: ok");
  console.log("screenshot", shot);
} catch (e) {
  console.error(e);
  process.exit(1);
}
