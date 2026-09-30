import { describe, expect, it } from "vitest";
import { buildGuideChecklist, buildSymptomChecklist, wiringSystemIdForModule } from "./obd-guide.js";
import {
  compareSnapshots,
  declaredBindingPatch,
  identityFieldsFromFinal,
  identityFromCapture,
  LOCAL_GARAGE_BINDING,
  normalizeRecordDtc,
  normalizeTopologyCapture,
  type DiagSnapshot,
} from "./obd-compare.js";

function reasonOf(r: ReturnType<typeof compareSnapshots>): string | undefined {
  return r.ok ? undefined : r.reason;
}

function snap(partial: Partial<DiagSnapshot> & Pick<DiagSnapshot, "id" | "at" | "modules">): DiagSnapshot {
  return {
    source: "simulation",
    identityKind: "user-declared",
    vehicleKey: LOCAL_GARAGE_BINDING,
    identityLabel: "user",
    completeness: "complete",
    fingerprint: "event:x",
    captureEventId: `e${partial.id}`,
    ...partial,
  };
}

const dme = (
  coverage: DiagSnapshot["modules"][0]["coverage"],
  dtcs: DiagSnapshot["modules"][0]["dtcs"],
  extra?: Partial<DiagSnapshot["modules"][0]>,
): DiagSnapshot["modules"][0] => ({
  moduleKey: "porsche-981-2014-dme",
  name: "DME",
  ecuVariant: extra?.ecuVariant ?? "hwA",
  coverage,
  dtcs,
  rawRef: extra?.rawRef ?? null,
});

const rec = (dtcHex: string, statusHex: string, display: string): DiagSnapshot["modules"][0]["dtcs"][0] => ({
  code: dtcHex,
  dtcHex,
  statusHex,
  subtype: statusHex,
  status: statusHex,
  display,
});

describe("guide checklist", () => {
  it("does not give DME combustion steps to gateway or generic OBD addresses", () => {
    const dmePlan = buildGuideChecklist({ code: "P0301", moduleKey: "porsche-981-2014-dme" });
    expect(dmePlan.ecuContext).toBe("dme");
    expect(dmePlan.steps.some((s) => s.standardSix)).toBe(true);
    const gw = buildGuideChecklist({ code: "P0301", moduleKey: "porsche-981-2014-gateway" });
    expect(gw.ecuContext).toBe("gateway");
    expect(gw.steps.every((s) => !s.standardSix)).toBe(true);
    expect(gw.evidenceGaps.join()).toMatch(/不是已声明/);
    const addr = buildGuideChecklist({ code: "P0301", moduleKey: "obd-can:7E9", ecuContext: "unknown" });
    expect(wiringSystemIdForModule("obd-can:7E9")).toBeNull();
    expect(addr.steps.some((s) => s.kind === "gap")).toBe(true);
    const symptom = buildSymptomChecklist({ symptom: "异响", checks: "听音" });
    expect(symptom.code).toBe("");
    expect(symptom.disclaimer).not.toMatch(/P0301/);
  });
});

describe("snapshot identity and compare", () => {
  it("does not infer VIN and rejects unbound pairs until user-declared binding", () => {
    expect(identityFromCapture({ vehicleKey: "unknown:abc" }).identityKind).toBe("unbound");
    expect(identityFromCapture(null).identityKind).toBe("unknown");
    expect(identityFromCapture({ vin: "WP0ZZZ98ZES000001" }).identityKind).toBe("vin");
    const unbound = snap({
      id: 1,
      at: "2026-09-30T10:00:00.000Z",
      identityKind: "unbound",
      vehicleKey: null,
      modules: [dme("success-none", [])],
    });
    expect(reasonOf(compareSnapshots(unbound, { ...unbound, id: 2, at: "2026-09-30T11:00:00.000Z" }))).toBe("identity-unqualified");
    const bound = { ...unbound, identityKind: "user-declared" as const, vehicleKey: LOCAL_GARAGE_BINDING };
    expect(compareSnapshots(bound, { ...bound, id: 2, at: "2026-09-30T11:00:00.000Z" }).ok).toBe(true);
    expect(reasonOf(compareSnapshots(bound, { ...bound, id: 2, at: "2026-09-30T11:00:00.000Z", vehicleKey: "local:other" }))).toBe("identity-mismatch");
    expect(declaredBindingPatch("本车库 981").vehicleKey).toBe(LOCAL_GARAGE_BINDING);
    expect(() => declaredBindingPatch("")).toThrow();
  });

  it("uses dtcHex identity; statusHex-only is status-changed; failed later is not gone", () => {
    const a = normalizeRecordDtc({ dtcHex: "C447", statusHex: "28", displayCode: "U0447" })!;
    const b = normalizeRecordDtc({ dtcHex: "C447", statusHex: "21", displayCode: "U0447" })!;
    const c = normalizeRecordDtc({ dtcHex: "C13002", statusHex: "09", displayCode: "U0130" })!;
    expect(a.code).toBe("C447");
    expect(a.statusHex).toBe("28");
    expect(c.dtcHex).toBe("C13002");
    const before = snap({
      id: 1,
      at: "2026-09-30T10:00:00.000Z",
      modules: [dme("success-dtc", [rec("C447", "28", "U0447"), rec("C412", "28", "U0412")])],
    });
    const statusOnly = snap({
      id: 2,
      at: "2026-09-30T11:00:00.000Z",
      modules: [dme("success-dtc", [rec("C447", "21", "U0447"), rec("C412", "28", "U0412")])],
    });
    const ok = compareSnapshots(before, statusOnly);
    expect(ok.ok).toBe(true);
    if (!ok.ok) throw new Error("x");
    expect(ok.rows.filter((r) => r.kind === "gone")).toHaveLength(0);
    expect(ok.rows.some((r) => r.kind === "status-changed" && r.code === "U0447")).toBe(true);
    expect(ok.rows.some((r) => r.kind === "still" && r.code === "U0412")).toBe(true);

    const afterFail = snap({
      id: 3,
      at: "2026-09-30T12:00:00.000Z",
      modules: [dme("failed", [rec("C447", "28", "U0447")])],
    });
    const fail = compareSnapshots(before, afterFail);
    expect(fail.ok).toBe(true);
    if (!fail.ok) throw new Error("x");
    expect(fail.rows.filter((r) => r.moduleKey.includes("dme") && r.kind === "gone")).toHaveLength(0);
    expect(fail.rows.filter((r) => r.moduleKey.includes("dme")).every((r) => r.kind === "not-comparable")).toBe(true);
  });

  it("reads variant from identity result rows, not qualification summary", () => {
    const fields = identityFieldsFromFinal({
      identityQualification: { observedProfileMatch: true },
      results: [
        { role: "identity", field: "hardware", ok: true, decoded: { ok: true, text: "HW1" } },
        { role: "identity", field: "hardwarePart", ok: true, decoded: { ok: true, text: "HP1" } },
      ],
    });
    expect(fields.hardware).toBe("HW1");
    const n = normalizeTopologyCapture({
      source: "simulation",
      captureEventId: "j1",
      at: "2026-09-30T10:00:00.000Z",
      nodes: [{ id: "dme", label: "DME", profileId: "porsche-981-2014-dme" }],
      results: [
        {
          nodeId: "dme",
          classified: { kind: "dtc-present", records: [{ dtcHex: "C447", statusHex: "28", displayCode: "U0447" }] },
          doc: {
            jobId: "j1",
            final: {
              results: [{ role: "identity", field: "hardware", ok: true, decoded: { text: "HW1" } }],
            },
          },
        },
      ],
    });
    expect(n.modules[0].ecuVariant).toContain("HW1");
    expect(n.identityKind).toBe("unbound");
  });

  it("rejects provenance, chronology, duplicates, and keeps two unchanged scans comparable after binding", () => {
    const base = snap({
      id: 1,
      at: "2026-09-30T10:00:00.000Z",
      modules: [dme("success-dtc", [rec("C447", "28", "U0447")])],
    });
    expect(reasonOf(compareSnapshots(base, { ...base, id: 1, at: "2026-09-30T11:00:00.000Z" }))).toBe("same-snapshot");
    expect(reasonOf(compareSnapshots({ ...base, at: "2026-09-30T12:00:00.000Z" }, { ...base, id: 2, at: "2026-09-30T11:00:00.000Z" }))).toBe("chronology");
    expect(reasonOf(compareSnapshots(base, { ...base, id: 2, at: "2026-09-30T11:00:00.000Z", source: "live" }))).toBe("source-mismatch");
    const still = compareSnapshots(
      base,
      snap({
        id: 2,
        at: "2026-09-30T11:00:00.000Z",
        modules: [dme("success-dtc", [rec("C447", "28", "U0447")])],
      }),
    );
    expect(still.ok).toBe(true);
    if (still.ok) expect(still.rows.every((r) => r.kind === "still")).toBe(true);
  });
});
