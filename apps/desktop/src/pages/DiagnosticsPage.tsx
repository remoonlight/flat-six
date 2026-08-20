import { useCallback, useEffect, useMemo, useState } from "react";
import { resolveSkuToLocator } from "@porsche981/domain";
import {
  api,
  type CodingSnapshot,
  type FaultEntry,
  type FaultLog,
  type LocatorMap,
  type Part,
  type Vehicle,
} from "../api";
import type { LocatorFocus } from "../locator-focus";

export type DiagnosticsPageProps = {
  /** Jump to Locator tab with zone/hotspot/sku (R5). */
  onLocate?: (focus: LocatorFocus) => void;
};

function codingSnapLabel(s: CodingSnapshot): string {
  const date = s.recorded_at.slice(0, 10);
  const fn = s.sub_function
    ? `${s.function_name} / ${s.sub_function}`
    : s.function_name;
  return `#${s.id} · ${s.system} · ${fn} · ${date}`;
}

export function DiagnosticsPage({ onLocate }: DiagnosticsPageProps = {}) {
  const [faults, setFaults] = useState<FaultEntry[]>([]);
  const [parts, setParts] = useState<Part[]>([]);
  const [locatorMap, setLocatorMap] = useState<LocatorMap | null>(null);
  const [active, setActive] = useState<FaultEntry | null>(null);
  const [logs, setLogs] = useState<FaultLog[]>([]);
  const [codingSnaps, setCodingSnaps] = useState<CodingSnapshot[]>([]);
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [loggedAt, setLoggedAt] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [odometerKm, setOdometerKm] = useState("");
  const [symptom, setSymptom] = useState("");
  const [areaHypothesis, setAreaHypothesis] = useState("");
  const [action, setAction] = useState("");
  const [result, setResult] = useState("");
  const [relatedSku, setRelatedSku] = useState("");
  const [codingSnapshotId, setCodingSnapshotId] = useState("");

  const refreshLogs = useCallback(async () => {
    const [f, p, v, l, map, snaps] = await Promise.all([
      api().listFaults(),
      api().listParts(),
      api().getVehicle(),
      api().listFaultLogs(),
      api().locatorMap(),
      api().listCoding(),
    ]);
    setFaults(f);
    setParts(p);
    setVehicle(v);
    setLogs(l);
    setLocatorMap(map);
    setCodingSnaps(snaps);
    setActive((prev) => prev ?? f[0] ?? null);
    setOdometerKm((prev) => (prev === "" ? String(v.current_km) : prev));
  }, []);

  useEffect(() => {
    refreshLogs().catch((e) => setError(String(e)));
  }, [refreshLogs]);

  const related = useMemo(() => {
    if (!active?.related_part_sku) return null;
    return parts.find((p) => p.sku === active.related_part_sku) ?? null;
  }, [active, parts]);

  /** Enabled only when related_part_sku resolves onto a three-zone hotspot. */
  const locateTarget = useMemo(() => {
    if (!active?.related_part_sku || !locatorMap) return null;
    return resolveSkuToLocator(
      active.related_part_sku,
      parts,
      locatorMap.zones,
    );
  }, [active, parts, locatorMap]);

  const snapById = useMemo(() => {
    const m = new Map<number, CodingSnapshot>();
    for (const s of codingSnaps) m.set(s.id, s);
    return m;
  }, [codingSnaps]);

  async function createLog() {
    setError(null);
    if (!symptom.trim()) {
      setError("请填写症状");
      return;
    }
    let codingId: number | null = null;
    if (codingSnapshotId.trim() !== "") {
      const n = Number(codingSnapshotId);
      if (!Number.isInteger(n) || n <= 0 || !snapById.has(n)) {
        setError(`无效设码快照 ID：${codingSnapshotId}`);
        return;
      }
      codingId = n;
    }
    try {
      await api().addFaultLog({
        logged_at: loggedAt,
        odometer_km: Number(odometerKm),
        symptom: symptom.trim(),
        area_hypothesis: areaHypothesis.trim() || null,
        action: action.trim() || null,
        result: result.trim() || null,
        related_part_sku: relatedSku.trim() || null,
        coding_snapshot_id: codingId,
      });
      setSymptom("");
      setAreaHypothesis("");
      setAction("");
      setResult("");
      setRelatedSku("");
      setCodingSnapshotId("");
      if (vehicle) setOdometerKm(String(vehicle.current_km));
      await refreshLogs();
    } catch (e) {
      setError(String(e));
    }
  }

  async function closeLog(id: number) {
    setError(null);
    try {
      await api().closeFaultLog(id);
      await refreshLogs();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div>
      <h2>手工记录</h2>
      <p className="muted">
        上半为只读知识库；下半为长期跟踪记录（与知识库分表）。
      </p>
      {error && <p className="error">{error}</p>}

      <div
        className="panel"
        style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 16 }}
      >
        <div>
          <h2>症状知识库</h2>
          {faults.map((f) => (
            <div
              key={f.id}
              className={`list-item ${active?.id === f.id ? "active" : ""}`}
              onClick={() => setActive(f)}
            >
              {f.symptom}
            </div>
          ))}
        </div>
        <div>
          {active && (
            <>
              <h2>{active.symptom}</h2>
              <h2>可能原因</h2>
              <p>{active.likely_causes}</p>
              <h2>检查步骤</h2>
              <p>{active.checks}</p>
              {(related || active.related_part_sku) && (
                <div className="callout" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <span>
                    {related
                      ? `相关零件：${related.name_zh}（OEM ${related.oem_number ?? "—"}）· 锚点 ${related.locator_hotspot ?? "—"}`
                      : `相关 SKU：${active.related_part_sku}（零件未入库）`}
                  </span>
                  <button
                    type="button"
                    className="ghost"
                    disabled={!locateTarget || !onLocate}
                    title={
                      locateTarget
                        ? `跳到 ${locateTarget.zoneId} / ${locateTarget.hotspotId}`
                        : "该 SKU 锚点不在三区定位图上"
                    }
                    onClick={() => {
                      if (!locateTarget || !onLocate) return;
                      onLocate({
                        zoneId: locateTarget.zoneId,
                        hotspotId: locateTarget.hotspotId,
                        sku: locateTarget.sku,
                      });
                    }}
                  >
                    定位
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>长期跟踪</h2>
        <p className="muted">记录现场处置；关闭后仍保留，仅标记已结束。</p>
        <div className="row">
          <label>
            日期
            <input
              type="date"
              value={loggedAt}
              onChange={(e) => setLoggedAt(e.target.value)}
            />
          </label>
          <label>
            当时公里
            <input
              value={odometerKm}
              onChange={(e) => setOdometerKm(e.target.value)}
            />
          </label>
          <label>
            症状
            <input
              value={symptom}
              onChange={(e) => setSymptom(e.target.value)}
              placeholder="必填"
            />
          </label>
          <label>
            部位假设
            <input
              value={areaHypothesis}
              onChange={(e) => setAreaHypothesis(e.target.value)}
            />
          </label>
          <label>
            处理动作
            <input value={action} onChange={(e) => setAction(e.target.value)} />
          </label>
          <label>
            结果
            <input value={result} onChange={(e) => setResult(e.target.value)} />
          </label>
          <label>
            关联 SKU
            <input
              value={relatedSku}
              onChange={(e) => setRelatedSku(e.target.value)}
              placeholder="可选"
            />
          </label>
          <label>
            设码快照
            <select
              value={codingSnapshotId}
              onChange={(e) => setCodingSnapshotId(e.target.value)}
            >
              <option value="">不关联</option>
              {codingSnaps.map((s) => (
                <option key={s.id} value={String(s.id)}>
                  {codingSnapLabel(s)}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" type="button" onClick={createLog}>
            新建跟踪
          </button>
        </div>

        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>公里</th>
              <th>症状</th>
              <th>部位假设</th>
              <th>动作</th>
              <th>结果</th>
              <th>设码快照</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => {
              const snap =
                log.coding_snapshot_id != null
                  ? snapById.get(log.coding_snapshot_id)
                  : undefined;
              const snapCell = snap
                ? codingSnapLabel(snap)
                : log.coding_snapshot_id != null
                  ? `#${log.coding_snapshot_id}（已删）`
                  : "—";
              return (
                <tr key={log.id}>
                  <td>{log.logged_at}</td>
                  <td>{log.odometer_km}</td>
                  <td>{log.symptom}</td>
                  <td>{log.area_hypothesis ?? "—"}</td>
                  <td>{log.action ?? "—"}</td>
                  <td>{log.result ?? "—"}</td>
                  <td>{snapCell}</td>
                  <td>
                    <span className={`pill ${log.closed ? "ok" : "dueSoon"}`}>
                      {log.closed ? "已关闭" : "进行中"}
                    </span>
                  </td>
                  <td>
                    {!log.closed && (
                      <button
                        className="ghost"
                        type="button"
                        onClick={() => closeLog(log.id)}
                      >
                        关闭
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>        </table>
        {logs.length === 0 && (
          <p className="muted">尚无跟踪记录。可在上方表单新建。</p>
        )}
      </div>
    </div>
  );
}
