import { describe, it, expect } from 'vitest';
import { buildFaultDataPlan, validateAnalysisSelections, decodeAnalysisPid, getAnalysisCatalog } from './obd-analysis.js';
import { decodeProductionObservation, assertTrustedCommand, type ScannedDtc } from './obd-production.js';
import { MANUAL_CODE_CANDIDATES } from './obd-manual-needs.generated.js';
const dtc = (code: string, ecu = '7E8'): ScannedDtc => ({ code, ecu, moduleKey: `obd-can:${ecu}`, status: 'stored', manual: null, manualFallback: null });
describe('analysis requirements and boundaries', () => {
  it('deduplicates only equal module/parameter and retains all gaps', () => {
    const plan = buildFaultDataPlan([dtc('P0301'), dtc('P0302'), dtc('P0301','7E9'), dtc('P9999')]);
    expect(plan.items.filter(p => p.parameterId === 'rpm')).toHaveLength(2);
    expect(plan.associations['obd-can:7E8/rpm']).toHaveLength(2);
    expect(plan.gaps.some(g => g.includes('P9999'))).toBe(true);
    expect(plan.items.find(p => p.parameterId === 'misfire-counts')?.supported).toBe(false);
    expect(plan.once.length).toBeGreaterThan(0);
    for (const id of MANUAL_CODE_CANDIDATES.P0301.signalIds) {
      expect(plan.items.some(p => p.parameterId === id)).toBe(true);
    }
  });
  it('distinguishes manual brake adjustment from supplemental switch data', () => {
    const p = buildFaultDataPlan([dtc('P0571')]);
    expect(p.items.every(i => !i.supported)).toBe(true);
    expect(p.manualChecks.join()).toContain('调整值');
    expect(p.gaps.join()).toContain('补充观察');
  });
  it('rejects overcap, duplicate, vendor and injected commands at backend boundary', () => {
    const s = { moduleKey: 'obd-can:7E8', parameterId: 'rpm' };
    expect(validateAnalysisSelections([s])).toEqual([s]);
    for (const invalid of [[], Array(9).fill(s), [s,s], [{...s, parameterId:'04'}], [{...s, moduleKey:'dme'}], [{...s, moduleKey:'obd-can:7E8\r04'}]]) expect(() => validateAnalysisSelections(invalid)).toThrow();
    for (const cmd of ['2E1234','3101','0105\r04','ATSH7E0']) expect(() => assertTrustedCommand(cmd)).toThrow();
    expect(getAnalysisCatalog().filter(i => i.supported).length).toBe(16);
  });
  it('decodes units and rejects wrong lengths and another PID', () => {
    expect(decodeAnalysisPid(5,[128])).toBe(88);
    expect(decodeAnalysisPid(6,[128])).toBe(0);
    expect(decodeAnalysisPid(0x10,[1,144])).toBe(4);
    expect(decodeAnalysisPid(0x42,[0x30,0x34])).toBeCloseTo(12.34);
    expect(decodeAnalysisPid(5,[128,0])).toBeNull();
    expect(decodeProductionObservation('0105','7E8 03 41 06 80\r>').outcome).toBe('invalid');
    expect(decodeProductionObservation('0120','7E8 06 41 20 00 00 00 19\r>').supported['7E8']).toEqual([0x3c,0x3d,0x40]);
    const multi = decodeProductionObservation('0105','7E8 03 41 05 80\r7E9 03 41 05 50\r>');
    expect(multi.perEcu.map(p=>p.value)).toEqual([88,40]);
  });
  it('uses extracted fuel-pressure needs without turning source conditions into thresholds', () => {
    const plan=buildFaultDataPlan([dtc('P0087')]);
    expect(plan.items.some(i=>i.name.includes('燃油轨压力'))).toBe(true);
    expect(plan.items.some(i=>i.parameterId==='rpm'&&i.supported)).toBe(true);
    expect(plan.items.filter(i=>i.parameterId.startsWith('manual-')).every(i=>!i.supported)).toBe(true);
    expect(plan.gaps.join()).toContain('候选');
    expect(plan.gaps.join()).not.toMatch(/140 km|14\.844/);
  });
});
