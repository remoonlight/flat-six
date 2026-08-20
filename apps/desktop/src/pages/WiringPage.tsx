import { useEffect, useState } from "react";
import { api, type WiringIndex } from "../api";
import type { LocatorFocus } from "../locator-focus";

export type WiringPageProps = {
  onLocate?: (focus: LocatorFocus) => void;
};

export function WiringPage({ onLocate }: WiringPageProps = {}) {
  const [idx, setIdx] = useState<WiringIndex | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api()
      .wiringIndex()
      .then(setIdx)
      .catch((e) => setMsg(e instanceof Error ? e.message : String(e)));
  }, []);

  async function openPdf(kind: "main" | "ref", id?: string) {
    setMsg(null);
    try {
      const r = await api().openWiringPdf(kind, id);
      if (!r.ok) setMsg(r.error ?? "打开失败");
      else setMsg("已用系统默认程序打开 PDF");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  if (!idx) {
    return (
      <div>
        <h2>线束参考</h2>
        <p className="muted">{msg ?? "加载索引…"}</p>
      </div>
    );
  }

  return (
    <div>
      <h2>线束参考（框架）</h2>
      <p className="muted">
        权威资料是本地 PDF，不进 PIWIS。本页先提供系统索引 + 一键打开；拆页/热点以后再补。
        有定位锚点的系统可跳到 Locator 草稿区。
      </p>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>{idx.pdf.label}</h2>
        <p className="muted" style={{ wordBreak: "break-all" }}>
          {idx.pdf.path}
        </p>
        <button type="button" className="btn" onClick={() => openPdf("main")}>
          用系统打开 981.pdf
        </button>
        {msg && <p className="muted" style={{ marginTop: 8 }}>{msg}</p>}
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>系统索引（占位）</h2>
        <table>
          <thead>
            <tr>
              <th>系统</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {idx.systems.map((s) => (
              <tr key={s.id}>
                <td>{s.label_zh}</td>
                <td className="muted">{s.status === "stub" ? "待挂页码" : s.status}</td>
                <td>
                  {s.locatorZoneId && onLocate ? (
                    <button
                      type="button"
                      className="ghost"
                      title={
                        s.locatorHotspotId
                          ? `跳到 ${s.locatorZoneId} / ${s.locatorHotspotId}`
                          : `跳到 ${s.locatorZoneId}`
                      }
                      onClick={() =>
                        onLocate({
                          zoneId: s.locatorZoneId!,
                          hotspotId: s.locatorHotspotId ?? null,
                          sku: null,
                        })
                      }
                    >
                      定位
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>对照文件（勿与主车混用）</h2>
        <ul className="plain-list">
          {idx.doNotUse.map((f) => (
            <li key={f.id}>
              <span>{f.label}</span>{" "}
              <button
                type="button"
                className="btn-link"
                onClick={() => openPdf("ref", f.id)}
              >
                打开
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
