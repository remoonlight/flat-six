import test from 'node:test';
import assert from 'node:assert/strict';
import { AT_HANDSHAKE } from '@porsche981/domain';
import { ProductionService, createMockProduction, memoryPersist } from './production.mjs';
import { CommandQueue, pci, MOCK_ADAPTERS } from './queue.mjs';
import { PowerShellSerialTransport, SERIAL_HOST_PS } from './powershell-serial.mjs';
import { spawn } from 'node:child_process';
function fixture(overrides = {}, persist = memoryPersist()) {
    let time = Date.now();
    const writes = [];
    const defaults = { ATRV: '12.5V\r>', '0100': pci([0x41, 0, 0xbe, 0x1f, 0xa8, 0x13]), '010C': pci([0x41, 0xc, 0, 0]),
        '03': pci([0x43, 0]), '07': pci([0x47, 0]), '0A': pci([0x4a, 0]), '04': pci([0x44]),
        '0902': pci([0x49, 2, 1, ...Buffer.from('WP0ZZZ98ZES000001')]),
        '0904': pci([0x49, 4, 1, ...Buffer.from('CAL-A'.padEnd(16))]), '0906': pci([0x49, 6, 1, 1, 2, 3, 4]) };
    const svc = new ProductionService({ now: () => time, persist, listAdapters: async () => MOCK_ADAPTERS,
        transportFactory: () => ({ close() { }, async request(cmd) { writes.push(cmd); if (cmd in overrides) {
                const x = overrides[cmd];
                return typeof x === 'function' ? x() : x;
            } if (cmd === 'ATZ')
                return 'ELM327 v2.3\r>'; if (AT_HANDSHAKE.includes(cmd))
                return 'OK\r>'; return defaults[cmd] ?? 'NO DATA\r>'; } }) });
    return { svc, writes, persist, advance: ms => { time += ms; }, async ready() { await svc.selectAdapter(MOCK_ADAPTERS[0]); await svc.connect(); await svc.pollStatus(); } };
}
test('MX+ is explicitly selectable and connects through the Bluetooth serial transport', async () => {
    const f = fixture();
    const adapters = await f.svc.listAdapters();
    const mx = adapters.find(a => /OBDLink MX\+/.test(a.friendlyName));
    assert.equal(mx.occupied, false);
    assert.equal(mx.preferred, false);
    await f.svc.selectAdapter(mx);
    await f.svc.connect();
    await f.svc.pollStatus();
    assert.equal(f.svc.snapshot().selected.port, 'COM9');
    assert.equal(f.svc.snapshot().adapterConnected, true);
    assert.equal(f.svc.snapshot().header.voltage.volts, 12.5);
    assert.equal((await f.persist('adapter:get', {})).pnpId, mx.pnpId);
    assert.ok(!f.writes.includes('04'));
    await f.svc.disconnect();
    assert.equal(f.svc.snapshot().adapterConnected, false);
    assert.equal(f.svc.snapshot().header.voltage, null);
});

test('serialized queue poisons after timeout including late SAME PID and queued writes', async () => {
    let receive;
    const sent = [];
    const q = new CommandQueue({ write: c => sent.push(c), subscribe: f => { receive = f; return () => { }; } });
    const first = q.request('010C', 5);
    const second = q.request('010C', 100);
    const outcomes = await Promise.allSettled([first, second]);
    assert.ok(outcomes.every(o => o.status === 'rejected'));
    receive('7E8 04 41 0C 0F A0\r>');
    assert.equal(sent.length, 1);
    await assert.rejects(q.request('04'));
    q.close();
});
test('prompt fragmentation works and overflow fails closed', async () => {
    let receive;
    const q = new CommandQueue({ write: () => { }, subscribe: f => { receive = f; return () => { }; } });
    const p = q.request('010C');
    receive('7E8 04 41');
    receive(' 0C 0F A0\r>');
    assert.match(await p, /0F A0/);
    const tooBig = q.request('03');
    await new Promise(r => setImmediate(r));
    receive('A'.repeat(65537));
    await assert.rejects(tooBig, /too_large/);
    await assert.rejects(q.request('03'));
    q.close();
});
test('mock is labelled and clear reports remaining stored, pending and permanent faults', async () => {
    const svc = createMockProduction();
    await svc.selectAdapter(MOCK_ADAPTERS[0]);
    await svc.connect();
    await svc.pollStatus();
    assert.equal(svc.snapshot().mock, true);
    const scan = await svc.scanFaults();
    assert.deepEqual(scan.modules[0].dtcs.map(d => d.status), ['stored', 'pending', 'permanent']);
    const report = await svc.clearDtcs();
    assert.equal(report.modules[0].result, 'remaining');
    assert.ok(report.preClearId);
    assert.equal((await svc.listEcus()).length, 1);
    await svc.disconnect();
});
test('adapter-only and stale state reject backend operations; unknown AT never connects', async () => {
    const f = fixture();
    await f.svc.selectAdapter(MOCK_ADAPTERS[0]);
    await f.svc.connect();
    await assert.rejects(f.svc.scanFaults());
    await f.svc.pollStatus();
    await f.svc.scanFaults();
    f.advance(30000);
    await assert.rejects(f.svc.clearDtcs());
    assert.ok(!f.writes.includes('04'));
    assert.equal(f.svc.snapshot().header.vehicleCommunicating, false);
    assert.equal(f.svc.snapshot().header.voltage, null);
    const bad = fixture({ ATH1: '?\r>' });
    await bad.svc.selectAdapter(MOCK_ADAPTERS[0]);
    await assert.rejects(bad.svc.connect());
    const voltage = fixture({ '0100': 'NO DATA\r>' });
    await voltage.ready();
    assert.equal(voltage.svc.snapshot().vehicleCommunicating, false);
});
test('per-ECU identity never borrows another response; partial mode stays incomplete', async () => {
    const both = (a, b) => pci(a).replace('>', '') + pci(b, '7E9');
    const f = fixture({ '0100': both([0x41, 0, 0, 0, 0, 0], [0x41, 0, 0, 0, 0, 0]), '03': both([0x43, 1, 3, 1], [0x43, 0]), '07': pci([0x47, 0]), '0A': both([0x4a, 0], [0x4a, 0]) });
    await f.ready();
    const scan = await f.svc.scanFaults();
    const rows = await f.svc.listEcus();
    assert.equal(rows.length, 2);
    assert.equal(rows.find(r => r.ecuAddress === '7E9').calibrationId, null);
    assert.equal(rows.find(r => r.ecuAddress === '7E9').cvn, null);
    assert.equal(scan.modules.find(r => r.ecu === '7E9').status, 'failed');
    const report = await f.svc.clearDtcs();
    assert.equal(report.modules.find(r => r.moduleKey === 'obd-can:7E9').result, 'unknown');
});
test('VIN changes and absent VIN never update a different vehicle; valid partial reads retain fields', async () => {
    let vin = 'WP0ZZZ98ZES000001', cal = 'CAL-A';
    const f = fixture({ '0902': () => vin ? pci([0x49, 2, 1, ...Buffer.from(vin)]) : 'NO DATA\r>', '0904': () => cal ? pci([0x49, 4, 1, ...Buffer.from(cal.padEnd(16))]) : 'NO DATA\r>' });
    await f.ready();
    await f.svc.scanFaults();
    cal = null;
    await f.svc.scanFaults();
    assert.equal((await f.svc.listEcus())[0].calibrationId, 'CAL-A');
    vin = 'WP0ZZZ98ZES000002';
    cal = 'CAL-B';
    await f.svc.scanFaults();
    assert.equal((await f.svc.listEcus())[0].calibrationId, 'CAL-B');
    const a = await f.persist('ecu:list', { vehicleKey: 'WP0ZZZ98ZES000001' });
    assert.equal(a[0].calibrationId, 'CAL-A');
    vin = null;
    cal = 'CAL-UNKNOWN';
    await f.svc.scanFaults();
    assert.match(f.svc.vehicleKey, /^unknown:/);
    assert.equal((await f.persist('ecu:list', { vehicleKey: 'WP0ZZZ98ZES000002' }))[0].calibrationId, 'CAL-B');
});
test('clear pre-save failure prevents transmit; timeout is unknown and never replayed', async () => {
    const store = memoryPersist();
    let fail = false;
    const f = fixture({}, async (op, v) => { if (fail && op === 'clear:pre')
        throw new Error('disk full'); return store(op, v); });
    await f.ready();
    await f.svc.scanFaults();
    fail = true;
    await assert.rejects(f.svc.clearDtcs(), /disk full/);
    assert.ok(!f.writes.includes('04'));
    const timed = fixture({ '04': () => { throw new Error('timeout'); } });
    await timed.ready();
    await timed.svc.scanFaults();
    const r = await timed.svc.clearDtcs();
    assert.equal(r.outcome, 'unknown');
    assert.equal(r.transportOk, false);
    assert.equal(timed.writes.filter(c => c === '04').length, 1);
    assert.equal(timed.svc.adapterConnected, false);
    await assert.rejects(timed.svc.clearDtcs());
});
test('clear needs same VIN, pending-only readback is remaining, complete empty readback clears', async () => {
    let vin = 'WP0ZZZ98ZES000001';
    const changed = fixture({ '0902': () => pci([0x49, 2, 1, ...Buffer.from(vin)]) });
    await changed.ready();
    await changed.svc.scanFaults();
    vin = 'WP0ZZZ98ZES000002';
    await assert.rejects(changed.svc.clearDtcs(), /vehicle_changed/);
    assert.ok(!changed.writes.includes('04'));
    const pending = fixture({ '07': pci([0x47, 1, 5, 0x71]) });
    await pending.ready();
    await pending.svc.scanFaults();
    assert.equal((await pending.svc.clearDtcs()).modules[0].result, 'remaining');
    const empty = fixture();
    await empty.ready();
    await empty.svc.scanFaults();
    assert.equal((await empty.svc.clearDtcs()).modules[0].result, 'cleared');
});
test('actual PowerShell stdin pump reads response while no next command exists (no serial hardware)', { skip: process.platform !== 'win32', timeout: 20000 }, async () => {
    const original = '$p = New-Object System.IO.Ports.SerialPort $portName, $baud';
    assert.ok(SERIAL_HOST_PS.includes(original));
    const fake = `$p = [pscustomobject]@{NewLine='';ReadTimeout=0;WriteTimeout=0;DtrEnable=$false;RtsEnable=$false;Chunks=[Collections.Generic.Queue[string]]::new()}
$p | Add-Member ScriptMethod Open {}
$p | Add-Member ScriptMethod Close {}
$p | Add-Member ScriptProperty BytesToRead { $this.Chunks.Count }
$p | Add-Member ScriptMethod Write { param($data) $this.Chunks.Enqueue("ELM327 v2.3" + [char]13 + '>') }
$p | Add-Member ScriptMethod ReadExisting { $this.Chunks.Dequeue() }`;
    const host = SERIAL_HOST_PS.replace(original, fake);
    assert.ok(!host.includes('New-Object System.IO.Ports.SerialPort'));
    const transport = new PowerShellSerialTransport({ port: 'COM55' }, { spawnPs: (bin, args, opts) => spawn(bin, ['-NoProfile', '-NonInteractive', '-Command', host], opts) });
    try {
        assert.match(await transport.request('ATZ', 2000), /ELM327 v2.3/);
    }
    finally {
        transport.close();
    }
});
