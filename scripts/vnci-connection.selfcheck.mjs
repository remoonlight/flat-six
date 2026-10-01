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
assert.equal(acceptMonitorReading({ ...handshake, simulation: true }, context).reject, "synthetic");
assert.equal(acceptMonitorReading({ ...handshake, at: -9999 }, context).reject, "stale");

const scratch = path.resolve(".local/vnci-support/selfcheck");
fs.mkdirSync(scratch, { recursive: true });
const stateFile = path.join(scratch, `connection-${process.pid}.json`);
const child = new EventEmitter();
Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
child.kill = () => { child.exitCode = 0; child.emit("close", 0); return true; };
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
  child.stdout.write(JSON.stringify(handshake) + "\n");
  assert.equal(manager.snapshot().connected, true);
  assert.equal(manager.snapshot().voltageSource, "d-pdu-vbatt");
  assert.equal(manager.snapshot().voltageVolts, 12.6);
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
