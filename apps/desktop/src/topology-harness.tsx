import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ObdPage } from "./pages/ObdPage";
import type { ReadOnlySessionRequest, ReadOnlySessionResult } from "./api";
import {
  buildGuideChecklist,
  buildSymptomChecklist,
  compareSnapshots,
  declaredBindingPatch,
  type DiagSnapshot,
} from "@porsche981/domain";
import "./styles.css";

const FLAGS = { executionEnabled: false, liveVerified: false, writePayload: null as null };

function dtcFinal(profileId: string, scenario: string, sessionTask?: string): Record<string, unknown> {
  const gw = profileId.includes("gateway");
  const op = gw ? "gw-dtc" : "dme-dtc";
  const dtype = gw ? "uds-dtc-19-02" : "kwp-dtc-18";
  if (scenario === "identity-mismatch") {
    return {
      ok: false,
      status: "failed",
      error: "identity-mismatch",
      simulation: true,
      mode: "simulation",
      profileId,
      identityQualification: { observedProfileMatch: false },
      results: [],
    };
  }
  if (scenario === "disconnect" || scenario === "pending-timeout" || scenario === "negative") {
    return {
      ok: false,
      status: "failed",
      error: scenario,
      simulation: true,
      mode: "simulation",
      profileId,
      results: [],
    };
  }
  const records =
    sessionTask === "clear"
      ? window.__TOPO_CLEAR_RESIDUAL__
        ? [{ dtcHex: "C447", statusHex: "28", displayCode: "U0447", observedText: "残留" }]
        : []
      : gw
        ? []
        : [
            { dtcHex: "C447", statusHex: "28", displayCode: "U0447", observedText: "与变速器通讯丢失" },
            { dtcHex: "C412", statusHex: "28", displayCode: "U0412" },
          ];
  return {
    ok: true,
    status: "completed",
    simulation: true,
    mode: "simulation",
    profileId,
    runId: "simokrunidxxxxxxxx",
    identityQualification: { observedProfileMatch: true, liveVerified: false },
    results: [
      { role: "identity", field: "hardware", ok: true, decoded: { ok: true, text: "HW-SIM" } },
      {
        role: "dependent",
        operationId: op,
        ok: true,
        freshlyRead: true,
        historical: false,
        capturedUtc: "2026-09-27T08:00:00Z",
        decoded: { ok: true, type: dtype, records },
      },
    ],
  };
}

function copy<T>(v: T): T {
  return structuredClone(v);
}

function installDiagMemory() {
  const KEY = "steuer14-diag-memory";
  type Store = {
    snaps: DiagSnapshot[];
    cases: Array<Record<string, unknown>>;
    reports: Array<Record<string, unknown>>;
    nid: number;
  };
  function load(): Store {
    try {
      const raw = sessionStorage.getItem(KEY);
      if (raw) return JSON.parse(raw) as Store;
    } catch { /* */ }
    return { snaps: [], cases: [], reports: [], nid: 1 };
  }
  function save(s: Store) {
    sessionStorage.setItem(KEY, JSON.stringify(s));
  }
  let st = load();
  return async (request: { op: string; value?: Record<string, unknown> | null }) => {
    const v = request.value || {};
    if (request.op === "snapshot:capture") {
      const eventId = String(v.captureEventId || "");
      if (!eventId) throw new Error("obd_capture_event_required");
      const hit = st.snaps.find((s) => s.captureEventId === eventId);
      if (hit) return copy(hit);
      const row = { ...(v as object), id: st.nid++, captureEventId: eventId, fingerprint: `event:${eventId}` } as DiagSnapshot;
      st.snaps.unshift(row);
      save(st);
      return copy(row);
    }
    if (request.op === "snapshot:list") return copy(st.snaps);
    if (request.op === "snapshot:get") return copy(st.snaps.find((s) => s.id === Number(v.id)) || null);
    if (request.op === "snapshot:assign") {
      const snap = st.snaps.find((s) => s.id === Number(v.id));
      if (!snap) throw new Error("obd_snapshot_not_found");
      const patch = declaredBindingPatch(String(v.note || ""), String(v.bindingKey || "local:garage-981"));
      Object.assign(snap, patch);
      save(st);
      return copy(snap);
    }
    if (request.op === "scan:listLegacy") return [];
    if (request.op === "guide:create") {
      const checklist = v.symptom && !v.code
        ? buildSymptomChecklist({ symptom: String(v.symptom), checks: v.checks == null ? null : String(v.checks), sku: v.sku == null ? null : String(v.sku) })
        : buildGuideChecklist({
          code: String(v.code || ""),
          moduleKey: String(v.moduleKey || ""),
          ecu: v.ecu == null ? undefined : String(v.ecu),
          ecuContext: v.ecuContext === "dme" || v.ecuContext === "gateway" || v.ecuContext === "unknown" ? v.ecuContext : undefined,
        });
      const fp = JSON.stringify(["guide-v2", v.identityKind || "unknown", v.vehicleKey || null, v.source || "manual", checklist.moduleKey, checklist.code, v.snapshotId || null, checklist.ecuContext, v.symptom || null]);
      const open = st.cases.find((c) => c.fingerprint === fp && c.open);
      if (open) return copy(open);
      const now = new Date().toISOString();
      const row = {
        id: st.nid++,
        createdAt: now,
        updatedAt: now,
        identityKind: v.identityKind || "unknown",
        vehicleKey: v.vehicleKey || null,
        source: v.source || "manual",
        moduleKey: checklist.moduleKey,
        ecu: checklist.ecu,
        code: checklist.code,
        snapshotId: v.snapshotId || null,
        symptom: v.symptom || null,
        sku: v.sku || null,
        ecuContext: checklist.ecuContext,
        open: true,
        fingerprint: fp,
        checklist,
        stepResults: [] as Array<{ stepId: string; result: string; note: string }>,
        faultLogId: null,
      };
      st.cases.unshift(row);
      save(st);
      return copy(row);
    }
    if (request.op === "guide:get") return copy(st.cases.find((c) => c.id === Number(v.id)) || null);
    if (request.op === "guide:list") return copy(st.cases);
    if (request.op === "guide:setStep") {
      const cas = st.cases.find((c) => c.id === Number(v.caseId));
      if (!cas) throw new Error("obd_guide_not_found");
      if (v.updatedAt && v.updatedAt !== cas.updatedAt) throw new Error("obd_guide_stale");
      const steps = copy((cas.stepResults as Array<{ stepId: string; result: string; note: string }>) || []);
      const i = steps.findIndex((s) => s.stepId === v.stepId);
      const rec = { stepId: String(v.stepId), result: String(v.result), note: String(v.note || "") };
      if (i >= 0) steps[i] = rec;
      else steps.push(rec);
      cas.stepResults = steps;
      cas.updatedAt = new Date().toISOString();
      save(st);
      return copy(cas);
    }
    if (request.op === "guide:linkFaultLog") {
      const cas = st.cases.find((c) => c.id === Number(v.caseId));
      if (!cas) throw new Error("obd_guide_not_found");
      if (!cas.faultLogId) cas.faultLogId = Number(v.faultLogId);
      save(st);
      return copy(cas);
    }
    if (request.op === "compare:preview") {
      const before = st.snaps.find((s) => s.id === Number(v.beforeId));
      const after = st.snaps.find((s) => s.id === Number(v.afterId));
      if (!before || !after) throw new Error("obd_snapshot_not_found");
      return copy(compareSnapshots(before, after));
    }
    if (request.op === "compare:save") {
      const before = st.snaps.find((s) => s.id === Number(v.beforeId));
      const after = st.snaps.find((s) => s.id === Number(v.afterId));
      if (!before || !after) throw new Error("obd_snapshot_not_found");
      const result = compareSnapshots(before, after);
      if (!result.ok) throw new Error(result.error);
      const dup = st.reports.find((r) => r.beforeId === v.beforeId && r.afterId === v.afterId && r.note === (v.note || ""));
      if (dup) {
        if (v.faultLogId && !(dup as { faultLogId?: number }).faultLogId) (dup as { faultLogId: number }).faultLogId = Number(v.faultLogId);
        save(st);
        return copy(dup);
      }
      const row = { id: st.nid++, beforeId: v.beforeId, afterId: v.afterId, note: v.note || "", result, faultLogId: v.faultLogId || null, createdAt: new Date().toISOString() };
      st.reports.unshift(row);
      save(st);
      return copy(row);
    }
    if (request.op === "compare:list") return copy(st.reports);
    if (request.op === "compare:get") return copy(st.reports.find((r) => r.id === Number(v.id)) || null);
    throw new Error("obd_unknown_diag_op");
  };
}

function installFake() {
  const jobs = new Map<
    string,
    { req: ReadOnlySessionRequest; ticks: number; cancelled: boolean; delayLeft: number }
  >();
  let n = 0;
  let busy = false;
  window.__FAKE_SESSION_CALLS__ = [];

  window.porsche981 = {
    listParts: async () => [],
    getVehicle: async () =>
      ({
        id: 1,
        year: 2014,
        model: "Boxster",
        trim: "S",
        chassis: "981",
        vin: null,
        current_km: 0,
        avg_km_per_day: null,
        paint_name: null,
        paint_code: null,
        interior: null,
        top: null,
        updated_at: "",
      }) as never,
    locatorMap: async () => ({ note: "", zones: [] }),
    listObdSessions: async () => [],
    listFaults: async () => [
      {
        id: 1,
        symptom: "怠速不稳（症状知识，不默认故障码）",
        likely_causes: "按知识库检查项，不套用无关代码",
        checks: "先核对该车实际记录的代码与控制单元",
        related_part_sku: null,
      },
    ],
    listFaultLogs: async () => [],
    listCoding: async () => [],
    searchDtc: async () => [],
    getDtc: async (code: string) => ({ id: 1, code, title_zh: code, likely_causes: "", checks: "", related_part_sku: null }),
    addFaultLog: async () => ({ id: 9, logged_at: "2026-09-30", odometer_km: 0, symptom: "x", area_hypothesis: null, action: null, result: null, closed: 0, related_part_sku: null, coding_snapshot_id: null }),
    wiringIndex: async () => ({ authority: "local-pdf", note: "", pdf: { id: "981-main", label: "x", path: "" }, doNotUse: [], systems: [{ id: "engine", label_zh: "发动机 / DME", status: "stub" }, { id: "can", label_zh: "CAN 网关", status: "stub" }] }),
    openWiringPdf: async () => ({ ok: true }),
    obdDiag: installDiagMemory(),
    readOnlySession: async (req: ReadOnlySessionRequest): Promise<ReadOnlySessionResult> => {
      window.__FAKE_SESSION_CALLS__!.push({ ...req });
      if (req.action === "overview") {
        if (window.__FAKE_OVERVIEW_THROW__) throw new Error("overview_fail");
        const delay = Number(window.__OVERVIEW_DELAY_MS__ || 0);
        if (delay) await new Promise((r) => setTimeout(r, delay));
        if (window.__FAKE_OVERVIEW__) return { ok: true, ...FLAGS, ...window.__FAKE_OVERVIEW__ } as ReadOnlySessionResult;
        return { ok: true, taskState: busy ? "running" : "idle", ...FLAGS };
      }
      if (req.action === "start") {
        if (busy) return { ok: false, error: "session-lock-busy", ...FLAGS };
        const delay = Number(window.__TOPO_DELAY_START_MS__ || 0);
        const jobId = `j${(++n).toString(16).padStart(16, "0")}`;
        busy = true;
        jobs.set(jobId, { req, ticks: 0, cancelled: false, delayLeft: delay });
        return { ok: true, jobId, state: "running", ...FLAGS };
      }
      if (req.action === "cancel" && req.jobId) {
        const j = jobs.get(req.jobId);
        if (j) j.cancelled = true;
        busy = false;
        return { ok: true, state: "cancelled", jobId: req.jobId, ...FLAGS };
      }
      if (req.action === "status" && req.jobId) {
        const j = jobs.get(req.jobId);
        if (!j) return { ok: false, error: "unknown_job", ...FLAGS };
        if (j.delayLeft > 0) {
          j.delayLeft -= 80;
          return { ok: true, jobId: req.jobId, state: "running", ...FLAGS };
        }
        if (j.cancelled) {
          busy = false;
          return {
            ok: true,
            jobId: req.jobId,
            state: "cancelled",
            error: "cancelled",
            final: { status: "cancelled", error: "cancelled", simulation: true, profileId: j.req.profileId },
            ...FLAGS,
          };
        }
        j.ticks += 1;
        const slow = j.req.scenario === "slow" && j.ticks < 8;
        if (slow) return { ok: true, jobId: req.jobId, state: "running", ...FLAGS };
        busy = false;
        const scenario = String(j.req.scenario || window.__TOPO_SCENARIO__ || "success");
        const final = dtcFinal(String(j.req.profileId), scenario, j.req.sessionTask);
        final.runId = req.jobId;
        const failed = final.status !== "completed";
        return {
          ok: true,
          jobId: req.jobId,
          state: failed ? "failed" : "completed",
          final,
          ...FLAGS,
        };
      }
      return { ok: false, error: "invalid_action", ...FLAGS };
    },
  } as never;
}

installFake();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ObdPage />
  </StrictMode>,
);
