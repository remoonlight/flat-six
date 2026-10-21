/** Real selector + executor + transport gate. Source jobs, client operations and
 * vehicle state are synthetic; model bytes are not qualified OEM firmware. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, '..'));
const load = name => import(pathToFileURL(path.join(root, 'apps/desktop/electron', name)));
const { createFirmwareVersionSelection } = await load('diagnostic-preparation.mjs');
const { createFirmwareExecution } = await load('piwis-workshop.mjs');
const { createTransportGate } = await load('obd-connection.mjs');
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'flat-six-oem-flow-'));
const file = path.join(temp, 'model.bin'), bytes = Buffer.from('SYNTHETIC ORIGINAL JOB MODEL'); await fs.writeFile(file, bytes);
const artifact = { path: file, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), sourceQualified: true, containerQualified: true };
const job = () => ({ id: 'linked-source-model', ecu: 'dme', origin: 'original', sourceQualified: true, sourceHash: 'a'.repeat(64),
  conditions: { qualified: true, generation: '981', productKeys: ['TEST'], modelYears: [2014], requiredOptions: [], excludedOptions: [] },
  participants: ['dme', 'pdk'].map(ecu => ({ ecu, label: ecu.toUpperCase(), targetVersion: '2', targetSoftware: ecu + '-target', executionQualified: true, recoveryQualified: true,
    routes: [{ hardware: ecu + '-hw', software: ecu + '-old', currentVersion: '1', operation: 'upgrade' }], files: [{ ...artifact }] })) });
const pause = () => new Promise(done => setTimeout(done, 1));
async function until(executor, phase) { for (let i = 0; i < 1000; i++) { const state = await executor.handle({ action: 'status' }, 101); if (state.job?.phase === phase) return state.job; await pause(); } throw new Error('phase not reached: ' + phase); }
const defer = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve: () => resolve() }; };
function setup(mode = 'success') {
  const model = { jobs: [job()], reads: 0, events: [], released: true, connected: true, reserved: false, held: defer(), sourceEnabled: true,
    current: ['dme', 'pdk'].map(ecu => ({ qualified: true, ecu, hardware: ecu + '-hw', software: ecu + '-old', version: '1' })) };
  const fresh = async () => { model.events.push('read'); return { qualified: true, freshRead: true, readId: 'read-' + ++model.reads,
    device: { connected: true, purpose: 'diagnostic', wiredVerified: true, family: 'VNCI', id: 'vnci:1', connectionEpoch: 'epoch' },
    vehicle: { generation: '981', vin: 'WP0ZZZ98ZES123456', productKey: 'TEST', modelYear: 2014, options: [] }, units: structuredClone(model.current) }; };
  const selection = createFirmwareVersionSelection({ getJobs: () => model.jobs, getVehicleState: fresh });
  const gate = createTransportGate();
  const connection = { snapshot: () => ({ purpose: 'diagnostic', connected: model.connected, selectedDeviceId: 'vnci:1', model: 'VNCI' }),
    reserveDispatch: () => { if (model.reserved) return false; model.reserved = true; return true; }, releaseDispatch: () => { model.reserved = false; },
    suspendForSession: async () => { model.connected = false; return { ok: true }; },
    resumeAfterSession: async () => { model.connected = true; model.reserved = false; return { ok: true }; } };
  const software = (ecu, run, safeStopAfter = false) => ({ id: ecu, label: '刷写 ' + ecu.toUpperCase(), kind: 'software', ecu, phase: 'flash', safeStopBefore: false, safeStopAfter, execute: run });
  const client = { sourceQualified: true, sourceHash: 'a'.repeat(64),
    open: async () => { model.events.push('open'); return { qualified: true, payloadsVerified: mode !== 'unpinned', context: {} }; },
    preflight: async () => { model.events.push('preflight'); return { ok: true }; }, readState: fresh,
    close: async () => { model.events.push('close'); return { released: model.released }; }, observeRelease: async () => model.released,
    stop: async () => { model.events.push('safe-stop'); return { ok: true }; },
    steps: [software('dme', async () => {
      model.events.push('write-dme');
      if (['throw', 'readback-throws', 'readback-foreign'].includes(mode)) throw new Error('PRIVATE/driver/unknown-write');
      if (mode === 'fail') return { ok: false, cause: '原厂步骤返回失败', remedy: '按原厂恢复方案核对。' };
      model.current[0].software = 'dme-target'; model.current[0].version = '2';
      if (mode === 'failed-but-version-matches') return { ok: false, cause: '原厂步骤返回失败', remedy: '核对完整原厂作业。' };
      if (['safe', 'no-safe'].includes(mode)) await model.held.promise;
      return { ok: true };
    }, mode === 'safe'), software('pdk', async () => {
      model.events.push('write-pdk'); model.current[1].software = 'pdk-target'; model.current[1].version = '2';
      if (mode === 'later-affects-earlier') model.current[0].version = '3';
      return { ok: true };
    })] };
  if (mode === 'manual') client.steps.splice(1, 0, { id: 'human', label: '完成原厂要求的人工操作', kind: 'manual', phase: 'prepare', safeStopBefore: false, safeStopAfter: false });
  if (['readback-throws', 'readback-foreign', 'final-read-throws'].includes(mode)) client.readState = async () => {
    if (model.events.some(event => event.startsWith('write-'))) {
      if (mode !== 'readback-foreign') { model.events.push('failed-read'); throw new Error('PRIVATE/driver/readback-failed'); }
      const state = await fresh(); state.vehicle.vin = 'WP0ZZZ98ZES123457'; return state;
    }
    return fresh();
  };
  const executor = createFirmwareExecution({ selection, connection, gate, getClient: () => model.sourceEnabled ? client : null });
  return { model, client, selection, executor, gate };
}
async function selected(fixture) {
  const listed = await fixture.selection.handle({ action: 'list', ecu: 'dme' }, 101); assert.equal(listed.versions.length, 1);
  const request = { action: 'review', ecu: 'dme', listId: listed.listId, targetId: listed.versions[0].id };
  const reviewed = await fixture.selection.handle(request, 101); assert.equal(reviewed.ok, true);
  return { ...request, action: 'start', reviewId: reviewed.reviewId };
}
const checks = [];
try {
  {
    const executor = createFirmwareExecution(); assert.equal((await executor.handle({ action: 'start', ecu: 'dme', listId: 'x', targetId: 'x', reviewId: 'x' }, 101)).error, 'firmware-selection-stale');
    assert.equal((await executor.handle({ action: 'start', ecu: 'dme', steps: [] }, 101)).error, 'firmware-execution-invalid');
    const f = setup(), request = await selected(f); f.model.sourceEnabled = false;
    assert.equal((await f.executor.handle(request, 101)).error, 'firmware-client-unavailable'); assert.equal(f.model.events.includes('open'), false);
  }
  checks.push('no production source client, no dispatch; renderer steps and unreviewed jobs are rejected');
  {
    const f = setup(), request = await selected(f); const started = await f.executor.handle(request, 101);
    const done = await until(f.executor, 'completed'); assert.equal(done.released, true);
    assert.deepEqual(done.units.map(unit => [unit.originalVersion, unit.targetVersion, unit.actualVersion, unit.status]), [['1', '2', '2', 'completed'], ['1', '2', '2', 'completed']]);
    assert.deepEqual(f.model.events, ['read', 'read', 'open', 'read', 'preflight', 'write-dme', 'write-pdk', 'read', 'close']);
    assert.equal(f.gate.owner(), null); assert.equal(f.model.reserved, false);
    assert.equal((await f.executor.handle(request, 101)).error, 'firmware-selection-stale', 'a reviewed target cannot be replayed');
    assert.equal((await f.executor.handle({ action: 'dismiss', jobId: started.job.id }, 102)).error, 'workshop-owner-mismatch');
  }
  checks.push('leased source preflight precedes all writes; original order is preserved; final full-scope read supplies actual versions; review is single-use');
  for (const mode of ['missing-target', 'missing-step', 'changed-source', 'changed-file', 'missing-file', 'unpinned', 'unknown-client', 'unknown-step']) {
    const f = setup(mode), request = await selected(f);
    if (mode === 'missing-target') delete f.model.jobs[0].participants[1].targetSoftware;
    if (mode === 'missing-step') f.client.steps.pop();
    if (mode === 'unknown-client') f.client.uninterpretedCondition = 'NOT';
    if (mode === 'unknown-step') f.client.steps[0].uninterpretedCondition = 'NOT';
    if (mode === 'changed-source') f.model.jobs[0].sourceHash = 'b'.repeat(64);
    if (mode === 'changed-file') await fs.writeFile(file, Buffer.from('X'.repeat(bytes.length)));
    if (mode === 'missing-file') await fs.unlink(file);
    const response = await f.executor.handle(request, 101);
    if (response.ok) await until(f.executor, 'failed');
    assert.equal(f.model.events.some(event => event.startsWith('write-')), false);
    await fs.writeFile(file, bytes);
  }
  checks.push('missing linked execution/target identity, changed source/file and unpinned payloads stop the entire job before its first write');
  for (const mode of ['throw', 'fail', 'later-affects-earlier']) {
    const f = setup(mode), request = await selected(f); await f.executor.handle(request, 101); const failed = await until(f.executor, 'failed');
    assert.equal(f.model.events.filter(event => event === 'write-dme').length, 1);
    if (mode === 'later-affects-earlier') {
      assert.deepEqual(failed.units.map(unit => [unit.actualVersion, unit.status]), [['3', 'unconfirmed'], ['2', 'completed']]);
    } else {
      assert.equal(failed.units[0].actualVersion, '1'); assert.equal(failed.units[1].status, 'unexecuted');
      assert.equal(f.model.events.includes('write-pdk'), false);
      assert.deepEqual(f.model.events.slice(-2), ['read', 'close'], 'failed writes get one read-only reconciliation before release');
    }
    assert.doesNotMatch(JSON.stringify(failed), /PRIVATE|driver/); assert.equal(failed.resumeSupported, false);
  }
  checks.push('unknown/failed writes are never replayed; final read detects a later ECU action changing an earlier version; missing reads stay unknown');
  for (const mode of ['readback-throws', 'readback-foreign', 'final-read-throws', 'failed-but-version-matches']) {
    const f = setup(mode), request = await selected(f); await f.executor.handle(request, 101); const failed = await until(f.executor, 'failed');
    assert.equal(failed.released, true); assert.equal(f.gate.owner(), null);
    assert.equal(f.model.events.filter(event => event === 'write-dme').length, 1);
    if (mode === 'failed-but-version-matches') {
      assert.equal(failed.units[0].actualVersion, '2'); assert.equal(failed.units[0].status, 'failed', 'a matching version cannot erase a source failure');
    } else assert.ok(failed.units.every(unit => unit.actualVersion === null));
    assert.equal(f.model.events.filter(event => event === 'failed-read').length, mode.includes('throws') ? 1 : 0);
    assert.equal(f.model.events.filter(event => event === 'close').length, 1);
    assert.doesNotMatch(JSON.stringify(failed), /PRIVATE|driver/);
  }
  checks.push('failed jobs retain bound actual versions when available, reject foreign/stale reads, never promote a failed step to success and release after a single failed read');
  for (const mode of ['safe', 'no-safe']) {
    const f = setup(mode), request = await selected(f); const started = await f.executor.handle(request, 101); await until(f.executor, 'running');
    await f.executor.handle({ action: 'cancel', jobId: started.job.id }, 101); assert.equal(f.executor.isIdle(), false);
    assert.equal(f.model.events.includes('safe-stop'), false); f.model.held.resolve();
    const done = await until(f.executor, mode === 'safe' ? 'cancelled' : 'completed');
    assert.equal(done.cancelNotApplied, mode === 'no-safe'); assert.equal(f.model.events.includes('write-pdk'), mode === 'no-safe');
    if (mode === 'safe') assert.deepEqual(done.units.map(unit => [unit.actualVersion, unit.status]), [['2', 'completed'], ['1', 'unexecuted']]);
  }
  checks.push('cancel waits for a source-declared safe boundary; no safe stop completes the job and explains that cancellation could not take effect');
  {
    const f = setup('manual'), request = await selected(f); f.model.released = false;
    const started = await f.executor.handle(request, 101); await until(f.executor, 'waiting-manual');
    assert.equal(f.model.events.includes('write-pdk'), false);
    assert.equal((await f.executor.handle({ action: 'continue', jobId: started.job.id, stepId: 'human' }, 102)).error, 'workshop-owner-mismatch');
    await f.executor.handle({ action: 'continue', jobId: started.job.id, stepId: 'human' }, 101); await until(f.executor, 'release-pending');
    assert.equal(f.executor.isIdle(), false); assert.equal(f.gate.tryAcquire('other'), false);
    assert.equal((await f.executor.handle({ action: 'dismiss', jobId: started.job.id }, 101)).error, 'vehicle-busy');
    f.model.released = true; await until(f.executor, 'completed'); assert.equal(f.gate.owner(), null);
    assert.equal(f.model.events.filter(event => event === 'close').length, 1);
  }
  checks.push('manual steps are owner/job/step-bound; results retain the transport lease until physical release is confirmed without replaying close or writes');
  console.log(JSON.stringify({ passed: true, checks, sourceCallbacksSynthetic: true, productionClientEnabled: false, deviceIO: false, vehicleIO: false }));
} finally { await fs.rm(temp, { recursive: true, force: true }); }
