import { useEffect, useState } from "react";
import { GaragePage } from "./pages/GaragePage";
import { LocatorPage } from "./pages/LocatorPage";
import { PartsBrowserPage } from "./pages/PartsBrowserPage";
import { ObdPage } from "./pages/ObdPage";
import { VehicleSettingsPage } from "./pages/VehicleSettingsPage";
import { XrayTunePage } from "./pages/XrayTunePage";
import type { BridgeStatus } from "./api";
import type { LocatorFocus } from "./locator-focus";
import porscheWordmark from "./assets/porsche-wordmark.svg";
import porsche981Mark from "./assets/porsche-981.svg";

/** 一级菜单（2026-08-02 拍板替换旧 6 Tab；设码并入实时 OBD） */
type Tab =
  | "parts"
  | "maintenance"
  | "obd"
  | "settings"
  | "locator";

const TABS: { id: Tab; label: string }[] = [
  { id: "parts", label: "车库 · 零件浏览器" },
  { id: "maintenance", label: "零件维护状态" },
  { id: "obd", label: "实时 OBD" },
  { id: "settings", label: "车辆设置" },
  { id: "locator", label: "部件定位" },
];

function bridgeBannerText(s: BridgeStatus): string | null {
  if (s.state === "restarting" || s.state === "starting") {
    return `数据库桥接正在恢复…${s.detail ? `（${s.detail}）` : ""}`;
  }
  if (s.state === "down") {
    return `数据库桥接失败，请重启应用。${s.detail ? ` ${s.detail}` : ""}`;
  }
  return null;
}

function isXrayTuneHash(hash = window.location.hash): boolean {
  return hash.replace(/^#/, "").split("?")[0] === "xray-tune";
}

export function App() {
  const [xrayTuneOnly] = useState(() => isXrayTuneHash());
  const [tab, setTab] = useState<Tab>("parts");
  const [obdVisited, setObdVisited] = useState(false);
  const [codingDirty, setCodingDirty] = useState(false);
  const [codingDiscardVersion, setCodingDiscardVersion] = useState(0);
  const [pendingTab, setPendingTab] = useState<Tab | null>(null);
  const [locatorFocus, setLocatorFocus] = useState<LocatorFocus | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus | null>(null);

  useEffect(() => {
    void window.porsche981?.setCodingDraftDirty?.(codingDirty);
  }, [codingDirty]);

  useEffect(() => {
    const api = window.porsche981;
    if (!api?.getBridgeStatus) return;
    let cancelled = false;
    api.getBridgeStatus().then((s) => {
      if (!cancelled) setBridgeStatus(s);
    });
    const unsub = api.onBridgeStatus?.((s) => setBridgeStatus(s));
    return () => {
      cancelled = true;
      unsub?.();
    };
  }, []);

  function goToTab(id: Tab) {
    if (tab === "obd" && id !== "obd" && codingDirty) { setPendingTab(id); return; }
    if (id === "obd") setObdVisited(true);
    if (id !== "locator") setLocatorFocus(null);
    setTab(id);
  }

  function openLocator(focus: LocatorFocus) {
    setLocatorFocus(focus);
    setTab("locator");
  }

  if (xrayTuneOnly) {
    return <XrayTunePage />;
  }

  const banner = bridgeStatus ? bridgeBannerText(bridgeStatus) : null;

  return (
    <div className="app-shell">
      <nav className="side">
        <div className="brand" aria-label="Porsche 981">
          <img
            className="brand-wordmark"
            src={porscheWordmark}
            alt="PORSCHE"
            width={180}
            height={12}
            decoding="async"
          />
          <img
            className="brand-model"
            src={porsche981Mark}
            alt=""
            width={32}
            height={13}
            decoding="async"
            aria-hidden="true"
          />
        </div>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-tab={t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => goToTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main className="content">
        {pendingTab && <section className="panel" role="alert" data-testid="coding-app-leave-review"><p>尚有未写入的调整，离开设码页会丢弃这些调整。</p>
          <button type="button" onClick={() => setPendingTab(null)}>继续修改</button>
          <button type="button" onClick={() => {
            setCodingDirty(false); setCodingDiscardVersion(v => v + 1);
            setTab(pendingTab); if (pendingTab !== "locator") setLocatorFocus(null); setPendingTab(null);
          }}>丢弃后离开</button>
        </section>}
        {banner && (
          <div
            className={
              bridgeStatus?.state === "down"
                ? "bridge-banner down"
                : "bridge-banner"
            }
            role="status"
          >
            {banner}
          </div>
        )}
        {tab === "parts" && <PartsBrowserPage />}
        {tab === "maintenance" && <GaragePage />}
        <div hidden={tab !== "obd"}>
          {obdVisited && <ObdPage active={tab === "obd"} onCodingDraftChange={setCodingDirty} codingDiscardVersion={codingDiscardVersion} />}
        </div>
        {tab === "settings" && <VehicleSettingsPage />}
        {tab === "locator" && (
          <LocatorPage
            initialZoneId={locatorFocus?.zoneId}
            initialHotspotId={locatorFocus?.hotspotId}
            initialSku={locatorFocus?.sku}
          />
        )}
      </main>
    </div>
  );
}
