import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { readWorkshopFlashIndex, prepareWorkshopExport } from "../apps/desktop/electron/piwis-workshop.mjs";

const root = process.env.FLATSIX_INSTALLED_APP || path.resolve(import.meta.dirname, "..");
const scratch = process.env.PORSCHE981_SELFCHECK_OUTPUT || path.join(root, ".local", "piwis-workshop-accept");
await fs.mkdir(scratch, { recursive: true });
const file = path.join(scratch, "fixture-index.json");
const fixture = { schemaVersion: 1, sources: [{ file: "fixture.xml", sha256: "a".repeat(64) }], rules: [{
  id: "rule", generation: "982", ecu: "DME", description: "TEST ONLY", source: "fixture.xml",
  conditions: [{ field: "PRODUKTSCHLUESSEL", values: ["TEST"] }],
  targets: [{ logicalLink: "DME_UDS", softwarePartNumber: "TEST-SW", session: null }],
}] };
assert.equal((await readWorkshopFlashIndex(path.join(scratch, "missing.json"))).status, "missing");
await fs.writeFile(file, "{broken");
assert.equal((await readWorkshopFlashIndex(file)).status, "invalid");
for (const mutate of [
  (f) => { f.rules[0].generation = "991.2"; },
  (f) => { f.rules[0].targets = []; },
  (f) => { f.rules.push(structuredClone(f.rules[0])); },
  (f) => { f.sources[0].sha256 = "bad"; },
  (f) => { f.rules[0].conditions = []; },
  (f) => { f.conditions = [{ field: "MODELLJAHR", values: ["TEST"] }]; },
  (f) => { f.sources[0].scope = "other-generation"; },
  (f) => { f.sources.push(structuredClone(f.sources[0])); },
  (f) => { f.sources.push({ ...f.sources[0], sha256: "b".repeat(64) }); },
  (f) => { f.rules[0].operator = "NOT"; },
  (f) => { f.rules[0].conditions[0].operator = "NOT"; },
  (f) => { f.rules[0].targets[0].excludeHardware = "TEST-HW"; },
  (f) => { f.rules[0].currentEcus = [{ logicalLink: "DME", conditions: [], operator: "NOT" }]; },
  (f) => { f.rules[0].currentEcus = [{ logicalLink: "DME", conditions: [{ field: "HWTNR", values: ["TEST-HW"], negate: true }] }]; },
]) {
  const bad = structuredClone(fixture); mutate(bad);
  await fs.writeFile(file, JSON.stringify(bad));
  assert.equal((await readWorkshopFlashIndex(file)).status, "invalid");
}
const decorated = structuredClone(fixture);
decorated.execute = true; decorated.rules[0].writePayload = "UNKNOWN";
await fs.writeFile(file, JSON.stringify(decorated));
const loaded = await readWorkshopFlashIndex(file);
assert.equal(loaded.status, "invalid", "unknown imported fields must not be projected away");
await fs.writeFile(file, JSON.stringify(fixture));
const draft = await prepareWorkshopExport({ generation: "982", functionId: "program-dme", flashRuleId: "rule", writePayload: "UNKNOWN" }, file);
assert.equal(draft.flashRule.targets[0].softwarePartNumber, "TEST-SW");
assert.equal(draft.executionEnabled, false);
assert.equal(draft.applicabilityStatus, "unverified");
assert.equal("writePayload" in draft, false);
await assert.rejects(prepareWorkshopExport({ generation: "981", functionId: "program-dme", flashRuleId: "rule" }, file), /rule-mismatch/);
await assert.rejects(prepareWorkshopExport({ generation: "982", functionId: "program-pdk", flashRuleId: "rule" }, file), /rule-mismatch/);
await assert.rejects(prepareWorkshopExport({ generation: "982", functionId: "program-dme", flashRuleId: "unknown" }, file), /rule-mismatch/);
const expanded = structuredClone(fixture);
expanded.rules[0].ecu = "Gateway";
expanded.rules[0].kind = "blocked";
expanded.rules[0].familyEvidence = "shared-platform";
expanded.rules[0].targets = [{ logicalLink: null, softwarePartNumber: null, session: null }];
expanded.rules[0].currentEcus = [{ logicalLink: "GATEWAY", conditions: [{ field: "HWTNR", values: ["OLD-HW"] }] }];
await fs.writeFile(file, JSON.stringify(expanded));
const restriction = await prepareWorkshopExport({ generation: "982", functionId: "program-gateway", flashRuleId: "rule" }, file);
assert.equal(restriction.programmingDisposition, "blocked");
assert.deepEqual(restriction.flashRule.currentEcus[0].conditions[0].values, ["OLD-HW"]);
expanded.rules[0].kind = "firmware";
expanded.rules[0].targets = [{ logicalLink: "GATEWAY", softwarePartNumber: "noflash", session: null }];
await fs.writeFile(file, JSON.stringify(expanded));
assert.equal((await readWorkshopFlashIndex(file)).status, "invalid", "noflash is never a software target");
const local = await readWorkshopFlashIndex(path.join(root, ".local/diagnostics/piwis-workshop/flash-index.json"));
if (local.status === "loaded") {
  console.log(`Local research index validated: ${local.index.rules.length} rows; no vehicle applicability inferred.`);
}
if (process.env.PORSCHE981_PIWIS_REFERENCE_INDEX) {
  const reference = await readWorkshopFlashIndex(process.env.PORSCHE981_PIWIS_REFERENCE_INDEX);
  assert.equal(reference.status, "loaded");
  assert.equal(reference.index.rules.length, 293);
  const original = JSON.parse(await fs.readFile(process.env.PORSCHE981_PIWIS_REFERENCE_INDEX, "utf8"));
  assert.deepEqual(reference.index.rules.map(r => r.conditions), original.rules.map(r => r.conditions));
  assert.deepEqual(reference.index.rules.map(r => r.currentEcus), original.rules.map(r => r.currentEcus));
  assert.deepEqual(reference.index.rules.map(r => r.targets), original.rules.map(r => r.targets));
  console.log("PASS original PIWIS index: 293 rules retain exact conditions, current ECU constraints and target order.");
}
console.log("PASS PIWIS IPC helpers: closed schema, source uniqueness, rule identity and offline export; no vehicle qualification.");
