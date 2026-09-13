import { randomUUID } from 'node:crypto';
import { AT_HANDSHAKE, applyEcuUpsert, assertAtOk, assertAtzBanner, assertTrustedCommand, attachManual, buildCapabilityRegistry, classifyAdapter, decodeProductionObservation, headerFresh, headerIdle, ignitionFromRpm, isObdResponder, parseAtrv, responderKey, responderName } from '@porsche981/domain';
import { MOCK_ADAPTERS, ScriptedTransport, cannedMockScript } from './queue.mjs';
import { readAnalysisCycle } from './analysis-reader.mjs';
export const STATUS_STALE_MS = 8000, SCAN_FRESH_MS = 120000;
const iso = now => new Date(now()).toISOString();
const validVin = v => /^[A-HJ-NPR-Z0-9]{17}$/.test(v ?? '');
export class ProductionService {
    constructor({ persist, transportFactory, listAdapters, now = Date.now, reference = null, codes = null, mock = false }) {
        Object.assign(this, { persist, transportFactory, listAdaptersFn: listAdapters, now, reference, codes, mock });
        this.op = null;
        this.selected = null;
        this.transport = null;
        this.adapterConnected = false;
        this.vehicleCommunicating = false;
        this.header = headerIdle(null, { mock });
        this.lastScan = null;
        this.sessionNonce = randomUUID();
        this.vehicleKey = null;
    }
    capabilities() { return buildCapabilityRegistry(this.reference); }
    async exclusive(name, fn) { if (this.op)
        throw new Error('obd_busy:' + this.op); this.op = name; try {
        return await fn();
    }
    finally {
        this.op = null;
    } }
    snapshot() {
        const h = { ...this.header };
        const fresh = headerFresh(h, this.now(), STATUS_STALE_MS);
        if (h.observedAt && !fresh) {
            h.vehicleCommunicating = false;
            h.ignition = 'unknown';
            h.stale = true;
        }
        if (h.voltage && (this.now() - Date.parse(h.voltage.observedAt) > STATUS_STALE_MS || !this.adapterConnected)) {
            h.voltage = null;
            h.stale = true;
        }
        return { mock: this.mock, op: this.op, selected: this.selected, header: h, lastScan: this.lastScan, capabilities: this.capabilities(), adapterConnected: this.adapterConnected, vehicleCommunicating: fresh, vehicleKey: this.vehicleKey, label: this.mock ? '隔离测试传输，不是实车' : null };
    }
    async listAdapters() { return (await this.listAdaptersFn()).filter(r => /^COM[1-9]\d*$/i.test(r.port)).map(r => classifyAdapter(r.port, r.friendlyName ?? r.port, r.pnpId ?? null)); }
    async selectAdapter(requested) {
        return this.exclusive('select', async () => {
            const hit = (await this.listAdapters()).find(a => a.port === requested?.port && a.pnpId === (requested?.pnpId ?? null));
            if (!hit)
                throw new Error('obd_adapter_not_enumerated');
            if (hit.occupied)
                throw new Error('obd_adapter_occupied');
            await this.disconnectInternal();
            await this.persist('adapter:set', hit);
            this.selected = hit;
            this.header = headerIdle(this.header, { adapter: hit, mock: this.mock });
            return hit;
        });
    }
    async connect() {
        return this.exclusive('connect', async () => {
            if (!this.selected)
                throw new Error('obd_adapter_not_selected');
            const current = (await this.listAdapters()).find(a => a.port === this.selected.port && a.pnpId === this.selected.pnpId);
            if (!current || current.occupied)
                throw new Error('obd_adapter_changed');
            await this.disconnectInternal();
            this.selected = current;
            this.sessionNonce = randomUUID();
            this.transport = this.transportFactory(current);
            try {
                for (const cmd of AT_HANDSHAKE) {
                    const raw = await this.transport.request(cmd, cmd === 'ATZ' ? 4000 : 2000);
                    if (cmd === 'ATZ')
                        assertAtzBanner(raw);
                    else
                        assertAtOk(cmd, raw);
                }
            }
            catch (e) {
                await this.disconnectInternal();
                throw e;
            }
            this.adapterConnected = true;
            this.header = headerIdle(this.header, { adapterConnected: true, adapter: current, mock: this.mock });
            return this.snapshot();
        });
    }
    async disconnect() { return this.exclusive('disconnect', () => this.disconnectInternal()); }
    async disconnectInternal() { this.transport?.close?.(); this.transport = null; this.adapterConnected = false; this.vehicleCommunicating = false; this.lastScan = null; this.vehicleKey = null; this.header = headerIdle(this.header, { adapter: this.selected, mock: this.mock }); return this.snapshot(); }
    async request(cmd, timeoutMs) {
        assertTrustedCommand(cmd);
        if (!this.transport)
            throw new Error('disconnected');
        try {
            return await this.transport.request(cmd, timeoutMs);
        }
        catch (e) {
            this.transport?.close?.();
            this.transport = null;
            this.adapterConnected = false;
            this.vehicleCommunicating = false;
            this.lastScan = null;
            this.header = headerIdle(this.header, { adapter: this.selected, mock: this.mock, stale: true });
            throw e;
        }
    }
    liveEnough() { return this.adapterConnected && Boolean(this.transport) && headerFresh(this.header, this.now(), STATUS_STALE_MS); }
    scanFresh() { const age = this.now() - Date.parse(this.lastScan?.at ?? ''); return Boolean(this.lastScan && this.lastScan.sessionNonce === this.sessionNonce && age >= 0 && age <= SCAN_FRESH_MS); }
    async pollStatus() {
        if (this.op)
            return this.snapshot().header;
        return this.exclusive('status', async () => {
            if (!this.adapterConnected || !this.transport)
                return this.snapshot().header;
            let voltage = null, rpm = null, vehicleAt = null;
            try {
                const v = parseAtrv(await this.request('ATRV', 1500));
                if (v != null)
                    voltage = { volts: v, source: 'ATRV', observedAt: iso(this.now) };
                const supported = decodeProductionObservation('0100', await this.request('0100', 2000));
                if (supported.perEcu.some(p => p.ok))
                    vehicleAt = iso(this.now);
                if (vehicleAt) {
                    const r = decodeProductionObservation('010C', await this.request('010C', 2000));
                    if (r.perEcu.some(p => p.ok)) {
                        rpm = r.rpm;
                        vehicleAt = iso(this.now);
                    }
                    if (!voltage) {
                        const r = decodeProductionObservation('0142', await this.request('0142', 2000));
                        if (r.voltage != null)
                            voltage = { volts: r.voltage, source: 'PID0142', observedAt: iso(this.now) };
                    }
                }
            }
            catch {
                return this.snapshot().header;
            }
            this.vehicleCommunicating = Boolean(vehicleAt);
            this.header = { adapterConnected: this.adapterConnected, vehicleCommunicating: this.vehicleCommunicating, ignition: ignitionFromRpm(rpm), voltage, stale: false, observedAt: vehicleAt, adapter: this.selected, mock: this.mock };
            return this.snapshot().header;
        });
    }
    async scanFaults() { return this.exclusive('scan', () => this.scanInternal()); }
    async readAnalysis(selections) { return this.exclusive('analysis', () => readAnalysisCycle(this, selections)); }
    async scanInternal() {
        if (!this.liveEnough())
            throw new Error('obd_scan_requires_live');
        const sessionNonce = this.sessionNonce, evidence = [], responders = new Map();
        let lastResponseAt = this.header.observedAt;
        const note = ecu => {
            if (!isObdResponder(ecu))
                return null;
            if (!responders.has(ecu))
                responders.set(ecu, { moduleKey: responderKey(ecu), name: responderName(ecu), ecu, modes: { '03': 'missing', '07': 'missing', '0A': 'missing' }, dtcs: [], raw: [], lastSuccessAt: null });
            return responders.get(ecu);
        };
        const query = async (cmd) => {
            if (!this.transport)
                return null;
            try {
                const raw = await this.request(cmd, 4000), decoded = decodeProductionObservation(cmd, raw), at = iso(this.now);
                evidence.push({ command: cmd, raw, at, outcome: decoded.outcome });
                for (const p of decoded.perEcu)
                    if (p.ok && isObdResponder(p.ecu)) {
                        note(p.ecu).lastSuccessAt = at;
                        lastResponseAt = at;
                    }
                return decoded;
            }
            catch (e) {
                evidence.push({ command: cmd, raw: '', at: iso(this.now), outcome: e.message });
                return null;
            }
        };
        await query('0100');
        for (const cmd of ['03', '07', '0A']) {
            const d = await query(cmd);
            for (const row of responders.values())
                row.modes[cmd] = d ? (d.outcome === 'no-data' ? 'no-data' : 'missing') : 'disconnected';
            for (const p of d?.perEcu ?? []) {
                const row = note(p.ecu);
                if (!row)
                    continue;
                row.modes[cmd] = p.ok ? 'ok' : 'invalid';
                row.raw.push(d.raw);
                if (p.ok)
                    row.dtcs.push(...p.dtcs.map(x => attachManual(x, row.moduleKey, this.reference, this.codes)));
            }
        }
        // Query each identity once, then partition strictly by responding CAN address.
        const vinD = await query('0902'), calD = await query('0904'), cvnD = await query('0906');
        const observedVin = vinD?.detail === 'conflicting_vins' ? null : vinD?.vin;
        // A later VIN cannot prove that an earlier unidentified scan belongs to the same car.
        const key = validVin(observedVin) ? observedVin : `unknown:${sessionNonce}:${randomUUID()}`;
        this.vehicleKey = key;
        for (const row of responders.values()) {
            if (!row.lastSuccessAt)
                continue;
            const existing = await this.persist('ecu:get', { vehicleKey: key, moduleKey: row.moduleKey });
            const cal = calD?.perEcu.find(p => p.ecu === row.ecu && p.ok)?.calibrationId ?? null;
            const cvn = cvnD?.perEcu.find(p => p.ecu === row.ecu && p.ok)?.cvn ?? null;
            await this.persist('ecu:upsert', applyEcuUpsert(existing, { vehicleKey: key, moduleKey: row.moduleKey, name: row.name, ecuAddress: row.ecu, calibrationId: cal, cvn, lastSuccessAt: row.lastSuccessAt }, row.lastSuccessAt));
        }
        const modules = [...responders.values()].map(row => {
            const complete = Object.values(row.modes).every(m => m === 'ok');
            return { ...row, orderUnknown: true, status: complete ? (row.dtcs.length ? 'success' : 'none') : 'failed', stored: row.dtcs.filter(d => d.status === 'stored').map(d => d.code), pending: row.dtcs.filter(d => d.status === 'pending').map(d => d.code), permanent: row.dtcs.filter(d => d.status === 'permanent').map(d => d.code), detail: complete ? null : '读取不完整；已读取的故障仍列出，缺失结果不能当作无故障。' };
        });
        modules.push(...this.capabilities().map(cap => ({ moduleKey: cap.key, name: cap.name, status: 'not-supported', ecu: null, orderUnknown: cap.orderUnknown, dtcs: [], stored: [], pending: [], permanent: [], modes: {}, raw: [], detail: '该厂商单元的诊断方式尚未验证；未确认本车是否安装。' })));
        const scan = { id: null, at: iso(this.now), vehicleKey: key, adapter: this.selected, sessionNonce, modules, evidence };
        scan.id = await this.persist('scan:save', scan);
        this.lastScan = scan;
        if (this.adapterConnected) {
            this.header = { ...this.header, vehicleCommunicating: Boolean(responders.size), observedAt: lastResponseAt, ignition: 'unknown' };
            this.vehicleCommunicating = this.header.vehicleCommunicating;
        }
        return scan;
    }
    async clearDtcs() {
        return this.exclusive('clear', async () => {
            if (!this.liveEnough())
                throw new Error('obd_clear_requires_live');
            if (!this.scanFresh())
                throw new Error('obd_clear_requires_fresh_scan');
            const pre = this.lastScan;
            if (!pre.modules.some(m => m.ecu && m.modes['03'] === 'ok'))
                throw new Error('obd_clear_requires_fresh_scan');
            const alive = decodeProductionObservation('0100', await this.request('0100', 4000));
            if (!alive.perEcu.some(p => p.ok)) {
                this.header.vehicleCommunicating = false;
                throw new Error('obd_clear_requires_live');
            }
            let verifiedAt = iso(this.now);
            if (validVin(pre.vehicleKey)) {
                const vin = decodeProductionObservation('0902', await this.request('0902', 4000));
                if (vin.vin !== pre.vehicleKey || vin.detail === 'conflicting_vins') {
                    this.lastScan = null;
                    this.vehicleKey = null;
                    throw new Error('obd_vehicle_changed_read_again');
                }
                verifiedAt = iso(this.now);
            }
            const preClearId = await this.persist('clear:pre', pre);
            let raw = '', decoded = null, transportOk = false, outcome = 'unknown';
            try {
                raw = await this.request('04', 5000);
                transportOk = true;
                decoded = decodeProductionObservation('04', raw);
                outcome = decoded.outcome;
            }
            catch (e) {
                raw = e.message;
            }
            this.lastScan = null;
            const positives = new Set((decoded?.perEcu ?? []).filter(p => p.ok).map(p => p.ecu));
            let readback = null;
            if (this.transport && this.adapterConnected) {
                this.header = { ...this.header, observedAt: positives.size ? iso(this.now) : verifiedAt, vehicleCommunicating: true, stale: false };
                try {
                    readback = await this.scanInternal();
                }
                catch { /* preserve uncertain result */ }
            }
            const modules = pre.modules.map(m => {
                const base = { moduleKey: m.moduleKey, name: m.name, ecuPositive: positives.has(m.ecu) };
                if (!m.ecu)
                    return { ...base, result: 'not-attempted', detail: '尚不支持此单元的清除' };
                if (!base.ecuPositive) {
                    const refused = decoded?.perEcu.some(p => p.ecu === m.ecu && !p.ok && p.detail === 'clear_not_positive');
                    return { ...base, result: refused ? 'failed' : 'unknown', detail: refused ? '控制单元未接受清除' : '未确认清除应答；不会自动重试' };
                }
                const after = readback?.modules.find(a => a.moduleKey === m.moduleKey);
                if (!after || after.status === 'failed' || (validVin(pre.vehicleKey) && readback.vehicleKey !== pre.vehicleKey))
                    return { ...base, result: 'unknown', detail: '已收到清除应答，复读不完整或车辆身份未确认' };
                const remaining = [...after.stored, ...after.pending, ...after.permanent];
                if (remaining.length)
                    return { ...base, result: 'remaining', detail: `仍有 ${remaining.join('、')}${after.permanent.length ? '；永久故障码由车辆自行验证消除' : ''}` };
                return { ...base, result: 'cleared', detail: '已收到清除应答，复读未见故障码' };
            });
            const report = { at: iso(this.now), preClearId, pre, transportOk, ecuPositive: positives.size > 0, raw, outcome, scope: '所有在线且支持标准排放诊断清除的控制单元；不包括尚未支持的保时捷专用诊断。', modules, readback };
            await this.persist('clear:save', report);
            return report;
        });
    }
    async listEcus() { return this.vehicleKey ? this.persist('ecu:list', { vehicleKey: this.vehicleKey }) : []; }
}
export function createMockProduction(opts = {}) { return new ProductionService({ mock: true, reference: opts.reference ?? null, codes: opts.codes ?? null, persist: opts.persist ?? memoryPersist(), now: opts.now ?? Date.now, listAdapters: async () => MOCK_ADAPTERS, transportFactory: () => new ScriptedTransport(opts.script ?? cannedMockScript(), { repeat: opts.repeat !== false }) }); }
export function memoryPersist() {
    let adapter = null;
    const ecus = new Map(), scans = [], clears = [], changes = [];
    return async (op, value) => {
        if (op === 'adapter:set')
            return adapter = value;
        if (op === 'adapter:get')
            return adapter;
        if (op === 'scan:save') {
            scans.push(structuredClone(value));
            return scans.length;
        }
        if (op === 'clear:pre' || op === 'clear:save') {
            clears.push(structuredClone(value));
            return clears.length;
        }
        if (op === 'ecu:knownVins')
            return [...new Set([...ecus.values()].map(e => e.vehicleKey))].filter(validVin);
        if (op === 'ecu:get')
            return structuredClone(ecus.get(value.vehicleKey + '|' + value.moduleKey) ?? null);
        if (op === 'ecu:upsert') {
            ecus.set(value.row.vehicleKey + '|' + value.row.moduleKey, structuredClone(value.row));
            changes.push(...value.changes);
            return value.row;
        }
        if (op === 'ecu:list')
            return structuredClone([...ecus.values()].filter(e => e.vehicleKey === value.vehicleKey));
        if (op === 'ecu:changes')
            return changes.filter(e => e.vehicleKey === value.vehicleKey);
        throw new Error('persist:' + op);
    };
}
export { ScriptedTransport, cannedMockScript, MOCK_ADAPTERS };
