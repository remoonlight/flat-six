import { isAdaptedProfile } from "./can-topology-logic.mjs";

// Project evidence as of 2026-10-02. These labels never qualify a vehicle or send a request.
const ORIGINALS = Object.freeze({ dme: 12, gateway: 4, pdk: 1, "bcm-front": 44, "bcm-rear": 26 });

export function topologyCapability(node, adapterModel, adapterDeviceId) {
  const readable = isAdaptedProfile(node?.profileId) && !["PT3G", "CANtieCAR"].includes(adapterModel);
  const engine = node?.profileId === "porsche-981-2014-dme";
  const referenceOnly = node?.sourceGenerations?.length === 1 && node.sourceGenerations[0] === "982";
  const originals = ORIGINALS[node?.id] || 0;
  return {
    readable,
    clearable: readable && ["porsche-981-2014-dme", "porsche-981-2014-gateway"].includes(node?.profileId)
      && !adapterDeviceId?.startsWith("tcp:") && !["VNCI", "PT3G", "CANtieCAR", "ELM327", "OBDKey", "OBDLink"].includes(adapterModel),
    engine,
    referenceOnly,
    diagnostic: readable ? "身份与故障码已接入" : "诊断待适配",
    readEvidence: adapterDeviceId?.startsWith("tcp:") ? "TCP 具名只读路径已接入，网络诊断头与本车待验收；清码、内网、设码和恢复未完成。"
      : adapterModel === "CANtieCAR" ? "CANtieCAR 工作模式、驱动与引脚尚未核实；当前不打开通道。"
      : adapterModel === "PT3G" ? "PT3G 仅支持诊断头连接与供电监测，车辆读取尚未资格化。"
      : ["ELM327", "OBDKey", "OBDLink"].includes(adapterModel) ? "已接入具名只读路径；本型号、车辆读取和持续采集待验收。"
      : readable ? "来源绑定的身份及故障读取软件已接入；当前诊断头和本车待验收。" : "仅参考拓扑，尚未接入本项目诊断；装配情况需按本车核实。",
    coding: originals ? `X431 设码原值已核对 ${originals} 项` : node?.id === "pcm" ? "PCM 设码原值待补" : "设码原值与版本资格待核实",
    codingDetail: node?.id === "bcm-rear" ? "另有 2 项重名字段待唯一定位；独立写入、回读和恢复待验证。"
      : node?.id === "pdk" || node?.id === "bcm-front" ? "完整 ECU 版本资格待补；独立写入、回读和恢复待验证。"
      : "可查看离线方案与记录；独立设码写入尚未完成验证。",
  };
}

export function topologyAdapterProgress(model) {
  return ({
    vLinker: "本车只读已有 CLI 实证；桌面与持续采集待验收。",
    "OBDLink MX+": "已有广播记录；出现 CAN ERROR 和打开失败，诊断口读取待验证。",
    VNCI: "已接入具名只读路径，本车读取待验收；此设备暂不支持清码。",
    CANtieCAR: "工作模式(未完成)：ELM、J2534 与透明串口需分别核实，当前不打开通道。",
    PT3G: "诊断头连接与供电监测已接入，车辆通信尚未资格化。",
    ELM327: "具名只读路径已接入；本型号及车辆待验收，清码和内网未完成。",
    OBDKey: "具名只读路径已接入；须通过所需 AT 设置，本型号及车辆待验收，清码和内网未完成。",
    OBDLink: "具名只读路径已接入；不等同 MX+，本型号及车辆待验收，清码和内网未完成。",
  })[model] || "在连接设置选择设备；历史设备档案不代表当前在线。";
}
