import { useEffect, useMemo, useState } from "react";
import {
  build981CodingPlan,
  coding981Applicability,
  codingStepModule,
  type CodingGuideItem,
  type CodingPlanStep,
} from "@porsche981/domain";
import guide from "../../../../data/seed/coding-guide/981.json";
import sourceLicense from "../../../../data/seed/coding-guide/LICENSE-StormEye818.txt?raw";
import { api, type CodingSnapshot, type Vehicle } from "../api";
import { X431CodingArchive } from "./X431CodingArchive";

const items: CodingGuideItem[] = guide.items;
const sourceLink = `${guide.source.repository}/blob/${guide.source.revision}/index.html`;
type View = "catalog" | "plan" | "records" | "x431";

export function CodingPage() {
  const [view, setView] = useState<View>("catalog");
  const [query, setQuery] = useState("");
  const [module, setModule] = useState("全部模块");
  const [includeExcluded, setIncludeExcluded] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [activeId, setActiveId] = useState(2);
  const [variant, setVariant] = useState("");
  const [snaps, setSnaps] = useState<CodingSnapshot[]>([]);
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [recordStep, setRecordStep] = useState<CodingPlanStep | null>(null);
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [ecu, setEcu] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let live = true;
    Promise.all([api().listCoding(), api().getVehicle()])
      .then(([s, v]) => {
        if (live) {
          setSnaps(s);
          setVehicle(v);
        }
      })
      .catch((e) => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
    };
  }, [view]);
  const filtered = useMemo(
    () =>
      items.filter((item) => {
        if (!includeExcluded && coding981Applicability(item).excluded)
          return false;
        if (
          module !== "全部模块" &&
          item.module !== module &&
          !item.steps.some((s) => codingStepModule(item, s) === module)
        )
          return false;
        return [
          item.name,
          item.nameEn,
          item.module,
          item.note,
          ...item.steps.map((s) => `${s.pathZh} ${s.pathEn}`),
        ]
          .join(" ")
          .toLowerCase()
          .includes(query.trim().toLowerCase());
      }),
    [query, module, includeExcluded],
  );
  const modules = [
    ...new Set(
      items.flatMap((i) => [
        i.module,
        ...i.steps.map((s) => codingStepModule(i, s)),
      ]),
    ),
  ];
  const active = filtered.find((i) => i.id === activeId) ?? filtered[0];
  const plan = useMemo(
    () => build981CodingPlan(items, selected, variant),
    [selected, variant],
  );
  function toggle(id: number) {
    setSelected((p) =>
      p.includes(id) ? p.filter((x) => x !== id) : [...p, id],
    );
  }
  function beginRecord(item: CodingGuideItem, stepIndex: number) {
    const step = item.steps[stepIndex];
    setRecordStep({
      ...step,
      stepIndex,
      itemId: item.id,
      feature: item.name,
      module: codingStepModule(item, step),
    });
    setBefore("");
    setAfter("");
    setEcu("");
    setNote("");
    setError(null);
    setMessage("");
    setView("records");
  }
  async function saveRecord() {
    if (!recordStep || !vehicle || busy) return;
    if (!before.trim() && !after.trim()) {
      setError("请填写实际读取的原值或操作后的实测值。");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await api().addCoding({
        system: recordStep.module,
        function_name: recordStep.feature,
        sub_function: recordStep.pathEn,
        before_value: before,
        after_value: after,
        odometer_km: vehicle.current_km,
        recorded_at: new Date().toISOString(),
        note: `来源：StormEye818 #${recordStep.itemId} / ${guide.source.revision}\n步骤：${recordStep.stepIndex + 1}\n设备：X431 手工记录\nECU 版本：${ecu.trim() || "未填写"}\n${note.trim()}`,
      });
      setBefore("");
      setAfter("");
      setNote("");
      setRecordStep(null);
      setMessage("X431 实测记录已保存。");
      setSnaps(await api().listCoding());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function copyPlan() {
    try {
      const text = [
        "2014 Boxster S（981）PDK · X431 对照方案",
        "社区参考，未验证本车 ECU；来源原值不是实测值。",
        `来源：${sourceLink}`,
        ...plan.items.map(
          (i) => `${i.name}：${coding981Applicability(i).reason} ${i.note}`,
        ),
        ...plan.groups.flatMap((g) => [
          `\n${g.module}`,
          ...g.steps.map(
            (s) =>
              `${s.feature} / 步骤 ${s.stepIndex + 1}\n${s.pathZh}\n${s.pathEn}\n来源原值：${s.from} → 来源目标值：${s.to}`,
          ),
        ]),
      ].join("\n");
      await navigator.clipboard.writeText(text);
      setMessage("方案已复制，包含来源与全部选定步骤。");
    } catch (e) {
      setError(`复制失败：${String(e)}`);
    }
  }
  return (
    <div className="coding-workspace" data-page="coding">
      <header className="coding-heading">
        <div>
          <p className="muted">2014 · BOXSTER S · 981 · PDK</p>
          <h2>设码与隐藏功能</h2>
          <p>选择想实现的功能，查看 X431 参数路径，保存实际操作前后的值。</p>
        </div>
        <div className="coding-connection">
          <strong>X431 指引与记录</strong>
          <span>vLinker 独立设码：待协议验证</span>
        </div>
      </header>
      <nav className="chip-row" aria-label="设码分区">
        {(
          [
            ["catalog", "981 功能库"],
            ["plan", `功能方案（${selected.length}）`],
            ["records", "操作记录"],
            ["x431", "X431 原始菜单"],
          ] as [View, string][]
        ).map(([id, label]) => (
          <button
            type="button"
            className={`chip${view === id ? " active" : ""}`}
            key={id}
            onClick={() => {
              setView(id);
              setMessage("");
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="callout" role="status">
          {message}
        </p>
      )}
      {view === "catalog" && (
        <>
          <div className="coding-source">
            <span>{items.length} 项 981 系列参考 · 默认显示当前车型候选</span>
            <span>
              社区资料，年款、配置与 ECU 版本待核对 ·{" "}
              <a href={sourceLink} target="_blank" rel="noreferrer">
                查看来源
              </a>
            </span>
          </div>
          <div className="coding-filters">
            <label>
              搜索功能
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="例如：后视镜、启停、水温"
              />
            </label>
            <label>
              筛选模块
              <select
                value={module}
                onChange={(e) => setModule(e.target.value)}
              >
                {["全部模块", ...modules].map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            </label>
            <label className="coding-check">
              <input
                type="checkbox"
                checked={includeExcluded}
                onChange={(e) => setIncludeExcluded(e.target.checked)}
              />
              显示不适用条目
            </label>
          </div>
          <div className="coding-layout">
            <section className="panel coding-catalog" aria-label="981 功能列表">
              <div className="coding-list-head">
                <strong>{filtered.length} 项功能</strong>
                <span>已选 {selected.length} 项</span>
              </div>
              {!filtered.length && (
                <p className="muted">没有匹配的功能，请调整搜索或模块。</p>
              )}
              {filtered.map((item) => {
                const app = coding981Applicability(item);
                return (
                  <div
                    key={item.id}
                    className={`coding-feature${active?.id === item.id ? " active" : ""}`}
                  >
                    <input
                      type="checkbox"
                      aria-label={`选择 ${item.name}`}
                      checked={selected.includes(item.id)}
                      disabled={app.excluded}
                      onChange={() => toggle(item.id)}
                    />
                    <button
                      type="button"
                      onClick={() => setActiveId(item.id)}
                      aria-pressed={active?.id === item.id}
                    >
                      <strong>{item.name}</strong>
                      <span>
                        {item.module} · {item.steps.length} 步
                      </span>
                      <small>{app.label}</small>
                    </button>
                  </div>
                );
              })}
            </section>
            <section className="panel coding-detail" aria-label="功能详情">
              {active ? (
                <>
                  <p className="muted">
                    {active.module} / 社区项目 #{active.id}
                  </p>
                  <h3>{active.name}</h3>
                  <p className="muted">{active.nameEn}</p>
                  <div className="callout">
                    {coding981Applicability(active).reason}
                  </div>
                  {active.note && (
                    <p>
                      <strong>来源备注：</strong>
                      {active.note}
                    </p>
                  )}
                  {active.id === 9002 && (
                    <p className="muted">
                      方案 A 与 B 为不同按键布局，在功能方案中二选一。
                    </p>
                  )}
                  <p className="muted">
                    以下数值来自参考资料，实际原值需在 X431 读取后记录。
                  </p>
                  <ol className="coding-steps">
                    {active.steps.map((step, index) => (
                      <li key={index}>
                        {step.module && (
                          <span className="coding-module">{step.module}</span>
                        )}
                        <strong>{step.pathZh}</strong>
                        <code>{step.pathEn}</code>
                        <div className="coding-values">
                          <span>
                            来源原值 <b>{step.from}</b>
                          </span>
                          <span>
                            来源目标值 <b>{step.to}</b>
                          </span>
                        </div>
                        {!coding981Applicability(active).excluded && (
                          <button
                            type="button"
                            className="ghost"
                            onClick={() => beginRecord(active, index)}
                          >
                            记录此步骤
                          </button>
                        )}
                      </li>
                    ))}
                  </ol>
                </>
              ) : (
                <p className="muted">选择功能查看参数指引。</p>
              )}
            </section>
          </div>
          <div className="coding-actionbar">
            <span>已选 {selected.length} 项 · 方案按模块整理完整步骤</span>
            <button
              type="button"
              className="primary"
              disabled={!selected.length}
              onClick={() => setView("plan")}
            >
              查看功能方案
            </button>
          </div>
        </>
      )}
      {view === "plan" && (
        <section className="panel">
          <div className="coding-list-head">
            <h3>功能方案</h3>
            <button
              type="button"
              className="ghost"
              disabled={!selected.length}
              onClick={() => setSelected([])}
            >
              清空方案
            </button>
          </div>
          {!selected.length ? (
            <p className="muted">先在 981 功能库勾选想实现的功能。</p>
          ) : (
            <>
              <div className="chip-row">
                {items
                  .filter((i) => selected.includes(i.id))
                  .map((i) => (
                    <button
                      type="button"
                      className="chip"
                      key={i.id}
                      onClick={() => toggle(i.id)}
                      aria-label={`移除 ${i.name}`}
                    >
                      {i.name} ×
                    </button>
                  ))}
              </div>
              {selected.includes(9002) && (
                <label>
                  运排按键方案
                  <select
                    value={variant}
                    onChange={(e) => setVariant(e.target.value)}
                  >
                    <option value="">请选择一种布局</option>
                    <option value="A">方案 A：右侧按钮 3 为运动排气</option>
                    <option value="B">
                      方案 B：右侧按钮 2 为运动排气，按钮 3 为启停
                    </option>
                  </select>
                </label>
              )}
              {!!plan.issues.length && (
                <div className="callout" role="status">
                  <strong>请先补齐方案</strong>
                  <ul>
                    {plan.issues.map((i) => (
                      <li key={i}>{i}</li>
                    ))}
                  </ul>
                  {selected.includes(11) !== selected.includes(103) && (
                    <button
                      type="button"
                      onClick={() =>
                        setSelected((p) => [...new Set([...p, 11, 103])])
                      }
                    >
                      补齐日行灯配套项目
                    </button>
                  )}
                </div>
              )}
              {!plan.issues.length && (
                <>
                  <p>
                    {plan.items.length} 项功能 · {plan.groups.length} 个模块 ·{" "}
                    {plan.stepCount} 步
                  </p>
                  <p className="muted">
                    在 X431
                    对照操作；序号保留各功能的来源顺序。参数存在与否、硬件条件及本车原值需逐项核对。
                  </p>
                  <button type="button" onClick={copyPlan}>
                    复制完整方案
                  </button>
                  {plan.items.map((i) => (
                    <p className="muted" key={i.id}>
                      {i.name}：{coding981Applicability(i).reason}{" "}
                      {i.note && `来源备注：${i.note}`}
                    </p>
                  ))}
                  {plan.groups.map((group) => (
                    <section key={group.module} className="coding-plan-group">
                      <h3>{group.module}</h3>
                      {group.steps.map((step) => (
                        <div
                          className="coding-plan-step"
                          key={`${step.itemId}-${step.stepIndex}`}
                        >
                          <strong>
                            {step.feature} · 步骤 {step.stepIndex + 1}
                          </strong>
                          <p>{step.pathZh}</p>
                          <code>{step.pathEn}</code>
                          <p>
                            来源原值：{step.from} → 来源目标值：{step.to}
                          </p>
                          <button
                            type="button"
                            className="ghost"
                            onClick={() =>
                              beginRecord(
                                items.find((i) => i.id === step.itemId)!,
                                step.stepIndex,
                              )
                            }
                          >
                            记录此步骤
                          </button>
                        </div>
                      ))}
                    </section>
                  ))}
                </>
              )}
            </>
          )}
        </section>
      )}
      {view === "records" && (
        <>
          {recordStep && (
            <section className="panel">
              <h3>记录 X431 实测值</h3>
              <p>
                {recordStep.feature} · {recordStep.module} · 步骤{" "}
                {recordStep.stepIndex + 1}
              </p>
              <code>{recordStep.pathEn}</code>
              <div className="coding-record-fields">
                <label>
                  实际原值
                  <textarea
                    value={before}
                    onChange={(e) => setBefore(e.target.value)}
                    placeholder="填写设备实际读到的值，可留空"
                  />
                </label>
                <label>
                  操作后实测值
                  <textarea
                    value={after}
                    onChange={(e) => setAfter(e.target.value)}
                    placeholder="填写操作后读回的值"
                  />
                </label>
                <label>
                  ECU 型号或软件版本
                  <input value={ecu} onChange={(e) => setEcu(e.target.value)} />
                </label>
                <label>
                  核验结果与备注
                  <textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
              </div>
              <div className="row">
                <button
                  type="button"
                  className="primary"
                  disabled={busy || !vehicle}
                  onClick={saveRecord}
                >
                  {busy ? "保存中…" : "保存实测记录"}
                </button>
                <button
                  type="button"
                  className="ghost"
                  disabled={busy}
                  onClick={() => setRecordStep(null)}
                >
                  取消记录
                </button>
              </div>
            </section>
          )}
          <section className="panel">
            <h3>操作记录 · {snaps.length}</h3>
            <p className="muted">
              包含原有 X431 快照。保存记录仅用于留档，不会发送车辆指令。
            </p>
            {!snaps.length && (
              <p className="muted">
                暂无记录。可从功能步骤进入记录，或使用 X431 原始菜单保存快照。
              </p>
            )}
            {snaps.map((s) => (
              <details key={s.id} className="coding-snapshot">
                <summary>
                  #{s.id} · {s.function_name} · {s.system} ·{" "}
                  {s.recorded_at.slice(0, 10)}
                </summary>
                <p>{s.sub_function}</p>
                <p>{s.odometer_km} km</p>
                <div className="coding-record-fields">
                  <div>
                    <strong>实际原值</strong>
                    <pre>{s.before_value || "未记录"}</pre>
                  </div>
                  <div>
                    <strong>操作后实测值</strong>
                    <pre>{s.after_value || "未记录"}</pre>
                  </div>
                </div>
                <pre>{s.note}</pre>
              </details>
            ))}
          </section>
        </>
      )}
      {view === "x431" && <X431CodingArchive />}
      <details className="coding-license">
        <summary>参考数据来源与许可 · StormEye818 / MIT</summary>
        <p>
          <a href={sourceLink} target="_blank" rel="noreferrer">
            固定版本的 981 参考数据
          </a>
        </p>
        <pre>{sourceLicense}</pre>
      </details>
    </div>
  );
}
