/** Offline inventory/mode guards; no real port, network or device session. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
const root = path.resolve(process.argv[2] || process.cwd());
const load = file => import(pathToFileURL(path.join(root, file)));
const { createObdConnectionManager, attachSessionHandoff } = await load("apps/desktop/electron/obd-connection.mjs");
const { createReadOnlySessionManager } = await load("apps/desktop/electron/read-only-session.mjs");
const { createManufacturerStream } = await load("apps/desktop/electron/manufacturer-stream.mjs");
const { buildDeviceRegistry } = await load("apps/desktop/electron/obd-device-registry.mjs");
const { topologyCapability, topologyAdapterProgress } = await load("apps/desktop/src/can-topology-capabilities.mjs");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-cantiecar-"));
let checks = 0;
try {
  for (const deviceId of ["bt:AABBCCDDEEFF", "usb:" + "a".repeat(64), "tcp:192.0.2.1:35000"]) {
    let opens = 0;
    const forbidden = () => { opens++; throw new Error("unqualified channel opened"); };
    const conn = createObdConnectionManager({ repoRoot: root, env: { PORSCHE981_CONNECTION_STATE: path.join(temp, `${checks}.json`) },
      listFn: async () => ({ devices: [{ id: deviceId, brand: "CANtieCAR", name: "CANtieCAR v4", available: true, comPort: "COM21" }] }),
      monitorSpawnFn: forbidden });
    const ordinary = attachSessionHandoff(conn, createReadOnlySessionManager({ repoRoot: root, gate: conn.gate, env: {},
      allowInjectedLive: true, spawnFn: forbidden, getLiveDeviceId: () => conn.selectedId(), getLiveAdapterModel: () => conn.snapshot().model }),
      { allowInjectedLive: true, spawnFn: forbidden, env: {} });
    const manufacturer = createManufacturerStream({ repoRoot: root, connection: conn, gate: conn.gate, env: {}, allowInjectedLive: true, spawnFn: forbidden });
    try {
      if (deviceId.startsWith("tcp:")) assert.equal((await conn.handle({ action: "configure-network", host: "192.0.2.1", port: 35000 })).ok, true);
      const list = await conn.handle({ action: "list" });
      assert.equal(list.devices.some(d => d.id === deviceId), true);
      assert.equal((await conn.handle({ action: "select", deviceId, model: "CANtieCAR" })).ok, true);
      if (!deviceId.startsWith("tcp:")) {
        assert.equal((await conn.handle({ action: "select", deviceId, model: "ELM327" })).error, "adapter_model_mismatch");
        const row = buildDeviceRegistry({ devices: list.devices }).registry.find(d => d.id === deviceId);
        assert.equal(row.family, "CANtieCAR");assert.equal(row.connectable, false);assert.deepEqual(row.routes, []);
        assert.match(row.state, /未完成/);
      }
      for (const extra of [{}, { model: "CANtieCAR" }, { model: "ELM327" }])
        assert.equal((await conn.handle({ action: "connect", ...extra })).error, "adapter-mode-not-qualified");
      for (const extra of [{}, { sessionTask: "engine" }])
        assert.equal((await ordinary.handle({ action: "start", profileId: "porsche-981-2014-dme", mode: "live", confirmedReadOnly: true,
          x431Inactive: true, ...extra }, { ownerId: 1 })).error, "adapter-mode-not-qualified");
      assert.equal((await manufacturer.handle({ action: "start-read", profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
        parameterIds: ["a".repeat(64)], continuous: true, confirmedReadOnly: true, x431Inactive: true }, 1)).error, "adapter-mode-not-qualified");
      assert.equal(opens, 0);assert.equal(conn.gate.owner(), null);
      const capability = topologyCapability({ profileId: "porsche-981-2014-dme" }, "CANtieCAR", deviceId);
      assert.equal(capability.readable, false);assert.equal(capability.clearable, false);
      assert.match(topologyAdapterProgress("CANtieCAR"), /工作模式.*未完成/);
      checks++;
    } finally { await ordinary.shutdown();await manufacturer.shutdown();await conn.shutdown(); }
  }
} finally { fs.rmSync(temp, { recursive: true, force: true }); }
console.log(JSON.stringify({ passed: true, transportCases: checks, channelOpens: 0, deviceIO: false, vehicleIO: false }));
