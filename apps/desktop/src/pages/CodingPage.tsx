import { useEffect, useMemo, useState } from "react";
import {
  api,
  type CodingMenu,
  type CodingSnapshot,
  type Vehicle,
} from "../api";

export function CodingPage() {
  const [menu, setMenu] = useState<CodingMenu | null>(null);
  const [snaps, setSnaps] = useState<CodingSnapshot[]>([]);
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [system, setSystem] = useState("");
  const [itemIdx, setItemIdx] = useState(0);
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [note, setNote] = useState("");

  async function refresh() {
    const [m, s, v] = await Promise.all([
      api().codingMenu(),
      api().listCoding(),
      api().getVehicle(),
    ]);
    setMenu(m);
    setSnaps(s);
    setVehicle(v);
    if (!system && m.systems[0]) setSystem(m.systems[0].system);
  }

  useEffect(() => {
    refresh();
  }, []);

  const sys = useMemo(
    () => menu?.systems.find((s) => s.system === system) ?? null,
    [menu, system],
  );
  const item = sys?.items[itemIdx] ?? null;

  async function saveSnap() {
    if (!item || !vehicle) return;
    await api().addCoding({
      system,
      function_name: item.function,
      sub_function: item.subFunction,
      before_value: before,
      after_value: after,
      note: note || null,
      odometer_km: vehicle.current_km,
      recorded_at: new Date().toISOString(),
    });
    setBefore("");
    setAfter("");
    setNote("");
    await refresh();
  }

  return (
    <div data-page="coding">
      <h2>设码</h2>
      <p className="muted">
        本模块不写 ECU。在 X431 上操作后，把 before/after 记在这里。菜单种子来自 can code 过滤结果。
      </p>
      <div className="callout">
        行数 {menu?.rowCount ?? 0} · 系统 {menu?.systemCount ?? menu?.systems.length ?? 0} ·{" "}
        {menu?.year} {menu?.model}
        {(!menu?.rowCount || menu.rowCount === 0) &&
          " — 请先运行 npm run ingest:x431 生成种子"}
      </div>

      <div className="panel" style={{ display: "grid", gridTemplateColumns: "1fr 1.3fr", gap: 16 }}>
        <div>
          <label>
            系统
            <select
              value={system}
              onChange={(e) => {
                setSystem(e.target.value);
                setItemIdx(0);
              }}
            >
              {(menu?.systems ?? []).map((s) => (
                <option key={s.system} value={s.system}>
                  {s.system} ({s.items.length})
                </option>
              ))}
            </select>
          </label>
          <div style={{ marginTop: 12, maxHeight: 420, overflow: "auto" }}>
            {(sys?.items ?? []).map((it, i) => (
              <div
                key={`${it.function}-${it.subFunction}-${i}`}
                className={`list-item ${itemIdx === i ? "active" : ""}`}
                onClick={() => setItemIdx(i)}
              >
                <strong>{it.function}</strong>
                {it.subFunction ? ` / ${it.subFunction}` : ""}
                <div className="muted">风险 {it.playbook.risk}</div>
              </div>
            ))}
          </div>
        </div>
        <div>
          {item ? (
            <>
              <h2>
                {item.function}
                {item.subFunction ? ` / ${item.subFunction}` : ""}
              </h2>
              <p className="muted">{item.playbook.x431Path}</p>
              <ol>
                {item.playbook.steps.map((st) => (
                  <li key={st}>{st}</li>
                ))}
              </ol>
              <div className="row">
                <label>
                  Before
                  <textarea value={before} onChange={(e) => setBefore(e.target.value)} />
                </label>
                <label>
                  After
                  <textarea value={after} onChange={(e) => setAfter(e.target.value)} />
                </label>
                <label>
                  备注
                  <textarea value={note} onChange={(e) => setNote(e.target.value)} />
                </label>
                <button className="primary" type="button" onClick={saveSnap}>
                  保存快照（不写车）
                </button>
              </div>
            </>
          ) : (
            <p className="muted">选择功能查看剧本</p>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>已保存快照</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>时间</th>
              <th>系统</th>
              <th>功能</th>
              <th>公里</th>
              <th>Before</th>
              <th>After</th>
            </tr>
          </thead>
          <tbody>
            {snaps.map((s) => (
              <tr key={s.id}>
                <td>{s.id}</td>
                <td>{s.recorded_at.slice(0, 19)}</td>
                <td>{s.system}</td>
                <td>
                  {s.function_name}
                  {s.sub_function ? ` / ${s.sub_function}` : ""}
                </td>
                <td>{s.odometer_km}</td>
                <td>{s.before_value}</td>
                <td>{s.after_value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
