/** Local diagnostic records only; no adapter or vehicle transport. */
import fs from "node:fs";
import path from "node:path";
import { GarageDb } from "../packages/db/dist/index.js";

const operations = new Set([
  "snapshot:capture", "snapshot:list", "snapshot:get", "snapshot:assign",
  "guide:create", "guide:list", "guide:get", "guide:setStep", "guide:linkFaultLog",
  "compare:preview", "compare:save", "compare:list", "compare:get",
]);
const help = `Usage: node scripts/diagnostic-records.mjs --db <SQLite file> <operation> [input.json]
Local records only. Uses the project database API; never opens an adapter.
Operations: ${[...operations].join(", ")}
Write operations require an input JSON object. Run npm run build first.
Input fields (values are validated by the database API):
  snapshot:get, guide:get, compare:get: {id}
  snapshot:assign: {id, note, bindingKey?} (local declared identity, never observed VIN)
  snapshot:capture: {source, at, identityKind, vehicleKey, completeness, captureEventId, modules}
  guide:create: {code?, symptom?, moduleKey?, ecuContext?, source?, identityKind?, snapshotId?}
  guide:setStep: {caseId, stepId, result, note?, updatedAt} (use guide:get for step IDs/version)
  guide:linkFaultLog: {caseId, faultLogId}
  compare:preview: {beforeId, afterId}
  compare:save: {beforeId, afterId, note?, faultLogId?}
Lists need no input file. Exact schemas: packages/db/src/obd-store.ts.
Example: node scripts/diagnostic-records.mjs --db .local/garage.db snapshot:get input.json`;

let db;
try {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === "--help") {
    console.log(help);
  } else {
    const [flag, dbPath, op, input, ...extra] = args;
    if (flag !== "--db" || !dbPath || !operations.has(op) || extra.length) throw new Error(help);
    const writes = /:(capture|assign|create|setStep|linkFaultLog|save)$/.test(op);
    if (writes && !input) throw new Error("write operation requires an input JSON file");
    const value = input ? JSON.parse(fs.readFileSync(input, "utf8").replace(/^\uFEFF/, "")) : {};
    if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("input must be a JSON object");
    // Reading a missing database must not silently create a fresh garage.
    if (!fs.existsSync(dbPath)) throw new Error("database does not exist; provide an existing project database");
    db = new GarageDb(path.resolve(dbPath));
    console.log(JSON.stringify(db.obd.diagOp(op, value), null, 2));
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  db?.close();
}
