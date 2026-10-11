/** Synthetic worker lifecycle only: no native DLL, device or vehicle access. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createObdConnectionManager } from "./obd-connection.mjs";

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-monitor-release-"));
const cases = ["normal", "failed-operation", "exit-before-close", "missing-release", "cleanup-error",
  "unexplained-exit", "signal-after-release", "forced-after-release", "late-release",
  "synthetic-error", "synthetic-release", "invalid-stop-status", "wrong-release-device", "conflicting-release"];
let checked = 0;
try {
  for (const brand of ["vnci", "pt3g", "vlinker", "mx"]) for (const ending of cases) {
    if (process.env.PORSCHE981_RELEASE_CASE && ending !== process.env.PORSCHE981_RELEASE_CASE) continue;
    const native = ["vnci", "pt3g"].includes(brand);
    const device = { id: native ? `${brand}:12345` : `bt:${brand === "mx" ? "AABBCCDDEEFF" : "0425E85BD4CB"}`,
      brand: native ? brand.toUpperCase() : brand === "mx" ? "OBDLink MX+" : "vLinker", available: true,
      transport: native ? "d-pdu-usb" : "bluetooth-spp", serial: native ? "12345" : undefined };
    let child, opens = 0;
    const events = [];
    const conn = createObdConnectionManager({ repoRoot: process.cwd(), now: () => 1000,
      env: { PORSCHE981_CONNECTION_STATE: path.join(scratch, `${brand}-${ending}.json`) },
      gracefulMs: 20, killWaitMs: 20, handshakeMs: 10000, livenessMs: 10000,
      listFn: async () => ({ devices: [device] }), onMonitorEvent: event => events.push(event),
      monitorSpawnFn: () => {
        opens++; child = new EventEmitter();
        Object.assign(child, { stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(), exitCode: null });
        const emit = doc => child.stdout.write(JSON.stringify(doc) + "\n");
        const close = (code, signal) => { child.exitCode = code; child.emit("close", code, signal); };
        child.kill = () => { child.killed = true; close(0); return true; };
        child.stdin.on("data", bytes => {
          if (!String(bytes).includes('"stop"')) return;
          if (ending === "exit-before-close") return;
          if (ending === "failed-operation") emit({ type: "error", ok: false, error: "adapter-identity-mismatch" });
          if (ending === "synthetic-error") emit({ type: "error", simulation: true, ok: false, error: "synthetic" });
          if (ending === "conflicting-release") emit({ type: "stopped", ok: true, restoration: { errors: ["disconnect failed"] } });
          if (!["missing-release", "late-release"].includes(ending)) emit({ type: "stopped", ok: ending !== "invalid-stop-status",
            ...(ending === "synthetic-release" ? { simulation: true } : {}),
            ...(ending === "wrong-release-device" ? { deviceId: `${brand}:99999` } : {}),
            restoration: { errors: ending === "cleanup-error" ? ["disconnect failed"] : [] } });
          if (ending === "forced-after-release") return;
          close(["failed-operation", "unexplained-exit", "synthetic-error"].includes(ending) ? 1 : ending === "signal-after-release" ? null : 0,
            ending === "signal-after-release" ? "SIGTERM" : null);
          if (ending === "late-release") emit({ type: "stopped", ok: true, restoration: { errors: [] } });
        });
        return child;
      } });
    try {
      await conn.handle({ action: "list" }); await conn.handle({ action: "select", deviceId: device.id });
      await conn.handle({ action: "connect" });
      const accepted = ["normal", "failed-operation", "exit-before-close"].includes(ending);
      if (ending === "exit-before-close") {
        // Node exposes exitCode at 'exit'; only 'close' proves stdout has drained.
        child.exitCode = 0;
        let finished = false;
        const stopping = conn.handle({ action: "disconnect" }).then(result => { finished = true; return result; });
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(finished, false, `${brand}: exit code is not pipe closure`);
        assert.match(String(conn.gate.owner()), /^monitor:/);
        child.stdout.write(JSON.stringify({ type: "stopped", ok: true, restoration: { errors: [] } }) + "\n");
        child.emit("close", 0, null);
        assert.equal((await stopping).ok, true);
      } else {
        const result = await conn.handle({ action: "disconnect" });
        assert.equal(result.ok, accepted, `${brand}: ${ending}`);
        if (!accepted) assert.equal(result.error, "adapter_release_not_verified");
      }
      assert.equal(conn.gate.owner(), null);
      if (accepted) {
        assert.equal(conn.snapshot().linkState, "idle");
        assert.equal((await conn.handle({ action: "list" })).ok, true, "clean release permits a new explicit action");
      } else {
        assert.equal(conn.snapshot().linkState, "close_failed");
        for (const action of ["connect", "list"]) assert.equal((await conn.handle({ action })).error, "adapter_release_not_verified");
        assert.equal((await conn.suspendForSession()).error, "adapter_release_not_verified");
        assert.equal(conn.snapshot().linkState, "close_failed", "rejected handoff retains the release failure state");
        assert.equal(conn._state().pausedForSession, false, "failed handoff does not enter a diagnostic session");
        assert.ok(events.some(event => event.type === "native-release-unverified"));
      }
      assert.equal(opens, 1, "no automatic replay/reopen after an explicit stop");
      checked++;
    } finally { await conn.shutdown(); }
  }
  console.log(JSON.stringify({ ok: true, cases: checked, synthetic: true, deviceIO: false,
    checks: "four heads: operation failure vs release, actual close, unknown/signal/forced/missing/bad/late release" }));
} finally { fs.rmSync(scratch, { recursive: true, force: true }); }
