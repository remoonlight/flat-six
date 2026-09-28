import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ObdPage } from "./pages/ObdPage";
import type { ObdConnectionRequest, ObdConnectionResult, ReadOnlySessionRequest, ReadOnlySessionResult } from "./api";
import "./styles.css";

const FLAGS = { executionEnabled: false, liveVerified: false, writePayload: null as null };

const EMPTY = {
  id: "bt:000000000000",
  brand: "vLinker",
  name: "vLinker FS 11436",
  comPort: "COM9",
  available: true,
  paired: true,
  osStatus: "OK",
  guidance: null,
};

function installFake() {
  const q = new URLSearchParams(window.location.search);
  const empty = q.get("empty") === "1";
  const noCom = q.get("nocom") === "1";
  let selected: string | null = null;
  let connected = false;
  let volts: number | null = null;
  let busy = false;
  const devices = empty
    ? []
    : [
        {
          ...EMPTY,
          available: !noCom,
          comPort: noCom ? null : "COM9",
          guidance: noCom ? "已配对，但没有可用的 Bluetooth SPP 串口。" : null,
        },
      ];

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
      if (req.action === "overview") {
        return {
          ok: true,
          taskState: busy ? "running" : "idle",
          voltageVolts: volts,
          ...FLAGS,
        };
      }
      if (req.action === "start") {
        if (busy) return { ok: false, error: "busy", ...FLAGS };
        busy = true;
        setTimeout(() => {
          busy = false;
        }, 800);
        return { ok: true, jobId: "j0123456789abcdef", ...FLAGS };
      }
      if (req.action === "status" || req.action === "cancel" || req.action === "prepare") {
        return { ok: true, state: "completed", plan: { ok: true }, ...FLAGS };
      }
      return { ok: true, ...FLAGS };
    },
    obdConnection: async (req: ObdConnectionRequest): Promise<ObdConnectionResult> => {
      if (req.action === "list" || req.action === "status") {
        return {
          ok: true,
          ...FLAGS,
          devices,
          selectedDeviceId: selected,
          connected,
          linkState: connected ? "connected" : "idle",
          voltageVolts: volts,
        };
      }
      if (req.action === "select") {
        selected = req.deviceId || null;
        connected = false;
        volts = null;
        const hit = devices.find((d) => d.id === selected);
        if (hit && !hit.available) {
          return { ok: false, error: "device_port_unavailable", ...FLAGS, devices, selectedDeviceId: selected, connected, linkState: "idle", voltageVolts: null };
        }
        return { ok: true, ...FLAGS, devices, selectedDeviceId: selected, connected, linkState: "idle", voltageVolts: null };
      }
      if (req.action === "connect") {
        if (busy) return { ok: false, error: "busy", ...FLAGS, devices, selectedDeviceId: selected, connected, voltageVolts: volts, linkState: "diagnostic" };
        selected = req.deviceId || selected;
        connected = true;
        volts = 12.6;
        return { ok: true, ...FLAGS, devices, selectedDeviceId: selected, connected, voltageVolts: 12.6, voltageSource: "atrv", linkState: "connected" };
      }
      if (req.action === "disconnect" || req.action === "clear") {
        connected = false;
        volts = null;
        if (req.action === "clear") selected = null;
        return { ok: true, ...FLAGS, devices, selectedDeviceId: selected, connected, voltageVolts: null, linkState: "idle" };
      }
      return { ok: false, error: "invalid_action", ...FLAGS };
    },
  } as never;
}

installFake();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <main className="content">
      <ObdPage />
    </main>
  </StrictMode>,
);
