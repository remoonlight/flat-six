/** Exercise retained records through the CLI, including restart and incomplete comparison. */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { GarageDb } from "../packages/db/dist/index.js";

const root = path.resolve(import.meta.dirname, "..");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "porsche-diag-records-"));
const dbPath = path.join(temp, "test.db");
new GarageDb(dbPath).close();
function cli(op, value) {
  const args = ["scripts/diagnostic-records.mjs", "--db", dbPath, op];
  if (value) {
    const input = path.join(temp, "input.json");
    fs.writeFileSync(input, JSON.stringify(value));
    args.push(input);
  }
  const result = spawnSync(process.execPath, args, { cwd: root, encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}
try {
  const payload = { source: "simulation", at: "2026-10-02T00:00:00Z", identityKind: "unknown", vehicleKey: null,
    identityLabel: "unbound", completeness: "complete", captureEventId: "cli-a", modules: [{
      moduleKey: "porsche-981-2014-dme", name: "DME", ecuVariant: null, coverage: "success-dtc",
      dtcs: [{ code: "C447", dtcHex: "C447", statusHex: "28", display: "U0447" }], rawRef: "synthetic" }] };
  const first = cli("snapshot:capture", payload);
  assert.equal(cli("snapshot:capture", payload).id, first.id);
  const second = cli("snapshot:capture", { ...payload, captureEventId: "cli-b", at: "2026-10-02T00:01:00Z" });
  assert.equal(cli("compare:preview", { beforeId: first.id, afterId: second.id }).reason, "identity-unqualified");
  for (const item of [first, second]) cli("snapshot:assign", { id: item.id, note: "synthetic local declaration" });
  assert.ok(cli("compare:preview", { beforeId: first.id, afterId: second.id }).rows.every((row) => row.kind === "still"));
  const failed = cli("snapshot:capture", { ...payload, captureEventId: "cli-failed", at: "2026-10-02T00:02:00Z", completeness: "failed",
    modules: [{ ...payload.modules[0], coverage: "failed", dtcs: [] }] });
  cli("snapshot:assign", { id: failed.id, note: "synthetic local declaration" });
  assert.ok(cli("compare:preview", { beforeId: first.id, afterId: failed.id }).rows.every((row) => row.kind !== "gone"));
  const report = cli("compare:save", { beforeId: first.id, afterId: second.id, note: "CLI preserved report" });
  assert.equal(cli("compare:get", { id: report.id }).note, "CLI preserved report");
  const guide = cli("guide:create", { code: "P0301", moduleKey: "porsche-981-2014-dme", ecuContext: "dme", source: "manual", identityKind: "unknown" });
  cli("guide:setStep", { caseId: guide.id, stepId: guide.checklist.steps[0].id, result: "normal", note: "CLI preserved step", updatedAt: guide.updatedAt });
  assert.equal(cli("guide:get", { id: guide.id }).stepResults[0].note, "CLI preserved step");
  assert.equal(cli("snapshot:list").length, 3);
  console.log("PASS diagnostic records CLI: capture idempotency, explicit binding, incomplete comparison, guide/report restart persistence; no hardware");
} finally {
  for (const name of ["test.db", "test.db-wal", "test.db-shm", "test.db-journal", "input.json"]) {
    const target = path.join(temp, name);
    if (fs.existsSync(target)) fs.unlinkSync(target);
  }
  fs.rmdirSync(temp);
}
