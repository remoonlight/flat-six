/** Production renderer with explicit decoded IPC fixtures; no head/vehicle I/O. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { installRealtimeUiIdentityModel, selectIdentifiedRealtimeUnit } from "./obd-live-ui-identity-model.mjs";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";
const root = path.resolve(import.meta.dirname, ".."), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT), output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(root, ".local/obd-live-curves-20261012/source-ui"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-")), fixture = path.join(temp, "devices.json");
await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const env = { ...process.env, PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "assets"), PORSCHE981_CONNECTION_FIXTURE: fixture,
  PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"), PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "sessions") };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
if (appRoot) verifyInstalledEntry(appRoot);
const curveProfileId = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5";
async function configureCurves(app, params, samples) {
  await app.evaluate(({ ipcMain }, { params, samples, profileId }) => {
    ipcMain.removeHandler("diagnostics:offline");
    ipcMain.handle("diagnostics:offline", (_event, request) => {
      if (request.action === "ready-units") return { ok: true, present: true, units: [{ systemId: "dme", ecuId: 1, label: "DME",
        variants: [{ profileId, name: "Explicit renderer fixture", status: "identity-matched", parameterCount: params.length }] }] };
      const filtered = params.filter((p, i) => (!request.groupId || request.groupId === String(i)) && (!request.search || p.name.includes(request.search)));
      return { ok: true, items: filtered, total: filtered.length, categories: [{ id: "0", label: "冷却", count: 1 }, { id: "1", label: "进气", count: 1 }] };
    });
    let stopped = false, selected = []; const clock = globalThis.__curveDisplayTime = { value: null };
    globalThis.__realtimeUiReadHandler = (_event, request) => {
      if (request.action === "start-read") { stopped = false; selected = request.parameterIds; return { ok: true, sessionId: "decoded-curve-fixture" }; }
      if (request.action === "stop") { stopped = true; return { ok: true }; }
      if (request.action === "status") return { ok: true, state: stopped ? "finished" : "running", released: stopped, cursor: 1, connectionCursor: 0, plotElapsedMs: clock.value,
        result: stopped ? { ok: true, stopReason: "cancelled" } : null, connectionEvents: [], awaitingRetry: false, unavailableParameterIds: [],
        events: request.cursor ? [] : [{ ok: true, completedCycles: Math.max(...samples.map(s => s.cycle)), samples: samples.filter(s => selected.includes(s.parameterId)) }] };
      return { ok: true };
    };
  }, { params, samples, profileId: curveProfileId });
}
const parameter = (id, name, unit, index) => ({ id, name, nameResolved: true, unit, requestHex: "2101", decoderReady: true,
  decoderIssue: null, status: "ready", decodedSampleCount: 0, byteOffset: index, bitOffset: 0, definitionOffset: 100 + index });
let app, passed = false; const errors = [], checks = [];
const watchdog = setTimeout(() => { console.error("curve UI total timeout"); void app?.close(); }, 120000);
try {
  app = await electron.launch({ args: [appRoot || path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on("pageerror", e => errors.push(e.message));
  app.on("close", () => { if (!passed) console.error("curve acceptance Electron closed before completion"); });
  app.process().on("exit", (code, signal) => { if (!passed) console.error(JSON.stringify({ electronExitCode: code, electronExitSignal: signal })); });
  await page.locator('nav.side [data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  const initial = [["冷却液温度", "°C", 90], ["进气温度", "°C", 30], ["转速", "rpm", 2000], ["车速", "km/h", 60], ["负荷", "%", 40], ["节气门", "%", 20]];
  const params = initial.map(([name, unit], i) => parameter(String(i).repeat(64), name, unit, i));
  const samples = [0, 1, 2].flatMap(cycle => params.map((p, i) => ({ parameterId: p.id, value: initial[i][2], display: String(initial[i][2]),
    numeric: true, synthetic: true, cycle: cycle + 1, elapsedMs: cycle * 1000, processedUtc: "2026-10-20T01:00:00.000Z", connectionEpoch: 1 })));
  await configureCurves(app, params, samples);
  await installRealtimeUiIdentityModel(app, { 1: curveProfileId, 9: "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1" });
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId: curveProfileId });
  assert.equal(await page.getByTestId("manufacturer-display-text").getAttribute("aria-pressed"), "true");
  for (const param of params) await page.getByTestId("offline-select-" + param.id).check();
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent.includes("18 个数值"));
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  await page.getByTestId("manufacturer-display-graph").click();
  const groups = page.getByTestId("live-curve-group"); assert.equal(await groups.count(), 4);
  const temperatures = groups.filter({ has: page.locator('[data-parameter-id="' + params[0].id + '"]') });
  assert.match(await temperatures.innerText(), /冷却液温度/); assert.match(await temperatures.innerText(), /进气温度/);
  assert.equal(await temperatures.getAttribute("data-unit"), "°C"); assert.equal(await temperatures.locator("svg").count(), 1);
  assert.equal(await temperatures.locator("[data-curve-id]").count(), 2); assert.equal(await temperatures.locator("circle").count(), 6);
  assert.notEqual(await temperatures.locator("polyline").nth(0).getAttribute("stroke"), await temperatures.locator("polyline").nth(1).getAttribute("stroke"));
  assert.doesNotMatch(await temperatures.innerText(), /上次更新/);
  checks.push("modeled automatic identity then manual selection/start: six parameters, four groups, temperature overlays with names, units and distinct colors");
  await page.getByTestId("manufacturer-display-both").click();
  assert.equal(await page.locator('[data-testid="manufacturer-rehearsal"] > [data-parameter-id]').count(), 6);
  await page.setViewportSize({ width: 760, height: 900 }); await page.getByTestId("live-curve-groups").scrollIntoViewIfNeeded();
  assert.ok(await page.getByTestId("live-curve-groups").evaluate(el => el.scrollWidth <= el.clientWidth));
  await page.screenshot({ path: path.join(output, "grouped-curves.png"), fullPage: true });
  await page.getByTestId("offline-select-" + params[1].id).uncheck();
  assert.equal(await page.locator('[data-parameter-id="' + params[1].id + '"]').count(), 0);
  assert.equal(await page.locator('[data-curve-id] circle').count(), 0, "changing selection discards prior batch");
  checks.push("both display and narrow layout; changing the expanded selection discards prior samples without a prompt");
  const mixedParams = ["冷却液温度", "进气温度", "转速"].map((name, i) => parameter(["b", "a", "c"][i].repeat(64), name, ["°C", "°F", "rpm"][i], i));
  const mixedSamples = mixedParams.map((p, i) => ({ parameterId: p.id, value: [100, 212, 2000][i], display: ["100", "212", "2000"][i],
    numeric: true, synthetic: true, cycle: 1, elapsedMs: i === 2 ? 140001 : 70000, processedUtc: "2026-10-20T01:01:10.000Z", connectionEpoch: 1 }));
  await configureCurves(app, mixedParams, mixedSamples);
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId: curveProfileId });
  const firstId = "b".repeat(64), secondId = "a".repeat(64);
  await page.getByTestId(`offline-select-${secondId}`).check();
  await page.getByTestId(`offline-select-${firstId}`).check();
  assert.deepEqual(await page.getByTestId("offline-selected").locator("button").allTextContents(), ["冷却液温度 ×", "进气温度 ×"]);
  await page.getByTestId("offline-category").selectOption("1");
  await page.getByTestId(`offline-select-${firstId}`).waitFor({ state: "detached" });
  assert.match(await page.getByTestId("offline-selected").innerText(), /冷却液温度/);
  await page.getByTestId(`offline-select-${secondId}`).check();
  await page.getByTestId("offline-search").fill("没有匹配");
  await page.getByTestId(`offline-select-${secondId}`).waitFor({ state: "detached" });
  assert.equal(await page.getByTestId("offline-selected").locator("button").count(), 2);
  await page.getByTestId("offline-search").fill("");
  await page.getByTestId("offline-category").selectOption("");
  assert.equal(await page.getByTestId(`offline-select-${firstId}`).isChecked(), true);
  assert.equal(await page.getByTestId(`offline-select-${secondId}`).isChecked(), true);
  checks.push("explicit catalog fixture: search/category filters preserve selections and selected summary; clearing filters restores checked rows");
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("2 个数值"));
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  assert.equal(await page.getByTestId("manufacturer-error").count(), 0);
  const panel = page.getByTestId("manufacturer-state").locator("..");
  assert.doesNotMatch(await panel.innerText(), /212|°F/);
  assert.match(await panel.innerText(), /进气温度：100 °C/);
  await page.getByTestId("manufacturer-display-graph").click();
  const combined = page.getByTestId("live-curve-group");
  assert.equal(await combined.count(), 1); assert.equal(await combined.getAttribute("data-unit"), "°C");
  assert.equal(await combined.locator("[data-curve-id]").count(), 2);
  assert.deepEqual(await combined.locator("[data-curve-id]").evaluateAll(rows => rows.map(row => row.getAttribute("data-curve-id"))), [firstId, secondId]);
  assert.equal(await combined.locator("circle").nth(0).getAttribute("cy"), await combined.locator("circle").nth(1).getAttribute("cy"));
  assert.doesNotMatch(await combined.innerText(), /212|°F/);
  await page.screenshot({ path: path.join(output, "mixed-temperature-units.png"), fullPage: true });
  checks.push("explicit decoded fixture: Fahrenheit/Celsius values agree in numeric view and one converted graph after stop; original source values retained");
  await page.getByTestId(`offline-select-${"c".repeat(64)}`).check();
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("3 个数值"));
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  await page.getByTestId("manufacturer-display-graph").click();
  const expired = page.getByTestId("live-curve-group").filter({ has: page.locator(`[data-parameter-id="${firstId}"]`) });
  assert.match(await expired.innerText(), /冷却液温度：100.*进气温度：100/s);
  assert.match(await expired.getByTestId("live-curve-empty").innerText(), /最近60秒无有效数值/);
  assert.equal(await expired.locator("svg").count(), 0);
  const current = page.getByTestId("live-curve-group").filter({ has: page.locator(`[data-parameter-id="${"c".repeat(64)}"]`) });
  assert.equal(await current.locator("circle").count(), 1);
  assert.match(await current.locator("svg").textContent(), /80\.001 s.*140 s/s);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /3 个数值/);
  checks.push("different parameter types share the latest batch window; expired curves disappear while last values and all retained samples remain after stop");
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("3 个数值"));
  await page.getByTestId("manufacturer-display-graph").click();
  await app.evaluate(() => { globalThis.__curveDisplayTime.value = 210002; });
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="live-curve-empty"]').length === 2
    && document.querySelectorAll('[data-testid="live-curve-group"] svg').length === 0);
  assert.match(await page.getByTestId("live-curve-groups").innerText(), /100.*2000/s);
  assert.match(await page.getByTestId("manufacturer-state").innerText(), /3 个数值/);
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  await page.getByTestId("manufacturer-display-text").click();
  assert.match(await panel.innerText(), /100 °C/); assert.match(await panel.innerText(), /2000 rpm/);
  await page.getByTestId("manufacturer-display-graph").click();
  assert.equal(await page.getByTestId("live-curve-empty").count(), 2);
  assert.equal(await page.locator('[data-testid="live-curve-group"] svg').count(), 0);
  checks.push("authoritative batch time advances every graph through complete silence; stop/view switching preserve the frozen window, last values and the same three samples");
  const sourceUnits = [
    ["转速甲", "转/分", 1500, "1500", "rpm"], ["转速乙", "rpm", 2000, "2000", "rpm"],
    ["电阻甲", "Ohm", 2, "2", "Ω"], ["电阻乙", "mOhm", 1500, "1.5", "Ω"],
    ["频率甲", "kHz", 2, "2000", "Hz"], ["频率乙", "MHz", .5, "500000", "Hz"],
    ["时长甲", "ms", 1500, "1.5", "s"], ["时长乙", "us", 250000, "0.25", "s"],
    ["速度甲", "knot", 10, "18.52", "km/h"], ["速度乙", "km/h", 20, "20", "km/h"],
  ];
  const unitParams = sourceUnits.map(([name, unit], i) => parameter(i.toString(16).repeat(64), name, unit, i));
  const unitSamples = unitParams.map((p, i) => ({ parameterId: p.id, value: sourceUnits[i][2], display: String(sourceUnits[i][2]),
    numeric: true, synthetic: true, cycle: 1, elapsedMs: 1000, processedUtc: "2026-10-20T01:00:01.000Z", connectionEpoch: 1 }));
  await configureCurves(app, unitParams, unitSamples);
  await selectIdentifiedRealtimeUnit(app, page, { systemId: "dme", ecuId: 1, profileId: curveProfileId });
  for (const p of unitParams) await page.getByTestId(`offline-select-${p.id}`).check();
  await page.getByTestId("manufacturer-read").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="manufacturer-state"]')?.textContent?.includes("10 个数值"));
  await page.getByTestId("manufacturer-stop").click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="manufacturer-save"]')?.disabled);
  await page.getByTestId("manufacturer-display-text").click();
  for (const [name, , , value, unit] of sourceUnits) assert.match(await panel.innerText(), new RegExp(`${name}：${value.replaceAll(".", "\\.")} ${unit}`));
  await page.getByTestId("manufacturer-display-graph").click();
  assert.equal(await page.getByTestId("live-curve-group").count(), 5);
  for (const unit of ["rpm", "Ω", "Hz", "s", "km/h"]) {
    const group = page.locator('[data-testid="live-curve-group"]').filter({ has: page.locator(`[data-curve-id="${unitParams[sourceUnits.findIndex(r => r[4] === unit)].id}"]`) });
    assert.equal(await group.getAttribute("data-unit"), unit);
    assert.equal(await group.locator("[data-curve-id]").count(), 2);
  }
  await page.screenshot({ path: path.join(output, "source-unit-groups.png"), fullPage: true });
  checks.push("explicit source-unit fixtures: numeric display and five paired graphs agree for Chinese rpm, knots, resistance, frequency and duration");
  assert.deepEqual(errors, []); passed = true;
} finally {
  if (app) await app.close(); clearTimeout(watchdog);
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify({ passed, checks, rendererErrors: errors,
    sourceOnly: !appRoot, installedEntryOnMac: Boolean(appRoot), host: "Mac", transport: "explicit identity and decoded IPC fixtures", deviceIO: false, vehicleIO: false,
    windowsDeliverySynchronized: false, goalComplete: false }, null, 2));
}
console.log(JSON.stringify({ passed, checks }));
