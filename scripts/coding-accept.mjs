/**
 * Coding R6 / §6.5 smoke: X431「外部放大器 → 设码」playbook + GarageDb after-only snapshot.
 * Does not open Electron or write ECU.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const seedPath = path.join(root, "data", "seed", "x431", "981-2014-coding-menu.json");

if (!fs.existsSync(seedPath)) {
  console.error(`FAIL: missing seed ${seedPath}`);
  process.exit(1);
}

const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const systems = seed.systems;
if (!Array.isArray(systems) || systems.length === 0) {
  console.error("FAIL: systems empty or missing in coding menu seed");
  process.exit(1);
}

const AMP_RE = /外部放大器|amplif|\bamp\b/i;

function isAmpSystem(sys) {
  const name = String(sys.system ?? "");
  if (AMP_RE.test(name)) return true;
  for (const item of sys.items ?? []) {
    const blob = [item.function, item.subFunction, item.playbook?.x431Path]
      .filter(Boolean)
      .join(" ");
    if (AMP_RE.test(blob)) return true;
  }
  return false;
}

const ampSystems = systems.filter(isAmpSystem);
if (ampSystems.length < 1) {
  console.error("FAIL: no amp / 外部放大器 system in coding menu");
  process.exit(1);
}

const amp = ampSystems[0];
const codingItem =
  (amp.items ?? []).find((it) => String(it.function ?? "") === "设码") ??
  (amp.items ?? [])[0];

if (!codingItem) {
  console.error(`FAIL: system "${amp.system}" has no items`);
  process.exit(1);
}

const steps = codingItem.playbook?.steps;
if (!Array.isArray(steps) || steps.length === 0) {
  console.error(
    `FAIL: playbook.steps empty for ${amp.system} / ${codingItem.function}`,
  );
  process.exit(1);
}

console.log(`seed: ${seedPath}`);
console.log(`system: ${amp.system}`);
console.log(
  `item: ${codingItem.function}` +
    (codingItem.subFunction ? ` / ${codingItem.subFunction}` : "") +
    ` (rowId=${codingItem.rowId})`,
);
console.log(`playbook.steps: ${steps.length}`);
console.log(`x431Path: ${codingItem.playbook.x431Path ?? "(none)"}`);

const dbPath = path.join(root, ".local", "coding-accept.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
try {
  fs.unlinkSync(dbPath);
} catch {
  /* */
}

const db = new GarageDb(dbPath);
const beforeList = db.listCodingSnapshots();
if (beforeList.length !== 0) {
  throw new Error("coding_snapshots should start empty");
}

const afterPayload = JSON.stringify({
  source: "accept:coding",
  system: amp.system,
  function: codingItem.function,
  note: "after-only smoke; before optional per R6",
});

const created = db.addCodingSnapshot({
  system: amp.system,
  function_name: codingItem.function,
  sub_function: codingItem.subFunction ?? null,
  before_value: "",
  after_value: afterPayload,
  note: "accept:coding after-only",
  odometer_km: 52_000,
  recorded_at: "2026-07-31",
});

console.log("\ncreated:", JSON.stringify(created, null, 2));
if (!created.id) throw new Error("missing id");
if (created.system !== amp.system) throw new Error("system mismatch");
if (created.function_name !== codingItem.function) {
  throw new Error("function_name mismatch");
}
if (created.before_value !== "") {
  throw new Error(`expected empty before, got ${JSON.stringify(created.before_value)}`);
}
if (created.after_value !== afterPayload) {
  throw new Error("after_value mismatch");
}

const listed = db.listCodingSnapshots();
console.log(`list count: ${listed.length}`);
if (listed.length !== 1) {
  throw new Error(`expected 1 snapshot, got ${listed.length}`);
}
if (listed[0].id !== created.id) throw new Error("list id mismatch");
if (listed[0].after_value !== afterPayload) {
  throw new Error("list after_value mismatch");
}

console.log("\nCODING ACCEPT PASS");
db.close();
process.exit(0);