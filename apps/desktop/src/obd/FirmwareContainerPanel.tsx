import type { FirmwareContainerInspection } from "../api";

export function FirmwareContainerPanel({ container }: { container: FirmwareContainerInspection }) {
  return <section className="panel" data-testid="firmware-container-inspection">
    <h4>固件容器检查</h4>
    <p data-testid="firmware-container-status">{container.complete ? "容器引用与载荷完整性核对通过。" : container.recognized ? "容器检查存在缺项。" : "此文件格式尚未解析。"}</p>
    <p>{container.counts.documents} 份 ODX · {container.counts.sessions} 个会话 · {container.counts.blocks} 个数据块 · {container.counts.externalPayloads} 个外部载荷 · {container.counts.inlinePayloads} 个内嵌载荷</p>
    <p>这些是文件声明和容器内的字节核对；车辆适用性、刷写步骤及恢复能力仍需核实。</p>
    {container.issues.length > 0 && <p role="alert">有 {container.counts.issues} 项缺失、冲突或未核实格式；完整原因随准备清单保存。</p>}
    {container.documents.map(doc => <details key={doc.member}>
      <summary>{doc.member} · ODX {doc.modelVersion || "版本未知"}</summary>
      <p>文件声明零件号：{doc.partNumbers.join("、") || "未提供"}</p>
      <p>文件声明会话：{doc.sessions.map(s => s.name || "未命名").join("、") || "未提供"}</p>
      <p>以下预览最多 10 个载荷；保存清单保留全部载荷和数据块。</p>
      <ul>{doc.flashdata.slice(0, 10).map((f, i) => <li key={f.id || i} style={{ overflowWrap: "anywhere" }}>
        {f.name || "未命名载荷"}：{f.payload?.resolved ? `${f.payload.bytes} 字节` : "内容未核对"}
        {f.payload?.sha256 && <>；SHA-256：<code>{f.payload.sha256}</code></>}
        {f.encryptCompressMethod && <>；压缩/加密标识原文：<code>{f.encryptCompressMethod.text}</code>（含义未解释）</>}
      </li>)}</ul>
    </details>)}
  </section>;
}
