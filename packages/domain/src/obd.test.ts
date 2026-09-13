import { describe, expect, it } from "vitest";
import { ElmPromptBuffer, createObdPlan, decodeObdObservation, parseCanReplies, validateObdRecording } from "./obd.js";

describe("OBD raw evidence", () => {
  it("waits for the prompt across arbitrary chunks and preserves multiple responses", () => {
    const p = new ElmPromptBuffer();
    expect(p.push("010C\r7E8 04 41 ")).toEqual([]);
    expect(p.push("0C 0F A0\r>03\r")).toEqual(["010C\r7E8 04 41 0C 0F A0\r>"]);
    expect(p.incomplete).toBe(true);
  });
  it("decodes separate ECUs without mixing bytes", () => {
    const r = decodeObdObservation("010C", "7E8 04 41 0C 0F A0\r7E9 04 41 0C 1F 40\r>", 1, 0);
    expect(r.samples.map((s) => [s.ecu, s.value])).toEqual([["7E8", 1000], ["7E9", 2000]]);
  });
  it("distinguishes a valid empty DTC response from no response or malformed data", () => {
    expect(decodeObdObservation("03", "7E8 03 43 00 00\r>", 1, 0).outcome).toBe("ok");
    expect(decodeObdObservation("03", "NO DATA\r>", 1, 0).outcome).toBe("no-data");
    expect(decodeObdObservation("03", "NO DATA", 1, 0).outcome).toBe("invalid");
    expect(decodeObdObservation("03", "7E8 03 43 03 01\rNO DATA\r>", 1, 0).outcome).toBe("invalid");
    expect(decodeObdObservation("03", "7E8 04 43 03 01\r>", 1, 0).outcome).toBe("invalid");
    expect(decodeObdObservation("03", "7E8 03 43 03 01\r>", 1, 0).dtcs[0].code).toBe("P0301");
  });
  it("rejects missing CFs, wrong sequence, unexpected service and short values", () => {
    expect(() => parseCanReplies("7E8 10 14 49 02 01 57 50 30\r>", "0902")).toThrow("incomplete_multiframe");
    expect(() => parseCanReplies("7E8 10 14 49 02 01 57 50 30\r7E8 22 30 30 30 30 30 30 30\r>", "0902")).toThrow("invalid_sequence");
    expect(decodeObdObservation("010C", "7E8 04 42 0C 0F A0\r>", 1, 0).samples).toEqual([]);
    expect(decodeObdObservation("010C", "7E8 03 41 0C 0F\r>", 1, 0).outcome).toBe("invalid");
    expect(decodeObdObservation("010C", "FFF 04 41 0C 0F A0\r>", 1, 0).outcome).toBe("invalid");
  });
  it("keeps freeze-frame measurements separate from live data", () => {
    const r = decodeObdObservation("020500", "7E8 04 42 05 00 82\r>", 1, 0);
    expect(r.samples[0]).toMatchObject({ value: 90, context: "freeze" });
  });
  it("never accepts write commands even through a replay", () => {
    expect(() => decodeObdObservation("04", ">", 1, 0)).toThrow("not_allowed");
    expect(() => createObdPlan({ budgetMs: Infinity })).toThrow();
    expect(() => validateObdRecording({ version: 1, run: { source: "vehicle" }, observations: [] })).toThrow();
  });
});
