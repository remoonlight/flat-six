import type { DatabaseSync } from "node:sqlite";
import { createObdPlan, decodeObdObservation, OBD_DECODER_VERSION, type ObdObservation, type ObdRecording, type ObdRun } from "@porsche981/domain";

type EcuRow = {
  vehicleKey: string;
  moduleKey: string;
  name: string;
  ecuAddress: string | null;
  hardwareId: string | null;
  serial: string | null;
  softwareId: string | null;
  calibrationId: string | null;
  cvn: string | null;
  codingFingerprint: string | null;
  lastSuccessAt: string | null;
};

type EcuChange = {
  vehicleKey: string;
  moduleKey: string;
  field: string;
  previous: string | null;
  next: string | null;
  observedAt: string;
};

/** Initial low-volume offline records are atomic in SQLite, including raw replies. */
export class ObdStore {
  constructor(private db: DatabaseSync) {
    db.exec(`CREATE TABLE IF NOT EXISTS obd_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);`);
    if (!db.prepare("SELECT version FROM obd_schema_migrations WHERE version = 1").get()) {
      db.exec("BEGIN IMMEDIATE");
      try {
        db.exec(`
          CREATE TABLE obd_acquisitions (
            session_id INTEGER PRIMARY KEY REFERENCES obd_sessions(id),
            source TEXT NOT NULL CHECK(source = 'simulation'), scenario TEXT NOT NULL,
            budget_ms INTEGER NOT NULL, status TEXT NOT NULL,
            ended_at TEXT, reason TEXT, decoder_version TEXT NOT NULL
          );
          CREATE TABLE obd_observations (
            session_id INTEGER NOT NULL REFERENCES obd_acquisitions(session_id),
            seq INTEGER NOT NULL, t_ms INTEGER NOT NULL, command TEXT NOT NULL,
            outcome TEXT NOT NULL, raw TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(session_id, seq)
          );
          CREATE TABLE obd_samples (
            session_id INTEGER NOT NULL, tx_seq INTEGER NOT NULL, sample_index INTEGER NOT NULL,
            t_ms INTEGER NOT NULL, ecu TEXT NOT NULL, signal TEXT NOT NULL, context TEXT NOT NULL,
            value REAL NOT NULL, unit TEXT NOT NULL,
            PRIMARY KEY(session_id, tx_seq, sample_index),
            FOREIGN KEY(session_id, tx_seq) REFERENCES obd_observations(session_id, seq)
          );
          CREATE INDEX obd_samples_timeline ON obd_samples(session_id, ecu, signal, t_ms);
        `);
        db.prepare("INSERT INTO obd_schema_migrations VALUES (1, ?)").run(new Date().toISOString());
        db.exec("COMMIT");
      } catch (e) { db.exec("ROLLBACK"); throw e; }
    }
    this.migrateProduction();
    this.migrateVehicleView();
  }
  private migrateProduction() {
    if (this.db.prepare("SELECT version FROM obd_schema_migrations WHERE version = 2").get()) return;
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS obd_adapter_pref (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          port TEXT NOT NULL, friendly_name TEXT NOT NULL, pnp_id TEXT,
          occupied INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS obd_ecu_identity (
          vehicle_key TEXT NOT NULL, module_key TEXT NOT NULL, name TEXT NOT NULL,
          ecu_address TEXT, hardware_id TEXT, serial TEXT, software_id TEXT,
          calibration_id TEXT, cvn TEXT, coding_fingerprint TEXT, last_success_at TEXT,
          PRIMARY KEY (vehicle_key, module_key)
        );
        CREATE TABLE IF NOT EXISTS obd_ecu_changes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          vehicle_key TEXT NOT NULL, module_key TEXT NOT NULL, field TEXT NOT NULL,
          previous TEXT, next TEXT, observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS obd_scan_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, vehicle_key TEXT NOT NULL,
          adapter_json TEXT, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS obd_clear_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
          pre_scan_json TEXT NOT NULL, report_json TEXT NOT NULL, pre_clear_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS obd_vehicle_view_pref (
          id INTEGER PRIMARY KEY CHECK (id = 1), vehicle_key TEXT, updated_at TEXT NOT NULL
        );
      `);
      this.db.prepare("INSERT INTO obd_schema_migrations VALUES (2, ?)").run(new Date().toISOString());
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
  }
  private migrateVehicleView() {
    if (this.db.prepare("SELECT version FROM obd_schema_migrations WHERE version = 3").get()) return;
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const cols = this.db.prepare("PRAGMA table_info(obd_ecu_identity)").all() as Array<{ name: string }>;
      if (cols.length && !cols.some((c) => c.name === "cvn")) {
        this.db.exec("ALTER TABLE obd_ecu_identity ADD COLUMN cvn TEXT");
      }
      const clearCols = this.db.prepare("PRAGMA table_info(obd_clear_events)").all() as Array<{ name: string }>;
      if (clearCols.length && !clearCols.some((c) => c.name === "pre_clear_id")) {
        this.db.exec("ALTER TABLE obd_clear_events ADD COLUMN pre_clear_id INTEGER");
      }
      this.db.exec(`CREATE TABLE IF NOT EXISTS obd_vehicle_view_pref (
        id INTEGER PRIMARY KEY CHECK (id = 1), vehicle_key TEXT, updated_at TEXT NOT NULL
      )`);
      this.db.prepare("INSERT INTO obd_schema_migrations VALUES (3, ?)").run(new Date().toISOString());
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
  }

  begin(input: { scenario?: unknown; budgetMs?: unknown }): ObdRun {
    const plan = createObdPlan(input);
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const started = new Date().toISOString();
      const info = this.db.prepare("INSERT INTO obd_sessions (started_at, adapter, note) VALUES (?, ?, ?)")
        .run(started, "模拟设备（无车辆连接）", `离线演练：${plan.scenario}`);
      const sessionId = Number(info.lastInsertRowid);
      this.db.prepare("INSERT INTO obd_acquisitions (session_id, source, scenario, budget_ms, status, decoder_version) VALUES (?, 'simulation', ?, ?, 'running', ?)")
        .run(sessionId, plan.scenario, plan.budgetMs, OBD_DECODER_VERSION);
      this.db.exec("COMMIT");
      return this.getRun(sessionId);
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
  }
  getRun(sessionId: number): ObdRun {
    if (!Number.isSafeInteger(sessionId) || sessionId < 1) throw new Error("obd_invalid_session");
    const row = this.db.prepare(`SELECT a.session_id AS sessionId, a.source, a.scenario, a.budget_ms AS budgetMs,
      a.status, s.started_at AS startedAt, a.ended_at AS endedAt, a.reason, a.decoder_version AS decoderVersion
      FROM obd_acquisitions a JOIN obd_sessions s ON a.session_id = s.id WHERE a.session_id = ?`).get(sessionId);
    if (!row) throw new Error("obd_acquisition_not_found");
    return row as unknown as ObdRun;
  }
  append(sessionId: number, observation: ObdObservation): { seq: number } {
    const run = this.getRun(sessionId);
    const o = observation;
    if (!o || !Number.isSafeInteger(o.seq) || o.seq < 1 || o.seq > 3000 || !Number.isInteger(o.tMs) || o.tMs < 0 || o.tMs > run.budgetMs + 10_000 || typeof o.raw !== "string" || o.raw.length > 65536) throw new Error("obd_invalid_observation");
    const decoded = decodeObdObservation(o.command, o.raw, o.seq, o.tMs);
    if (["timeout", "disconnected", "cancelled"].includes(o.outcome) && !o.raw) { decoded.outcome = o.outcome; decoded.detail = o.outcome; }
    const json = JSON.stringify(decoded);
    const existing = this.db.prepare("SELECT data_json FROM obd_observations WHERE session_id = ? AND seq = ?").get(sessionId, o.seq);
    if (existing) {
      if ((existing as { data_json: string }).data_json !== json) throw new Error("obd_sequence_conflict");
      return { seq: o.seq };
    }
    if (run.status !== "running") throw new Error("obd_session_closed");
    const last = this.db.prepare("SELECT seq, t_ms FROM obd_observations WHERE session_id = ? ORDER BY seq DESC LIMIT 1").get(sessionId) as { seq: number; t_ms: number } | undefined;
    if (o.seq !== Number(last?.seq ?? 0) + 1 || o.tMs < Number(last?.t_ms ?? 0)) throw new Error("obd_sequence_gap");
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db.prepare("INSERT INTO obd_observations VALUES (?, ?, ?, ?, ?, ?, ?)").run(sessionId, o.seq, o.tMs, o.command, decoded.outcome, o.raw, json);
      const insert = this.db.prepare("INSERT INTO obd_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)");
      decoded.samples.forEach((s, i) => insert.run(sessionId, o.seq, i, o.tMs, s.ecu, s.signal, s.context, s.value, s.unit));
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
    return { seq: o.seq };
  }
  finish(input: { sessionId: number; status: string; endedAt?: string; reason?: string }): ObdRun {
    const run = this.getRun(input.sessionId);
    if (!["completed", "partial", "cancelled", "interrupted"].includes(input.status)) throw new Error("obd_invalid_run_status");
    if (run.status !== "running") return run;
    this.db.prepare("UPDATE obd_acquisitions SET status = ?, ended_at = ?, reason = ? WHERE session_id = ?")
      .run(input.status, new Date().toISOString(), String(input.reason ?? "").slice(0, 1000), input.sessionId);
    return this.getRun(input.sessionId);
  }
  recover(): number {
    return Number(this.db.prepare("UPDATE obd_acquisitions SET status = 'interrupted', ended_at = ?, reason = '上次采集未正常结束；仅保留已确认落盘的记录' WHERE status = 'running'").run(new Date().toISOString()).changes);
  }
  recording(sessionId: number): ObdRecording {
    const run = this.getRun(sessionId);
    const observations = this.db.prepare("SELECT data_json FROM obd_observations WHERE session_id = ? ORDER BY seq").all(sessionId).map((row) => JSON.parse(String((row as { data_json: string }).data_json)) as ObdObservation);
    return { version: 1, run, observations };
  }
  list(): ObdRun[] {
    return this.db.prepare("SELECT session_id FROM obd_acquisitions ORDER BY session_id DESC LIMIT 100").all().map((row) => this.getRun(Number((row as { session_id: number }).session_id)));
  }

  getAdapterPref() {
    return this.db.prepare("SELECT port, friendly_name AS friendlyName, pnp_id AS pnpId, occupied, updated_at AS updatedAt FROM obd_adapter_pref WHERE id = 1").get() ?? null;
  }
  setAdapterPref(adapter: { port: string; friendlyName: string; pnpId?: string | null; occupied?: boolean }) {
    this.db.prepare(`INSERT INTO obd_adapter_pref (id, port, friendly_name, pnp_id, occupied, updated_at)
      VALUES (1, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET port=excluded.port, friendly_name=excluded.friendly_name, pnp_id=excluded.pnp_id, occupied=excluded.occupied, updated_at=excluded.updated_at`)
      .run(adapter.port, adapter.friendlyName, adapter.pnpId ?? null, adapter.occupied ? 1 : 0, new Date().toISOString());
    return this.getAdapterPref();
  }
  saveScan(scan: unknown) {
    const s = scan as { at: string; vehicleKey: string; adapter?: unknown };
    const info = this.db.prepare("INSERT INTO obd_scan_events (at, vehicle_key, adapter_json, payload_json) VALUES (?, ?, ?, ?)")
      .run(s.at, s.vehicleKey, JSON.stringify(s.adapter ?? null), JSON.stringify(scan));
    return Number(info.lastInsertRowid);
  }
  beginClear(pre: unknown) {
    const at = new Date().toISOString();
    const info = this.db.prepare("INSERT INTO obd_clear_events (at, pre_scan_json, report_json) VALUES (?, ?, ?)")
      .run(at, JSON.stringify(pre ?? null), JSON.stringify({ pending: true }));
    return Number(info.lastInsertRowid);
  }
  saveClear(report: Record<string, unknown>) {
    const at = String(report.at ?? new Date().toISOString());
    const json = JSON.stringify(report);
    const preClearId = Number(report.preClearId);
    if (!Number.isSafeInteger(preClearId) || preClearId <= 0) throw new Error('obd_clear_evidence_id_required');
    const result = this.db.prepare("UPDATE obd_clear_events SET at = ?, report_json = ?, pre_clear_id = ? WHERE id = ? AND json_extract(report_json, '$.pending') = 1")
      .run(at, json, preClearId, preClearId);
    if (Number(result.changes) !== 1) throw new Error('obd_clear_evidence_not_pending');
    return preClearId;
  }
  getClear(id: number) {
    return this.db.prepare("SELECT id, at, pre_scan_json AS preScanJson, report_json AS reportJson, pre_clear_id AS preClearId FROM obd_clear_events WHERE id = ?").get(id) ?? null;
  }
  knownVins(): string[] {
    return this.db.prepare("SELECT DISTINCT vehicle_key AS k FROM obd_ecu_identity").all()
      .map((r) => String((r as { k: string }).k)).filter((k) => /^[A-HJ-NPR-Z0-9]{17}$/.test(k));
  }
  listVehicleKeys(): string[] {
    return this.db.prepare("SELECT DISTINCT vehicle_key AS k FROM obd_ecu_identity ORDER BY vehicle_key").all()
      .map((r) => String((r as { k: string }).k));
  }
  getVehicleView(): string | null {
    const row = this.db.prepare("SELECT vehicle_key AS k FROM obd_vehicle_view_pref WHERE id = 1").get() as { k: string | null } | undefined;
    return row?.k ?? null;
  }
  setVehicleView(vehicleKey: string | null) {
    this.db.prepare(`INSERT INTO obd_vehicle_view_pref (id, vehicle_key, updated_at) VALUES (1, ?, ?)
      ON CONFLICT(id) DO UPDATE SET vehicle_key=excluded.vehicle_key, updated_at=excluded.updated_at`)
      .run(vehicleKey, new Date().toISOString());
    return this.getVehicleView();
  }
  getEcu(vehicleKey: string, moduleKey: string) {
    const row = this.db.prepare("SELECT * FROM obd_ecu_identity WHERE vehicle_key = ? AND module_key = ?").get(vehicleKey, moduleKey) as Record<string, unknown> | undefined;
    return row ? mapEcu(row) : null;
  }
  upsertEcu(row: EcuRow, changes: EcuChange[]) {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db.prepare(`INSERT INTO obd_ecu_identity (vehicle_key, module_key, name, ecu_address, hardware_id, serial, software_id, calibration_id, cvn, coding_fingerprint, last_success_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vehicle_key, module_key) DO UPDATE SET
          name=excluded.name,
          ecu_address=COALESCE(excluded.ecu_address, ecu_address),
          hardware_id=COALESCE(excluded.hardware_id, hardware_id),
          serial=COALESCE(excluded.serial, serial),
          software_id=COALESCE(excluded.software_id, software_id),
          calibration_id=COALESCE(excluded.calibration_id, calibration_id),
          cvn=COALESCE(excluded.cvn, cvn),
          coding_fingerprint=COALESCE(excluded.coding_fingerprint, coding_fingerprint),
          last_success_at=COALESCE(excluded.last_success_at, last_success_at)`).run(
        row.vehicleKey, row.moduleKey, row.name, row.ecuAddress, row.hardwareId, row.serial,
        row.softwareId, row.calibrationId, row.cvn ?? null, row.codingFingerprint, row.lastSuccessAt,
      );
      const ins = this.db.prepare("INSERT INTO obd_ecu_changes (vehicle_key, module_key, field, previous, next, observed_at) VALUES (?, ?, ?, ?, ?, ?)");
      for (const c of changes) ins.run(c.vehicleKey, c.moduleKey, c.field, c.previous, c.next, c.observedAt);
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
    return this.getEcu(row.vehicleKey, row.moduleKey);
  }
  listEcus(vehicleKey: string) {
    return this.db.prepare("SELECT * FROM obd_ecu_identity WHERE vehicle_key = ?").all(vehicleKey).map((r) => mapEcu(r as Record<string, unknown>));
  }
  listEcuChanges(vehicleKey: string): EcuChange[] {
    return this.db.prepare("SELECT vehicle_key AS vehicleKey, module_key AS moduleKey, field, previous, next, observed_at AS observedAt FROM obd_ecu_changes WHERE vehicle_key = ? ORDER BY id DESC LIMIT 50")
      .all(vehicleKey) as EcuChange[];
  }
  rekeyEcus(from: string, to: string) {
    if (!from || from === to) return;
    if (/^[A-HJ-NPR-Z0-9]{17}$/.test(from) && from !== to) return;
    const now = new Date().toISOString();
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const incoming = this.listEcus(from);
      for (const src of incoming) {
        const dest = this.getEcu(to, src.moduleKey);
        if (!dest) {
          this.db.prepare(`UPDATE obd_ecu_identity SET vehicle_key = ? WHERE vehicle_key = ? AND module_key = ?`).run(to, from, src.moduleKey);
        } else {
          const merged: EcuRow = {
            ...dest,
            name: dest.name || src.name,
            ecuAddress: dest.ecuAddress ?? src.ecuAddress,
            hardwareId: dest.hardwareId ?? src.hardwareId,
            serial: dest.serial ?? src.serial,
            softwareId: dest.softwareId ?? src.softwareId,
            calibrationId: dest.calibrationId ?? src.calibrationId,
            cvn: dest.cvn ?? src.cvn,
            codingFingerprint: dest.codingFingerprint ?? src.codingFingerprint,
            lastSuccessAt: dest.lastSuccessAt ?? src.lastSuccessAt,
          };
          this.db.prepare(`UPDATE obd_ecu_identity SET name=?, ecu_address=?, hardware_id=?, serial=?, software_id=?, calibration_id=?, cvn=?, coding_fingerprint=?, last_success_at=?
            WHERE vehicle_key = ? AND module_key = ?`).run(
            merged.name, merged.ecuAddress, merged.hardwareId, merged.serial, merged.softwareId,
            merged.calibrationId, merged.cvn, merged.codingFingerprint, merged.lastSuccessAt, to, src.moduleKey,
          );
          this.db.prepare("DELETE FROM obd_ecu_identity WHERE vehicle_key = ? AND module_key = ?").run(from, src.moduleKey);
        }
      }
      this.db.prepare("UPDATE obd_ecu_changes SET vehicle_key = ? WHERE vehicle_key = ?").run(to, from);
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
    void now;
  }

  productionOp(op: string, value: Record<string, unknown> | null) {
    const v = value ?? {};
    if (op === "adapter:set") return this.setAdapterPref(v as { port: string; friendlyName: string; pnpId?: string | null; occupied?: boolean });
    if (op === "adapter:get") return this.getAdapterPref();
    if (op === "scan:save") return this.saveScan(v);
    if (op === "clear:pre") return this.beginClear(v);
    if (op === "clear:save") return this.saveClear(v);
    if (op === "ecu:knownVins") return this.knownVins();
    if (op === "ecu:get") return this.getEcu(String(v.vehicleKey), String(v.moduleKey));
    if (op === "ecu:upsert") return this.upsertEcu(v.row as EcuRow, (v.changes as EcuChange[]) ?? []);
    if (op === "ecu:list") return this.listEcus(String(v.vehicleKey));
    if (op === "ecu:changes") return this.listEcuChanges(String(v.vehicleKey));
    if (op === "ecu:rekey") return this.rekeyEcus(String(v.from), String(v.to));
    if (op === "vehicle:list") return this.listVehicleKeys();
    if (op === "vehicle:getView") return this.getVehicleView();
    if (op === "vehicle:setView") return this.setVehicleView(v.vehicleKey == null ? null : String(v.vehicleKey));
    throw new Error("obd_unknown_production_op");
  }
}

function mapEcu(row: Record<string, unknown>): EcuRow {
  return {
    vehicleKey: String(row.vehicle_key),
    moduleKey: String(row.module_key),
    name: String(row.name),
    ecuAddress: (row.ecu_address as string | null) ?? null,
    hardwareId: (row.hardware_id as string | null) ?? null,
    serial: (row.serial as string | null) ?? null,
    softwareId: (row.software_id as string | null) ?? null,
    calibrationId: (row.calibration_id as string | null) ?? null,
    cvn: (row.cvn as string | null) ?? null,
    codingFingerprint: (row.coding_fingerprint as string | null) ?? null,
    lastSuccessAt: (row.last_success_at as string | null) ?? null,
  };
}
