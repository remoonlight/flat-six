/**
 * R1 mileage acceptance: increase-only setMileage; decreases rejected.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GarageDb } from "../packages/db/dist/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");

const dbPath = path.join(root, ".local", "mileage-accept.db");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });
try {
  fs.unlinkSync(dbPath);
} catch {
  /* */
}

const db = new GarageDb(dbPath);

const up = db.setMileage(45_000);
if (up.current_km !== 45_000) throw new Error(`expected 45000, got ${up.current_km}`);
console.log("setMileage increase →", up.current_km);

const same = db.setMileage(45_000);
if (same.current_km !== 45_000) throw new Error("same km should be allowed");
console.log("setMileage same km OK");

let threwDecrease = false;
try {
  db.setMileage(40_000);
} catch (e) {
  threwDecrease = String(e.message || e).includes("mileage_decrease_not_allowed");
  console.log("setMileage decrease rejected:", e.message || e);
}
if (!threwDecrease) throw new Error("setMileage must reject decrease");
if (db.getVehicle().current_km !== 45_000) {
  throw new Error("km mutated after rejected decrease");
}

if (typeof db.correctMileage === "function") {
  throw new Error("correctMileage must be removed");
}

const again = db.setMileage(46_000);
if (again.current_km !== 46_000) throw new Error("post-reject increase failed");
console.log("setMileage after reject →", again.current_km);

console.log("\nMILEAGE ACCEPT PASS");
db.close();
