/** Real OBD worker + real SQLite bridge, on an isolated temporary database. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createBridgeController } from "../apps/desktop/electron/bridge-lifecycle.mjs";
import { createObdController } from "../apps/desktop/electron/obd-controller.mjs";
import { validateObdRecording } from "../packages/domain/dist/index.js";

const root = path.resolve(import.meta.dirname, "..");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-obd-accept-"));
const dbPath = path.join(dir, "accept.db");
const processes = [];
let obdWorker;
function tracked(script, env = process.env) {
  const proc = spawn(process.execPath, [path.join(root, script)], { cwd: root, env, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  processes.push(proc); proc.stderr.on("data", () => {}); return proc;
}
const db = createBridgeController({ spawnBridge: () => tracked("apps/desktop/electron/db-bridge.mjs", { ...process.env, PORSCHE981_DB: dbPath }), maxRetries: 0 });
const obd = createObdController({ dbCall: (method, params) => db.call(method, params), spawnWorker: () => { obdWorker = tracked("apps/desktop/electron/obd-bridge.mjs"); return obdWorker; } });
async function until(test, timeout = 10000) {
  const end = Date.now() + timeout;
  while (!await test()) { if (Date.now() > end) throw new Error("accept_timeout"); await new Promise((r) => setTimeout(r, 30)); }
}
try {
  db.start(); await until(() => db.getStatus().state === "ready");
  const manual = await db.call("obdSessions:create", { note: "manual regression" });
  await db.call("obdDtcs:add", { session_id: manual.id, code: "P0300" });
  const start = await obd.start({ scenario: "normal", budgetMs: 5000 });
  await assert.rejects(() => obd.start({ scenario: "normal", budgetMs: 5000 }));
  await until(() => obd.getState().phase === "finished");
  const original = await obd.recording(start.run.sessionId);
  assert.equal(original.run.status, "completed");
  assert.ok(original.observations.some((o) => o.samples.some((s) => s.context === "live")));
  assert.deepEqual(validateObdRecording(JSON.parse(JSON.stringify(original))), original);
  const replay = await obd.replay(start.run.sessionId);
  assert.equal(replay.mode, "replay"); assert.deepEqual(replay.observations, original.observations);
  assert.equal((await db.call("obdSessions:list")).length, 1, "simulation must not pollute manual history");
  assert.equal((await db.call("obdDtcs:list", manual.id))[0].code, "P0300");

  const cancellation = await obd.start({ scenario: "normal", budgetMs: 15000 });
  await until(() => obd.getState().observations.length >= 2);
  await obd.stop();
  assert.equal((await obd.recording(cancellation.run.sessionId)).run.status, "cancelled");

  const crash = await obd.start({ scenario: "normal", budgetMs: 15000 });
  await until(() => obd.getState().observations.length >= 2);
  obdWorker.kill();
  await until(() => obd.getState().phase === "error");
  await until(async () => (await obd.recording(crash.run.sessionId)).run.status === "interrupted");
  assert.ok((await obd.recording(crash.run.sessionId)).observations.length >= 2);

  const missing = await obd.start({ scenario: "no-response", budgetMs: 5000 });
  await until(() => obd.getState().phase === "finished");
  const failed = await obd.recording(missing.run.sessionId);
  assert.equal(failed.run.status, "partial"); assert.equal(failed.observations[0].outcome, "timeout");
  console.log("PASS OBD offline: acquisition, persistence, raw replay, manual isolation, cancellation, worker crash, restart, no response");
} finally {
  await obd.shutdown(); db.stop();
  await Promise.all(processes.map(async (proc) => {
    if (proc.exitCode === null && proc.signalCode === null) { const exited = once(proc, "exit"); proc.kill(); await exited; }
  }));
  fs.unlinkSync(dbPath); fs.rmdirSync(dir);
}
