import type { DatabaseSync } from "node:sqlite";
import {
  assertGuideStepResult,
  buildGuideChecklist,
  buildSymptomChecklist,
  compareSnapshots,
  declaredBindingPatch,
  DIAG_SOURCES,
  IDENTITY_KINDS,
  isValidVin,
  MODULE_COVERAGES,
  parseJsonBounded,
  type DiagSnapshot,
  type SnapshotModule,
  createObdPlan,
  decodeObdObservation,
  OBD_DECODER_VERSION,
  type ObdObservation,
  type ObdRecording,
  type ObdRun,
} from "@porsche981/domain";

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
    this.migrateDiagGuide();
    this.migrateDiagEventId();
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

  private migrateDiagGuide() {
    if (this.db.prepare("SELECT version FROM obd_schema_migrations WHERE version = 4").get()) return;
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS obd_diag_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          at TEXT NOT NULL,
          source TEXT NOT NULL,
          identity_kind TEXT NOT NULL,
          vehicle_key TEXT,
          identity_label TEXT NOT NULL,
          completeness TEXT NOT NULL,
          fingerprint TEXT NOT NULL,
          payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS obd_diag_snapshots_fp ON obd_diag_snapshots(fingerprint);
        CREATE TABLE IF NOT EXISTS obd_guide_cases (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          identity_kind TEXT NOT NULL,
          vehicle_key TEXT,
          source TEXT NOT NULL,
          module_key TEXT NOT NULL,
          ecu TEXT NOT NULL,
          code TEXT NOT NULL,
          open INTEGER NOT NULL DEFAULT 1,
          fingerprint TEXT NOT NULL,
          checklist_json TEXT NOT NULL,
          fault_log_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS obd_guide_step_results (
          case_id INTEGER NOT NULL REFERENCES obd_guide_cases(id),
          step_id TEXT NOT NULL,
          result TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          PRIMARY KEY (case_id, step_id)
        );
        CREATE TABLE IF NOT EXISTS obd_compare_reports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          before_id INTEGER NOT NULL,
          after_id INTEGER NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          result_json TEXT NOT NULL,
          fault_log_id INTEGER
        );
      `);
      this.db.prepare("INSERT INTO obd_schema_migrations VALUES (4, ?)").run(new Date().toISOString());
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
  }

  private migrateDiagEventId() {
    if (this.db.prepare("SELECT version FROM obd_schema_migrations WHERE version = 5").get()) return;
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const cols = this.db.prepare("PRAGMA table_info(obd_diag_snapshots)").all() as Array<{ name: string }>;
      if (cols.length && !cols.some((c) => c.name === "capture_event_id")) {
        this.db.exec("ALTER TABLE obd_diag_snapshots ADD COLUMN capture_event_id TEXT");
      }
      this.db.exec("CREATE UNIQUE INDEX IF NOT EXISTS obd_diag_snapshots_event ON obd_diag_snapshots(capture_event_id) WHERE capture_event_id IS NOT NULL AND capture_event_id != ''");
      this.db.prepare("INSERT INTO obd_schema_migrations VALUES (5, ?)").run(new Date().toISOString());
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
  }

  private requireId(v: unknown, name: string): number {
    const n = typeof v === "number" ? v : Number(v);
    if (!Number.isSafeInteger(n) || n < 1) throw new Error(`obd_invalid_${name}`);
    return n;
  }

  private requireEnum<T extends string>(v: unknown, allowed: readonly T[], name: string): T {
    if (typeof v !== "string" || !(allowed as readonly string[]).includes(v)) throw new Error(`obd_invalid_${name}`);
    return v as T;
  }

  captureSnapshot(raw: unknown) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("obd_invalid_snapshot");
    const o = raw as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(o, "__proto__")) throw new Error("obd_invalid_snapshot");
    const source = this.requireEnum(o.source, DIAG_SOURCES, "source");
    const identityKind = this.requireEnum(o.identityKind, IDENTITY_KINDS, "identity");
    const at = String(o.at || "");
    if (!at || Number.isNaN(Date.parse(at))) throw new Error("obd_invalid_time");
    const bounded = (value: unknown, max: number): string => {
      if (value != null && typeof value !== "string") throw new Error("obd_invalid_field");
      const text = String(value ?? "");
      if (text.length > max || text.includes("\0")) throw new Error("obd_invalid_field");
      return text;
    };
    const captureEventId = bounded(o.captureEventId, 240);
    if (!captureEventId) throw new Error("obd_capture_event_required");
    if (!Array.isArray(o.modules) || o.modules.length > 80) throw new Error("obd_invalid_modules");
    const moduleKeys = new Set<string>();
    const modules: SnapshotModule[] = o.modules.map((m) => {
      if (!m || typeof m !== "object") throw new Error("obd_invalid_module");
      const row = m as Record<string, unknown>;
      const coverage = this.requireEnum(row.coverage, MODULE_COVERAGES, "coverage");
      if (!Array.isArray(row.dtcs)) throw new Error("obd_invalid_dtc");
      if (row.dtcs.length > 200) throw new Error("obd_too_many_dtcs");
      const dtcs = row.dtcs.map((d) => {
        if (!d || typeof d !== "object") throw new Error("obd_invalid_dtc");
        const x = d as Record<string, unknown>;
        const dtcHex = bounded(x.dtcHex || x.code, 32).toUpperCase();
        if (!dtcHex) throw new Error("obd_invalid_dtc");
        return {
          code: dtcHex,
          dtcHex,
          statusHex: x.statusHex == null ? null : bounded(x.statusHex, 16).toUpperCase(),
          subtype: x.subtype == null ? null : bounded(x.subtype, 32),
          status: x.status == null ? null : bounded(x.status, 80),
          display: x.display == null ? dtcHex : bounded(x.display, 32),
        };
      });
      if (new Set(dtcs.map((d) => d.dtcHex)).size !== dtcs.length) throw new Error("obd_duplicate_dtc");
      if (coverage === "success-none" && dtcs.length) throw new Error("obd_none_with_dtcs");
      if (coverage === "success-dtc" && !dtcs.length) throw new Error("obd_dtc_without_codes");
      const moduleKey = bounded(row.moduleKey, 80);
      if (!moduleKey) throw new Error("obd_invalid_module");
      if (moduleKeys.has(moduleKey)) throw new Error("obd_duplicate_module");
      moduleKeys.add(moduleKey);
      return {
        moduleKey,
        name: bounded(row.name, 80),
        ecuVariant: row.ecuVariant == null ? null : bounded(row.ecuVariant, 500),
        coverage,
        dtcs,
        rawRef: row.rawRef == null ? null : bounded(row.rawRef, 80),
      };
    });
    const completeness = this.requireEnum(
      o.completeness || "partial",
      ["complete", "partial", "failed"] as const,
      "completeness",
    );
    const vehicleKey = o.vehicleKey == null || o.vehicleKey === "" ? null : bounded(o.vehicleKey, 64);
    if (identityKind === "vin" && !isValidVin(vehicleKey)) throw new Error("obd_invalid_vin");
    if (identityKind === "user-declared" && (!vehicleKey || !o.boundNote || !o.boundAt)) throw new Error("obd_invalid_binding");
    if (completeness === "complete" && (!modules.length || modules.some((m) => !["success-dtc", "success-none"].includes(m.coverage)))) throw new Error("obd_invalid_completeness");
    const identityLabel = String(o.identityLabel || identityKind).slice(0, 160);
    const payload = {
      at, source, identityKind, vehicleKey, identityLabel, completeness, modules, captureEventId,
      boundAt: o.boundAt == null ? null : String(o.boundAt),
      boundNote: o.boundNote == null ? null : String(o.boundNote).slice(0, 500),
    };
    const json = JSON.stringify(payload);
    if (json.length > 200_000) throw new Error("obd_snapshot_too_large");
    const existing = this.db.prepare("SELECT id FROM obd_diag_snapshots WHERE capture_event_id = ? LIMIT 1").get(captureEventId) as { id: number } | undefined;
    if (existing) return this.getSnapshot(existing.id);
    const info = this.db.prepare(
      `INSERT INTO obd_diag_snapshots (at, source, identity_kind, vehicle_key, identity_label, completeness, fingerprint, payload_json, capture_event_id)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(at, source, identityKind, vehicleKey, identityLabel, completeness, `event:${captureEventId}`, json, captureEventId);
    return this.getSnapshot(Number(info.lastInsertRowid));
  }

  assignSnapshot(id: unknown, note: unknown, bindingKey?: unknown) {
    const snap = this.getSnapshot(this.requireId(id, "snapshot_id"));
    if (!snap) throw new Error("obd_snapshot_not_found");
    if (snap.identityKind === "vin") throw new Error("obd_observed_vin_cannot_be_reassigned");
    const patch = declaredBindingPatch(String(note || ""), String(bindingKey || "local:garage-981"));
    const next = { ...snap, ...patch, fingerprint: `event:${snap.captureEventId}` };
    const json = JSON.stringify(next);
    this.db.prepare(
      "UPDATE obd_diag_snapshots SET identity_kind = ?, vehicle_key = ?, identity_label = ?, payload_json = ? WHERE id = ?",
    ).run(patch.identityKind, patch.vehicleKey, patch.identityLabel, json, snap.id);
    return this.getSnapshot(snap.id as number);
  }

  listSnapshots(): DiagSnapshot[] {
    return this.db.prepare("SELECT id FROM obd_diag_snapshots ORDER BY id DESC LIMIT 100").all()
      .map((r) => this.getSnapshot(Number((r as { id: number }).id))!)
      .filter(Boolean);
  }

  getSnapshot(id: number): DiagSnapshot | null {
    const n = this.requireId(id, "snapshot_id");
    const row = this.db.prepare("SELECT id, payload_json AS json FROM obd_diag_snapshots WHERE id = ?").get(n) as { id: number; json: string } | undefined;
    if (!row) return null;
    const payload = parseJsonBounded(row.json) as Omit<DiagSnapshot, "id">;
    return { ...payload, id: row.id, captureEventId: payload.captureEventId || "" };
  }

  listLegacyScans(): Array<{ id: number; at: string; vehicleKey: string; source: "legacy-unknown" }> {
    return this.db.prepare("SELECT id, at, vehicle_key AS vehicleKey FROM obd_scan_events ORDER BY id DESC LIMIT 50").all()
      .map((r) => ({
        id: Number((r as { id: number }).id),
        at: String((r as { at: string }).at),
        vehicleKey: String((r as { vehicleKey: string }).vehicleKey),
        source: "legacy-unknown" as const,
      }));
  }

  createGuideCase(input: unknown) {
    if (!input || typeof input !== "object") throw new Error("obd_invalid_guide");
    const o = input as Record<string, unknown>;
    const plan = o.symptom && !o.code
      ? buildSymptomChecklist({ symptom: String(o.symptom), checks: o.checks == null ? null : String(o.checks), sku: o.sku == null ? null : String(o.sku) })
      : buildGuideChecklist({
        code: String(o.code || ""),
        moduleKey: String(o.moduleKey || ""),
        ecu: o.ecu == null ? undefined : String(o.ecu),
        ecuContext: o.ecuContext === "dme" || o.ecuContext === "gateway" || o.ecuContext === "unknown" ? o.ecuContext : undefined,
        observedCodes: Array.isArray(o.observedCodes) ? o.observedCodes as never : undefined,
      });
    const snapshotId = o.snapshotId == null || o.snapshotId === "" ? null : this.requireId(o.snapshotId, "snapshot_id");
    const snapshot = snapshotId ? this.getSnapshot(snapshotId) : null;
    if (snapshotId && !snapshot) throw new Error("obd_snapshot_not_found");
    if (snapshot && !snapshot.modules.some((m) => m.moduleKey === plan.moduleKey && m.dtcs.some((d) => d.display === plan.code || d.code === plan.code))) throw new Error("obd_guide_snapshot_mismatch");
    const source = snapshot?.source ?? this.requireEnum(o.source || "manual", DIAG_SOURCES, "source");
    const identityKind = snapshot?.identityKind ?? this.requireEnum(o.identityKind || "unknown", IDENTITY_KINDS, "identity");
    const vehicleKey = snapshot ? snapshot.vehicleKey : (o.vehicleKey == null || o.vehicleKey === "" ? null : String(o.vehicleKey).slice(0, 64));
    if (source !== "manual" && !snapshot) throw new Error("obd_guide_snapshot_required");
    const checklist = { ...plan, snapshotId, symptom: o.symptom ? String(o.symptom) : null, sku: o.symptom && o.sku ? String(o.sku) : null };
    const fingerprint = JSON.stringify(["guide-v2", identityKind, vehicleKey, source, checklist.moduleKey, checklist.code, snapshotId, checklist.ecuContext, checklist.symptom]);
    const open = this.db.prepare("SELECT id FROM obd_guide_cases WHERE fingerprint = ? AND open = 1 LIMIT 1").get(fingerprint) as { id: number } | undefined;
    if (open) return this.getGuideCase(open.id);
    const now = new Date().toISOString();
    const json = JSON.stringify(checklist);
    if (json.length > 200_000) throw new Error("obd_guide_too_large");
    const info = this.db.prepare(
      `INSERT INTO obd_guide_cases (created_at, updated_at, identity_kind, vehicle_key, source, module_key, ecu, code, open, fingerprint, checklist_json)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
    ).run(now, now, identityKind, vehicleKey, source, checklist.moduleKey, checklist.ecu, checklist.code, fingerprint, json);
    return this.getGuideCase(Number(info.lastInsertRowid));
  }

  listGuideCases() {
    return this.db.prepare("SELECT id FROM obd_guide_cases ORDER BY id DESC LIMIT 50").all()
      .map((r) => this.getGuideCase(Number((r as { id: number }).id)));
  }

  getGuideCase(id: number) {
    const n = this.requireId(id, "case_id");
    const row = this.db.prepare(`SELECT * FROM obd_guide_cases WHERE id = ?`).get(n) as Record<string, unknown> | undefined;
    if (!row) return null;
    const checklist = parseJsonBounded(String(row.checklist_json)) as { steps: Array<{ id: string }>; snapshotId?: number; symptom?: string; sku?: string; ecuContext?: string };
    const steps = this.db.prepare(
      "SELECT step_id AS stepId, result, note, updated_at AS updatedAt FROM obd_guide_step_results WHERE case_id = ?",
    ).all(n);
    return {
      id: n,
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
      identityKind: String(row.identity_kind),
      vehicleKey: (row.vehicle_key as string | null) ?? null,
      source: String(row.source),
      moduleKey: String(row.module_key),
      ecu: String(row.ecu),
      code: String(row.code),
      snapshotId: checklist.snapshotId ?? null,
      symptom: checklist.symptom ?? null,
      sku: checklist.sku ?? null,
      ecuContext: checklist.ecuContext ?? "unknown",
      open: Number(row.open) === 1,
      faultLogId: row.fault_log_id == null ? null : Number(row.fault_log_id),
      checklist,
      stepResults: steps,
    };
  }

  setGuideStep(input: unknown) {
    if (!input || typeof input !== "object") throw new Error("obd_invalid_step");
    const o = input as Record<string, unknown>;
    const caseId = this.requireId(o.caseId, "case_id");
    const expected = o.updatedAt == null ? null : String(o.updatedAt);
    const row = this.db.prepare("SELECT updated_at AS u FROM obd_guide_cases WHERE id = ?").get(caseId) as { u: string } | undefined;
    if (!row) throw new Error("obd_guide_not_found");
    if (expected && expected !== row.u) throw new Error("obd_guide_stale");
    const stepId = String(o.stepId || "");
    if (!stepId || stepId.length > 400) throw new Error("obd_invalid_step_id");
    const cas = this.getGuideCase(caseId)!;
    if (!cas.checklist.steps.some((s) => s.id === stepId)) throw new Error("obd_invalid_step_id");
    const result = assertGuideStepResult(o.result);
    const note = String(o.note ?? "").slice(0, 2000);
    const now = new Date(Math.max(Date.now(), Date.parse(row.u) + 1)).toISOString();
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db.prepare(
        `INSERT INTO obd_guide_step_results (case_id, step_id, result, note, updated_at) VALUES (?, ?, ?, ?, ?)
         ON CONFLICT(case_id, step_id) DO UPDATE SET result=excluded.result, note=excluded.note, updated_at=excluded.updated_at`,
      ).run(caseId, stepId, result, note, now);
      this.db.prepare("UPDATE obd_guide_cases SET updated_at = ? WHERE id = ?").run(now, caseId);
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
    return this.getGuideCase(caseId);
  }

  linkGuideFaultLog(caseId: number, faultLogId: number) {
    const c = this.requireId(caseId, "case_id");
    const f = this.requireId(faultLogId, "fault_log_id");
    const exists = this.db.prepare("SELECT id FROM fault_logs WHERE id = ?").get(f);
    if (!exists) throw new Error("obd_fault_log_not_found");
    const current = this.db.prepare("SELECT fault_log_id AS f FROM obd_guide_cases WHERE id = ?").get(c) as { f: number | null } | undefined;
    if (!current) throw new Error("obd_guide_not_found");
    if (current.f && Number(current.f) === f) return this.getGuideCase(c);
    if (current.f) return this.getGuideCase(c);
    this.db.prepare("UPDATE obd_guide_cases SET fault_log_id = ?, updated_at = ? WHERE id = ?").run(f, new Date().toISOString(), c);
    return this.getGuideCase(c);
  }

  previewCompare(beforeId: unknown, afterId: unknown) {
    const before = this.getSnapshot(this.requireId(beforeId, "before_id"));
    const after = this.getSnapshot(this.requireId(afterId, "after_id"));
    if (!before || !after) throw new Error("obd_snapshot_not_found");
    return compareSnapshots(before, after);
  }

  saveCompare(input: unknown) {
    if (!input || typeof input !== "object") throw new Error("obd_invalid_compare");
    const o = input as Record<string, unknown>;
    const result = this.previewCompare(o.beforeId, o.afterId);
    if (!result.ok) throw new Error(result.error);
    const note = String(o.note ?? "").slice(0, 2000);
    const faultLogId = o.faultLogId == null || o.faultLogId === "" ? null : this.requireId(o.faultLogId, "fault_log_id");
    if (faultLogId && !this.db.prepare("SELECT id FROM fault_logs WHERE id = ?").get(faultLogId)) throw new Error("obd_fault_log_not_found");
    const fp = `${result.beforeId}:${result.afterId}:${note}`;
    const dup = this.db.prepare(
      "SELECT id, fault_log_id AS f FROM obd_compare_reports WHERE before_id = ? AND after_id = ? AND note = ? LIMIT 1",
    ).get(result.beforeId, result.afterId, note) as { id: number; f: number | null } | undefined;
    if (dup) {
      if (faultLogId && !dup.f) {
        this.db.prepare("UPDATE obd_compare_reports SET fault_log_id = ? WHERE id = ?").run(faultLogId, dup.id);
      }
      return this.getCompare(dup.id);
    }
    const json = JSON.stringify({ ...result, note });
    if (json.length > 200_000) throw new Error("obd_report_too_large");
    const info = this.db.prepare(
      "INSERT INTO obd_compare_reports (created_at, before_id, after_id, note, result_json, fault_log_id) VALUES (?, ?, ?, ?, ?, ?)",
    ).run(new Date().toISOString(), result.beforeId, result.afterId, note, json, faultLogId);
    void fp;
    return this.getCompare(Number(info.lastInsertRowid));
  }

  listCompares() {
    return this.db.prepare("SELECT id FROM obd_compare_reports ORDER BY id DESC LIMIT 50").all()
      .map((r) => this.getCompare(Number((r as { id: number }).id)));
  }

  getCompare(id: number) {
    const n = this.requireId(id, "report_id");
    const row = this.db.prepare("SELECT * FROM obd_compare_reports WHERE id = ?").get(n) as Record<string, unknown> | undefined;
    if (!row) return null;
    return {
      id: n,
      createdAt: String(row.created_at),
      beforeId: Number(row.before_id),
      afterId: Number(row.after_id),
      note: String(row.note || ""),
      faultLogId: row.fault_log_id == null ? null : Number(row.fault_log_id),
      result: parseJsonBounded(String(row.result_json)),
    };
  }

  diagOp(op: string, value: Record<string, unknown> | null) {
    const v = value ?? {};
    if (op === "snapshot:capture") return this.captureSnapshot(v);
    if (op === "snapshot:list") return this.listSnapshots();
    if (op === "snapshot:get") return this.getSnapshot(this.requireId(v.id, "snapshot_id"));
    if (op === "snapshot:assign") return this.assignSnapshot(v.id, v.note, v.bindingKey);
    if (op === "scan:listLegacy") return this.listLegacyScans();
    if (op === "guide:create") return this.createGuideCase(v);
    if (op === "guide:list") return this.listGuideCases();
    if (op === "guide:get") return this.getGuideCase(this.requireId(v.id, "case_id"));
    if (op === "guide:setStep") return this.setGuideStep(v);
    if (op === "guide:linkFaultLog") return this.linkGuideFaultLog(this.requireId(v.caseId, "case_id"), this.requireId(v.faultLogId, "fault_log_id"));
    if (op === "compare:preview") return this.previewCompare(v.beforeId, v.afterId);
    if (op === "compare:save") return this.saveCompare(v);
    if (op === "compare:list") return this.listCompares();
    if (op === "compare:get") return this.getCompare(this.requireId(v.id, "report_id"));
    throw new Error("obd_unknown_diag_op");
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
