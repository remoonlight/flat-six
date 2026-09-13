import { randomUUID } from 'node:crypto';
import {
  ANALYSIS_PARAMETERS, analysisCommand, analysisKey, validateAnalysisSelections,
  decodeProductionObservation, applyEcuUpsert, responderName, headerIdle, ignitionFromRpm,
} from '@porsche981/domain';

/** One bounded cycle. Reuses the production service lock and quarantined transport.
 * No renderer command strings, physical module guesses, writes or retry loops.
 */
export async function readAnalysisCycle(svc, input) {
  const selections = validateAnalysisSelections(input);
  if (!svc.liveEnough()) throw new Error('需要当前有效的车辆通信后才能采集');
  const started = svc.now(), stamp = () => new Date(svc.now()).toISOString();
  const evidence = [], values = [], support = new Map(), failedSupport = new Set();
  let lastResponseAt = null, rpm = null, voltage = null;
  const query = async command => {
    const raw = await svc.request(command, 2000);
    const result = decodeProductionObservation(command, raw);
    const at = stamp();
    evidence.push({ command, raw, at, outcome: result.outcome });
    if (result.perEcu.some(p => p.ok)) lastResponseAt = at;
    return { result, at };
  };
  const invalidate = () => {
    svc.lastScan = null; svc.vehicleKey = null; svc.vehicleCommunicating = false;
    svc.header = headerIdle(svc.header, { adapterConnected: svc.adapterConnected, adapter: svc.selected, mock: svc.mock, stale: true });
  };
  try {
    const vinBefore = (await query('0902')).result;
    if (vinBefore.detail === 'conflicting_vins') { invalidate(); throw new Error('多个车架号应答冲突，请重新确认车辆'); }
    const beforeVin = vinBefore.vin;
    if (svc.vehicleKey && !svc.vehicleKey.startsWith('unknown:') && beforeVin !== svc.vehicleKey) {
      invalidate(); throw new Error('车辆身份已变化或未能确认，请重新读取故障码并选择数据');
    }
    const wantedPids = selections.map(s => ANALYSIS_PARAMETERS.find(p => p.id === s.parameterId).pid);
    for (const base of [0, 0x20, 0x40]) {
      if (base && !wantedPids.some(pid => pid > base)) continue;
      // Only follow a supported page chain. No assumption that other responders support it.
      if (base && ![...support.values()].some(pids => pids.has(base))) continue;
      const { result } = await query(analysisCommand(base));
      for (const s of selections) {
        const ecu = s.moduleKey.slice(8);
        if (base && !support.get(ecu)?.has(base)) continue;
        const pids = result.supported[ecu];
        if (pids) {
          const set = support.get(ecu) ?? new Set();
          pids.forEach(pid => set.add(pid)); support.set(ecu, set);
        } else failedSupport.add(`${ecu}:${base}`);
      }
    }
    const received = new Map();
    for (const selection of selections) {
      const p = ANALYSIS_PARAMETERS.find(p => p.id === selection.parameterId), ecu = selection.moduleKey.slice(8);
      const value = { ...selection, key: analysisKey(selection), value: null, unit: p.unit, status: 'unsupported', observedAt: null, detail: '该单元本次未报告支持此数据' };
      if (!support.get(ecu)?.has(p.pid)) {
        const base = Math.floor((p.pid - 1) / 32) * 32;
        if ([0, 0x20, 0x40].some(page => page <= base && failedSupport.has(`${ecu}:${page}`))) {
          value.status = 'failed'; value.detail = '该单元支持列表读取失败，无法确认支持情况';
        }
        values.push(value); continue;
      }
      const command = analysisCommand(p.pid);
      // Functional request once per PID, but values stay partitioned by exact ECU address.
      if (!received.has(command)) received.set(command, await query(command));
      const { result, at } = received.get(command);
      const own = result.perEcu.find(r => r.ecu === ecu);
      if (own?.ok && typeof own.value === 'number') {
        Object.assign(value, { value: own.value, status: 'valid', observedAt: at, detail: null });
        if (p.id === 'rpm') rpm = own.value;
        if (p.id === 'voltage') voltage = { volts: own.value, source: 'PID0142', observedAt: at };
      } else { value.status = 'failed'; value.detail = own?.detail ?? result.detail ?? '该单元未返回此数据'; }
      values.push(value);
    }
    // Refresh identities at most once a minute, or when a new selection involves another ECU.
    const signature = `${svc.sessionNonce}/${beforeVin ?? '?'}/${[...new Set(selections.map(s => s.moduleKey))].sort().join(',')}`;
    const refreshIdentity = svc.analysisIdentity?.signature !== signature || svc.now() - svc.analysisIdentity.at >= 60000;
    const calibration = refreshIdentity ? (await query('0904')).result : null;
    const cvn = refreshIdentity ? (await query('0906')).result : null;
    const vinAfter = (await query('0902')).result;
    if (vinAfter.detail === 'conflicting_vins' || vinAfter.vin !== beforeVin) {
      invalidate(); throw new Error('采集期间车辆身份未保持一致，本批数据已丢弃');
    }
    const vehicleKey = beforeVin ?? `unknown:${svc.sessionNonce}:analysis:${randomUUID()}`;
    if (refreshIdentity) {
      for (const moduleKey of new Set(values.filter(v => v.status === 'valid').map(v => v.moduleKey))) {
        const ecu = moduleKey.slice(8);
        const cal = calibration?.perEcu.find(r => r.ecu === ecu && r.ok)?.calibrationId ?? null;
        const checksum = cvn?.perEcu.find(r => r.ecu === ecu && r.ok)?.cvn ?? null;
        const existing = await svc.persist('ecu:get', { vehicleKey, moduleKey });
        await svc.persist('ecu:upsert', applyEcuUpsert(existing, { vehicleKey, moduleKey, name: responderName(ecu), ecuAddress: ecu, calibrationId: cal, cvn: checksum, lastSuccessAt: stamp() }, stamp()));
      }
      // Failed/partial identity reads stay eligible for retry on the next explicitly scheduled cycle.
      if (calibration?.outcome === 'ok' && cvn?.outcome === 'ok') svc.analysisIdentity = { signature, at: svc.now() };
    }
    svc.vehicleKey = vehicleKey;
    svc.vehicleCommunicating = Boolean(lastResponseAt);
    svc.header = { ...svc.header, adapterConnected: svc.adapterConnected, vehicleCommunicating: Boolean(lastResponseAt), observedAt: lastResponseAt, stale: false, ignition: ignitionFromRpm(rpm), voltage: voltage ?? svc.header.voltage };
    return { values, startedAt: new Date(started).toISOString(), finishedAt: stamp(), elapsedMs: svc.now() - started, mock: svc.mock, vehicleKey, evidence };
  } catch (error) {
    if (!svc.adapterConnected) invalidate();
    throw error;
  }
}
