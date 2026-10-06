/** Synthetic adapter lifecycle and denied ECU dispatch; no hardware access. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createObdConnectionManager, attachSessionHandoff } from "../apps/desktop/electron/obd-connection.mjs";
import { buildDeviceRegistry } from "../apps/desktop/electron/obd-device-registry.mjs";

const scratch = path.resolve(".local/obd-heads-20261006/selfcheck");
fs.mkdirSync(scratch, { recursive: true });
const device = { id: "pt3g:10001", brand: "PT3G", serial: "10001", transport: "d-pdu-usb", available: true };
const row = buildDeviceRegistry({ devices: [device] }).registry[0];
assert.equal(row.connectable, true);
assert.deepEqual(row.routes, []);

for (const ending of ["clean", "forced", "cleanup-error", "exit-without-release"]) {
  let child, opens = 0, dispatched = 0, lists = 0;
  const events = [];
  const manager = createObdConnectionManager({ repoRoot: process.cwd(), now: () => 1000,
    env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `${process.pid}-${ending}.json`) },
    gracefulMs: 10, killWaitMs: 10, handshakeMs: 3000, livenessMs: 3000,
    listFn: async () => { lists++; return { devices: [device] }; }, onMonitorEvent: (event) => events.push(event),
    monitorSpawnFn: () => {
      opens++;
      child = new EventEmitter();
      Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
      child.kill = () => { child.emit("close", null, "SIGTERM"); return true; };
      child.stdin.on("data", (bytes) => {
        if (!String(bytes).includes('"stop"') || ending === "forced") return;
        if (ending !== "exit-without-release") child.stdout.write(JSON.stringify({ type: "stopped",
          restoration: { errors: ending === "cleanup-error" ? ["native close failed"] : [] } }) + "\n");
        child.exitCode = 0; child.emit("close", 0);
      });
      return child;
    } });
  try {
    await manager.handle({ action: "list" });
    assert.equal((await manager.handle({ action: "select", deviceId: device.id })).model, "PT3G");
    await manager.handle({ action: "connect" });
    child.stdout.write(JSON.stringify({ type: "handshake", ok: true, simulation: false, deviceId: device.id,
      commOk: true, volts: null, at: 1000, voltageSource: "d-pdu-vbatt" }) + "\n");
    assert.equal(manager.snapshot().connected, true);
    assert.equal(manager.snapshot().voltageVolts, null);
    assert.equal((await manager.handle({ action: "list" })).error, "disconnect_before_refresh");
    assert.equal(lists, 1);
    assert.equal(manager.snapshot().connected, true);
    const session = attachSessionHandoff(manager, { handle: async () => { dispatched++; return { ok: true }; },
      cancelActiveAndWait: async () => ({ ok: true }) }, { allowInjectedLive: true, spawnFn: () => {}, env: {} });
    assert.equal((await session.handle({ action: "start", mode: "live", profileId: "porsche-981-2014-dme",
      operationIds: ["dme-dtc"], confirmedReadOnly: true, x431Inactive: true })).error,
      "pt3g-vehicle-transport-not-qualified");
    assert.equal(dispatched, 0);
    const result = await manager.handle({ action: "disconnect" });
    if (ending === "clean") {
      assert.equal(result.ok, true);
      assert.equal(manager.snapshot().linkState, "idle");
      assert.equal(manager.gate.owner(), null);
    } else {
      assert.equal(result.error, "adapter_release_not_verified");
      assert.equal(manager.snapshot().linkState, "close_failed");
      assert.equal((await manager.handle({ action: "connect" })).error, "adapter_release_not_verified");
      assert.equal((await manager.handle({ action: "list" })).error, "adapter_release_not_verified");
      assert.equal((await manager.suspendForSession()).error, "adapter_release_not_verified");
      assert.equal((await manager.handle({ action: "select", deviceId: device.id })).error, "adapter_release_not_verified");
      assert.equal(opens, 1);
      assert.ok(events.some((event) => event.type === "native-release-unverified"));
    }
  } finally { await manager.shutdown(); }
}
console.log("PT3G connection PASS: native provenance, unknown voltage, denied ECU dispatch, verified release and failed-release latch (synthetic)");
