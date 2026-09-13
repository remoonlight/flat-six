import { describe, it, expect } from "vitest";
import {
  build981CodingPlan,
  coding981Applicability,
  codingStepModule,
  type CodingGuideItem,
} from "./coding-guide";
const item = (
  id: number,
  steps: CodingGuideItem["steps"] = [],
): CodingGuideItem => ({
  id,
  module: "组合仪表",
  name: `功能${id}`,
  nameEn: "",
  compat: ["981"],
  compatStatus: "ok",
  note: "",
  steps,
});
const step = { pathZh: "路径", pathEn: "Path", from: "0", to: "1" };
describe("981 coding plan boundaries", () => {
  it("uses explicitly named door and watch ECUs instead of upstream category labels", () => {
    expect(
      codingStepModule(item(114), {
        ...step,
        pathEn: "Driver's Door-Coding-Illumination",
      }),
    ).toBe("驾驶员车门");
    expect(
      codingStepModule(item(114), {
        ...step,
        pathEn: "Passenger Door-Coding-Illumination",
      }),
    ).toBe("乘客车门");
    expect(
      codingStepModule(item(30), {
        ...step,
        pathEn: "Additional Instrument Watch-Coding value-PWM_MaxS",
      }),
    ).toBe("附加仪表时钟");
  });
  it("excludes turbo settings and never translates upstream ok to vehicle verified", () => {
    expect(coding981Applicability(item(5)).excluded).toBe(true);
    expect(coding981Applicability(item(2)).label).toContain("待实车核对");
    expect(build981CodingPlan([item(5, [step])], [5], "").stepCount).toBe(0);
  });
  it("keeps all cross-module steps except incompatible series and separates alternatives", () => {
    const cross = item(9002, [
      step,
      { ...step, module: "DME模块" },
      step,
      { ...step, to: "A" },
      { ...step, to: "B" },
      { ...step, compat: ["macan"] },
    ]);
    const plan = build981CodingPlan([cross], [9002], "B");
    expect(plan.stepCount).toBe(4);
    expect(plan.groups.flatMap((g) => g.steps).some((s) => s.to === "A")).toBe(
      false,
    );
    expect(plan.groups.find((g) => g.module === "DME模块")?.steps).toHaveLength(
      1,
    );
    expect(build981CodingPlan([cross], [9002], "").issues).toHaveLength(1);
  });
  it("reports conflicting start-stop options and incomplete DRL combination", () => {
    expect(
      build981CodingPlan([item(301), item(302), item(11)], [301, 302, 11], "")
        .issues,
    ).toHaveLength(2);
    expect(
      build981CodingPlan([item(11), item(103)], [11, 103], "").issues,
    ).toEqual([]);
  });
});
