import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import path from "node:path";
import { createCanCaptureManager } from "../apps/desktop/electron/can-capture.mjs";
import { createTransportGate } from "../apps/desktop/src/obd-connection-logic.mjs";
import { createReplayFixture } from "./can-replay-fixture.mjs";

const root = path.resolve(import.meta.dirname, "..");
const request = { action: "start", seconds: 20, confirmedReadOnly: true, x431Inactive: true };
const ctx = { ownerId: 1 };
function fixture(options = {}) {
  const gate = createTransportGate();
  let reserved = false, spawns = 0, resumes = 0, child;
  const conn = { selectedId: () => "bt:AABBCCDDEEFF", reserveDispatch: () => { if (reserved) return false; reserved = true; return true; },
    suspendForSession: options.pause || (async () => ({ ok: true })), resumeAfterSession: async () => { reserved = false; resumes++; } };
  const spawnFn = () => {
    spawns++;
    child = new EventEmitter();
    child.stdin = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
    child.stdin.write = (line) => {
      const doc = JSON.parse(line);
      if (options.throwWrite === doc.action) throw new Error("injected-input-failure");
      if (doc.action === "start") child.request = doc;
      if (doc.action === "cancel") {
        child.stdout.emit("data", Buffer.from(JSON.stringify({ type: "result", ok: false,
          capture: { device_id: child.request.deviceId, state: "cancelled" } }) + "\n"));
        queueMicrotask(() => child.emit("close", 1));
      }
    };
    child.kill = () => { if (!options.manualClose) queueMicrotask(() => child.emit("close", 1)); };
    return child;
  };
  const manager = createCanCaptureManager({ repoRoot: root, gate, conn, spawnFn, env: options.env || {}, allowInjectedLive: true });
  return { manager, gate, get child() { return child; }, stats: () => ({ spawns, resumes }) };
}
const denied = fixture({ env: { PORSCHE981_HEADLESS: "1" } });
assert.equal((await denied.manager.handle(request, ctx)).error, "live_not_enabled");
assert.equal(denied.stats().spawns, 0);
const f = fixture();
assert.equal((await f.manager.handle({ ...request, requestHex: "FFFF" }, ctx)).error, "invalid-request");
const started = await f.manager.handle(request, ctx);
assert.equal(started.ok, true); assert.equal(f.gate.owner(), "session");
assert.equal((await f.manager.handle(request, ctx)).error, "busy");
assert.equal((await f.manager.handle({ action: "cancel", jobId: started.jobId }, { ownerId: 9 })).error, "capture-job-not-found");
assert.equal(f.gate.owner(), "session");
await f.manager.handle({ action: "cancel", jobId: started.jobId }, ctx);
await new Promise((resolve) => setImmediate(resolve));
const cancelled = await f.manager.handle({ action: "status", jobId: started.jobId }, ctx);
assert.equal(cancelled.state, "cancelled"); assert.equal(f.gate.owner(), null); assert.equal(f.stats().resumes, 1);
const g = fixture();
g.gate.tryAcquire("session");
assert.equal((await g.manager.handle(request, ctx)).error, "busy");
assert.equal(g.gate.owner(), "session"); assert.equal(g.stats().spawns, 0);
const p = fixture();
const job = await p.manager.handle(request, ctx);
p.child.stdout.emit("data", Buffer.from('{"type":"result","ok":true,"capture":{"device_id":"bt:AABBCCDDEEFF","state":"received"}}\n'));
assert.equal(p.gate.owner(), "session"); // result alone must not release serial ownership
p.child.emit("close", 0);
await new Promise((resolve) => setImmediate(resolve));
assert.equal((await p.manager.handle({ action: "status", jobId: job.jobId }, ctx)).state, "completed");
let release;
const pending = fixture({ pause: () => new Promise((resolve) => { release = resolve; }) });
const begun = pending.manager.handle(request, ctx);
pending.manager.cancelOwned(1);
release({ ok: true });
assert.equal((await begun).error, "cancelled"); assert.equal(pending.stats().spawns, 0);
const h = fixture();
const malformed = await h.manager.handle(request, ctx);
h.child.stdout.emit("data", Buffer.from("invalid\n"));
await new Promise((resolve) => setImmediate(resolve));
assert.equal((await h.manager.handle({ action: "status", jobId: malformed.jobId }, ctx)).error, "capture-protocol-error");
assert.equal(h.gate.owner(), null);
for (const action of ["start", "cancel"]) {
  const broken = fixture({ throwWrite: action, manualClose: true });
  const opened = await broken.manager.handle(request, ctx);
  if (action === "start") assert.equal(opened.error, "injected-input-failure");
  else await broken.manager.handle({ action: "cancel", jobId: opened.jobId }, ctx);
  assert.equal(broken.gate.owner(), "session", "stdin failure must retain ownership until child close");
  assert.equal(broken.stats().resumes, 0);
  broken.child.emit("close", 1);
  await broken.manager.shutdown();
  assert.equal(broken.gate.owner(), null);
  assert.equal(broken.stats().resumes, 1);
}
const saved = createReplayFixture(root);
const real = createCanCaptureManager({ repoRoot: root, gate: createTransportGate(), conn: {}, env: { ...process.env, PORSCHE981_HEADLESS: "1" } });
try {
  const replay = await real.handle({ action: "replay", runId: saved.runId }, ctx);
  assert.equal(replay.ok, true); assert.equal(replay.integrityVerified, true); assert.equal(replay.captureQualityOk, false);
  assert.equal(replay.capture.frame_count, 6); assert.equal(replay.capture.adapter_notices["CAN ERROR"], 2);
} finally { await real.shutdown(); saved.cleanup(); }
console.log("can-capture.selfcheck: PASS ownership, no premature release, pending cancel, denied live, protocol abort, real offline replay; no hardware");
