import { useEffect, useMemo, useRef, useState } from "react";
import {
  ANALYSIS_BATCH_LIMIT,
  ANALYSIS_STALE_MS,
  buildFaultDataPlan,
  headerFresh,
  resolveSkuToLocator,
  type AdapterIdentity,
  type ClearReport,
  type EcuChange,
  type EcuIdentity,
  type LiveHeader,
  type ModuleCapability,
  type ScanSnapshot,
  type ScannedDtc,
} from "@porsche981/domain";
import { api, type DtcEntry, type LocatorMap, type ObdDtc, type ObdLiveSnapshot, type ObdSession, type Part, type Vehicle } from "../api";
import type { LocatorFocus } from "../locator-focus";
import { CodingPage } from "./CodingPage";
import { ObdWorkbench } from "../obd/ObdWorkbench";
import { ObdDataAnalysis, type AnalysisJump } from "../obd/ObdDataAnalysis";
import { DiagnosticsPage } from "./DiagnosticsPage";

export type ObdPageProps = { onLocate?: (focus: LocatorFocus) => void };

type ObdTab = "adapter" | "faults" | "analysis" | "vehicle" | "coding" | "dev";
const TABS: { id: ObdTab; label: string }[] = [
  { id: "adapter", label: "OBD设备匹配设置" },
  { id: "faults", label: "故障码" },
  { id: "analysis", label: "数据分析" },
  { id: "vehicle", label: "车辆信息" },
  { id: "coding", label: "设码" },
  { id: "dev", label: "开发与验证" },
];

function faultKey(d: ScannedDtc) {
  return `${d.moduleKey}:${d.code}:${d.status}`;
}

/** Strip leftover handbook file/page citations; never treat DTC codes (P0571) as pages. */
function stripManualCitations(text: string): string {
  return text
    .replace(/出处[:：][^\n]*/g, "")
    .replace(/\b[\w .()/-]+\.pdf\b/gi, "")
    .replace(/第\s*\d+(?:\s*[、,]\s*\d+)*\s*页/g, "")
    .replace(/（\s*约?\s*p\.\s*\d[\d–—,-]*\s*页?\s*）/gi, "")
    .replace(/\(\s*约?\s*p\.\s*\d[\d–—,-]*\s*\)/gi, "")
    .replace(/\bpp?\.\s*\d+(?:\s*[-–]\s*\d+)?/gi, "")
    .replace(/[（(]\s*[）)]/g, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+([，。；、])/g, "$1")
    .trim();
}

function isSourceOnlyManualText(original: string, stripped: string): boolean {
  if (!stripped) return true;
  const compact = stripped.replace(/[《》「」[\]（）()\s，。；、：:·\-—]/g, "");
  if (compact.length < 8) return true;
  const sourceHeavy = /出处|出自|手册|\.pdf|页码|CMap|车间手册|Diagnostic Information|translated-from/i.test(original);
  const substance = /检查|测量|调整|观察|踏板|开关|电压|温度|转速|失火|故障|电路|传感器/.test(stripped) || /[PBCU]\d{4}/i.test(stripped);
  return sourceHeavy && !substance;
}

function hideManualSource(text: string): string {
  const stripped = stripManualCitations(text);
  return isSourceOnlyManualText(text, stripped) ? "" : stripped;
}

function problemTitle(dtc: ScannedDtc): string {
  return hideManualSource(dtc.manual?.title ?? "") || "问题名称待核实";
}

const STALE_MS = ANALYSIS_STALE_MS;
const EMPTY: ObdLiveSnapshot = {
  mock: false, op: null, selected: null,
  header: { adapterConnected: false, vehicleCommunicating: false, ignition: "unknown", voltage: null, stale: false, observedAt: null, adapter: null, mock: false },
  lastScan: null, capabilities: [], adapterConnected: false, vehicleCommunicating: false,
};

function fmtTime(iso: string | null | undefined) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

const DTC_STATUS: Record<string, string> = { stored: "已存储", pending: "待定", permanent: "永久", freeze: "冻结帧" };
const SCAN_STATUS: Record<string, string> = { success: "有码", none: "无码", failed: "读取失败", "not-supported": "当前不支持" };
const CLEAR_RESULT: Record<string, string> = {
  cleared: "存储码已清除", remaining: "仍有码", failed: "失败", unknown: "结果未知", "not-attempted": "未尝试",
};
const FIELD_ZH: Record<string, string> = {
  hardwareId: "硬件号", serial: "序列号", softwareId: "软件号", calibrationId: "标定", cvn: "校验号", codingFingerprint: "设码指纹",
};

export function ObdPage({ onLocate }: ObdPageProps) {
  const [tab, setTab] = useState<ObdTab>("adapter");
  const [live, setLive] = useState<ObdLiveSnapshot>(EMPTY);
  const [adapters, setAdapters] = useState<AdapterIdentity[]>([]);
  const [scan, setScan] = useState<ScanSnapshot | null>(null);
  const [clear, setClear] = useState<ClearReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clock, setClock] = useState(Date.now());
  const [faultPicked, setFaultPicked] = useState<Record<string, boolean>>({});
  const [jump, setJump] = useState<AnalysisJump | null>(null);
  const [analysisRunning, setAnalysisRunning] = useState(false);
  const pollRef = useRef(false);
  const analysisBusyRef = useRef(false);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let on = true;
    const off = api().onObdLive((next) => { if (on) setLive(next); });
    api().obdLive().then((n) => { if (on) { setLive(n); setScan(n.lastScan); } }).catch((e) => setError(String(e)));
    return () => { on = false; off(); };
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      if (pollRef.current || live.op || analysisBusyRef.current || analysisRunning || !live.adapterConnected) return;
      pollRef.current = true;
      api().obdPollStatus().catch(() => {}).finally(() => { pollRef.current = false; });
    }, 4000);
    return () => clearInterval(id);
  }, [live.op, live.adapterConnected, analysisRunning]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  const h: LiveHeader = live.header;
  const readingClock = Math.max(clock, Date.now());
  const fresh = headerFresh(h, readingClock, STALE_MS);
  const voltageFresh = h.voltage && live.adapterConnected && readingClock - Date.parse(h.voltage.observedAt) <= STALE_MS;
  const canScan = fresh && !live.op && !busy && !analysisRunning;
  const scanAge = readingClock - Date.parse(live.lastScan?.at ?? '');
  const canClear = canScan && Boolean(live.lastScan) && scanAge >= 0 && scanAge <= 120000;

  return (
    <div className="obd-page" data-page="obd">
      <header className="page-head obd-head">
        <div>
          <p className="muted obd-kicker">诊断</p>
          <h1>实时 OBD</h1>
          {live.mock ? <p className="obd-beta" data-obd="mock-banner">隔离测试传输，不是实车</p> : null}
        </div>
        <dl className="obd-live-header" aria-label="适配器与车辆状态">
          <div><dt>适配器</dt><dd data-obd="adapter-status">{h.adapterConnected ? "已连接" : "未连接"}</dd></div>
          <div><dt>车辆通信</dt><dd data-obd="vehicle-status">{fresh ? "有控制单元应答" : "无"}</dd></div>
          <div><dt>点火</dt><dd data-obd="ignition">{fresh && h.ignition === "running" ? "运转中" : "未知"}</dd></div>
          <div>
            <dt>电压</dt>
            <dd data-obd="voltage">
              {h.voltage && voltageFresh
                ? `${h.voltage.volts.toFixed(2)} V · ${h.voltage.source === "ATRV" ? "适配器" : "控制单元"} · ${fmtTime(h.voltage.observedAt)}${h.stale ? " · 已过期" : ""}`
                : "—"}
            </dd>
          </div>
        </dl>
      </header>

      <nav className="obd-tabs chip-row" aria-label="OBD 分区">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={`chip${tab === t.id ? " active" : ""}`} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </nav>
      {error ? <p className="error" role="alert">{error}</p> : null}

      {tab === "adapter" && (
        <section className="panel">
          <h2>OBD设备匹配设置</h2>
          <p className="muted">选择本机蓝牙串口。不会自动探测，也不会选用 RaceChrono 占用的设备。端口号会变化，以本次列表为准。</p>
          <div className="row">
            <button type="button" disabled={busy} onClick={() => run(async () => setAdapters(await api().obdListAdapters()))}>刷新端口</button>
            <button type="button" className="ghost" onClick={() => api().obdOpenBluetooth()}>打开系统蓝牙设置</button>
            <button type="button" disabled={busy || !live.selected || live.adapterConnected} onClick={() => run(async () => { await api().obdConnect(); await api().obdPollStatus(); })}>连接</button>
            <button type="button" disabled={busy || !live.adapterConnected} onClick={() => run(async () => { await api().obdDisconnect(); })}>断开</button>
          </div>
          {!adapters.length ? <p className="muted">点击刷新以列出当前可用串口。</p> : (
            <ul className="plain-list obd-adapter-list">
              {adapters.map((a) => (
                <li key={a.port + (a.pnpId ?? "")}>
                  <label>
                    <input type="radio" name="obd-adapter" disabled={a.occupied || busy}
                      checked={live.selected?.port === a.port && live.selected?.pnpId === a.pnpId}
                      onChange={() => run(async () => { await api().obdSelectAdapter(a); })} />
                    <strong>{a.friendlyName}</strong> · {a.port}
                    {a.preferred ? " · 名称含 vLinker，可优先考虑" : ""}
                    {a.occupied ? ` · ${a.occupiedReason}` : ""}
                  </label>
                </li>
              ))}
            </ul>
          )}
          {live.selected ? <p>已保存选择：{live.selected.friendlyName}（{live.selected.port}）</p> : null}
        </section>
      )}

      {tab === "faults" && (
        <section className="panel">
          <h2>故障码</h2>
          <p className="muted">当前支持标准排放诊断；未支持模块会单独列出。清除会抹掉诊断记录和就绪状态，不是修理。</p>
          <div className="row">
            <button type="button" disabled={!canScan} onClick={() => run(async () => {
              const s = await api().obdScanFaults();
              setScan(s);
              if (s.vehicleKey) await api().obdSetSavedVehicle(s.vehicleKey);
              setError(null);
            })}>读取当前故障码</button>
          </div>
          {!fresh ? <p>需要当前这次连接里、刚刚有效的控制单元应答后才能读码或清除。</p> : null}
          {scan ? (
            <ModuleScanView
              scan={scan}
              picked={faultPicked}
              onToggle={(key) => setFaultPicked((p) => ({ ...p, [key]: !p[key] }))}
            />
          ) : <p className="muted">尚未完成读取。</p>}
          <div className="row" data-obd="analysis-row">
            <button type="button" onClick={() => {
              const dtcs = (scan?.modules ?? []).flatMap((m) => m.dtcs).filter((d) => faultPicked[faultKey(d)]);
              const plan = buildFaultDataPlan(dtcs);
              const supported = plan.items.filter((i) => i.supported);
              const auto = Boolean(
                fresh && live.adapterConnected && plan.items.length > 0
                && plan.items.every((i) => i.supported) && plan.gaps.length === 0
                && supported.length <= ANALYSIS_BATCH_LIMIT,
              );
              const reasons: string[] = [];
              if (!dtcs.length) reasons.push("未选择故障，可在左侧目录自行勾选数据。");
              else if (!auto) {
                if (!fresh || !live.adapterConnected) reasons.push("当前没有有效车辆通信，未自动开始。");
                if (plan.items.some((i) => !i.supported)) reasons.push("所选故障包含当前不能采集的项目。");
                if (plan.gaps.length) reasons.push("仍有未核实或未实现的需求，未自动开始。");
                if (supported.length > ANALYSIS_BATCH_LIMIT) reasons.push(`可采集项目 ${supported.length} 项，超过软件暂定上限 ${ANALYSIS_BATCH_LIMIT}；清单完整保留，请手动选择本批。`);
              }
              setJump({ token: Date.now(), plan, autoStart: auto, holdReason: auto ? null : reasons.join(" ") || null });
              setTab("analysis");
            }}>实时数据分析</button>
          </div>
          <div className="row" data-obd="clear-row">
            <button type="button" disabled={!canClear} onClick={() => run(async () => {
              const r = await api().obdClearDtcs();
              setClear(r);
              if (r.readback) setScan(r.readback);
            })}>一键清除故障码</button>
          </div>
          {clear ? <ClearView report={clear} /> : null}
        </section>
      )}

      <div hidden={tab !== "analysis"}>
        <ObdDataAnalysis
          active={tab === "analysis"}
          live={live}
          scan={scan ?? live.lastScan}
          now={readingClock}
          jump={jump}
          analysisBusyRef={analysisBusyRef}
          onRunningChange={setAnalysisRunning}
        />
      </div>
      {tab === "vehicle" && <VehicleIdentityPanel capabilities={live.capabilities} />}
      {tab === "coding" ? <CodingPage /> : null}
      {tab === "dev" && (
        <>
          <ObdWorkbench tab="dev" />
          <DevManualLookup onLocate={onLocate} />
          <section className="panel" aria-label="故障台账">
            <h2>故障台账</h2>
            <p className="muted">手工台账，独立于上方实车读取。</p>
            <DiagnosticsPage onLocate={onLocate} />
          </section>
        </>
      )}
    </div>
  );
}

function ModuleScanView({ scan, picked, onToggle }: {
  scan: ScanSnapshot;
  picked: Record<string, boolean>;
  onToggle: (key: string) => void;
}) {
  const supported = scan.modules.filter((m) => m.status !== "not-supported");
  const unsupported = scan.modules.filter((m) => m.status === "not-supported");
  return (
    <div className="obd-scan">
      <p className="muted">读取时间 {fmtTime(scan.at)}</p>
      {supported.map((m) => (
        <article key={m.moduleKey} className="obd-module" data-obd="supported-module">
          <h3>{m.name} · {SCAN_STATUS[m.status]}{m.orderUnknown ? " · 顺序与厂家对应未核实" : ""}</h3>
          {m.detail ? <p className="muted">{m.detail}</p> : null}
          {m.dtcs.length === 0 && m.status === "none" ? <p>该应答器本次无存储、待定或永久码。</p> : null}
          <ul className="obd-fault-list">
            {m.dtcs.map((d) => (
              <FaultRow key={faultKey(d)} dtc={d} checked={Boolean(picked[faultKey(d)])} onToggle={() => onToggle(faultKey(d))} />
            ))}
          </ul>
        </article>
      ))}
      {unsupported.length ? (
        <section data-obd="unsupported">
          <h3>未支持模块</h3>
          <p className="muted">下列顺序仅前若干项已核实；其余顺序未核实。列表不表示这些模块一定装在本车上。</p>
          <ul>{unsupported.map((m) => <li key={m.moduleKey}>{m.name}{m.orderUnknown ? " · 顺序未核实" : ""}</li>)}</ul>
        </section>
      ) : null}
    </div>
  );
}

type FaultTab = "checks" | "data" | "about";

function FaultRow({ dtc, checked, onToggle }: { dtc: ScannedDtc; checked: boolean; onToggle: () => void }) {
  const [open, setOpen] = useState(false);
  const [pane, setPane] = useState<FaultTab>("checks");
  const title = problemTitle(dtc);
  const checks = (dtc.manual?.checks ?? []).map(hideManualSource).filter(Boolean);
  const about = hideManualSource(dtc.manual?.description ?? "");
  const applicability = hideManualSource(dtc.manual?.applicability ?? "");
  const hint = hideManualSource(dtc.manual?.moduleHint ?? "");
  const plan = useMemo(() => buildFaultDataPlan([dtc]), [dtc]);
  return (
    <li className="obd-fault-row" data-obd="fault-row" data-fault-key={faultKey(dtc)}>
      <div className="obd-fault-line">
        <label className="obd-fault-check" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`选择 ${dtc.code}`} />
        </label>
        <button type="button" className="obd-fault-title" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <strong>{dtc.code}</strong> — {title}
        </button>
        <span className="muted">{DTC_STATUS[dtc.status] ?? dtc.status} · 地址 {dtc.ecu}</span>
      </div>
      {open ? (
        <div className="obd-fault-detail" data-obd="fault-detail">
          <div className="obd-fault-panes" role="tablist">
            {([["checks", "检查办法"], ["data", "需要读取的数据"], ["about", "问题说明"]] as const).map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={pane === id} className={`chip${pane === id ? " active" : ""}`} onClick={() => setPane(id)}>{label}</button>
            ))}
          </div>
          {pane === "checks" ? (
            checks.length
              ? <ul>{checks.map((c) => <li key={c}>{c}</li>)}</ul>
              : <p className="muted">{dtc.manualFallback}</p>
          ) : null}
          {pane === "data" ? (
            <div>
              {plan.items.length ? (
                <ul>{plan.items.map((i) => (
                  <li key={i.key}>{i.name}{i.unit ? ` · ${i.unit}` : ""} · {i.supported ? "可采集" : `不可采集${i.reason ? `：${i.reason}` : ""}`}{i.note ? ` · ${i.note}` : ""}</li>
                ))}</ul>
              ) : <p className="muted">没有已整理的实时数据项。</p>}
              {plan.manualChecks.map((g) => <p key={g}>{g}</p>)}
              {plan.once.map((g) => <p key={g}>{g}</p>)}
              {plan.gaps.map((g) => <p key={g} className="muted">{g}</p>)}
            </div>
          ) : null}
          {pane === "about" ? (
            <div data-obd="fault-about">
              {about ? <p>{about}</p> : null}
              {applicability ? <p>{applicability}</p> : null}
              {hint ? <p>手册提到的系统（不能证明本车已安装该模块）：{hint}</p> : null}
              {!about && !applicability && !hint ? <p className="muted">暂无问题说明。</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function ClearView({ report }: { report: ClearReport }) {
  return (
    <section>
      <h3>清除结果</h3>
      <p>{report.scope || "只对当时在线的标准排放诊断应答器发出一次清除，不是针对每个厂家模块单独写入。"}</p>
      <p>{report.transportOk ? "适配器已返回结果" : "通信结果未确认"} · 控制单元确认清除：{report.ecuPositive ? "有" : "无"}</p>
      <ul>{report.modules.map((m) => <li key={m.moduleKey}>{m.name}：{CLEAR_RESULT[m.result] ?? m.result}{m.detail ? ` · ${m.detail}` : ""}</li>)}</ul>
      <details><summary>原始响应（开发核对）</summary><pre>{report.raw}</pre></details>
    </section>
  );
}

function VehicleIdentityPanel({ capabilities }: { capabilities: ModuleCapability[] }) {
  const [keys, setKeys] = useState<string[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [ecus, setEcus] = useState<EcuIdentity[]>([]);
  const [changes, setChanges] = useState<EcuChange[]>([]);
  async function load(key: string | null) {
    const list = await api().obdListSavedVehicles();
    setKeys(list);
    const use = key ?? await api().obdGetSavedVehicle() ?? list[0] ?? "";
    setSelected(use);
    if (!use) { setEcus([]); setChanges([]); return; }
    const [rows, ch] = await Promise.all([api().obdListSavedEcus(use), api().obdListChanges(use)]);
    setEcus(rows); setChanges(ch);
  }
  useEffect(() => { load(null).catch(() => {}); }, []);
  const order = new Map(capabilities.map((c) => [c.key, c.order]));
  const sorted = useMemo(() => [...ecus].sort((a, b) => {
    const ao = order.get(a.moduleKey) ?? 500;
    const bo = order.get(b.moduleKey) ?? 500;
    return ao - bo || a.moduleKey.localeCompare(b.moduleKey);
  }), [ecus, capabilities]);
  return (
    <section className="panel">
      <h2>车辆信息</h2>
      <p className="muted">这里是已保存的最近一次有效身份，与右上角实时通信状态无关。重复成功读取不会新增行。缺项保持上次有效值。软件或标定变化不是更换控制单元的证据。</p>
      <div className="row">
        <label>已保存车辆
          <select aria-label="已保存车辆" value={selected} onChange={(e) => {
            const v = e.target.value;
            api().obdSetSavedVehicle(v || null).then(() => load(v)).catch(() => {});
          }}>
            <option value="">（无）</option>
            {keys.map((k) => <option key={k} value={k}>{k.startsWith("unknown:") ? "尚未读到车架号的一次记录" : k}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => load(selected).catch(() => {})}>刷新</button>
      </div>
      {!sorted.length ? <p>尚无成功保存的模块身份。</p> : (
        <table className="obd-ecu-table">
          <thead><tr><th>模块</th><th>地址</th><th>硬件号</th><th>序列号</th><th>软件号</th><th>标定</th><th>校验号</th><th>设码指纹</th><th>最近成功</th><th>顺序</th></tr></thead>
          <tbody>
            {sorted.map((e) => (
              <tr key={`${e.moduleKey}-${e.ecuAddress ?? ""}`}>
                <td>{e.name}</td>
                <td>{e.ecuAddress ?? "未知（厂家对应未核实）"}</td>
                <td>{e.hardwareId ?? "未知"}</td>
                <td>{e.serial ?? "未知"}</td>
                <td>{e.softwareId ?? "未知"}</td>
                <td>{e.calibrationId ?? "未知"}</td>
                <td>{e.cvn ?? "未知"}</td>
                <td>{e.codingFingerprint ?? "未知"}</td>
                <td>{fmtTime(e.lastSuccessAt)}</td>
                <td>{order.has(e.moduleKey) ? String(order.get(e.moduleKey)) : "未核实"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>最近观察到的变化</h3>
      {!changes.length ? <p className="muted">尚无字段变化记录。</p> : (
        <ul data-obd="ecu-changes">
          {changes.map((c, i) => (
            <li key={i}>{fmtTime(c.observedAt)} · {eName(ecus, c.moduleKey)} · {FIELD_ZH[c.field] ?? c.field}：{c.previous ?? "（空）"} → {c.next ?? "（空）"}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function eName(ecus: EcuIdentity[], key: string) {
  return ecus.find((e) => e.moduleKey === key)?.name ?? "模块";
}

function DevManualLookup({ onLocate }: { onLocate?: (focus: LocatorFocus) => void }) {
  const [codeInput, setCodeInput] = useState("");
  const [active, setActive] = useState<DtcEntry | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [locatorMap, setLocatorMap] = useState<LocatorMap | null>(null);
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [sessions, setSessions] = useState<ObdSession[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [sessionDtcs, setSessionDtcs] = useState<ObdDtc[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = () => Promise.all([api().listParts(), api().getVehicle(), api().locatorMap(), api().listObdSessions()])
    .then(([p, v, map, sess]) => { setParts(p); setVehicle(v); setLocatorMap(map); setSessions(sess); });
  useEffect(() => { refresh().catch((e) => setError(String(e))); }, []);
  useEffect(() => {
    if (expandedId == null) { setSessionDtcs([]); return; }
    api().listObdDtcs(expandedId).then(setSessionDtcs).catch((e) => setError(String(e)));
  }, [expandedId]);
  const normalized = codeInput.trim().toUpperCase();
  async function lookup() {
    setError(null);
    if (!normalized) { setActive(null); return; }
    const hit = await api().getDtc(normalized);
    if (hit) { setActive(hit); return; }
    const matches = await api().searchDtc(normalized);
    setActive(matches[0] ?? null);
    if (!matches.length) setError(`码库无 ${normalized}；仍可记入会话作手工记录`);
  }
  const related = active?.related_part_sku ? parts.find((p) => p.sku === active.related_part_sku) ?? null : null;
  const locateTarget = active?.related_part_sku && locatorMap
    ? resolveSkuToLocator(active.related_part_sku, parts, locatorMap.zones) : null;
  return (
    <section className="panel">
      <h2>手工查码与记录</h2>
      <p className="muted">仅开发与验证使用。主故障码页才是实车读取。</p>
      {error ? <p className="error">{error}</p> : null}
      <div className="row">
        <label>故障码<input value={codeInput} onChange={(e) => setCodeInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && lookup()} placeholder="P0300" /></label>
        <button type="button" onClick={() => lookup()}>查询</button>
      </div>
      {active && (
        <div className="callout" style={{ marginTop: 12 }}>
          <h3>{active.code} — {active.title_zh}</h3>
          <p>{active.likely_causes}</p>
          <p>{active.checks}</p>
          {(related || active.related_part_sku) && (
            <button type="button" className="ghost" disabled={!locateTarget || !onLocate} onClick={() => {
              if (!locateTarget || !onLocate) return;
              onLocate({ zoneId: locateTarget.zoneId, hotspotId: locateTarget.hotspotId, sku: locateTarget.sku });
            }}>定位</button>
          )}
        </div>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <label>会话备注<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <button type="button" disabled={busy} onClick={async () => {
          if (!normalized) { setError("请输入故障码"); return; }
          setBusy(true);
          try {
            const session = await api().createObdSession({ odometer_km: vehicle?.current_km ?? null, note: note.trim() || (active ? null : `手工码 ${normalized}`) });
            await api().addObdDtc({ session_id: session.id, code: normalized, status: active ? "manual_kb" : "manual_unknown" });
            setNote(""); await refresh(); setExpandedId(session.id);
          } catch (e) { setError(String(e)); }
          finally { setBusy(false); }
        }}>记入会话</button>
      </div>
      {sessions.length > 0 && (
        <ul className="plain-list">
          {sessions.slice(0, 5).map((s) => (
            <li key={s.id}>
              <button type="button" className="ghost" onClick={() => setExpandedId((p) => p === s.id ? null : s.id)}>
                #{s.id} · {s.started_at.slice(0, 16)}{s.note ? ` · ${s.note}` : ""}
              </button>
              {expandedId === s.id && sessionDtcs.map((d) => <div key={d.id}>{d.code} · {d.status}</div>)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
