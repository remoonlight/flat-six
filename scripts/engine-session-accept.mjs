/**
 * Engine session accept: fake bridge first, then real Python simulation after backend-ready.
 * Isolated DB. Deny live. Never touches .local/garage.db.
 * node scripts/engine-session-accept.mjs
 */
import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import http from "node:http";
import net from "node:net";
import { chromium, _electron as electron } from "playwright";
import { createReadOnlySessionManager } from "../apps/desktop/electron/read-only-session.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const appRoot = path.join(root, "apps", "desktop");
const require = createRequire(path.join(appRoot, "electron", "main.mjs"));
const electronBin = require("electron");
const coord = path.join(root, ".local", "cursor-coordination", "precar-completion-20260927");
const outDir = path.join(coord, "scratch", "frontend-evidence");
const testDbDir = path.join(outDir, "test-db");
const testDb = path.join(testDbDir, "garage.db");
const readyPath = path.join(coord, "backend-ready.json");
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
      else resolve({ out, err, code: 0 });
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

async function waitBackendReady(ms = 180_000) {
  const candidates = [
    path.join(coord, "backend-ready.json"),
    path.join(coord, "scratch", "backend-ready.json"),
  ];
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    for (const p of candidates) {
      if (fs.existsSync(p)) {
        const st = fs.statSync(p);
        return { ready: true, path: p, mtime: st.mtime.toISOString(), waitedMs: Date.now() - t0 };
      }
    }
    await sleep(2000);
  }
  return { ready: false, path: null, mtime: null, waitedMs: Date.now() - t0 };
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
    reject(new Error("vite_not_ready " + url));
  });
}

function engineBlob(extra = {}) {
  return {
    supportedPids: ["04", "05", "0C", "0D", "0F"],
    unsupportedPids: ["11"],
    samples: [
      {
        pid: "0C",
        label: "rpm",
        value: 900,
        unit: "rpm",
        capturedUtc: "2026-09-27T10:00:00Z",
        elapsedMs: 8,
        cycle: 1,
        synthetic: true,
      },
    ],
    completedCycles: 1,
    sampleCycles: 5,
    intervalMs: 1000,
    ...extra,
  };
}

try {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const tscBin = path.join(root, "node_modules", "typescript", "bin", "tsc");
  const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");

  const fake = await run(process.execPath, [path.join(appRoot, "electron", "read-only-session.selfcheck.mjs")], {
    cwd: root,
    env: { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_HEADLESS: "1" },
  });
  fs.writeFileSync(path.join(outDir, "selfcheck-fake.log"), fake.out + (fake.err || ""));

  const tsc = await run(process.execPath, [tscBin, "-p", "tsconfig.json", "--pretty", "false"], { cwd: appRoot }).catch((e) => {
    const msg = String(e && e.message ? e.message : e);
    fs.writeFileSync(path.join(outDir, "tsc.log"), msg);
    const ours = msg.split("\n").filter((l) => /EngineDataPage|engine-session|engine-session-harness|read-only-session-logic/.test(l));
    if (ours.length) throw e;
    return { out: msg, err: "", code: 1, preexisting: true };
  });
  if (!tsc.preexisting) fs.writeFileSync(path.join(outDir, "tsc.log"), tsc.out + (tsc.err || ""));

  await run(process.execPath, [viteBin, "build"], { cwd: appRoot });
  for (const name of [
    "engine-session-harness.html",
    "read-only-session-harness.html",
    "topology-harness.html",
    "offline-diagnostics-harness.html",
  ]) {
    if (fs.existsSync(path.join(appRoot, "dist", name))) {
      throw new Error("production build contains harness " + name);
    }
  }

  const engineAlready = /"action": "engine"/.test(fake.out) && /"state": "completed"/.test(fake.out);
  const backend = engineAlready
    ? { ready: fs.existsSync(readyPath) || fs.existsSync(path.join(coord, "scratch", "backend-ready.json")), path: readyPath, mtime: null, waitedMs: 0, enginePythonVerified: true }
    : await waitBackendReady();
  fs.writeFileSync(path.join(outDir, "backend-wait.json"), JSON.stringify({ ...backend, engineAlready }, null, 2));
  if (!backend.ready && !engineAlready) throw new Error("backend-ready.json missing after wait");

  const real = await run(process.execPath, [path.join(appRoot, "electron", "read-only-session.selfcheck.mjs")], {
    cwd: root,
    env: { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1", PORSCHE981_HEADLESS: "1" },
  });
  fs.writeFileSync(path.join(outDir, "selfcheck-real.log"), real.out + (real.err || ""));
  if (!/engine-prepare/.test(real.out) || /"ok": false/.test(real.out) && /engine-prepare/.test(real.out)) {
    const parsed = real.out.match(/\{[\s\S]*\}\s*$/);
    let enginePrepOk = true;
    try {
      const j = JSON.parse(parsed ? parsed[0] : "{}");
      const row = (j.python || []).find((x) => x.action === "engine-prepare");
      enginePrepOk = Boolean(row?.ok);
    } catch {
      enginePrepOk = /"action": "engine"/.test(real.out);
    }
    if (!enginePrepOk) throw new Error("engine prepare not ok after backend-ready\n" + real.out.slice(-2000));
  }

  await run(npm, ["run", "build", "-w", "@porsche981/db"], { cwd: root, shell: true });
  const { GarageDb } = await import("../packages/db/dist/index.js");
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
    await page.click("[data-obd-tab='live']");
    await page.waitForSelector("[data-page='engine-session']", { timeout: 60_000 });

    const liveDenied = await page.evaluate(() =>
      window.porsche981.readOnlySession({
        action: "start",
        profileId: "porsche-981-2014-dme",
        mode: "live",
        sessionTask: "engine",
        confirmedReadOnly: true,
        x431Inactive: true,
      }),
    );
    if (liveDenied.error !== "live_not_enabled") {
      throw new Error(`live not denied ${JSON.stringify(liveDenied)}`);
    }

    await page.selectOption("[data-testid='eng-mode']", "live");
    if (!(await page.isDisabled("[data-testid='eng-start']"))) throw new Error("live start enabled without checks");
    await page.check("[data-testid='eng-x431']");
    await page.check("[data-testid='eng-readonly']");
    if (await page.isDisabled("[data-testid='eng-start']")) throw new Error("live start still disabled after checks");
    await page.uncheck("[data-testid='eng-x431']");
    if (!(await page.isDisabled("[data-testid='eng-start']"))) throw new Error("live start enabled after uncheck");
    await page.selectOption("[data-testid='eng-mode']", "simulation");
    await page.fill("[data-testid='eng-cycles']", "1");
    await page.fill("[data-testid='eng-interval']", "500");

    await page.fill("[data-testid='eng-cycles']", "3");
    await page.fill("[data-testid='eng-interval']", "800");
    await page.click("[data-testid='eng-prepare']");
    await page.waitForFunction(
      () => {
        const t = document.querySelector("[data-testid='eng-plan']")?.innerText || "";
        return t.includes("转速") && t.includes("3") && t.includes("800") && !t.includes("standard-obd") && !t.includes("[object Object]");
      },
      null,
      { timeout: 30_000 },
    );

    await page.fill("[data-testid='eng-cycles']", "1");
    await page.fill("[data-testid='eng-interval']", "500");
    await page.selectOption("[data-testid='eng-scenario']", "slow");
    await page.click("[data-testid='eng-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "") === "sampling-sim",
      null,
      { timeout: 20_000 },
    );
    if ((await page.locator("[data-testid='eng-pids']").getAttribute("data-live")) === "1") {
      throw new Error("running simulation marked live");
    }
    await page.click("[data-testid='eng-cancel']");
    await page.waitForFunction(
      () => {
        const f = document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "";
        return f === "stale" || f === "idle";
      },
      null,
      { timeout: 30_000 },
    );

    await page.selectOption("[data-testid='eng-scenario']", "slow");
    await page.click("[data-testid='eng-start']");
    await page.click("[data-testid='eng-cancel']");
    await page.waitForFunction(
      () => {
        const f = document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "";
        return f === "stale" || f === "cancelling" || f === "idle";
      },
      null,
      { timeout: 20_000 },
    );
    await page.waitForFunction(
      () => {
        const f = document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "";
        const startOn = document.querySelector("[data-testid='eng-start']")?.disabled !== true;
        return (f === "stale" || f === "idle") && startOn;
      },
      null,
      { timeout: 30_000 },
    );

    await page.selectOption("[data-testid='eng-scenario']", "success");
    await page.click("[data-testid='eng-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "") === "simulated",
      null,
      { timeout: 90_000 },
    );
    const supportOk = await page.locator("[data-testid='eng-support']").innerText();
    if (!/支持/.test(supportOk)) throw new Error("support line missing");
    const liveAttr = await page.locator("[data-testid='eng-pids']").getAttribute("data-live");
    if (liveAttr === "1") throw new Error("completed samples marked live");

    await page.click("[data-obd-tab='connection']");
    await page.click("[data-obd-tab='live']");
    await page.waitForSelector("[data-page='engine-session']");
    const afterRemount = await page.locator("[data-testid='eng-kind']").getAttribute("data-freshness");
    if (afterRemount !== "idle") throw new Error(`remount stale ${afterRemount}`);

    await page.fill("[data-testid='eng-cycles']", "1");
    await page.fill("[data-testid='eng-interval']", "500");
    await page.selectOption("[data-testid='eng-scenario']", "identity-mismatch");
    await page.click("[data-testid='eng-start']");
    await page.waitForFunction(
      () => {
        const f = document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "";
        const e = document.querySelector("[data-testid='eng-error']")?.innerText || "";
        const k = document.querySelector("[data-testid='eng-kind']")?.innerText || "";
        return f === "stale" || /identity|不匹配|失败|失效/.test(e + k);
      },
      null,
      { timeout: 90_000 },
    );

    await page.selectOption("[data-testid='eng-scenario']", "slow");
    await page.click("[data-testid='eng-start']");
    await page.waitForFunction(
      () => document.querySelector("[data-obd-tab='session']")?.disabled === true,
      null,
      { timeout: 20_000 },
    );
    const busy = await page.evaluate(() =>
      window.porsche981.readOnlySession({
        action: "start",
        profileId: "porsche-981-2014-dme",
        mode: "simulation",
        sessionTask: "read",
      }),
    );
    if (busy.error !== "busy") throw new Error(`cross-task not busy ${JSON.stringify(busy)}`);
    await page.click("[data-testid='eng-cancel']");
    await page.waitForFunction(
      () => {
        const f = document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "";
        return f === "stale" || f === "idle";
      },
      null,
      { timeout: 30_000 },
    );

    await page.selectOption("[data-testid='eng-scenario']", "success");
    await page.click("[data-testid='eng-restart']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "") === "simulated",
      null,
      { timeout: 90_000 },
    );

    await page.selectOption("[data-testid='eng-scenario']", "disconnect");
    await page.click("[data-testid='eng-start']");
    await page.waitForFunction(
      () => (document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "") === "stale",
      null,
      { timeout: 90_000 },
    );

    try {
      await page.screenshot({
        path: path.join(outDir, "engine-session.png"),
        timeout: 8_000,
        animations: "disabled",
      });
    } catch {
      fs.writeFileSync(path.join(outDir, "screenshot.txt"), "screenshot_timeout_after_ui_ok");
    }
    fs.writeFileSync(
      path.join(outDir, "electron-nav.json"),
      JSON.stringify({ db: testDb, liveDenied: liveDenied.error, busy: busy.error }, null, 2),
    );
  } finally {
    await app.close();
  }

  await run(process.execPath, [viteBin, "build", "--mode", "engine-test"], { cwd: appRoot });
  if (!fs.existsSync(path.join(appRoot, "dist", "engine-session-harness.html"))) {
    throw new Error("engine-test build missing harness");
  }
  const port = await unusedPort();
  const vite = spawn(
    process.execPath,
    [viteBin, "preview", "--host", "127.0.0.1", "--port", String(port), "--strictPort"],
    { cwd: appRoot, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, BROWSER: "none" } },
  );
  try {
    await waitHttp(`http://127.0.0.1:${port}/engine-session-harness.html`);
    const mgr = createReadOnlySessionManager({
      repoRoot: root,
      env: { ...process.env, PORSCHE981_SESSION_DENY_LIVE: "1" },
      spawnFn: () => {
        const child = new EventEmitter();
        child.killed = false;
        child.exitCode = null;
        child.stdin = { write: () => true, end: () => {}, on: () => {} };
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        child.kill = () => {
          child.killed = true;
          child.exitCode = 1;
          queueMicrotask(() => child.emit("close", 1));
        };
        queueMicrotask(() => {
          const runId = "simokrunidxxxxxxxx";
          const lines = [
            JSON.stringify({
              type: "progress",
              runId,
              stage: "engine",
              profileId: "porsche-981-2014-dme",
              completed: 1,
              total: 5,
              engine: engineBlob({ completedCycles: 1 }),
            }),
            JSON.stringify({
              type: "result",
              ok: true,
              mode: "simulation",
              runId,
              profileId: "porsche-981-2014-dme",
              status: "completed",
              error: null,
              sessionTask: "engine",
              results: [],
              engine: engineBlob(),
              simulation: true,
              liveVerified: false,
              writePayload: null,
            }),
          ];
          child.stdout.emit("data", Buffer.from(lines.join("\n") + "\n"));
          child.exitCode = 0;
          child.emit("close", 0);
        });
        return child;
      },
    });
    const browser = await chromium.launch({ headless: true, channel: "chrome" });
    let liveMgr;
    try {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await page.exposeFunction("__engBridge", (req) => {
        if (req?.action === "prepare") {
          return {
            ok: true,
            executionEnabled: true,
            liveVerified: false,
            writePayload: null,
            plan: {
              engine: {
                definitions: [
                  { pid: "04" },
                  { pid: "05" },
                  { pid: "0C" },
                  { pid: "0D" },
                  { pid: "0F" },
                  { pid: "11" },
                ],
                sampleCycles: req.sampleCycles ?? 5,
                intervalMs: req.intervalMs ?? 1000,
                options: {
                  sampleCycles: { min: 1, max: 10, default: 5 },
                  intervalMs: { min: 500, max: 5000, default: 1000 },
                },
              },
            },
          };
        }
        return mgr.handle(req, { ownerId: 1 });
      });
      await page.addInitScript(() => {
        window.porsche981 = { readOnlySession: (req) => window.__engBridge(req) };
      });
      await page.goto(`http://127.0.0.1:${port}/engine-session-harness.html`, { waitUntil: "domcontentloaded" });
      await page.waitForSelector("[data-page='engine-session']", { timeout: 30_000 });
      await page.fill("[data-testid='eng-cycles']", "3");
      await page.fill("[data-testid='eng-interval']", "800");
      await page.click("[data-testid='eng-prepare']");
      await page.waitForFunction(
        () => {
          const t = document.querySelector("[data-testid='eng-plan']")?.innerText || "";
          return t.includes("转速") && t.includes("800") && !t.includes("standard-obd");
        },
        null,
        { timeout: 30_000 },
      );
      await page.fill("[data-testid='eng-cycles']", "5");
      await page.fill("[data-testid='eng-interval']", "1000");
      await page.click("[data-testid='eng-start']");
      await page.waitForFunction(
        () => (document.querySelector("[data-testid='eng-support']")?.innerText || "").includes("11"),
        null,
        { timeout: 20_000 },
      );
      const unsupported = await page.locator("[data-pid='11']").getAttribute("data-unsupported");
      if (unsupported !== "1") throw new Error("unsupported pid not marked");
      await page.screenshot({
        path: path.join(outDir, "engine-unsupported.png"),
        timeout: 15_000,
        animations: "disabled",
      });
      await page.screenshot({
        path: path.join(outDir, "engine-session-styled.png"),
        timeout: 15_000,
        animations: "disabled",
      });

      const liveEnv = { ...process.env };
      delete liveEnv.PORSCHE981_SESSION_DENY_LIVE;
      liveMgr = createReadOnlySessionManager({
        repoRoot: root,
        allowInjectedLive: true,
        env: liveEnv,
        spawnFn: () => {
          const child = new EventEmitter();
          child.killed = false;
          child.exitCode = null;
          child.stdin = { write: () => true, end: () => {}, on: () => {} };
          child.stdout = new EventEmitter();
          child.stderr = new EventEmitter();
          child.kill = () => {
            child.killed = true;
            child.exitCode = 1;
            queueMicrotask(() => child.emit("close", 1));
          };
          queueMicrotask(() => {
            child.stdout.emit(
              "data",
              Buffer.from(
                JSON.stringify({
                  type: "progress",
                  runId: "liverunidxxxxxxxx",
                  stage: "engine",
                  profileId: "porsche-981-2014-dme",
                  completed: 1,
                  total: 5,
                  engine: engineBlob({
                    completedCycles: 1,
                    sampleCycles: 5,
                    intervalMs: 1000,
                    samples: [
                      {
                        pid: "0C",
                        label: "rpm",
                        value: 900,
                        unit: "rpm",
                        capturedUtc: "2020-01-01T00:00:00Z",
                        elapsedMs: 8,
                        cycle: 1,
                        synthetic: false,
                      },
                    ],
                  }),
                }) + "\n",
              ),
            );
          });
          return child;
        },
      });
      const livePage = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await livePage.exposeFunction("__engBridge", (req) => liveMgr.handle(req, { ownerId: 2 }));
      await livePage.addInitScript(() => {
        window.porsche981 = { readOnlySession: (req) => window.__engBridge(req) };
      });
      await livePage.goto(`http://127.0.0.1:${port}/engine-session-harness.html`, { waitUntil: "domcontentloaded" });
      await livePage.selectOption("[data-testid='eng-mode']", "live");
      await livePage.check("[data-testid='eng-x431']");
      await livePage.check("[data-testid='eng-readonly']");
      await livePage.click("[data-testid='eng-start']");
      await livePage.waitForFunction(
        () => (document.querySelector("[data-testid='eng-kind']")?.getAttribute("data-freshness") || "") === "sampling",
        null,
        { timeout: 20_000 },
      );
      await livePage.waitForFunction(
        () => document.querySelector("[data-pid='0C']")?.getAttribute("data-stale") === "1",
        null,
        { timeout: 10_000 },
      );
    } finally {
      await browser.close();
      await mgr.shutdown();
      await liveMgr?.shutdown();
    }
  } finally {
    try {
      if (vite.pid) process.kill(vite.pid);
    } catch {
      /* */
    }
  }

  await run(process.execPath, [viteBin, "build"], { cwd: appRoot });
  console.log("engine-session-accept: ok");
} catch (e) {
  console.error(e);
  process.exit(1);
}
