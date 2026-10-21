/** Real renderer/preload/selector, synthetic source-qualified jobs and fresh states.
 * No OEM file, device or vehicle is enabled by these models. */
import { _electron as electron } from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
const source = path.resolve(import.meta.dirname, '..'), appRoot = process.env.PORSCHE981_ACCEPT_APP_ROOT && path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT);
const root = appRoot || source, output = path.resolve(process.env.PORSCHE981_ACCEPT_OUTPUT || path.join(source, '.local/obd-firmware-selection-20261015/source-ui'));
await fs.mkdir(output, { recursive: true }); const temp = await fs.mkdtemp(path.join(output, 'isolated-'));
const fixture = path.join(temp, 'devices.json'), file = path.join(temp, 'model.bin'), bytes = Buffer.from('SYNTHETIC UI FIRMWARE MODEL BYTES');
await fs.writeFile(fixture, JSON.stringify({ devices: [], errors: [] })); await fs.writeFile(file, bytes);
const env = { ...process.env, PORSCHE981_HEADLESS: '1', PORSCHE981_OBD_SMOKE: '1', PORSCHE981_SESSION_DENY_LIVE: '1',
  PORSCHE981_USER_DATA: path.join(temp, 'user-data'), PORSCHE981_DB: path.join(temp, 'ui.db'), PORSCHE981_LOCAL_ROOT: path.join(temp, 'assets'),
  PORSCHE981_CONNECTION_FIXTURE: fixture, PORSCHE981_CONNECTION_STATE: path.join(temp, 'connection.json') };
delete env.ELECTRON_RUN_AS_NODE; delete env.VITE_DEV_SERVER_URL;
let app, passed = false; const checks = [], errors = [];
const watchdog = setTimeout(() => { console.error('firmware selection UI timeout'); void app?.close(); }, 90000);
try {
  app = await electron.launch({ args: [appRoot || path.join(source, 'apps/desktop')], env, timeout: 30000 });
  const page = await app.firstWindow(); page.setDefaultTimeout(12000); page.on('pageerror', error => errors.push(error.message));
  await page.locator('nav.side [data-tab="obd"]').click(); await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click(); await page.locator('[data-coding-category="programming"]').click();
  const openDme = () => page.locator('[data-coding-function="workshop-program-dme"]').click(); await openDme();
  await page.getByTestId('firmware-version-unfinished').waitFor();
  assert.equal(await page.getByTestId('firmware-target-version').count(), 0);
  assert.equal(await page.getByRole('button', { name: '确认刷写(未完成)', exact: true }).isDisabled(), true);
  const production = await page.evaluate(async () => [await window.porsche981.firmwareVersions({ action: 'list', ecu: 'dme' }),
    await window.porsche981.firmwareVersions({ action: 'list', ecu: 'dme', jobs: [] }),
    await window.porsche981.firmwareVersions({ action: 'confirm', ecu: 'dme' })]);
  assert.equal(production[0].catalogueState, 'source-unavailable'); assert.deepEqual(production[0].versions, []);
  assert.equal(production[1].error, 'firmware-selection-invalid'); assert.equal(production[2].error, 'firmware-selection-invalid');
  checks.push('production lists no unqualified archive version; no renderer job injection or write action is accepted');
  await app.evaluate(async ({ ipcMain }, args) => {
    const vm = process.getBuiltinModule('vm');
    const { createFirmwareVersionSelection } = await vm.runInThisContext(`import(${JSON.stringify(args.module)})`, { importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER });
    const state = globalThis.__firmwareSelection = { reads: 0, changed: false, calls: [] };
    const artifact = { path: args.file, bytes: args.bytes, sha256: args.sha, sourceQualified: true, containerQualified: true };
    state.jobs = [['upgrade', '2', 'upgrade'], ['same', '1', 'reflash'], ['older', '0', 'downgrade']].map(([id, version, operation]) => ({
      id, ecu: 'dme', origin: 'original', sourceQualified: true, sourceHash: 'a'.repeat(64),
      conditions: { qualified: true, generation: '981', productKeys: ['TEST-ONLY'], modelYears: [2014], requiredOptions: ['TEST-PDK'], excludedOptions: [] },
      participants: ['dme', 'pdk'].map(ecu => ({ ecu, label: ecu.toUpperCase(), targetVersion: ecu === 'dme' ? version : '2',
        executionQualified: true, recoveryQualified: true, routes: [{ hardware: ecu + '-hw', software: ecu + '-sw', currentVersion: '1', operation: ecu === 'dme' ? operation : 'upgrade' }], files: [{ ...artifact }] })),
      ...(id === 'upgrade' ? { releaseNotes: { qualified: true, sourceHash: 'b'.repeat(64), text: '本段为测试用的原厂说明模型。' } } : {}),
    }));
    const selector = createFirmwareVersionSelection({ getJobs: () => state.jobs, getVehicleState: async () => ({
      qualified: true, freshRead: true, readId: 'read-' + ++state.reads,
      device: { connected: true, purpose: 'diagnostic', wiredVerified: true, family: 'VNCI', id: 'vnci:1', connectionEpoch: 'epoch-one' },
      vehicle: { generation: '981', vin: 'WP0ZZZ98ZES123456', productKey: 'TEST-ONLY', modelYear: 2014, options: ['TEST-PDK'] },
      units: ['dme', 'pdk'].map(ecu => ({ qualified: true, ecu, hardware: ecu + '-hw', software: state.changed && ecu === 'pdk' ? 'CHANGED' : ecu + '-sw', version: '1' })),
    }) });
    ipcMain.removeHandler('diagnostics:firmwareVersions'); ipcMain.handle('diagnostics:firmwareVersions', (event, request) => {
      state.calls.push(request.action); return selector.handle(request, event.sender.id);
    });
  }, { module: pathToFileURL(path.join(root, 'apps/desktop/electron/diagnostic-preparation.mjs')).href, file, bytes: bytes.length, sha: createHash('sha256').update(bytes).digest('hex') });
  // Remount the same real panel after installing the private source model.
  await page.locator('[data-coding-function="workshop-program-pcm"]').count().then(async count => {
    if (count) await page.locator('[data-coding-function="workshop-program-pcm"]').click();
    else { await page.locator('[data-coding-system="pdk"]').click(); await page.locator('[data-coding-system="dme"]').click(); }
  });
  await openDme();
  const select = () => page.getByTestId('firmware-target-version'); await select().waitFor();
  assert.equal(await select().inputValue(), '');
  assert.deepEqual(await select().locator('option').allTextContents(), ['请选择目标版本', '2', '1', '0']);
  assert.equal(await page.getByTestId('firmware-version-review').count(), 0);
  const choose = async version => { await select().selectOption({ label: version }); await page.getByTestId('firmware-version-review').waitFor(); };
  await choose('2'); await page.getByTestId('firmware-release-notes').locator('summary').click();
  assert.match(await page.getByTestId('firmware-release-notes').innerText(), /原厂说明模型/);
  assert.equal(await page.locator('[data-firmware-review-ecu]').count(), 2);
  assert.match(await page.getByTestId('firmware-version-review').innerText(), /DME：1 → 2/);
  assert.match(await page.getByTestId('firmware-version-review').innerText(), /PDK：1 → 2/);
  checks.push('target is never preselected; only supplied supported versions appear; qualified original notes expand and the full linked range is reviewed in-page');
  await choose('1'); assert.match(await page.getByTestId('firmware-version-review').innerText(), /DME：1 → 1/);
  assert.equal(await page.getByTestId('firmware-release-notes').count(), 0);
  await choose('0'); assert.match(await page.getByTestId('firmware-version-review').innerText(), /DME：1 → 0/);
  assert.equal(await page.getByRole('button', { name: '确认刷写(未完成)', exact: true }).isDisabled(), true);
  checks.push('source-declared same-version and downgrade choices remain available; missing notes are not invented and a review does not authorize execution');
  await app.evaluate(() => { __firmwareSelection.changed = true; });
  await select().selectOption({ label: '2' }); await page.getByTestId('firmware-version-error').filter({ hasText: '版本已变化' }).waitFor();
  assert.equal(await select().inputValue(), ''); assert.equal(await page.getByTestId('firmware-version-review').count(), 0);
  await page.getByRole('button', { name: '重新核对版本', exact: true }).click();
  await page.getByTestId('firmware-version-unfinished').filter({ hasText: '没有核对通过' }).waitFor();
  assert.equal(await select().count(), 0);
  await app.evaluate(() => { __firmwareSelection.changed = false; });
  await page.locator('[data-coding-system="pdk"]').click(); await page.locator('[data-coding-system="dme"]').click(); await openDme(); await select().waitFor();
  await fs.writeFile(file, Buffer.from('X'.repeat(bytes.length)));
  await select().selectOption({ label: '2' }); await page.getByTestId('firmware-version-error').filter({ hasText: '文件已变化' }).waitFor();
  assert.equal(await select().inputValue(), ''); assert.equal(await page.getByTestId('firmware-version-review').count(), 0);
  await fs.writeFile(file, bytes); await page.getByRole('button', { name: '重新核对版本', exact: true }).click(); await select().waitFor();
  checks.push('changed linked identity and changed firmware bytes clear the target/review; explicit retry rebuilds the list without using old results');
  await choose('2');
  await page.locator('[data-coding-system="pdk"]').click(); await page.locator('[data-coding-function="workshop-program-pdk"]').click();
  await page.getByTestId('firmware-version-unfinished').waitFor(); assert.equal(await page.getByTestId('firmware-version-review').count(), 0);
  await page.locator('[data-coding-system="dme"]').click(); await openDme(); await select().waitFor();
  assert.equal(await select().inputValue(), ''); assert.equal(await page.getByTestId('firmware-version-review').count(), 0);
  checks.push('switching units clears target, notes and review; returning requires a new explicit target choice');
  await choose('2'); await page.getByTestId('diagnostic-preparation').scrollIntoViewIfNeeded();
  await page.setViewportSize({ width: 800, height: 900 });
  assert.equal(await page.getByTestId('diagnostic-preparation').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({ path: path.join(output, 'linked-version-review.png'), fullPage: true });
  assert.doesNotMatch(await page.getByTestId('diagnostic-preparation').innerText(), /model.bin|PRIVATE|TEST-ONLY/);
  assert.deepEqual(errors, []); checks.push('narrow layout, private-source redaction and renderer errors checked'); passed = true;
} catch (error) { errors.push(error.stack || String(error)); throw error; }
finally {
  clearTimeout(watchdog); if (app) await app.close().catch(() => {});
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ passed, checks, errors, sourceQualificationSynthetic: true,
    actualOemJobsEnabled: false, deviceIO: false, vehicleIO: false }, null, 2) + '\n');
}
