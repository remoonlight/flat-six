import { describe, expect, it } from "vitest";
import { buildWorkshopPreview, type WorkshopFlashRule, type WorkshopPreviewInput } from "./piwis-workshop.js";

const rule: WorkshopFlashRule = { id: "test-rule", generation: "982", ecu: "DME", description: "982 test",
  conditions: [{ field: "PRODUKTSCHLUESSEL", values: ["TEST"] }],
  targets: [{ logicalLink: "DME_UDS", softwarePartNumber: "TEST-SW", session: null }], source: "fixture.xml" };

describe("PIWIS offline preparation", () => {
  it("rejects other families and unknown functions", () => {
    expect(() => buildWorkshopPreview({ generation: "991.2", functionId: "psm-roller" } as unknown as WorkshopPreviewInput)).toThrow("generation-unsupported");
    expect(() => buildWorkshopPreview({ generation: "981", functionId: "unknown" })).toThrow("function-unknown");
  });
  it("requires an explicit roller option and does not infer live status", () => {
    expect(() => buildWorkshopPreview({ generation: "981", functionId: "psm-roller" })).toThrow("persistence-required");
    const preview = buildWorkshopPreview({ generation: "981", functionId: "psm-roller", rollerPersistence: "persistent" });
    expect(preview.rollerPersistence).toBe("persistent");
    expect(preview.executionEnabled).toBe(false);
    expect(preview.identityStatus).toBe("unverified");
    expect(preview.liveVerified).toBe(false);
    expect(() => buildWorkshopPreview({ generation: "981", functionId: "vehicle-roller", rollerPersistence: "persistent" })).toThrow("not-applicable");
  });
  it("rejects rule mismatch across generations, ECUs and rule IDs", () => {
    expect(() => buildWorkshopPreview({ generation: "981", functionId: "program-dme", flashRuleId: rule.id }, rule)).toThrow("rule-mismatch");
    expect(() => buildWorkshopPreview({ generation: "982", functionId: "program-pdk", flashRuleId: rule.id }, rule)).toThrow("rule-mismatch");
    expect(() => buildWorkshopPreview({ generation: "982", functionId: "program-dme", flashRuleId: "other" }, rule)).toThrow("rule-mismatch");
    expect(() => buildWorkshopPreview({ generation: "982", functionId: "program-dme", flashRuleId: rule.id })).toThrow("rule-mismatch");
  });
  it("exports a detached research rule while preserving unverified applicability", () => {
    const preview = buildWorkshopPreview({ generation: "982", functionId: "program-dme", flashRuleId: rule.id }, rule);
    expect(preview.flashRule?.targets[0].softwarePartNumber).toBe("TEST-SW");
    expect(preview.applicabilityStatus).toBe("unverified");
    preview.flashRule!.targets[0].softwarePartNumber = "changed";
    expect(rule.targets[0].softwarePartNumber).toBe("TEST-SW");
  });
  it("exposes programming dependency in PDK calibration preparation", () => {
    const preview = buildWorkshopPreview({ generation: "981", functionId: "pdk-calibration" });
    expect(preview.dependencies.join(" ")).toContain("activateProgramming=true");
    expect(preview.steps.join(" ")).toContain("编程");
    expect(JSON.stringify(preview)).not.toMatch(/writePayload|requestBytes|seedKey/);
  });
  it("keeps no-flash branches as restrictions without a programming step", () => {
    const restriction: WorkshopFlashRule = { ...rule, ecu: "Gateway", kind: "blocked", familyEvidence: "shared-platform",
      targets: [{ logicalLink: null, softwarePartNumber: null, session: null }],
      currentEcus: [{ logicalLink: "GATEWAY", conditions: [{ field: "HWTNR", values: ["OLD-HW"] }] }] };
    const preview = buildWorkshopPreview({ generation: "982", functionId: "program-gateway", flashRuleId: rule.id }, restriction);
    expect(preview.programmingDisposition).toBe("blocked");
    expect(preview.steps.join(" ")).toContain("不选择固件目标");
    expect(preview.flashRule?.currentEcus?.[0].conditions[0].values).toEqual(["OLD-HW"]);
  });
  it("preserves target dataset sessions without inventing a software part number", () => {
    const dataset: WorkshopFlashRule = { ...rule, ecu: "左LED前灯", kind: "dataset", familyEvidence: "shared-platform",
      targets: [{ logicalLink: "SCHEINWERFER_LED_LINKS", softwarePartNumber: null, session: "SESD_TEST" }] };
    const preview = buildWorkshopPreview({ generation: "982", functionId: "program-led-left", flashRuleId: rule.id }, dataset);
    expect(preview.flashRule?.targets[0]).toEqual(dataset.targets[0]);
    expect(preview.applicabilityStatus).toBe("unverified");
    expect(preview.executionEnabled).toBe(false);
  });
});
