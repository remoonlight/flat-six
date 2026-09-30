import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { resolveSkuToLocator, wiringSystemIdForModule, SOURCE_ZH, IDENTITY_ZH, type DiagSource, type IdentityKind, type GuideStepResult } from "@porsche981/domain";
import { api, type LocatorMap, type Part, type WiringIndex } from "../api";
import type { LocatorFocus } from "../locator-focus";

type CaseRow = {
  id: number;
  updatedAt: string;
  moduleKey: string;
  ecu: string;
  code: string;
  source: DiagSource;
  identityKind: IdentityKind;
  vehicleKey: string | null;
  snapshotId?: number | null;
  symptom?: string | null;
  sku?: string | null;
  ecuContext?: string;
  faultLogId: number | null;
  checklist: {
    disclaimer: string;
    evidenceGaps: string[];
    steps: Array<{
      id: string;
      kind: string;
      label: string;
      standardSix: boolean;
      supportedRead: boolean;
      parameterId?: string;
    }>;
  };
  stepResults: Array<{ stepId: string; result: string; note: string }>;
};

const RESULT_LABEL: Record<GuideStepResult, string> = {
  unchecked: "未检查",
  normal: "正常",
  abnormal: "异常",
  unable: "无法完成",
};

export type GuideSeed = {
  code?: string;
  moduleKey?: string;
  ecu?: string;
  ecuContext?: "dme" | "gateway" | "unknown";
  source?: string;
  identityKind?: string;
  vehicleKey?: string | null;
  snapshotId?: number;
  symptom?: string;
  checks?: string | null;
  sku?: string | null;
  observedCodes?: Array<{ code: string; moduleKey: string }>;
};
export function GuidedTroubleshootPanel({
  seed,
  vehicleKm,
  onLocate,
  onOpenEngine,
}: {
  seed: GuideSeed | null;
  vehicleKm: number;
  onLocate?: (focus: LocatorFocus) => void;
  onOpenEngine?: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cas, setCas] = useState<CaseRow | null>(null);
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [parts, setParts] = useState<Part[]>([]);
  const [locatorMap, setLocatorMap] = useState<LocatorMap | null>(null);
  const [wiring, setWiring] = useState<WiringIndex | null>(null);
  const [relatedSku, setRelatedSku] = useState<string | null>(null);
  const caseIdRef = useRef<number | null>(null);
  caseIdRef.current = cas?.id ?? null;
  const metaSeq = useRef(0);
  const logLock = useRef(false);

  const loadMeta = useCallback(async (row: CaseRow) => {
    const token = ++metaSeq.current;
    setRelatedSku(null);
    const [p, map, dtc, index] = await Promise.all([
      api().listParts(),
      api().locatorMap(),
      row.code && row.ecuContext === "dme" ? api().getDtc(row.code).catch(() => undefined) : Promise.resolve(undefined),
      api().wiringIndex().catch(() => null),
    ]);
    if (token !== metaSeq.current || caseIdRef.current !== row.id) return;
    setParts(p);
    setLocatorMap(map);
    setRelatedSku(dtc?.related_part_sku ?? row.sku ?? null);
    setWiring(index);
  }, []);

  function showCase(row: CaseRow) {
    caseIdRef.current = row.id;
    setCas(row);
    setSelected(null);
    setNote("");
    void loadMeta(row).catch((e) => setError(String(e)));
  }

  const refreshCases = useCallback(async () => {
    if (!api().obdDiag) return;
    try {
      const rows = (await api().obdDiag!({ op: "guide:list" })) as CaseRow[];
      setCases(rows || []);
    } catch {
      setCases([]);
    }
  }, []);

  useEffect(() => {
    void refreshCases();
  }, [refreshCases]);

  async function openCase() {
    setError(null);
    if (!seed?.code && !seed?.symptom) {
      setError("请先在故障码页指定代码和控制单元语境，或从拓扑中的故障码打开，或从症状知识库打开。");
      return;
    }
    if (!api().obdDiag) {
      setError("需要桌面端才能保存排障案。");
      return;
    }
    setLoading(true);
    try {
      const row = (await api().obdDiag!({
        op: "guide:create",
        value: seed.symptom && !seed.code
          ? { symptom: seed.symptom, checks: seed.checks, sku: seed.sku, source: "manual", identityKind: "unknown" }
          : {
            code: seed.code,
            moduleKey: seed.moduleKey,
            ecu: seed.ecu,
            ecuContext: seed.ecuContext,
            observedCodes: seed.observedCodes,
            source: seed.source || "manual",
            identityKind: seed.identityKind || "unknown",
            vehicleKey: seed.vehicleKey,
            snapshotId: seed.snapshotId,
          },
      })) as CaseRow;
      showCase(row);
      await refreshCases();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  const seedKey = JSON.stringify(seed);
  const lastSeedKey = useRef(seedKey);
  useEffect(() => {
    if (lastSeedKey.current === seedKey) return;
    lastSeedKey.current = seedKey;
    setCas(null);
    caseIdRef.current = null;
    ++metaSeq.current;
    setSelected(null);
    setNote("");
  }, [seedKey]);

  const locateTarget = useMemo(() => {
    if (!relatedSku || !locatorMap) return null;
    return resolveSkuToLocator(relatedSku, parts, locatorMap.zones);
  }, [relatedSku, locatorMap, parts]);

  const wiringHit = useMemo(() => {
    const id = cas?.moduleKey ? wiringSystemIdForModule(cas.moduleKey) : null;
    if (!id || !wiring) return null;
    return wiring.systems.find((s) => s.id === id) || null;
  }, [cas?.moduleKey, wiring]);

  const resultMap = useMemo(() => {
    const m = new Map<string, { result: string; note: string }>();
    for (const s of cas?.stepResults || []) m.set(s.stepId, s);
    return m;
  }, [cas]);

  const done = cas ? cas.checklist.steps.filter((s) => (resultMap.get(s.id)?.result || "unchecked") !== "unchecked").length : 0;

  async function saveStep(result: GuideStepResult) {
    if (!cas || !selected || loading) return;
    const id = cas.id;
    setError(null);
    setLoading(true);
    try {
      const row = (await api().obdDiag!({
        op: "guide:setStep",
        value: { caseId: id, stepId: selected, result, note, updatedAt: cas.updatedAt },
      })) as CaseRow;
      if (caseIdRef.current !== id) return;
      setCas(row);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function linkLog() {
    if (!cas || logLock.current) return;
    if (cas.faultLogId) return;
    logLock.current = true;
    setLoading(true);
    const id = cas.id;
    setError(null);
    try {
      const log = await api().addFaultLog({
        logged_at: new Date().toISOString().slice(0, 10),
        odometer_km: vehicleKm,
        symptom: `${cas.symptom || cas.code} · ${cas.moduleKey}`,
        action: "引导排障案（未给出零件结论）",
        result: `关联排障案 #${cas.id}`,
        related_part_sku: relatedSku,
      });
      const row = (await api().obdDiag!({ op: "guide:linkFaultLog", value: { caseId: id, faultLogId: log.id } })) as CaseRow;
      if (caseIdRef.current !== id) return;
      setCas(row);
    } catch (e) {
      setError(String(e));
    } finally {
      logLock.current = false;
      setLoading(false);
    }
  }

  return (
    <section className="panel" data-testid="obd-guide">
      <h2>引导排障</h2>
      <p className="muted">
        按现有知识列出检查项，并标明补充观察、人工检查与未核实缺口。不是完整工厂步骤。不会从本页自动读取发动机数据。
      </p>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {loading ? <p className="muted">正在打开…</p> : null}
      <div className="row">
        <button type="button" disabled={loading} data-testid="obd-guide-open" onClick={() => void openCase()}>
          {seed ? seed.symptom ? `打开：${seed.symptom}` : `打开 ${seed.code}（${seed.moduleKey}）` : "打开排障案"}
        </button>
      </div>
      {!cas && !loading ? (
        <div data-testid="obd-guide-empty">
          <p className="muted">还没有打开的排障案。请从故障码查询或拓扑故障码进入，或选择已保存的案。</p>
          {cases.length > 0 ? (
            <ul className="plain-list">
              {cases.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    data-testid="obd-guide-resume"
                    onClick={() => showCase(c)}
                  >
                    恢复 #{c.id} {c.symptom || c.code} · {c.moduleKey}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {cas ? (
        <div>
          <p>{cas.checklist.disclaimer}</p>
          <p className="muted">{SOURCE_ZH[cas.source]} · {IDENTITY_ZH[cas.identityKind]}{cas.snapshotId ? ` · 快照 #${cas.snapshotId}` : ""}</p>
          <p className="muted" data-testid="obd-guide-progress">
            进度 {done}/{cas.checklist.steps.length} · 案 #{cas.id}
            {cas.faultLogId ? ` · 已关联台账 #${cas.faultLogId}` : ""}
          </p>
          <ol data-testid="obd-guide-steps">
            {cas.checklist.steps.map((s) => {
              const r = (resultMap.get(s.id)?.result || "unchecked") as GuideStepResult;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    className={selected === s.id ? "chip active" : "chip"}
                    data-testid="obd-guide-step"
                    data-selected={selected === s.id ? "1" : "0"}
                    onClick={() => {
                      setSelected(s.id);
                      setNote(resultMap.get(s.id)?.note || "");
                    }}
                  >
                    {RESULT_LABEL[r] || r} · {s.label}
                  </button>
                </li>
              );
            })}
          </ol>
          {selected ? (
            <div className="row" style={{ marginTop: 8 }}>
              <label>
                备注
                <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="检查备注" />
              </label>
              {(Object.keys(RESULT_LABEL) as GuideStepResult[]).map((r) => (
                <button key={r} type="button" data-testid={`obd-guide-result-${r}`} disabled={loading} onClick={() => void saveStep(r)}>
                  {RESULT_LABEL[r]}
                </button>
              ))}
            </div>
          ) : null}
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" disabled={loading || !!cas.faultLogId} data-testid="obd-guide-fault-log" onClick={() => void linkLog()}>
              记入故障台账（不写结论）
            </button>
            <button
              type="button"
              className="ghost"
              disabled={!locateTarget || !onLocate}
              onClick={() => locateTarget && onLocate?.({ zoneId: locateTarget.zoneId, hotspotId: locateTarget.hotspotId, sku: locateTarget.sku })}
            >
              定位相关零件
            </button>
            <button
              type="button"
              className="ghost"
              data-testid="obd-guide-engine"
              disabled={!cas.checklist.steps.some((s) => s.standardSix && s.supportedRead)}
              onClick={() => onOpenEngine?.()}
            >
              打开发动机数据页（不自动读取）
            </button>
            {wiringHit ? (
              <button
                type="button"
                className="ghost"
                data-testid="obd-guide-wiring"
                onClick={() => void api().openWiringPdf("main").catch((e) => setError(String(e)))}
              >
                打开线束参考：{wiringHit.label_zh}
              </button>
            ) : (
              <span className="muted">无线束映射（仅已知 DME/网关档案）</span>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
