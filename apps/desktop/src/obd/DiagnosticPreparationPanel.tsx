import { useEffect, useRef, useState } from "react";
import { api, hasDesktopApi, callOfflineDiagnostics, type DiagnosticPreparationResult, type CodingBlockIdentifier } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";
import { CodingReadScopePanel } from "./CodingReadScopePanel";
import { CodingOperationHistoryPanel } from "./CodingOperationHistoryPanel";
import { FirmwareContainerPanel } from "./FirmwareContainerPanel";
import { obdFunctionLabel } from "./function-label";

type Row = NonNullable<DiagnosticPreparationResult["backups"]>[number];
type Field = { at: number; displayName?: string | { text?: string }; name?: string };
const codingBlockKey = (block: CodingBlockIdentifier) => block.did ? `DID:${block.did}` : `${block.identifierKind}:${block.identifierHex}`;
const displayValue = (value: { text?: string | null; raw?: number | null; value?: number | string | null; phys?: string | null; display?: string | null; unit?: string | null }) => `${value.text || value.display || value.phys || String(value.value ?? value.raw ?? "--")}${(value.display || value.phys) && value.unit ? ` ${value.unit}` : ""}`;
const fractionText = (value: { numerator: string; denominator: string }) => value.denominator === "1" ? value.numerator : `${value.numerator}/${value.denominator}`;
const readRequest = (key: string) => (key.startsWith("LID:") ? "21" : "22") + key.split(":")[1];
export function DiagnosticPreparationPanel({ ecu, programming = false }: { ecu: string; programming?: boolean }) {
  const [rows, setRows] = useState<Row[]>([]), [id, setId] = useState("");
  const [backup, setBackup] = useState<DiagnosticPreparationResult["backup"]>();
  const [did, setDid] = useState(""), [field, setField] = useState("");
  const [fields, setFields] = useState<Field[]>([]), [search, setSearch] = useState(""), [offset, setOffset] = useState(0), [total, setTotal] = useState(0);
  const [options, setOptions] = useState<NonNullable<DiagnosticPreparationResult["options"]>>([]), [raw, setRaw] = useState("");
  const [numericDomain, setNumericDomain] = useState<DiagnosticPreparationResult["numericDomain"]>();
  const [physical, setPhysical] = useState("");
  const [analyzedValue, setAnalyzedValue] = useState<DiagnosticPreparationResult["decoded"]>();
  const [result, setResult] = useState<DiagnosticPreparationResult | null>(null), [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false);
  const sequence = useRef(0);
  const current = rows.find((row) => row.id === id);
  async function run(action: string, extra = {}) {
    const fn = api().diagnosticPreparation;
    if (!fn || busy || loading) return;
    const token = ++sequence.current;
    setBusy(true); setResult(null); setMessage("");
    if (action === "coding-options") { setOptions([]); setNumericDomain(undefined); setPhysical(""); setAnalyzedValue(undefined); }
    try {
      const doc = await fn({ action, ecu, id: id || undefined, blockKey: did || undefined, recordAt: field ? Number(field) : undefined,
        ...(["coding-preview", "simulate-coding"].includes(action) ? numericDomain ? { physicalValue: physical } : { rawValue: Number(raw) } : {}), ...extra });
      if (token !== sequence.current) return;
      setResult(doc);
      if (!doc.ok) setMessage(diagnosticMessage(doc.error));
      if (doc.backups) setRows(doc.backups);
      if (action === "coding-options" && doc.ok) { setOptions(doc.options || []); setRaw(""); setNumericDomain(doc.numericDomain); setAnalyzedValue(doc.decoded); }
      if (doc.saved) setMessage("文件已保存。");
    } catch (error) { if (token === sequence.current) setMessage(String(error)); }
    finally { if (token === sequence.current) setBusy(false); }
  }
  useEffect(() => {
    if (!hasDesktopApi()) return;
    ++sequence.current;
    let active = true;
    setRows([]); setId(""); setBackup(undefined); setResult(null); setMessage(""); setLoading(true);
    void api().diagnosticPreparation?.({ action: "list", ecu }).then((doc) => {
      if (!active) return;
      if (doc.ok) setRows(doc.backups || []); else setMessage(`备份读取失败：${doc.error}`);
    }).catch((error) => { if (active) setMessage(String(error)); }).finally(() => { if (active) setLoading(false); });
    if (!api().diagnosticPreparation) setLoading(false);
    return () => { active = false; ++sequence.current; };
  }, [ecu]);
  useEffect(() => {
    if (!hasDesktopApi()) return;
    let active = true;
    ++sequence.current; setResult(null); setBackup(undefined); setDid(""); setField(""); setOptions([]); setRaw(""); setNumericDomain(undefined); setPhysical(""); setAnalyzedValue(undefined); setFields([]); setOffset(0);
    if (!id) return;
    setLoading(true);
    void api().diagnosticPreparation?.({ action: "backup", ecu, id }).then((doc) => {
      if (!active) return;
      if (doc.ok) { setBackup(doc.backup); setDid(doc.backup?.blocks[0] ? codingBlockKey(doc.backup.blocks[0]) : ""); }
      else setMessage(`备份读取失败：${doc.error}`);
    }).catch((error) => { if (active) setMessage(String(error)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [ecu, id]);
  useEffect(() => {
    let active = true; setFields([]); setField(""); setOptions([]); setRaw(""); setNumericDomain(undefined); setPhysical(""); setAnalyzedValue(undefined); setResult(null);
    if (!backup || programming || !did) return;
    const timer = setTimeout(() => {
      void callOfflineDiagnostics({ action: "records", category: "coding", generation: backup.identity.generation as "981" | "982",
        profileId: backup.profileId, expectedReadRequestHex: readRequest(did), search, offset, limit: 40 }).then((doc) => {
        if (!active) return;
        if (doc.ok) { setFields((doc.items || []) as Field[]); setTotal(Number(doc.total || 0)); }
        else setMessage(`该备份的设码定义无法读取：${doc.error}`);
      }).catch((error) => { if (active) setMessage(String(error)); });
    }, 180);
    return () => { active = false; clearTimeout(timer); };
  }, [backup, programming, did, search, offset]);
  function discard() { ++sequence.current; setResult(null); setOptions([]); setRaw(""); setNumericDomain(undefined); setPhysical(""); setAnalyzedValue(undefined); setMessage(""); }
  const locked = busy || loading;
  if (!hasDesktopApi()) return <section className="panel"><p>码值备份、恢复演练与原厂固件准备需要桌面端。</p></section>;
  return <section className="panel" data-testid="diagnostic-preparation">
    <h3>{programming ? "原厂控制单元固件准备" : "当前控制单元码值与恢复"}</h3>
    {!programming && <><CodingReadScopePanel ecu={ecu} /><div className="chip-row">
      <button type="button" disabled>{obdFunctionLabel("读取车辆完整原码", false)}</button>
      <button type="button" disabled>{obdFunctionLabel("写入车辆", false)}</button>
      <button type="button" disabled>{obdFunctionLabel("恢复原码", false)}</button>
    </div><p className="muted">车辆完整原码读取、设码写入及恢复的通信定义与流程尚未齐备；离线备份和演练不开放车辆执行。</p></>}
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
        <label>码值块 <select aria-label="码值块" value={did} disabled={locked} onChange={(event) => { discard(); setDid(event.target.value); setField(""); setOffset(0); }}>
          {backup.blocks.map((block) => <option key={codingBlockKey(block)} value={codingBlockKey(block)}>{codingBlockKey(block).replace(":", " ")} · {block.dataHex.length / 2} 字节</option>)}
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
        {numericDomain ? <label>目标数值{numericDomain.unit ? `（${numericDomain.unit}）` : ""}
          <input aria-label="设码目标数值" type="text" inputMode="decimal" value={physical} maxLength={128} disabled={locked}
            onChange={(event) => { ++sequence.current; setPhysical(event.target.value); setResult(null); setMessage(""); }} />
        </label> : <select aria-label="设码合法值" value={raw} disabled={locked || !options.length} onChange={(event) => { ++sequence.current; setRaw(event.target.value); setResult(null); setMessage(""); }}>
          <option value="">请选择定义中的合法值</option>{options.map((option) => <option key={option.rawValue} value={option.rawValue}>{option.label}（{option.rawValue}）</option>)}
        </select>}
        <button type="button" disabled={locked || !(numericDomain ? physical.trim() : raw)} onClick={() => void run("coding-preview")}>预览码值修改</button>
        <button type="button" disabled={locked || !(numericDomain ? physical.trim() : raw)} onClick={() => void run("simulate-coding", { scenario: "success" })}>模拟设码、备份与回读</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("restore-plan")}>恢复当前控制单元原码：生成方案</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("simulate-restore", { scenario: "success" })}>模拟恢复与回读</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("simulate-restore", { scenario: "disconnect" })}>模拟写入中断</button>
        <button type="button" disabled={locked || !id} onClick={() => void run("simulate-restore", { scenario: "backup-failed" })}>模拟备份失败</button>
      </div>
      {numericDomain && <p data-testid="coding-numeric-domain">来源数值范围 {numericDomain.physicalEnvelope.map(fractionText).join(" ～ ")}{numericDomain.unit ? ` ${numericDomain.unit}` : ""}，步进 {fractionText(numericDomain.physicalStepMagnitude)}。可输入十进制或分数；保留值与不可精确表示的值不可选。此处仅作离线预览与内存演练，原生编辑规则及实车资格仍待验收。</p>}
      {analyzedValue && <p data-testid="coding-current-value">当前字段值：{displayValue(analyzedValue)}（原始值 {analyzedValue.raw ?? "--"}）</p>}
      {result?.targetDecoded && <p data-testid="coding-target-value">目标字段值：{displayValue(result.targetDecoded)}</p>}
      {result?.effects && result.effects.length > 0 && <section data-testid="coding-field-effects" aria-label="字段变化">
        <h4>字段变化</h4>
        <ul>{result.effects.map((effect, index) => <li key={`${effect.sourceOffset}:${index}`}>
          <strong>{effect.name}</strong>（{effect.selected ? "所选字段" : "同块相关字段"}）：
          {displayValue(effect.before)} → {displayValue(effect.after)}
        </li>)}</ul>
      </section>}
      {result?.effects?.length === 0 && result.afterHex && <p data-testid="coding-no-changes">所选值与当前码值一致，没有字段变化。</p>}
      {result?.afterHex && <><p>修改前：<code>{result.beforeHex}</code></p><p>修改后：<code>{result.afterHex}</code></p><p>实际改变位：<code>{result.changedBitMaskHex}</code>。其余字节与位保持原值。</p></>}
      <details><summary>查看备份中的完整原始码值</summary>{backup.blocks.map((block) => <p key={codingBlockKey(block)} style={{ overflowWrap: "anywhere" }}>{codingBlockKey(block).replace(":", " ")}：<code>{block.dataHex}</code></p>)}</details>
      {result?.plan && <><p>{result.plan.kind === "current-ecu-coding-preview-plan" ? "模拟设码范围" : "恢复范围"}仅为本控制单元，共 {result.plan.changedBlocks} 个码值块需要改变。</p>{result.plan.blocks.filter((block) => block.changed).map((block) => <p key={codingBlockKey(block)}>{codingBlockKey(block).replace(":", " ")}：<code>{block.beforeHex}</code> → <code>{block.targetHex}</code></p>)}</>}
      {result?.result && <p data-testid="restore-simulation">模拟结果：{result.result.ok ? "内存中的修改及回读检查通过。" : diagnosticMessage(result.result.error)}这是内存模拟；没有向车辆发送命令。</p>}
    </>}
    {programming && <>
      <p>选择原厂匹配清单和固件文件后计算完整 SHA-256，检查文件长度、当前车辆与控制单元版本。PDX/ODX 另核对会话、数据块和载荷完整性，并随准备清单完整保存。蓝牙和连接类型未知均不能刷写。清单自报原厂来源仍需核实。</p>
      <div className="chip-row"><button type="button" disabled={locked} onClick={() => void run("prepare-firmware")}>选择并校验原厂固件</button>
        <button type="button" disabled={locked || !result?.preparation} onClick={() => void run("export-firmware")}>保存固件准备清单</button>
        <button type="button" disabled>{obdFunctionLabel("刷写车辆固件", false)}</button></div>
      {result?.preparation && <><p>文件：{result.preparation.file.name} · {result.preparation.file.bytes} 字节 · 哈希匹配：{result.preparation.hashMatches ? "是" : "否"}</p><p>SHA-256：<code>{result.preparation.file.sha256}</code></p><p>目标软件：{result.preparation.targetSoftware}</p><ul>{result.preparation.blockers.map((item) => <li key={item}>{item}</li>)}</ul></>}
      {result?.preparation?.transportRequirement && <p data-testid="firmware-connection-requirement">目标诊断头：VNCI / PT3G · 当前：{result.preparation.transportRequirement.selectedFamily || "未选择目标诊断头"} · 本次有线连接：未核实</p>}
      {result?.preparation?.container && <FirmwareContainerPanel container={result.preparation.container} />}
    </>}
    {!programming && <CodingOperationHistoryPanel key={ecu} ecu={ecu} revision={result?.result?.operationId} />}
    {result?.result?.operationId && <p data-testid="coding-operation-state">演练操作编号：{result.result.operationId}；状态：{({
      "completed": "完整回读已核对并保存", "failed-before-write": "写入前失败", "outcome-unknown": "结果未知，需核对",
      "backed-up": "备份已保存", "write-intent": "写入意图已保存，结果未确认", "write-returned": "调用已返回，回读未确认", "block-verified": "部分块已回读，完整范围未确认",
    } as Record<string, string>)[result.result.operationState || ""] || "操作记录未确认"}。记录在重启后保留，不自动重放。</p>}
    {result?.result?.journalError && <p role="alert">操作记录保存或核对失败：{diagnosticMessage(result.result.journalError)}；不能据此确认操作完成。</p>}
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
