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
  const [locatorFocus, setLocatorFocus] = useState<LocatorFocus | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus | null>(null);

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
        {tab === "parts" && <PartsBrowserPage onLocate={openLocator} />}
        {tab === "maintenance" && <GaragePage />}
        {tab === "obd" && <ObdPage onLocate={openLocator} />}
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
