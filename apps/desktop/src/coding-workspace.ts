import { WORKSHOP_FUNCTIONS, codingStepModule, type CodingGuideItem } from "@porsche981/domain";
import guide from "../../../data/seed/coding-guide/981.json";
import menu from "../../../data/seed/coding-guide/workspace-menu.json";
import archive from "../../../data/seed/x431/981-2014-coding-menu.json";
import { topologySeed } from "./can-topology-data";
import { combinedGeneration, flattenNodes } from "./can-topology-logic.mjs";

export type CodingCategory = "maintenance" | "coding" | "special" | "programming";
export const CODING_CATEGORIES: { id: CodingCategory; label: string }[] = [
  { id: "maintenance", label: "维护" }, { id: "coding", label: "设码" },
  { id: "special", label: "特殊功能" }, { id: "programming", label: "编程" },
];
export type CodingSystem = { id: string; short: string; label: string; branchLabel: string; supplemental?: boolean };
export type MenuEntry = typeof menu.items[number];
export type ArchiveEvidence = { path: string; source: string; year: string; rowId: number };
export type WorkspaceEntry = {
  id: string; name: string; category: CodingCategory; systemIds: string[];
  families: string; source: string; guideId?: number; workshopId?: string; menu?: MenuEntry; archive?: ArchiveEvidence; sourceIds?: string[];
};

// Explicit aliases only. Tiptronic evidence is kept separate from PDK.
const SYSTEM_ALIASES: Record<string, string[]> = {
  "DME模块": ["dme"], "发动机": ["dme"], "DME": ["dme"],
  "PDK": ["pdk"], "PSM模块": ["psm"], "PSM": ["psm"], "保时捷稳定管理系统": ["psm"],
  "保时捷主动悬挂管理系统": ["pasm"], "转向助力": ["eps"], "动力转向": ["eps"],
  "组合仪表": ["cluster"], "仪表": ["cluster"], "网关模块": ["gateway"], "网关": ["gateway"], "Gateway": ["gateway"],
  "前端电子设备": ["bcm-front"], "前车身": ["bcm-front"], "后端电子设备": ["bcm-rear"], "后车身": ["bcm-rear"],
  "空调模块": ["hvac"], "空调": ["hvac"], "方向盘电子设备": ["steering-column"], "转向盘电子设备": ["steering-column"],
  "座椅模块": ["seat-driver", "seat-passenger"], "驾驶员侧座椅记忆": ["seat-driver"], "乘客侧座椅位置记忆": ["seat-passenger"],
  "泊车辅助": ["parking"], "驻车辅助系统": ["parking"], "驻车制动": ["epb"], "驻车制动器": ["epb"],
  "安全气囊": ["airbag"], "Airbag": ["airbag"], "保时捷通讯管理系统": ["pcm"], "PCM": ["pcm"],
  "驾驶员侧车门": ["door-driver"], "乘客侧车门": ["door-passenger"], "驾驶员车门": ["door-driver"], "乘客车门": ["door-passenger"], "附加仪表时钟": ["clock"], "活顶": ["roof"], "轮胎压力监控 (TPM)": ["rdk"],
  "选择杆": ["selector"], "附加仪表-时钟": ["clock"], "倒车摄像机": ["reverse-camera"], "前部摄像机": ["front-camera"],
  "自适应巡航控制 (ACC)": ["acc"], "车道变换辅助系统，右侧(主设备)": ["swa-right"], "车道变换辅助系统，左侧(从动设备)": ["swa-left"],
  "左侧前照灯": ["headlamp-left"], "左前灯": ["headlamp-left"], "左LED前灯": ["headlamp-left"],
  "右前照灯": ["headlamp-right"], "右前灯": ["headlamp-right"], "右LED前灯": ["headlamp-right"],
  "功放": ["source-amplifier"], "外部放大器": ["source-amplifier"], "整车": ["whole-vehicle"],
};
export function codingSystemIds(label: string): string[] {
  return SYSTEM_ALIASES[label] ?? [`source-${label}`];
}
const SPECIAL_WORKSHOP = new Set(["psm-roller", "vehicle-roller", "wheel-speed-test", "sensor-calibration", "pdk-calibration"]);
const MENU_MAINTENANCE = new Set(["重置保养间隔", "电池更换", "对制动器排气", "机油加注"]);
export function workshopCategory(id: string, section: string): CodingCategory {
  return section === "programming" ? "programming" : SPECIAL_WORKSHOP.has(id) ? "special" : "maintenance";
}

export const WORKSPACE_ENTRIES: WorkspaceEntry[] = [
  ...WORKSHOP_FUNCTIONS.map((f) => ({ id: `workshop-${f.id}`, name: f.name,
    category: workshopCategory(f.id, f.section), systemIds: codingSystemIds(f.ecu),
    families: "981 / 982 · 适用性待核实", source: "PIWIS 流程", workshopId: f.id })),
  ...(guide.items as CodingGuideItem[]).map((item) => ({ id: `guide-${item.id}`, name: item.name, category: "coding" as const,
    systemIds: [...new Set(item.steps.flatMap((step) => codingSystemIds(codingStepModule(item, step))))],
    families: "981 · 社区参考", source: "设码指引", guideId: item.id })),
  ...menu.items.map((item) => ({ id: item.id, name: item.subFunction ?? `${item.system} · ${item.function}`,
    category: (item.function === "设码" ? "coding" : item.function === "编程" ? "programming" : MENU_MAINTENANCE.has(item.subFunction ?? "") ? "maintenance" : "special") as CodingCategory,
    systemIds: codingSystemIds(item.system), families: `${item.evidence.map((e) => e.generation).join(" / ")} · 来源菜单`, source: "X431 菜单", menu: item,
    archive: (() => {
      const original = archive.systems.find((s) => s.system === item.system)?.items.find((i) => i.function === item.function && i.subFunction === item.subFunction);
      return original ? { path: original.playbook.x431Path, source: archive.source, year: archive.year, rowId: original.rowId } : undefined;
    })() })),
];
// Merge only explicit equivalents in the same system; generic menus never
// select between distinct LED/headlamp procedures or qualify vehicle fit.
const MENU_EQUIVALENTS: Record<string, string> = {
  "重置保养间隔": "service-reset", "电池更换": "battery-change", "对制动器排气": "brake-bleed",
};
for (let i = WORKSPACE_ENTRIES.length - 1; i >= 0; i--) {
  const entry = WORKSPACE_ENTRIES[i];
  if (!entry.menu) continue;
  const equivalent = MENU_EQUIVALENTS[entry.menu.subFunction ?? ""];
  const candidates = WORKSPACE_ENTRIES.filter((other) => other.workshopId && other.category === entry.category
    && other.systemIds.length === entry.systemIds.length && other.systemIds.every((id) => entry.systemIds.includes(id))
    && (equivalent ? other.workshopId === equivalent : entry.menu?.function === "编程" && !entry.menu.subFunction));
  if (candidates.length !== 1 || candidates[0].menu) continue;
  const target = candidates[0];
  target.menu = entry.menu; target.archive = entry.archive;
  target.sourceIds = [target.id, entry.id]; target.source += " · X431 菜单";
  WORKSPACE_ENTRIES.splice(i, 1);
}
const topologySystems = flattenNodes(combinedGeneration(topologySeed())) as CodingSystem[];
const extraLabels = new Map<string, string>([["whole-vehicle", "整车协调"], ["source-amplifier", "外部放大器 / 功放"]]);
for (const item of menu.items) for (const id of codingSystemIds(item.system)) if (!extraLabels.has(id)) extraLabels.set(id, item.system);
export const CODING_SYSTEMS: CodingSystem[] = [...topologySystems];
for (const entry of WORKSPACE_ENTRIES) for (const id of entry.systemIds) {
  if (CODING_SYSTEMS.some((s) => s.id === id)) continue;
  CODING_SYSTEMS.push({ id, short: id === "whole-vehicle" ? "ALL" : "REF", label: extraLabels.get(id) ?? id, branchLabel: "补充资料系统", supplemental: true });
}
