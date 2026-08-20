/**
 * OBD Phase 1 acceptance — dtc_kb seed, search, session + manual DTC.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const dbPath = path.join(root, ".local", "obd-phase1-accept.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
try {
  fs.unlinkSync(dbPath);
} catch {
  /* */
}

const partsSeed = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/parts/bootstrap.json"), "utf8"),
);
const faultsSeed = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/faults/bootstrap.json"), "utf8"),
);
const dtcSeed = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/dtc/bootstrap.json"), "utf8"),
);

const db = new GarageDb(dbPath);
db.seedIfEmpty({
  parts: partsSeed.parts,
  faults: faultsSeed.faults,
  dtcs: dtcSeed.dtcs,
});
db.seedDtcIfEmpty(dtcSeed.dtcs);
db.seedDtcMissing(dtcSeed.dtcs);
db.setMileage(52_000);

const all = db.listDtcs();
if (all.length < 30) {
  throw new Error(`dtc_kb expected >=30 rows, got ${all.length}`);
}
console.log(`dtc_kb rows: ${all.length}`);

const p0300 = db.getDtcByCode("P0300");
if (!p0300) throw new Error("P0300 not found");
if (!p0300.title_zh.includes("失火")) {
  throw new Error(`P0300 title unexpected: ${p0300.title_zh}`);
}
console.log("P0300:", p0300.code, p0300.title_zh);

const prefix = db.searchDtc("P030");
if (prefix.length < 7) {
  throw new Error(`searchDtc P030 expected >=7, got ${prefix.length}`);
}
console.log(`searchDtc P030: ${prefix.length} hits`);

const before = db.listObdSessions();
if (before.length !== 0) throw new Error("obd_sessions should start empty");

const session = db.createObdSession({
  odometer_km: 52_000,
  note: "obd-phase1-accept",
});
console.log("session:", JSON.stringify(session, null, 2));
if (!session.id) throw new Error("missing session id");

const dtc = db.addObdDtc({
  session_id: session.id,
  code: "P0300",
  status: "manual_kb",
});
console.log("dtc:", JSON.stringify(dtc, null, 2));
if (dtc.code !== "P0300") throw new Error("dtc code mismatch");

const unknown = db.addObdDtc({
  session_id: session.id,
  code: "P9999",
  status: "manual_unknown",
});
if (unknown.code !== "P9999") throw new Error("unknown dtc code mismatch");
console.log("unknown dtc OK");

const sessionDtcs = db.listObdDtcs(session.id);
if (sessionDtcs.length !== 2) {
  throw new Error(`listObdDtcs expected 2, got ${sessionDtcs.length}`);
}
console.log(`listObdDtcs: ${sessionDtcs.length} codes`);

const kb = db.getDtcByCode("P0300");
const fault = db.addFaultLog({
  logged_at: session.started_at.slice(0, 10),
  odometer_km: session.odometer_km ?? 52_000,
  symptom: kb ? `${kb.code} — ${kb.title_zh}` : dtc.code,
  area_hypothesis: kb?.likely_causes ?? null,
  action: kb?.checks ?? "OBD 手工码",
  result: `来自 OBD 会话 #${session.id}（${dtc.status}）`,
  related_part_sku: kb?.related_part_sku ?? null,
});
if (!fault.id) throw new Error("fault_log from session missing id");
if (!fault.symptom.includes("P0300")) {
  throw new Error(`fault symptom unexpected: ${fault.symptom}`);
}
console.log("fault_log from OBD session OK");

const listed = db.listObdSessions();
if (listed.length !== 1) throw new Error(`expected 1 session, got ${listed.length}`);

const kbAfter = db.listDtcs();
if (kbAfter.length !== all.length) {
  throw new Error("dtc_kb mutated by session ops");
}

// seedDtcMissing: old DB with 19 codes gets new bootstrap rows
const partialPath = path.join(root, ".local", "obd-phase1-partial.db");
try {
  fs.unlinkSync(partialPath);
} catch {
  /* */
}
const partial = new GarageDb(partialPath);
partial.seedIfEmpty({
  parts: partsSeed.parts,
  faults: faultsSeed.faults,
  dtcs: dtcSeed.dtcs.slice(0, 19),
});
const beforeMissing = partial.listDtcs().length;
const missingRes = partial.seedDtcMissing(dtcSeed.dtcs);
if (missingRes.inserted < 1) {
  throw new Error(`seedDtcMissing expected inserts, got ${missingRes.inserted}`);
}
if (partial.listDtcs().length < 30) {
  throw new Error("seedDtcMissing did not reach >=30 codes");
}
console.log(
  `seedDtcMissing: ${beforeMissing} → ${partial.listDtcs().length} (+${missingRes.inserted})`,
);
partial.close();

console.log("\nOBD PHASE 1 ACCEPT PASS");
db.close();
