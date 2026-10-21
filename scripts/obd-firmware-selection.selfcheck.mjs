/** Production selector; explicitly synthetic source qualifications and fresh states.
 * Files are isolated model bytes, never an OEM firmware qualification. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
const root = path.resolve(process.env.PORSCHE981_ACCEPT_APP_ROOT || path.join(import.meta.dirname, '..'));
const { createFirmwareVersionSelection } = await import(pathToFileURL(path.join(root, 'apps/desktop/electron/diagnostic-preparation.mjs')));
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'flat-six-firmware-selection-'));
const bytes = Buffer.from('SYNTHETIC TEST BYTES, NOT OEM FIRMWARE');
const file = path.join(temp, 'model.bin'); await fs.writeFile(file, bytes);
const artifact = { path: file, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'), sourceQualified: true, containerQualified: true };
const state = () => ({ qualified: true, freshRead: true, readId: 'fresh',
  device: { connected: true, purpose: 'diagnostic', wiredVerified: true, family: 'VNCI', id: 'vnci:1', connectionEpoch: 'epoch-one' },
  vehicle: { generation: '981', vin: 'WP0ZZZ98ZES123456', productKey: 'TEST-ONLY', modelYear: 2014, options: ['TEST-PDK'] },
  units: ['dme', 'pdk'].map(ecu => ({ qualified: true, ecu, hardware: ecu + '-hw', software: ecu + '-sw', version: '1' })) });
const job = (id = 'upgrade', version = '2', operation = 'upgrade') => ({ id, ecu: 'dme', origin: 'original', sourceQualified: true, sourceHash: 'a'.repeat(64),
  conditions: { qualified: true, generation: '981', productKeys: ['TEST-ONLY'], modelYears: [2014], requiredOptions: ['TEST-PDK'], excludedOptions: ['TEST-EXCLUDED'] },
  participants: ['dme', 'pdk'].map(ecu => ({ ecu, label: ecu.toUpperCase(), targetVersion: ecu === 'dme' ? version : '2', executionQualified: true, recoveryQualified: true,
    routes: [{ hardware: ecu + '-hw', software: ecu + '-sw', currentVersion: '1', operation: ecu === 'dme' ? operation : 'upgrade' }], files: [{ ...artifact }] })),
  releaseNotes: { qualified: true, sourceHash: 'b'.repeat(64), text: '合成原厂说明模型' } });
function setup(jobs = [job()]) {
  const model = { state: state(), jobs, reads: 0, busy: false };
  const service = createFirmwareVersionSelection({ getJobs: () => model.jobs,
    getVehicleState: async () => ({ ...structuredClone(model.state), readId: 'read-' + ++model.reads }), isVehicleBusy: () => model.busy });
  return { model, service };
}
const list = (service, owner = 101) => service.handle({ action: 'list', ecu: 'dme' }, owner);
const review = (service, listing, owner = 101) => service.handle({ action: 'review', ecu: 'dme', listId: listing.listId, targetId: listing.versions[0].id }, owner);
const checks = [];
try {
  let reads = 0; const defaultService = createFirmwareVersionSelection({ getVehicleState: () => { reads++; throw new Error('must not read'); } });
  assert.deepEqual((await list(defaultService)).versions, []); assert.equal(reads, 0);
  for (const request of [{ action: 'list', ecu: 'dme', path: file }, { action: 'list', ecu: 'dme', jobs: [job()] }, { action: 'confirm', ecu: 'dme' }])
    assert.equal((await defaultService.handle(request, 101)).error, 'firmware-selection-invalid');
  checks.push('empty production registry performs no vehicle read; renderer cannot supply files/jobs or invoke a write');
  for (const mutate of [
    j => { j.sourceQualified = false; }, j => { j.origin = 'third-party'; }, j => { j.conditions.operator = 'NOT'; },
    j => { j.participants[1].recoveryQualified = false; }, j => { j.participants[1].executionQualified = false; },
    j => { j.participants[1].files[0].containerQualified = false; }, j => { j.participants[0].routes[0].excludeHardware = 'OTHER'; },
    j => { j.participants[0].routes[0].operation = 'downgrade-guess'; },
  ]) {
    const j = job(); mutate(j); const { service, model } = setup([j]); assert.deepEqual((await list(service)).versions, []); assert.equal(model.reads, 0);
  }
  checks.push('unknown qualifiers, unsupported operation meanings, incomplete linked execution/recovery and unqualified files cannot enter the list');
  for (const mutate of [
    s => { s.vehicle.productKey = 'OTHER'; }, s => { s.vehicle.modelYear = 2015; }, s => { s.vehicle.options = []; },
    s => { s.vehicle.options.push('TEST-EXCLUDED'); }, s => { s.units.pop(); }, s => { s.units[1].software = 'OTHER'; },
    s => { s.vehicle.generation = '982'; },
  ]) {
    const { service, model } = setup(); mutate(model.state); assert.deepEqual((await list(service)).versions, []);
  }
  const { service, model } = setup([job(), job('reflash', '1', 'reflash'), job('downgrade', '0', 'downgrade')]);
  const listing = await list(service); assert.deepEqual(listing.versions.map(item => item.version), ['2', '1', '0']);
  assert.equal(Object.hasOwn(listing, 'targetId'), false); assert.doesNotMatch(JSON.stringify(listing), /model.bin|TEST-ONLY|wp0/i);
  const checked = await review(service, listing); assert.equal(checked.rows.length, 2); assert.equal(checked.executionEnabled, false);
  assert.deepEqual(checked.rows.map(row => row.ecu), ['dme', 'pdk']); assert.equal(model.reads, 2);
  assert.equal((await review(service, listing, 102)).error, 'firmware-selection-stale');
  checks.push('exact vehicle/options and every linked identity match; source-declared upgrade/reflash/downgrade remain unselected and review the complete range with a new read');
  for (const mutate of [
    s => { s.device.connectionEpoch = 'changed'; }, s => { s.device.id = 'vnci:2'; }, s => { s.vehicle.vin = 'WP0ZZZ98ZES123457'; },
    s => { s.units[0].hardware = 'OTHER'; }, s => { s.units[1].version = 'CHANGED'; }, s => { s.vehicle.options.push('OTHER'); },
  ]) {
    const { service, model } = setup(); const l = await list(service); mutate(model.state);
    assert.equal((await review(service, l)).error, 'firmware-current-changed'); assert.equal((await review(service, l)).error, 'firmware-selection-stale');
  }
  for (const mutate of [s => { s.freshRead = false; }, s => { s.device.wiredVerified = false; }, s => { s.device.family = 'vLinker'; }, s => { s.units[1].qualified = false; }]) {
    const { service, model } = setup(); mutate(model.state); assert.equal((await list(service)).error, 'firmware-current-unavailable');
  }
  checks.push('changed vehicle/head/epoch/software invalidates selection; missing fresh identity, wired proof or participant qualification blocks review');
  {
    const { service } = setup(); const l = await list(service); await fs.writeFile(file, Buffer.from('X'.repeat(bytes.length)));
    assert.equal((await review(service, l)).error, 'firmware-file-changed'); assert.deepEqual((await list(service)).versions, []);
    await fs.writeFile(file, bytes); const l2 = await list(service); await fs.unlink(file);
    assert.equal((await review(service, l2)).error, 'firmware-file-changed'); await fs.writeFile(file, bytes);
    const { service: ambiguous } = setup([job('one'), job('two')]); assert.deepEqual((await list(ambiguous)).versions, []);
    const { service: duplicate } = setup([job('one'), job('one', '3')]); assert.deepEqual((await list(duplicate)).versions, []);
  }
  checks.push('file size/hash and existence are checked again; ambiguous version labels or duplicate source jobs are not guessed');
  {
    const { service, model } = setup(); const l = await list(service); model.jobs[0].sourceHash = 'c'.repeat(64);
    assert.equal((await review(service, l)).error, 'firmware-source-changed');
    const cached = createFirmwareVersionSelection({ getJobs: () => [job()], getVehicleState: async () => state() });
    const c = await list(cached); assert.equal((await review(cached, c)).error, 'firmware-current-unavailable');
  }
  {
    const { service, model } = setup(); model.busy = true; assert.equal((await list(service)).error, 'vehicle-busy'); assert.equal(model.reads, 0);
    model.busy = false; const l = await list(service); model.busy = true; assert.equal((await review(service, l)).error, 'vehicle-busy');
    model.busy = false; await service.handle({ action: 'list', ecu: 'pdk' }, 101); assert.equal((await review(service, l)).error, 'firmware-selection-stale');
    const l2 = await list(service); await service.handle({ action: 'clear', listId: l.listId }, 101); assert.equal((await review(service, l2)).ok, true);
    service.clearOwned(101); assert.equal((await review(service, l2)).error, 'firmware-selection-stale');
    let resolve; const pending = new Promise(done => { resolve = done; }); let reads = 0;
    const concurrent = createFirmwareVersionSelection({ getJobs: () => [job()], getVehicleState: async () => {
      if (!reads++) await pending; return { ...state(), readId: String(reads) }; } });
    const old = list(concurrent); await new Promise(done => setTimeout(done, 1));
    const fresh = await list(concurrent); resolve(); assert.equal((await old).error, 'firmware-selection-stale'); assert.equal((await review(concurrent, fresh)).ok, true);
    let finishReview; const held = new Promise(done => { finishReview = done; }); let count = 0;
    const overlapping = createFirmwareVersionSelection({ getJobs: () => [job()], getVehicleState: async () => {
      const n = ++count; if (n === 2) await held;
      const current = state(); if (n === 2) current.units[0].version = 'old-pending-result';
      return { ...current, readId: String(n) }; } });
    const first = await list(overlapping), oldReview = review(overlapping, first);
    await new Promise(done => setTimeout(done, 1)); const current = await list(overlapping);
    finishReview(); assert.equal((await oldReview).error, 'firmware-selection-stale');
    assert.equal((await review(overlapping, current)).ok, true, 'an obsolete review cannot delete a newer listing');
  }
  checks.push('busy tasks prevent vehicle reads; owner/unit changes and stale asynchronous results cannot resurrect a previous choice');
  {
    const { service, model } = setup(); const l = await list(service), reviewed = await review(service, l);
    const plan = service.takeReviewed({ ecu: 'dme', listId: l.listId, targetId: l.versions[0].id, reviewId: reviewed.reviewId }, 101);
    let count = 0; const fresh = () => ({ ...structuredClone(model.state), readId: 'result-' + ++count });
    assert.deepEqual(plan.inspectResult(fresh()).rows.map(row => row.actualVersion), ['1', '1']);
    for (const mutate of [s => { s.vehicle.vin = 'WP0ZZZ98ZES123457'; }, s => { s.device.id = 'vnci:2'; },
      s => { s.device.connectionEpoch = 'new-epoch'; }, s => { s.vehicle.options.push('CHANGED'); }]) {
      const state = fresh(); mutate(state);
      const result = plan.inspectResult(state); assert.equal(result.ok, false);
      assert.ok(result.rows.every(row => row.actualVersion === null), 'unbound vehicle/head state must not publish actual versions');
    }
    const changedHardware = fresh(); changedHardware.units[0].hardware = 'replacement-hw';
    assert.deepEqual(plan.inspectResult(changedHardware).rows.map(row => row.actualVersion), [null, '1']);
    const repeat = fresh(); plan.inspectResult(repeat);
    assert.ok(plan.inspectResult(repeat).rows.every(row => row.actualVersion === null), 'a repeated read ID is stale, including its displayed values');
  }
  checks.push('actual result versions require fresh replies bound to the reviewed vehicle, head, connection and unit hardware');
  console.log(JSON.stringify({ passed: true, checks, syntheticQualification: true, actualOemJobsEnabled: false, deviceIO: false, vehicleIO: false }));
} finally { await fs.rm(temp, { recursive: true, force: true }); }
