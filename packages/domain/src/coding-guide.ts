export type CodingGuideStep = {
  module?: string;
  compat?: string[];
  pathZh: string;
  pathEn: string;
  from: string;
  to: string;
};
export type CodingGuideItem = {
  id: number;
  module: string;
  name: string;
  nameEn: string;
  compat: string[];
  compatStatus: string;
  note: string;
  multiModule?: boolean;
  steps: CodingGuideStep[];
};
export type CodingPlanStep = CodingGuideStep & {
  itemId: number;
  feature: string;
  stepIndex: number;
  module: string;
};

/** Some upstream category labels differ from the explicitly named ECU in the path. */
export function codingStepModule(
  item: CodingGuideItem,
  step: CodingGuideStep,
): string {
  if (step.module) return step.module;
  if (step.pathEn.startsWith("Driver's Door-")) return "驾驶员车门";
  if (step.pathEn.startsWith("Passenger Door-")) return "乘客车门";
  if (step.pathEn.startsWith("Additional Instrument Watch-"))
    return "附加仪表时钟";
  return item.module;
}

export function coding981Applicability(item: CodingGuideItem): {
  excluded: boolean;
  label: string;
  reason: string;
} {
  if (!item.compat.includes("981") || item.id === 5)
    return {
      excluded: true,
      label: "不适用当前车辆",
      reason: "当前车辆为自然吸气 981 Boxster S，不使用涡轮车型参数。",
    };
  const hardware: Record<number, string> = {
    16: "需记忆座椅",
    29: "需电动折叠后视镜，区分原厂与后装",
    30: "需 Sport Chrono 时钟",
    31: "需匹配的主机与 LVDS 硬件",
    111: "需雨量传感器",
    114: "需后视镜迎宾灯硬件",
    502: "需核对 PTV 相关硬件和控制单元配置",
    801: "需多功能方向盘硬件",
    802: "需加热方向盘",
    901: "需记忆座椅",
    1001: "需倒车影像硬件",
    9002: "需运动排气硬件，并选择一种按键方案",
  };
  if (hardware[item.id] || item.compatStatus === "hw")
    return {
      excluded: false,
      label: "需核对硬件",
      reason: hardware[item.id] || item.note,
    };
  if (item.id === 602)
    return {
      excluded: false,
      label: "需核对车型参数",
      reason: "来源原值引用 982，不能作为当前 981 的原值或转向标定。",
    };
  if (item.id === 13)
    return {
      excluded: false,
      label: "需核对年款",
      reason:
        "来源仅标注 981 系列，Sport Individual 在本车上的可用性尚未验证。",
    };
  return {
    excluded: false,
    label: "社区参考 · 待实车核对",
    reason: "来源按 981 系列筛选，未验证本车控制单元版本。",
  };
}

export function build981CodingPlan(
  items: CodingGuideItem[],
  selected: number[],
  exhaustVariant: string,
) {
  const chosen = items.filter(
    (i) => selected.includes(i.id) && !coding981Applicability(i).excluded,
  );
  const issues: string[] = [];
  if (chosen.some((i) => i.id === 301) && chosen.some((i) => i.id === 302))
    issues.push("启停记忆与启动时默认关闭启停是替代方案，请只保留一项。");
  if (chosen.some((i) => i.id === 9002) && !["A", "B"].includes(exhaustVariant))
    issues.push("后加装运排需要先选择按键方案 A 或 B。");
  if (chosen.some((i) => i.id === 11) !== chosen.some((i) => i.id === 103))
    issues.push("日行灯菜单需要组合仪表与前端电子设备配套，请同时选择两项。");
  const steps: CodingPlanStep[] = chosen.flatMap((item) =>
    item.steps.flatMap((step, stepIndex) => {
      if (step.compat && !step.compat.includes("981")) return [];
      if (
        item.id === 9002 &&
        stepIndex >= 3 &&
        stepIndex !==
          (exhaustVariant === "A" ? 3 : exhaustVariant === "B" ? 4 : -1)
      )
        return [];
      return [
        {
          ...step,
          itemId: item.id,
          feature: item.name,
          stepIndex,
          module: codingStepModule(item, step),
        },
      ];
    }),
  );
  const groups = [...new Set(steps.map((s) => s.module))].map((module) => ({
    module,
    steps: steps.filter((s) => s.module === module),
  }));
  return { items: chosen, groups, stepCount: steps.length, issues };
}
