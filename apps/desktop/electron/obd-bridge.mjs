import readline from "node:readline";
import { ObdEngine } from "../../../packages/obd/src/engine.mjs";

let nextPersistId = 1;
const pending = new Map();
const send = (message) => process.stdout.write(JSON.stringify(message) + "\n");
const engine = new ObdEngine({
  publish: (state) => send({ kind: "state", state }),
  persist: (kind, value) => new Promise((resolve, reject) => {
    const id = nextPersistId++;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error("storage_ack_timeout")); }, 5000);
    pending.set(id, { resolve, reject, timer });
    send({ kind: "persist", id, operation: kind, value });
  }),
});
const input = readline.createInterface({ input: process.stdin });
input.on("line", async (line) => {
  let msg;
  try { if (line.length > 1_000_000) throw new Error("message_too_large"); msg = JSON.parse(line); }
  catch { return; }
  if (msg.kind === "persist-result") {
    const p = pending.get(msg.id);
    if (p) { clearTimeout(p.timer); pending.delete(msg.id); msg.error ? p.reject(new Error(msg.error)) : p.resolve(msg.result); }
    return;
  }
  try {
    let result;
    if (msg.method === "start") result = engine.start(msg.params);
    else if (msg.method === "stop") { await engine.stop(); result = engine.state; }
    else if (msg.method === "state") result = engine.state;
    else throw new Error("unknown_obd_method");
    send({ kind: "result", id: msg.id, result });
  } catch (e) { send({ kind: "result", id: msg.id, error: e.message }); }
});
input.on("close", () => { engine.abort?.abort(); process.exit(0); });
send({ kind: "ready" });
