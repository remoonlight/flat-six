/**
 * P0 acceptance: mileage + oil service → interval remaining
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const dbPath = path.join(root, ".local", "p0-accept.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
try {
  fs.unlinkSync(dbPath);
} catch {
  /* */
}

const db = new GarageDb(dbPath);
const parts = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/parts/bootstrap.json"), "utf8"),
);
const faults = JSON.parse(
  fs.readFileSync(path.join(root, "data/seed/faults/bootstrap.json"), "utf8"),
);
db.seedIfEmpty({ parts: parts.parts, faults: faults.faults });

db.setMileage(45_000);
db.setAvgKmPerDay(40);
const oil = db.listParts().find((p) => p.sku === "engine-oil");
if (!oil) throw new Error("missing engine-oil");
db.addServiceRecord({
  part_id: oil.id,
  title: "机油更换",
  replaced_at: "2026-01-01",
  odometer_km: 40_000,
  brand: "Castrol",
  cost: 800,
});
const iv = db.partIntervalStatus(oil.id);
console.log(JSON.stringify(iv, null, 2));
if (!iv) throw new Error("no interval");
if (iv.nextDueKm !== 55_000) throw new Error(`nextDueKm ${iv.nextDueKm}`);
if (iv.remainingKm !== 10_000) throw new Error(`remainingKm ${iv.remainingKm}`);
if (!iv.nextDueDate) throw new Error("missing nextDueDate");
console.log("P0 ACCEPT PASS");
db.close();
