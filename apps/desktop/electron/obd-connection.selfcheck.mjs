import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import fs from "node:fs";
import path from "node:path";
import {
  attachSessionHandoff,
  createObdConnectionManager,
  createTransportGate,
  nextRetryDelayMs,
} from "./obd-connection.mjs";
import { createReadOnlySessionManager } from "./read-only-session.mjs";
import { acceptMonitorReading, parseVoltageVolts, VOLTAGE_FRESH_MS, RETRY_BACKOFF_MS } from "../src/obd-connection-logic.mjs";

const repoRoot = process.cwd();
const scratch = path.join(repoRoot, ".local/selfchecks/obd-connection");
fs.mkdirSync(scratch, { recursive: true });
const device = { id: "bt:0425E85BD4CB", brand: "vLinker", available: true, paired: true };
let serial = 0;
function manager(extra = {}) {
  return createObdConnectionManager({
    repoRoot,
    env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `accept-${Date.now()}-${serial++}.json`) },
    listFn: async () => ({ devices: [device] }),
    handshakeMs: extra.handshakeMs ?? 60_000,
    livenessMs: extra.livenessMs ?? 60_000,
    ...extra,
  });
}
async function select(m) {
  await m.handle({ action: "list" });
  assert.equal((await m.handle({ action: "select", deviceId: device.id })).ok, true);
}
function fakeChild() {
  const child = new EventEmitter();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = () => {
    child.killed = true;
    return true;
  };
  child.exitCode = null;
  return child;
}
const hs = (volts = 12.6, at = 1000) =>
  JSON.stringify({
    ok: true,
    type: "handshake",
    volts,
    voltageSource: "atrv",
    simulation: false,
    deviceId: device.id,
    at,
  }) + "\n";

assert.equal(acceptMonitorReading({ ok: true, simulation: false, type: "handshake", volts: 12.6, voltageSource: "atrv", deviceId: device.id, at: 1000 }, { deviceId: device.id, now: 1000, freshMs: 6000 }).volts, 12.6);
assert.equal(acceptMonitorReading({ ok: true, simulation: false, type: "handshake", volts: 12.6, voltageSource: "atrv", deviceId: "bt:000000000000", at: 1000 }, { deviceId: device.id, now: 1000, freshMs: 6000 }).reject, "device_mismatch");
assert.equal(acceptMonitorReading({ ok: true, type: "handshake", volts: 12.6, voltageSource: "atrv", simulation: true, deviceId: device.id, at: 1000 }, { deviceId: device.id, now: 1000, freshMs: 6000 }).reject, "synthetic");
assert.equal(acceptMonitorReading({ ok: true, simulation: false, type: "handshake", volts: 12.6, voltageSource: "atrv", deviceId: device.id, at: 9000 }, { deviceId: device.id, now: 1000, freshMs: 6000 }).reject, "stale");
assert.equal(acceptMonitorReading({ ok: false, type: "error", error: "adapter-identity-mismatch" }, { deviceId: device.id, now: 1000, freshMs: 6000 }).fatal, true);

for (const raw of ["bad12.6V", "1.12.6V", "-12.6V", "12.6V\rERROR", "12.6V\r13.0V"]) assert.equal(parseVoltageVolts(raw), null);
assert.equal(parseVoltageVolts("ATRV\r12.6V\r>"), 12.6);
assert.deepEqual([...RETRY_BACKOFF_MS], [5000, 10000, 20000, 30000]);
assert.equal(nextRetryDelayMs(1), 5000);
assert.equal(nextRetryDelayMs(4), 30000);
assert.equal(VOLTAGE_FRESH_MS, 6000);

const gate = createTransportGate();
const m = manager({ gate });
await select(m);
gate.tryAcquire("session");
for (const action of ["select", "clear", "disconnect", "connect"]) {
  const req = action === "select" ? { action, deviceId: device.id } : { action };
  assert.equal((await m.handle(req)).error, "busy");
}
gate.release("session");
await m.shutdown();

let child;
const processGate = createTransportGate();
const processManager = manager({
  gate: processGate,
  monitorSpawnFn: () => {
    child = fakeChild();
    return child;
  },
});
await select(processManager);
await processManager.handle({ action: "connect" });
assert.match(String(processGate.owner()), /^monitor:/);
child.stdout.emit("data", Buffer.alloc(65 * 1024, 65));
assert.equal(child.killed, true);
assert.match(String(processGate.owner()), /^monitor:/, "retain lock before child closes");
child.exitCode = 1;
child._exited = true;
child.emit("close", 1);
assert.equal(processGate.owner(), null);
await processManager.shutdown();

{
  const g = createTransportGate();
  let stuck;
  const stuckMgr = manager({
    gate: g,
    gracefulMs: 20,
    killWaitMs: 20,
    monitorSpawnFn: () => {
      stuck = fakeChild();
      stuck.kill = () => {
        stuck.killed = true;
        return false;
      };
      return stuck;
    },
  });
  await select(stuckMgr);
  await stuckMgr.handle({ action: "connect" });
  const token = g.owner();
  assert.match(String(token), /^monitor:/);
  const t0 = Date.now();
  const sus = await stuckMgr.suspendForSession();
  assert.equal(sus.ok, false);
  assert.equal(sus.error, "monitor_close_timeout");
  assert.equal(g.owner(), token, "lock retained until actual close");
  assert.ok(Date.now() - t0 >= 15);
  stuck.exitCode = 1;
  stuck._exited = true;
  stuck.emit("close", 1);
  assert.equal(g.owner(), null);
  await stuckMgr.shutdown();
}

const late = manager({
  now: () => 1000,
  monitorSpawnFn: () => {
    child = fakeChild();
    return child;
  },
});
await select(late);
await late.handle({ action: "connect" });
await late.shutdown();
child.stdout.emit("data", Buffer.from(hs()));
assert.equal(late.snapshot().connected, false);
assert.equal(late.snapshot().voltageVolts, null);

let now = 1_000;
const fresh = manager({ now: () => now, monitorSpawnFn: () => { child = fakeChild(); return child; } });
await select(fresh);
await fresh.handle({ action: "connect" });
child.stdout.emit("data", Buffer.from(hs(12.6, 1000)));
assert.equal(fresh.snapshot().voltageVolts, 12.6);
now += 6001;
assert.equal(fresh.snapshot().voltageVolts, null);
await fresh.handle({ action: "disconnect" });
assert.equal(fresh.snapshot().voltageVolts, null);
await fresh.shutdown();

const delays = [];
const timers = [];
let child2;
const retryGate = createTransportGate();
const retryer = manager({
  gate: retryGate,
  handshakeMs: 120000,
  livenessMs: 120000,
  monitorSpawnFn: () => {
    child2 = fakeChild();
    return child2;
  },
  setTimeoutFn: (fn, ms) => {
    delays.push(ms);
    const id = timers.length;
    timers.push({ fn, ms });
    return id;
  },
  clearTimeoutFn: () => {},
});
await select(retryer);
await retryer.handle({ action: "connect" });
child2.exitCode = 1;
child2._exited = true;
child2.emit("close", 1);
assert.equal(retryer.snapshot().linkState, "reconnecting");
assert.ok(delays.includes(5000));
const retryFns = timers.filter((t) => t.ms === 5000 || t.ms === 10000 || t.ms === 20000 || t.ms === 30000);
retryFns[0].fn();
child2.exitCode = 1;
child2._exited = true;
child2.emit("close", 1);
retryFns[1]?.fn?.();
child2.exitCode = 1;
child2._exited = true;
child2.emit("close", 1);
retryFns[2]?.fn?.();
child2.exitCode = 1;
child2._exited = true;
child2.emit("close", 1);
const retryDelays = delays.filter((d) => d === 5000 || d === 10000 || d === 20000 || d === 30000);
assert.deepEqual(retryDelays.slice(0, 4), [5000, 10000, 20000, 30000]);
await retryer.handle({ action: "disconnect" });
assert.equal(retryer.snapshot().linkState, "idle");
const before = delays.length;
timers.filter((t) => t.ms === 30000).at(-1)?.fn?.();
assert.equal(delays.filter((d) => d === 5000).length, delays.filter((d) => d === 5000).length);
void before;
await retryer.shutdown();

const syn = manager({ now: () => 1000, monitorSpawnFn: () => { child = fakeChild(); return child; } });
await select(syn);
await syn.handle({ action: "connect" });
const lastAt = syn.snapshot().voltageAt;
child.stdout.emit("data", Buffer.from(JSON.stringify({ ok: true, volts: 12.6, voltageSource: "atrv", simulation: true, type: "handshake", deviceId: device.id, at: 1000 }) + "\n"));
assert.equal(syn.snapshot().voltageAt, lastAt);
child.stdout.emit("data", Buffer.from("not-json\n"));
assert.equal(syn.snapshot().voltageVolts, null);
child.stdout.emit("data", Buffer.from(hs(11.9, 1000)));
assert.equal(syn.snapshot().voltageVolts, 11.9);
child.stdout.emit("data", Buffer.from(JSON.stringify({ ok: false, type: "error", error: "adapter-identity-mismatch" }) + "\n"));
assert.equal(syn.snapshot().voltageVolts, null);
assert.equal(syn._state().wantConnected, false);
await syn.handle({ action: "disconnect" });
await syn.shutdown();

{
  const g = createTransportGate();
  const silent = manager({
    gate: g,
    handshakeMs: 30,
    monitorSpawnFn: () => {
      child = fakeChild();
      child.kill = () => {
        child.killed = true;
        return false;
      };
      return child;
    },
  });
  await select(silent);
  await silent.handle({ action: "connect" });
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(child.killed, true);
  assert.match(String(g.owner()), /^monitor:/);
  child.exitCode = 1;
  child._exited = true;
  child.emit("close", 1);
  await silent.shutdown();
}

{
  const g = createTransportGate();
  const conn = manager({
    gate: g,
    now: () => Date.now(),
    gracefulMs: 20,
    killWaitMs: 20,
    monitorSpawnFn: () => {
      child = fakeChild();
      child.kill = () => {
        child.killed = true;
        return false;
      };
      return child;
    },
  });
  await select(conn);
  await conn.handle({ action: "connect" });
  child.stdout.emit("data", Buffer.from(hs(12.6, Date.now())));
  const sess = attachSessionHandoff(
    conn,
    createReadOnlySessionManager({
      repoRoot,
      gate: g,
      spawnFn: () => {
        throw new Error("session must not spawn while monitor alive");
      },
    }),
  );
  const first = sess.handle({ action: "prepare", profileId: "porsche-981-2014-dme", mode: "simulation" }, { ownerId: 1 });
  const second = await sess.handle({ action: "prepare", profileId: "porsche-981-2014-dme", mode: "simulation" }, { ownerId: 1 });
  assert.equal(second.error, "busy");
  const out = await first;
  assert.equal(out.ok, false);
  assert.equal(out.error, "monitor_close_timeout");
  child.exitCode = 0;
  child._exited = true;
  child.emit("close", 0);
  await conn.shutdown();
}

{
  let n = 0;
  const g = createTransportGate();
  const fb = manager({
    gate: g,
    now: () => 1000,
    pythonCandidates: [
      { exe: "missing-a", prefix: [] },
      { exe: "ok-b", prefix: [] },
    ],
    monitorSpawnFn: () => {
      n += 1;
      if (n === 1) {
        const err = new Error("missing");
        err.code = "ENOENT";
        throw err;
      }
      child = fakeChild();
      return child;
    },
  });
  await select(fb);
  await fb.handle({ action: "connect" });
  child.stdout.emit("data", Buffer.from(hs(12.6, 1000)));
  assert.equal(n, 2);
  assert.equal(fb.snapshot().voltageVolts, 12.6);
  await fb.shutdown();
}

{
  const g = createTransportGate();
  let stuck;
  const ov = manager({
    gate: g,
    gracefulMs: 20,
    killWaitMs: 20,
    monitorSpawnFn: () => {
      stuck = fakeChild();
      stuck.kill = () => {
        stuck.killed = true;
        return false;
      };
      return stuck;
    },
  });
  await select(ov);
  await ov.handle({ action: "connect" });
  const disc = ov.handle({ action: "disconnect" });
  const sel = ov.handle({ action: "select", deviceId: device.id });
  const out = await disc;
  assert.equal(out.ok, false);
  assert.equal(out.error, "monitor_close_timeout");
  assert.equal((await sel).error, undefined);
  assert.equal(g.owner() != null, true);
  stuck.exitCode = 0;
  stuck._exited = true;
  stuck.emit("close", 0);
  await ov.shutdown();
}

{
  const second = { ...device, id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+" };
  let opens = 0;
  const queued = manager({ listFn: async () => ({ devices: [device, second] }), monitorSpawnFn: () => { opens++; return fakeChild(); } });
  await select(queued);
  const selecting = queued.handle({ action: "select", deviceId: second.id });
  const connecting = queued.handle({ action: "connect", deviceId: device.id });
  assert.equal(queued.reserveDispatch(), false, "pending selection reserves mutations before diagnostic dispatch");
  assert.equal((await selecting).selectedDeviceId, second.id);
  assert.equal((await connecting).error, "device_mismatch", "queued explicit connect cannot silently target the next selected device");
  assert.equal(opens, 0);
  await queued.shutdown();
}

{
  const mx = { ...device, id: "bt:AABBCCDDEEFF", brand: "OBDLink MX+" };
  const unresolved = { ...device, id: "bt:112233445566", brand: "unresolved" };
  const stateFile = path.join(scratch, `mx-persist-${Date.now()}.json`);
  let child;
  let opens = 0;
  const monitorRequests = [];
  const options = {
    env: { PORSCHE981_CONNECTION_STATE: stateFile },
    listFn: async () => ({ devices: [device, mx, unresolved] }),
    now: () => 1000,
    monitorSpawnFn: () => {
      opens++;
      child = fakeChild();
      child.stdin.on("data", (data) => {
        monitorRequests.push(JSON.parse(String(data)));
        if (String(data).includes('"stop"')) {
          child.exitCode = 0;
          child.emit("close", 0);
        }
      });
      return child;
    },
  };
  const conn = manager(options);
  await conn.handle({ action: "list" });
  const selected = await conn.handle({ action: "select", deviceId: mx.id });
  assert.equal(selected.model, "OBDLink MX+");
  await conn.handle({ action: "connect", deviceId: mx.id });
  assert.equal(monitorRequests[0].deviceId, mx.id);
  child.stdout.emit("data", Buffer.from(hs().replace(device.id, mx.id)));
  assert.equal(conn.snapshot().connected, true);
  assert.equal(conn.snapshot().voltageVolts, 12.6);
  await conn.handle({ action: "disconnect" });
  assert.equal(conn.snapshot().voltageVolts, null);
  await conn.shutdown();

  const restored = manager(options);
  assert.equal(restored.selectedId(), mx.id);
  assert.equal(restored.snapshot().model, "OBDLink MX+");
  assert.equal(restored.snapshot().connected, false, "restart must not open a device automatically");
  await restored.handle({ action: "list" });
  const unknown = await restored.handle({ action: "select", deviceId: unresolved.id });
  assert.equal(unknown.model, null, "new device must not inherit MX+ model");
  assert.equal((await restored.handle({ action: "connect" })).error, "device_model_required");
  assert.equal(opens, 1, "unresolved model must not open transport");
  const connected = await restored.handle({ action: "connect", model: "OBDLink MX+" });
  assert.equal(connected.model, "OBDLink MX+");
  assert.equal(JSON.parse(fs.readFileSync(stateFile, "utf8")).model, "OBDLink MX+");
  await restored.shutdown();
}

// Explicit networks persist; unqualified MX+ networks never reuse drive CAN.
{
  let opens = 0;
  const stateFile = path.join(scratch, `networks-${Date.now()}.json`);
  const mx = { ...device, brand: "OBDLink MX+" };
  const options = { env: { PORSCHE981_CONNECTION_STATE: stateFile }, listFn: async () => ({ devices: [mx] }),
    monitorSpawnFn: () => { opens++; return fakeChild(); } };
  let settings = manager(options);
  await settings.handle({ action: "list" });
  await settings.handle({ action: "select", deviceId: mx.id });
  for (const canNetwork of ["drive", "chassis", "comfort", "crash"]) {
    assert.equal((await settings.handle({ action: "configure", purpose: "internal", canNetwork })).ok, true);
    await settings.shutdown();
    settings = manager(options);
    assert.equal(settings.snapshot().canNetwork, canNetwork);
    assert.equal(settings.snapshot().purpose, "internal");
    assert.equal(settings.snapshot().connected, false);
    if (canNetwork !== "drive") {
      await settings.handle({ action: "list" });
      assert.equal((await settings.handle({ action: "connect" })).error, "internal_profile_not_qualified");
    }
  }
  assert.equal(opens, 0);
  await settings.shutdown();
}
console.log("obd-connection selfcheck PASS: close-timeout quarantine, cadence, backoff, ingest, silent child, queued device identity, MX+ persistence and explicit model");

// Current list, persistent purpose/CAN, unavailable remembered head, and no startup open.
{
  let opens = 0;
  const stateFile = path.join(scratch, `settings-${Date.now()}.json`);
  const settings = manager({ env: { PORSCHE981_CONNECTION_STATE: stateFile },
    listFn: async () => ({ devices: [device, { ...device, id: "bt:AABBCCDDEEFF", available: false }, { id: "pt3g:1", brand: "PT3G", available: true }] }),
    monitorSpawnFn: () => { opens++; return fakeChild(); } });
  assert.equal((await settings.handle({ action: "list" })).devices.length, 1);
  await settings.handle({ action: "configure", purpose: "internal" });
  assert.equal((await settings.handle({ action: "configure", canNetwork: "guess" })).error, "invalid_can_network");
  await settings.handle({ action: "configure", canNetwork: "adas" });
  await settings.handle({ action: "select", deviceId: device.id });
  assert.equal((await settings.handle({ action: "connect" })).error, "internal_profile_not_qualified");
  assert.equal(opens, 0, "internal selection must never fall back to diagnostic commands");
  await settings.shutdown();
  const restored = manager({ env: { PORSCHE981_CONNECTION_STATE: stateFile }, listFn: async () => ({ devices: [] }), monitorSpawnFn: () => { opens++; return fakeChild(); } });
  assert.equal(restored.snapshot().purpose, "internal");
  assert.equal(restored.snapshot().canNetwork, "adas");
  assert.equal(restored.snapshot().selectedDeviceId, device.id);
  await restored.handle({ action: "list" });
  await restored.handle({ action: "configure", purpose: "diagnostic" });
  assert.equal((await restored.handle({ action: "connect" })).error, "device_port_unavailable");
  assert.equal(opens, 0);
  await restored.shutdown();
}
{
  let child;
  const g = createTransportGate();
  const conn = manager({ gate: g, now: () => 1000, gracefulMs: 15, killWaitMs: 15,
    monitorSpawnFn: () => { child = fakeChild(); return child; } });
  await select(conn);
  await conn.handle({ action: "connect" });
  child.stdout.emit("data", Buffer.from(JSON.stringify({ ok: true, type: "handshake", deviceId: device.id,
    simulation: false, commOk: true, volts: null, voltageSource: "atrv", at: 1000 }) + "\n"));
  assert.equal(conn.snapshot().connected, true);
  assert.equal(conn.snapshot().voltageVolts, null);
  const stopping = conn.handle({ action: "disconnect" });
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(conn.snapshot().linkState, "disconnecting");
  assert.equal((await stopping).error, "monitor_close_timeout");
  assert.equal(conn.snapshot().linkState, "close_failed");
  assert.ok(g.owner(), "failed close retains port ownership");
  child.exitCode = 0; child.emit("close", 0);
  assert.equal(conn.snapshot().linkState, "idle");
  assert.equal(g.owner(), null);
  await conn.shutdown();
}
{
  const g = createTransportGate();
  const conn = manager({ gate: g });
  let cancelled = false;
  const session = { handle: async () => ({ ok: true }), cancelActiveAndWait: async () => {
    cancelled = true; g.release("session"); conn.releaseDispatch(); return { ok: true };
  } };
  attachSessionHandoff(conn, session);
  g.tryAcquire("session");
  assert.equal((await conn.handle({ action: "disconnect" })).ok, true);
  assert.equal(cancelled, true);
  assert.equal(conn.snapshot().linkState, "idle");
  await conn.shutdown();
}
console.log("obd-connection convergence checks PASS: settings, current devices, unknown voltage, truthful close, session disconnect");

// Pipe reads can coalesce many valid JSON lines beyond the per-line bound.
// Do not turn healthy buffered output into a timeout/reconnect.
{
  let child;
  const events = [];
  const conn = manager({ now: () => 1000, onMonitorEvent: (event) => events.push(event),
    monitorSpawnFn: () => {
      child = fakeChild();
      child.stdin.on("data", (data) => {
        if (String(data).includes('"stop"')) {
          child.stdout.emit("data", Buffer.from('{"type":"stopped"}\n'));
          child.exitCode = 0; child.emit("close", 0);
        }
      });
      return child;
    } });
  await select(conn);
  for (let cycle = 0; cycle < 14; cycle++) {
    await conn.handle({ action: "connect" });
    const burst = Buffer.from(hs().repeat(600));
    assert.ok(burst.length > 65536);
    child.stdout.emit("data", burst);
    assert.equal(conn.snapshot().connected, true);
    assert.equal(child.killed, undefined);
    child.stderr.write(Buffer.alloc(100000, 120));
    await conn.handle({ action: "disconnect" });
    const closed = conn.snapshot().monitorDiagnostics.events.at(-1);
    assert.equal(closed.type, "closed"); assert.equal(closed.stopReported, true);
    assert.equal(closed.stderrBytes, 100000);
    assert.equal(closed.lastAcceptedAt, 1000);
    assert.equal(conn.gate.owner(), null);
  }
  assert.equal(conn.snapshot().monitorDiagnostics.starts, 14);
  assert.equal(conn.snapshot().monitorDiagnostics.events.length, 32);
  assert.ok(conn.snapshot().monitorDiagnostics.omittedEvents > 0);
  assert.ok(events.length > 32, "optional audit receives every lifecycle event");
  assert.equal(events.some((event) => event.type === "forced-kill"), false);
  await conn.shutdown();
}
{
  let child;
  const conn = manager({ now: () => 1000, gracefulMs: 1, killWaitMs: 1,
    monitorSpawnFn: () => { child = fakeChild(); return child; } });
  await select(conn); await conn.handle({ action: "connect" });
  child.stdout.emit("data", Buffer.from(hs()));
  // A genuinely oversized UTF-8 line must still fail and retain the port lock.
  child.stdout.emit("data", Buffer.from(JSON.stringify({ note: "测".repeat(25000) }) + "\n"));
  assert.equal(conn.snapshot().connectionError, "output_cap");
  assert.equal(conn.snapshot().connected, false);
  assert.equal(child.killed, true); assert.ok(conn.gate.owner());
  child.stdout.emit("data", Buffer.from(hs()));
  assert.equal(conn.snapshot().connected, false, "late output cannot revive failed worker");
  await conn.handle({ action: "disconnect" });
  child.exitCode = 0; child.emit("close", 0);
  await conn.shutdown();
}
console.log("obd-connection stream checks PASS: coalesced lines, UTF-8 byte cap, stderr drain, bounded lifecycle evidence");
