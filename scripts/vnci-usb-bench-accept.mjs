/** Real USB-only VNCI monitor. Guard forbids CAN logical links and communication primitives. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { resolvePythonCandidates } from "../apps/desktop/electron/offline-diagnostics.mjs";
import { waitForIpc } from "./obd-accept-helpers.mjs";

const root = path.resolve(import.meta.dirname, "..");
const holdMs = Number(process.argv[2] || 60000);
assert.ok(Number.isInteger(holdMs) && holdMs >= 5000 && holdMs <= 600000);
const delivery = process.argv[3] ? path.resolve(process.argv[3]) : null;
const codeRoot = delivery ? path.join(delivery, "resources/app") : root;
const base = path.join(root, ".local/obd-precar-followup-20261005"); fs.mkdirSync(base, { recursive: true });
const output = fs.mkdtempSync(path.join(base, "vnci-usb-"));
const worker = path.join(root, "scripts/diagnostics/tests/vnci_bench_worker.py");
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(output, "isolated.db"), PORSCHE981_USER_DATA: path.join(output, "user data"),
  PORSCHE981_CONNECTION_STATE: path.join(output, "unused-state.json"), PORSCHE981_BENCH_CODE_ROOT: codeRoot,
  PORSCHE981_VNCI_ROOT: path.join(root, ".local/vnci-support/vendor/VW_PDUAPI_OS") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
if (delivery) { env.PATH = path.join(process.env.SystemRoot, "System32"); env.PORSCHE981_PYTHON = path.join(codeRoot, "runtime/python/python.exe"); }
let python;
for (const candidate of resolvePythonCandidates(env)) {
  const result = spawnSync(candidate.exe, [...candidate.prefix, "-c", "import sys;print(sys.executable)"],
    { env, encoding: "utf8", windowsHide: true, timeout: 10000 });
  if (result.status === 0 && result.stdout.trim()) { python = result.stdout.trim(); break; }
}
assert.ok(python, "direct CPython required");
const discovered = spawnSync(python, ["-X", "utf8", worker, output, "--discover"],
  { cwd: codeRoot, env, encoding: "utf8", windowsHide: true, timeout: 20000 });
assert.equal(discovered.status, 0, discovered.stderr);
const devices = JSON.parse(discovered.stdout.trim().split("\n").at(-1)).devices.filter((d) => d.available);
if (devices.length !== 1) {
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: false, error: "one-available-VNCI-required",
    availableCount: devices.length, ecuRequestsSent: 0, output }, null, 2));
  throw new Error(`USB bench requires one available VNCI; found ${devices.length}. Evidence: ${output}`);
}
const device = devices[0], states = [], errors = [];
let app;
const watchdog = setTimeout(() => { console.error("VNCI bench total timeout"); void app?.close(); }, holdMs + 120000);
try {
  app = await electron.launch(delivery ? { executablePath: path.join(delivery, "FlatSix.exe"), args: [], env, timeout: 30000 }
    : { args: [path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(20000); page.on("pageerror", (e) => errors.push(e.message));
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule("vm"), cp = process.getBuiltinModule("child_process"), fs = process.getBuiltinModule("fs");
    const { createObdConnectionManager, CONNECTION_CHANNEL } = await vm.runInThisContext(
      `import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    let number = 0;
    const mgr = createObdConnectionManager({ repoRoot: args.codeRoot, env: { ...process.env, PORSCHE981_CONNECTION_STATE: args.output + "/connection.json" },
      listFn: async () => ({ devices: [args.device], errors: [] }),
      onMonitorEvent: (event) => fs.appendFileSync(args.output + "/lifecycle.jsonl", JSON.stringify(event) + "\n"),
      monitorSpawnFn: (_exe, _args, opts) => {
        const child = cp.spawn(args.python, ["-X", "utf8", args.worker, args.output], opts);
        const name = args.output + `/monitor-${++number}.jsonl`;
        child.stdout.on("data", (bytes) => fs.appendFileSync(name, bytes));
        child.stderr.on("data", (bytes) => fs.appendFileSync(name + ".stderr", bytes));
        return child;
      } });
    globalThis.__vnciBench = mgr;
    ipcMain.removeHandler(CONNECTION_CHANNEL); ipcMain.handle(CONNECTION_CHANNEL, (_event, request) => mgr.handle(request));
  }, { codeRoot, output, worker, python, device,
    module: pathToFileURL(path.join(codeRoot, "apps/desktop/electron/obd-connection.mjs")).href });
  await page.locator('nav.side button[data-tab="obd"]').click(); await page.locator('[data-obd-tab="connection"]').click();
  await page.getByTestId("obd-conn-refresh").click(); await page.locator('input[name="obd-device"]').check();
  for (const [cycle, duration] of [holdMs, 5000].entries()) {
    await page.getByTestId("obd-conn-connect").click();
    await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).connected, undefined, 40000);
    await page.locator('[data-obd-tab="live"]').click();
    const started = Date.now();
    do {
      const state = await page.evaluate(() => window.porsche981.obdConnection({ action: "status" }));
      assert.equal(state.connected, true, JSON.stringify(state.monitorDiagnostics));
      assert.equal(state.monitorDiagnostics.starts, cycle + 1, "unexpected monitor restart");
      states.push({ cycle: cycle + 1, elapsedMs: Date.now() - started, voltageVolts: state.voltageVolts,
        starts: state.monitorDiagnostics.starts });
      await new Promise((resolve) => setTimeout(resolve, Math.min(2000, Math.max(1, duration - (Date.now() - started)))));
    } while (Date.now() - started < duration);
    await page.locator('[data-obd-tab="connection"]').click(); await page.getByTestId("obd-conn-disconnect").click();
    await waitForIpc(page, async () => (await window.porsche981.obdConnection({ action: "status" })).linkState === "idle");
    assert.equal(await app.evaluate(() => globalThis.__vnciBench.gate.owner()), null);
  }
  const events = fs.readFileSync(path.join(output, "lifecycle.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
  assert.equal(events.filter((e) => e.type === "started").length, 2);
  assert.equal(events.filter((e) => ["watchdog", "forced-kill", "close-timeout", "retry-scheduled"].includes(e.type)).length, 0);
  assert.ok(events.filter((e) => e.type === "closed").every((e) => e.stopReported && e.code === 0));
  const cycles = [1, 2].map((number) => {
    const docs = fs.readFileSync(path.join(output, `monitor-${number}.jsonl`), "utf8").trim().split("\n").map(JSON.parse);
    assert.equal(docs.at(-1).type, "stopped"); assert.deepEqual(docs.at(-1).restoration.errors, []);
    const readings = docs.filter((d) => ["handshake", "reading"].includes(d.type));
    assert.ok(readings.every((d) => d.ok && d.commOk && d.simulation === false));
    const stamps = readings.map((d) => Date.parse(d.at));
    return { samples: readings.length, spanMs: stamps.at(-1) - stamps[0],
      maxGapMs: Math.max(0, ...stamps.slice(1).map((at, i) => at - stamps[i])) };
  });
  assert.ok(cycles[0].spanMs >= holdMs - 3000); assert.deepEqual(errors, []);
  const native = fs.readdirSync(output).filter((n) => /^native-\d+\.jsonl$/.test(n))
    .flatMap((n) => fs.readFileSync(path.join(output, n), "utf8").trim().split("\n").map(JSON.parse));
  assert.ok(native.every((event) => !/ComLogicalLink|ComPrimitive|^PDUConnect$/.test(event.api)));
  assert.ok(native.filter((e) => e.phase === "end").every((e) => e.ok));
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify({ ok: true, guardedRealAdapterOnly: true,
    packaged: Boolean(delivery), developerPathRemoved: Boolean(delivery), cycles, states, events,
    nativeCalls: native.filter((e) => e.phase === "end").length, ecuRequestsSent: 0, vehicleVerified: false,
    physicalSilenceVerified: false, historicalRestartCauseResolved: false, errors }, null, 2));
  console.log(JSON.stringify({ ok: true, output, holdMs, cycles, ecuRequestsSent: 0 }));
} finally {
  clearTimeout(watchdog);
  if (app) { await app.evaluate(async () => globalThis.__vnciBench?.shutdown()).catch(() => {}); await app.close(); }
}
