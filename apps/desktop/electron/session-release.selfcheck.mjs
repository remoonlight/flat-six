/** Actual managers with injected workers only; no adapter/vehicle I/O. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

const root = path.resolve(process.argv[2] || process.cwd());
const { createObdConnectionManager, attachSessionHandoff } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/obd-connection.mjs")));
const { createReadOnlySessionManager } = await import(pathToFileURL(path.join(root, "apps/desktop/electron/read-only-session.mjs")));
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-session-release-"));
const devices = ["bt:AABBCCDDEEFF", "usb:" + "a".repeat(64), "tcp:192.0.2.1:35000", "vnci:123"];
const endings = ["clean", "read-failed-clean", "poison-closed", "cleanup-error", "missing-cleanup", "malformed-cleanup",
  "wrong-close-kind", "killed", "trailing-output", "signal-after-final", "unexpected-exit", "forced-late-final", "python-missing"];
function child() {
  const c = new EventEmitter();
  Object.assign(c, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
  c.kill = () => { c.killed = true; return true; }; return c;
}
let checks = 0;
try {
  for (const deviceId of devices) for (const kind of ["diagnostic", "engine"]) for (const ending of endings) {
    const native = deviceId.startsWith("vnci:");
    let worker, opens = 0;
    const conn = createObdConnectionManager({ repoRoot: root,
      env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `${checks}.json`) },
      listFn: async () => ({ devices: [{ id: deviceId, brand: native ? "VNCI" : "vLinker", available: true }] }),
      monitorSpawnFn: () => {
        opens++; const monitor = child();
        monitor.stdin.on("data", () => { monitor.stdout.write(JSON.stringify({ type: "stopped", restoration: { errors: [] } }) + "\n");
          monitor.exitCode = 0; monitor.emit("close", 0); }); return monitor;
      } });
    if (deviceId.startsWith("tcp:"))
      assert.equal((await conn.handle({ action: "configure-network", host: "192.0.2.1", port: 35000 })).ok, true);
    await conn.handle({ action: "list" });
    const selected = await conn.handle({ action: "select", deviceId, model: native ? "VNCI" : "vLinker" });
    assert.equal(selected.ok, true, `${deviceId}/${kind}/${ending}: ${JSON.stringify(selected)}`);
    const manager = attachSessionHandoff(conn, createReadOnlySessionManager({ repoRoot: root, gate: conn.gate,
      getLiveDeviceId: () => conn.selectedId(), getLiveAdapterModel: () => conn.snapshot().model,
      onSessionEnded: job => conn.resumeAfterSession({ nativeReleaseVerified: job.nativeReleaseVerified }),
      allowInjectedLive: true, env: {}, timeoutMs: 1000, gracefulMs: 10, killWatchMs: 60,
      spawnFn() {
        if (ending === "python-missing") throw Object.assign(new Error("missing"), { code: "ENOENT" });
        worker = child(); return worker;
      } }), { allowInjectedLive: true, env: {} });
    const label = `${deviceId}/${kind}/${ending}`;
    try {
      const request = { action: "start", profileId: "porsche-981-2014-dme", mode: "live", confirmedReadOnly: true, x431Inactive: true,
        ...(kind === "engine" ? { sessionTask: "engine", sampleCycles: 1, intervalMs: 500, selectedPids: ["0C"] } : {}) };
      const started = await manager.handle(request, { ownerId: 1 }); assert.equal(started.ok, true, label);
      const safe = ["clean", "read-failed-clean", "python-missing"].includes(ending) || ending === "poison-closed" && !native;
      if (ending !== "python-missing") {
        if (ending === "forced-late-final") {
          await manager.handle({ action: "cancel", jobId: started.jobId }, { ownerId: 1 });
          await new Promise(resolve => setTimeout(resolve, 20)); assert.equal(worker.killed, true, label);
        }
        if (ending !== "killed") {
          const errors = ending === "malformed-cleanup" ? "" : ending === "cleanup-error" ? ["close:EIO"] : ending === "poison-closed" ? ["poisoned:disconnect"] : [];
          const ok = !["read-failed-clean", "forced-late-final", "poison-closed"].includes(ending);
          const doc = { type: "result", ok, error: ok ? null : "cancelled", profileId: request.profileId, mode: "live",
            status: ok ? "completed" : "failed", runId: "sessioncheckxxxxxxxx", results: [], simulation: false, liveVerified: false, writePayload: null,
            ...(ending === "missing-cleanup" ? {} : { restoration: { errors,
              adapterClose: ending === "wrong-close-kind" ? "unknown" : native ? "D-PDU disconnect/destruct" : "ATPC" } }),
            ...(kind === "engine" ? { sessionTask: "engine", engine: { selectedPids: ["0C"], supportedPids: ["0C"], unsupportedPids: [],
              completedCycles: 1, sampleCycles: 1, intervalMs: 500, samples: [{ pid: "0C", label: "Engine RPM", value: 42, unit: "rpm",
                capturedUtc: "2026-10-12T00:00:00Z", elapsedMs: 1, cycle: 1, synthetic: false }] } } : {}) };
          worker.stdout.write(JSON.stringify(doc) + "\n");
          if (ending === "trailing-output") worker.stdout.write("invalid\n");
        }
        assert.equal(conn.gate.owner(), "session", `${label}: report cannot release before process close`);
        const openStatus = await manager.handle({ action: "status", jobId: started.jobId }, { ownerId: 1 });
        assert.equal(openStatus.released, false, `${label}: UI cannot resume before process close`);
        assert.equal(openStatus.releaseVerified, false, `${label}: report alone does not qualify release`);
        worker.exitCode = ["signal-after-final", "killed"].includes(ending) ? null : ending === "unexpected-exit" ? 2 : ["read-failed-clean", "forced-late-final", "poison-closed"].includes(ending) ? 1 : 0;
        worker.emit("close", worker.exitCode);
      }
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(conn.gate.owner(), null, label);
      const status = await manager.handle({ action: "status", jobId: started.jobId }, { ownerId: 1 });
      assert.equal(status.released, true, `${label}: closed worker reported`);
      assert.equal(status.releaseVerified, safe, `${label}: UI sees actual cleanup verdict`);
      if (kind === "engine" && !["killed", "python-missing"].includes(ending))
        assert.equal(status.final.engine.samples[0].value, 42, `${label}: retained result survives cleanup verdict`);
      const connected = await conn.handle({ action: "connect" });
      assert.equal(connected.error, safe ? undefined : "adapter_release_not_verified", `${label}: physical release verdict`);
      assert.equal(opens, safe ? 1 : 0, `${label}: unresolved cleanup must not open monitor`);
      if (!safe) {
        assert.equal(status.error, "adapter_release_not_verified", label);
        assert.equal(conn.reserveDispatch(), false, `${label}: next diagnosis stays blocked`);
        assert.equal((await conn.handle({ action: "list" })).error, "adapter_release_not_verified", label);
        assert.equal((await conn.resumeAfterSession({ nativeReleaseVerified: true })).error, "adapter_release_not_verified", `${label}: no stale success clears failure`);
      }
      checks++;
    } finally { await manager.shutdown(); await conn.shutdown(); }
  }
} finally { fs.rmSync(scratch, { recursive: true, force: true }); }
console.log(JSON.stringify({ passed: true, checks, transportInjected: true, deviceIO: false, vehicleIO: false }));
