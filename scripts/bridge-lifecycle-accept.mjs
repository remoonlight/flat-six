/**
 * Self-check for db-bridge auto-restart lifecycle.
 *
 * Manual (Electron app):
 * 1. npm run dev
 * 2. In Task Manager / `tasklist`, find the `node … db-bridge.mjs` child and End Task
 * 3. Expect: top banner "正在恢复…", pending IPC rejects once, then banner clears when ready
 * 4. Garage page reload / re-open tab should work again
 * 5. If you kill it >5 times quickly, banner shows permanent failure
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  createBridgeController,
  restartDelayMs,
} from "../apps/desktop/electron/bridge-lifecycle.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const bridgeScript = path.join(
  repoRoot,
  "apps/desktop/electron/db-bridge.mjs",
);
const dbFile = path.join(repoRoot, ".local", "garage.db");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function checkDelays() {
  assert(restartDelayMs(0) === 400, "delay0");
  assert(restartDelayMs(1) === 800, "delay1");
  assert(restartDelayMs(2) === 1600, "delay2");
  assert(restartDelayMs(10) === 8000, "delay capped");
  console.log("ok delay backoff");
}

/** Fake child that exits immediately — exercises restart counter without real DB. */
function checkFakeDeathThenDown() {
  return new Promise((resolve, reject) => {
    const statuses = [];
    let ticks = 0;
    const timers = new Set();

    const ctrl = createBridgeController({
      maxRetries: 2,
      baseDelayMs: 10,
      maxDelayMs: 10,
      setTimeoutFn: (fn, ms) => {
        const id = setTimeout(() => {
          timers.delete(id);
          ticks += 1;
          fn();
        }, ms);
        timers.add(id);
        return id;
      },
      clearTimeoutFn: (id) => {
        clearTimeout(id);
        timers.delete(id);
      },
      onStatus: (s) => statuses.push(s.state),
      spawnBridge: () => {
        const child = spawn(
          process.execPath,
          ["-e", "process.exit(42)"],
          { stdio: ["pipe", "pipe", "pipe"] },
        );
        return child;
      },
    });

    ctrl.start();

    const deadline = Date.now() + 5000;
    const poll = () => {
      const st = ctrl.getStatus();
      if (st.state === "down") {
        ctrl.stop();
        for (const t of timers) clearTimeout(t);
        assert(st.failures === 2, `expected 2 failures, got ${st.failures}`);
        assert(
          statuses.includes("restarting") || statuses.includes("starting"),
          "saw starting/restarting",
        );
        assert(statuses.includes("down"), "ended down");
        console.log("ok fake death → down after maxRetries");
        resolve();
        return;
      }
      if (Date.now() > deadline) {
        ctrl.stop();
        reject(new Error(`timeout waiting for down; status=${st.state} ticks=${ticks}`));
        return;
      }
      setTimeout(poll, 20);
    };
    setTimeout(poll, 20);
  });
}

/** Real db-bridge: ping → kill → auto restart → ping again. */
async function checkRealKillAndRecover() {
  if (!fs.existsSync(bridgeScript)) {
    throw new Error(`missing ${bridgeScript}`);
  }
  fs.mkdirSync(path.dirname(dbFile), { recursive: true });

  const statuses = [];
  const ctrl = createBridgeController({
    maxRetries: 3,
    baseDelayMs: 50,
    maxDelayMs: 200,
    onStatus: (s) => statuses.push({ ...s }),
    spawnBridge: () =>
      spawn(process.execPath, [bridgeScript], {
        env: { ...process.env, PORSCHE981_DB: dbFile },
        stdio: ["pipe", "pipe", "inherit"],
        cwd: repoRoot,
      }),
  });

  ctrl.start();

  const waitReady = () =>
    new Promise((resolve, reject) => {
      const t0 = Date.now();
      const tick = () => {
        if (ctrl.getStatus().state === "ready") return resolve();
        if (Date.now() - t0 > 15000) {
          return reject(new Error("timeout waiting ready"));
        }
        setTimeout(tick, 30);
      };
      tick();
    });

  await waitReady();
  const first = await ctrl.call("ping", null);
  assert(first?.ok === true, "first ping");

  const child = ctrl.getChild();
  assert(child?.pid, "has child pid");
  const killPromise = new Promise((resolve) => {
    child.once("exit", () => resolve());
  });
  child.kill("SIGTERM");
  await killPromise;

  // After exit: pending cleared; call must reject (not hang) until restart ready
  let rejected = false;
  try {
    await Promise.race([
      ctrl.call("ping", null),
      new Promise((_, rej) =>
        setTimeout(() => rej(new Error("call hung after bridge death")), 2000),
      ),
    ]);
  } catch (e) {
    rejected = true;
    assert(
      !String(e.message).includes("hung"),
      `IPC must not hang: ${e.message}`,
    );
  }
  assert(rejected, "call after death should reject");

  await waitReady();
  const second = await ctrl.call("ping", null);
  assert(second?.ok === true, "ping after restart");
  assert(
    statuses.some((s) => s.state === "restarting"),
    "saw restarting status",
  );

  ctrl.stop();
  console.log("ok real bridge kill → restart → ping");
}

async function main() {
  checkDelays();
  await checkFakeDeathThenDown();
  await checkRealKillAndRecover();
  console.log("bridge-lifecycle-accept: PASS");
  console.log(
    "Manual: kill Electron's db-bridge.mjs child; UI banner should recover.",
  );
}

main().catch((e) => {
  console.error("bridge-lifecycle-accept: FAIL", e);
  process.exit(1);
});
