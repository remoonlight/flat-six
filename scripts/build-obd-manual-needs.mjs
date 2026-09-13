/** Convert reviewed extraction structure to conservative, source-traceable candidates.
 * Never turns parsed thresholds into diagnosis rules or vendor commands.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
const root = path.resolve(import.meta.dirname, '..');
const input = path.resolve(root, process.argv[2] ?? '.local/obd-analysis/manual/requirements.json');
const rows = JSON.parse(fs.readFileSync(input, 'utf8'));
for (const [source, target] of [['source-inventory.json','manual-source-inventory.json'],['extra-codes.json','manual-extra-codes.pending.json']]) {
  const file = path.join(path.dirname(input), source);
  if (fs.existsSync(file)) fs.copyFileSync(file, path.join(root,'data/seed/obd',target));
}
if (!Array.isArray(rows) || rows.length < 1106) throw new Error('manual_audit_incomplete_index');
const knownCodes = JSON.parse(fs.readFileSync(path.join(root, 'data/seed/dtc/manuals/by-code/codes.json'), 'utf8')).codes.filter(c => c.models.some(m => ['981','982'].includes(m)));
if (knownCodes.some(c => !rows.some(r => r.code === c.code))) throw new Error('missing_index_code');
// Keep conditions/possible causes in the research record as well: some relevant
// data (e.g. fuel-rail actual/target pressure) appears there, not in check steps.
const rowByCode = new Map(rows.map(r => [r.code, r]));
for (const [model, file] of [['981','981_grouped.json'],['982','982_grouped.json']]) {
  const groupedPath = path.join(path.dirname(input), 'extract', file);
  if (!fs.existsSync(groupedPath)) continue;
  for (const group of Object.values(JSON.parse(fs.readFileSync(groupedPath,'utf8')))) {
    if (!/^[PBCU][0-9A-F]{4}(?:[0-9A-F]{2})?$/.test(group.fullCode ?? '')) continue;
    const row = rowByCode.get(group.fullCode.slice(0,5));
    if (!row) continue;
    for (const [role, sections] of Object.entries(group.sections ?? {})) for (const section of sections) {
      row.needs ??= [];
      row.needs.push({name:section.text,condition:section.text,kind:'unverified',moduleHint:group.module,fullCode:group.fullCode,role,
        evidence:{file:`${model}_Boxster BoxsterS BoxsterGTS.pdf`,page:section.page},confidence:'extracted-candidate'});
    }
  }
}
// Normalized signal families, matching actual extracted body evidence only, not DTC titles.
const definitions = [
  ['rpm','发动机转速','rpm',/发动机转速|engine speed|engine rpm/i,'rpm'],
  ['coolant','冷却液温度','°C',/冷却液温度|coolant temperature/i,'coolant'],
  ['engine-temp','发动机温度','°C',/发动机温度|engine temperature/i],
  ['load','发动机计算负荷','%',/发动机负荷|engine load/i,'load'],
  ['voltage','控制单元供电电压','V',/控制单元.{0,8}电压|control (?:unit|module) voltage|battery voltage|电池电压|蓄电池电压/i,'voltage'],
  ['sensor-supply','传感器或线路供电电压','V',/供电|电源电压|supply voltage|power supply/i],
  ['intake-temp','进气温度','°C',/进气温度|intake air temperature/i,'intake-temp'],
  ['maf','空气质量流量','g/s',/空气流量|air mass|mass air flow/i,'maf'],
  ['map','进气歧管压力','kPa',/进气歧管.{0,6}压力|intake manifold pressure/i,'map'],
  ['fuel-rail-pressure','燃油轨压力实际值与目标值','',/燃油.{0,6}压力|燃油高压|fuel.{0,30}pressure|rail pressure/i],
  ['fuel-trim','燃油修正或混合气自适应值','',/燃油修正|混合气.{0,6}(自适应|调节)|fuel trim|mixture adaptation/i],
  ['lambda','氧传感器及空燃比信号','',/氧传感|lambda|oxygen sensor|air.fuel ratio/i],
  ['catalyst-model','催化器诊断温度','°C',/催化.{0,8}温度|cataly.{0,25}temperature/i],
  ['camshaft','凸轮轴目标角度与实际角度','°',/凸轮轴|camshaft/i],
  ['crankshaft','曲轴位置信号或同步状态','',/曲轴|crankshaft/i],
  ['misfire','各缸失火计数或缺火率','',/缺火率|失火计数|缺火计数|misfire (?:rate|counter|count)/i],
  ['torque','发动机扭矩','Nm',/扭矩|engine torque/i],
  ['throttle','节气门位置及目标值','%',/节气门|throttle (?:valve|position|angle)/i,'throttle'],
  ['pedal','油门踏板位置','%',/油门踏板|accelerator pedal/i],
  ['brake-switch','制动灯开关及制动测试开关状态','',/制动灯开关|制动测试开关|brake (?:light |test )?switch/i],
  ['brake-pressure','制动压力','',/制动压力|brake pressure/i],
  ['speed','所属单元记录的车速','km/h',/车速|vehicle speed|road speed/i],
  ['wheel-speeds','各车轮轮速','km/h',/轮速|wheel speed/i],
  ['acceleration','控制单元记录的加速度','',/加速度|acceleration|deceleration/i],
  ['fuel-level','燃油液位','',/燃油液位|油箱液位|fuel level|tank level/i],
  ['oil-temp','机油或变速箱油温','°C',/油温|oil temperature|transmission fluid temperature/i],
  ['oil-pressure','油压','',/油压|oil pressure/i],
  ['gear','实际挡位与目标挡位','',/挡位|档位|gear (?:position|engaged|actual)|selected gear/i],
  ['clutch','离合器位置、压力或滑差','',/离合器|clutch/i],
  ['shaft-speed','变速箱输入轴及输出轴转速','rpm',/输入轴|输出轴|input (?:shaft )?speed|output (?:shaft )?speed/i],
  ['steering','转向角及转向状态','',/转向角|steering angle/i],
  ['boost','增压压力实际值与目标值','',/增压压力|boost pressure|charge pressure/i],
  ['purge','油箱通风或蒸发排放状态','',/油箱通风|蒸发|tank vent|purge valve|evaporative/i],
  ['fan','风扇请求与实际状态','',/风扇|fan (?:speed|actuation|activation)/i],
  ['ac-pressure','空调制冷剂压力','',/制冷剂压力|refrigerant pressure/i],
  ['can-state','控制单元通信状态','',/CAN.{0,12}(消息|信号)|CAN (?:message|signal)|communication fault|通信故障/i],
  ['runtime','发动机启动后运行时间','s',/启动后的时间|time since engine start/i],
  ['fuel-quantity','喷油量或供油量','',/燃油量|供给量|injection quantity|delivery quantity/i],
  ['ignition-state','点火及发动机运行状态','',/点火开关|ignition on|engine running/i],
  ['ambient','环境温度或气压','',/环境温度|ambient (?:temperature|pressure)|大气压力|atmospheric pressure/i],
];
const signals = new Map(), codeMap = {}, audits = [];
for (const row of rows) {
  const ids = new Set();
  const evidence = [];
  for (const need of row.needs ?? []) {
    const source = need.evidence;
    if (!source?.file || !Number.isInteger(source.page)) throw new Error(`missing_need_source:${row.code}`);
    if ((source.file.startsWith('981_Boxster') && source.page >= 3831 && source.page <= 3840) || (source.file.startsWith('982_Boxster') && source.page >= 4225 && source.page < 4242)) throw new Error(`toc_used_as_need_source:${row.code}:${source.page}`);
    const body = `${need.name ?? ''} ${need.condition ?? ''}`;
    const hint = String(need.moduleHint ?? '待确认单元').trim();
    const moduleKey = /DME|ECM|engine/i.test(hint) ? 'dme' : /PDK|transmission|TCM/i.test(hint) ? 'pdk' : /PSM|ABS/i.test(hint) ? 'psm' : 'manual-unit-' + createHash('sha256').update(hint).digest('hex').slice(0,8);
    const moduleName = moduleKey === 'dme' ? 'DME（发动机）' : moduleKey === 'pdk' ? 'PDK（变速箱）' : moduleKey === 'psm' ? 'PSM（车身稳定）' : /selector lever/i.test(hint) ? '选档杆' : /valid for all/i.test(hint) ? '多个单元（待定位）' : hint === 'CAN' ? '车载网络（单元待定位）' : hint;
    for (const [family,name,unit,match,standard] of definitions) {
      if (!match.test(body)) continue;
      const id = `manual-${moduleKey}-${family}`;
      signals.set(id, { id, name, unit, moduleKey, moduleName, ...(standard && moduleKey === 'dme' ? {standardParameterId:standard} : {}) });
      ids.add(id);
      evidence.push({ signalId:id, file:source.file, page:source.page, fullCode:need.fullCode ?? null, role:need.role ?? null, category:'extracted-candidate' });
    }
  }
  const sourceModels = (row.models ?? []).filter(m => ['981','982'].includes(m));
  const status = (row.needs ?? []).length ? 'extracted-candidate' : 'needs-review';
  codeMap[row.code] = {signalIds:[...ids].sort(),status,sourceModels};
  const reviewNeeds = [...new Map((row.needs ?? []).map(n => {
    const item={name:n.name,kind:n.kind,role:n.role ?? null,moduleHint:n.moduleHint ?? null,condition:n.condition ?? null,evidence:n.evidence,fullCode:n.fullCode ?? null,uncertainGlyph:Boolean(n.uncertainGlyph),confidence:'extracted-candidate'};
    return [JSON.stringify(item),item];
  })).values()];
  audits.push({code:row.code,sourceModels,status,signals:[...ids].sort(),evidence:[...new Map(evidence.map(e=>[JSON.stringify(e),e])).values()],sourceVariants:row.variants ?? [],reviewNeeds,gaps:[...new Set(['提取结果待逐条核对，未等同于已完成的手册核查。','厂商读取对应、本车适用条件和诊断阈值未完成验证。',...(!ids.size?['尚未建立可用的数据参数对应。']:[])])]});
}
const signalList = [...signals.values()].sort((a,b)=>a.id.localeCompare(b.id));
const summary = {indexedCodes:audits.length,withSignalCandidates:audits.filter(a=>a.signals.length).length,withoutSignalCandidates:audits.filter(a=>!a.signals.length).length,signalCandidates:signalList.length,fullyVerifiedCodes:0};
fs.writeFileSync(path.join(root,'data/seed/obd/manual-data-needs.json'), JSON.stringify({schemaVersion:1,generated:'2026-09-13',note:'统一981故障库，包含982。提取候选与已核实检查项分开，不包含可执行厂商协议。',summary,signals:signalList,codes:audits},null,2)+'\n');
const ts = `// Generated by scripts/build-obd-manual-needs.mjs. Original evidence stays in data/seed/obd.\nexport type ManualSignalCandidate = { id: string; name: string; moduleKey: string; moduleName: string; unit: string; standardParameterId?: string };\nexport type ManualCodeCandidates = { signalIds: string[]; status: string; sourceModels: string[] };\nexport const MANUAL_SIGNAL_CANDIDATES: ManualSignalCandidate[] = ${JSON.stringify(signalList,null,2)};\nexport const MANUAL_CODE_CANDIDATES: Record<string, ManualCodeCandidates> = ${JSON.stringify(codeMap,null,2)};\n`;
fs.writeFileSync(path.join(root,'packages/domain/src/obd-manual-needs.generated.ts'),ts);
console.log(JSON.stringify(summary));
const byId=new Map(signalList.map(s=>[s.id,s]));
const titleMap=new Map(knownCodes.map(c=>[c.code,c.title_zh]));
const md=['# 统一 981 故障库：逐码数据需求清单','','更新：2026-09-13。包含 982；不分设车型库。','',
  `已登记 ${audits.length} 个索引代码，其中 ${summary.withSignalCandidates} 个提取到规范化候选数据，${summary.withoutSignalCandidates} 个尚未建立参数对应。`, '',
  '以下为原文提取候选，不是已确认的厂家读取协议，也不是自动诊断规则。原始条件、车型分支、页码和人工检查原句保存在 data/seed/obd/manual-data-needs.json；产品中隐藏出处。标准 OBD 基础参数需以各应答单元实际支持列表确认。','',
  '## 仍需收敛核查','','- 同码可能包含不同故障子类型，不能合并判断阈值。','- 982 条目并入资料库不代表本车支持；条件和厂商参数仍需逐项确认。','- 自动提取仍有残字及跨页风险；不能把已提取数量称为完整核查数。','- 一次性信息、人工检查和诊断使能条件留在研究记录，不当作连续采集参数。','',
  '## 逐码清单',''];
for(const a of audits){md.push(`### ${a.code} — ${titleMap.get(a.code)||'问题名称待核实'}`,'',a.signals.length?`候选数据：${a.signals.map(id=>{const s=byId.get(id);return `${s.moduleName} / ${s.name}`;}).join('；')}。`:'暂无可确认的数据对应，需核对诊断正文及人工检查项目。','', '状态：待逐项核实读取对应、适用条件和完整检查步骤。','');}
fs.writeFileSync(path.join(root,'docs/research/obd-manual-data-needs.md'),md.join('\n'));
