import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import {
  ANALYSIS_BATCH_LIMIT,
  ANALYSIS_STALE_MS,
  getAnalysisCatalog,
  headerFresh,
  type AnalysisItem,
  type AnalysisSelection,
  type AnalysisValue,
  type FaultDataPlan,
  type ScanSnapshot,
} from "@porsche981/domain";
import { api, type ObdLiveSnapshot } from "../api";

const KIND: Record<AnalysisItem["kind"], string> = {
  continuous: "连续",
  once: "一次",
  manual: "人工",
  unverified: "待核实",
};

function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function catalogModules(scan: ScanSnapshot | null) {
  return (scan?.modules ?? [])
    .filter((m) => /^obd-can:7E[8-F]$/.test(m.moduleKey))
    .map((m) => ({ moduleKey: m.moduleKey, name: m.name }));
}

function fmtReading(v: number | string | null): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) return "—";
    const abs = Math.abs(v);
    const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
    return String(Number(v.toFixed(digits)));
  }
  return String(v);
}

export function displayStatus(opts: {
  running: boolean;
  live: ObdLiveSnapshot;
  value: AnalysisValue | undefined;
  now: number;
  inFlight: boolean;
}): { label: string; historical: boolean } {
  const { running, live, value, now, inFlight } = opts;
  const headerOk = headerFresh(live.header, now, ANALYSIS_STALE_MS);
  if (!value) {
    if (running && inFlight) return { label: "读取中", historical: false };
    return { label: running ? "等待本批" : "未读取", historical: false };
  }
  if (value.status === "unsupported") return { label: "未支持", historical: false };
  if (value.status === "failed") return { label: "读取失败", historical: value.value != null };
  const age = value.observedAt ? now - Date.parse(value.observedAt) : Number.NaN;
  const tsBad = !Number.isFinite(age) || age < 0 || age > ANALYSIS_STALE_MS;
  const linkDead = !live.adapterConnected || value.status === "disconnected";
  const notLive = !headerOk || !live.header.vehicleCommunicating || tsBad || linkDead;
  if (linkDead) return { label: live.adapterConnected ? "已过期" : "已过期 · 未连接", historical: true };
  if (!running) return { label: notLive ? "已暂停 · 历史值" : "已暂停", historical: true };
  if (notLive) return { label: inFlight ? "读取中 · 上次已过期" : "已过期", historical: true };
  if (value.status === "valid") return { label: "有效", historical: false };
  return { label: value.status, historical: true };
}

export type AnalysisJump = { token: number; plan: FaultDataPlan; autoStart: boolean; holdReason: string | null };

export function ObdDataAnalysis(props: {
  active: boolean;
  live: ObdLiveSnapshot;
  scan: ScanSnapshot | null;
  now: number;
  jump: AnalysisJump | null;
  analysisBusyRef: MutableRefObject<boolean>;
  onRunningChange: (on: boolean) => void;
}) {
  const { active, live, scan, now, jump, analysisBusyRef, onRunningChange } = props;
  const [query, setQuery] = useState("");
  const [openMods, setOpenMods] = useState<Record<string, boolean>>({});
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [values, setValues] = useState<Record<string, AnalysisValue>>({});
  const [running, setRunning] = useState(false);
  const [inFlight, setInFlight] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastBatch, setLastBatch] = useState<{ elapsedMs: number; count: number } | null>(null);
  const [focused, setFocused] = useState<AnalysisItem | null>(null);
  const wantRunRef = useRef(false);
  const pumpingRef = useRef(false);
  const flightRef = useRef<Promise<unknown> | null>(null);
  const seqRef = useRef(0);
  const pickedRef = useRef(picked);
  const catalogRef = useRef<AnalysisItem[]>([]);
  const onHoldRef = useRef(onRunningChange);
  const busyRef = analysisBusyRef;
  pickedRef.current = picked;
  onHoldRef.current = onRunningChange;

  const catalog = useMemo(() => getAnalysisCatalog(catalogModules(scan)), [scan]);
  catalogRef.current = catalog;

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const map = new Map<string, AnalysisItem[]>();
    for (const item of catalog) {
      if (q && !`${item.moduleName} ${item.name} ${item.parameterId}`.toLowerCase().includes(q)) continue;
      const list = map.get(item.moduleKey) ?? [];
      list.push(item);
      map.set(item.moduleKey, list);
    }
    return [...map.entries()].map(([moduleKey, items]) => ({
      moduleKey, name: items[0]?.moduleName ?? moduleKey, items,
    }));
  }, [catalog, query]);

  const selectedItems = catalog.filter((i) => picked[i.key] && i.supported);
  const overcap = selectedItems.length > ANALYSIS_BATCH_LIMIT;
  const liveOk = headerFresh(live.header, now, ANALYSIS_STALE_MS) && !live.op && !inFlight;
  const canStart = selectedItems.length >= 1 && !overcap && liveOk && !running;

  function publishHold() {
    const hold = wantRunRef.current || flightRef.current != null;
    busyRef.current = flightRef.current != null;
    onHoldRef.current(hold);
  }

  async function pump() {
    if (pumpingRef.current) return;
    pumpingRef.current = true;
    try {
      while (wantRunRef.current) {
        if (flightRef.current) await flightRef.current;
        if (!wantRunRef.current) break;
        const sels: AnalysisSelection[] = catalogRef.current
          .filter((i) => pickedRef.current[i.key] && i.supported)
          .map((i) => ({ moduleKey: i.moduleKey, parameterId: i.parameterId }));
        if (!sels.length || sels.length > ANALYSIS_BATCH_LIMIT) {
          wantRunRef.current = false;
          setRunning(false);
          setError(sels.length > ANALYSIS_BATCH_LIMIT
            ? `已选 ${sels.length} 项，超过软件暂定上限 ${ANALYSIS_BATCH_LIMIT}，请先减少本批项目。完整需求仍列在下方，没有丢项。`
            : "请勾选 1 项及以上已支持数据");
          publishHold();
          break;
        }
        const seq = seqRef.current;
        const req = api().obdReadAnalysis(sels).finally(() => {
          if (flightRef.current === req) flightRef.current = null;
        });
        flightRef.current = req;
        setInFlight(true);
        publishHold();
        try {
          const batch = await req;
          if (seq !== seqRef.current) continue;
          setValues((prev) => {
            const next = { ...prev };
            for (const v of batch.values) next[v.key] = v;
            return next;
          });
          setLastBatch({ elapsedMs: batch.elapsedMs, count: batch.values.length });
          setError(null);
          if (!batch.values.some(v => v.status === 'valid')) {
            wantRunRef.current = false;
            setRunning(false);
            setError('本批没有可用读数，已暂停。请查看各项状态后调整选择。');
          }
        } catch (e) {
          if (seq !== seqRef.current) continue;
          if (String(e).includes("obd_busy")) { await delay(500); continue; }
          wantRunRef.current = false;
          setRunning(false);
          setError(String(e));
          publishHold();
          break;
        } finally {
          setInFlight(false);
          publishHold();
        }
        if (!wantRunRef.current) break;
        await delay(500);
      }
    } finally {
      pumpingRef.current = false;
      publishHold();
    }
  }

  function start() {
    setError(null);
    wantRunRef.current = true;
    setRunning(true);
    publishHold();
    void pump();
  }

  function pause() {
    wantRunRef.current = false;
    seqRef.current += 1;
    setRunning(false);
    publishHold();
  }

  useEffect(() => {
    if (!jump) return;
    const next: Record<string, boolean> = {};
    for (const item of jump.plan.items) {
      if (item.supported) next[item.key] = true;
    }
    setPicked(next);
    pickedRef.current = next;
    setError(null);
    seqRef.current += 1;
    if (jump.autoStart) start();
    else {
      wantRunRef.current = false;
      setRunning(false);
      publishHold();
    }
  }, [jump?.token]);

  useEffect(() => {
    if (!active) pause();
  }, [active]);

  useEffect(() => {
    if (!live.adapterConnected) pause();
  }, [live.adapterConnected]);

  useEffect(() => () => {
    wantRunRef.current = false;
    seqRef.current += 1;
    const pending = flightRef.current;
    if (pending) {
      void pending.catch(() => {}).finally(() => {
        busyRef.current = false;
        onHoldRef.current(false);
      });
    } else {
      busyRef.current = false;
      onHoldRef.current(false);
    }
  }, []);

  function toggle(item: AnalysisItem) {
    if (!item.supported) return;
    setPicked((prev) => {
      const on = !prev[item.key];
      if (on) {
        const n = catalog.filter((i) => (i.key === item.key ? true : prev[i.key]) && i.supported).length;
        if (n > ANALYSIS_BATCH_LIMIT) return prev;
      }
      return { ...prev, [item.key]: on };
    });
  }

  const missing = jump?.plan
    ? [
        ...jump.plan.gaps,
        ...jump.plan.manualChecks,
        ...jump.plan.once,
        ...jump.plan.items.filter((i) => !i.supported).map((i) => `${i.name}（${i.moduleName}）：${i.reason ?? "当前不能采集"}`),
      ]
    : [];

  return (
    <section className="panel obd-analysis" data-obd="analysis-page">
      <h2>数据分析</h2>
      <p className="muted">只显示实际读回的值。没有手册判断阈值时不自行推断，也不给出维修结论。采集上限 {ANALYSIS_BATCH_LIMIT} 项是软件暂定，不是适配器硬件上限。</p>
      {jump?.holdReason ? <p data-obd="analysis-hold">{jump.holdReason}</p> : null}
      {missing.length ? (
        <div className="obd-analysis-missing" data-obd="analysis-gaps">
          <h3>完整需求与缺口</h3>
          <ul>{missing.map((g) => <li key={g}>{g}</li>)}</ul>
        </div>
      ) : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      <p data-obd="analysis-count">已选 {selectedItems.length} / {ANALYSIS_BATCH_LIMIT}</p>
      {lastBatch ? <p data-obd="analysis-refresh">本批 {lastBatch.count} 项 · 耗时 {lastBatch.elapsedMs} ms</p> : null}
      {overcap ? <p data-obd="analysis-overcap">本批超过上限，未丢弃任何需求项；请先取消部分勾选后再开始。</p> : null}
      <div className="row">
        <button type="button" data-obd="analysis-start" disabled={!canStart} onClick={start}>开始采集</button>
        <button type="button" data-obd="analysis-pause" disabled={!running} onClick={pause}>暂停</button>
      </div>
      <div className="obd-analysis-split">
        <div className="obd-analysis-left" data-obd="analysis-catalog">
          <label>搜索<input value={query} onChange={(e) => setQuery(e.target.value)} /></label>
          <p className="muted">勾选采集；点击名称查看详情。支持情况以本车应答为准。</p>
          {groups.map((g) => (
            <article key={g.moduleKey} className="obd-analysis-mod">
              <button type="button" className="ghost" aria-expanded={openMods[g.moduleKey] !== false} onClick={() => setOpenMods((p) => ({ ...p, [g.moduleKey]: p[g.moduleKey] === false }))}>
                {g.name}
              </button>
              {openMods[g.moduleKey] === false ? null : (
                <ul className="plain-list">
                  {g.items.map((item) => {
                    const n = selectedItems.length;
                    const blocked = item.supported && !picked[item.key] && n >= ANALYSIS_BATCH_LIMIT;
                    return (
                      <li key={item.key} data-analysis-key={item.key}>
                        <div className="obd-analysis-item-line">
                          <input type="checkbox" aria-label={`采集 ${item.name} · ${item.moduleName}`} disabled={!item.supported || blocked} checked={Boolean(picked[item.key])} onChange={() => toggle(item)} />
                          <button type="button" className="obd-analysis-item-name" onClick={() => setFocused(item)}>{item.name}</button>
                          <small className="muted">{item.supported ? item.unit : KIND[item.kind] === '连续' ? '待支持' : KIND[item.kind]}</small>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </article>
          ))}
        </div>
        <div className="obd-analysis-right" data-obd="analysis-chosen">
          {focused ? <article className="obd-analysis-focus" data-obd="analysis-item-detail">
            <h3>{focused.name}</h3>
            <p>{focused.moduleName}{focused.unit ? ` · ${focused.unit}` : ''}</p>
            <p>{focused.reason ?? '已实现读取方法；接车后确认该单元是否支持。'}</p>
            <p className="muted">{focused.note}</p>
          </article> : null}
          <h3>已选项目</h3>
          {!selectedItems.length ? <p className="muted">从左侧勾选要采集的数据。</p> : (
            <ul className="plain-list">
              {selectedItems.map((item) => {
                const v = values[item.key];
                const st = displayStatus({ running, live, value: v, now, inFlight });
                const assoc = jump?.plan.associations[item.key] ?? [];
                return (
                  <li key={item.key} data-obd="analysis-value" data-analysis-key={item.key} data-historical={st.historical ? "1" : "0"}>
                    <strong>{item.name}</strong> · {item.moduleName}
                    <div className="obd-analysis-reading">
                      <span>{fmtReading(v?.value ?? null)}</span>
                      {item.unit ? <small> {item.unit}</small> : null}
                    </div>
                    <p className="muted">
                      {st.label}
                      {v?.observedAt ? ` · ${new Date(v.observedAt).toLocaleString()}` : ""}
                    </p>
                    {v?.detail ? <p className="muted">{v.detail}</p> : null}
                    <p className="muted">{item.note}</p>
                    {assoc.length ? <p className="muted">关联故障：{assoc.join("；")}</p> : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
