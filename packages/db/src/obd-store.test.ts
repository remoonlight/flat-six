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
});
