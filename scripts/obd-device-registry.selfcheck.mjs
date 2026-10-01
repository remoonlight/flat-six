import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { buildDeviceRegistry } from "../apps/desktop/electron/obd-device-registry.mjs";
import { createObdConnectionManager } from "../apps/desktop/electron/obd-connection.mjs";

const known = [
  { id: "bt:AABBCCDDEEFF", family: "vLinker", mac: "AABBCCDDEEFF", lastSeenAt: "2026-09-26T00:00:00Z" },
  { id: "vnci:10001", family: "VNCI", serial: "10001", firmware: "qualified-version" },
  { id: "pt3g:20002", family: "PT3G", serial: "20002", usbInstanceId: "USB\\VID_0BDA&PID_8152\\CONFIRMED" },
  { id: "x431:AABB", family: "X431" },
  { id: "tablet:test", family: "X431-tablet" },
];
const host = { drivers: { VNCI: { installed: true, version: "29.0.0" }, PT3G: { installed: true, version: "E70" } },
  services: [{ Name: "VciToolServerPORSCHE", State: "Running" }],
  usb: [{ InstanceId: known[2].usbInstanceId, Status: "OK" }] };
const device = { id: known[0].id, brand: "vLinker", available: true, paired: true, comPort: "COM15" };
const fresh = buildDeviceRegistry({ devices: [device], host, known, checkedAt: "2026-10-01T00:00:00Z" });
assert.equal(fresh.registry[0].comPort, "COM15");
assert.equal(fresh.registry[0].connectable, true);
assert.equal(fresh.registry[1].present, false);
assert.equal(fresh.registry[1].connectable, false);
assert.equal(fresh.registry[1].driverInstalled, true);
assert.equal(fresh.registry[2].state, "USB 在线");
assert.equal(fresh.registry[2].serviceState, "Running");
assert.equal(fresh.registry[2].connectable, false);
assert.deepEqual(fresh.registry[2].routes, []);
assert.deepEqual(fresh.registry[3].routes, ["offline", "coding"]);
assert(!JSON.stringify(fresh.known).includes("COM15"), "never remember a COM port as routing identity");
const offline = buildDeviceRegistry({ known: fresh.known, host: { ...host, usb: [] } });
assert(offline.registry.every((d) => !d.present && !d.connectable));
assert.equal(offline.registry[0].lastSeenAt, fresh.registry[0].lastSeenAt);
assert(offline.registry.every((d) => !("voltageVolts" in d)), "historical voltage must never appear live");
const otherNic = buildDeviceRegistry({ known, host: { usb: [{ InstanceId: "USB\\VID_0BDA&PID_8152\\OTHER", Status: "OK" }] } });
assert.equal(otherNic.registry[2].present, false);
assert.equal(buildDeviceRegistry({ host: { usb: [{ InstanceId: "USB\\VID_0BDA&PID_8152\\OTHER", Status: "OK" }] } }).registry.length, 0);

const scratch = path.resolve(".local/device-settings-check");
fs.mkdirSync(scratch, { recursive: true });
const state = path.join(scratch, `connection-${process.pid}.json`);
const registryFile = state.replace(/\.json$/, ".devices.json");
fs.writeFileSync(registryFile, JSON.stringify({ version: 1, devices: known }));
let rows = [device];
let spawned = 0;
const opts = { repoRoot: process.cwd(), env: { PORSCHE981_CONNECTION_STATE: state },
  listFn: async () => ({ devices: rows, host }), spawnFn: () => { spawned++; throw new Error("must not open hardware"); } };
const manager = createObdConnectionManager(opts);
try {
  const list = await manager.handle({ action: "list" });
  assert.equal(list.deviceRegistry.length, 5);
  assert.equal((await manager.handle({ action: "select", deviceId: device.id })).ok, true);
  assert.equal((await manager.handle({ action: "select", deviceId: known[2].id })).error, "malformed_device_id");
  rows = [];
  const gone = await manager.handle({ action: "list" });
  assert.equal(gone.devices.length, 0);
  assert(gone.deviceRegistry.every((d) => !d.connectable));
  assert.equal(gone.voltageVolts, null);
  assert.equal(spawned, 0);
} finally { await manager.shutdown(); }
const restored = createObdConnectionManager(opts);
try {
  assert.equal(restored.snapshot().deviceRegistry.length, 5);
  assert(restored.snapshot().deviceRegistry.every((d) => !d.present && !d.connectable));
} finally { await restored.shutdown(); }
console.log("Device registry: offline persistence, exact USB matching, current COM selection, routes and hardware-free listing passed");
