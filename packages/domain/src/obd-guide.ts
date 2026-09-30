import { ANALYSIS_PARAMETERS, buildFaultDataPlan } from "./obd-analysis.js";
import type { ScannedDtc } from "./obd-production.js";

export const GUIDE_STEP_RESULTS = ["unchecked", "normal", "abnormal", "unable"] as const;
export type GuideStepResult = (typeof GUIDE_STEP_RESULTS)[number];
export type GuideStepKind = "observed" | "parameter" | "manual" | "once" | "gap" | "symptom";
export const KNOWN_DME_PROFILE = "porsche-981-2014-dme";
export const KNOWN_GATEWAY_PROFILE = "porsche-981-2014-gateway";

/** ADR 001 standard engine set: 04/05/0C/0D/0F/11. Only for known DME profile. */
export const STANDARD_SIX_PARAMETER_IDS = new Set(["load", "coolant", "rpm", "speed", "intake-temp", "throttle"]);

export type GuideEcuContext = "dme" | "gateway" | "unknown";

export type GuideStep = {
  id: string;
  kind: GuideStepKind;
  label: string;
  factoryVerified: false;
  moduleKey: string;
  code: string;
  parameterId?: string;
  standardSix: boolean;
  supportedRead: boolean;
};

export type GuideChecklist = {
  moduleKey: string;
  ecu: string;
  code: string;
  disclaimer: string;
  steps: GuideStep[];
  evidenceGaps: string[];
  ecuContext: GuideEcuContext;
};

export function guideEcuContext(moduleKey: string, declared?: GuideEcuContext): GuideEcuContext {
  if (declared) return declared;
  if (moduleKey === KNOWN_DME_PROFILE) return "dme";
  if (moduleKey === KNOWN_GATEWAY_PROFILE) return "gateway";
  return "unknown";
}

export function wiringSystemIdForModule(moduleKey: string): string | null {
  if (moduleKey === KNOWN_DME_PROFILE) return "engine";
  if (moduleKey === KNOWN_GATEWAY_PROFILE) return "can";
  return null;
}

function stepId(moduleKey: string, code: string, kind: string, rest: string): string {
  return JSON.stringify([moduleKey, code.toUpperCase(), kind, rest]);
}

function asDtc(input: { code: string; moduleKey: string; ecu?: string; status?: string }): ScannedDtc {
  return {
    code: input.code.toUpperCase(),
    moduleKey: input.moduleKey,
    ecu: input.ecu || input.moduleKey,
    status: (input.status as ScannedDtc["status"]) || "stored",
    manual: null,
    manualFallback: null,
  };
}

export function buildSymptomChecklist(input: {
  symptom: string;
  checks?: string | null;
  sku?: string | null;
}): GuideChecklist {
  const symptom = String(input.symptom || "").trim();
  if (!symptom) throw new Error("guide_identity_required");
  const steps: GuideStep[] = [
    {
      id: stepId("symptom", "NONE", "symptom", "title"),
      kind: "symptom",
      label: `知识库症状：${symptom}。没有关联故障码，不套用代码检查单。`,
      factoryVerified: false,
      moduleKey: "symptom",
      code: "",
      standardSix: false,
      supportedRead: false,
    },
  ];
  if (input.checks) {
    steps.push({
      id: stepId("symptom", "NONE", "manual", "kb"),
      kind: "manual",
      label: `知识库检查步骤：${input.checks}`,
      factoryVerified: false,
      moduleKey: "symptom",
      code: "",
      standardSix: false,
      supportedRead: false,
    });
  }
  if (input.sku) {
    steps.push({
      id: stepId("symptom", "NONE", "manual", "sku"),
      kind: "manual",
      label: `知识库相关 SKU：${input.sku}（不是由故障码推断的零件结论）`,
      factoryVerified: false,
      moduleKey: "symptom",
      code: "",
      standardSix: false,
      supportedRead: false,
    });
  }
  return {
    moduleKey: "symptom",
    ecu: "none",
    code: "",
    ecuContext: "unknown",
    disclaimer: "本页只用所选症状知识库原文。没有故障码时不会猜测或套用无关代码。",
    steps,
    evidenceGaps: input.checks ? [] : ["该症状知识库没有可执行的代码级检查对应。"],
  };
}

export function buildGuideChecklist(input: {
  code: string;
  moduleKey: string;
  ecu?: string;
  status?: string;
  ecuContext?: GuideEcuContext;
  observedCodes?: Array<{ code: string; moduleKey: string; ecu?: string }>;
}): GuideChecklist {
  const code = input.code.trim().toUpperCase();
  const moduleKey = String(input.moduleKey || "").trim();
  if (!code || !moduleKey) throw new Error("guide_identity_required");
  const ctx = guideEcuContext(moduleKey, input.ecuContext);
  const dtc = asDtc({ ...input, code, moduleKey });
  const sameEcuObserved = (input.observedCodes || []).filter((o) => o.moduleKey === moduleKey);
  const steps: GuideStep[] = [];
  const observed = sameEcuObserved.length ? sameEcuObserved : [{ code, moduleKey, ecu: dtc.ecu }];
  for (const o of observed) {
    steps.push({
      id: stepId(moduleKey, code, "observed", o.code),
      kind: "observed",
      label: `已记录故障码 ${o.code.toUpperCase()}（单元 ${moduleKey}，语境 ${ctx}）。不能据此认定损坏零件。`,
      factoryVerified: false,
      moduleKey,
      code,
      standardSix: false,
      supportedRead: false,
    });
  }
  const evidenceGaps: string[] = [];
  if (ctx !== "dme") {
    evidenceGaps.push(`${code} · ${moduleKey}：不是已声明/已观察的 981 DME 档案，不套用发动机燃烧/失火补充观察。`);
    steps.push({
      id: stepId(moduleKey, code, "gap", "not-dme"),
      kind: "gap",
      label: `未核实缺口：${evidenceGaps[0]} 通用 OBD 地址或网关/PSM 同码不能证明是 DME。`,
      factoryVerified: false,
      moduleKey,
      code,
      standardSix: false,
      supportedRead: false,
    });
    return {
      moduleKey,
      ecu: dtc.ecu,
      code,
      ecuContext: ctx,
      disclaimer:
        "本清单由现有知识拼装，不是经工厂核定的完整维修步骤。仅在已知 DME 档案下提供发动机补充观察。",
      steps,
      evidenceGaps,
    };
  }
  const plan = buildFaultDataPlan([{ ...dtc, moduleKey: "obd-can:7E8", ecu: "7E8" }]);
  for (const item of plan.items) {
    const labels = plan.associations[item.key] || [];
    if (!labels.some((l) => l.startsWith(`${code} ·`))) continue;
    const standardSix = STANDARD_SIX_PARAMETER_IDS.has(item.parameterId);
    const supportedRead = Boolean(item.supported && ANALYSIS_PARAMETERS.some((p) => p.id === item.parameterId) && standardSix);
    steps.push({
      id: stepId(moduleKey, code, "parameter", item.key),
      kind: "parameter",
      label: `补充观察（非工厂核定步骤）：${item.moduleName} · ${item.name}${item.unit ? `（${item.unit}）` : ""}。${item.note}${supportedRead ? " 可到发动机数据页手工读取标准六项（本页不自动读取）。" : " 当前无已实现读取，不会发送指令。"}`,
      factoryVerified: false,
      moduleKey: KNOWN_DME_PROFILE,
      code,
      parameterId: item.parameterId,
      standardSix,
      supportedRead,
    });
  }
  for (const [i, text] of plan.manualChecks.entries()) {
    steps.push({
      id: stepId(moduleKey, code, "manual", String(i)),
      kind: "manual",
      label: `人工检查：${text}`,
      factoryVerified: false,
      moduleKey,
      code,
      standardSix: false,
      supportedRead: false,
    });
  }
  for (const [i, text] of plan.once.entries()) {
    steps.push({
      id: stepId(moduleKey, code, "once", String(i)),
      kind: "once",
      label: `一次性/尚未实现：${text}`,
      factoryVerified: false,
      moduleKey,
      code,
      standardSix: false,
      supportedRead: false,
    });
  }
  for (const [i, text] of plan.gaps.entries()) {
    steps.push({
      id: stepId(moduleKey, code, "gap", String(i)),
      kind: "gap",
      label: `未核实缺口：${text}`,
      factoryVerified: false,
      moduleKey,
      code,
      standardSix: false,
      supportedRead: false,
    });
  }
  return {
    moduleKey,
    ecu: dtc.ecu,
    code,
    ecuContext: ctx,
    disclaimer:
      "本清单由现有知识拼装，不是经工厂核定的完整维修步骤。补充观察、人工检查与缺口均已标明。同一故障码在不同控制单元上各自成案，互不继承。",
    steps,
    evidenceGaps: [...new Set([...plan.gaps, ...evidenceGaps])],
  };
}

export function assertGuideStepResult(v: unknown): GuideStepResult {
  if (typeof v !== "string" || !(GUIDE_STEP_RESULTS as readonly string[]).includes(v)) {
    throw new Error("guide_invalid_step_result");
  }
  return v as GuideStepResult;
}
