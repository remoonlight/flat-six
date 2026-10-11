import fs from "node:fs";

// Capabilities are product routes, not claims of completed vehicle qualification.
const PROFILES = Object.freeze({
  vLinker: { name: "vLinker FS BT", connection: "蓝牙 SPP", features: ["连接与电压", "具名只读诊断", "发动机参数"], routes: ["session", "live"], note: "按蓝牙身份匹配当前串口；设码逐功能验证。" },
  "OBDLink MX+": { name: "OBDLink MX+", connection: "蓝牙 SPP / Drive CAN", features: ["连接与电压", "广播记录与回放"], routes: ["broadcast"], note: "当前接 Drive CAN；广播质量错误保留在记录中，诊断读取需核验接口与 ECU 身份。" },
  VNCI: { name: "VNCI VAS6154A", connection: "USB / D-PDU", features: ["连接与电压", "具名只读诊断", "发动机参数"], routes: ["session", "live"], note: "具名981 DME身份核对后可切换标准发动机读取；实车待验证，清码与独立设码尚未开放。" },
  PT3G: { name: "PT3G / E70", connection: "USB / E70 D-PDU", features: ["连接与电压", "当前模块身份"], routes: [], note: "仅诊断头通信与供电监测；车辆读取、设码与内网监听尚未资格化。" },
  ELM327: { name: "ELM327", connection: "USB / 蓝牙 SPP", features: ["连接与电压", "具名只读诊断", "发动机参数"], routes: ["session", "live"], note: "串口默认 38400；具名只读路径已接入，实物及本车待验收。清码、内网、设码和恢复未完成。" },
  OBDKey: { name: "OBDKey", connection: "USB / 蓝牙 SPP", features: ["连接与电压", "具名只读诊断", "发动机参数"], routes: ["session", "live"], note: "按参考配置使用 9600；必须通过 OBDKey 身份及全部所需 AT 设置。实物、车辆、清码、内网、设码和恢复未完成。" },
  OBDLink: { name: "OBDLink / STN", connection: "USB / 蓝牙 SPP", features: ["连接与电压", "具名只读诊断", "发动机参数"], routes: ["session", "live"], note: "串口默认 115200；不视为 MX+，不使用其专用内网配置。实物、车辆、清码、内网、设码和恢复未完成。" },
  X431: { name: "Launch X431 Pro3S 诊断头", connection: "诊断头 ↔ 平板蓝牙", features: ["数据与日志回放", "设码原值记录"], routes: ["offline", "coding"], note: "经平板留存的诊断参考；项目不直接连接该蓝牙头，也不重放设码指令。" },
  "X431-tablet": { name: "X431 Android 平板", connection: "USB / ADB", features: ["诊断参考数据", "设码记录"], routes: ["offline", "coding"], note: "USB 存在与 ADB 授权分别核验；进入记录页面不会启动 ADB 或诊断。" },
  Espressif: { name: "Espressif USB 调试设备", connection: "USB 调试 / 串口", features: ["USB 识别"], routes: [], note: "完整板型、CAN 收发器与采集固件未确认；不作为可连接 OBD 诊断头。" },
});
const CONNECTABLE = new Set(["vLinker", "OBDLink MX+", "ELM327", "OBDKey", "OBDLink", "VNCI", "PT3G"]);

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
    const supportFailed = driver?.supportFilesQualified === false;
    const service = record.family === "PT3G" ? (host.services || []).find((s) => s.Name === "VciToolServerPORSCHE")?.State : null;
    const state = current?.available && supportFailed ? "驱动配套未通过" : current?.available ? (paired ? "已配对 · 串口可用" : "模块可用")
      : current ? "已识别 · 暂不可连接" : usbPresent ? "USB 在线" : "本次未发现";
    if (current || usbPresent) record.lastSeenAt = checkedAt;
    return { ...record, name: record.name || profile.name, connection: record.id.startsWith("usb:") || current?.transport === "usb-serial" ? "USB / 串口" : profile.connection,
      state, present: Boolean(current || usbPresent), connectable: Boolean(current?.available && CONNECTABLE.has(record.family) && !supportFailed),
      comPort: current?.comPort || null, driverInstalled: driver?.installed ?? (paired ? true : null),
      driverVersion: driver?.installed && !supportFailed ? driver.version : null, serviceState: service || null,
      features: profile.features, routes: profile.routes,
      note: supportFailed ? `${profile.note} 当前驱动配套未通过核对，连接前须补齐匹配资料。` : profile.note,
      lastSeenAt: record.lastSeenAt || null, checkedAt };
  });
  return { registry, known: [...records.values()].map(identity).filter(Boolean) };
}
