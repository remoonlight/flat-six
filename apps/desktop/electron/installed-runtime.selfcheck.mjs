import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { installedRuntime, definitionLibraryRoot } from "./installed-runtime.mjs";
import { resolvePythonCandidates } from "./offline-diagnostics.mjs";
const root = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-runtime-"));
const repoRoot = path.join(root, "app"), userData = path.join(root, "user");
const devEnv = { CUSTOM: "unchanged" };
assert.equal(definitionLibraryRoot("C:\\Users\\a\\AppData\\Roaming\\flat-six", "win32"),
  "\\\\?\\C:\\Users\\a\\AppData\\Roaming\\flat-six\\diagnostic-library");
assert.equal(definitionLibraryRoot("\\\\server\\share\\flat-six", "win32"),
  "\\\\?\\UNC\\server\\share\\flat-six\\diagnostic-library");
assert.equal(definitionLibraryRoot("/tmp/flat-six", "darwin"), "/tmp/flat-six/diagnostic-library");
const development = installedRuntime({ packaged: false, repoRoot, userData, env: devEnv });
assert.equal(development.definitionsRoot, definitionLibraryRoot(userData));
assert.equal(development.env.CUSTOM, "unchanged");
assert.ok(development.env.PORSCHE981_REALTIME_PREPARATION.startsWith(development.definitionsRoot + path.sep));
assert.equal(fs.existsSync(development.definitionsRoot), false, "startup does not invent or unpack private definitions");
const explicit = installedRuntime({ packaged: false, repoRoot, userData, env: { PORSCHE981_DEFINITION_ROOT: "/explicit", PORSCHE981_VARIANTS: "/custom-variants" } });
assert.equal(explicit.definitionsRoot, "/explicit");
assert.equal(explicit.env.PORSCHE981_VARIANTS, "/custom-variants");
fs.mkdirSync(path.join(repoRoot, ".local/diagnostics/realtime-preparation"), { recursive: true });
fs.writeFileSync(path.join(repoRoot, ".local/diagnostics/realtime-preparation/index.json"), "{}");
assert.equal(installedRuntime({ packaged: false, repoRoot, userData, env: devEnv }).env, devEnv, "existing checkout library remains supported");
assert.throws(() => installedRuntime({ packaged: true, repoRoot, userData, env: {} }), /runtime-missing/);
fs.mkdirSync(path.join(repoRoot, "runtime/python"), { recursive: true });
fs.writeFileSync(path.join(repoRoot, "runtime/node.exe"), "fixture");
fs.writeFileSync(path.join(repoRoot, "runtime/python/python.exe"), "fixture");
fs.mkdirSync(path.join(repoRoot, "data/seed/diagnostics"), { recursive: true });
fs.writeFileSync(path.join(repoRoot, "data/seed/diagnostics/source.json"), "original");
const runtime = installedRuntime({ packaged: true, repoRoot, userData, env: {} });
assert.ok(runtime.env.PORSCHE981_PYTHON.startsWith(repoRoot));
assert.ok(runtime.env.PORSCHE981_VARIANTS.startsWith(runtime.definitionsRoot + path.sep));
for (const key of ["PORSCHE981_CONNECTION_STATE", "PORSCHE981_SESSION_ARTIFACT_ROOT", "PORSCHE981_CAN_CAPTURE_ROOT", "PORSCHE981_LOCAL_ROOT"])
  assert.ok(runtime.env[key].startsWith(userData), key);
assert.deepEqual(resolvePythonCandidates(runtime.env)[0].prefix, ["-B", "-X", "utf8"]);
const destination = path.join(runtime.definitionsRoot, "data/seed/diagnostics/source.json");
fs.writeFileSync(destination, "user's imported contents");
installedRuntime({ packaged: true, repoRoot, userData, env: {} });
assert.equal(fs.readFileSync(destination, "utf8"), "user's imported contents", "startup must preserve imported definitions");
// Test owns this fresh temp directory; there are no user files here.
assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
fs.rmSync(root, { recursive: true });
console.log("installed-runtime.selfcheck: ok");
