import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createObdConnectionManager, validateConnectionRequest } from './obd-connection.mjs';
import { ELM_SERIAL_BAUDS, ELM_DEFAULT_BAUDS, validSerialBaud } from '../src/obd-connection-logic.mjs';

assert.deepEqual(ELM_SERIAL_BAUDS, [9600, 38400, 115200, 230400]);
for (const value of [...ELM_SERIAL_BAUDS, null]) {
  assert.equal(validSerialBaud(value), true);
  assert.equal(validateConnectionRequest({ action: 'configure-serial', serialBaud: value }), null);
}
for (const value of [undefined, '115200', 10400, true, false, 0, NaN, Infinity, [], {}])
  assert.equal(validateConnectionRequest({ action: 'configure-serial', serialBaud: value }).error, 'serial_baud_invalid');
assert.equal(validateConnectionRequest({ action: 'connect', serialBaud: 9600 }).error, 'forbidden_field');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'flat-six-baud-'));
const state = path.join(temp, 'connection.json');
const usb = 'usb:' + 'a'.repeat(64), bt = 'bt:AABBCCDDEEFF';
const inputs = [], children = [], timers = [];
const devices = [{ id: usb, brand: 'unresolved', available: true, paired: false, transport: 'usb-serial' },
  { id: bt, brand: 'OBDLink MX+', available: true, paired: true, transport: 'bluetooth-spp' },
  { id: 'vnci:123', brand: 'VNCI', available: true, transport: 'd-pdu-usb' }];
function make() {
  return createObdConnectionManager({ repoRoot: process.cwd(), env: { PORSCHE981_CONNECTION_STATE: state },
    listFn: async () => ({ devices }), handshakeMs: 60000, livenessMs: 60000, gracefulMs: 25, killWaitMs: 25,
    setTimeoutFn(fn, ms) { const t = { fn, ms }; timers.push(t); return t; }, clearTimeoutFn(t) { if (t) t.cleared = true; },
    monitorSpawnFn() {
      const c = new EventEmitter(); c.stdin = new PassThrough(); c.stdout = new PassThrough(); c.stderr = new PassThrough();
      c.exitCode = null; c.kill = () => true;
      c.stdin.on('data', data => {
        const req = JSON.parse(String(data)); inputs.push(req);
        if (req.action !== 'stop') return;
        c.stdout.write(JSON.stringify({ type: 'stopped', ok: true, restoration: { errors: [] } }) + '\n');
        queueMicrotask(() => { c.exitCode = 0; c.emit('close', 0); });
      });
      children.push(c); return c;
    } });
}
let manager;
try {
  manager = make(); await manager.handle({ action: 'list' });
  assert.equal((await manager.handle({ action: 'configure-serial', serialBaud: 9600 })).error, 'serial_baud_transport_mismatch');
  await manager.handle({ action: 'select', deviceId: usb });
  assert.equal((await manager.handle({ action: 'configure-serial', serialBaud: 9600 })).error, 'serial_baud_transport_mismatch');
  for (const [model, rate] of Object.entries(ELM_DEFAULT_BAUDS)) {
    await manager.handle({ action: 'select', deviceId: usb, model });
    assert.equal(manager.snapshot().serialBaud, null); assert.equal(manager.snapshot().effectiveSerialBaud, rate);
    const result = await manager.handle({ action: 'configure-serial', serialBaud: 230400 });
    assert.equal(result.ok, true); assert.equal(result.effectiveSerialBaud, 230400);
    assert.equal(children.length, 0, 'configuration must not open a port');
  }
  assert.equal(manager.reserveDispatch(), true);
  for (const req of [{ action: 'configure-serial', serialBaud: 38400 },
    { action: 'configure', purpose: 'internal' }, { action: 'configure-network', host: '127.0.0.1', port: 35000 }])
    assert.equal((await manager.handle(req)).error, 'busy', 'dispatch reservation must block settings');
  manager.releaseDispatch();
  await manager.shutdown(); manager = make(); await manager.handle({ action: 'list' });
  assert.equal(manager.snapshot().selectedDeviceId, null);
  assert.equal(manager.snapshot().model, null); assert.equal(manager.snapshot().serialBaud, null);
  assert.equal(manager.snapshot().purpose, 'diagnostic');
  assert.equal((await manager.handle({ action: 'connect' })).error, 'device_not_selected');
  assert.equal(children.length, 0, 'restarting/listing must not open the remembered device');
  await manager.handle({ action: 'select', deviceId: usb, model: 'OBDLink' });
  await manager.handle({ action: 'configure-serial', serialBaud: 230400 });
  await manager.handle({ action: 'connect' });
  assert.equal(inputs.at(-1).serialBaud, 230400); assert.equal(inputs.at(-1).adapterModel, 'OBDLink');
  assert.equal((await manager.handle({ action: 'configure-serial', serialBaud: 9600 })).error, 'disconnect_before_configure');
  // An unexpected worker close waits; explicit retry uses the same configured rate.
  children.at(-1).stdout.write(JSON.stringify({ type: 'error', ok: false, error: 'port-io', simulation: false }) + '\n');
  children.at(-1).stdout.write(JSON.stringify({ type: 'stopped', ok: true, restoration: { errors: [] } }) + '\n');
  children.at(-1).exitCode = 1; children.at(-1).emit('close', 1);
  assert.equal(manager.snapshot().linkState, 'retry-required');
  assert.equal(timers.some(t => t.ms === 5000 && !t.cleared), false);
  assert.equal(children.length, 1, 'no automatic reconnect');
  await manager.handle({ action: 'connect', deviceId: usb });
  assert.equal(inputs.at(-1).serialBaud, 230400); assert.equal(children.length, 2);
  await manager.handle({ action: 'disconnect' });
  await manager.handle({ action: 'select', deviceId: usb, model: 'OBDLink' });
  assert.equal(manager.snapshot().serialBaud, 230400, 'same selection preserves settings');
  await manager.handle({ action: 'select', deviceId: usb, model: 'ELM327' });
  assert.equal(manager.snapshot().serialBaud, null); assert.equal(manager.snapshot().effectiveSerialBaud, 38400);
  await manager.handle({ action: 'configure-serial', serialBaud: 9600 });
  await manager.handle({ action: 'select', deviceId: bt }); assert.equal(manager.snapshot().serialBaud, null);
  await manager.handle({ action: 'configure-serial', serialBaud: 38400 });
  await manager.handle({ action: 'select', deviceId: 'vnci:123' });
  assert.equal(manager.snapshot().serialBaud, null); assert.equal(manager.snapshot().effectiveSerialBaud, null);
  assert.equal((await manager.handle({ action: 'configure-serial', serialBaud: null })).error, 'serial_baud_transport_mismatch');
  await manager.handle({ action: 'configure-network', host: '127.0.0.1', port: 35000 });
  await manager.handle({ action: 'select', deviceId: 'tcp:127.0.0.1:35000', model: 'ELM327' });
  assert.equal((await manager.handle({ action: 'configure-serial', serialBaud: 9600 })).error, 'serial_baud_transport_mismatch');
  await manager.shutdown();
  fs.writeFileSync(state, JSON.stringify({ deviceId: 'tcp:127.0.0.1:35000', model: 'ELM327', serialBaud: 9600 }));
  manager = make(); assert.equal(manager.snapshot().serialBaud, null);
  console.log(JSON.stringify({ passed: true, transportInjected: true, deviceIO: false, vehicleIO: false,
    checks: ['strict rates', 'all defaults', 'no open on configure', 'trusted worker input', 'busy reservation',
      'same rate on reconnect', 'fresh selection after restart', 'reset on device/model change', 'native/TCP rejection'] }));
} finally { await manager?.shutdown(); fs.rmSync(temp, { recursive: true, force: true }); }
