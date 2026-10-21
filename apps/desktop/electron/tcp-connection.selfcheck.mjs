import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createObdConnectionManager, attachSessionHandoff, validateConnectionRequest } from './obd-connection.mjs';
import { tcpEndpointId, parseTcpDeviceId, validObdDeviceId } from '../src/obd-connection-logic.mjs';

for (const id of ['tcp:192.168.0.10:35000', 'tcp:[2001:db8::1]:2000', 'tcp:[::1]:65535']) {
  assert.equal(validObdDeviceId(id), true);
  const value = parseTcpDeviceId(id); assert.equal(tcpEndpointId(value.host, value.port), id);
}
for (const id of ['tcp:head.local:35000', 'tcp:192.168.00.10:35000', 'tcp:256.1.1.1:35000',
  'tcp:127.0.0.1:0', 'tcp:127.0.0.1:65536', 'tcp:127.0.0.1:035000', 'tcp:[2001:0db8::1]:2000',
  'tcp:[fe80::1%en0]:2000', 'tcp:127.0.0.1:1\n', 'tcp://127.0.0.1:35000']) assert.equal(validObdDeviceId(id), false, id);
assert.equal(tcpEndpointId('2001:DB8::1', 2000), 'tcp:[2001:db8::1]:2000');
for (const request of [{ action: 'configure-network', host: 'head.local', port: 35000 },
  { action: 'configure-network', host: '127.0.0.1', port: '35000' },
  { action: 'configure-network', host: '127.0.0.1', port: 35000, model: 'vLinker' },
  { action: 'connect', deviceId: 'tcp:999.0.0.1:35000' }]) assert.ok(validateConnectionRequest(request));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'flat-six-tcp-connection-'));
const state = path.join(temp, '连接.json'), children = [], inputs = [];
const id = 'tcp:192.168.0.10:35000';
function make() {
  return createObdConnectionManager({ repoRoot: process.cwd(), env: { PORSCHE981_CONNECTION_STATE: state },
    listFn: async () => ({ devices: [] }), handshakeMs: 60000, livenessMs: 60000, gracefulMs: 30, killWaitMs: 30,
    monitorSpawnFn() {
      const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
      child.exitCode = null; child.kill = () => true;
      child.stdin.on('data', bytes => {
        const doc = JSON.parse(String(bytes)); inputs.push(doc);
        if (doc.action !== 'stop') return;
        child.stdout.write(JSON.stringify({ type: 'stopped', ok: true, restoration: { errors: [] } }) + '\n');
        queueMicrotask(() => { child.exitCode = 0; child.emit('close', 0); });
      });
      children.push(child); return child;
    } });
}
let manager;
try {
  manager = make();
  let result = await manager.handle({ action: 'configure-network', host: '192.168.0.10', port: 35000 });
  assert.equal(result.ok, true); assert.equal(result.devices.length, 1); assert.equal(result.devices[0].id, id);
  assert.equal(result.devices[0].transport, 'tcp'); assert.equal(result.devices[0].paired, false);
  assert.equal(result.devices[0].brand, 'unresolved'); assert.equal(children.length, 0, 'saving/listing must not open a socket');
  await manager.handle({ action: 'list' }); assert.equal(children.length, 0);
  assert.equal((await manager.handle({ action: 'select', deviceId: id, model: 'VNCI' })).error, 'adapter_model_mismatch');
  await manager.handle({ action: 'select', deviceId: id });
  assert.equal((await manager.handle({ action: 'connect' })).error, 'device_model_required'); assert.equal(children.length, 0);
  await manager.handle({ action: 'select', deviceId: id, model: 'vLinker' });
  assert.equal((await manager.handle({ action: 'connect' })).ok, true);
  assert.equal(children.length, 1); assert.equal(inputs[0].deviceId, id); assert.equal(inputs[0].adapterModel, 'vLinker');
  children[0].stdout.write(JSON.stringify({ ok: true, type: 'handshake', simulation: false, deviceId: id,
    voltageSource: 'atrv', volts: 12.6, at: Date.now() }) + '\n');
  assert.equal(manager.snapshot().connected, true);
  assert.equal((await manager.handle({ action: 'configure-network', host: null, port: null })).error, 'disconnect_before_configure');
  let dispatched = 0;
  const sessions = { handle: async () => { dispatched++; return {}; }, cancelActiveAndWait: async () => {} };
  attachSessionHandoff(manager, sessions, { spawnFn() {}, allowInjectedLive: true, env: {} });
  const denied = await sessions.handle({ action: 'start', profileId: 'porsche-981-2014-dme', mode: 'live',
    sessionTask: 'clear', confirmedClearDtc: true, x431Inactive: true });
  assert.equal(denied.error, 'adapter-clear-not-qualified'); assert.equal(dispatched, 0); assert.equal(children.length, 1);
  assert.equal((await manager.suspendForSession()).ok, true); assert.equal(children[0].exitCode, 0);
  await manager.shutdown(); manager = make();
  result = await manager.handle({ action: 'list' });
  assert.deepEqual(result.networkEndpoint, { host: '192.168.0.10', port: 35000 });
  assert.equal(result.selectedDeviceId, null); assert.equal(result.model, null);
  assert.equal(result.purpose, 'diagnostic');
  assert.equal((await manager.handle({ action: 'connect' })).error, 'device_not_selected');
  await manager.handle({ action: 'configure', purpose: 'internal', canNetwork: 'drive' });
  await manager.handle({ action: 'select', deviceId: id, model: 'OBDLink MX+' });
  assert.equal((await manager.handle({ action: 'connect' })).error, 'internal_profile_not_qualified');
  assert.equal(children.length, 1, 'TCP cannot inherit MX+ internal qualification');
  result = await manager.handle({ action: 'configure-network', host: null, port: null });
  assert.equal(result.ok, true); assert.equal(result.selectedDeviceId, null); assert.equal(result.devices.length, 0);
  assert.equal(result.networkEndpoint, null);
  console.log(JSON.stringify({ passed: true, transport: 'injected worker only', deviceIO: false, vehicleIO: false,
    checks: ['canonical endpoint', 'configuration/list never opens', 'model required', 'trusted worker input',
      'release before handoff', 'endpoint retained without selection', 'clear denied before handoff', 'internal denied before open', 'remove endpoint'] }));
} finally { await manager?.shutdown(); fs.rmSync(temp, { recursive: true, force: true }); }
