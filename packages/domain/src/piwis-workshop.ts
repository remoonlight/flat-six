/** Project-owned research summaries. Menu evidence never qualifies an ECU operation. */
export type WorkshopGeneration = "981" | "982";
export type WorkshopSection = "maintenance" | "programming";
export type RollerPersistence = "ignition-cycle" | "persistent";
export const WORKSHOP_PROGRAMMING_ECUS = ["DME", "PDK", "Gateway", "Airbag", "功放", "左前灯", "右前灯", "左LED前灯", "右LED前灯", "仪表", "PCM", "前车身", "后车身", "驻车制动"] as const;
export type WorkshopProgrammingEcu = typeof WORKSHOP_PROGRAMMING_ECUS[number];
export type WorkshopFunction = {
  id: string;
  section: WorkshopSection;
  name: string;
  german: string;
  ecu: string;
  summary: string;
  source: string;
  evidence: "platform-menu" | "model-rules" | "platform-rules" | "service-bulletin";
  familyNotes?: Partial<Record<WorkshopGeneration, string>>;
  prerequisites: readonly string[];
  steps: readonly string[];
  dependencies: readonly string[];
};

const IDENTITY = "核对 VIN、年款、发动机／变速箱配置及 ECU 硬件、软件、诊断变体。";
const ROUTINE = "补齐该变体的会话、访问条件、精确请求、回读和退出／恢复流程。";
const VERIFY = "独立核实操作结果，保存前后记录；结果不明时进入待核验状态。";
function maintenance(id: string, name: string, german: string, ecu: string, summary: string,
  source = "9x1/cts-ecu.xml", prerequisites: string[] = [], dependencies: string[] = [], steps: string[] = []): WorkshopFunction {
  return { id, name, german, ecu, summary, source, section: "maintenance", evidence: "platform-menu",
    prerequisites: [IDENTITY, ROUTINE, ...prerequisites],
    steps: ["读取当前状态及相关故障，保存操作前记录。", ...steps, VERIFY], dependencies };
}

function programming(id: string, name: string, ecu: WorkshopProgrammingEcu, source: string,
  evidence: WorkshopFunction["evidence"] = "model-rules", familyNotes: WorkshopFunction["familyNotes"] = {}): WorkshopFunction {
  return { id, name, ecu, source, evidence, familyNotes, german: "Codierung/Programmierung", section: "programming",
    summary: "查看本机规则或厂方更新资料，核实当前身份、更新方式和目标软件，准备逐单元编程计划。",
    prerequisites: [IDENTITY, "逐项核实产品键、年款、市场、配置码、当前硬件号和软件号；保留禁止刷写分支。", "核实完整更新包、合法访问授权、更新媒介、传输时序及规定供电。", "完成目标 ECU 的中断恢复和台架验证；编码备份不等于固件备份。"],
    steps: ["读取当前软硬件身份，保存编码与相关原值。", "选择经核实的规则和更新包，展开全部关联 ECU 及执行顺序。", "按具名 ECU 流程更新、设码、自适应和复位。", "重新读取软件身份及相关故障，核实完整目标状态。"],
    dependencies: ["规则或厂方流程可能要求其他 ECU 一并更新。", "后续设码、自适应、点火周期及验证。"] };
}

export const WORKSHOP_FUNCTIONS: readonly WorkshopFunction[] = [
  maintenance("psm-roller", "滚筒模式 · PSM", "Rollenmodus", "PSM", "进入、查询与退出 PSM 滚筒模式；可选择保持方式。",
    "9x1/cts-ecu.xml + cts-config/psm_rollenmodus_config.xml", [], [],
    ["核实所选保持方式及开始条件，再启动具名 PSM 例程。", "读取例程状态，区分已启用、已退出与结果不明。", "按已验证的停止流程退出，并再次读取状态。"]),
  maintenance("vehicle-roller", "滚筒模式 · 整车", "Rollenmodus", "整车", "整车入口可能协调多个控制单元，须逐单元核实状态。",
    "9x1/cts-platform.xml + cts-config/PAG_Gesamtfahrzeug_Rollenmodus.xml",
    ["核实整车参与单元和排除清单；共享配置不能直接作为广播请求。"], ["多控制单元启动、状态核验和退出。"],
    ["按已核实的单元清单执行开始／状态／停止，记录每个单元结果。"]),
  maintenance("service-reset", "保养周期复位", "Wartungsintervall zuruecksetzen", "仪表", "记录实际保养后，复位车辆仪表中的保养周期。", undefined,
    ["确认仪表变体及实际完成的保养项目。"], [], ["读取原周期，预览复位后的目标值；执行后回读。"]),
  maintenance("service-set", "保养周期设置", "Wartungsintervall schreiben", "仪表", "设置仪表保养周期；目录限定部分仪表变体。", undefined,
    ["目录变体为 Kombiinstrument_A[2-3]_.*，仍须核对实际身份和字段定义。"], [], ["备份原周期，核对允许的字段和值，再写入及回读。"]),
  maintenance("battery-change", "蓄电池更换", "Batteriewechsel", "Gateway", "更换电池后的引导登记／适配流程。", undefined,
    ["核实该变体的电池参数字段与实际更换规格。"], [], ["读取并备份当前参数，准备差异，按流程登记及回读。"]),
  maintenance("brake-bleed", "刹车排气", "Bremsen entlueften", "PSM", "PSM 引导的制动液排气流程。", undefined,
    ["取得该变体的设备、工况、阀／泵操作顺序及结束条件。"], ["液压阀／泵动作。"], ["按经核实的排气步骤执行并检查退出状态。"]),
  maintenance("wheel-speed-test", "轮速传感器测试", "Drehzahlfuehlertest", "PSM", "检查轮速传感器的专用测试流程。"),
  maintenance("sensor-calibration", "传感器组校准", "Abgleich Sensorcluster", "PSM", "PSM 传感器组的校准流程。", undefined,
    ["取得该变体的车辆姿态、工况与校准成功判据。"], ["校准值改变。"]),
  maintenance("pdk-oil-fill", "PDK 加油流程", "Oelbefuellung", "PDK", "包含温度条件、例程启停和规定等待的加油流程。",
    "9x1/cts-ecu.xml + cts-config/PAG_9x1_PDK_Oelbefuellung.xml",
    ["现有配置记录油底壳温度 30–50 °C，并有 40 秒／5 分钟等待；须核实目标变体后采用。"],
    ["例程启动、停止、结果读取及等待。"], ["按目标变体的温度与加油步骤执行，遵守规定等待及收尾。"]),
  maintenance("pdk-calibration", "PDK 完整校准", "Kalibrieren (Gesamtablauf)", "PDK", "完整引导校准；现有准备配置可以触发变速箱编程。",
    "9x1/cts-ecu.xml + cts-config/PAG_9x1_PDKit_Calibration_Prepare.xml",
    ["核实编程分支、温度条件、点火等待与恢复方案。"], ["可能刷写：activateProgramming=true，引用 GETRIEBE_9x1_ABLAUF.xml。", "校准与点火周期。"],
    ["先展开是否需要编程及其目标软件，补齐整个执行计划。", "按已验证的流程完成编程分支、校准和点火等待。"]),
  programming("program-dme", "DME 控制单元编程", "DME", "9x1/flash-data/regeln/DME.xml + LL_EnginContrModul1UDS.xml"),
  programming("program-pdk", "PDK 控制单元编程", "PDK", "9x1/flash-data/regeln/GETRIEBE.xml"),
  programming("program-airbag", "安全气囊控制单元编程", "Airbag", "9x1/flash-data/regeln/AIRBAG_9x1.xml + E5K1G.xml"),
  programming("program-gateway", "Gateway 控制单元编程", "Gateway", "9x1/flash-data/regeln/GATEWAY.xml", "model-rules", {
    "981": "本机规则含旧年款／硬件的 NO FLASH 分支；尚未找到明确 981 的可刷写目标，不能套用 982 目标。",
    "982": "已找到 982 产品键／年款规则，同时须核对旧硬件 NO FLASH 限制。",
  }),
  programming("program-amplifier", "音响功放编程", "功放", "9x1/flash-data/regeln/VERSTAERKER.xml", "model-rules", {
    "981": "已找到 Boxster／Cayman 的 Bose、Burmester 规则，依赖配置码和当前硬件号。",
    "982": "这份本机规则尚未发现明确 982 的功放分支；不能据此套用 981 目标。",
  }),
  programming("program-headlight-left", "左前灯控制单元编程", "左前灯", "9x1/flash-data/regeln/SCHEINWERFER_LINKS.xml", "platform-rules"),
  programming("program-headlight-right", "右前灯控制单元编程", "右前灯", "9x1/flash-data/regeln/SCHEINWERFER_RECHTS.xml", "platform-rules"),
  programming("program-led-left", "左 LED 前灯编程／数据集", "左LED前灯", "9x1/flash-data/regeln/SCHEINWERFER_LED_LINKS_9X1_SW.xml + SCHEINWERFER_LED_LINKS_9X1_DS.xml", "platform-rules"),
  programming("program-led-right", "右 LED 前灯编程／数据集", "右LED前灯", "9x1/flash-data/regeln/SCHEINWERFER_LED_RECHTS_9X1_SW.xml + SCHEINWERFER_LED_RECHTS_9X1_DS.xml", "platform-rules"),
  programming("program-cluster", "仪表控制单元编程", "仪表", "Porsche WE02 / SY 29/13 / WG39 技术通告", "service-bulletin", {
    "981": "WE02／SY 29/13 明确包含部分 2012–2014 981 的仪表重新编程，须核对具体适用条件。",
    "982": "WG39 明确包含部分 2017 982 的仪表重新编程，与 PCM 更新保持一致。",
  }),
  programming("program-pcm", "PCM 软件更新", "PCM", "Porsche WC17 / WG39 技术通告", "service-bulletin", {
    "981": "WC17 涉及部分 2013 Boxster／Boxster S 的 PCM 3.1 更新；需核对配置、地区和更新媒介。",
    "982": "WG39 的 PCM 4.0 更新采用地区对应 SD 卡；不能当作通用 OBD 刷写请求。",
  }),
  programming("program-front-body", "前车身控制单元编程", "前车身", "Porsche 59/14 技术通告", "service-bulletin", {
    "981": "59/14 明确涉及部分 2013–2014 981：更换指定后 BCM 后核对并更新前 BCM，包含关联编程。",
    "982": "当前资料未完成 982 前车身编程条件核实。",
  }),
  programming("program-rear-body", "后车身控制单元编程", "后车身", "Porsche 59/14 技术通告", "service-bulletin", {
    "981": "59/14 的具名更换流程包含前／后 BCM 关联编程，须核对后 BCM 零件号和完整流程。",
    "982": "当前资料未完成 982 后车身编程条件核实。",
  }),
  programming("program-parking-brake", "电子驻车制动编程", "驻车制动", "Porsche WD08 技术通告", "service-bulletin", {
    "981": "WD08 仅覆盖具名 2013–2014 981 手动变速箱车辆；不能用于本车 PDK。",
    "982": "当前资料未完成 982 驻车制动编程条件核实。",
  }),
];

export type WorkshopFlashRule = {
  id: string;
  generation: WorkshopGeneration;
  ecu: WorkshopProgrammingEcu;
  description: string;
  conditions: { field: string; values: string[] }[];
  targets: { logicalLink: string | null; softwarePartNumber: string | null; session: string | null }[];
  currentEcus?: { logicalLink: string; conditions: { field: string; values: string[] }[] }[];
  kind?: "firmware" | "dataset" | "blocked";
  familyEvidence?: "product-key" | "shared-platform" | "description";
  source: string;
};
export type WorkshopFlashIndex = {
  schemaVersion: 1;
  sources: { file: string; sha256: string }[];
  rules: WorkshopFlashRule[];
};
export type WorkshopFlashResult = { status: "loaded" | "missing" | "invalid"; index: WorkshopFlashIndex | null };
export type WorkshopPreviewInput = {
  functionId: string;
  generation: WorkshopGeneration;
  rollerPersistence?: RollerPersistence;
  flashRuleId?: string;
};

/** An offline preparation checklist only: deliberately contains no executable requests. */
export function buildWorkshopPreview(input: WorkshopPreviewInput, rule?: WorkshopFlashRule) {
  if (!input || (input.generation !== "981" && input.generation !== "982")) throw new Error("workshop-generation-unsupported");
  const fn = WORKSHOP_FUNCTIONS.find((f) => f.id === input.functionId);
  if (!fn) throw new Error("workshop-function-unknown");
  if (fn.id === "psm-roller" && input.rollerPersistence !== "ignition-cycle" && input.rollerPersistence !== "persistent") {
    throw new Error("workshop-roller-persistence-required");
  }
  if (fn.id !== "psm-roller" && input.rollerPersistence !== undefined) throw new Error("workshop-option-not-applicable");
  if (input.flashRuleId !== undefined || rule !== undefined) {
    if (!rule || input.flashRuleId !== rule.id || fn.section !== "programming" || rule.generation !== input.generation || rule.ecu !== fn.ecu) {
      throw new Error("workshop-rule-mismatch");
    }
  }
  return {
    schemaVersion: 1 as const, mode: "offline-preview" as const, state: "Draft" as const,
    executionEnabled: false as const, liveVerified: false as const,
    generation: input.generation, functionId: fn.id, name: fn.name, ecu: fn.ecu,
    identityStatus: "unverified" as const, applicabilityStatus: "unverified" as const,
    rollerPersistence: input.rollerPersistence ?? null,
    flashRule: rule ? structuredClone(rule) : null,
    programmingDisposition: rule?.kind === "blocked" ? "blocked" as const : "unverified" as const,
    familyNote: fn.familyNotes?.[input.generation] ?? null,
    prerequisites: [...fn.prerequisites],
    steps: rule?.kind === "blocked" ? ["记录禁止刷写分支和完整条件，不选择固件目标。", "核实当前身份及限制；独立验证前不进入编程。"] : [...fn.steps],
    dependencies: rule?.kind === "blocked" ? [] : [...fn.dependencies], source: fn.source,
    unresolved: ["目标 ECU 版本适用性尚未核实。", "精确执行请求、访问条件与退出／恢复流程尚未完成独立验证。"],
  };
}
