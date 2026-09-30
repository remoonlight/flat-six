import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, it, expect } from "vitest";
import { GarageDb } from "./index.js";
import { decodeObdObservation } from "@porsche981/domain";

function tmp() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "obd-store-"));
  return { dir, file: path.join(dir, "test.db") };
}

describe("offline OBD persistence", () => {
  it("preserves manual sessions and reopens raw evidence after interrupted recording", () => {
    const { dir, file } = tmp();
    let db = new GarageDb(file);
    try {
      const manual = db.createObdSession({ note: "manual" });
      db.addObdDtc({ session_id: manual.id, code: "P0300" });
      const run = db.obd.begin({ budgetMs: 5000 });
      const o = decodeObdObservation("010C", "7E8 04 41 0C 0F A0\r>", 1, 150);
      o.samples[0].value = 99999;
      db.obd.append(run.sessionId, o);
      db.obd.append(run.sessionId, o);
      expect(db.obd.recording(run.sessionId).observations[0].samples[0].value).toBe(1000);
      expect(db.db.prepare("SELECT COUNT(*) AS n FROM obd_samples").get()?.n).toBe(1);
      expect(() => db.obd.append(run.sessionId, { ...o, seq: 3 })).toThrow("sequence_gap");
      expect(() => db.obd.append(run.sessionId, { ...o, raw: "7E8 04 41 0C 1F 40\r>" })).toThrow("sequence_conflict");
      db.close(); db = new GarageDb(file);
      expect(db.obd.recover()).toBe(1);
      expect(db.obd.getRun(run.sessionId).status).toBe("interrupted");
      expect(db.obd.recording(run.sessionId).observations).toHaveLength(1);
      expect(db.listObdDtcs(manual.id)[0].code).toBe("P0300");
      expect(() => db.obd.append(run.sessionId, { ...o, seq: 2 })).toThrow("session_closed");
    } finally { db.close(); fs.unlinkSync(file); fs.rmdirSync(dir); }
  });

  it("upserts one ECU row, keeps partial fields, records changes, and merges rekey without overwriting newer VIN state", () => {
    const { dir, file } = tmp();
    const db = new GarageDb(file);
    try {
      const vin = "WP0ZZZ98ZES000001";
      const base = {
        vehicleKey: vin, moduleKey: "obd-can:7E8", name: "排放诊断", ecuAddress: "7E8",
        hardwareId: null, serial: "S1", softwareId: null, calibrationId: "CALA", cvn: "DEADBEEF",
        codingFingerprint: null, lastSuccessAt: "t1",
      };
      expect(db.obd.upsertEcu(base, [])?.serial).toBe("S1");
      db.obd.upsertEcu({ ...base, calibrationId: "CALB", serial: null, lastSuccessAt: "t2" }, [
        { vehicleKey: vin, moduleKey: "obd-can:7E8", field: "calibrationId", previous: "CALA", next: "CALB", observedAt: "t2" },
      ]);
      const after = db.obd.getEcu(vin, "obd-can:7E8")!;
      expect(after.serial).toBe("S1");
      expect(after.calibrationId).toBe("CALB");
      expect(after.cvn).toBe("DEADBEEF");
      expect(db.obd.listEcuChanges(vin)[0]).toMatchObject({ field: "calibrationId", next: "CALB" });

      db.obd.upsertEcu({
        vehicleKey: "unknown:s", moduleKey: "obd-can:7E8", name: "排放诊断", ecuAddress: "7E8",
        hardwareId: "HW9", serial: "OLD", softwareId: null, calibrationId: "OLD", cvn: null,
        codingFingerprint: null, lastSuccessAt: "t0",
      }, []);
      db.obd.rekeyEcus("unknown:s", vin);
      expect(db.obd.getEcu("unknown:s", "obd-can:7E8")).toBeNull();
      const merged = db.obd.getEcu(vin, "obd-can:7E8")!;
      expect(merged.calibrationId).toBe("CALB");
      expect(merged.serial).toBe("S1");
      expect(merged.hardwareId).toBe("HW9");

      const preId = db.obd.productionOp("clear:pre", { at: "t", modules: [] });
      expect(preId).toBeGreaterThan(0);
      db.obd.productionOp("clear:save", { preClearId: preId, at: "t2", outcome: "unknown" });
      expect((db.obd.getClear(Number(preId)) as { preClearId: number }).preClearId).toBe(preId);

      const pendingId = db.obd.productionOp("clear:pre", { at: "t3", modules: [{ name: "DME" }] });
      expect(() => db.obd.productionOp("clear:save", { at: "t4", outcome: "ok" })).toThrow('evidence_id_required');
      const linked = db.obd.productionOp("clear:save", { preClearId: pendingId, at: "t4", outcome: "ok" });
      expect(linked).toBe(pendingId);
      const row = db.obd.getClear(Number(pendingId)) as { preScanJson: string; reportJson: string };
      expect(JSON.parse(row.preScanJson).modules[0].name).toBe("DME");
      expect(JSON.parse(row.reportJson).pending).toBeUndefined();

      db.obd.setVehicleView(vin);
      db.close();
      const db2 = new GarageDb(file);
      expect(db2.obd.getVehicleView()).toBe(vin);
      expect(db2.obd.listVehicleKeys()).toContain(vin);
      db2.close();
    } finally {
      try { db.close(); } catch { /* */ }
      fs.unlinkSync(file); fs.rmdirSync(dir);
    }
  });

  it("migrates diag snapshots/cases, round-trips, rejects bad input, preserves prior rows", () => {
    const { dir, file } = tmp();
    const db = new GarageDb(file);
    try {
      const manual = db.createObdSession({ note: "keep" });
      const payload = {
        at: "2026-09-30T10:00:00.000Z",
        source: "simulation",
        identityKind: "unbound",
        vehicleKey: null,
        identityLabel: "unbound",
        completeness: "complete",
        captureEventId: "job-a",
        modules: [{
          moduleKey: "porsche-981-2014-dme",
          name: "DME",
          ecuVariant: null,
          coverage: "success-dtc",
          dtcs: [{ code: "C447", dtcHex: "C447", statusHex: "28", status: "28", display: "U0447" }],
          rawRef: "r1",
        }],
      };
      const a = db.obd.diagOp("snapshot:capture", payload) as { id: number; identityKind: string };
      const again = db.obd.diagOp("snapshot:capture", payload) as { id: number };
      expect(again.id).toBe(a.id);
      const sameContent = db.obd.diagOp("snapshot:capture", { ...payload, captureEventId: "job-b", at: "2026-09-30T10:01:00.000Z" }) as { id: number };
      expect(sameContent.id).not.toBe(a.id);
      const unboundCmp = db.obd.diagOp("compare:preview", { beforeId: a.id, afterId: sameContent.id }) as { ok: boolean; reason?: string };
      expect(unboundCmp.ok).toBe(false);
      expect(unboundCmp.reason).toBe("identity-unqualified");
      const boundA = db.obd.diagOp("snapshot:assign", { id: a.id, note: "本车库 981 声明绑定" }) as { identityKind: string; vehicleKey: string };
      const boundB = db.obd.diagOp("snapshot:assign", { id: sameContent.id, note: "本车库 981 声明绑定" }) as { id: number };
      const still = db.obd.diagOp("compare:preview", { beforeId: a.id, afterId: boundB.id }) as { ok: boolean; rows: Array<{ kind: string }> };
      expect(boundA.identityKind).toBe("user-declared");
      expect(still.ok).toBe(true);
      expect(still.rows.every((r) => r.kind === "still")).toBe(true);
      const later = db.obd.diagOp("snapshot:capture", {
        ...payload,
        captureEventId: "job-fail",
        at: "2026-09-30T11:00:00.000Z",
        completeness: "failed",
        modules: [{ ...payload.modules[0], coverage: "failed", dtcs: [{ code: "C447", dtcHex: "C447", statusHex: "28", display: "U0447" }] }],
      }) as { id: number };
      db.obd.diagOp("snapshot:assign", { id: later.id, note: "本车库 981 声明绑定" });
      const preview = db.obd.diagOp("compare:preview", { beforeId: a.id, afterId: later.id }) as { ok: boolean; rows: Array<{ kind: string }> };
      expect(preview.ok).toBe(true);
      expect(preview.rows.filter((r) => r.kind === "gone")).toHaveLength(0);
      expect(preview.rows.every((r) => r.kind === "not-comparable")).toBe(true);
      expect(() => db.obd.diagOp("snapshot:get", { id: 0 })).toThrow();
      expect(() => db.obd.diagOp("snapshot:capture", { ...payload, captureEventId: "x", source: "nope" })).toThrow();
      expect(() => db.obd.diagOp("snapshot:capture", { ...payload, captureEventId: "over", modules: [{ ...payload.modules[0], dtcs: Array(201).fill(payload.modules[0].dtcs[0]) }] })).toThrow("too_many");

      const cas = db.obd.diagOp("guide:create", { code: "P0301", moduleKey: "porsche-981-2014-dme", ecuContext: "dme", source: "manual", identityKind: "unknown" }) as { id: number; updatedAt: string; checklist: { steps: Array<{ id: string }> } };
      const cas2 = db.obd.diagOp("guide:create", { code: "P0301", moduleKey: "porsche-981-2014-dme", ecuContext: "dme", source: "manual", identityKind: "unknown" }) as { id: number };
      expect(cas2.id).toBe(cas.id);
      const otherEcu = db.obd.diagOp("guide:create", { code: "P0301", moduleKey: "porsche-981-2014-gateway", ecuContext: "gateway", source: "manual", identityKind: "unknown" }) as { id: number };
      expect(otherEcu.id).not.toBe(cas.id);
      const stepId = cas.checklist.steps[0].id;
      db.obd.diagOp("guide:setStep", { caseId: cas.id, stepId, result: "normal", note: "ok", updatedAt: cas.updatedAt });
      expect(() => db.obd.diagOp("guide:setStep", { caseId: cas.id, stepId, result: "abnormal", note: "late", updatedAt: cas.updatedAt })).toThrow("stale");
      const log = db.addFaultLog({ logged_at: "2026-09-30", odometer_km: 1, symptom: "P0301" });
      db.obd.diagOp("guide:linkFaultLog", { caseId: cas.id, faultLogId: log.id });
      db.obd.diagOp("guide:linkFaultLog", { caseId: cas.id, faultLogId: log.id });
      expect((db.obd.diagOp("guide:get", { id: cas.id }) as { faultLogId: number }).faultLogId).toBe(log.id);
      const symptom = db.obd.diagOp("guide:create", { symptom: "异响", checks: "听音", source: "manual", identityKind: "unknown" }) as { code: string };
      expect(symptom.code).toBe("");
      const anotherSymptom = db.obd.diagOp("guide:create", { symptom: "启动困难", checks: "记录启动状态" }) as { id: number };
      expect(anotherSymptom.id).not.toBe((symptom as unknown as { id: number }).id);
      const anotherDmeCode = db.obd.diagOp("guide:create", { code: "P0302", moduleKey: "porsche-981-2014-dme" }) as { id: number };
      expect(anotherDmeCode.id).not.toBe(cas.id);
      expect(stepId).not.toContain("\0");
      expect((db.obd.getGuideCase(cas.id)!.stepResults[0] as { stepId: string }).stepId).toBe(stepId);
      expect(() => db.obd.diagOp("guide:setStep", { caseId: cas.id, stepId: "fake", result: "normal" })).toThrow("invalid_step_id");
      expect(() => db.obd.diagOp("snapshot:capture", { ...payload, captureEventId: "duplicate", modules: [{ ...payload.modules[0], dtcs: [payload.modules[0].dtcs[0], payload.modules[0].dtcs[0]] }] })).toThrow("duplicate_dtc");
      expect(() => db.obd.diagOp("snapshot:capture", { ...payload, captureEventId: "oversize-key", modules: [{ ...payload.modules[0], moduleKey: "x".repeat(81) }] })).toThrow("invalid_field");
      const observed = db.obd.diagOp("snapshot:capture", { ...payload, captureEventId: "vin", identityKind: "vin", vehicleKey: "WP0ZZZ98ZES000001" }) as { id: number };
      expect(() => db.obd.diagOp("snapshot:assign", { id: observed.id, note: "override" })).toThrow("cannot_be_reassigned");
      const capturedCase = db.obd.diagOp("guide:create", { snapshotId: a.id, code: "U0447", moduleKey: "porsche-981-2014-dme" }) as { id: number; snapshotId: number; source: string };
      const laterCase = db.obd.diagOp("guide:create", { snapshotId: boundB.id, code: "U0447", moduleKey: "porsche-981-2014-dme" }) as { id: number };
      expect(capturedCase.source).toBe("simulation");
      expect(capturedCase.snapshotId).toBe(a.id);
      expect(laterCase.id).not.toBe(capturedCase.id);
      const report = db.obd.diagOp("compare:save", { beforeId: a.id, afterId: boundB.id, note: "review report", faultLogId: log.id }) as { id: number };
      expect(manual.id).toBeGreaterThan(0);

      db.close();
      const db2 = new GarageDb(file);
      expect(db2.listObdSessions().some((s) => s.id === manual.id)).toBe(true);
      expect((db2.obd.diagOp("snapshot:list", {}) as Array<{ id: number }>).length).toBeGreaterThanOrEqual(2);
      expect(db2.obd.diagOp("compare:get", { id: report.id })).toMatchObject({ note: "review report", faultLogId: log.id });
      expect(db2.obd.diagOp("guide:get", { id: cas.id })).toMatchObject({ faultLogId: log.id, stepResults: [{ stepId, note: "ok", result: "normal" }] });
      db2.close();
    } finally {
      try { db.close(); } catch { /* */ }
      fs.unlinkSync(file); fs.rmdirSync(dir);
    }
  });
});
