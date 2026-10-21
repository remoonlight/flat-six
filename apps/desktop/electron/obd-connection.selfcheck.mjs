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
    gracefulMs: 20, killWaitMs: 20,
    ...extra,
  });
}
async function select(m) {
  await m.handle({ action: "list" });
  assert.equal((await m.handle({ action: "select", deviceId: device.id })).ok, true);
}
function fakeChild(graceful = true) {
  const child = new EventEmitter();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = () => {
    child.killed = true;
    return true;
  };
  child.exitCode = null;
  if (graceful) child.stdin.on("data", bytes => {
    if (!String(bytes).includes('"stop"')) return;
    cleanStopped(child);
    queueMicrotask(() => {
      if (child._exited) return;
      child.exitCode = 0; child.emit("close", 0);
    });
  });
  return child;
}
function cleanStopped(child) {
  child.stdout.write(JSON.stringify({ type: "stopped", ok: true, restoration: { errors: [] } }) + "\n");
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

// Terminating a handshake uses the existing disconnect route, including native
// heads. Late output cannot revive the connection while cleanup is pending.
for (const waitingDevice of [device,
  { id: "vnci:10001", brand: "VNCI", available: true },
  { id: "pt3g:10001", brand: "PT3G", available: true }]) {
  let child, stopCount = 0;
  const m = manager({ gracefulMs: 1000, listFn: async () => ({ devices: [waitingDevice] }), monitorSpawnFn: () => {
    child = fakeChild(false);
    child.stdin.on("data", bytes => { if (JSON.parse(String(bytes)).action === "stop") stopCount++; });
    return child;
  } });
  try {
    await m.handle({ action: "list" });
    assert.equal((await m.handle({ action: "select", deviceId: waitingDevice.id })).ok, true);
    assert.equal((await m.handle({ action: "connect" })).linkState, "connecting");
    const terminating = m.handle({ action: "disconnect" });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(stopCount, 1);
    assert.equal(m.snapshot().linkState, "disconnecting");
    assert.equal(m.reserveDispatch(), false, "release must finish before vehicle dispatch");
    child.stdout.write(JSON.stringify({ ok: true, type: "handshake", simulation: false,
      deviceId: waitingDevice.id, volts: 12.6, voltageSource: waitingDevice.id.startsWith("bt:") ? "atrv" : "d-pdu-vbatt", at: Date.now() }) + "\n");
    assert.equal(m.snapshot().connected, false);
    assert.equal(m.snapshot().linkState, "disconnecting");
    cleanStopped(child); child.exitCode = 0; child.emit("close", 0);
    const ended = await terminating;
    assert.equal(ended.ok, true); assert.equal(ended.linkState, "idle");
    assert.equal(ended.selectedDeviceId, waitingDevice.id);
    assert.equal(ended.purpose, "diagnostic"); assert.equal(m.gate.owner(), null);
    assert.equal(ended.monitorDiagnostics.starts, 1, "termination never starts a replacement monitor");
    assert.equal((await m.handle({ action: "connect" })).linkState, "connecting");
    const last = m.handle({ action: "disconnect" });
    await new Promise(resolve => setImmediate(resolve));
    cleanStopped(child); child.exitCode = 0; child.emit("close", 0); await last;
  } finally { await m.shutdown(); }
}

// Exercise the real selection/monitor/handoff route using an OS USB instance ID.
// No serial or native API is opened by this injected worker.
{
  const usb = { id: "usb:" + "a".repeat(64), brand: "vLinker", available: true, paired: false,
    transport: "usb-serial", comPort: "COM21", usbInstanceId: "USB\\VID_0403&PID_6001\\SYNTHETIC01" };
  const children = [], requests = [];
  const m = manager({ listFn: async () => ({ devices: [usb] }), monitorSpawnFn: () => {
    const child = fakeChild(); child.stdin.on("data", bytes => requests.push(JSON.parse(String(bytes))));
    children.push(child); return child;
  } });
  try {
    assert.equal((await m.handle({ action: "list" })).devices.length, 1);
    assert.equal((await m.handle({ action: "select", deviceId: usb.id })).model, "vLinker");
    assert.equal((await m.handle({ action: "connect" })).ok, true);
    assert.equal(children.length, 1);
    assert.equal(requests[0].deviceId, usb.id);
    children[0].stdout.write(JSON.stringify({ ok: true, type: "handshake", simulation: false,
      deviceId: usb.id, volts: 12.6, voltageSource: "atrv", at: Date.now() }) + "\n");
    assert.equal(m.snapshot().connected, true);
    assert.equal(m.snapshot().deviceRegistry[0].connection, "USB / 串口");
    assert.equal((await m.suspendForSession()).ok, true);
    assert.equal(children[0].exitCode, 0);
    assert.equal(m.snapshot().connected, false);
  } finally { await m.shutdown(); }
}

for (const model of ["ELM327", "OBDKey", "OBDLink"]) {
  const usb = { id: "usb:" + "b".repeat(64), brand: "unresolved", available: true, transport: "usb-serial" };
  const requests = [];
  const m = manager({ listFn: async () => ({ devices: [usb] }), monitorSpawnFn: () => {
    const child = fakeChild(); child.stdin.on("data", bytes => requests.push(JSON.parse(String(bytes)))); return child;
  } });
  try {
    await m.handle({ action: "list" }); await m.handle({ action: "select", deviceId: usb.id });
    assert.equal((await m.handle({ action: "connect" })).error, "device_model_required");
    assert.equal((await m.handle({ action: "select", deviceId: usb.id, model })).model, model);
    assert.equal((await m.handle({ action: "connect" })).ok, true);
    assert.equal(requests[0].adapterModel, model);
    assert.equal((await m.handle({ action: "disconnect" })).ok, true);
  } finally { await m.shutdown(); }
}

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
      stuck = fakeChild(false);
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

// Diagnostic connection loss waits for a choice, after verified release.
const delays = [];
const timers = [];
let child2, retryOpens = 0;
const retryGate = createTransportGate();
const retryer = manager({
  gate: retryGate,
  handshakeMs: 120000,
  livenessMs: 120000,
  monitorSpawnFn: () => { retryOpens++; child2 = fakeChild(); return child2; },
  setTimeoutFn: (fn, ms) => { delays.push(ms); const id = timers.length; timers.push({ fn, ms }); return id; },
  clearTimeoutFn: () => {},
});
await select(retryer);
await retryer.handle({ action: "connect" });
for (let attempt = 0; attempt < 2; attempt++) {
  child2.stdout.write(hs(12.6, Date.now()));
  assert.equal(retryer.snapshot().connected, true);
  child2.stdout.write(JSON.stringify({ type: "error", ok: false, error: "port-io" }) + "\n");
  assert.equal((await retryer.handle({ action: "connect" })).error, "port_release_pending");
  assert.equal(retryOpens, attempt + 1, "retry cannot open before old worker releases");
  cleanStopped(child2); child2.exitCode = 1; child2.emit("close", 1);
  assert.equal(retryer.snapshot().linkState, "retry-required");
  assert.equal(retryer.snapshot().selectedDeviceId, device.id);
  assert.equal(retryer.snapshot().voltageVolts, null);
  assert.equal(retryer._state().wantConnected, false);
  assert.equal(retryGate.owner(), null);
  assert.deepEqual(delays.filter(d => [5000, 10000, 20000, 30000].includes(d)), [], "no automatic diagnostic retry timer");
  await retryer.handle({ action: "status" });
  assert.equal(retryOpens, attempt + 1, "status cannot retry on behalf of user");
  if (attempt === 0) {
    assert.equal((await retryer.handle({ action: "connect", deviceId: device.id })).ok, true);
    assert.equal(retryOpens, 2, "one explicit retry opens exactly one new worker");
  }
}
assert.equal((await retryer.handle({ action: "disconnect" })).ok, true);
assert.equal(retryer.snapshot().linkState, "idle");
assert.equal(retryer.snapshot().selectedDeviceId, device.id, "terminate leaves the selected head available");
assert.equal(retryOpens, 2);
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
      child = fakeChild(false);
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
      child = fakeChild(false);
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
      stuck = fakeChild(false);
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
  assert.equal(restored.selectedId(), null);
  assert.equal(restored.snapshot().model, null);
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

// Startup resets the purpose/network; explicit unqualified networks remain blocked.
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
    assert.equal(settings.snapshot().canNetwork, null);
    assert.equal(settings.snapshot().purpose, "diagnostic");
    assert.equal(settings.snapshot().selectedDeviceId, null);
    assert.equal(settings.snapshot().connected, false);
    await settings.handle({ action: "list" });
    assert.equal((await settings.handle({ action: "connect" })).error, "device_not_selected");
    await settings.handle({ action: "select", deviceId: mx.id });
    await settings.handle({ action: "configure", purpose: "internal", canNetwork });
    if (canNetwork !== "drive") {
      assert.equal((await settings.handle({ action: "connect" })).error, "internal_profile_not_qualified");
    }
  }
  assert.equal(opens, 0);
  await settings.shutdown();
}
console.log("obd-connection selfcheck PASS: close-timeout quarantine, cadence, explicit diagnostic retry, ingest, silent child, queued device identity, startup defaults and explicit model");

// Current list, ignored historical purpose/head, and no startup/list/configure open.
{
  let opens = 0;
  const stateFile = path.join(scratch, `settings-${Date.now()}.json`);
  const settings = manager({ env: { PORSCHE981_CONNECTION_STATE: stateFile },
    listFn: async () => ({ devices: [device, { ...device, id: "bt:AABBCCDDEEFF", available: false }, { id: "pt3g:1", brand: "PT3G", available: true }] }),
    monitorSpawnFn: () => { opens++; return fakeChild(); } });
  assert.deepEqual((await settings.handle({ action: "list" })).devices.map((d) => d.id), [device.id, "pt3g:1"]);
  await settings.handle({ action: "configure", purpose: "internal" });
  assert.equal((await settings.handle({ action: "configure", canNetwork: "guess" })).error, "invalid_can_network");
  await settings.handle({ action: "configure", canNetwork: "adas" });
  await settings.handle({ action: "select", deviceId: device.id });
  assert.equal((await settings.handle({ action: "connect" })).error, "internal_profile_not_qualified");
  assert.equal(opens, 0, "internal selection must never fall back to diagnostic commands");
  await settings.shutdown();
  const restored = manager({ env: { PORSCHE981_CONNECTION_STATE: stateFile }, listFn: async () => ({ devices: [] }), monitorSpawnFn: () => { opens++; return fakeChild(); } });
  assert.equal(restored.snapshot().purpose, "diagnostic");
  assert.equal(restored.snapshot().canNetwork, null);
  assert.equal(restored.snapshot().selectedDeviceId, null);
  await restored.handle({ action: "list" });
  await restored.handle({ action: "configure", purpose: "diagnostic" });
  assert.equal((await restored.handle({ action: "connect" })).error, "device_not_selected");
  assert.equal((await restored.handle({ action: "select", deviceId: device.id })).error, "unknown_device");
  assert.equal(restored.snapshot().selectedDeviceId, null, "a missing historical head cannot be selected");
  assert.equal((await restored.handle({ action: "connect" })).error, "device_not_selected");
  assert.equal(opens, 0);
  await restored.shutdown();
}
{
  let child;
  const g = createTransportGate();
  const conn = manager({ gate: g, now: () => 1000, gracefulMs: 15, killWaitMs: 15,
    monitorSpawnFn: () => { child = fakeChild(false); return child; } });
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
  assert.equal(conn.snapshot().linkState, "close_failed", "late unverified close cannot erase the release failure");
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
  assert.equal((await conn.handle({ action: "disconnect" })).error, "busy");
  assert.equal(cancelled, false, "connection settings must not cancel the active vehicle task");
  assert.equal(g.owner(), "session", "busy disconnect keeps transport ownership unchanged");
  g.release("session");
  conn.releaseDispatch();
  assert.equal((await conn.handle({ action: "disconnect" })).ok, true);
  assert.equal(cancelled, false);
  assert.equal(conn.snapshot().linkState, "idle");
  await conn.shutdown();
}
console.log("obd-connection convergence checks PASS: settings, current devices, unknown voltage, truthful close, busy disconnect rejected");

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
          cleanStopped(child);
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
// A head change owns the entire stop/select transition. It never opens the new
// head until a later explicit connect and never publishes a selection on failure.
for (const closes of [true, false]) {
  const next = { ...device, id: "bt:001122334455", brand: "OBDLink MX+" };
  let oldWorker, opens = 0;
  const conn = manager({ now: () => 1000, listFn: async () => ({ devices: [device, next] }),
    monitorSpawnFn: () => {
      opens++; oldWorker = fakeChild(closes);
      if (!closes) oldWorker.kill = () => false;
      queueMicrotask(() => oldWorker.stdout.write(hs()));
      return oldWorker;
    } });
  await conn.handle({ action: "list" });
  await conn.handle({ action: "configure", purpose: "internal", canNetwork: "drive" });
  assert.equal((await conn.handle({ action: "select", deviceId: device.id })).purpose, "internal", "choosing the first head preserves an explicitly chosen purpose");
  await conn.handle({ action: "configure", purpose: "diagnostic" });
  await conn.handle({ action: "connect" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(conn.snapshot().connected, true);
  assert.equal((await conn.handle({ action: "select", deviceId: "bt:999999999999" })).error, "unknown_device");
  assert.equal(conn.snapshot().connected, true, "invalid selection does not disconnect the old head");
  const changed = await conn.handle({ action: "select", deviceId: next.id });
  assert.equal(opens, 1, "selecting another head does not connect it automatically");
  if (closes) {
    assert.equal(changed.ok, true); assert.equal(changed.selectedDeviceId, next.id);
    assert.equal(changed.model, next.brand); assert.equal(changed.connected, false);
    assert.equal(changed.linkState, "idle"); assert.equal(oldWorker.exitCode, 0);
    assert.equal(changed.purpose, "diagnostic"); assert.equal(changed.canNetwork, null);
    assert.equal(conn.gate.owner(), null);
  } else {
    assert.equal(changed.ok, false); assert.equal(changed.error, "monitor_close_timeout");
    assert.equal(changed.selectedDeviceId, device.id); assert.equal(changed.model, device.brand);
    assert.equal(changed.linkState, "close_failed"); assert.ok(conn.gate.owner());
    oldWorker.exitCode = 0; cleanStopped(oldWorker); oldWorker.emit("close", 0);
  }
  await conn.shutdown();
}
{
  const next = { ...device, id: "bt:001122334455", brand: "OBDLink MX+" };
  const conn = manager({ listFn: async () => ({ devices: [device, next] }) });
  await select(conn);
  await conn.handle({ action: "configure", purpose: "internal", canNetwork: "comfort" });
  assert.equal((await conn.handle({ action: "select", deviceId: device.id })).purpose, "internal", "reselecting the same head does not reset purpose");
  const changed = await conn.handle({ action: "select", deviceId: next.id });
  assert.equal(changed.purpose, "diagnostic"); assert.equal(changed.canNetwork, null);
  assert.equal(changed.connected, false);
  assert.equal((await conn.handle({ action: "configure", purpose: "internal", canNetwork: "chassis" })).purpose, "internal", "the user can choose a different purpose for the new head before connecting");
  await conn.shutdown();
}
{
  const next = { ...device, id: "bt:001122334455", brand: "OBDLink MX+" };
  let listed = [device, next], failed = false, opens = 0;
  const conn = manager({ listFn: async () => {
    if (failed) throw new Error("inventory unavailable");
    return { devices: listed };
  }, monitorSpawnFn: () => { opens++; throw new Error("refresh must not open a head"); } });
  await select(conn);
  await conn.handle({ action: "configure", purpose: "internal", canNetwork: "comfort" });
  const retained = await conn.handle({ action: "list" });
  assert.equal(retained.selectedDeviceId, device.id); assert.equal(retained.purpose, "internal");
  assert.equal(retained.canNetwork, "comfort"); assert.equal(retained.connected, false);
  listed = [next];
  const removed = await conn.handle({ action: "list" });
  assert.equal(removed.ok, true); assert.equal(removed.selectedDeviceId, null);
  assert.equal(removed.model, null); assert.equal(removed.serialBaud, null);
  assert.equal(removed.purpose, "diagnostic"); assert.equal(removed.canNetwork, null);
  assert.equal((await conn.handle({ action: "list" })).selectedDeviceId, null, "refresh never selects the remaining head");
  await conn.handle({ action: "select", deviceId: next.id });
  failed = true;
  const unavailable = await conn.handle({ action: "list" });
  assert.equal(unavailable.ok, false); assert.equal(unavailable.selectedDeviceId, next.id, "a failed inventory is not evidence that the head disappeared");
  assert.equal(opens, 0);
  await conn.shutdown();
}
console.log("obd-connection stream checks PASS: bounded lifecycle, serial head switch, failed-release retention and manual refresh selection");
