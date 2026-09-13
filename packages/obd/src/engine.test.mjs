import test from "node:test";
import assert from "node:assert/strict";
import { ObdEngine } from "./engine.mjs";
import { OBD_DECODER_VERSION, validateObdRecording } from "@porsche981/domain";

async function exercise(scenario, options = {}) {
  let time = 0; const records = []; let finished;
  const engine = new ObdEngine({ now: () => time, wait: async (ms, signal) => { if (signal?.aborted) throw new Error("cancelled"); time += ms; },
    persist: async (kind, value) => {
      if (kind === "observation") {
        if (options.failSave && records.length === 2) throw new Error("disk_full");
        records.push(value.observation);
        if (options.cancel && records.length === 3) engine.abort.abort();
      } else finished = value;
    } });
  engine.start({ sessionId: 1, source: "simulation", scenario, budgetMs: 15000, status: "running", startedAt: new Date().toISOString(), endedAt: null, reason: null, decoderVersion: OBD_DECODER_VERSION });
  await engine.active;
  return { engine, records, finished, time };
}
test("offline acquisition produces replayable raw VIN, DTC, freeze and live values within budget", async () => {
  const { engine, records, finished, time } = await exercise("normal");
  assert.equal(finished.status, "completed"); assert.ok(time <= 15000);
  assert.equal(records.find((o) => o.command === "0902").vin[0].value, "WP0ZZZ98ZES000001");
  assert.ok(records.some((o) => o.samples.some((s) => s.context === "freeze")));
  assert.ok(records.some((o) => o.samples.some((s) => s.context === "live")));
  const replay = validateObdRecording({ version: 1, run: engine.state.run, observations: records });
  assert.deepEqual(replay.observations, records);
});
test("no-response stops early; no-codes stays distinct", async () => {
  const missing = await exercise("no-response");
  assert.equal(missing.records.length, 1); assert.equal(missing.finished.status, "partial");
  const clean = await exercise("no-codes");
  assert.equal(clean.finished.status, "completed");
  assert.equal(clean.records.find((r) => r.command === "03").dtcs.length, 0);
});
test("disconnect retains evidence, malformed values are never shown as real data", async () => {
  const dropped = await exercise("disconnect");
  assert.equal(dropped.records.length, 5); assert.equal(dropped.finished.status, "partial");
  const malformed = await exercise("malformed");
  assert.equal(malformed.finished.status, "partial");
  assert.equal(malformed.records.filter((r) => r.command === "010C").flatMap((r) => r.samples).length, 0);
});
test("cancellation and disk failure stop requests, saved boundary remains honest", async () => {
  const cancelled = await exercise("normal", { cancel: true });
  assert.equal(cancelled.finished.status, "cancelled"); assert.equal(cancelled.records.length, 3);
  const broken = await exercise("normal", { failSave: true });
  assert.equal(broken.finished.status, "interrupted"); assert.equal(broken.records.length, 2);
  assert.equal(broken.engine.state.observations.length, 2);
});
