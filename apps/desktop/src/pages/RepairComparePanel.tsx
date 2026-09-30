import { useCallback, useEffect, useRef, useState } from "react";
import {
  COVERAGE_ZH,
  formatCompareReport,
  IDENTITY_ZH,
  SOURCE_ZH,
  type DiagSource,
  type IdentityKind,
  type ModuleCoverage,
} from "@porsche981/domain";
import { api } from "../api";

type Snap = {
  id: number;
  at: string;
  source: DiagSource;
  identityKind: IdentityKind;
  identityLabel: string;
  completeness: string;
  vehicleKey: string | null;
  modules: Array<{ moduleKey: string; coverage: ModuleCoverage; dtcs: unknown[] }>;
};

type Preview = {
  ok: boolean;
  error?: string;
  reason?: string;
  rows?: Array<{ kind: string; moduleKey: string; code: string; dtcHex?: string; subtype: string | null; note: string; beforeStatus: string | null; afterStatus: string | null }>;
  disclaimer?: string;
  context?: string;
  beforeId?: number;
  afterId?: number;
};

type Report = { id: number; beforeId: number; afterId: number; note: string; faultLogId: number | null; result: Preview; createdAt?: string };

const KIND_ZH: Record<string, string> = {
  new: "新出现",
  still: "仍存在",
  gone: "未再观察到",
  "status-changed": "状态变化",
  "not-comparable": "不可比",
};

export function RepairComparePanel({ vehicleKm }: { vehicleKm: number }) {
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [list, setList] = useState<Snap[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [legacy, setLegacy] = useState<Array<{ id: number; at: string; source: string; vehicleKey: string }>>([]);
  const [beforeId, setBeforeId] = useState<number | "">("");
  const [afterId, setAfterId] = useState<number | "">("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [note, setNote] = useState("");
  const [bindNote, setBindNote] = useState("声明绑定本车库 2014 Boxster S（981），非实测 VIN");
  const [savedId, setSavedId] = useState<number | null>(null);
  const seq = useRef(0);

  const refresh = useCallback(async () => {
    if (!api().obdDiag) {
      setList([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setList(((await api().obdDiag!({ op: "snapshot:list" })) as Snap[]) || []);
      setLegacy(((await api().obdDiag!({ op: "scan:listLegacy" })) as Array<{ id: number; at: string; source: string; vehicleKey: string }>) || []);
      try {
        setReports(((await api().obdDiag!({ op: "compare:list" })) as Report[]) || []);
      } catch {
        setReports([]);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  function invalidate() {
    ++seq.current;
    setPreview(null);
    setSavedId(null);
    setInfo(null);
    setError(null);
  }

  async function runPreview() {
    setError(null);
    setInfo(null);
    setPreview(null);
    if (!beforeId || !afterId) {
      setError("请选择维修前和维修后两条记录");
      return;
    }
    const token = ++seq.current;
    setPending(true);
    try {
      const r = (await api().obdDiag!({ op: "compare:preview", value: { beforeId, afterId } })) as Preview;
      if (token !== seq.current) return;
      setPreview(r);
      if (!r.ok) setError(r.error || "无法对比");
    } catch (e) {
      if (token !== seq.current) return;
      setError(String(e));
    } finally {
      if (token === seq.current) setPending(false);
    }
  }

  async function assign(id: number) {
    invalidate();
    setError(null);
    setPending(true);
    try {
      await api().obdDiag!({ op: "snapshot:assign", value: { id, note: bindNote } });
      setInfo(`已将 #${id} 用户声明绑定到本车库车辆`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }

  async function save() {
    if (!preview?.ok || pending) return;
    const token = ++seq.current;
    setPending(true);
    try {
      const row = (await api().obdDiag!({ op: "compare:save", value: { beforeId, afterId, note } })) as Report;
      if (token !== seq.current) return;
      setSavedId(row.id);
      setInfo(`已保存对比 #${row.id}`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }

  async function linkLog() {
    if (!preview?.ok || !savedId || pending) return;
    setPending(true);
    try {
      const existing = reports.find((r) => r.id === savedId);
      if (existing?.faultLogId) {
        setInfo(`已关联台账 #${existing.faultLogId}`);
        return;
      }
      const log = await api().addFaultLog({
        logged_at: new Date().toISOString().slice(0, 10),
        odometer_km: vehicleKm,
        symptom: `诊断对比 #${beforeId} → #${afterId}`,
        action: note || "维修前后观察对比",
        result: "仅记录两次观察，不宣称已修好",
      });
      await api().obdDiag!({ op: "compare:save", value: { beforeId, afterId, note, faultLogId: log.id } });
      setInfo(`已记入台账 #${log.id}`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }

  function copyReport() {
    if (!preview?.ok || !preview.rows || !preview.disclaimer) return;
    const text = formatCompareReport(
      {
        ok: true,
        beforeId: Number(beforeId),
        afterId: Number(afterId),
        rows: preview.rows.map((r) => ({
          kind: r.kind as never,
          moduleKey: r.moduleKey,
          code: r.code,
          dtcHex: r.dtcHex,
          subtype: r.subtype,
          beforeStatus: r.beforeStatus,
          afterStatus: r.afterStatus,
          note: r.note,
        })),
        disclaimer: preview.disclaimer,
        context: preview.context,
      },
      note,
    );
    void navigator.clipboard?.writeText(text);
  }

  return (
    <section className="panel" data-testid="obd-compare">
      <h2>维修前后对比</h2>
      <p className="muted">
        从系统拓扑或只读采集完成后保存的观察记录中选择两条。未绑定记录必须先用户声明绑定本车库车辆（不是实测 VIN）。消失只表示两次观察之差。
      </p>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {info ? <p className="muted" data-testid="obd-compare-info">{info}</p> : null}
      {loading ? <p className="muted">加载记录…</p> : null}
      {!loading && list.length === 0 ? (
        <p className="muted" data-testid="obd-compare-empty">
          还没有可对比的诊断快照。请在「系统拓扑」做一次读取（完成后会自动保存）。不会向库中写入演示数据。
        </p>
      ) : null}
      {legacy.length > 0 ? (
        <p className="muted">另有 {legacy.length} 条来源不明的遗留扫描（不可混比）。</p>
      ) : null}
      <label>
        绑定说明（用户声明，非 VIN）
        <input data-testid="obd-bind-note" value={bindNote} onChange={(e) => setBindNote(e.target.value)} />
      </label>
      <div className="row">
        <label>
          维修前
          <select disabled={pending} data-testid="obd-compare-before" value={beforeId} onChange={(e) => { invalidate(); setBeforeId(e.target.value ? Number(e.target.value) : ""); }}>
            <option value="">选择</option>
            {list.map((s) => (
              <option key={s.id} value={s.id}>
                #{s.id} · {s.at.slice(0, 19)} · {SOURCE_ZH[s.source]} · {IDENTITY_ZH[s.identityKind]} · {s.completeness === "complete" ? "完整" : s.completeness === "failed" ? "失败" : "部分"}
              </option>
            ))}
          </select>
        </label>
        <label>
          维修后
          <select disabled={pending} data-testid="obd-compare-after" value={afterId} onChange={(e) => { invalidate(); setAfterId(e.target.value ? Number(e.target.value) : ""); }}>
            <option value="">选择</option>
            {list.map((s) => (
              <option key={`a-${s.id}`} value={s.id}>
                #{s.id} · {s.at.slice(0, 19)} · {SOURCE_ZH[s.source]} · {IDENTITY_ZH[s.identityKind]}
              </option>
            ))}
          </select>
        </label>
        <button type="button" data-testid="obd-compare-run" disabled={pending} onClick={() => void runPreview()}>
          对比
        </button>
        <button type="button" className="ghost" data-testid="obd-compare-bind-before" disabled={!beforeId || pending} onClick={() => beforeId && void assign(Number(beforeId))}>
          绑定维修前
        </button>
        <button type="button" className="ghost" data-testid="obd-compare-bind-after" disabled={!afterId || pending} onClick={() => afterId && void assign(Number(afterId))}>
          绑定维修后
        </button>
        <button type="button" className="ghost" onClick={() => void refresh()}>刷新列表</button>
      </div>
      {preview && !preview.ok ? (
        <div data-testid="obd-compare-result">{preview.error}</div>
      ) : null}
      {preview?.ok && preview.rows ? (
        <div data-testid="obd-compare-result">
          <p>{preview.disclaimer}</p>
          <p className="muted">{preview.context}</p>
          <ul className="plain-list">
            {preview.rows.map((r, i) => (
              <li key={i} data-kind={r.kind}>
                {KIND_ZH[r.kind] || r.kind} · {r.moduleKey} · {r.code}
                {r.dtcHex ? ` [${r.dtcHex}]` : ""}
                {r.subtype ? ` 子码 ${r.subtype}` : ""}
                {r.beforeStatus || r.afterStatus ? `（${r.beforeStatus || "—"} → ${r.afterStatus || "—"}）` : ""} · {r.note}
              </li>
            ))}
          </ul>
          <label>
            维修/操作备注
            <input disabled={pending} data-testid="obd-compare-note" value={note} onChange={(e) => { setNote(e.target.value); setSavedId(null); }} />
          </label>
          <div className="row">
            <button type="button" data-testid="obd-compare-save" disabled={pending} onClick={() => void save()}>保存对比</button>
            <button type="button" className="ghost" data-testid="obd-compare-fault-log" disabled={!savedId || pending} onClick={() => void linkLog()}>记入故障台账</button>
            <button type="button" className="ghost" data-testid="obd-compare-copy" onClick={copyReport}>复制报告</button>
            {savedId ? <span className="muted">已保存 #{savedId}</span> : null}
          </div>
        </div>
      ) : null}
      {reports.length > 0 ? (
        <div data-testid="obd-compare-saved">
          <h3>已保存对比</h3>
          <ul className="plain-list">
            {reports.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  data-testid="obd-compare-open-saved"
                  disabled={pending}
                  onClick={() => {
                    ++seq.current;
                    setBeforeId(r.beforeId);
                    setAfterId(r.afterId);
                    setNote(r.note || "");
                    setPreview(r.result);
                    setSavedId(r.id);
                  }}
                >
                  #{r.id} · {r.beforeId}→{r.afterId} · {r.note || "无备注"}
                  {r.faultLogId ? ` · 台账 #${r.faultLogId}` : ""}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
