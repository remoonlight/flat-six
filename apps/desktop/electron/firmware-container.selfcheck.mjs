import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { createDiagnosticPreparation } from "./diagnostic-preparation.mjs";
import { runWorkbench, validateRequest } from "./offline-diagnostics.mjs";

const repoRoot = process.env.FLATSIX_INSTALLED_APP || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const directory = await fs.mkdtemp(path.join(os.tmpdir(), "flat-six-firmware-"));
try {
  const identity = { generation: "981", vin: "WP0ZZZ98ZES000000", ecu: "dme", hardware: "TEST-HW", software: "TEST-SW" };
  const file = path.join(directory, "原厂 测试.odx");
  const xml = '<ODX MODEL-VERSION="2.1.0"><FLASH><MEM><SESSION ID="S"><SHORT-NAME>测试会话</SHORT-NAME><DATABLOCK-REF ID-REF="B"/></SESSION><DATABLOCK ID="B"><FLASHDATA-REF ID-REF="F"/></DATABLOCK><FLASHDATA ID="F"><DATAFORMAT SELECTION="BINARY"/><DATA>0102FF</DATA></FLASHDATA></MEM></FLASH></ODX>';
  await fs.writeFile(file, xml);
  const bytes = Buffer.from(xml), sha256 = createHash("sha256").update(bytes).digest("hex");
  const manifest = path.join(directory, "manifest.json");
  await fs.writeFile(manifest, JSON.stringify({ schemaVersion: 1, kind: "oem-ecu-firmware-manifest", origin: "original",
    identity, bytes: bytes.length, sha256, manufacturerSource: "Synthetic offline fixture; not OEM authenticity evidence", targetSoftware: "TEST-TARGET" }));
  assert.equal(validateRequest({ action: "firmware-container", firmwareFile: file }).error, "forbidden_field");
  assert.equal(validateRequest({ action: "firmware-container", path: file }).error, "forbidden_field");
  const inherited = await runWorkbench({ action: "firmware-container" }, { repoRoot, env: { ...process.env, PORSCHE981_FIRMWARE_FILE: file } });
  assert.equal(inherited.error, "firmware-container-selection-missing", "public IPC never inherits a file selection");
  const inspection = await runWorkbench({ action: "firmware-container" }, { repoRoot, firmwareFile: file });
  assert.equal(inspection.ok, true, JSON.stringify(inspection));
  assert.equal(inspection.container.complete, true);
  assert.equal(inspection.container.file.sha256, sha256);
  assert.equal(inspection.container.documents[0].sessions[0].name, "测试会话");
  assert.equal(inspection.container.documents[0].flashdata[0].payload.sha256, createHash("sha256").update(Buffer.from([1,2,255])).digest("hex"));
  let canceled = false, fail = null, exported = null, inspections = 0, saves = 0;
  const preparation = createDiagnosticPreparation({ directory: path.join(directory, "vault"),
    chooseOpenFile: async kind => canceled ? null : kind === "manifest" ? manifest : file,
    inspectFirmware: async selected => { inspections++; assert.equal(selected, file);
      return fail === "hash" ? { ...inspection, container: { ...inspection.container, file: { ...inspection.container.file, sha256: "0".repeat(64) } } }
        : fail === "worker" ? { ok: false, error: "firmware-container-xml-invalid" } : inspection; },
    saveFile: async (_name, result) => { saves++; exported = result; return { ok: true, saved: true }; },
    connectionStatus: () => null, previewField: () => { throw new Error("must-not-touch-coding"); } });
  const prepare = () => preparation.handle({ action: "prepare-firmware", ecu: "dme" });
  const save = () => preparation.handle({ action: "export-firmware", ecu: "dme" });
  const first = await prepare();
  assert.equal(first.ok, true);
  assert.equal(first.preparation.hashMatches, true);
  assert.equal(first.preparation.executionEnabled, false);
  assert.equal(first.preparation.writePayload, null);
  await save();
  assert.deepEqual(exported.container, inspection.container, "export preserves full inspected document and payload metadata");
  canceled = true;
  assert.equal((await prepare()).canceled, true);
  assert.equal((await save()).error, "firmware-not-prepared", "canceled replacement cannot export old firmware");
  canceled = false;
  for (const failure of ["hash", "worker"]) {
    fail = null; assert.equal((await prepare()).ok, true); fail = failure;
    const result = await prepare();
    assert.equal(result.error, failure === "hash" ? "firmware-container-file-changed" : "firmware-container-xml-invalid");
    assert.equal((await save()).error, "firmware-not-prepared");
  }
  assert.equal(saves, 1);
  assert.equal(inspections, 5);
  const after = await fs.readFile(file); assert.deepEqual(after, bytes);
  console.log("firmware container PASS: real UTF-8 Python bridge, private picker scope, payload hash, complete export, cancel/failure invalidation and immutable input; no device/vehicle access");
} finally { await fs.rm(directory, { recursive: true, force: true }); }
