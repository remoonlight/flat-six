import { useState } from "react";
import { api } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
export function DiagnosticDefinitionsPanel({ connected }: { connected: boolean }) {
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  async function run(action: "export" | "import") {
    if (busy || connected || !api().diagnosticDefinitionBundle) return;
    setBusy(true); setMessage("");
    try {
      const result = await api().diagnosticDefinitionBundle!({ action });
      setMessage(result.canceled ? "已取消。" : result.ok ? `${action === "export" ? "已导出" : "已校验并导入"} ${result.files} 个诊断资料文件。导入后重新进入相关页面。` : diagnosticMessage(result.error));
    } catch (error) { setMessage(String(error)); } finally { setBusy(false); }
  }
  return <section className="panel"><h3>诊断资料迁移</h3>
    <p>可迁移已安装的诊断协议、数据与设码定义、原厂固件匹配清单，以及诊断头能力资料。资料包包含离线派生样本和来源信息，请作为私人文件保管。原始抓包、码值备份、固件本体和设备配对记录分别保存。</p>
    <p>导入会核对每个文件的哈希和路径，已有不同内容不会覆盖。迁移不会把资料中的候选功能变成已通过实车验证的功能。设备需要在新电脑重新安装驱动和配对。</p>
    <button type="button" disabled={busy || connected} onClick={() => void run("export")}>导出诊断资料包</button>{" "}
    <button type="button" disabled={busy || connected} onClick={() => void run("import")}>导入诊断资料包</button>
    <p role="status">{busy ? "正在处理资料包…" : message}</p>
  </section>;
}
