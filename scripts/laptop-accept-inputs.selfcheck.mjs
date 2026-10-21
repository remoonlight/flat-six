/** Synthetic filesystem/PE fixtures; never launch Windows, DLLs or devices. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { inventory, verifyAcceptanceKit, verifyLaptopAcceptanceInputs } from "./laptop-accept-inputs.mjs";
import { installedAppPackage } from "../apps/desktop/electron/installed-entry.mjs";
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "flat-six-laptop-kit-"));
const delivery = path.join(scratch, "Flat Six 测试"), kit = path.join(scratch, "kit 测试"), bundle = path.join(scratch, "资料.zip");
const put = (root, name, data) => { const file = path.join(root, name); fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, data); };
const pe = Buffer.alloc(128); pe.write("MZ"); pe.writeUInt32LE(64, 0x3c); pe.writeUInt32LE(0x4550, 64); pe.writeUInt16LE(0x8664, 68);
let checks = 0;
try {
  for (const name of ["FlatSix.exe", "resources/app/runtime/node.exe", "resources/app/runtime/python/python.exe"]) put(delivery, name, pe);
  put(delivery, "version", "34.5.8"); put(delivery, "resources/app/package.json", JSON.stringify(installedAppPackage()));
  for (const name of ["installed-bootstrap.cjs", "installed-entry.mjs", "main.mjs", "preload.cjs"]) put(delivery, "resources/app/apps/desktop/electron/" + name, "SYNTHETIC static file");
  put(delivery, "resources/app/apps/desktop/dist/index.html", "SYNTHETIC");
  put(scratch, "资料.zip", "SYNTHETIC; preflight hashes only; GUI import still validates contents");
  const manifest = { kind: "local-windows-unpacked-delivery", bundledRuntimes: true,
    privateDefinitionsIncluded: false, driversIncluded: false, electronVersion: "34.5.8", files: inventory(delivery) };
  put(delivery, "delivery-manifest.json", JSON.stringify(manifest));
  const first = verifyLaptopAcceptanceInputs(delivery, bundle); assert.equal(first.staticOnly, true); assert.equal(first.windowsExecutionVerified, false); checks++;
  for (const name of ["accept-laptop.cmd", "README.md", "scripts/windows-runtime-accept.mjs", "scripts/windows-runtime-source.mjs",
    "scripts/laptop-accept-inputs.mjs", "scripts/obd-unidentified-ui-check.mjs", "apps/desktop/electron/offline-diagnostics.mjs", "apps/desktop/electron/installed-entry.mjs",
    "node_modules/playwright/index.mjs", "node_modules/playwright-core/index.mjs"]) put(kit, name, "SYNTHETIC fixture only");
  put(kit, "package.json", JSON.stringify({ name: "flat-six-laptop-acceptance", type: "module" }));
  for (const name of ["playwright", "playwright-core"]) put(kit, `node_modules/${name}/package.json`, JSON.stringify({ name, version: "1.62.1" }));
  const bound = { kind: "flat-six-laptop-acceptance-v1", programIncluded: false, privateDefinitionsIncluded: false,
    deliveryManifestSha256: first.deliveryManifestSha256, definitionZipSha256: first.definitionZipSha256,
    electronVersion: "34.5.8", playwrightVersion: "1.62.1", files: inventory(kit) };
  const reset = () => put(kit, "laptop-accept-manifest.json", JSON.stringify(bound)); reset();
  assert.equal(verifyLaptopAcceptanceInputs(delivery, bundle, kit).kitBound, true); checks++;
  put(kit, ".local/previous-evidence.json", "preserve evidence; excluded from tool manifest"); assert.ok(verifyAcceptanceKit(kit)); checks++;
  for (const changed of [{ ...bound, files: [...bound.files, bound.files[0]] },
    { ...bound, files: bound.files.filter(row => row.path !== "accept-laptop.cmd") },
    { ...bound, deliveryManifestSha256: "0".repeat(64) }, { ...bound, definitionZipSha256: "0".repeat(64) },
    { ...bound, programIncluded: true }, { ...bound, files: [...bound.files, { path: "../outside", bytes: 0, sha256: "0".repeat(64) }] }]) {
    put(kit, "laptop-accept-manifest.json", JSON.stringify(changed)); assert.throws(() => verifyLaptopAcceptanceInputs(delivery, bundle, kit)); checks++;
  }
  reset();
  const required = path.join(kit, "scripts/windows-runtime-accept.mjs"); fs.appendFileSync(required, "changed");
  assert.throws(() => verifyAcceptanceKit(kit), /integrity/); fs.writeFileSync(required, "SYNTHETIC fixture only"); checks++;
  put(kit, "unexpected.txt", "extra"); assert.throws(() => verifyAcceptanceKit(kit), /unexpected/); fs.unlinkSync(path.join(kit, "unexpected.txt")); checks++;
  const node = path.join(delivery, "resources/app/runtime/node.exe"); fs.appendFileSync(node, "changed");
  assert.throws(() => verifyLaptopAcceptanceInputs(delivery, bundle), /integrity/); fs.writeFileSync(node, pe); checks++;
  const main = path.join(delivery, "resources/app/apps/desktop/electron/main.mjs"); fs.appendFileSync(main, "changed");
  assert.throws(() => verifyLaptopAcceptanceInputs(delivery, bundle), /integrity/); fs.writeFileSync(main, "SYNTHETIC static file"); checks++;
  const original = fs.readFileSync(bundle); fs.appendFileSync(bundle, "changed");
  assert.throws(() => verifyLaptopAcceptanceInputs(delivery, bundle, kit), /input-mismatch/); fs.writeFileSync(bundle, original); checks++;
  fs.renameSync(main, main + ".original"); fs.symlinkSync(main + ".original", main);
  assert.throws(() => verifyLaptopAcceptanceInputs(delivery, bundle), /symlink/); fs.unlinkSync(main); fs.renameSync(main + ".original", main); checks++;
  const extra = path.join(delivery, "unexpected.txt"); fs.writeFileSync(extra, "extra");
  assert.throws(() => verifyLaptopAcceptanceInputs(delivery, bundle), /unexpected/); fs.unlinkSync(extra); checks++;
  assert.equal(verifyLaptopAcceptanceInputs(delivery, bundle, kit).kitBound, true);
  console.log(JSON.stringify({ ok: true, checks, synthetic: true, windowsExecutionVerified: false, deviceIO: false }));
} finally { fs.rmSync(scratch, { recursive: true, force: true }); }
