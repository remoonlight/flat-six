/**
 * Mileage acceptance: setMileage may increase or decrease; still audits.
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

const down = db.setMileage(40_000);
if (down.current_km !== 40_000) throw new Error(`decrease failed: ${down.current_km}`);
console.log("setMileage decrease →", down.current_km);

const again = db.setMileage(46_000);
if (again.current_km !== 46_000) throw new Error("post-decrease increase failed");
console.log("setMileage after decrease →", again.current_km);

console.log("\nMILEAGE ACCEPT PASS");
db.close();
