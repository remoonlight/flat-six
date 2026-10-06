import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createObdConnectionManager, validateConnectionRequest } from "../apps/desktop/electron/obd-connection.mjs";
import { acceptMonitorReading } from "../apps/desktop/src/obd-connection-logic.mjs";

const id = "vnci:10001";
const context = { deviceId: id, now: 1000, freshMs: 6000 };
const handshake = { ok: true, simulation: false, type: "handshake", deviceId: id,
  volts: 12.6, voltageSource: "d-pdu-vbatt", at: 1000 };
assert.equal(validateConnectionRequest({ action: "select", deviceId: id }), null);
assert.equal(validateConnectionRequest({ action: "connect", model: "VNCI" }), null);
assert.equal(validateConnectionRequest({ action: "connect", deviceId: "vnci:../bad" }).error, "malformed_device_id");
assert.equal(validateConnectionRequest({ action: "connect", deviceId: id, dll: "evil.dll" }).error, "forbidden_field");
assert.equal(acceptMonitorReading(handshake, context).source, "d-pdu-vbatt");
assert.equal(acceptMonitorReading({ ...handshake, voltageSource: "atrv" }, context).reject, "malformed");
assert.equal(acceptMonitorReading({ ...handshake, deviceId: "vnci:99999" }, context).reject, "device_mismatch");
assert.equal(acceptMonitorReading({ ...handshake, volts: 0 }, context).reject, "malformed");
assert.equal(acceptMonitorReading({ ...handshake, volts: null }, context).reject, "malformed");
assert.equal(acceptMonitorReading({ ...handshake, volts: null, commOk: true }, context).volts, null);
assert.equal(acceptMonitorReading({ ...handshake, simulation: true }, context).reject, "synthetic");
assert.equal(acceptMonitorReading({ ...handshake, at: -9999 }, context).reject, "stale");
// Reproduce the real USB bench sample rejected 1 ms ahead of Date.now().
const receivedAt = 1791243849741;
for (const type of ["handshake", "reading"]) {
  const reading = { ...handshake, type, volts: null, commOk: true };
  const receipt = { ...context, now: receivedAt };
  assert.equal(acceptMonitorReading({ ...reading, at: "2026-10-05T23:44:09.742108+00:00" }, receipt).at, receivedAt);
  assert.equal(acceptMonitorReading({ ...reading, at: receivedAt + 2 }, receipt).at, receivedAt);
  assert.equal(acceptMonitorReading({ ...reading, at: receivedAt + 3 }, receipt).reject, "stale");
  assert.equal(acceptMonitorReading({ ...reading, at: receivedAt - 6000 }, receipt).at, receivedAt - 6000);
  assert.equal(acceptMonitorReading({ ...reading, at: receivedAt - 6001 }, receipt).reject, "stale");
  assert.equal(acceptMonitorReading({ ...reading, at: receivedAt + 1, deviceId: "vnci:99999" }, receipt).reject, "device_mismatch");
  assert.equal(acceptMonitorReading({ ...reading, at: receivedAt + 1, simulation: true }, receipt).reject, "synthetic");
}

const scratch = path.resolve(".local/vnci-support/selfcheck");
fs.mkdirSync(scratch, { recursive: true });
const stateFile = path.join(scratch, `connection-${process.pid}.json`);
const child = new EventEmitter();
Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
child.kill = () => { child.exitCode = 0; child.emit("close", 0); return true; };
child.stdin.on("data", (bytes) => {
  if (String(bytes).includes('"stop"')) {
    child.stdout.write(JSON.stringify({ type: "stopped", restoration: { errors: [] } }) + "\n");
    child.exitCode = 0; child.emit("close", 0);
  }
});
let opened = 0;
const options = { repoRoot: process.cwd(), env: { PORSCHE981_CONNECTION_STATE: stateFile },
  now: () => 1000, handshakeMs: 3000, livenessMs: 3000,
  listFn: async () => ({ devices: [{ id, brand: "VNCI", transport: "d-pdu-usb", serial: "10001", available: true }] }),
  spawnFn: () => { opened++; return child; } };
const manager = createObdConnectionManager(options);
try {
  await manager.handle({ action: "list" });
  assert.equal((await manager.handle({ action: "select", deviceId: id })).model, "VNCI");
  await manager.handle({ action: "connect" });
  child.stdout.write(JSON.stringify({ ...handshake, at: 1001 }) + "\n");
  assert.equal(manager.snapshot().connected, true);
  assert.equal(manager.snapshot().voltageSource, "d-pdu-vbatt");
  assert.equal(manager.snapshot().voltageVolts, 12.6);
  child.stdout.write(JSON.stringify({ ...handshake, type: "reading", at: 1002, volts: null, commOk: true }) + "\n");
  assert.equal(manager.snapshot().connected, true);
  assert.equal(manager.snapshot().voltageVolts, null);
  assert.equal(manager.snapshot().voltageLabel, "电压 -- V");
  child.stdout.write(JSON.stringify({ ...handshake, type: "reading", volts: 12.7, commOk: true }) + "\n");
  assert.equal(manager.snapshot().connected, true);
  assert.equal(manager.snapshot().voltageVolts, 12.7);
  assert.equal(opened, 1);
  assert.equal((await manager.suspendForSession()).ok, true);
  assert.equal(manager.snapshot().connected, false);
  assert.equal(manager.snapshot().voltageVolts, null);
  await manager.handle({ action: "disconnect" });
} finally { await manager.shutdown(); }
const restored = createObdConnectionManager(options);
try {
  assert.equal(restored.selectedId(), id);
  assert.equal(restored.snapshot().model, "VNCI");
  assert.equal(restored.snapshot().connected, false);
} finally { await restored.shutdown(); }
console.log("VNCI connection selfcheck passed: USB identity, voltage provenance, persistence and exclusive session handoff (synthetic)");

// A native worker's first error ends its readings, but it still owns the
// adapter until cleanup finishes. Late output must not revive that worker or
// postpone its bounded cleanup deadline.
for (const brand of ["VNCI", "PT3G"]) {
  for (const ending of ["released", "hung"]) {
    const nativeId = `${brand.toLowerCase()}:12345`;
    const nativeDevice = { id: nativeId, brand, available: true, transport: "d-pdu-usb", serial: "12345" };
    const timers = [];
    let worker, opens = 0;
    const conn = createObdConnectionManager({ repoRoot: process.cwd(), now: () => 1000,
      env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `failure-${process.pid}-${brand}-${ending}.json`) },
      gracefulMs: 10, handshakeMs: 3000, livenessMs: 6000,
      setTimeoutFn: (callback, ms) => { const timer = { callback, ms, cleared: false }; timers.push(timer); return timer; },
      clearTimeoutFn: (timer) => { timer.cleared = true; },
      listFn: async () => ({ devices: [nativeDevice] }),
      monitorSpawnFn: () => {
        opens++;
        worker = new EventEmitter();
        Object.assign(worker, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
        worker.kill = () => { worker.killed = true; worker.emit("close", null, "SIGTERM"); return true; };
        worker.stdin.on("data", (bytes) => {
          if (!String(bytes).includes('"stop"')) return;
          worker.stdout.write(JSON.stringify({ type: "stopped", restoration: { errors: [] } }) + "\n");
          worker.exitCode = 0; worker.emit("close", 0);
        });
        return worker;
      } });
    const activeTimers = () => timers.filter((timer) => !timer.cleared);
    const emit = (doc) => worker.stdout.write(JSON.stringify(doc) + "\n");
    try {
      await conn.handle({ action: "list" }); await conn.handle({ action: "select", deviceId: nativeId });
      await conn.handle({ action: "connect" });
      const sample = { ...handshake, deviceId: nativeId };
      emit(sample); assert.equal(conn.snapshot().connected, true);
      const error = { type: "error", ok: false, error: "adapter-identity-mismatch" };
      emit(error);
      assert.equal(conn.snapshot().connected, false);
      assert.equal(worker.killed, undefined, "allow native cleanup before termination");
      const deadline = activeTimers()[0];
      assert.equal(deadline.ms, 10);
      emit({ ...sample, type: "reading" });
      assert.equal(conn.snapshot().connected, false, `${brand}: late readings cannot revive a failed worker`);
      assert.equal(conn.snapshot().voltageVolts, null);
      assert.equal(conn.snapshot().linkState, "disconnecting");
      assert.equal((await conn.handle({ action: "connect" })).error, "port_release_pending");
      for (let repeat = 0; repeat < 3; repeat++) emit(error);
      assert.deepEqual(activeTimers(), [deadline], "repeated errors cannot extend native cleanup");
      if (ending === "released") {
        emit({ type: "stopped", restoration: { errors: [] } });
        worker.exitCode = 1; worker.emit("close", 1);
        assert.equal(conn.gate.owner(), null);
        assert.equal(conn.snapshot().linkState, "idle");
        assert.equal(conn.snapshot().connectionError, "adapter-identity-mismatch", "keep the failure visible after clean release");
      } else {
        deadline.cleared = true; deadline.callback();
        assert.equal(worker.killed, true);
        assert.equal(conn.snapshot().linkState, "close_failed");
        // A delayed success report after forced exit is not proof of release.
        emit({ type: "stopped", restoration: { errors: [] } });
        assert.equal((await conn.handle({ action: "connect" })).error, "adapter_release_not_verified");
        assert.equal((await conn.handle({ action: "list" })).error, "adapter_release_not_verified");
        assert.equal((await conn.suspendForSession()).error, "adapter_release_not_verified");
      }
      assert.equal(opens, 1);
    } finally { await conn.shutdown(); }
  }
}
console.log("Native failure checks PASS: late output denied, fixed cleanup deadline, error retained, unknown release blocks reuse (synthetic)");
