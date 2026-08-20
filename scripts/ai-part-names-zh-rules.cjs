/**
 * AI 编制的保时捷/汽配中文名规则：把 name_en 压成短中文零件名。
 * 不调用任何在线翻译 API。输出 batch JSON 供 ai-part-names-zh.mjs --apply。
 *
 * Usage:
 *   node scripts/ai-part-names-zh-rules.mjs
 *   node scripts/ai-part-names-zh-rules.mjs --apply
 */
const fs = require("fs");
const path = require("path");
const { DatabaseSync } = require("node:sqlite");
const { pathToFileURL } = require("url");

const root = path.resolve(__dirname, "..");
const OUT = path.join(root, ".local", "design911-names", "zh-ai-batches", "batch-rules.json");

function hasHan(t) {
  return /[\u4e00-\u9fff]/.test(String(t || ""));
}
function compact(o) {
  return String(o || "").replace(/[.\s-]/g, "").toUpperCase();
}
function isPh(name, oem) {
  const n = String(name || "").trim();
  if (!n) return true;
  if (hasHan(n)) return false;
  const o = compact(oem);
  if (!o) return true;
  return compact(n) === o;
}
function stemEn(en) {
  return String(en || "")
    .replace(/\s*[.\-]?\s*Porsche\b.*/i, "")
    .replace(/\s+for\s+Porsche\b.*/i, "")
    .replace(/\s*[-–—]\s*Several Applications.*/i, "")
    .replace(/\s*\(PR\s*[^)]+\)/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** 先剥车型/颜色噪声，再短语替换（长词优先） */
function prep(en) {
  let s = stemEn(en);
  s = s
    .replace(/\b(left[- ]side|left hand|lh|l\.h\.)\b/gi, "LEFT")
    .replace(/\b(right[- ]side|right hand|rh|r\.h\.)\b/gi, "RIGHT")
    .replace(/\b(front)\b/gi, "FRONT")
    .replace(/\b(rear|back)\b/gi, "REAR")
    .replace(/\b(upper|top)\b/gi, "UPPER")
    .replace(/\b(lower|bottom)\b/gi, "LOWER")
    .replace(/\b(centre|center)\b/gi, "CENTRE")
    .replace(/\b(inner)\b/gi, "INNER")
    .replace(/\b(outer)\b/gi, "OUTER");
  // drop long color / finish suffixes when trailing
  s = s
    .replace(/\b(matt black|matte black|hi-gloss chrome|galvano silver|brilliant chrome|satin[- ]matt|metallic|agate grey|basalt black|anthracite|platinum|titanium|carrera red|aqua blue|achat grey|nubuck|leather|prime coated|prime)\b/gi, "")
    .replace(/\s+/g, " ")
    .trim();
  return s;
}

// 短语表：英文片段 → 中文（按长度降序匹配一次）
const PHRASES = [
  ["air conditioning (air con) condenser", "空调冷凝器"],
  ["air conditioning (air con) line o-ring seal", "空调管路O形圈"],
  ["air conditioning blower regulator", "空调鼓风机调速器"],
  ["air con compressor", "空调压缩机"],
  ["engine lid blower fan", "发动机盖鼓风机"],
  ["engine radiator", "发动机散热器"],
  ["engine coolant overflow hose", "冷却液溢流软管"],
  ["radiator water fan bracket", "散热器风扇支架"],
  ["radiator coolant vent line", "散热器排气软管"],
  ["radiator coolant top hose", "散热器上水管"],
  ["coolant radiator bottom hose", "散热器下水管"],
  ["water coolant radiator top hose", "散热器上水管"],
  ["fan housing and bracket for water radiator", "散热器风扇罩支架"],
  ["plastic support bracket for front coolant pipes", "前冷却管支架"],
  ["plastic bracket - supports front coolant distributor pipe", "前冷却分配管支架"],
  ["right-side coolant distributor pipe", "右冷却分配管"],
  ["front coolant hose", "前冷却软管"],
  ["blanking plug / bung for water radiator", "散热器堵盖"],
  ["spacer sleeve for radiator fan", "散热器风扇隔套"],
  ["rubber mounting/isolator for radiator fan", "散热器风扇橡胶垫"],
  ["mounting bush lower for radiator", "散热器下安装衬套"],
  ["mounting bush upper for radiator", "散热器上安装衬套"],
  ["captive nut for front radiator fan console", "散热器风扇支架卡母"],
  ["centre radiator sealing frame", "中央散热器密封框"],
  ["centre radiator upper rubber mounting", "中央散热器上橡胶垫"],
  ["centre radiator lower rubber mounting", "中央散热器下橡胶垫"],
  ["radiator water / coolant", "散热器"],
  ["heat exchanger for pdk transmission", "PDK变速箱热交换器"],
  ["gasket for transmission heat exchanger", "变速箱热交换器垫"],
  ["water coolant hose pdk gearbox", "PDK冷却软管"],
  ["rear bumper heat protection shield", "后保险杠隔热罩"],
  ["water drain hose b-pillar", "B柱排水软管"],
  ["left pressure line, condenser", "左冷凝器高压管"],
  ["pressure line, condenser", "冷凝器高压管"],
  ["ignition starter switch", "点火开关"],
  ["anti roll bar", "稳定杆"],
  ["anti-roll bar", "稳定杆"],
  ["control arm", "控制臂"],
  ["wishbone", "三角臂"],
  ["track rod", "横拉杆"],
  ["tie rod", "横拉杆"],
  ["ball joint", "球头"],
  ["wheel bearing", "轮毂轴承"],
  ["brake disc", "刹车盘"],
  ["brake pad", "刹车片"],
  ["brake pads", "刹车片"],
  ["brake caliper", "刹车卡钳"],
  ["brake line", "刹车油管"],
  ["brake hose", "刹车软管"],
  ["brake light", "刹车灯"],
  ["3rd brake light", "高位刹车灯"],
  ["additional 3rd brake light", "高位刹车灯"],
  ["parking brake", "驻车制动"],
  ["clutch", "离合器"],
  ["flywheel", "飞轮"],
  ["dual mass flywheel", "双质量飞轮"],
  ["oil filter", "机油滤清器"],
  ["air filter", "空气滤清器"],
  ["cabin filter", "空调滤芯"],
  ["fuel filter", "燃油滤清器"],
  ["spark plug", "火花塞"],
  ["ignition coil", "点火线圈"],
  ["timing chain", "正时链条"],
  ["water pump", "水泵"],
  ["thermostat", "节温器"],
  ["oil cooler", "机油冷却器"],
  ["intercooler", "中冷器"],
  ["turbocharger", "涡轮增压器"],
  ["catalytic converter", "三元催化器"],
  ["oxygen sensor", "氧传感器"],
  ["lambda sensor", "氧传感器"],
  ["mass air flow", "空气流量计"],
  ["throttle body", "节气门体"],
  ["intake manifold", "进气歧管"],
  ["exhaust manifold", "排气歧管"],
  ["muffler", "消声器"],
  ["silencer", "消声器"],
  ["drive shaft", "传动轴"],
  ["half shaft", "半轴"],
  ["cv joint", "万向节"],
  ["propshaft", "传动轴"],
  ["differential", "差速器"],
  ["gearbox", "变速箱"],
  ["transmission", "变速箱"],
  ["pdk", "PDK"],
  ["mechatronic", "机电单元"],
  ["alternator", "发电机"],
  ["starter", "启动机"],
  ["battery", "蓄电池"],
  ["radiator fan", "散热器风扇"],
  ["cooling fan", "冷却风扇"],
  ["coolant hose", "冷却液软管"],
  ["coolant pipe", "冷却水管"],
  ["coolant", "冷却液"],
  ["heater core", "暖风水箱"],
  ["heater hose", "暖风软管"],
  ["heater element", "加热元件"],
  ["heat exchanger", "热交换器"],
  ["heat deflector", "隔热板"],
  ["heat protection", "隔热"],
  ["heat shield", "隔热罩"],
  ["retaining frame", "固定框"],
  ["sealing frame", "密封框"],
  ["expansion rivet", "膨胀铆钉"],
  ["o-ring", "O形圈"],
  ["oring", "O形圈"],
  ["gasket", "垫片"],
  ["seal ring", "密封圈"],
  ["oil seal", "油封"],
  ["shaft seal", "轴封"],
  ["dust boot", "防尘套"],
  ["rubber mounting", "橡胶垫"],
  ["rubber mount", "橡胶垫"],
  ["mounting bush", "安装衬套"],
  ["bushing", "衬套"],
  ["spacer sleeve", "隔套"],
  ["spacer", "垫片/隔套"],
  ["captive nut", "卡母"],
  ["speed nut", "卡母"],
  ["fillister hd. screw", "圆柱头螺钉"],
  ["fillister head screw", "圆柱头螺钉"],
  ["hexagon nut", "六角螺母"],
  ["hexagon bolt", "六角螺栓"],
  ["socket head", "内六角"],
  ["pan head screw", "盘头螺钉"],
  ["self-tapping screw", "自攻螺钉"],
  ["blind rivet", "抽芯铆钉"],
  ["pop rivet", "抽芯铆钉"],
  ["clip", "卡扣"],
  ["retainer", "卡扣"],
  ["harness", "线束"],
  ["wiring harness", "线束"],
  ["cable harness", "线束"],
  ["adapter cable", "转接电缆"],
  ["antenna connecting cable", "天线连接线"],
  ["antenna booster", "天线放大器"],
  ["antenna", "天线"],
  ["amplifier", "功放"],
  ["loudspeaker", "扬声器"],
  ["speaker", "扬声器"],
  ["headlight", "大灯"],
  ["headlamp", "大灯"],
  ["tail light", "尾灯"],
  ["taillight", "尾灯"],
  ["fog light", "雾灯"],
  ["indicator", "转向灯"],
  ["turn signal", "转向灯"],
  ["side marker", "示廓灯"],
  ["number plate light", "牌照灯"],
  ["license plate light", "牌照灯"],
  ["door lock", "门锁"],
  ["lock cylinder", "锁芯"],
  ["door handle", "门把手"],
  ["window regulator", "玻璃升降器"],
  ["window lifter", "玻璃升降器"],
  ["mirror glass", "镜片"],
  ["mirror base", "后视镜底座"],
  ["exterior mirror", "外后视镜"],
  ["wing mirror", "外后视镜"],
  ["interior mirror", "内后视镜"],
  ["sun visor", "遮阳板"],
  ["seat belt", "安全带"],
  ["seatbelt", "安全带"],
  ["airbag", "气囊"],
  ["steering wheel", "方向盘"],
  ["steering rack", "转向机"],
  ["power steering", "助力转向"],
  ["shock absorber", "减震器"],
  ["damper", "减震器"],
  ["coil spring", "螺旋弹簧"],
  ["strut mount", "减震器顶胶"],
  ["strut", "减震支柱"],
  ["hub carrier", "转向节"],
  ["knuckle", "转向节"],
  ["wheel hub", "轮毂"],
  ["alloy wheel", "铝合金轮毂"],
  ["complete summer wheels", "夏季轮毂轮胎总成"],
  ["complete winter wheels", "冬季轮毂轮胎总成"],
  ["floor mat", "脚垫"],
  ["floor mats", "脚垫"],
  ["sill trim", "门槛饰条"],
  ["door sill", "门槛"],
  ["door trim panel", "门内饰板"],
  ["door trim", "门饰板"],
  ["door panel", "门板"],
  ["door seal", "车门密封条"],
  ["weatherstrip", "密封条"],
  ["sealing strip", "密封条"],
  ["trim molding", "饰条"],
  ["trim moulding", "饰条"],
  ["trim strip", "饰条"],
  ["decorative strip", "饰条"],
  ["badge", "徽标"],
  ["emblem", "徽标"],
  ["decal", "贴纸"],
  ["lettering", "字标"],
  ["grille", "格栅"],
  ["air intake", "进气口"],
  ["air duct", "风道"],
  ["air guide", "导风板"],
  ["spoiler", "扰流板"],
  ["rear spoiler", "后扰流板"],
  ["front spoiler", "前扰流板"],
  ["diffuser", "扩散器"],
  ["bumper cover", "保险杠蒙皮"],
  ["bumper", "保险杠"],
  ["fender", "翼子板"],
  ["wing", "翼子板"],
  ["hood", "发动机盖"],
  ["bonnet", "发动机盖"],
  ["engine lid", "发动机盖"],
  ["trunk lid", "行李箱盖"],
  ["boot lid", "行李箱盖"],
  ["convertible top", "软顶"],
  ["soft top", "软顶"],
  ["roof panel", "车顶板"],
  ["a-pillar", "A柱"],
  ["b-pillar", "B柱"],
  ["c-pillar", "C柱"],
  ["windshield", "前挡风玻璃"],
  ["windscreen", "前挡风玻璃"],
  ["wiper blade", "雨刮片"],
  ["wiper arm", "雨刮臂"],
  ["wiper motor", "雨刮电机"],
  ["washer pump", "喷水泵"],
  ["washer nozzle", "喷嘴"],
  ["fuel pump", "燃油泵"],
  ["fuel tank", "油箱"],
  ["fuel filler", "加油口"],
  ["fuel cap", "油箱盖"],
  ["fuel line", "燃油管"],
  ["evap", "蒸发排放"],
  ["charcoal canister", "活性炭罐"],
  ["vacuum pump", "真空泵"],
  ["brake booster", "刹车真空助力器"],
  ["master cylinder", "制动总泵"],
  ["abs unit", "ABS泵"],
  ["abs hydraulic", "ABS液压单元"],
  ["sensor", "传感器"],
  ["switch", "开关"],
  ["relay", "继电器"],
  ["fuse", "保险丝"],
  ["control unit", "控制单元"],
  ["ecu", "控制单元"],
  ["module", "模块"],
  ["actuator", "执行器"],
  ["solenoid", "电磁阀"],
  ["valve", "阀门"],
  ["pump", "泵"],
  ["hose", "软管"],
  ["pipe", "管子"],
  ["tube", "管子"],
  ["line", "管路"],
  ["duct", "风道"],
  ["housing", "壳体"],
  ["cover", "盖板"],
  ["lid", "盖"],
  ["cap", "盖"],
  ["plug", "堵盖"],
  ["bung", "堵盖"],
  ["grommet", "护线套"],
  ["boot", "防尘套"],
  ["sleeve", "套管"],
  ["clamp", "卡箍"],
  ["bracket", "支架"],
  ["support", "支架"],
  ["holder", "固定座"],
  ["mounting", "安装件"],
  ["mount", "安装座"],
  ["console", "托架"],
  ["frame", "框架"],
  ["panel", "面板"],
  ["plate", "板"],
  ["shield", "护板"],
  ["guard", "护板"],
  ["deflector", "导流板"],
  ["liner", "内衬"],
  ["lining", "内衬"],
  ["insulation", "隔音棉"],
  ["foam", "泡沫件"],
  ["foam part", "泡沫件"],
  ["pad", "垫"],
  ["mat", "垫"],
  ["tray", "托盘"],
  ["collecting tray", "集水槽"],
  ["water collecting tray", "集水槽"],
  ["drain hose", "排水软管"],
  ["drain pipe", "排水管"],
  ["water pipe", "水管"],
  ["water hose", "水管"],
  ["water tube", "水管"],
  ["water pump tube", "水泵水管"],
  ["cross tube", "横管"],
  ["distributor tube", "分配管"],
  ["distributor pipe", "分配管"],
  ["overflow hose", "溢流软管"],
  ["vent line", "排气管路"],
  ["bleeder", "排气阀"],
  ["repair kit", "修理包"],
  ["assembly kit", "装配套件"],
  ["assembly frame", "装配框"],
  ["operating unit", "操作单元"],
  ["control panel", "控制面板"],
  ["ashtray", "烟灰缸"],
  ["cup holder", "杯架"],
  ["glove box", "手套箱"],
  ["glovebox", "手套箱"],
  ["centre console", "中央扶手箱"],
  ["armrest", "扶手"],
  ["backrest", "靠背"],
  ["headrest", "头枕"],
  ["seat cover", "座套"],
  ["upholstery", "内饰皮"],
  ["carpet", "地毯"],
  ["floor covering", "地板覆盖件"],
  ["accelerator pedal", "油门踏板"],
  ["brake pedal", "刹车踏板"],
  ["clutch pedal", "离合踏板"],
  ["pedal", "踏板"],
  ["handbrake", "手刹"],
  ["parking brake lever", "手刹杆"],
  ["gear lever", "换挡杆"],
  ["selector lever", "换挡杆"],
  ["shift paddle", "换挡拨片"],
  ["steering column", "转向柱"],
  ["ignition lock", "点火锁"],
  ["key", "钥匙"],
  ["remote control", "遥控器"],
  ["transmitter", "发射器"],
  ["receiver", "接收器"],
  ["camera", "摄像头"],
  ["parking aid", "泊车辅助"],
  ["pdc", "泊车雷达"],
  ["ultrasonic", "超声波传感器"],
  ["rain sensor", "雨量传感器"],
  ["light sensor", "光线传感器"],
  ["acceleration sensor", "加速度传感器"],
  ["yaw rate", "横摆传感器"],
  ["wheel speed sensor", "轮速传感器"],
  ["abs sensor", "轮速传感器"],
  ["crankshaft sensor", "曲轴传感器"],
  ["camshaft sensor", "凸轮轴传感器"],
  ["knock sensor", "爆震传感器"],
  ["temperature sensor", "温度传感器"],
  ["pressure sensor", "压力传感器"],
  ["oil pressure", "机油压力"],
  ["oil level", "机油液位"],
  ["dipstick", "机油尺"],
  ["filler neck", "加油颈"],
  ["expansion tank", "膨胀水箱"],
  ["reservoir", "储液罐"],
  ["washer bottle", "玻璃水壶"],
  ["washer fluid", "玻璃水"],
  ["radiator", "散热器"],
  ["condenser", "冷凝器"],
  ["evaporator", "蒸发器"],
  ["blower", "鼓风机"],
  ["fan", "风扇"],
  ["compressor", "压缩机"],
  ["dryer", "干燥瓶"],
  ["receiver drier", "干燥瓶"],
  ["expansion valve", "膨胀阀"],
  ["orifice tube", "节流管"],
  ["heater", "暖风"],
  ["climate", "空调"],
  ["hvac", "空调"],
  ["filter", "滤清器"],
  ["strainer", "滤网"],
  ["screen", "滤网"],
  ["mesh", "滤网"],
  ["nut", "螺母"],
  ["bolt", "螺栓"],
  ["screw", "螺钉"],
  ["washer", "垫圈"],
  ["rivet", "铆钉"],
  ["pin", "销"],
  ["bearing pin", "销轴"],
  ["dowel", "定位销"],
  ["stud", "双头螺栓"],
  ["thread", "螺纹件"],
  ["adapter", "转接件"],
  ["extension", "延长件"],
  ["connector", "接头"],
  ["coupling", "联轴器"],
  ["union", "活接头"],
  ["fitting", "管接头"],
  ["joint", "接头"],
  ["flange", "法兰"],
  ["collar", "卡圈"],
  ["ring", "环"],
  ["band", "箍带"],
  ["strap", "绑带"],
  ["tie", "扎带"],
  ["cable tie", "扎带"],
  ["tape", "胶带"],
  ["foam tape", "泡棉胶带"],
  ["adhesive", "胶粘剂"],
  ["sealant", "密封胶"],
  ["grease", "润滑脂"],
  ["oil", "机油"],
  ["fluid", "油液"],
  ["kit", "套件"],
  ["set", "套装"],
  ["pair", "一对"],
  ["left", "左"],
  ["right", "右"],
  ["front", "前"],
  ["rear", "后"],
  ["upper", "上"],
  ["lower", "下"],
  ["inner", "内"],
  ["outer", "外"],
  ["centre", "中央"],
  ["center", "中央"],
];

// sort longest first once
PHRASES.sort((a, b) => b[0].length - a[0].length);

function sidePrefix(s) {
  const bits = [];
  if (/\bLEFT\b/.test(s)) bits.push("左");
  if (/\bRIGHT\b/.test(s)) bits.push("右");
  if (/\bFRONT\b/.test(s) && !/前/.test("")) bits.push("前");
  if (/\bREAR\b/.test(s)) bits.push("后");
  if (/\bUPPER\b/.test(s)) bits.push("上");
  if (/\bLOWER\b/.test(s)) bits.push("下");
  if (/\bINNER\b/.test(s)) bits.push("内");
  if (/\bOUTER\b/.test(s)) bits.push("外");
  if (/\bCENTRE\b/.test(s)) bits.push("中央");
  return bits;
}

function translateEn(en) {
  const raw = String(en || "").trim();
  if (!raw) return "零件";
  let s = prep(raw);
  const sides = sidePrefix(s);
  // remove side tokens from match string
  let work = s
    .replace(/\b(LEFT|RIGHT|FRONT|REAR|UPPER|LOWER|INNER|OUTER|CENTRE)\b/g, " ")
    .replace(/[-–,]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  let zhCore = null;
  const lower = work.toLowerCase();
  for (const [enPhrase, zh] of PHRASES) {
    if (lower.includes(enPhrase.toLowerCase())) {
      zhCore = zh;
      break;
    }
  }
  if (!zhCore) {
    // very short fallbacks
    if (/^bracket$/i.test(work)) zhCore = "支架";
    else if (/^cover$/i.test(work)) zhCore = "盖板";
    else if (/^trim$/i.test(work)) zhCore = "饰板";
    else if (/^gasket$/i.test(work)) zhCore = "垫片";
    else if (/^harness$/i.test(work)) zhCore = "线束";
    else if (/^seal$/i.test(work)) zhCore = "密封件";
    else if (/^hose$/i.test(work)) zhCore = "软管";
    else if (/^pipe$/i.test(work) || /^tube$/i.test(work)) zhCore = "管子";
    else if (/^cap$/i.test(work)) zhCore = "盖";
    else if (/^holder$/i.test(work)) zhCore = "固定座";
    else if (/^support$/i.test(work)) zhCore = "支架";
    else if (/^frame$/i.test(work)) zhCore = "框架";
    else if (/^panel$/i.test(work)) zhCore = "面板";
    else if (/^lining$/i.test(work)) zhCore = "内衬";
    else if (/^foam/i.test(work)) zhCore = "泡沫件";
    else zhCore = null;
  }

  if (!zhCore) {
    // last resort: keep a short descriptive Chinese from keywords already partially matched
    // Prefer not to leave English in UI
    zhCore = "配件";
  }

  // compose side + core without duplicating if core already has side word
  const prefix = sides.filter((x) => !zhCore.includes(x)).join("");
  let out = prefix + zhCore;
  // tidy
  out = out.replace(/配件配件/g, "配件").slice(0, 40);
  return out;
}

function main() {
  const apply = process.argv.includes("--apply");
  const d = new DatabaseSync(path.join(root, ".local", "garage.db"));
  const rows = d
    .prepare(
      "SELECT sku, oem_number, name_en, name_zh FROM parts WHERE generation = '981' AND name_en IS NOT NULL AND TRIM(name_en) != ''",
    )
    .all();
  const pending = rows.filter((p) => isPh(p.name_zh, p.oem_number));
  const batch = [];
  const byEn = {};
  let generic = 0;
  for (const p of pending) {
    const zh = translateEn(p.name_en);
    if (zh === "配件") generic++;
    batch.push({ sku: p.sku, name_zh: zh, en: p.name_en });
    const st = stemEn(p.name_en).toLowerCase();
    if (st && !byEn[st]) byEn[st] = zh;
  }
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(batch, null, 2));
  fs.writeFileSync(
    path.join(root, ".local", "design911-names", "zh-ai-batches", "batch-rules-by-en.json"),
    JSON.stringify({ by_en: byEn }, null, 2),
  );
  console.log(
    JSON.stringify(
      {
        pending: pending.length,
        wrote: batch.length,
        generic_配件: generic,
        out: OUT,
      },
      null,
      2,
    ),
  );
  // sample
  const samples = [
    "Engine radiator",
    "harness",
    "bracket",
    "Alloy wheel Black",
    "1 set of brake pads for disc brakes",
    "retaining frame",
    "Air con compressor",
  ];
  for (const s of samples) {
    console.log("SAMPLE", s, "=>", translateEn(s));
  }
  d.close();
  if (apply) {
    const { spawnSync } = require("child_process");
    const r = spawnSync(
      process.execPath,
      [path.join(root, "scripts", "ai-part-names-zh.mjs"), "--apply", OUT],
      { stdio: "inherit", cwd: root },
    );
    process.exit(r.status || 0);
  }
}

main();