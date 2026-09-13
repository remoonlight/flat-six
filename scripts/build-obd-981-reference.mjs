import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = p => JSON.parse(fs.readFileSync(path.join(root, p), 'utf8'));
const source = 'data/seed/dtc/manuals/by-code/codes.json';
const observed = [
  ['srs', 'SRS(安全气囊)'], ['gateway', '网关'],
  ['dme', 'ECM/DME(发动机控制模块)'], ['pdk', 'TCM/PDK(自动变速箱控制模块)'],
  ['selector', '选档杆'], ['instrument', 'IC(仪表)'], ['steering', '转向盘电子设备'],
];
const modules = observed.map(([key, name], i) => ({ key, name, order: i + 1,
  orderEvidence: 'X431 V24.58 / 981 默认列表首屏；2026-09-13 ui-after-back.xml；仅首屏顺序已核实' }));
const checks = {
  P0571: ['核对是否有制动灯常亮、PSM 故障或起停禁用投诉，并核对 PSM 是否同时记录 4340。', '操作踏板轴承座或制动灯开关支架之前，先检查制动灯开关调整值并记入工单。'],
  '4340': ['核对制动灯常亮投诉及仪表 PSM / 起停提示，并核对 DME 是否同时记录 P0571。', '调整或改动踏板、开关支架之前，先检查制动灯开关调整值并记入工单。'],
};
checks.P0571.push('观察踩下制动器时制动灯开关和制动测试开关的实际值是否变化；检查霍尔传感器及线路供电。', '核对 PSM 中记录的车速与 DME 计算的实际加速度；不要用其他单元的同名数据代替。');
checks.P0420 = ['核对燃油质量和伴随失火；仅凭一个温度读数不能判定催化器损坏。', '区分诊断模型温度与温度传感器读数；诊断是否运行还取决于冷却液温度、发动机转速和驾驶周期。'];
const readableTitles = {P0300:'检测到随机或多缸失火',P0420:'缸列 1 催化转化器效率不足'};
for (let cylinder=1;cylinder<=6;cylinder++) readableTitles[`P030${cylinder}`]=`检测到第 ${cylinder} 缸失火`;
const manualEntries = read(source).codes.filter(c => c.models?.some(m => ['981','982'].includes(m))).map(c => ({
  code: c.code,
  title: readableTitles[c.code] || c.title_zh || c.en_gloss || c.code,
  description: c.context_zh || '',
  checks: checks[c.code] || [],
  source: c.pages.find(p => p.model === '981' || p.model === '982')?.file ?? null,
  sourcePages: c.pages.filter(p => p.model === '981' || p.model === '982'),
  sourceModels: c.models.filter(m => m === '981' || m === '982'),
  inspectionEvidence: c.code === 'P0571' ? [{ model: '981', file: '981_Boxster BoxsterS BoxsterGTS.pdf', page: 3543 }, { model: '981', file: '981_Boxster BoxsterS BoxsterGTS.pdf', page: 4034 }] : c.code === '4340' ? [{ model: '981', file: '981_Boxster BoxsterS BoxsterGTS.pdf', page: 3543 }] : c.code === 'P0420' ? [{ model: '981', file: '981_Boxster BoxsterS BoxsterGTS.pdf', page: 3999 }] : [],
  applicability: c.models.includes('981') ? '来源含 981；具体控制单元、故障子类型和本车适用条件仍需核实' : '扩充收录；本车适用性待核实',
  sourceVariants: ['981','982'].filter(m => c.models.includes(m)).map(model => ({ model, pages: c.pages.filter(p => p.model === model), interpretation: '原索引按基础代码合并，不能据此认定不同车型或子类型的检查条件相同' })),
  verification: c.verify || 'unverified',
  moduleHint: c.code === '4340' ? 'PSM' : c.code === 'P0571' ? 'DME' : null,
}));
if (manualEntries.length !== 1106 || manualEntries.some(c => !c.sourcePages.length || c.sourcePages.some(p => !['981','982'].includes(p.model)))) throw new Error('Invalid unified manual references');
const out = {
  schemaVersion: 1, vehicle: '2014 Boxster S (981) PDK',
  source, moduleOrderComplete: false,
  knowledgeModels: ['981', '982'],
  notes: [
    '仅归档已核实的 X431 默认首屏顺序；后续单元及标准 OBD 响应地址与厂商模块的对应关系待核实，不用字母排序冒充 X431 默认顺序。',
    '故障题义索引不等于完整检查流程。checks 为空时须显示检查项未核实，并保留原手册页码；不得生成替换零件结论。',
    '存在 translated-from-en 条目，原 PDF 中文字体 CMap 损坏，不能据此宣称已核实对应页完整诊断步骤。',
  ], modules, manualEntries,
};
fs.mkdirSync(path.join(root, 'data/seed/obd'), { recursive: true });
fs.writeFileSync(path.join(root, 'data/seed/obd/981-reference.json'), JSON.stringify(out, null, 2) + '\n');
console.log(`981 reference: ${manualEntries.length} codes, ${Object.keys(checks).length} verified inspection entries, ${modules.length} observed module positions`);
