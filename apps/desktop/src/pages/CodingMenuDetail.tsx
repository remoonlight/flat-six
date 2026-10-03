import { useState } from "react";
import { api } from "../api";
import type { ArchiveEvidence, MenuEntry } from "../coding-workspace";

export function CodingMenuDetail({ entry, archive }: { entry: MenuEntry; archive?: ArchiveEvidence }) {
  const [family, setFamily] = useState(entry.evidence[0].generation);
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  async function save() {
    if (busy) return;
    if (!before.trim() && !after.trim()) { setError("请填写实际读取的原值或操作后的实测值。"); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      const vehicle = await api().getVehicle();
      await api().addCoding({ system: entry.system, function_name: entry.function, sub_function: entry.subFunction,
        before_value: before, after_value: after, odometer_km: vehicle.current_km, recorded_at: new Date().toISOString(),
        note: `来源菜单：${family}，仅为资料语境，不代表实测 VIN 或车辆车系。\n来源：${entry.evidence.find((e) => e.generation === family)?.source}\n${note.trim()}` });
      setBefore(""); setAfter(""); setNote(""); setMessage("实测记录已保存。");
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <section className="panel coding-detail" data-testid="coding-menu-detail">
    <p className="muted">{entry.system} · X431 原始菜单</p>
    <h3>{entry.subFunction ?? entry.function}</h3>
    <p>{entry.evidence.map((e) => e.generation).join(" / ")} · 来源菜单证据，年款、参数和具体 ECU 适用性待核实。</p>
    <p>此条目仅保留菜单入口。尚无经核实的执行步骤、参数或写入流程。</p>
    <button type="button" disabled>实车执行待验证</button>
    <details><summary>来源与年款标签</summary>{entry.evidence.map((e) => <div key={e.generation}>
      <p>{e.generation} · 原始年款标签：{e.years.join("、")}（未核实）</p><code>{e.source}</code>
      <p>来源行号：{e.rowIds.join("、")}</p>
    </div>)}{archive ? <div data-testid="coding-archive-evidence"><p>X431 来源菜单路径</p><code>{archive.path}</code>
      <p>来源年款：{archive.year} · 来源行号：{archive.rowId}</p><code>{archive.source}</code></div> : null}</details>
    <h4>保存手工实测记录</h4>
    <p className="muted">记录归入当前车库车辆；请只填写该车辆的实际测量结果。资料车系不改变车辆档案。</p>
    <div className="coding-record-fields">
      <label>资料车系<select value={family} disabled={busy} onChange={(e) => { setFamily(e.target.value); setBefore(""); setAfter(""); setNote(""); setMessage(""); setError(""); }}>
        {entry.evidence.map((e) => <option key={e.generation}>{e.generation}</option>)}
      </select></label>
      <label>实际原值<textarea value={before} disabled={busy} onChange={(e) => setBefore(e.target.value)} /></label>
      <label>操作后实测值<textarea value={after} disabled={busy} onChange={(e) => setAfter(e.target.value)} /></label>
      <label>核验结果与备注<textarea value={note} disabled={busy} onChange={(e) => setNote(e.target.value)} /></label>
    </div>
    <div className="row"><button type="button" disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存实测记录"}</button></div>
    {message && <p role="status">{message}</p>}{error && <p className="error" role="alert">{error}</p>}
  </section>;
}
