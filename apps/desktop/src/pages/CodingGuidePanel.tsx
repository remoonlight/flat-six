import { useEffect, useState } from "react";
import {
  coding981Applicability,
  codingStepModule,
  type CodingGuideItem,
  type CodingPlanStep,
} from "@porsche981/domain";
import guide from "../../../../data/seed/coding-guide/981.json";
import license from "../../../../data/seed/coding-guide/LICENSE-StormEye818.txt?raw";
import { api, type Vehicle } from "../api";

const items: CodingGuideItem[] = guide.items;
export function CodingGuidePanel({ focusId }: { focusId: number }) {
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
    api().getVehicle().then((v) => { if (live) setVehicle(v); })
      .catch((e) => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
    };
  }, []);
  const active = items.find((item) => item.id === focusId);

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
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="coding-guide-panel" data-page="coding-guide">
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
      {active && (
        <>
          <div className="coding-guide-detail">
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
                      方案 A 与 B 为不同按键布局，按本车实际配置核实。
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
                  <details className="coding-license"><summary>来源与许可证</summary>
                    <p>{guide.source.repository}</p><p>版本：{guide.source.revision} · {guide.source.license}</p>
                    <pre>{license}</pre>
                  </details>
                </>
              ) : (
                <p className="muted">选择功能查看参数指引。</p>
              )}
            </section>
          </div>
        </>
      )}
      {recordStep && (
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
        </>
      )}
    </div>
  );
}
