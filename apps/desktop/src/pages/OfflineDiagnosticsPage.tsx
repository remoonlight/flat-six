import { useCallback, useEffect, useRef, useState } from "react";
import {
  callOfflineDiagnostics,
  hasDesktopApi,
  offlineDiagnosticsFixtureEnabled,
  type OfflineDiagnosticsRequest,
  type OfflineDiagnosticsResult,
} from "../api";
import "../offline-diagnostics.css";

type Gen = "981" | "982";
type Category = "identity" | "measurement" | "coding" | "dtc" | "routine";
type Scope =
  | "summary"
  | "variants"
  | "records"
  | "decode"
  | "preview"
  | "match"
  | "plan"
  | "replay";

const CATS: { id: Category; label: string }[] = [
  { id: "identity", label: "身份" },
  { id: "measurement", label: "测量" },
  { id: "coding", label: "设码定义" },
  { id: "dtc", label: "故障码定义" },
  { id: "routine", label: "例程" },
];

const EXAMPLE_IDENTITY = `{
  "generation": "981",
  "ecuId": 1,
  "identity": { "dsn": "P200" }
}`;

function asItems(doc: OfflineDiagnosticsResult | null): Record<string, unknown>[] {
  const items = doc?.items;
  return Array.isArray(items) ? (items as Record<string, unknown>[]) : [];
}

function parseRawValue(s: string): number | null {
  const t = s.trim();
  if (!/^-?\d+$/.test(t)) return null;
  const n = Number(t);
  return Number.isInteger(n) ? n : null;
}

function membershipLabel(v: Record<string, unknown>): string {
  const raw = String(v.membership || "");
  const m =
    raw === "confirmed" ? "源车型名称已确认" : raw === "candidate" ? "候选" : raw || "未标注";
  const cap = v.observedCapture ? " · 捕获已观察" : "";
  const shared = v.generationShared ? " · 共用目录" : "";
  return `${m}${cap}${shared}`;
}

type ManualNeed = {
  sourceLabel?: string;
  itemsPreview?: string[];
  uncertainGlyph?: boolean;
};
type ManualHit = {
  relation?: string;
  rawCode?: string;
  docName?: string;
  pages?: number[];
  bodyEvidenceStatus?: string;
  bodyApplicability?: string;
  caymanBody?: boolean;
  needs?: ManualNeed[];
};
type ManualJoin = { exactHits?: ManualHit[]; relatedBaseCodeHits?: ManualHit[] };

function asManualJoin(v: unknown): ManualJoin | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as ManualJoin;
  return {
    exactHits: Array.isArray(o.exactHits) ? o.exactHits : [],
    relatedBaseCodeHits: Array.isArray(o.relatedBaseCodeHits) ? o.relatedBaseCodeHits : [],
  };
}

function renderHit(h: ManualHit): string {
  const pages = (h.pages || []).join(",") || "?";
  const body = h.caymanBody ? "Cayman车身" : h.bodyApplicability || "";
  return `${h.relation || ""} ${h.rawCode || ""} ${h.docName || ""} p${pages} ${h.bodyEvidenceStatus || ""} ${body}`.trim();
}

export function OfflineDiagnosticsPage() {
  const desktop = hasDesktopApi() || offlineDiagnosticsFixtureEnabled();
  const tokens = useRef<Record<Scope, number>>({
    summary: 0,
    variants: 0,
    records: 0,
    decode: 0,
    preview: 0,
    match: 0,
    plan: 0,
    replay: 0,
  });
  const [generation, setGeneration] = useState<Gen>("981");
  const [ecuId, setEcuId] = useState<number | "">("");
  const [menuEcus, setMenuEcus] = useState<Array<{ ecuId: number; label: string }>>([]);
  const [summary, setSummary] = useState<OfflineDiagnosticsResult | null>(null);
  const [variantQ, setVariantQ] = useState("");
  const [variants, setVariants] = useState<OfflineDiagnosticsResult | null>(null);
  const [variantOff, setVariantOff] = useState(0);
  const [profileId, setProfileId] = useState("");
  const [category, setCategory] = useState<Category>("coding");
  const [recordQ, setRecordQ] = useState("");
  const [records, setRecords] = useState<OfflineDiagnosticsResult | null>(null);
  const [recordOff, setRecordOff] = useState(0);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [dataHex, setDataHex] = useState("");
  const [responseMode, setResponseMode] = useState<"data" | "pdu">("data");
  const [previewHex, setPreviewHex] = useState("");
  const [rawValue, setRawValue] = useState("0");
  const [decodeOut, setDecodeOut] = useState<OfflineDiagnosticsResult | null>(null);
  const [previewOut, setPreviewOut] = useState<OfflineDiagnosticsResult | null>(null);
  const [identityText, setIdentityText] = useState("");
  const [matchOut, setMatchOut] = useState<OfflineDiagnosticsResult | null>(null);
  const [planOut, setPlanOut] = useState<OfflineDiagnosticsResult | null>(null);
  const [replayOut, setReplayOut] = useState<OfflineDiagnosticsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Partial<Record<Scope, boolean>>>({});

  const nextTok = (scope: Scope) => {
    tokens.current[scope] += 1;
    return tokens.current[scope];
  };
  const live = (scope: Scope, tok: number) => tokens.current[scope] === tok;
  const bump = (scopes: Scope[]) => {
    for (const s of scopes) tokens.current[s] += 1;
    setBusy((b) => {
      const n = { ...b };
      for (const s of scopes) n[s] = false;
      return n;
    });
  };

  const run = useCallback(async (scope: Scope, req: OfflineDiagnosticsRequest) => {
    const tok = nextTok(scope);
    setBusy((b) => ({ ...b, [scope]: true }));
    try {
      const doc = await callOfflineDiagnostics(req);
      if (!live(scope, tok)) return null;
      if (!doc.ok) setError(String(doc.error || doc.reason || scope));
      return doc;
    } catch (e) {
      if (live(scope, tok)) setError(String(e));
      return null;
    } finally {
      if (live(scope, tok)) setBusy((b) => ({ ...b, [scope]: false }));
    }
  }, []);

  const loadSummary = useCallback(async () => {
    setError(null);
    const doc = await run("summary", { action: "summary" });
    if (!doc) return;
    setSummary(doc);
    const menu = doc.menuEcus;
    if (Array.isArray(menu)) setMenuEcus(menu as Array<{ ecuId: number; label: string }>);
  }, [run]);

  const loadVariants = useCallback(
    async (off = 0) => {
      setError(null);
      const doc = await run("variants", {
        action: "variants",
        generation,
        ecuId: ecuId === "" ? undefined : ecuId,
        search: variantQ || undefined,
        offset: off,
        limit: 40,
      });
      if (!doc) return;
      setVariants(doc);
      setVariantOff(off);
    },
    [ecuId, generation, run, variantQ],
  );

  const loadRecords = useCallback(
    async (off = 0, pid = profileId) => {
      if (!pid) {
        setRecords(null);
        setSelected(null);
        return;
      }
      setError(null);
      const doc = await run("records", {
        action: "records",
        generation,
        ecuId: ecuId === "" ? undefined : ecuId,
        profileId: pid,
        category,
        search: recordQ || undefined,
        offset: off,
        limit: 40,
      });
      if (!doc) return;
      setRecords(doc);
      setRecordOff(off);
    },
    [category, ecuId, generation, profileId, recordQ, run],
  );

  const clearSelection = useCallback(() => {
    bump(["decode", "preview"]);
    setSelected(null);
    setDecodeOut(null);
    setPreviewOut(null);
  }, []);

  const invalidateContext = useCallback(() => {
    bump(["decode", "preview", "records", "plan", "match"]);
    setSelected(null);
    setDecodeOut(null);
    setPreviewOut(null);
    setPlanOut(null);
    setMatchOut(null);
    setRecords(null);
  }, []);

  useEffect(() => {
    if (!desktop) return;
    loadSummary();
  }, [desktop, loadSummary]);

  useEffect(() => {
    if (!desktop) return;
    setProfileId("");
    invalidateContext();
    loadVariants(0);
    // search is explicit
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop, generation, ecuId]);

  useEffect(() => {
    bump(["decode", "preview", "records", "plan", "match"]);
    setSelected(null);
    setDecodeOut(null);
    setPreviewOut(null);
    setPlanOut(null);
    setMatchOut(null);
    if (!desktop || !profileId) {
      setRecords(null);
      return;
    }
    loadRecords(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, profileId]);

  if (!desktop) {
    return (
      <section className="panel offline-diag-desktop-only" data-page="offline-diagnostics">
        <h2>离线工作台</h2>
        <p className="callout">需要桌面端才能查询本机定义库。浏览器只读，不会假装已连接车辆。</p>
        <p className="muted">车辆动作全部禁用。设码预览只改本地缓冲区。</p>
      </section>
    );
  }

  const variantItems = asItems(variants);
  const recordItems = asItems(records);
  const variantTotal = Number(variants?.total || 0);
  const recordTotal = Number(records?.total || 0);
  const proto = summary?.protocolInventory as {
    counts?: { streamedVariants?: number; menuGroups?: number };
    groups?: unknown[];
    sysdata?: { checked?: boolean; systemCount?: number };
  } | undefined;
  const values = summary?.valueSupport as { decodeCounts?: Record<string, Record<string, number>>; present?: boolean } | undefined;
  const manual = summary?.manualEvidence as { present?: boolean; dtcEntryCount?: number; sources?: unknown[] } | undefined;
  const busyAny = Object.entries(busy).find(([, v]) => v)?.[0] ?? null;

  function parseIdentity(): Record<string, unknown> | null {
    const t = identityText.trim();
    if (!t) return null;
    try {
      const o = JSON.parse(t);
      return o && typeof o === "object" && !Array.isArray(o) ? o : null;
    } catch {
      return null;
    }
  }

  async function runDecode() {
    if (!profileId || selected?.at == null) {
      setError("请先选择一条定义");
      return;
    }
    setError(null);
    const doc = await run("decode", {
      action: "decode",
      generation,
      ecuId: ecuId === "" ? undefined : ecuId,
      profileId,
      category,
      recordAt: Number(selected.at),
      dataHex,
      responseMode,
    });
    if (doc) setDecodeOut(doc);
  }

  async function runPreview() {
    if (!profileId || selected?.at == null) {
      setError("请先选择一条设码定义");
      return;
    }
    const raw = parseRawValue(rawValue);
    if (raw == null) {
      setError("rawValue 必须是整数");
      return;
    }
    setError(null);
    const doc = await run("preview", {
      action: "preview",
      generation,
      ecuId: ecuId === "" ? undefined : ecuId,
      profileId,
      category,
      recordAt: Number(selected.at),
      dataHex: previewHex,
      rawValue: raw,
    });
    if (doc) setPreviewOut(doc);
  }

  async function runMatch() {
    const identity = parseIdentity();
    if (!identity) {
      setError("身份 JSON 无效（示例不是本车数据）");
      return;
    }
    setError(null);
    const doc = await run("match", {
      action: "match",
      generation,
      ecuId: ecuId === "" ? undefined : ecuId,
      identity,
    });
    if (doc) setMatchOut(doc);
  }

  async function runPlan() {
    const identity = identityText.trim() ? parseIdentity() : undefined;
    if (identityText.trim() && !identity) {
      setError("身份 JSON 无效");
      return;
    }
    setError(null);
    const doc = await run("plan", {
      action: "plan",
      generation,
      ecuId: ecuId === "" ? undefined : ecuId,
      profileId: profileId || undefined,
      identity: identity ?? undefined,
    });
    if (doc) setPlanOut(doc);
  }

  function downloadPlan() {
    if (!planOut) return;
    const blob = new Blob([JSON.stringify(planOut, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `offline-plan-${generation}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function runReplay() {
    setError(null);
    const doc = await run("replay", { action: "replay" });
    if (doc) setReplayOut(doc);
  }

  const sys = records?.systemEvidence as { registry?: { locator?: string; pages?: number[]; wm?: string }; manual?: { headings?: Array<{ title?: string; page?: number }> } } | undefined;

  return (
    <div className="offline-diag" data-page="offline-diagnostics">
      <section className="panel">
        <h2>离线工作台</h2>
        <p className="offline-diag-banner callout">
          离线定义源与已测范围。车辆连接、清码、设码写入全部禁用。示例已标明，不是本车实采。
        </p>
        <p className="muted" data-testid="od-source-status">
          车代范围 981/982 · 菜单 ECU {menuEcus.length} 条目
          {proto?.counts?.streamedVariants != null
            ? ` · 变体流 ${proto.counts.streamedVariants} 条目（跨变体含重复）`
            : ""}
          {values?.decodeCounts?.coding?.supported != null
            ? ` · 设码可解码 ${values.decodeCounts.coding.supported} 条目`
            : ""}
          {manual?.present ? ` · 手册 DTC ${manual.dtcEntryCount} 条目` : " · 手册证据缺席"}
          {proto?.sysdata?.systemCount != null
            ? ` · SYS ${proto.sysdata.checked ? "已核对" : "未核对"} ${proto.sysdata.systemCount} 系统（静态候选）`
            : ""}
        </p>
        {summary?.protocolNotReady ? <p className="muted">协议清单未就绪。</p> : null}
        {summary?.valueSupportNotReady ? <p className="muted">数值支持表未就绪。</p> : null}
        {variants && variants.present === false ? <p className="muted">变体库缺席。</p> : null}
        {error ? (
          <p className="error" data-testid="od-error">
            {error}
          </p>
        ) : null}
        {busyAny ? <p className="muted">加载中：{busyAny}</p> : null}

        <div className="offline-diag-toolbar">
          <label>
            车代
            <select
              data-testid="od-generation"
              value={generation}
              onChange={(e) => setGeneration(e.target.value as Gen)}
            >
              <option value="981">981</option>
              <option value="982">982</option>
            </select>
          </label>
          <label>
            ECU（35）
            <select
              data-testid="od-ecu"
              value={ecuId === "" ? "" : String(ecuId)}
              onChange={(e) => setEcuId(e.target.value === "" ? "" : Number(e.target.value))}
            >
              <option value="">全部</option>
              {menuEcus.map((e) => (
                <option key={e.ecuId} value={e.ecuId}>
                  {e.ecuId} · {e.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <div className="offline-diag-split">
        <section className="panel">
          <h3>变体</h3>
          <div className="offline-diag-filters">
            <label>
              搜索
              <input
                data-testid="od-variant-search"
                value={variantQ}
                onChange={(e) => setVariantQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && loadVariants(0)}
              />
            </label>
            <button type="button" onClick={() => loadVariants(0)}>
              筛选
            </button>
          </div>
          <p className="muted" data-testid="od-variant-total">
            {variantTotal} 条{profileId ? ` · 当前 ${profileId}` : ""}
          </p>
          <ul className="offline-diag-list">
            {variantItems.length === 0 ? <li className="muted">无匹配变体</li> : null}
            {variantItems.map((v) => (
              <li key={String(v.profileId)}>
                <button
                  type="button"
                  className={`ghost${profileId === v.profileId ? " active" : ""}`}
                  onClick={() => {
                    setProfileId(String(v.profileId));
                    clearSelection();
                  }}
                >
                  {String(v.name)} · <span data-testid="od-membership">{membershipLabel(v)}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="offline-diag-actions">
            <button type="button" disabled={variantOff <= 0} onClick={() => loadVariants(Math.max(0, variantOff - 40))}>
              上一页
            </button>
            <button type="button" disabled={variantOff + 40 >= variantTotal} onClick={() => loadVariants(variantOff + 40)}>
              下一页
            </button>
          </div>
        </section>

        <section className="panel">
          <h3>定义</h3>
          <div className="offline-diag-filters">
            <label>
              类别
              <select data-testid="od-category" value={category} onChange={(e) => setCategory(e.target.value as Category)}>
                {CATS.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              搜索
              <input
                data-testid="od-record-search"
                value={recordQ}
                onChange={(e) => setRecordQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && loadRecords(0)}
              />
            </label>
            <button type="button" onClick={() => loadRecords(0)} disabled={!profileId}>
              查询
            </button>
          </div>
          <p className="muted">{profileId ? `${recordTotal} 条` : "先选变体"}</p>
          <ul className="offline-diag-list" data-testid="od-record-list">
            {profileId && recordItems.length === 0 ? <li className="muted">无匹配定义</li> : null}
            {recordItems.map((r) => (
              <li key={String(r.at)}>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    bump(["decode", "preview"]);
                    setDecodeOut(null);
                    setPreviewOut(null);
                    setSelected(r);
                  }}
                >
                  {String(r.displayName || r.name || r.at)}
                  {r.dtc && typeof r.dtc === "object" ? ` · ${(r.dtc as { code?: string }).code}` : ""}
                </button>
              </li>
            ))}
          </ul>
          <div className="offline-diag-actions">
            <button type="button" disabled={recordOff <= 0} onClick={() => loadRecords(Math.max(0, recordOff - 40))}>
              上一页
            </button>
            <button type="button" disabled={recordOff + 40 >= recordTotal} onClick={() => loadRecords(recordOff + 40)}>
              下一页
            </button>
          </div>
        </section>
      </div>

      <section className="panel">
        <h3>选中定义</h3>
        {!selected ? (
          <p className="muted">未选择</p>
        ) : (
          <dl className="offline-diag-def" data-testid="od-selected">
            <dt>名称 / 单位</dt>
            <dd>
              {String(selected.displayName || selected.name || "—")} · {String(selected.unit || "无单位")}
            </dd>
            <dt>读请求候选</dt>
            <dd data-testid="od-request-candidate">禁用（候选不是实车请求）</dd>
            <dt>标签 / 枚举来源</dt>
            <dd>
              标签 {String(selected.labelSource)} · 枚举 {String(selected.enumSource)}
            </dd>
            <dt>手册条目</dt>
            <dd data-testid="od-manual">
              {category !== "dtc" ? (
                "手册对齐仅用于故障码定义"
              ) : (
                (() => {
                  const join = asManualJoin(selected.manual);
                  const exact = join?.exactHits || [];
                  const related = join?.relatedBaseCodeHits || [];
                  if (!exact.length && !related.length) {
                    return "无精确 rawCode 对齐；有同基码相关条目则单独列出，不合并子类型";
                  }
                  return (
                    <div>
                      {exact.length ? (
                        <p>精确 {exact.map(renderHit).join(" · ")}</p>
                      ) : (
                        <p>无精确 rawCode 对齐</p>
                      )}
                      {related.length ? (
                        <ul data-testid="od-manual-related">
                          {related.map((h, i) => (
                            <li key={`${h.rawCode}-${i}`}>
                              相关 {renderHit(h)}
                              {(h.needs || []).slice(0, 3).map((n, j) => (
                                <span key={j}>
                                  {" "}
                                  · {n.sourceLabel}
                                  {n.uncertainGlyph ? (
                                    <em className="od-uncertain"> 字形不确定</em>
                                  ) : null}
                                  {(n.itemsPreview || []).length
                                    ? `：${(n.itemsPreview || []).slice(0, 4).join("；")}`
                                    : ""}
                                </span>
                              ))}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  );
                })()
              )}
            </dd>
            {selected.unsupportedReason ? (
              <>
                <dt>不支持原因</dt>
                <dd>{String(selected.unsupportedReason)}</dd>
              </>
            ) : null}
          </dl>
        )}
        {sys?.registry ? (
          <p className="muted" data-testid="od-system-evidence">
            系统手册 {sys.registry.locator || "—"} WM {sys.registry.wm || "—"} 页{" "}
            {(sys.registry.pages || []).join(",") || "—"}
            {sys.manual?.headings?.[0] ? ` · ${sys.manual.headings[0].title}` : ""}
          </p>
        ) : null}
        <details>
          <summary>高级细节</summary>
          <pre className="muted">{selected ? JSON.stringify(selected, null, 2) : "—"}</pre>
        </details>
      </section>

      <section className="panel">
        <h3>显式 hex 解码</h3>
        <p className="muted offline-diag-example">示例（非本车数据）：EU5 巡航 at=4047899，hex <code>01</code></p>
        <label>
          输入形态
          <select
            data-testid="od-response-mode"
            value={responseMode}
            onChange={(e) => setResponseMode(e.target.value as "data" | "pdu")}
          >
            <option value="data">载荷数据</option>
            <option value="pdu">完整诊断响应</option>
          </select>
        </label>
        <label>
          dataHex
          <input data-testid="od-decode-hex" value={dataHex} onChange={(e) => setDataHex(e.target.value)} placeholder="01" />
        </label>
        <button type="button" data-testid="od-decode" onClick={() => runDecode()}>
          解码
        </button>
        {decodeOut ? (
          <p data-testid="od-decode-out">
            {decodeOut.ok
              ? `结果 ${String(decodeOut.text ?? decodeOut.display ?? decodeOut.value)}`
              : `失败 ${String(decodeOut.error || decodeOut.reason)}`}
          </p>
        ) : null}
      </section>

      <section className="panel">
        <h3>设码缓冲预览</h3>
        <p className="muted offline-diag-example">示例（非本车数据）：缓冲 <code>A5</code>，rawValue <code>0</code></p>
        <div className="offline-diag-filters">
          <label>
            原缓冲 hex
            <input data-testid="od-preview-hex" value={previewHex} onChange={(e) => setPreviewHex(e.target.value)} placeholder="A5" />
          </label>
          <label>
            rawValue
            <input data-testid="od-preview-raw" value={rawValue} onChange={(e) => setRawValue(e.target.value)} />
          </label>
        </div>
        <button type="button" data-testid="od-preview" onClick={() => runPreview()}>
          预览
        </button>
        <p className="muted">无车辆写入。</p>
        {previewOut ? (
          <dl className="offline-diag-def" data-testid="od-preview-out">
            <dt>before</dt>
            <dd>{String(previewOut.beforeHex || "—")}</dd>
            <dt>after</dt>
            <dd>{String(previewOut.afterHex || previewOut.error || previewOut.reason || "—")}</dd>
            <dt>changed mask</dt>
            <dd>{String(previewOut.changedBitMaskHex || "—")}</dd>
          </dl>
        ) : null}
      </section>

      <section className="panel">
        <h3>ECU 身份匹配</h3>
        <p className="muted offline-diag-example">下列框为示例 JSON，不是本车指纹。</p>
        <textarea
          data-testid="od-identity"
          rows={7}
          value={identityText}
          onChange={(e) => setIdentityText(e.target.value)}
          placeholder={EXAMPLE_IDENTITY}
        />
        <div className="offline-diag-actions">
          <button type="button" className="ghost" onClick={() => setIdentityText(EXAMPLE_IDENTITY)}>
            填入示例
          </button>
          <button type="button" data-testid="od-match" onClick={() => runMatch()}>
            匹配
          </button>
        </div>
        {matchOut ? (
          <p data-testid="od-match-out">
            资格 {(matchOut.identityQualification as { status?: string } | undefined)?.status || matchOut.error}
          </p>
        ) : null}
      </section>

      <section className="panel">
        <h3>计划 JSON</h3>
        <div className="offline-diag-actions">
          <button type="button" data-testid="od-plan" onClick={() => runPlan()}>
            生成计划
          </button>
          <button type="button" data-testid="od-plan-download" disabled={!planOut} onClick={() => downloadPlan()}>
            下载 JSON
          </button>
        </div>
        {planOut ? <p data-testid="od-plan-out">组 {String(planOut.groupCount)} · 车辆动作禁用</p> : null}
      </section>

      <section className="panel">
        <h3>离线回放摘要</h3>
        <button type="button" data-testid="od-replay" onClick={() => runReplay()}>
          运行回放（目录/台架，无串口）
        </button>
        {replayOut ? (
          <p data-testid="od-replay-out">
            实采/捕获 {String((replayOut.summary as { realCount?: number } | undefined)?.realCount)} · 合成{" "}
            {String((replayOut.summary as { syntheticCount?: number } | undefined)?.syntheticCount)} · VIN 已脱敏
          </p>
        ) : null}
      </section>
    </div>
  );
}
