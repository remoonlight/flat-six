import type { ScannedDtc } from './obd-production.js';
import { MANUAL_CODE_CANDIDATES, MANUAL_SIGNAL_CANDIDATES } from './obd-manual-needs.generated.js';

/** A provisional software batch budget, NOT an adapter hardware maximum. */
export const ANALYSIS_BATCH_LIMIT = 8;
export const ANALYSIS_STALE_MS = 8000;
export type AnalysisSelection = { moduleKey: string; parameterId: string };
export type AnalysisItem = AnalysisSelection & { key: string; moduleName: string; name: string; unit: string; supported: boolean; reason: string | null; kind: 'continuous' | 'once' | 'manual' | 'unverified'; note: string };
export type AnalysisValue = AnalysisSelection & { key: string; value: number | string | null; unit: string; status: 'valid' | 'unsupported' | 'failed' | 'disconnected'; observedAt: string | null; detail: string | null };
export type AnalysisBatch = { values: AnalysisValue[]; startedAt: string; finishedAt: string; elapsedMs: number; mock: boolean; vehicleKey: string | null };
export type FaultDataPlan = { items: AnalysisItem[]; associations: Record<string, string[]>; gaps: string[]; manualChecks: string[]; once: string[] };
export function analysisKey(s: AnalysisSelection): string { return `${s.moduleKey}/${s.parameterId}`; }

/** Public service 01 physical signals. Byte ranges are encoding ranges, never diagnostic thresholds.
 * Engineering source: CSS Electronics OBD2 PID reference, retrieved 2026-09-13.
 * https://www.csselectronics.com/pages/obd2-pid-table-on-board-diagnostics-j1979
 */
export const ANALYSIS_PARAMETERS = [
  { id: 'load', pid: 0x04, name: '发动机计算负荷', unit: '%', bytes: 1, scale: 100 / 255, offset: 0 },
  { id: 'coolant', pid: 0x05, name: '冷却液温度', unit: '°C', bytes: 1, scale: 1, offset: -40 },
  { id: 'stft1', pid: 0x06, name: '缸列 1 短期燃油修正', unit: '%', bytes: 1, scale: 100 / 128, offset: -100 },
  { id: 'ltft1', pid: 0x07, name: '缸列 1 长期燃油修正', unit: '%', bytes: 1, scale: 100 / 128, offset: -100 },
  { id: 'stft2', pid: 0x08, name: '缸列 2 短期燃油修正', unit: '%', bytes: 1, scale: 100 / 128, offset: -100 },
  { id: 'ltft2', pid: 0x09, name: '缸列 2 长期燃油修正', unit: '%', bytes: 1, scale: 100 / 128, offset: -100 },
  { id: 'map', pid: 0x0b, name: '进气歧管绝对压力', unit: 'kPa', bytes: 1, scale: 1, offset: 0 },
  { id: 'rpm', pid: 0x0c, name: '发动机转速', unit: 'rpm', bytes: 2, scale: 0.25, offset: 0 },
  { id: 'speed', pid: 0x0d, name: '车速', unit: 'km/h', bytes: 1, scale: 1, offset: 0 },
  { id: 'timing', pid: 0x0e, name: '点火提前角', unit: '°', bytes: 1, scale: 0.5, offset: -64 },
  { id: 'intake-temp', pid: 0x0f, name: '进气温度', unit: '°C', bytes: 1, scale: 1, offset: -40 },
  { id: 'maf', pid: 0x10, name: '空气流量', unit: 'g/s', bytes: 2, scale: 0.01, offset: 0 },
  { id: 'throttle', pid: 0x11, name: '节气门位置', unit: '%', bytes: 1, scale: 100 / 255, offset: 0 },
  { id: 'catalyst1', pid: 0x3c, name: '缸列 1 催化器温度（传感器 1）', unit: '°C', bytes: 2, scale: 0.1, offset: -40 },
  { id: 'catalyst2', pid: 0x3d, name: '缸列 2 催化器温度（传感器 1）', unit: '°C', bytes: 2, scale: 0.1, offset: -40 },
  { id: 'voltage', pid: 0x42, name: '控制单元电压', unit: 'V', bytes: 2, scale: 0.001, offset: 0 },
] as const;

export function analysisCommand(pid: number): string { return `01${pid.toString(16).padStart(2, '0').toUpperCase()}`; }
export function decodeAnalysisPid(pid: number, bytes: number[]): number | null {
  const p = ANALYSIS_PARAMETERS.find(p => p.pid === pid);
  if (!p || bytes.length !== p.bytes || bytes.some(b => !Number.isInteger(b) || b < 0 || b > 255)) return null;
  return bytes.reduce((a, b) => a * 256 + b, 0) * p.scale + p.offset;
}

const SPECIAL_ITEMS = [
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'misfire-counts', name: '各缸失火计数', unit: '次', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'lambda-signals', name: '前后氧传感器 / 空燃比信号', unit: '', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'brake-switch', name: '制动灯开关状态', unit: '', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'brake-test-switch', name: '制动测试开关实际值', unit: '', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'acceleration', name: '控制单元计算的实际加速度', unit: '', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'engine-torque', name: '发动机扭矩', unit: 'Nm', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'modeled-catalyst-temp', name: '诊断使用的催化器模型温度', unit: '°C', kind: 'continuous' as const },
  { moduleKey: 'psm', moduleName: 'PSM（车身稳定）', parameterId: 'brake-switch', name: '制动灯开关状态', unit: '', kind: 'continuous' as const },
  { moduleKey: 'psm', moduleName: 'PSM（车身稳定）', parameterId: 'vehicle-speed', name: 'PSM 记录的车速', unit: 'km/h', kind: 'continuous' as const },
  { moduleKey: 'pdk', moduleName: 'PDK（变速箱）', parameterId: 'oil-temp', name: '变速箱油温', unit: '°C', kind: 'continuous' as const },
  { moduleKey: 'dme', moduleName: 'DME（发动机）', parameterId: 'freeze-frame', name: '故障发生时的冻结帧', unit: '', kind: 'once' as const },
];
export function getAnalysisCatalog(modules: Array<{ moduleKey: string; name: string }> = []): AnalysisItem[] {
  const responders = [...new Map(modules.filter(m => /^obd-can:7E[8-F]$/.test(m.moduleKey)).map(m => [m.moduleKey, m])).values()];
  if (!responders.length) responders.push({ moduleKey: 'obd-can:7E8', name: '排放诊断单元 7E8（接车后核实）' });
  return [
    ...responders.flatMap(m => ANALYSIS_PARAMETERS.map(p => ({ moduleKey: m.moduleKey, parameterId: p.id, key: analysisKey({ moduleKey: m.moduleKey, parameterId: p.id }), moduleName: m.name, name: p.name, unit: p.unit, supported: true, reason: null, kind: 'continuous' as const, note: '已实现标准读取；本车是否支持由该单元实际回复确定。暂无本车手册判断阈值。' }))),
    ...SPECIAL_ITEMS.map(p => ({ ...p, key: analysisKey(p), supported: false, reason: '厂商读取方法与本车适用性待验证', note: '不使用其他单元或其他车型的同名值替代。' })),
    ...MANUAL_SIGNAL_CANDIDATES.map(p => ({ moduleKey: p.moduleKey, moduleName: p.moduleName, parameterId: p.id, key: analysisKey({ moduleKey: p.moduleKey, parameterId: p.id }), name: p.name, unit: p.unit, kind: 'unverified' as const, supported: false, reason: '已从诊断正文提取相关需求；具体读取对应尚待核实', note: '候选检查需求，尚未证明本车厂商诊断支持。不套用其他车型阈值。' })),
  ];
}

/** Conservative curated observations. They are supplemental hypotheses, not verified workshop procedures. */
export function buildFaultDataPlan(dtcs: ScannedDtc[]): FaultDataPlan {
  const plan: FaultDataPlan = { items: [], associations: {}, gaps: [], manualChecks: [], once: [] };
  const seen = new Map<string, AnalysisItem>();
  for (const dtc of dtcs) {
    const code = dtc.code.toUpperCase(), label = `${code} · 单元 ${dtc.ecu}`;
    const catalog = getAnalysisCatalog([{ moduleKey: dtc.moduleKey, name: `排放诊断单元 ${dtc.ecu}` }]);
    const ids: string[] = [];
    const special: string[] = [];
    if (/^P030[0-6]$/.test(code)) {
      ids.push('rpm', 'load', 'coolant', 'stft1', 'ltft1', 'stft2', 'ltft2');
      special.push('dme/misfire-counts');
      if (code !== 'P0300') {
        special.push('dme/engine-torque');
        plan.manualChecks.push(`${label}：诊断运行条件包含发动机转速 150–6850 rpm、扭矩大于 0 Nm；这是诊断条件，不是正常值判定或操作指令。`);
      }
      plan.once.push(`${label}：故障发生时的冻结帧（当前读取尚未实现）`);
      plan.gaps.push(`${label}：所列实时参数为补充观察建议；失火计数、原文检查条件与本车阈值尚待核实。`);
    } else if (code === 'P0420' || code === 'P0430') {
      ids.push('rpm', 'load', 'coolant', code === 'P0420' ? 'catalyst1' : 'catalyst2');
      special.push('dme/lambda-signals', 'dme/modeled-catalyst-temp');
      plan.manualChecks.push(`${label}：核对燃油质量及伴随失火；催化器温度需使用诊断对应的模型值，不能直接用同名传感器值替代。`);
      if (code === 'P0420') plan.manualChecks.push(`${label}：已核对的诊断条件为模型温度 630–900 °C、冷却液温度大于 70 °C、转速 1100–3500 rpm，每个驾驶周期运行一次；仅供理解诊断启用条件。`);
      plan.once.push(`${label}：故障冻结帧和催化器监测结果（当前读取尚未实现）`);
      plan.gaps.push(`${label}：补充观察不能单独判定催化器损坏；氧传感器类型、读取方法及原文判断条件待核实。`);
    } else if (code === 'P0571' || code === '4340') {
      special.push('dme/brake-switch', 'psm/brake-switch');
      if (code === 'P0571') {
        special.push('dme/brake-test-switch', 'psm/vehicle-speed', 'dme/acceleration');
        plan.manualChecks.push(`${label}：对照踩下制动器前后的制动灯开关、制动测试开关实际值；检查霍尔传感器及线路供电，并核对 PSM 车速与 DME 实际加速度。`);
      }
      plan.manualChecks.push(`${label}：改动踏板或开关支架前，检查并记录制动灯开关调整值；同时核对制动灯表现和 DME P0571 / PSM 4340。`);
      plan.gaps.push(`${label}：开关调整值及传感器供电需人工检查；PSM 开关状态为补充观察，其余实际值的厂商读取方法尚未验证。`);
    }
    const extracted = MANUAL_CODE_CANDIDATES[code];
    const hasCuratedPlan = ids.length > 0 || special.length > 0;
    if (extracted) {
        for (const id of extracted.signalIds) {
          const candidate = MANUAL_SIGNAL_CANDIDATES.find(p => p.id === id);
          if (!candidate) continue;
          // Preserve every extracted need, including those beyond a curated plan.
          // Unreviewed additions do not silently enlarge a curated live batch.
          if (!hasCuratedPlan && candidate.standardParameterId && /^obd-can:7E[8-F]$/.test(dtc.moduleKey)) ids.push(candidate.standardParameterId);
          const item = catalog.find(p => p.parameterId === id);
          if (item) add(item, label);
        }
        plan.gaps.push(`${label}：${extracted.signalIds.length ? '已提取候选数据需求，仍需核对完整检查条件和本车读取对应。标准参数仅作辅助观察。' : '已登记原文核查进度，尚无可确认的数据对应。'}不能据此给出更换零件结论。`);
        if (!extracted.sourceModels.includes('981')) plan.gaps.push(`${label}：扩充收录；本车适用性待核实。`);
    } else if (!hasCuratedPlan) plan.gaps.push(`${label}：逐项检查内容尚未核实，所需数据待核实；不能依据代码标题自动认定参数或更换零件。`);
    for (const id of ids) {
      const item = catalog.find(p => p.moduleKey === dtc.moduleKey && p.parameterId === id);
      if (item) add(item, label);
      else plan.gaps.push(`${label}：${id} 的控制单元读取对应尚未核实。`);
    }
    for (const key of special) { const item = catalog.find(p => p.key === key); if (item) add(item, label); }
  }
  function add(item: AnalysisItem, label: string) {
    seen.set(item.key, item);
    (plan.associations[item.key] ??= []).push(label);
  }
  plan.items = [...seen.values()];
  plan.associations = Object.fromEntries(Object.entries(plan.associations).map(([k, v]) => [k, [...new Set(v)]]));
  plan.gaps = [...new Set(plan.gaps)]; plan.once = [...new Set(plan.once)]; plan.manualChecks = [...new Set(plan.manualChecks)];
  return plan;
}

export function validateAnalysisSelections(input: unknown): AnalysisSelection[] {
  if (!Array.isArray(input) || !input.length || input.length > ANALYSIS_BATCH_LIMIT) throw new Error(`每批请选择 1–${ANALYSIS_BATCH_LIMIT} 项数据（软件暂定限制）`);
  const selections: AnalysisSelection[] = input.map(s => {
    if (!s || typeof s !== 'object' || !/^obd-can:7E[8-F]$/.test(s.moduleKey) || !ANALYSIS_PARAMETERS.some(p => p.id === s.parameterId)) throw new Error('所选数据尚无已实现的读取方法');
    return { moduleKey: s.moduleKey, parameterId: s.parameterId };
  });
  if (new Set(selections.map(analysisKey)).size !== selections.length) throw new Error('同一单元的数据项目不能重复');
  return selections;
}
