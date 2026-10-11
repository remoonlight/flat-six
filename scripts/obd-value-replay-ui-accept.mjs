/** Actual Electron/native-file IPC acceptance. All input files are explicitly synthetic. */
import { _electron as electron } from "playwright";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { verifyInstalledEntry } from "../apps/desktop/electron/installed-entry.mjs";
const source = path.resolve(import.meta.dirname, ".."), root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || source);
const base = path.join(source, ".local/obd-value-replay-20261010");
if (process.env.PORSCHE981_ACCEPT_APP_ROOT) verifyInstalledEntry(root);
const output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(base, "source-ui"));
const input = path.resolve(process.env.PORSCHE981_REPLAY_INPUT || path.join(base, "main-check"));
await fs.mkdir(output, { recursive: true });
const temp = await fs.mkdtemp(path.join(output, "isolated-"));
const fixture = path.join(temp, "devices.json"); await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] }));
const files = ["SYNTHETIC-standard.json", "SYNTHETIC-manufacturer.json", "SYNTHETIC-internal.json", "SYNTHETIC-many.json", "SYNTHETIC-extreme.json",
  "SYNTHETIC-connection-events.json", "SYNTHETIC-events-only.json", "SYNTHETIC-unconfirmed-recovery.json"].map(name => path.join(input, name));
const hashes = await Promise.all(files.map(async file => createHash("sha256").update(await fs.readFile(file)).digest("hex")));
const invalid = path.join(temp, "SYNTHETIC-truncated.json"); await fs.writeFile(invalid, "{");
const env = { ...process.env, PORSCHE981_DB: path.join(temp, "ui.db"), PORSCHE981_USER_DATA: path.join(temp, "user-data"),
  PORSCHE981_LOCAL_ROOT: path.join(temp, "local-assets"), PORSCHE981_CAN_CAPTURE_ROOT: path.join(temp, "raw-captures"),
  PORSCHE981_SESSION_ARTIFACT_ROOT: path.join(temp, "sessions"), PORSCHE981_CONNECTION_STATE: path.join(temp, "connection.json"),
  PORSCHE981_HEADLESS: "1", PORSCHE981_OBD_SMOKE: "1", PORSCHE981_SESSION_DENY_LIVE: "1",
  PORSCHE981_CONNECTION_FIXTURE: fixture, PYTHONDONTWRITEBYTECODE: "1" };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const errors = [];
const watchdog = setTimeout(() => { console.error("value replay UI total timeout"); void app?.close(); }, 120000);
try {
  app = await electron.launch({ args: [process.env.PORSCHE981_ACCEPT_APP_ROOT ? root : path.join(root, "apps/desktop")], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(15000);
  const startup = await app.evaluate(({ app }, entry) => {
    const { createRequire } = process.getBuiltinModule("module"), load = createRequire(entry);
    const cached = load.cache[load.resolve(entry)];
    return { appPath: app.getAppPath(), mainFile: cached?.filename || "", bootstrapLoaded: cached?.loaded === true };
  }, path.join(root, "apps/desktop/electron/installed-bootstrap.cjs"));
  if (process.env.PORSCHE981_ACCEPT_APP_ROOT) {
    assert.equal(path.resolve(startup.appPath), root);
    assert.equal(path.resolve(startup.mainFile), path.join(root, "apps/desktop/electron/installed-bootstrap.cjs"));
    assert.equal(startup.bootstrapLoaded, true);
  }
  page.on("pageerror", error => errors.push(error.message));
  await app.evaluate(({ dialog }) => {
    globalThis.__replayChoice = null; globalThis.__replayCanceled = false; globalThis.__replayDialogs = 0;
    dialog.showOpenDialog = async (window, options) => {
      if (!options || options.title !== "打开采集记录" || !window?.webContents) throw new Error("native-owner-or-options-missing");
      globalThis.__replayDialogs++;
      return { canceled: globalThis.__replayCanceled, filePaths: [globalThis.__replayChoice] };
    };
  });
  await page.locator('nav.side button[data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  const open = async file => {
    await app.evaluate((_electron, chosen) => { globalThis.__replayChoice = chosen; globalThis.__replayCanceled = false; }, file);
    await page.getByTestId("replay-open").click();
  };
  await open(files[0]);
  await page.getByTestId("replay-summary").filter({ hasText: "6000 条" }).waitFor();
  assert.match(await page.getByTestId("replay-summary").innerText(), /历史采集.*模拟来源/);
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "1–100 / 6000");
  const seen = [];
  for (let offset = 0; offset < 6000; offset += 100) {
    await page.waitForFunction(expected => Number(document.querySelector('[data-testid="replay-rows"] tr td')?.textContent) === expected, offset + 1);
    const rows = await page.getByTestId("replay-rows").locator("tr").evaluateAll(items => items.map(row => [...row.querySelectorAll("td")].map(td => td.textContent)));
    assert.equal(rows.length, 100);
    rows.forEach((row, i) => { assert.equal(Number(row[0]), offset + i + 1); assert.equal(Number(row[4]), Math.floor((offset + i) / 2) + 1); });
    seen.push(...rows.map(row => row[0]));
    if (offset + 100 < 6000) await page.getByTestId("replay-next").click();
  }
  assert.equal(new Set(seen).size, 6000); assert.equal(await page.getByTestId("replay-next").isDisabled(), true);
  await page.getByTestId("replay-prev").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "5801–5900 / 6000");
  const seek = async position => {
    await page.getByTestId("replay-position").evaluate((el, value) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, String(value));
      el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
    }, position);
  };
  await seek(80000);
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-time"]')?.textContent.startsWith("80.000 s"));
  await page.getByTestId("replay-series").filter({ hasText: "电压 · 2998 V" }).waitFor();
  assert.equal(await page.locator('[data-testid="acquisition-replay"] polyline').count(), 0, "no interpolation across gaps");
  await page.getByTestId("replay-position").press("End");
  await page.getByTestId("replay-series").filter({ hasText: "转速 · 5999 rpm" }).waitFor();
  await page.getByTestId("acquisition-replay").locator("details > summary").click();
  await page.getByTestId("replay-parameters").locator('input[type="checkbox"]').first().uncheck();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "1–100 / 3000");
  await page.getByTestId("replay-parameters").locator('input[type="checkbox"]').first().check();
  await page.getByRole("combobox", { name: "回放速度" }).selectOption("5");
  await page.getByTestId("replay-play").click();
  await page.waitForFunction(() => parseFloat(document.querySelector('[data-testid="replay-time"]')?.textContent) > .2
    && parseFloat(document.querySelector('[data-testid="replay-time"]')?.textContent) < 10);
  await page.getByTestId("replay-play").click();
  const paused = await page.getByTestId("replay-time").innerText(); await new Promise(resolve => setTimeout(resolve, 250));
  assert.equal(await page.getByTestId("replay-time").innerText(), paused);
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelectorAll("main, .main, .content").forEach(el => el.scrollTop = 0); });
  await page.screenshot({ path: path.join(output, "standard-replay.png"), fullPage: true });
  await app.evaluate(() => { globalThis.__replayCanceled = true; }); await page.getByTestId("replay-open").click();
  assert.match(await page.getByTestId("replay-summary").innerText(), /6000 条/);
  await open(invalid); await page.getByTestId("replay-error").filter({ hasText: "可能尚未写完" }).waitFor();
  assert.match(await page.getByTestId("replay-summary").innerText(), /6000 条/);
  await open(files[1]); await page.getByTestId("replay-summary").filter({ hasText: "3 条" }).waitFor();
  await page.getByTestId("replay-series").filter({ hasText: "档位 · 驻车" }).waitFor();
  await seek(2000); await page.getByTestId("replay-series").filter({ hasText: "档位 · 前进" }).waitFor();
  assert.match(await page.getByTestId("replay-rows").innerText(), /前进/);
  assert.match(await page.getByTestId("acquisition-replay").innerText(), /adapter_release_not_verified/);
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelectorAll("main, .main, .content").forEach(el => el.scrollTop = 0); });
  await page.screenshot({ path: path.join(output, "manufacturer-replay.png"), fullPage: true });
  await open(files[3]); await page.getByTestId("replay-summary").filter({ hasText: "35 条" }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="replay-series"]').length === 12);
  await page.getByTestId("replay-series-next").click();
  await page.getByTestId("replay-series").filter({ hasText: "参数12 · 12.4 V" }).waitFor();
  await page.getByTestId("replay-series-next").click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="replay-series"]').length === 11);
  await page.getByTestId("replay-series").filter({ hasText: "参数34 · 12.4 V" }).waitFor();
  await page.getByTestId("replay-series-prev").click();
  const details = page.getByTestId("acquisition-replay").locator("details");
  if (!await details.evaluate(el => el.open)) await details.locator("summary").click();
  await page.getByRole("textbox", { name: "查找回放参数" }).fill("参数34");
  assert.equal(await page.getByTestId("replay-parameters").locator("input").count(), 1);
  await page.getByTestId("replay-parameters").locator("input").uncheck();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "1–34 / 34");
  await details.getByRole("button", { name: "取消全选", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "0 条");
  await details.getByRole("button", { name: "全选", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "1–35 / 35");
  await open(files[4]); await page.getByTestId("replay-summary").filter({ hasText: "2 条" }).waitFor();
  await seek(2000);
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="acquisition-replay"] circle').length === 2);
  assert.equal(await page.locator('[data-testid="acquisition-replay"] circle').evaluateAll(nodes => nodes.every(node =>
    Number.isFinite(Number(node.getAttribute("cx"))) && Number.isFinite(Number(node.getAttribute("cy"))))), true);
  await open(files[5]); await page.getByTestId("replay-summary").filter({ hasText: "212 个通信事件" }).waitFor();
  await seek(25); await page.getByTestId("replay-connection-current").filter({ hasText: "身份一致，等待有效回答" }).waitFor();
  assert.doesNotMatch(await page.getByTestId("replay-connection-current").innerText(), /通信恢复/);
  const eventSeen = [];
  for (let eventOffset = 0; eventOffset < 212; eventOffset += 100) {
    await page.waitForFunction(expected => Number(document.querySelector('[data-testid="replay-connection-rows"] tr td')?.textContent) === expected, eventOffset + 1);
    const rows = await page.getByTestId("replay-connection-rows").locator("tr").evaluateAll(items => items.map(row => [...row.querySelectorAll("td")].map(td => td.textContent)));
    assert.equal(rows.length, Math.min(100, 212 - eventOffset));
    rows.forEach((row, i) => assert.equal(Number(row[0]), eventOffset + i + 1)); eventSeen.push(...rows.map(row => row[0]));
    if (eventOffset + 100 < 212) await page.getByTestId("replay-connection-next").click();
  }
  assert.equal(new Set(eventSeen).size, 212); assert.equal(await page.getByTestId("replay-connection-next").isDisabled(), true);
  await page.getByTestId("replay-connection-prev").click();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-connection-page"]')?.textContent === "101–200 / 212");
  const eventDetails = page.getByTestId("acquisition-replay").locator("details");
  if (!await eventDetails.evaluate(el => el.open)) await eventDetails.locator("summary").click();
  await eventDetails.getByRole("button", { name: "取消全选", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid="replay-page"]')?.textContent === "0 条");
  assert.match(await page.getByTestId("replay-connection-current").innerText(), /身份一致，等待有效回答/);
  assert.match(await page.getByTestId("replay-connection-page").innerText(), /101–200/);
  await seek(2145); await page.getByTestId("replay-connection-current").filter({ hasText: "已收到有效回答，通信恢复" }).waitFor();
  await open(files[6]); await page.getByTestId("replay-summary").filter({ hasText: "0 条 · 2 个通信事件" }).waitFor();
  await page.getByTestId("replay-events-only").waitFor();
  await page.getByTestId("replay-connection-current").filter({ hasText: "通信中断" }).waitFor();
  assert.equal(await page.getByTestId("replay-connection-rows").locator("tr").count(), 2);
  assert.equal(await page.getByTestId("replay-rows").locator("tr").count(), 0);
  assert.equal(await page.locator('[data-testid="acquisition-replay"] svg').count(), 0);
  assert.equal(await page.getByTestId("replay-play").isEnabled(), true);
  await page.getByTestId("replay-play").click();
  await page.getByTestId("replay-connection-current").filter({ hasText: "恢复终止" }).waitFor();
  assert.match(await page.getByTestId("replay-connection-current").innerText(), /recovery-close-failed/);
  assert.match(await page.getByTestId("replay-connection-rows").innerText(), /0\.100 s.*0\.240 s/s);
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelectorAll("main, .main, .content").forEach(el => el.scrollTop = 0); });
  await page.screenshot({ path: path.join(output, "events-only-replay.png"), fullPage: true });
  const badEvents = path.join(temp, "SYNTHETIC-bad-events.json");
  await fs.writeFile(badEvents, JSON.stringify({ ...JSON.parse(await fs.readFile(files[6], "utf8")), connectionEventCount: 3 }));
  await open(badEvents); await page.getByTestId("replay-error").filter({ hasText: "通信事件数量、来源或顺序不一致" }).waitFor();
  assert.match(await page.getByTestId("replay-summary").innerText(), /0 条 · 2 个通信事件/);
  await page.evaluate(() => { window.scrollTo(0, 0); document.querySelectorAll("main, .main, .content").forEach(el => el.scrollTop = 0); });
  await page.screenshot({ path: path.join(output, "invalid-events-previous-retained.png"), fullPage: true });
  await open(files[7]); await page.getByTestId("replay-summary").filter({ hasText: "0 条 · 3 个通信事件" }).waitFor();
  await seek(20); await page.getByTestId("replay-connection-current").filter({ hasText: "本批结束时恢复结果未确认" }).waitFor();
  assert.doesNotMatch(await page.getByTestId("replay-connection-current").innerText(), /已收到有效回答/);
  await open(files[2]); await page.getByTestId("replay-summary").filter({ hasText: "100001 条" }).waitFor();
  assert.equal(await page.getByTestId("replay-connections").count(), 0);
  await seek(100000); await page.getByTestId("replay-series").filter({ hasText: "CAN 扩展 18DAF110 · 00112233" }).waitFor();
  assert.equal(await page.locator('[data-testid="acquisition-replay"] svg').count(), 0);
  assert.match(await page.getByTestId("replay-rows").innerText(), /1700000000000000 µs/);
  await page.getByRole("button", { name: "关闭回放", exact: true }).click(); assert.equal(await page.getByTestId("replay-summary").count(), 0);
  assert.deepEqual(await Promise.all(files.map(async file => createHash("sha256").update(await fs.readFile(file)).digest("hex"))), hashes);
  assert.deepEqual(errors, []); passed = true;
  const result = { ok: true, moduleRoot: root, noHardware: true, vehicleIO: false, standardRowsPaged: seen.length,
    manufacturerEnumAndFailure: true, internalFrames: 100001, gapAndTimestampsPreserved: true,
    playPauseSeekSelection: true, replaySelection35: true, extremeFiniteValues: true, nativeOwnerDialog: true, inputsUnmodified: true, startup, errors,
    connectionEventsPaged: eventSeen.length, eventsIndependentOfSelectedValues: true, eventOnlyFailurePlayback: true, invalidEventsPreservePreviousReplay: true,
    unconfirmedRecoveryExplicit: true };
  await fs.writeFile(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result));
} finally {
  clearTimeout(watchdog); if (app) await app.close(); if (passed) await fs.rm(temp, { recursive: true, force: true });
}
