import { describe, expect, it } from "vitest";
import { decodeObdObservation } from "./obd.js";
import {
  applyEcuUpsert,
  attachManual,
  buildCapabilityRegistry,
  classifyAdapter,
  decodeProductionObservation,
  filter981CodesJson,
  ignitionFromRpm,
  lookup981Manual,
  parseAtrv,
  resolveVehicleKey,
  vehicleFromVoltage,
} from "./obd-production.js";

describe("production OBD decode", () => {
  it("handles CAN PCI multiframe, rejects guessed framing, errors and Mode04", () => {
    const vinPci = decodeProductionObservation(
      "0902",
      "7E8 10 14 49 02 01 57 50 30\r7E8 21 5A 5A 5A 39 38 5A 45\r7E8 22 53 30 30 30 30 30 31\r>",
      "pci",
    );
    expect(vinPci.vin).toBe("WP0ZZZ98ZES000001");
    const fallback = decodeProductionObservation("010C", "7E8 41 0C 0F A0\r>", "pci");
    expect(fallback.outcome).toBe("invalid");
    expect(decodeProductionObservation("03", "NO DATA\r>", "pci").outcome).toBe("no-data");
    expect(decodeProductionObservation("03", "BUS ERROR\r>", "pci").outcome).toBe("bus-error");
    expect(decodeProductionObservation("ATZ", "ERROR\r>", "pci").outcome).toBe("at-error");
    const cleared = decodeProductionObservation("04", "7E8 01 44\r>", "pci");
    expect(cleared.outcome).toBe("ok");
    expect(decodeProductionObservation("04", "7E8 01 43\r>", "pci").outcome).toBe("invalid");
  });

  it("does not treat voltage as vehicle communication; RPM>0 is running else unknown", () => {
    expect(parseAtrv("12.6V\r>")).toBe(12.6);
    expect(vehicleFromVoltage(12.6)).toBe(false);
    expect(ignitionFromRpm(850)).toBe("running");
    expect(ignitionFromRpm(0)).toBe("unknown");
    expect(ignitionFromRpm(null)).toBe("unknown");
  });

  it("isolates simulation decoder from Mode04", () => {
    expect(() => decodeObdObservation("04", "7E8 01 44\r>", 1, 0)).toThrow("not_allowed");
  });
});

describe("981 manual filtering", () => {
  const codes = {
    codes: [
      {
        code: "P0301",
        models: ["981", "982"],
        title_zh: "气缸1缺火",
        context_zh: "981 检查点火",
        pages: [
          { model: "981", page: 1, file: "981.pdf" },
          { model: "982", page: 9, file: "982.pdf" },
        ],
      },
      { code: "P9999", models: ["982"], title_zh: "982 only", pages: [{ model: "982", page: 2, file: "982.pdf" }] },
    ],
  };
  it("unifies 981 and 982 while preserving both source page branches", () => {
    const hit = filter981CodesJson(codes, "P0301");
    expect(hit?.sourcePages).toEqual([{ model: "981", page: 1, file: "981.pdf" }, { model: '982', page: 9, file: '982.pdf' }]);
    expect(filter981CodesJson(codes, "P9999")?.sourcePages[0].model).toBe('982');
    expect(lookup981Manual("P0301", { schemaVersion: 1, modules: [], manualEntries: [{ code: "P0301", title: "ref", checks: "a" }] })?.title).toBe("ref");
    expect(attachManual({ ecu: "7E8", code: "P1111", status: "stored" }, "obd-can", null, codes).manualFallback).toContain("未找到");
  });
});

describe("ecu upsert and VIN isolation", () => {
  it("updates observed fields, keeps previous on partial failure, records like-field changes", () => {
    const first = applyEcuUpsert(null, { vehicleKey: "WVIN", moduleKey: "obd-can", name: "OBD", calibrationId: "CALA", serial: "S1" }, "t1");
    expect(first.row.calibrationId).toBe("CALA");
    const partial = applyEcuUpsert(first.row, { vehicleKey: "WVIN", moduleKey: "obd-can", name: "OBD" }, "t2");
    expect(partial.row.calibrationId).toBe("CALA");
    expect(partial.row.serial).toBe("S1");
    const changed = applyEcuUpsert(first.row, { vehicleKey: "WVIN", moduleKey: "obd-can", name: "OBD", calibrationId: "CALB", serial: "S1" }, "t3");
    expect(changed.changes.map((c) => c.field)).toEqual(["calibrationId"]);
  });
  it("does not attach unknown vehicle data onto an existing VIN", () => {
    const unknown = resolveVehicleKey({ observedVin: null, knownVins: ["WP0ZZZ98ZES000001"], currentKey: null, sessionNonce: "s1" });
    expect(unknown.vehicleKey).toBe("unknown:s1");
    const later = resolveVehicleKey({ observedVin: "WP0ZZZ98ZES000002", knownVins: ["WP0ZZZ98ZES000001"], currentKey: unknown.vehicleKey, sessionNonce: "s1" });
    expect(later.vehicleKey).toBe("WP0ZZZ98ZES000002");
  });
});

describe("adapter and capabilities", () => {
  it("allows explicit MX+ selection without inferring occupancy from its name", () => {
    const mx = classifyAdapter("COM9", "OBDLink MX+ (COM9)", null);
    expect(mx.occupied).toBe(false);
    expect(mx.occupiedReason).toBeNull();
    expect(classifyAdapter("COM9", "OBDLink MX+ | RaceChrono", null).occupied).toBe(false);
    expect(mx.preferred).toBe(false);
    const vl = classifyAdapter("COM5", "vLinker FS BT (COM5)", "BTHENUM\\x");
    expect(vl.preferred).toBe(true);
    const cap = buildCapabilityRegistry(null);
    expect(cap.find((m) => m.key === "psm")?.support).toBe("unsupported");
    expect(cap.every(m => m.clearScope === 'none')).toBe(true);
  });
});

describe('production protocol integrity', () => {
  it('validates CAN DTC count, empty responses and padding', () => {
    expect(decodeProductionObservation('03','7E8 06 43 02 03 01 05 71\r>').dtcs.map(d=>d.code)).toEqual(['P0301','P0571']);
    expect(decodeProductionObservation('03','7E8 02 43 00\r>').outcome).toBe('ok');
    for(const raw of ['7E8 04 43 02 03 01\r>','7E8 04 43 00 03 01\r>','7E8 04 43 01 00 00\r>','123 02 43 00\r>']) expect(decodeProductionObservation('03',raw).outcome).toBe('invalid');
  });
  it('keeps valid responders and exposes malformed/negative peers separately', () => {
    const d=decodeProductionObservation('03','7E8 04 43 01 03 01\r7E9 03 7F 03 11\r>');
    expect(d.dtcs.map(x=>x.ecu)).toEqual(['7E8']);
    expect(d.perEcu.find(p=>p.ecu==='7E9')?.ok).toBe(false);
  });
  it('does not accept voltage banners or truncated identities as measurements', () => {
    expect(parseAtrv('ELM327 v2.3\r>')).toBeNull();
    expect(decodeProductionObservation('0906','7E8 05 49 06 01 AA BB\r>').outcome).toBe('invalid');
    expect(decodeProductionObservation('010C','7E8 03 41 0C 00\r>').outcome).toBe('invalid');
  });
});
