import { useEffect, useRef, useState } from "react";
import { api, type CodingCaptureResult, type CodingCaptureRequest, type CodingSourceCaptureRequest, type DtcCaptureRequest, type IdentityCaptureRequest } from "../api";
import { diagnosticMessage } from "./diagnostic-messages";

export function CodingCapturePanel({ generation, profileId, ecuId, sourceCatalogId }: { generation: "981" | "982"; profileId: string; ecuId: number; sourceCatalogId: string }) {
  return <ApplicationCapturePanel generation={generation} profileId={profileId} kind="coding" codingContext={{ sourceCatalogId, ecuId }} />;
}

export function ApplicationCapturePanel({ generation, profileId, kind, dtcContext, identityContext, codingContext }: { generation: "981" | "982"; profileId: string; kind: "coding" | "measurement" | "dtc" | "identity"; dtcContext?: { sourceCatalogId: string; ecuId: number }; identityContext?: { sourceCatalogId: string; ecuId: number }; codingContext?: { sourceCatalogId: string; ecuId: number } }) {
  const measurement = kind === "measurement", dtc = kind === "dtc", identity = kind === "identity", coding = kind === "coding", explicitTarget = dtc || identity || !!codingContext;
  const invoke = (identity ? api().identityCapture : dtc ? api().dtcCapture : measurement ? api().measurementCapture : api().codingCapture) as ((request: CodingCaptureRequest | CodingSourceCaptureRequest | DtcCaptureRequest | IdentityCaptureRequest) => Promise<CodingCaptureResult>) | undefined;
  const client = useRef(crypto.randomUUID()), mounted = useRef(true);
  const [capture, setCapture] = useState<CodingCaptureResult>(), [analysis, setAnalysis] = useState<CodingCaptureResult>();
  const [runId, setRunId] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [rows, setRows] = useState<NonNullable<CodingCaptureResult["rows"]>>([]), [cursor, setCursor] = useState<number | null>(null);
  const [saved, setSaved] = useState("");
  const [captureTarget, setCaptureTarget] = useState(""), [captureTargets, setCaptureTargets] = useState<NonNullable<CodingCaptureResult["captureTargets"]>>([]);
  const [view, setView] = useState<"values" | "responses" | "faults" | "blocks">(dtc ? "faults" : measurement || identity ? "values" : "responses");
  const [links, setLinks] = useState<NonNullable<CodingCaptureResult["links"]>>([]), [linkIndex, setLinkIndex] = useState("");
  useEffect(() => {
    mounted.current = true;
    if (dtc && dtcContext) void operation(async () => {
      const result = accept(await invoke?.({ action: "scope", clientId: client.current, generation, profileId, ...dtcContext }));
      if (mounted.current) { setLinks(result.links || []); setCaptureTargets(result.captureTargets || []); }
    });
    if (identity && identityContext) void operation(async () => {
      const result = accept(await invoke?.({ action: "scope", clientId: client.current, generation, ...identityContext }));
      if (mounted.current) setCaptureTargets(result.captureTargets || []);
    });
    if (codingContext) void operation(async () => {
      const result = accept(await invoke?.({ action: "scope", clientId: client.current, generation, profileId, ...codingContext }));
      if (mounted.current) setCaptureTargets(result.captureTargets || []);
    });
    return () => { mounted.current = false; void invoke?.({ action: "discard", clientId: client.current }).catch(() => {}); };
  }, []);
  async function operation(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setSaved("");
    try { await work(); } catch (e) { if (mounted.current) setError(diagnosticMessage(e instanceof Error ? e.message : e)); }
    finally { if (mounted.current) setBusy(false); }
  }
  function accept(result: CodingCaptureResult | undefined) {
    if (!result?.ok) throw new Error(result?.error || "capture-unavailable");
    return result;
  }
  async function page(analysisId: string, offset: number, append: boolean, stream = view) {
    const result = accept(await invoke?.({ action: "page", clientId: client.current, analysisId, cursor: offset, stream }));
    if (mounted.current) { setRows(previous => append ? [...previous, ...(result.rows || [])] : result.rows || []); setCursor(result.nextCursor ?? null); }
  }
  const sessions = [...(capture?.sessions || []).filter(session => session.targetProfileId === (explicitTarget ? captureTarget : profileId)).reduce((map, session) => {
    const prior = map.get(session.runId); map.set(session.runId, prior ? { ...session, packets: prior.packets + session.packets, synthetic: prior.synthetic || session.synthetic } : session);
    return map;
  }, new Map<string, NonNullable<CodingCaptureResult["sessions"]>[number]>()).values()];
  const targets = [...new Set((capture?.sessions || []).map(session => session.targetProfileId))];
  const targetAllowed = captureTargets.some(target => target.targetProfileId === captureTarget);
  const label = (status: string) => ({ "candidate-block": "完整候选数据块", "synthetic-capture-excluded": "模拟响应，单列保留",
    "response-observed": "已收到完整响应", "synthetic-response": "模拟响应",
    "identity-response-observed": "完整身份观察响应", "synthetic-identity-response": "模拟身份响应",
    "request-not-in-identity-scope": "当前单元全部身份候选范围外的请求", "missing_terminator": "来源要求的终止符缺失",
    "decoded-response-observed": "按来源解码的观察响应", "synthetic-decoded-response": "模拟故障响应",
    "request-not-in-dtc-scope": "当前链接故障读取范围外的请求", "negative-response": "控制单元否定响应",
    "count-inconsistent-dtc": "故障响应长度或数量不符", "truncated-dtc": "故障响应头不完整", "positive-prefix-mismatch": "响应服务或标识不符",
    "request-not-in-measurement-scope": "当前版本实时数据范围外的请求",
    "cleanup-response-unqualified": "结束清理期间的响应", "native-result-unqualified": "原生结果身份或标志不符",
    "native-primitive-context-missing": "旧捕获缺少当次请求通信句柄", "native-primitive-mismatch": "响应来自其他通信句柄",
    "coding-read-short-block": "响应短于已知字段要求", "coding-read-block-length-mismatch": "完整长度不符",
    "capture-packet-error": "捕获包格式或来源信息异常", "negative_response": "控制单元否定响应",
    "negative": "控制单元否定响应", "pending": "控制单元尚未完成响应", "negative_wrong_sid": "否定响应对应其他服务",
    "truncated_negative": "否定响应不完整", "response_prefix_mismatch": "响应服务或标识不符",
    "wrong_sid": "响应服务不符", "wrong_did": "响应数据标识不符", "wrong_lid": "响应本地标识不符", "truncated": "响应头不完整",
    "response_pending": "控制单元尚未完成响应" } as Record<string, string>)[status] || diagnosticMessage(status);
  return <section data-testid={`${kind}-capture-panel`}>
    <h4>{identity ? "断车后核对全部身份候选" : dtc ? "断车后核对完整故障响应" : measurement ? "断车后复解完整实时字段" : "断车后核对设码响应"}</h4>
    <p>选择本项目录制的完整应用响应 PCAPNG，按{identity ? "当前单元全部来源版本" : "当前资料版本"}和一次采集核对。请求目标尚不证明响应身份；核对结果不会创建原码基线或开放修改。{measurement && "每条响应展开来源中的全部字段，不受现场 12 项选参限制；缺值不会沿用上一笔。"}</p>
    {dtc && <>
      <label>来源诊断链接 <select aria-label="故障核对诊断链接" disabled={busy} value={linkIndex} onChange={e => {
        setLinkIndex(e.target.value); setAnalysis(undefined); setRows([]); setCursor(null); setSaved("");
        void operation(async () => { accept(await invoke?.({ action: "clear", clientId: client.current })); });
      }}><option value="">请选择明确的来源链接</option>{links.map(link => <option key={link.linkIndex} value={link.linkIndex}>链接 {link.linkIndex} · {link.requests.length} 组故障读取 · 缺项 {link.missing.length}</option>)}</select></label>
      {!busy && !links.length && !error && <p>此版本没有可核对的来源诊断链接，请保留缺项，补采实际版本和链接信息。</p>}
    </>}
    <button type="button" disabled={busy || !invoke || (dtc && !links.some(link => String(link.linkIndex) === linkIndex && link.requests.length > 0))} onClick={() => void operation(async () => {
      const result = accept(await invoke?.({ action: "open", clientId: client.current }));
      if (!mounted.current || result.canceled) return;
      setCapture(result); setAnalysis(undefined); setRows([]); setCursor(null); setRunId(""); setCaptureTarget(""); setSaved("");
    })}>选择应用响应捕获</button>
    {capture && <>
      <p>{capture.fileName} · {capture.capture?.packets} 个完整捕获包</p>
      {capture.capture?.error && <p role="alert">仅保留可解析的完整前缀：{capture.capture.error}</p>}
      {explicitTarget && <>
        <label>捕获中的运行目标 <select aria-label={identity ? "身份响应捕获目标" : coding ? "设码响应捕获目标" : "故障响应捕获目标"} value={captureTarget} disabled={busy} onChange={event => {
          setCaptureTarget(event.target.value); setRunId(""); setAnalysis(undefined); setRows([]); setCursor(null); setSaved("");
          void operation(async () => { accept(await invoke?.({ action: "clear", clientId: client.current })); });
        }}><option value="">请选择捕获中的运行目标</option>{targets.map(target => {
          const allowed = captureTargets.some(row => row.targetProfileId === target);
          return <option key={target} value={target} disabled={!allowed}>{target}{allowed ? "" : " · 不能关联当前车型/单元来源"}</option>;
        })}</select></label>
        <p>{identity ? "对照当前单元全部来源版本" : "捕获目标与来源版本分别选择"}，仅比较当前车型/单元的完整响应；不识别车辆身份或实际版本，不改写原件。</p>
      </>}
      <label>本次采集 <select aria-label={identity ? "身份响应采集会话" : dtc ? "故障响应采集会话" : measurement ? "实时响应采集会话" : "设码响应采集会话"} value={runId} disabled={busy || (explicitTarget && !targetAllowed)} onChange={event => {
        setRunId(event.target.value); setAnalysis(undefined); setRows([]); setCursor(null); setSaved("");
        void operation(async () => { accept(await invoke?.({ action: "clear", clientId: client.current })); });
      }}><option value="">请选择本次采集</option>{sessions.map(session => <option key={session.runId} value={session.runId}>
        {session.synthetic ? "含模拟 · " : ""}{session.runId} · {session.packets} 包
      </option>)}</select></label>
      {!sessions.length && <p>{explicitTarget ? "请先选择可比较的捕获目标，再选择该目标的一次采集。" : "此捕获没有当前资料版本的采集会话，请核对版本和文件。"}</p>}
      <button type="button" disabled={busy || !runId || !capture.captureId || (explicitTarget && !targetAllowed)} onClick={() => void operation(async () => {
        setAnalysis(undefined); setRows([]); setCursor(null);
        const result = accept(await invoke?.({ action: "analyze", clientId: client.current,
          captureId: capture.captureId!, generation, runId, ...(identity && identityContext ? { ...identityContext, captureTargetProfileId: captureTarget } : { profileId }), ...(dtc && dtcContext ? { ...dtcContext, linkIndex: Number(linkIndex), captureTargetProfileId: captureTarget } : {}), ...(codingContext ? { ...codingContext, captureTargetProfileId: captureTarget } : {}) }));
        if (!mounted.current) return;
        setAnalysis(result); if (result.analysisId) await page(result.analysisId, 0, false);
      })}>离线核对本次响应</button>
    </>}
    {busy && <button type="button" onClick={() => void invoke?.({ action: "cancel", clientId: client.current })}>取消响应核对</button>}
    {analysis?.report && <div data-testid={`${kind}-capture-result`}>
      <p>{analysis.report.definitionFieldCount} {dtc ? "条来源故障定义" : "个来源字段"} · {analysis.report.requestGroupCount} 组读取 · 缺少 {analysis.report.missingReadRequests.length} 组 · 来源缺项 {analysis.report.definitionMissing.length} 项</p>
      {coding && analysis.report.blockSet && <div data-testid="coding-block-set">
        <p>捕获目标：{analysis.report.captureTargetProfileId}；来源版本：{analysis.report.sourceProfileId}。</p>
        <p>按读取组整理完整响应，保留未知尾部字节；缺组、失败和不同码值分别列出。重复一致不证明原子读取或完整编码长度，不创建车辆原码基线。</p>
        {analysis.report.blockSet.reports.map(r => <p key={r.source}>{r.source === "synthetic" ? "模拟响应" : "捕获响应"}：一致 {r.counts.consistent || 0} 组 · 缺少 {r.counts.missing || 0} 组 · 冲突 {r.counts.conflicting || 0} 组 · 失败 {r.counts.failed || 0} 组{r.consistentReadSetAvailable ? "；已定义读取组均有一致响应，完整车辆编码范围尚未确认。" : "；尚无可用的一致读取集合。"}</p>)}
        {!analysis.report.blockSet.captureIntegrityComplete && <p role="alert">捕获不完整，不能形成一致读取集合。</p>}
        <label>核对内容 <select aria-label="设码核对内容" value={view} disabled={busy} onChange={e => {
          const next = e.target.value as "blocks" | "responses"; setView(next); setRows([]); setCursor(null);
          void operation(() => page(analysis.analysisId!, 0, false, next));
        }}><option value="responses">逐响应原文与失败</option><option value="blocks">读取组完整性与码值</option></select></label>
      </div>}
      {identity && <>
        <p>{analysis.report.candidateVersionCount} 个候选版本；实际观察独立解码一致 {analysis.report.fieldObservationCounts?.["observed-independent-agreement"] || 0} 条，模拟一致 {analysis.report.fieldObservationCounts?.["synthetic-independent-agreement"] || 0} 条；失败或不一致 { (analysis.report.fieldObservationCounts?.["observed-failed-or-disagreed"] || 0) + (analysis.report.fieldObservationCounts?.["synthetic-failed-or-disagreed"] || 0)} 条。未确定车辆身份或实际版本。</p>
        <details><summary>查看全部候选版本缺项与冲突</summary><ul>{analysis.report.profiles?.map(p => <li key={p.sourceProfileId}>{p.sourceProfileId} · 来源字段 {p.sourceFieldCount} · 实际一致 {p.observedFieldsWithAgreement} · 模拟一致 {p.syntheticFieldsWithAgreement} · 缺实际观察 {p.missingObservedFields.length} · 冲突 {p.conflictingFields.length} · 来源缺项 {p.sourceMissing.length}</li>)}</ul></details>
        {analysis.report.versionRuleComparison && <details data-testid="identity-version-rules">
          <summary>查看来源版本规则候选（{analysis.report.versionRuleComparison.routeCount} 组）</summary>
          <p>按本次捕获响应计算已有来源中的版本规则。规则可能只比较部分字段；匹配结果仍需核对完整身份，不自动选择车辆版本。</p>
          <ul>{analysis.report.versionRuleComparison.reports.map((r, index) => <li key={`${r.routeIndexOffset}-${r.definitionOffset}-${r.source}`}>
            规则组 {Math.floor(index / 2) + 1} · {r.source === "synthetic" ? "模拟响应" : "捕获响应"} · {r.sourceVariantCount} 个来源版本 · 缺少 {r.missingRequests.length} 组 · 冲突 {r.conflictingRequests.length} 组 · 失败 {r.failedRequests.length} 组
            {r.calculationComplete ? (r.matchingCandidates.length ? <ul>{r.matchingCandidates.map(candidate => <li key={candidate.variant}>
              {candidate.variant} · {candidate.matchingRules.some(rule => rule.allFieldsCompared) ? "包含全部字段匹配" : "仅规则前缀匹配"}
            </li>)}</ul> : <p>规则计算完成，无匹配版本。</p>) : <p>{r.error ? "版本规则计算失败，原因保留在保存结果中。" : "版本规则未完成计算，请核对缺项、冲突及来源定义。"}</p>}
          </li>)}</ul>
        </details>}
        <label>核对内容 <select aria-label="身份核对内容" value={view} disabled={busy} onChange={e => {
          const next = e.target.value as "values" | "responses"; setView(next); setRows([]); setCursor(null);
          void operation(() => page(analysis.analysisId!, 0, false, next));
        }}><option value="values">全部候选字段值与失败</option><option value="responses">逐响应原文与失败</option></select></label>
      </>}
      {dtc && <p data-testid="dtc-comparison-context">捕获目标：{analysis.report.captureTargetProfileId}；来源版本：{analysis.report.sourceProfileId}。仅来源比较，车辆身份及实际版本均未核实。</p>}
      {dtc && <p>实际观察故障记录 {analysis.report.recordCounts?.observed || 0} 条；模拟故障记录 {analysis.report.recordCounts?.synthetic || 0} 条。文字仅为所选版本的来源候选；状态字节不解释为当前故障状态，空响应或失败不沿用之前的故障。</p>}
      {dtc && <label>核对内容 <select aria-label="故障核对内容" value={view} disabled={busy} onChange={e => {
        const next = e.target.value as "faults" | "responses"; setView(next); setRows([]); setCursor(null);
        void operation(() => page(analysis.analysisId!, 0, false, next));
      }}><option value="faults">逐条故障记录</option><option value="responses">逐响应原文与失败</option></select></label>}
      <p>完整记录按原始顺序保留：{Object.entries(analysis.report.counts).map(([status, count]) => `${label(status)} ${count}`).join("；") || "没有当前范围的响应"}</p>
      {measurement && <>
        <p>来源代际：{analysis.report.sourceGeneration || "未确定"}；所选视图：{generation}。所选视图不证明精确适用性或车辆身份。</p>
        <p>实际观察中可解 {analysis.report.decodedSourceFieldCount} 个来源字段；模拟可解 {analysis.report.syntheticDecodedSourceFieldCount} 个；缺名 {analysis.report.nameMissing?.length || 0} 项。可解仅表示来源解码成功，尚未确认是本车值。</p>
        <label>核对内容 <select aria-label="实时复解内容" value={view} disabled={busy} onChange={event => {
          const next = event.target.value as "values" | "responses"; setView(next); setRows([]); setCursor(null);
          void operation(() => page(analysis.analysisId!, 0, false, next));
        }}><option value="values">逐字段值与失败</option><option value="responses">逐响应原文</option></select></label>
      </>}
      {!!analysis.report.changedGroups.length && <p>同次采集中码值发生变化：{analysis.report.changedGroups.map(group => `${group.requestHex}（${group.distinctValues}种）`).join("、")}</p>}
      {!!analysis.report.missingReadRequests.length && <details><summary>查看全部缺少的读取组</summary><p>{analysis.report.missingReadRequests.join("、")}</p></details>}
      {analysis.report.capture.error && <p role="alert">捕获末尾未完整解析：{analysis.report.capture.error}；此前完整响应已保留。</p>}
      <button type="button" disabled={busy || !analysis.analysisId} onClick={() => void operation(async () => {
        const result = accept(await invoke?.({ action: "save", clientId: client.current, analysisId: analysis.analysisId! }));
        if (mounted.current && result.saved) setSaved(result.filePath || "已保存");
      })}>保存完整核对结果 ZIP</button>
      {saved && <p role="status">已保存：{saved}</p>}
      <p>结果暂存在本次页面，换文件、资料版本或关闭页面即丢弃；保存 ZIP 包含完整核对报告和全部本次核对响应。{coding && "还包含全部读取组的完整性、首条完整候选码值、缺项和冲突，不受预览分页限制。"}{measurement && "还包含全部逐字段值、失败及完整来源字段目录，不受预览分页限制。"}{identity && "还包含全部候选版本、完整字段定义、逐次字段值与失败，不受预览分页限制。"}{dtc && "还包含完整版本故障字典，不受预览分页限制。"}原始 PCAPNG 请另行保留。</p>
      <ol>{rows.map(row => <li key={`${row.source || ""}:${row.requestHex || ""}:${row.packet}:${row.sourceProfileId || ""}:${row.sourceFieldIndex ?? row.parameterId ?? row.recordIndex ?? "response"}`}>{row.requestHex || "无法关联请求"}{row.packet != null && <> · 包 {row.packet}</>}
        {view === "blocks" ? <>
          <p>{row.source === "synthetic" ? "模拟响应" : "捕获响应"} · {({ missing: "缺少响应", conflicting: "码值冲突", failed: "存在失败", consistent: "重复响应一致" } as Record<string, string>)[row.status || ""]} · 收到 {row.observations} 次 · 有效完整响应 {row.validResponses} 次</p>
          {Object.entries(row.failures || {}).map(([reason, count]) => <p key={reason}>{label(reason)} {count} 次</p>)}
          {row.representative && <details><summary>查看首条完整候选码值</summary><p style={{ overflowWrap: "anywhere" }}>{row.representative.dataHex}</p><p>包 {row.representative.packet}；保留全部返回字节，尚未认定为车辆完整原码。</p></details>}
        </> : identity && row.value ? <p>{row.synthetic ? "模拟 · " : ""}{row.sourceProfileId} · {row.sourceName || `来源字段 ${row.sourceFieldIndex}`}：{row.value.ok ? String(row.value.text ?? row.value.phys ?? row.value.value ?? row.value.hex ?? "--") : `--（${label(row.value.reason || "解码失败")}）`} · {row.independentDecoderAgreement ? "独立解码一致，未确认车辆值" : `独立解码未通过（${row.issue || "未明确"}）`}</p> : row.dtcHex ? <p>{row.synthetic ? "模拟 · " : ""}{row.sourceDisplayCode || row.dtcHex} · 状态原值 {row.statusHex} · {row.sourceText ?? (row.definitionBindingStatus === "source-number-missing" ? "字典无此编号" : row.definitionBindingStatus === "source-number-ambiguous-or-unresolved" ? "字典编号有歧义或定义缺项" : "来源缺少文字")}{row.sourceTextState === "source-empty" ? "来源文字为空" : ""} · 电脑观察时间 {row.elapsedHostMs} ms</p> : row.result ? <p>{row.synthetic ? "模拟 · " : ""}{row.name}：{row.result.ok ? String(row.result.display ?? row.result.text ?? row.result.value ?? "--") : `--（${label(row.result.reason || "解码失败")}）`} {row.unit || ""} · 电脑观察时间 {row.elapsedHostMs} ms</p> : <>
          {label(row.status || "capture-packet-error")}<details><summary>查看完整原始响应</summary><p style={{ overflowWrap: "anywhere" }}>PDU：{row.pduHex || row.error}</p>
          {row.dataHex !== undefined && <p style={{ overflowWrap: "anywhere" }}>候选原始码值：{row.dataHex}</p>}</details>
          {dtc && row.decodedObservation?.ok && <p>本条完整响应包含 {row.decodedObservation.recordCount} 条故障记录。</p>}</>}</li>)}</ol>
      {cursor !== null && <button type="button" disabled={busy} onClick={() => void operation(() => page(analysis.analysisId!, cursor, true))}>继续查看后续响应</button>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
