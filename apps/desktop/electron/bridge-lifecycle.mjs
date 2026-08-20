/**
 * db-bridge child-process lifecycle: pending cleanup + limited restart/backoff.
 * Inject spawn + timers so accept scripts can simulate death without Electron.
 */
import readline from "node:readline";

/**
 * @typedef {'idle' | 'starting' | 'ready' | 'restarting' | 'down' | 'stopped'} BridgeState
 * @typedef {{ state: BridgeState, detail: string | null, failures: number, at: number }} BridgeStatus
 */

/**
 * @param {number} failures already-recorded consecutive failures (before this delay)
 * @param {{ baseDelayMs?: number, maxDelayMs?: number }} [opts]
 */
export function restartDelayMs(failures, opts = {}) {
  const base = opts.baseDelayMs ?? 400;
  const max = opts.maxDelayMs ?? 8000;
  return Math.min(max, base * 2 ** Math.max(0, failures));
}

/**
 * @param {object} opts
 * @param {() => import('node:child_process').ChildProcessWithoutNullStreams} opts.spawnBridge
 * @param {(status: BridgeStatus) => void} [opts.onStatus]
 * @param {number} [opts.maxRetries]
 * @param {number} [opts.baseDelayMs]
 * @param {number} [opts.maxDelayMs]
 * @param {typeof setTimeout} [opts.setTimeoutFn]
 * @param {typeof clearTimeout} [opts.clearTimeoutFn]
 */
export function createBridgeController(opts) {
  const {
    spawnBridge,
    onStatus,
    maxRetries = 5,
    baseDelayMs = 400,
    maxDelayMs = 8000,
    setTimeoutFn = setTimeout,
    clearTimeoutFn = clearTimeout,
  } = opts;

  /** @type {import('node:child_process').ChildProcessWithoutNullStreams | null} */
  let child = null;
  let gen = 0;
  let shuttingDown = false;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let restartTimer = null;
  let failures = 0;
  let nextId = 1;
  /** @type {Map<number, { resolve: Function, reject: Function }>} */
  const pending = new Map();

  /** @type {BridgeStatus} */
  let status = {
    state: "idle",
    detail: null,
    failures: 0,
    at: Date.now(),
  };

  /** @param {BridgeState} state @param {string | null} [detail] */
  function publish(state, detail = null) {
    status = {
      state,
      detail,
      failures,
      at: Date.now(),
    };
    onStatus?.(status);
  }

  /** @param {string} reason */
  function rejectPending(reason) {
    for (const [, w] of pending) w.reject(new Error(reason));
    pending.clear();
  }

  function clearRestartTimer() {
    if (restartTimer != null) {
      clearTimeoutFn(restartTimer);
      restartTimer = null;
    }
  }

  /** @param {string} reason */
  function scheduleRestart(reason) {
    if (shuttingDown) return;
    if (restartTimer != null) return;
    if (failures >= maxRetries) {
      publish("down", reason);
      return;
    }
    const delay = restartDelayMs(failures, { baseDelayMs, maxDelayMs });
    failures += 1;
    publish(
      "restarting",
      `${reason}; retry #${failures}/${maxRetries} in ${delay}ms`,
    );
    restartTimer = setTimeoutFn(() => {
      restartTimer = null;
      start();
    }, delay);
  }

  /** @param {string} reason */
  function onDeath(reason) {
    if (shuttingDown) return;
    const dead = child;
    child = null;
    if (dead?.stdout) {
      try {
        dead.stdout.destroy();
      } catch {
        /* ignore */
      }
    }
    rejectPending(`db-bridge ${reason}`);
    scheduleRestart(reason);
  }

  /**
   * @param {import('node:child_process').ChildProcessWithoutNullStreams} proc
   * @param {number} myGen
   */
  function attach(proc, myGen) {
    const rl = readline.createInterface({ input: proc.stdout });
    rl.on("line", (line) => {
      if (myGen !== gen) return;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        return;
      }
      if (msg.id === 0) {
        failures = 0;
        publish("ready", null);
        return;
      }
      const wait = pending.get(msg.id);
      if (!wait) return;
      pending.delete(msg.id);
      if (msg.error) wait.reject(new Error(msg.error));
      else wait.resolve(msg.result);
    });

    proc.on("error", (err) => {
      if (myGen !== gen) return;
      onDeath(`error: ${err.message}`);
    });

    proc.on("exit", (code, signal) => {
      if (myGen !== gen) return;
      rl.close();
      if (shuttingDown) return;
      onDeath(`exited ${code ?? signal}`);
    });
  }

  function start() {
    if (shuttingDown) return;
    clearRestartTimer();
    publish(failures > 0 ? "restarting" : "starting", null);
    const myGen = ++gen;
    let proc;
    try {
      proc = spawnBridge();
    } catch (e) {
      onDeath(`spawn failed: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    child = proc;
    attach(proc, myGen);
  }

  /**
   * @param {string} method
   * @param {unknown} params
   */
  function call(method, params) {
    return new Promise((resolve, reject) => {
      if (!child || !child.stdin?.writable) {
        reject(
          new Error(
            status.state === "down"
              ? `db-bridge down: ${status.detail ?? "gave up"}`
              : status.state === "restarting" || status.state === "starting"
                ? "db-bridge restarting"
                : "db-bridge not running",
          ),
        );
        return;
      }
      const id = nextId++;
      pending.set(id, { resolve, reject });
      try {
        child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
      } catch (e) {
        pending.delete(id);
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    });
  }

  function stop() {
    shuttingDown = true;
    clearRestartTimer();
    gen += 1; // invalidate handlers
    const proc = child;
    child = null;
    if (proc) {
      try {
        proc.kill();
      } catch {
        /* ignore */
      }
    }
    rejectPending("db-bridge shutting down");
    publish("stopped", null);
  }

  function getStatus() {
    return status;
  }

  /** @returns {import('node:child_process').ChildProcessWithoutNullStreams | null} */
  function getChild() {
    return child;
  }

  return { start, call, stop, getStatus, getChild };
}
