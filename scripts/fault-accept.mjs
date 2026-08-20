/**
 * Fault long-term tracking acceptance (R5 §6.4).
 * create → list → close; verifies fault_logs is separate from fault_kb.
 * Also asserts fault_kb related_part_sku → parts.locator_hotspot → zones map.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";
import {
  resolveSkuToLocator,
  skuToHotspot,
} from "../packages/domain/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const dbPath = path.join(root, ".local", "fault-accept.db");
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
const zonesSeed = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/locator/zones.json"), "utf8"),
);

// Temp DB only; real .local/garage.db needs `npm run sync:parts-bootstrap` after bootstrap edits.
const db = new GarageDb(dbPath);
db.seedIfEmpty({ parts: partsSeed.parts, faults: faultsSeed.faults });
db.setMileage(52_000);

const kb = db.listFaults();
if (kb.length < 1) throw new Error("fault_kb empty after seed");
console.log(`fault_kb rows: ${kb.length}`);

const parts = db.listParts();
const zones = zonesSeed.zones;

console.log("\n--- R5 sku → hotspot → zone map ---");
console.log(
  "sku".padEnd(16),
  "hotspot".padEnd(14),
  "zone".padEnd(12),
  "locate?",
);
let withSku = 0;
let locateOk = 0;
for (const f of kb) {
  const sku = f.related_part_sku;
  if (!sku) continue;
  withSku += 1;
  const hotspot = skuToHotspot(sku, parts);
  if (!hotspot) {
    throw new Error(
      `fault "${f.symptom}" sku=${sku} has no parts.locator_hotspot`,
    );
  }
  const resolved = resolveSkuToLocator(sku, parts, zones);
  const zoneCol = resolved?.zoneId ?? "(off-map)";
  const ok = resolved != null;
  if (ok) locateOk += 1;
  console.log(
    sku.padEnd(16),
    hotspot.padEnd(14),
    zoneCol.padEnd(12),
    ok ? "YES" : "no",
  );
}
if (withSku < 1) throw new Error("expected fault_kb rows with related_part_sku");
console.log(
  `mapped ${withSku} sku→hotspot; ${locateOk} resolvable onto locator map`,
);

// Manual logic self-check (mirrors DiagnosticsPage enable rule)
const selfChecks = [
  ["coil-pack", true],
  ["brake-fluid", true],
  ["pdk-fluid", true],
  ["cabin-filter", true],
  ["battery", true],
  ["wiper-blades", true],
];
for (const [sku, expectOk] of selfChecks) {
  const got = resolveSkuToLocator(sku, parts, zones) != null;
  if (got !== expectOk) {
    throw new Error(
      `self-check ${sku}: expected locate=${expectOk}, got ${got}`,
    );
  }
}
console.log("self-check locate enable rules: PASS");

const before = db.listFaultLogs();
if (before.length !== 0) throw new Error("fault_logs should start empty");

const created = db.addFaultLog({
  logged_at: "2026-07-31",
  odometer_km: 52_000,
  symptom: "冷车怠速抖动",
  area_hypothesis: "点火/喷油",
  action: "读码 + 检查火花塞间隙",
  result: "待观察",
  related_part_sku: "spark-plugs",
});
console.log("\ncreated:", JSON.stringify(created, null, 2));
if (!created.id) throw new Error("missing id");
if (created.closed !== 0) throw new Error(`expected open, got closed=${created.closed}`);
if (created.symptom !== "冷车怠速抖动") throw new Error("symptom mismatch");
if (created.coding_snapshot_id != null) {
  throw new Error("expected null coding_snapshot_id by default");
}

const listed = db.listFaultLogs();
console.log(`list count: ${listed.length}`);
if (listed.length !== 1) throw new Error(`expected 1 log, got ${listed.length}`);
if (listed[0].id !== created.id) throw new Error("list id mismatch");

const closed = db.closeFaultLog(created.id);
console.log("closed:", JSON.stringify(closed, null, 2));
if (closed.closed !== 1) throw new Error(`expected closed=1, got ${closed.closed}`);

const afterClose = db.listFaultLogs();
if (afterClose.length !== 1) throw new Error("close must not delete row");
if (afterClose[0].closed !== 1) throw new Error("list after close not marked");

const kbAfter = db.listFaults();
if (kbAfter.length !== kb.length) {
  throw new Error("fault_kb mutated by fault_logs ops");
}

let threw = false;
try {
  db.closeFaultLog(99999);
} catch {
  threw = true;
}
if (!threw) throw new Error("close missing id should throw");

// --- coding_snapshot_id association ---
console.log("\n--- coding_snapshot_id ---");
const snap = db.addCodingSnapshot({
  system: "发动机控制单元",
  function_name: "设码",
  sub_function: null,
  before_value: "off",
  after_value: "on",
  note: "fault-accept",
  odometer_km: 52_000,
  recorded_at: "2026-07-31T10:00:00.000Z",
});
console.log("coding snap:", `#${snap.id} ${snap.system} ${snap.function_name}`);

const withCoding = db.addFaultLog({
  logged_at: "2026-07-31",
  odometer_km: 52_100,
  symptom: "设码后怠速变化",
  area_hypothesis: "ECU 设码",
  action: "对照 X431 快照",
  result: "跟踪中",
  coding_snapshot_id: snap.id,
});
console.log("created with coding:", JSON.stringify(withCoding, null, 2));
if (withCoding.coding_snapshot_id !== snap.id) {
  throw new Error(
    `coding_snapshot_id mismatch: got ${withCoding.coding_snapshot_id}`,
  );
}

const listedCoding = db.listFaultLogs().find((r) => r.id === withCoding.id);
if (!listedCoding || listedCoding.coding_snapshot_id !== snap.id) {
  throw new Error("list did not round-trip coding_snapshot_id");
}
console.log(
  `list round-trip coding_snapshot_id=${listedCoding.coding_snapshot_id}: OK`,
);

const closedWithCoding = db.closeFaultLog(withCoding.id);
if (closedWithCoding.coding_snapshot_id !== snap.id) {
  throw new Error("close must preserve coding_snapshot_id");
}
if (closedWithCoding.closed !== 1) {
  throw new Error("close with coding_snapshot_id failed");
}
console.log("close preserves coding_snapshot_id: OK");

let badIdThrew = false;
try {
  db.addFaultLog({
    logged_at: "2026-07-31",
    odometer_km: 52_200,
    symptom: "非法快照",
    coding_snapshot_id: 999_999,
  });
} catch (e) {
  badIdThrew = true;
  const msg = String(e);
  if (!msg.includes("coding_snapshot_not_found")) {
    throw new Error(`expected coding_snapshot_not_found, got: ${msg}`);
  }
  console.log("illegal coding id rejected:", msg);
}
if (!badIdThrew) throw new Error("illegal coding_snapshot_id should throw");

let badNumThrew = false;
try {
  db.addFaultLog({
    logged_at: "2026-07-31",
    odometer_km: 52_200,
    symptom: "非法快照数值",
    coding_snapshot_id: -1,
  });
} catch (e) {
  badNumThrew = true;
  console.log("invalid coding id rejected:", String(e));
}
if (!badNumThrew) throw new Error("invalid coding_snapshot_id should throw");

console.log("\nFAULT ACCEPT PASS");
db.close();
