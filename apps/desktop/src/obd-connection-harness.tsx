import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ObdPage } from "./pages/ObdPage";
import type { ObdConnectionRequest, ObdConnectionResult, ObdRegisteredDevice, ReadOnlySessionRequest, ReadOnlySessionResult } from "./api";
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
  const mx = q.get("mx") === "1";
  const vnci = q.get("vnci") === "1";
  const unpowered = q.get("unpowered") === "1";
  const unresolved = q.get("unresolved") === "1";
  const registryMode = q.get("registry") === "1";
  let selected: string | null = null;
  let model: ObdConnectionRequest["model"] | null = null;
  let purpose: "diagnostic" | "internal" = "diagnostic";
  let canNetwork: "drive" | "chassis" | "comfort" | "crash" | "adas" | null = null;
  let connected = false;
  let volts: number | null = null;
  let busy = false;
  const devices = empty
    ? []
    : [
        {
          ...EMPTY,
          id: vnci ? "vnci:10001" : EMPTY.id,
          brand: vnci ? "VNCI" : unresolved ? "unresolved" : mx ? "OBDLink MX+" : EMPTY.brand,
          name: vnci ? "VAS6154A · USB" : unresolved ? "OBDLink" : mx ? "OBDLink MX+" : EMPTY.name,
          transport: vnci ? "d-pdu-usb" as const : undefined,
          serial: vnci ? "10001" : undefined,
          available: !noCom,
          comPort: noCom || vnci ? null : "COM9",
          guidance: noCom ? "已配对，但没有可用的 Bluetooth SPP 串口。" : null,
        },
      ];
  const deviceRegistry: ObdRegisteredDevice[] = registryMode ? [
    { id: EMPTY.id, family: "vLinker", name: "vLinker FS BT", state: "已配对 · 串口可用", present: true, connectable: true, features: ["连接与电压", "具名只读诊断"], routes: ["session", "live"] },
    { id: "bt:AABBCCDDEEFF", family: "OBDLink MX+", name: "OBDLink MX+", state: "本次未发现", present: false, connectable: false, features: ["广播记录与回放"], routes: ["broadcast"] },
    { id: "vnci:10001", family: "VNCI", name: "VNCI VAS6154A", state: "本次未发现", present: false, connectable: false, features: ["具名只读诊断"], routes: ["session"] },
    { id: "pt3g:20002", family: "PT3G", name: "PT3G / E70", serial: "20002", state: "USB 在线", present: true, connectable: false, serviceState: "Running", features: ["驱动与 USB 状态"], routes: [] },
    { id: "x431:test", family: "X431", name: "Launch X431 Pro3S 诊断头", state: "本次未发现", present: false, connectable: false, features: ["设码原值记录"], routes: ["offline", "coding"] },
    { id: "tablet:test", family: "X431-tablet", name: "Lenovo Android 平板", state: "本次未发现", present: false, connectable: false, features: ["诊断参考数据"], routes: ["offline"] },
    { id: "usb:test", family: "Espressif", name: "Espressif USB 调试设备", state: "本次未发现", present: false, connectable: false, features: ["USB 识别"], routes: [] },
  ].map((d) => ({ connection: "测试接口", comPort: null, driverInstalled: true, driverVersion: null, serviceState: null,
    note: d.family === "PT3G" ? "项目车辆传输尚未接入" : "测试设备档案", lastSeenAt: "2026-10-01T00:00:00Z", checkedAt: "2026-10-01T01:00:00Z", ...d })) as ObdRegisteredDevice[] : [];

  window.porsche981 = {
    obdOpenBluetooth: async () => "opened",
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
    listCoding: async () => [],
    codingMenu: async () => ({ systems: [] }),
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
          deviceRegistry,
          purpose, canNetwork,
          selectedDeviceId: selected,
          model,
          connected,
          linkState: connected ? "connected" : "idle",
          voltageVolts: volts,
        };
      }
      if (req.action === "configure") {
        purpose = req.purpose || purpose;
        canNetwork = req.canNetwork || canNetwork;
        return { ok: true, ...FLAGS, purpose, canNetwork, connected, linkState: "idle" };
      }
      if (req.action === "select") {
        selected = req.deviceId || null;
        connected = false;
        volts = null;
        const hit = devices.find((d) => d.id === selected);
        model = req.model || (hit?.brand === "VNCI" ? "VNCI" : hit?.brand === "OBDLink MX+" ? "OBDLink MX+" : hit?.brand === "vLinker" ? "vLinker" : model);
        if (hit && !hit.available) {
          return { ok: false, error: "device_port_unavailable", ...FLAGS, devices, selectedDeviceId: selected, model, connected, linkState: "idle", voltageVolts: null };
        }
        return { ok: true, ...FLAGS, devices, selectedDeviceId: selected, model, connected, linkState: "idle", voltageVolts: null };
      }
      if (req.action === "connect") {
        if (purpose === "internal") return { ok: false, error: "internal_receive_not_integrated", ...FLAGS, connected: false, linkState: "idle" };
        if (busy) return { ok: false, error: "busy", ...FLAGS, devices, selectedDeviceId: selected, connected, voltageVolts: volts, linkState: "diagnostic" };
        selected = req.deviceId || selected;
        model = req.model || model;
        if (!model) return { ok: false, error: "device_model_required", ...FLAGS };
        if (vnci && unpowered) return { ok: false, error: "vnci-obd-unpowered-or-invalid-voltage", ...FLAGS, devices, selectedDeviceId: selected, model, connected: false, voltageVolts: null, linkState: "idle" };
        connected = true;
        volts = 12.6;
        return { ok: true, ...FLAGS, devices, selectedDeviceId: selected, model, connected, voltageVolts: 12.6, voltageSource: vnci ? "d-pdu-vbatt" : "atrv", linkState: "connected" };
      }
      if (req.action === "disconnect" || req.action === "clear") {
        connected = false;
        volts = null;
        if (req.action === "clear") { selected = null; model = null; }
        return { ok: true, ...FLAGS, devices, selectedDeviceId: selected, model, connected, voltageVolts: null, linkState: "idle" };
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
