/** Actual production IPC/native file choice. Inject only filesystem failure at the chosen directory. */
import { _electron as electron } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
const source = path.resolve(import.meta.dirname, ".."), root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || source);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, ".local/obd-generic-save-20261012/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), file = path.join(temp, "SYNTHETIC-prior.json");
const prior = '{"previous":"complete saved record"}\n';
const devices = path.join(temp, "devices.json"); await fs.writeFile(devices, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: devices,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "sessions") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const checks = [], errors = [];
const timeout = setTimeout(() => { console.error("recording save IPC total timeout"); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.on("pageerror", error => errors.push(error.message));
  assert.equal(path.resolve(await app.evaluate(({ app }) => app.getAppPath())), process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop"));
  await app.evaluate(({ dialog }, args) => {
    const fs = process.getBuiltinModule("fs"), path = process.getBuiltinModule("path");
    const originalOpen = fs.promises.open;
    const state = globalThis.__recordingSave = { mode: "success", calls: 0, opens: [], originalOpen };
    dialog.showSaveDialog = async (window, options) => {
      if (!window?.webContents || options?.title !== "保存此次采集" || options.defaultPath !== "failure.json") throw new Error("native-save-owner-or-options-invalid");
      state.calls++; return { canceled: state.mode === "cancel", filePath: args.file };
    };
    fs.promises.open = async function(filename, flags, ...rest) {
      if (path.dirname(filename) !== args.temp || !path.basename(filename).startsWith(".flat-six-")) return originalOpen(filename, flags, ...rest);
      state.opens.push({ filename, flags }); const handle = await originalOpen(filename, flags, ...rest);
      return {
        write: async (...values) => { if (state.mode === "write") throw Object.assign(new Error(), { code: "EIO" }); return handle.write(...values); },
        sync: async () => { if (state.mode === "sync") throw Object.assign(new Error(), { code: "ENOSPC" }); return handle.sync(); },
        close: async () => { await handle.close(); if (state.mode === "close") throw Object.assign(new Error(), { code: "EIO" }); },
      };
    };
  }, { temp, file });
  const payload = { fileName: "failure.json", recording: { kind: "synthetic-failed-diagnostic", error: "identity-mismatch", simulation: true, sourceKind: "acceptance-only" } };
  const call = input => page.evaluate(input => window.porsche981.saveDiagnosticRecording(input), input);
  assert.equal((await call({ ...payload, path: file })).error, "invalid_recording");
  assert.equal(await app.evaluate(() => globalThis.__recordingSave.calls), 0);
  checks.push("renderer destination injection rejected before the native dialog");
  for (const mode of ["cancel", "write", "sync", "close", "success"]) {
    await fs.writeFile(file, prior); await app.evaluate((_e, mode) => { globalThis.__recordingSave.mode = mode; }, mode);
    const result = await call(payload);
    if (mode === "success") {
      assert.equal(result.saved, true); assert.deepEqual(JSON.parse(await fs.readFile(file, "utf8")), payload.recording);
      assert.deepEqual(Object.keys(result).sort(), ["filePath", "ok", "saved"]);
    } else {
      assert.equal(result.saved, false); assert.equal(await fs.readFile(file, "utf8"), prior);
      if (mode === "cancel") assert.equal(result.canceled, true);
      else assert.equal(result.error, mode === "sync" ? "ENOSPC" : "EIO");
    }
    assert.equal((await fs.readdir(temp)).filter(name => name.endsWith(".tmp")).length, 0);
    checks.push(`native generic Save As ${mode}: correct result and previous-file protection`);
  }
  const opened = await app.evaluate(() => globalThis.__recordingSave.opens);
  assert.equal(opened.length, 4); assert.ok(opened.every(item => item.flags === "wx" && item.filename !== file));
  assert.deepEqual(errors, []); passed = true;
} finally {
  if (app) { await app.evaluate(() => { const state = globalThis.__recordingSave; if (state) process.getBuiltinModule("fs").promises.open = state.originalOpen; }).catch(() => {}); await app.close(); }
  clearTimeout(timeout);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, rendererErrors: errors, actualIpc: true,
    filesystemFaultInjected: true, deviceIO: false, vehicleIO: false, windowsExecuted: false }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
