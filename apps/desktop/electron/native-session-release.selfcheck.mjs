/** Injected workers only: an exited native process is not proof of head cleanup. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createObdConnectionManager, attachSessionHandoff } from "./obd-connection.mjs";
import { createManufacturerStream } from "./manufacturer-stream.mjs";
import { createReadOnlySessionManager } from "./read-only-session.mjs";

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-native-release-"));
const selection = { action: "start-read", profileId: "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
  parameterIds: ["a".repeat(64)], continuous: true, confirmedReadOnly: true, x431Inactive: true };
function child() {
  const c = new EventEmitter();
  Object.assign(c, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
  c.kill = () => { c.killed = true; return true; };
  return c;
}
let checks = 0;
try {
  for (const kind of ["manufacturer", "diagnostic"]) {
    for (const ending of ["clean", "read-failed-clean", "cleanup-error", "missing-cleanup", "malformed-cleanup", "killed", "trailing-output", "signal-after-final", "unexpected-exit", "python-missing"]) {
      let worker, opens = 0;
      const conn = createObdConnectionManager({ repoRoot: process.cwd(), env: {
        PORSCHE981_CONNECTION_STATE: path.join(scratch, `${kind}-${ending}.json`) },
      listFn: async () => ({ devices: [{ id: "vnci:123", brand: "VNCI", available: true, transport: "d-pdu-usb" }] }),
      monitorSpawnFn: () => {
        opens++;
        const monitor = child();
        monitor.stdin.on("data", () => {
          monitor.stdout.write(JSON.stringify({ type: "stopped", restoration: { errors: [] } }) + "\n");
          monitor.exitCode = 0; monitor.emit("close", 0);
        });
        return monitor;
      } });
      await conn.handle({ action: "list" }); await conn.handle({ action: "select", deviceId: "vnci:123" });
      const spawnFn = () => {
        if (kind === "diagnostic" && ending === "python-missing") throw Object.assign(new Error("missing"), { code: "ENOENT" });
        worker = child();
        if (ending === "python-missing") queueMicrotask(() => worker.emit("error", Object.assign(new Error("missing"), { code: "ENOENT" })));
        return worker;
      };
      const common = { repoRoot: process.cwd(), gate: conn.gate, spawnFn, allowInjectedLive: true,
        env: {}, timeoutMs: 1000, stopTimeoutMs: 10, gracefulMs: 10, killWatchMs: 10 };
      const manager = kind === "manufacturer" ? createManufacturerStream({ ...common, connection: conn })
        : attachSessionHandoff(conn, createReadOnlySessionManager({ ...common,
          getLiveDeviceId: () => conn.selectedId(), onSessionEnded: job => conn.resumeAfterSession({ nativeReleaseVerified: job.nativeReleaseVerified }) }),
        { spawnFn, allowInjectedLive: true, env: {} });
      try {
        const started = await manager.handle(kind === "manufacturer" ? selection
          : { action: "start", profileId: "porsche-981-2014-dme", mode: "live", confirmedReadOnly: true, x431Inactive: true }, kind === "manufacturer" ? 1 : { ownerId: 1 });
        assert.equal(started.ok, true, `${kind}/${ending}: ${JSON.stringify(started)}`);
        const safe = ["clean", "read-failed-clean", "python-missing"].includes(ending);
        if (ending !== "python-missing") {
          if (ending !== "killed") {
            const errors = ending === "malformed-cleanup" ? "" : ending === "cleanup-error" ? ["d-pdu:module-disconnect-failed"] : [];
            const ok = ending !== "read-failed-clean";
            const doc = kind === "manufacturer" ? { event: "finished", result: { ok, error: ok ? null : "identity-mismatch",
              sourceKind: "independent-manufacturer-read", executionEnabled: true, simulation: false,
              vehicleDataCollected: false, liveVerified: false, writePayload: null, completedCycles: 0, requestCount: 0,
              ...(ending === "missing-cleanup" ? {} : { transport: { closed: errors.length === 0, closeErrors: errors } }) } }
              : { type: "result", ok, error: ok ? null : "identity-mismatch", profileId: "porsche-981-2014-dme", mode: "live",
                status: ok ? "completed" : "failed", runId: "nativecheckxxxxxxxx", results: [], simulation: false, liveVerified: false, writePayload: null,
                ...(ending === "missing-cleanup" ? {} : { restoration: { errors, adapterClose: "D-PDU disconnect/destruct" } }) };
            worker.stdout.write(JSON.stringify(doc) + "\n");
            if (ending === "trailing-output") worker.stdout.write("invalid\n");
          }
          assert.equal(conn.gate.owner(), "session", `${kind}/${ending}: final output cannot release the lease`);
          worker.exitCode = ending === "signal-after-final" || ending === "killed" ? null : ending === "unexpected-exit" ? 2 : ending === "read-failed-clean" ? 1 : 0;
          worker.emit("close", worker.exitCode);
        }
        await new Promise(resolve => setTimeout(resolve, 25));
        assert.equal(conn.gate.owner(), null);
        const result = await conn.handle({ action: "connect" });
        assert.equal(result.error, safe ? undefined : "adapter_release_not_verified", `${kind}/${ending}: only proven native cleanup permits reconnect`);
        assert.equal(opens, safe ? 1 : 0, `${kind}/${ending}: unknown cleanup must not open another native worker`);
        if (!safe) {
          const status = await manager.handle(kind === "manufacturer" ? { action: "status", sessionId: started.sessionId }
            : { action: "status", jobId: started.jobId }, kind === "manufacturer" ? 1 : { ownerId: 1 });
          assert.equal(status.error, "adapter_release_not_verified");
          assert.equal(conn.reserveDispatch(), false, `${kind}/${ending}: every session route stays blocked`);
          assert.equal((await conn.handle({ action: "list" })).error, "adapter_release_not_verified");
          assert.equal((await conn.resumeAfterSession({ nativeReleaseVerified: true })).error, "adapter_release_not_verified", "later cleanup reports cannot clear the latch");
        }
        checks++;
      } finally { await manager.shutdown(); await conn.shutdown(); }
    }
  }
  // Exercise the elapsed limit through both production managers while a head
  // monitor is already connected. Injected frames are never vehicle evidence.
  for (const scenario of ["serial-clean", "native-clean", "native-cleanup-error", "native-user-disconnect"]) {
    const native = scenario !== "serial-clean";
    const deviceId = native ? "vnci:123" : "bt:AABBCCDDEEFF";
    let elapsed = 100, saved = null;
    const monitors = [], workers = [];
    const emit = (worker, doc) => worker.stdout.write(JSON.stringify(doc) + "\n");
    const close = worker => { worker.exitCode = 0; worker.emit("close", 0); };
    const conn = createObdConnectionManager({ repoRoot: process.cwd(), now: () => 1000,
      env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `${scenario}.json`) },
      handshakeMs: 60_000, livenessMs: 60_000,
      listFn: async () => ({ devices: [{ id: deviceId, available: true, brand: native ? "VNCI" : "vLinker" }] }),
      monitorSpawnFn: () => {
        const monitor = child(); monitors.push(monitor);
        monitor.stdin.on("data", bytes => {
          if (!String(bytes).includes('"stop"')) return;
          emit(monitor, { type: "stopped", ok: true, restoration: { errors: [] } }); close(monitor);
        });
        return monitor;
      } });
    const manager = createManufacturerStream({ repoRoot: process.cwd(), env: {}, connection: conn, gate: conn.gate,
      allowInjectedLive: true, nowFn: () => elapsed, timeoutMs: 60_000,
      spawnFn: () => {
        const worker = child(); worker.input = "";
        worker.stdin.on("data", bytes => { worker.input += bytes; }); workers.push(worker); return worker;
      },
      saveRecording: async recording => {
        saved = { synthetic: true, vehicleIO: false, sampleCount: recording.sampleCount,
          samples: [...recording.samples], termination: recording.termination };
        return { saved: true };
      } });
    conn.setSessionStop(() => manager.cancelActiveAndWait());
    const status = sessionId => manager.handle({ action: "status", sessionId }, 2);
    const handshake = () => emit(monitors.at(-1), { ok: true, type: "handshake", simulation: false,
      deviceId, volts: 12.6, voltageSource: native ? "d-pdu-vbatt" : "atrv", at: 1000 });
    const finish = (worker, cleanupError = false, count = 1) => {
      emit(worker, { event: "finished", result: { ok: false, error: "cancelled",
        sourceKind: "independent-manufacturer-read", executionEnabled: true, simulation: false,
        vehicleDataCollected: count > 0, liveVerified: false, writePayload: null,
        completedCycles: count, requestCount: count,
        transport: { closed: !cleanupError, closeErrors: cleanupError ? ["d-pdu:disconnect-failed"] : [] } } });
    };
    try {
      await conn.handle({ action: "list" }); await conn.handle({ action: "select", deviceId });
      assert.equal((await conn.handle({ action: "connect" })).ok, true); handshake();
      assert.equal(conn.snapshot().connected, true);
      const started = await manager.handle(selection, 2), worker = workers[0];
      assert.equal(started.ok, true); assert.equal(monitors[0].exitCode, 0);
      assert.equal(conn.gate.owner(), "session");
      const transaction = { requestHex: "21A4", pduHex: "61A42A", cycle: 1, elapsedMs: 1, connectionEpoch: 1,
        pduSha256: createHash("sha256").update(Buffer.from("61A42A", "hex")).digest("hex"),
        sourceKind: "independent-manufacturer-read", timestampSource: "host-response-observed",
        processedUtc: "2026-10-11T00:00:00+00:00", ok: true };
      const { pduHex: _rawPdu, ...valueEvidence } = transaction;
      emit(worker, { event: "cycle", result: { ok: true, completedCycles: 1, cycle: 1, transactions: [transaction],
        sourceKind: "independent-manufacturer-read", executionEnabled: true, simulation: false,
        vehicleDataCollected: true, liveVerified: false, writePayload: null,
        samples: [{ ...valueEvidence, parameterId: selection.parameterIds[0], cycle: 1, synthetic: false, elapsedMs: 1, value: 42 }] } });
      elapsed += 599999;
      assert.equal(status(started.sessionId).state, "running", JSON.stringify({ scenario, status: status(started.sessionId) }));
      elapsed++;
      assert.equal(status(started.sessionId).state, "stopping");
      assert.equal(status(started.sessionId).stopReason, "time-limit");
      assert.equal(worker.input.split('\n').filter(line => line === '{"action":"stop"}').length, 1);
      assert.equal(monitors.length, 1, "deadline cannot resume before cleanup and actual pipe close");
      assert.equal((await manager.handle({ action: "save", sessionId: started.sessionId }, 2)).error, "stream_busy");
      let disconnectBlocked = false;
      if (scenario === "native-user-disconnect") {
        assert.equal((await conn.handle({ action: "disconnect" })).error, "busy");
        disconnectBlocked = true;
        assert.equal(conn._state().wantConnected, true, "connection settings cannot interrupt an active vehicle operation");
      }
      finish(worker, scenario === "native-cleanup-error");
      assert.equal(conn.gate.owner(), "session", "final output still holds the port until actual close");
      assert.equal(monitors.length, 1);
      close(worker); await new Promise(resolve => setImmediate(resolve));
      const failed = scenario === "native-cleanup-error", resumed = !failed;
      assert.equal(monitors.length, resumed ? 2 : 1);
      assert.equal(status(started.sessionId).released, true);
      assert.equal(status(started.sessionId).error, failed ? "adapter_release_not_verified" : null);
      assert.equal(status(started.sessionId).events[0].samples[0].value, 42);
      if (resumed) {
        assert.equal(conn.snapshot().connected, false, "new monitor must provide its own handshake");
        handshake(); assert.equal(conn.snapshot().connected, true);
        assert.match(String(conn.gate.owner()), /^monitor:/);
      } else assert.equal(conn.snapshot().linkState, failed ? "close_failed" : "idle");
      assert.equal(saved, null, "ordinary results remain volatile until explicit save");
      assert.equal((await manager.handle({ action: "save", sessionId: started.sessionId }, 2)).saved, true);
      assert.equal(saved.termination.stopReason, "time-limit");
      assert.equal(saved.sampleCount, 1); assert.equal(saved.samples[0].value, 42);
      if (disconnectBlocked) assert.equal((await conn.handle({ action: "disconnect" })).ok, true, "disconnect is available after task release");
      elapsed += 600001; status(started.sessionId);
      assert.equal(workers.length, 1, "retained result and monitor never start another acquisition automatically");
      if (resumed) {
        const next = await manager.handle(selection, 2);
        assert.equal(next.ok, true); assert.equal(workers.length, 2);
        assert.equal(status(next.sessionId).state, "running", "manual next batch gets its own elapsed deadline");
        manager.handle({ action: "stop", sessionId: next.sessionId }, 2);
        finish(workers[1], false, 0); close(workers[1]);
      } else if (failed) assert.equal((await conn.handle({ action: "connect" })).error, "adapter_release_not_verified");
      checks++;
    } finally { await manager.shutdown(); await conn.shutdown(); }
  }
} finally { fs.rmSync(scratch, { recursive: true, force: true }); }
console.log(`native session release selfcheck passed: ${checks} injected diagnostic/manufacturer endings, no hardware IO`);
console.log("manufacturer deadline handoff passed: connected serial/native monitor, cleanup failure, user disconnect, retained explicit save, manual next batch; injected clock/workers only");
