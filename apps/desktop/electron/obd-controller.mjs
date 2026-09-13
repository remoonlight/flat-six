import { spawn } from "node:child_process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import { createObdPlan, validateObdRecording } from "@porsche981/domain";
import { idleState } from "../../../packages/obd/src/engine.mjs";
import { createProductionHost } from "./obd-production-host.mjs";

/** Separate worker lifecycle; acquisition results require DB acknowledgement. */
export function createObdController({ dbCall, publish = () => {}, publishLive = () => {}, openBluetooth = async () => {}, spawnWorker = () => spawn(process.env.PORSCHE981_NODE || "node", [fileURLToPath(new URL("./obd-bridge.mjs", import.meta.url))], { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] }) }) {
  let child = null, ready = null, nextId = 1, state = idleState(), starting = false, closing = false;
  const pending = new Map();
  const prod = createProductionHost({ dbCall, publishLive, openBluetooth });
  const emit = () => publish(state);
  const simBusy = () => starting || ["running", "saving"].includes(state.phase);
  const busy = () => simBusy() || Boolean(prod.snapshot().op);
  async function boundedDb(method, params) {
    let timer;
    try { return await Promise.race([dbCall(method, params), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("obd_database_timeout")), 4500); })]); }
    finally { clearTimeout(timer); }
  }
  function write(proc, value) {
    if (proc !== child || !proc.stdin.writable) throw new Error("obd_worker_unavailable");
    proc.stdin.write(JSON.stringify(value) + "\n");
  }
  function ensureWorker() {
    if (ready) return ready;
    closing = false;
    ready = new Promise((resolve, reject) => {
      const proc = spawnWorker(); child = proc;
      const timeout = setTimeout(() => { reject(new Error("obd_worker_start_timeout")); proc.kill(); }, 5000);
      let dead = false;
      const death = (detail) => {
        if (dead) return; dead = true; clearTimeout(timeout);
        reject(new Error(detail));
        if (proc !== child) return;
        child = null; ready = null;
        for (const p of pending.values()) { clearTimeout(p.timer); p.reject(new Error(detail)); }
        pending.clear();
        if (busy()) {
          const run = state.run;
          state = { ...state, phase: "error", error: "采集进程中断；已保存记录可从历史打开" };
          if (run) boundedDb("obdRuns:finish", { sessionId: run.sessionId, status: "interrupted", reason: detail }).catch(() => {});
          emit();
        } else if (!closing) { state = { ...state, phase: "error", error: detail }; emit(); }
      };
      proc.on("error", (e) => death(e.message));
      proc.on("exit", () => death("obd_worker_exited"));
      // Drain stderr; do not log large or potentially sensitive payloads.
      proc.stderr?.on("data", () => {});
      readline.createInterface({ input: proc.stdout }).on("line", async (line) => {
        if (child !== proc) return;
        let msg; try { msg = JSON.parse(line); } catch { return; }
        if (msg.kind === "ready") { clearTimeout(timeout); resolve(); }
        else if (msg.kind === "result") {
          const p = pending.get(msg.id); if (!p) return;
          pending.delete(msg.id); clearTimeout(p.timer);
          msg.error ? p.reject(new Error(msg.error)) : p.resolve(msg.result);
        } else if (msg.kind === "state") { state = msg.state; emit(); }
        else if (msg.kind === "persist") {
          try {
            if (!state.run || msg.value?.sessionId !== state.run.sessionId) throw new Error("obd_session_mismatch");
            const method = msg.operation === "observation" ? "obdRuns:append" : msg.operation === "finish" ? "obdRuns:finish" : null;
            if (!method) throw new Error("obd_invalid_persistence_operation");
            const result = await boundedDb(method, msg.value);
            if (child === proc) write(proc, { kind: "persist-result", id: msg.id, result });
          } catch (e) { if (child === proc) write(proc, { kind: "persist-result", id: msg.id, error: e.message }); }
        }
      });
    });
    return ready;
  }
  async function rpc(method, params) {
    await ensureWorker();
    const proc = child, id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("obd_worker_timeout")); proc.kill(); }, method === "start" ? 200_000 : 15_000);
      pending.set(id, { resolve, reject, timer });
      try { write(proc, { id, method, params }); }
      catch (e) { clearTimeout(timer); pending.delete(id); reject(e); }
    });
  }
  return {
    getState: () => state,
    async start(input) {
      if (busy()) throw new Error("采集正在进行，请先停止");
      if (prod.snapshot().adapterConnected) throw new Error("请先断开生产适配器再开始模拟");
      const plan = createObdPlan(input);
      starting = true; let run;
      try {
        await ensureWorker();
        run = await boundedDb("obdRuns:begin", plan);
        // Publish session identity before the worker can request a DB write.
        state = { ...idleState(), mode: "simulation", phase: "running", run };
        emit();
        await rpc("start", run);
        return state;
      } catch (e) {
        if (run) await boundedDb("obdRuns:finish", { sessionId: run.sessionId, status: "interrupted", reason: e.message }).catch(() => {});
        state = { ...state, phase: "error", error: e.message }; emit(); throw e;
      } finally { starting = false; }
    },
    async stop() { if (starting) throw new Error("正在创建会话，请稍后停止"); if (child && busy()) await rpc("stop"); return state; },
    list: () => boundedDb("obdRuns:list"),
    recording: (id) => boundedDb("obdRuns:recording", id),
    async replay(id) {
      if (busy()) throw new Error("请先停止采集再打开回放");
      const recording = validateObdRecording(await boundedDb("obdRuns:recording", id));
      if (busy()) throw new Error("采集已开始，暂不能打开回放");
      state = { mode: "replay", phase: "finished", run: recording.run, elapsedMs: recording.observations.at(-1)?.tMs ?? 0, observations: recording.observations, error: null };
      emit(); return state;
    },
    async shutdown() {
      closing = true;
      await prod.shutdown().catch(() => {});
      if (child && simBusy()) await rpc("stop").catch(() => {});
      const proc = child; child = null; ready = null;
      proc?.kill();
    },
    production: prod,
  };
}
