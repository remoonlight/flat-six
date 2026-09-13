import { performance } from "node:perf_hooks";
import { createObdPlan, decodeObdObservation, OBD_LIVE_COMMANDS } from "@porsche981/domain";
import { SimulatedAdapter, delay } from "./simulator.mjs";

export const idleState = () => ({ mode: "offline", phase: "idle", run: null, elapsedMs: 0, observations: [], error: null });

/** Sequential acquisition with durable acknowledgement before the next request. */
export class ObdEngine {
  constructor({ persist, publish = () => {}, now = () => performance.now(), wait = delay }) {
    this.persist = persist; this.publish = publish; this.now = now; this.wait = wait;
    this.state = idleState(); this.active = null; this.abort = null;
  }
  start(run) {
    if (this.active) throw new Error("obd_busy");
    const plan = createObdPlan(run);
    if (run.source !== "simulation") throw new Error("obd_hardware_not_enabled");
    this.abort = new AbortController();
    this.state = { mode: "simulation", phase: "running", run, elapsedMs: 0, observations: [], error: null };
    this.publish(this.state);
    this.active = this.execute(plan).finally(() => { this.active = null; });
    return { sessionId: run.sessionId };
  }
  stop() { this.abort?.abort(); return this.active ?? Promise.resolve(); }
  async execute(plan) {
    const start = this.now();
    const adapter = new SimulatedAdapter(plan.scenario, this.wait);
    const signal = this.abort.signal;
    let terminal = "completed", reason = "已按预算结束；这是模拟采集";
    let index = 0, seq = 0;
    const supported = new Set();
    try {
      while (!signal.aborted) {
        const elapsed = this.now() - start;
        // Include request time and reserved flush time in the whole-run budget.
        if (elapsed + 1000 >= plan.budgetMs - plan.reserveMs) break;
        let command;
        if (index < plan.commands.length) command = plan.commands[index++];
        else {
          const live = OBD_LIVE_COMMANDS.filter((c) => supported.has(parseInt(c.slice(2), 16)));
          if (!live.length) { terminal = "partial"; reason = "没有确认可采样指标；请离线检查响应"; break; }
          command = live[(index++ - plan.commands.length) % live.length];
        }
        let observation;
        try {
          const raw = await adapter.request(command, signal);
          observation = decodeObdObservation(command, raw, ++seq, Math.round(this.now() - start));
        } catch (e) {
          const outcome = ["timeout", "disconnected", "cancelled"].includes(e.message) ? e.message : "invalid";
          observation = { ...decodeObdObservation(command, ">", ++seq, Math.round(this.now() - start)), outcome, detail: e.message, raw: "" };
        }
        await this.persist("observation", { sessionId: this.state.run.sessionId, observation });
        this.state.observations.push(observation);
        this.state.elapsedMs = Math.round(this.now() - start);
        for (const pids of Object.values(observation.supported)) for (const pid of pids) supported.add(pid);
        this.publish(this.state);
        if (observation.outcome === "disconnected") { terminal = "partial"; reason = "模拟设备掉线，已保存此前结果"; break; }
        if (command === "0100" && observation.outcome !== "ok") { terminal = "partial"; reason = "未建立有效 ECU 通信，已保存失败证据"; break; }
        if (index >= plan.commands.length) await this.wait(350, signal);
      }
      if (signal.aborted) { terminal = "cancelled"; reason = "已停止新请求并保存已收到的结果"; }
      else if (terminal === "completed" && (index < plan.commands.length || this.state.observations.some((o) => !["ok", "no-data"].includes(o.outcome)))) {
        terminal = "partial"; reason = "预算内完成部分采集；请查看缺项与原始记录";
      }
    } catch (e) {
      if (signal.aborted && e.message === "cancelled") { terminal = "cancelled"; reason = "用户停止采集"; }
      else {
        terminal = "interrupted"; reason = "存储或采集异常：" + e.message;
        this.state.error = reason;
      }
    }
    this.state.phase = "saving";
    this.state.elapsedMs = Math.round(this.now() - start);
    this.publish(this.state);
    try {
      const endedAt = new Date().toISOString();
      await this.persist("finish", { sessionId: this.state.run.sessionId, status: terminal, reason, endedAt });
      this.state.run = { ...this.state.run, status: terminal, reason, endedAt };
      this.state.phase = this.state.error ? "error" : "finished";
    } catch (e) {
      this.state.phase = "error"; this.state.error = "无法确认保存完成：" + e.message;
    }
    this.publish(this.state);
  }
}
