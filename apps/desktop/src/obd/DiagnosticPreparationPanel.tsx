import { useEffect, useRef, useState } from "react";
import { api, hasDesktopApi, callOfflineDiagnostics, type DiagnosticPreparationResult } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { CodingReadScopePanel } from "./CodingReadScopePanel";

type Row = NonNullable<DiagnosticPreparationResult["backups"]>[number];
type Field = { at: number; displayName?: string | { text?: string }; name?: string };
export function DiagnosticPreparationPanel({ ecu, programming = false }: { ecu: string; programming?: boolean }) {
  const [rows, setRows] = useState<Row[]>([]), [id, setId] = useState("");
  const [backup, setBackup] = useState<DiagnosticPreparationResult["backup"]>();
  const [did, setDid] = useState(""), [field, setField] = useState("");
  const [fields, setFields] = useState<Field[]>([]), [search, setSearch] = useState(""), [offset, setOffset] = useState(0), [total, setTotal] = useState(0);
  const [options, setOptions] = useState<NonNullable<DiagnosticPreparationResult["options"]>>([]), [raw, setRaw] = useState("");
  const [result, setResult] = useState<DiagnosticPreparationResult | null>(null), [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  const current = rows.find((row) => row.id === id);
  async function run(action: string, extra = {}) {
    const fn = api().diagnosticPreparation;
    if (!fn || busy || loading) return;
    const token = ++sequence.current;
    setBusy(true); setResult(null); setMessage("");
    try {
      const doc = await fn({ action, ecu, id: id || undefined, did: did || undefined, recordAt: field ? Number(field) : undefined,
        rawValue: raw ? Number(raw) : undefined, ...extra });
      if (token !== sequence.current) return;
      setResult(doc);
      if (!doc.ok) setMessage(diagnosticMessage(doc.error));
      if (doc.backups) setRows(doc.backups);
      if (doc.options) { setOptions(doc.options); setRaw(""); }
      if (doc.saved) setMessage("文件已保存。");
    } catch (error) { if (token === sequence.current) setMessage(String(error)); }
    finally { if (token === sequence.current) setBusy(false); }
  }
  useEffect(() => {
    if (!hasDesktopApi()) return;
    const token = ++sequence.current;
    let active = true;
    setRows([]); setId(""); setBackup(undefined); setResult(null); setMessage(""); setLoading(true);
    void api().diagnosticPreparation?.({ action: "list", ecu }).then((doc) => {
      if (!active || token !== sequence.current) return;
      if (doc.ok) setRows(doc.backups || []); else setMessage(`备份读取失败：${doc.error}`);
    }).catch((error) => { if (active) setMessage(String(error)); }).finally(() => { if (active) setLoading(false); });
    if (!api().diagnosticPreparation) setLoading(false);
    return () => { active = false; ++sequence.current; };
  }, [ecu]);
  useEffect(() => {
    if (!hasDesktopApi()) return;
    let active = true;
    ++sequence.current; setResult(null); setBackup(undefined); setDid(""); setField(""); setOptions([]); setRaw(""); setFields([]); setOffset(0);
    if (!id) return;
    setLoading(true);
    void api().diagnosticPreparation?.({ action: "backup", ecu, id }).then((doc) => {
      if (!active) return;
      if (doc.ok) { setBackup(doc.backup); setDid(doc.backup?.blocks[0]?.did || ""); }
      else setMessage(`备份读取失败：${doc.error}`);
    }).catch((error) => { if (active) setMessage(String(error)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [ecu, id]);
  useEffect(() => {
    let active = true; setFields([]); setField(""); setOptions([]); setRaw(""); setResult(null);
    if (!backup || programming) return;
    const timer = setTimeout(() => {
      void callOfflineDiagnostics({ action: "records", category: "coding", generation: backup.identity.generation as "981" | "982",
        profileId: backup.profileId, search, offset, limit: 40 }).then((doc) => {
        if (!active) return;
        if (doc.ok) { setFields((doc.items || []) as Field[]); setTotal(Number(doc.total || 0)); }
        else setMessage(`该备份的设码定义无法读取：${doc.error}`);
      }).catch((error) => { if (active) setMessage(String(error)); });
    }, 180);
    return () => { active = false; clearTimeout(timer); };
  }, [backup, programming, search, offset]);
  function discard() { ++sequence.current; setResult(null); setOptions([]); setRaw(""); setMessage(""); }
  const locked = busy || loading;
  if (!hasDesktopApi()) return <section className="panel"><p>码值备份、恢复演练与原厂固件准备需要桌面端。</p></section>;
  return <section className="panel" data-testid="diagnostic-preparation">
    <h3>{programming ? "原厂控制单元固件准备" : "当前控制单元码值与恢复"}</h3>
    {!programming && <CodingReadScopePanel ecu={ecu} />}
    <p className="muted">当前控制单元：{ecu}。导入的码值只用于离线分析；首次完整导入作为原码基线，后续备份保留为独立记录。实车修改前仍需重新读取并保存完整当前码值。</p>
    <div className="chip-row">
      <button type="button" disabled={locked} onClick={() => void run("import-backup")}>导入并保存完整码值备份</button>
      <select aria-label="码值备份" value={id} disabled={locked} onChange={(event) => { discard(); setId(event.target.value); }}>
        <option value="">请选择本控制单元备份</option>
        {rows.map((row) => <option key={row.id} value={row.id}>{row.provenance === "simulation" ? "模拟操作前备份 · " : row.original ? "原码基线 · " : "后续备份 · "}{row.identity.generation} · {row.identity.software} · {row.capturedUtc}</option>)}
      </select>
      <button type="button" disabled={locked || !id} onClick={() => void run("export-backup")}>导出完整备份</button>
    </div>
    {current && <p className="muted">硬件 {current.identity.hardware} · 软件 {current.identity.software} · {current.blockCount} 个码值块 · {current.profileId}</p>}
    {!programming && backup && <>
      <div className="chip-row">
        <label>码值块 <select aria-label="码值块" value={did} disabled={locked} onChange={(event) => { discard(); setDid(event.target.value); }}>
          {backup.blocks.map((block) => <option key={block.did} value={block.did}>DID {block.did} · {block.dataHex.length / 2} 字节</option>)}
        </select></label>
        <label>搜索设码字段 <input value={search} disabled={locked} onChange={(event) => { discard(); setSearch(event.target.value); setOffset(0); }} /></label>
        <select aria-label="设码字段" value={field} disabled={locked} onChange={(event) => { discard(); setField(event.target.value); }}>
          <option value="">请选择已定义字段</option>{fields.map((item) => <option key={item.at} value={item.at}>{typeof item.displayName === "string" ? item.displayName : item.displayName?.text || item.name || `字段 ${item.at}`}</option>)}
        </select>
        <button type="button" disabled={locked || offset === 0} onClick={() => { discard(); setOffset(Math.max(0, offset - 40)); }}>上一页</button>
        <button type="button" disabled={locked || offset + 40 >= total} onClick={() => { discard(); setOffset(offset + 40); }}>下一页</button>
      </div>
      <div className="chip-row">
        <button type="button" disabled={locked || !field || !did} onClick={() => void run("coding-options")}>分析当前值与合法选项</button>
        <select aria-label="设码合法值" value={raw} disabled={locked || !options.length} onChange={(event) => { setRaw(event.target.value); setResult(null); }}>
          <option value="">请选择定义中的合法值</option>{options.map((option) => <option key={option.rawValue} value={option.rawValue}>{option.label}（{option.rawValue}）</option>)}
        </select>
        <button type="button" disabled={locked || !raw} onClick={() => void run("coding-preview")}>预览码值修改</button>
        <button type="button" disabled={locked || !raw} onClick={() => void run("simulate-coding", { scenario: "success" })}>模拟设码、备份与回读</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("restore-plan")}>恢复当前控制单元原码：生成方案</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("simulate-restore", { scenario: "success" })}>模拟恢复与回读</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("simulate-restore", { scenario: "disconnect" })}>模拟写入中断</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("simulate-restore", { scenario: "backup-failed" })}>模拟备份失败</button>
      </div>
      {result?.decoded && <p>当前字段值：{result.decoded.text || "定义未提供可显示名称"}（{result.decoded.raw ?? "--"}）</p>}
      {result?.afterHex && <><p>修改前：<code>{result.beforeHex}</code></p><p>修改后：<code>{result.afterHex}</code></p><p>实际改变位：<code>{result.changedBitMaskHex}</code>。其余字节与位保持原值。</p></>}
      {result?.plan && <><p>{result.plan.kind === "current-ecu-coding-preview-plan" ? "模拟设码范围" : "恢复范围"}仅为本控制单元，共 {result.plan.changedBlocks} 个码值块需要改变。</p>{result.plan.blocks.filter((block) => block.changed).map((block) => <p key={block.did}>DID {block.did}：<code>{block.beforeHex}</code> → <code>{block.targetHex}</code></p>)}</>}
      {result?.result && <p data-testid="restore-simulation">模拟结果：{result.result.ok ? "内存中的修改及回读检查通过。" : diagnosticMessage(result.result.error)}这是内存模拟；没有向车辆发送命令。</p>}
      <button type="button" disabled>写入车辆 / 执行实车恢复（通信定义与实车验收未完成）</button>
    </>}
    {programming && <>
      <p>选择原厂匹配清单和固件文件后计算完整 SHA-256，检查文件长度、当前车辆与控制单元版本。蓝牙和连接类型未知均不能刷写。清单自报原厂来源仍需核实。</p>
      <div className="chip-row"><button type="button" disabled={locked} onClick={() => void run("prepare-firmware")}>选择并校验原厂固件</button>
        <button type="button" disabled={locked || !result?.preparation} onClick={() => void run("export-firmware")}>保存固件准备清单</button>
        <button type="button" disabled>刷写车辆固件（刷写及恢复定义未完成）</button></div>
      {result?.preparation && <><p>文件：{result.preparation.file.name} · {result.preparation.file.bytes} 字节 · 哈希匹配：{result.preparation.hashMatches ? "是" : "否"}</p><p>SHA-256：<code>{result.preparation.file.sha256}</code></p><p>目标软件：{result.preparation.targetSoftware}</p><ul>{result.preparation.blockers.map((item) => <li key={item}>{item}</li>)}</ul></>}
    </>}
    {result?.plan && <ul>{result.plan.blockers.map((item) => <li key={item}>{item}</li>)}</ul>}
    {result?.ok && (result.plan || result.afterHex) && <button type="button" disabled={locked} onClick={async () => {
      setBusy(true); setMessage("");
      try { const saved = await api().saveDiagnosticRecording?.({ fileName: "ecu-offline-preparation.json", recording: { ecu, backupId: id, vehicleWritten: false, result } });
        setMessage(saved?.saved ? "离线方案已保存。" : saved?.canceled ? "已取消保存。" : diagnosticMessage(saved?.error));
      } catch (error) { setMessage(diagnosticMessage(error)); } finally { setBusy(false); }
    }}>保存离线修改或恢复方案</button>}
    <p role="status">{busy ? "正在处理…" : message}</p>
  </section>;
}
