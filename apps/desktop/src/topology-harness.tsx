import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ObdPage } from "./pages/ObdPage";
import type { ReadOnlySessionRequest, ReadOnlySessionResult } from "./api";
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
