import { useCallback, useEffect, useState } from "react";
import { parseIntervalKind } from "@porsche981/domain";
import {
  api,
  type Part,
  type ServiceRecord,
  type Vehicle,
  type IntervalResult,
} from "../api";

const INTERVAL_STATUS_LABEL: Record<IntervalResult["status"], string> = {
  ok: "正常",
  dueSoon: "即将到期",
  overdue: "已过期",
  no_baseline: "需登记首次更换",
};

/** 到期/登记更换不列：旧左前胎、燃油滤、以及与消耗品重复的空调过滤器。 */
const HIDDEN_MAINT_SKUS = new Set([
  "tire-fl",
  "fuel-filter",
  "cabin-filter",
]);

function isMaintDuePart(p: Part): boolean {
  if (HIDDEN_MAINT_SKUS.has(p.sku)) return false;
  return p.interval_km != null || p.interval_months != null;
}

/** Soft community reminders: never show red overdue / amber dueSoon scare pills. */
function intervalDisplay(
  part: Part,
  iv: IntervalResult,
): { className: string; label: string } {
  const soft = parseIntervalKind(part.notes) === "soft";
  if (soft && (iv.status === "overdue" || iv.status === "dueSoon")) {
    return { className: "soft", label: "软提醒/按磨损" };
  }
  if (soft && iv.status === "ok") {
    return { className: "soft", label: "软提醒/按磨损" };
  }
  return {
    className: iv.status,
    label: INTERVAL_STATUS_LABEL[iv.status],
  };
}

export function GaragePage() {
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [kmInput, setKmInput] = useState("");
  const [avgInput, setAvgInput] = useState("");
  const [vinInput, setVinInput] = useState("");
  const [parts, setParts] = useState<Part[]>([]);
  const [records, setRecords] = useState<ServiceRecord[]>([]);
  const [intervals, setIntervals] = useState<Record<number, IntervalResult | null>>({});
  const [error, setError] = useState<string | null>(null);

  const [svcPartId, setSvcPartId] = useState<string>("");
  const [svcDate, setSvcDate] = useState(new Date().toISOString().slice(0, 10));
  const [svcKm, setSvcKm] = useState("");
  const [svcBrand, setSvcBrand] = useState("");
  const [svcCost, setSvcCost] = useState("");
  const [svcNotes, setSvcNotes] = useState("");

  const refresh = useCallback(async () => {
    const v = await api().getVehicle();
    setVehicle(v);
    setKmInput(String(v.current_km));
    setAvgInput(v.avg_km_per_day == null ? "" : String(v.avg_km_per_day));
    setVinInput(v.vin ?? "");
    setSvcKm(String(v.current_km));
    const p = await api().listParts();
    setParts(p);
    setRecords(await api().listService());
    const map: Record<number, IntervalResult | null> = {};
    for (const part of p) {
      if (isMaintDuePart(part)) {
        map[part.id] = await api().partInterval(part.id);
      }
    }
    setIntervals(map);
  }, []);

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  async function saveMileage() {
    setError(null);
    try {
      await api().setMileage(Number(kmInput));
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveAvg() {
    await api().setAvgKmPerDay(avgInput === "" ? null : Number(avgInput));
    await refresh();
  }

  async function saveVin() {
    await api().setVin(vinInput.trim() || null);
    await refresh();
  }

  async function addService() {
    setError(null);
    try {
      const partId = svcPartId ? Number(svcPartId) : null;
      const part = parts.find((p) => p.id === partId);
      if (!part) {
        setError("请选择零件");
        return;
      }
      await api().addService({
        part_id: partId,
        title: part.name_zh,
        replaced_at: svcDate,
        odometer_km: Number(svcKm),
        brand: svcBrand || null,
        cost: svcCost === "" ? null : Number(svcCost),
        notes: svcNotes.trim() || null,
      });
      setSvcNotes("");
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeService(id: number) {
    if (!window.confirm("删除这条服务记录？")) return;
    setError(null);
    try {
      await api().removeService(id);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  if (!vehicle) {
    return <p className="muted">加载中…</p>;
  }

  const intervalParts = parts.filter((p) => intervals[p.id]);
  const servicePartChoices = parts.filter(isMaintDuePart);

  return (
    <div className="garage-maint">
      <h1>零件维护状态</h1>
      {error && <p className="error">{error}</p>}

      <div className="panel">
        <h2>当前公里</h2>
        <div className="row">
          <label>
            公里数
            <input value={kmInput} onChange={(e) => setKmInput(e.target.value)} />
          </label>
          <button className="primary" type="button" onClick={saveMileage}>
            更新公里
          </button>
          <label>
            日均公里（用于推算到期日）
            <input value={avgInput} onChange={(e) => setAvgInput(e.target.value)} />
          </label>
          <button className="ghost" type="button" onClick={saveAvg}>
            保存日均
          </button>
          <label>
            VIN（可选）
            <input value={vinInput} onChange={(e) => setVinInput(e.target.value)} />
          </label>
          <button className="ghost" type="button" onClick={saveVin}>
            保存 VIN
          </button>
        </div>
      </div>

      <div className="panel">
        <h2>登记更换</h2>
        <div className="row">
          <label>
            零件
            <select value={svcPartId} onChange={(e) => setSvcPartId(e.target.value)}>
              <option value="">请选择零件</option>
              {servicePartChoices.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name_zh}
                </option>
              ))}
            </select>
          </label>
          <label>
            日期
            <input type="date" value={svcDate} onChange={(e) => setSvcDate(e.target.value)} />
          </label>
          <label>
            当时公里
            <input value={svcKm} onChange={(e) => setSvcKm(e.target.value)} />
          </label>
          <label>
            品牌
            <input value={svcBrand} onChange={(e) => setSvcBrand(e.target.value)} />
          </label>
          <label>
            费用
            <input value={svcCost} onChange={(e) => setSvcCost(e.target.value)} />
          </label>
          <label>
            备注
            <input value={svcNotes} onChange={(e) => setSvcNotes(e.target.value)} />
          </label>
          <button className="primary" type="button" onClick={addService}>
            保存记录
          </button>
        </div>
      </div>

      <div className="panel">
        <h2>到期状态</h2>
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>零件</th>
              <th>下次公里</th>
              <th>剩余公里</th>
              <th>建议日期</th>
              <th>剩余天</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {intervalParts.map((p) => {
              const iv = intervals[p.id]!;
              const display = intervalDisplay(p, iv);
              return (
                <tr key={p.id}>
                  <td>{p.name_zh}</td>
                  <td>{iv.nextDueKm ?? "—"}</td>
                  <td>{iv.remainingKm ?? "—"}</td>
                  <td>{iv.nextDueDate ?? "—"}</td>
                  <td>{iv.remainingDays ?? "—"}</td>
                  <td>
                    <span className={`pill ${display.className}`}>
                      {display.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
        {intervalParts.length === 0 && (
          <p className="muted">尚无带间隔的零件。请先导入/种子间隔数据。</p>
        )}
      </div>

      <div className="panel">
        <h2>服务历史</h2>
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>零件</th>
              <th>公里</th>
              <th>品牌</th>
              <th>费用</th>
              <th>备注</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.id}>
                <td>{r.replaced_at}</td>
                <td>
                  {r.part_id != null
                    ? (parts.find((p) => p.id === r.part_id)?.name_zh ?? r.title)
                    : r.title || "—"}
                </td>
                <td>{r.odometer_km}</td>
                <td>{r.brand ?? "—"}</td>
                <td>{r.cost ?? "—"}</td>
                <td>{r.notes ?? "—"}</td>
                <td>
                  <button
                    className="ghost"
                    type="button"
                    onClick={() => void removeService(r.id)}
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  );
}
