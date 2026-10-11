import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import { validateManufacturerEvidence as validate } from "./manufacturer-evidence.mjs";
const hash = pdu => createHash("sha256").update(Buffer.from(pdu, "hex")).digest("hex");
const sourceKind = "historical-fixture-rehearsal";
const response = (requestHex, pduHex, elapsedMs, connectionEpoch = 1) => ({ requestHex, pduHex,
  pduSha256: hash(pduHex), ok: true, cycle: 1, elapsedMs, connectionEpoch, sourceKind,
  timestampSource: "host-response-observed", processedUtc: "2026-10-11T00:00:00+00:00" });
const transactions = [response("21A4", "61A42A", 10), response("22F100", "62F10007", 20)];
const cycle = { ok: true, cycle: 1, sourceKind, transactions, samples: transactions.map((t, i) => ({
  ...t, parameterId: String(i).repeat(64), value: i ? 7 : 42, pduHex: undefined })) };
const initial = validate(cycle); assert.ok(initial); let cases = 1;
for (const change of [
  c => { c.samples[0].pduHex = "61A42A"; },
  c => { c.transactions[0].pduHex = "61A42B"; },
  c => { c.samples[0].pduSha256 = "f".repeat(64); },
  c => { c.samples[0].requestHex = "21A5"; },
  c => { c.samples[0].elapsedMs++; },
  c => { c.samples[0].processedUtc = "2026-10-11T00:00:01Z"; },
  c => { c.samples[0].connectionEpoch = 2; },
  c => { c.transactions[1].elapsedMs = 9; },
  c => { c.transactions.push(c.transactions[0]); },
  c => { c.transactions[0].requestHex = "2EA4"; },
  c => { c.transactions[0].pduHex = "61A52A"; c.transactions[0].pduSha256 = hash("61A52A"); },
  c => { c.transactions[0].connectionEpoch = 2; c.samples[0].connectionEpoch = 2; },
  c => { c.samples.pop(); },
  c => { c.transactions[0].ok = false; },
  c => { c.samples[0].timestampSource = "ecu"; },
  c => { c.samples[0].sourceKind = "independent-manufacturer-read"; },
]) { const changed = structuredClone(cycle); change(changed); assert.equal(validate(changed), null); cases++; }
const bound = structuredClone(cycle);
bound.transactions.reverse(); bound.samples.reverse();
bound.transactions[0].elapsedMs = bound.samples[0].elapsedMs = 30;
bound.transactions[1].elapsedMs = bound.samples[1].elapsedMs = 40;
[bound.samples[0].parameterId, bound.samples[1].parameterId] = [bound.samples[1].parameterId, bound.samples[0].parameterId];
assert.equal(validate(bound, initial), null); assert.equal(initial.parameterRequests.get("0".repeat(64)), "21A4"); cases++;
const partial = structuredClone(cycle); partial.ok = false; partial.samples.pop(); partial.transactions[1].ok = false;
partial.transactions[1].pduHex = "7F2231"; partial.transactions[1].pduSha256 = hash("7F2231");
assert.ok(validate(partial)); cases++;
const reconnected = structuredClone(cycle);
for (const r of [...reconnected.transactions, ...reconnected.samples]) r.connectionEpoch = 2;
assert.ok(validate(reconnected, { qualifiedEpochs: new Set([1, 2]) })); cases++;
assert.equal(validate(cycle, { ...initial, elapsedMs: 21 }), null); cases++;
let producerCycles = 0;
if (process.env.PORSCHE981_EVIDENCE_FIXTURE) {
 const events = JSON.parse(await fs.readFile(process.env.PORSCHE981_EVIDENCE_FIXTURE, "utf8"));
 let prior;
 for (const event of events) { prior = validate(event, prior); assert.ok(prior, "production Python cycle evidence rejected"); producerCycles++; }
 assert.ok(producerCycles > 1);
}
console.log(JSON.stringify({ ok: true, cases, producerCycles, deviceIO: false, independentDecoderQualification: false }));
