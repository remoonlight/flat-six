import fs from "node:fs";

// Capabilities are product routes, not claims of completed vehicle qualification.
const PROFILES = Object.freeze({
  vLinker: { name: "vLinker FS BT", connection: "蓝牙 SPP", features: ["连接与电压", "具名只读诊断", "发动机参数"], routes: ["session", "live"], note: "按蓝牙身份匹配当前串口；设码逐功能验证。" },
  "OBDLink MX+": { name: "OBDLink MX+", connection: "蓝牙 SPP / Drive CAN", features: ["连接与电压", "广播记录与回放"], routes: ["broadcast"], note: "当前接 Drive CAN；广播质量错误保留在记录中，诊断读取需核验接口与 ECU 身份。" },
  VNCI: { name: "VNCI VAS6154A", connection: "USB / D-PDU", features: ["连接与电压", "具名只读诊断"], routes: ["session"], note: "实车 ECU 读取待验证；发动机标准参数、清码与独立设码尚未开放。" },
  PT3G: { name: "PT3G / E70", connection: "USB 网卡 / E70 服务", features: ["驱动与 USB 状态", "历史模块身份"], routes: [], note: "已关联已安装的 E70 驱动；项目车辆传输尚未接入，不能启动诊断或设码。" },
  X431: { name: "Launch X431 Pro3S 诊断头", connection: "诊断头 ↔ 平板蓝牙", features: ["数据与日志回放", "设码原值记录"], routes: ["offline", "coding"], note: "经平板留存的诊断参考；项目不直接连接该蓝牙头，也不重放设码指令。" },
  "X431-tablet": { name: "X431 Android 平板", connection: "USB / ADB", features: ["诊断参考数据", "设码记录"], routes: ["offline", "coding"], note: "USB 存在与 ADB 授权分别核验；进入记录页面不会启动 ADB 或诊断。" },
  Espressif: { name: "Espressif USB 调试设备", connection: "USB 调试 / 串口", features: ["USB 识别"], routes: [], note: "完整板型、CAN 收发器与采集固件未确认；不作为可连接 OBD 诊断头。" },
});
const CONNECTABLE = new Set(["vLinker", "OBDLink MX+", "VNCI"]);

function identity(row) {
  if (!row || typeof row.id !== "string" || row.id.length > 240 || !PROFILES[row.family]) return null;
  const out = { id: row.id, family: row.family };
  for (const key of ["name", "serial", "mac", "usbInstanceId", "firmware", "lastSeenAt"])
    if (typeof row[key] === "string" && row[key].length < 256) out[key] = row[key];
  return out;
}

export function readKnownDevices(file) {
  try {
    const doc = JSON.parse(fs.readFileSync(file, "utf8"));
    return doc.version === 1 && Array.isArray(doc.devices) ? doc.devices.map(identity).filter(Boolean).slice(0, 64) : [];
  } catch { return []; }
}

export function buildDeviceRegistry({ devices = [], host = {}, known = [], checkedAt = new Date().toISOString() }) {
  const records = new Map(known.map(identity).filter(Boolean).map((d) => [d.id, d]));
  for (const d of devices) {
    if (!CONNECTABLE.has(d.brand)) continue;
    const fresh = identity({ ...d, family: d.brand, lastSeenAt: checkedAt });
    if (fresh) records.set(d.id, { ...records.get(d.id), ...fresh });
  }
  // Generic Realtek NICs never imply PT3G: use only an already confirmed exact USB identity.
  for (const usb of host.usb || []) {
    const instance = usb.InstanceId || "";
    if (/^USB\\VID_303A&PID_1001\\/i.test(instance)) {
      const id = "usb:" + instance;
      if (![...records.values()].some((r) => r.usbInstanceId?.toUpperCase() === instance.toUpperCase()))
        records.set(id, { id, family: "Espressif", name: usb.FriendlyName || PROFILES.Espressif.name, usbInstanceId: instance });
    }
  }
  for (const family of ["VNCI", "PT3G"]) {
    if (host.drivers?.[family]?.installed && ![...records.values()].some((r) => r.family === family))
      records.set("driver:" + family, { id: "driver:" + family, family });
  }
  const order = Object.keys(PROFILES);
  const registry = [...records.values()].sort((a, b) => order.indexOf(a.family) - order.indexOf(b.family) || a.id.localeCompare(b.id)).map((record) => {
    const profile = PROFILES[record.family];
    const current = devices.find((d) => d.id === record.id);
    const usb = record.usbInstanceId && (host.usb || []).find((d) => d.InstanceId?.toUpperCase() === record.usbInstanceId.toUpperCase());
    const usbPresent = Boolean(usb && usb.Status === "OK");
    const paired = Boolean(current?.paired);
    const driver = host.drivers?.[record.family];
    const service = record.family === "PT3G" ? (host.services || []).find((s) => s.Name === "VciToolServerPORSCHE")?.State : null;
    const state = current?.available ? (paired ? "已配对 · 串口可用" : "模块可用")
      : current ? "已识别 · 暂不可连接" : usbPresent ? "USB 在线" : "本次未发现";
    if (current || usbPresent) record.lastSeenAt = checkedAt;
    return { ...record, name: record.name || profile.name, connection: profile.connection,
      state, present: Boolean(current || usbPresent), connectable: Boolean(current?.available && CONNECTABLE.has(record.family)),
      comPort: current?.comPort || null, driverInstalled: driver?.installed ?? (paired ? true : null),
      driverVersion: driver?.installed ? driver.version : null, serviceState: service || null,
      features: profile.features, routes: profile.routes, note: profile.note,
      lastSeenAt: record.lastSeenAt || null, checkedAt };
  });
  return { registry, known: [...records.values()].map(identity).filter(Boolean) };
}
