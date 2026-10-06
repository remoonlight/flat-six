import { isAdaptedProfile } from "./can-topology-logic.mjs";

// Project evidence as of 2026-10-02. These labels never qualify a vehicle or send a request.
const ORIGINALS = Object.freeze({ dme: 12, gateway: 4, pdk: 1, "bcm-front": 44, "bcm-rear": 26 });

export function topologyCapability(node, adapterModel) {
  const readable = isAdaptedProfile(node?.profileId) && adapterModel !== "PT3G";
  const engine = node?.profileId === "porsche-981-2014-dme";
  const referenceOnly = node?.sourceGenerations?.length === 1 && node.sourceGenerations[0] === "982";
  const originals = ORIGINALS[node?.id] || 0;
  return {
    readable,
    clearable: readable && adapterModel !== "VNCI" && adapterModel !== "PT3G",
    engine,
    referenceOnly,
    diagnostic: readable ? "身份与故障码已接入" : "诊断待适配",
    readEvidence: adapterModel === "PT3G" ? "PT3G 仅支持诊断头连接与供电监测，车辆读取尚未资格化。"
      : readable ? "vLinker CLI 本车已验证；桌面读取待实车验收。" : "仅参考拓扑，尚未接入本项目诊断；装配情况需按本车核实。",
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
    PT3G: "诊断头连接与供电监测已接入，车辆通信尚未资格化。",
  })[model] || "在连接设置选择设备；历史设备档案不代表当前在线。";
}
