import { useCallback, useEffect, useMemo, useState } from "react";
import { type FxTable, resolveBodyPaintHex, resolveBodyPaintPbr, resolveInteriorHex, resolveSoftTopHex } from "@porsche981/domain";
import {
  api,
  type CmsAsset,
  type CmsMeshMap,
  type LocatorBridgeJump,
  type LocatorMap,
  type LocatorSystemsDraft,
  type LocatorZone,
  type Part,
  type ServiceRecord,
  type Vehicle,
  type XrayAssembly,
  type XrayMeshState,
  type XrayScene,
  type XrayTransform,
} from "../api";
import { LocatorGlbViewer } from "../components/LocatorGlbViewer";
import {
  DEFAULT_FX_TABLE,
  currencyFromPriceNote,
  formatMoney,
  formatMoneyDisplay,
  isPetkaPriceVerified,
  needsPetkaPriceVerify,
  needsVinPetkaConfirm,
} from "../price";

function isFxTable(v: unknown): v is FxTable {
  if (!v || typeof v !== "object") return false;
  const o = v as FxTable;
  return (
    o.display_currency === "CNY" &&
    !!o.rates_to_cny &&
    typeof o.as_of === "string"
  );
}

function toUint8(raw: unknown): Uint8Array {
  if (raw instanceof Uint8Array) return raw;
  if (raw instanceof ArrayBuffer) return new Uint8Array(raw);
  if (
    raw &&
    typeof raw === "object" &&
    "data" in raw &&
    Array.isArray((raw as { data: number[] }).data)
  ) {
    return Uint8Array.from((raw as { data: number[] }).data);
  }
  throw new Error("cms_glb_buffer_unexpected");
}

export type LocatorPageProps = {
  /** Optional deep-link from fault KB (R5). */
  initialZoneId?: string | null;
  initialHotspotId?: string | null;
  /** When set, select matching part after hotspot filter. */
  initialSku?: string | null;
};

export function LocatorPage({
  initialZoneId = null,
  initialHotspotId = null,
  initialSku = null,
}: LocatorPageProps = {}) {
  const [map, setMap] = useState<LocatorMap | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [serviceRecords, setServiceRecords] = useState<ServiceRecord[]>([]);
  const [systemsDraft, setSystemsDraft] = useState<LocatorSystemsDraft | null>(
    null,
  );
  const [zoneId, setZoneId] = useState<string | null>(null);
  const [activeHotspot, setActiveHotspot] = useState<string | null>(null);
  const [selectedPart, setSelectedPart] = useState<Part | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [fx, setFx] = useState<FxTable>(DEFAULT_FX_TABLE);
  const [assets, setAssets] = useState<CmsAsset[]>([]);
  const [assetId, setAssetId] = useState<string | null>(null);
  const [meshMap, setMeshMap] = useState<CmsMeshMap | null>(null);
  const [pickedMesh, setPickedMesh] = useState<string | null>(null);
  const [pickedMeshPath, setPickedMeshPath] = useState<string | null>(null);
  const [mapBusy, setMapBusy] = useState(false);
  const [mapMsg, setMapMsg] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"single" | "xray">("single");
  const [xrayScene, setXrayScene] = useState<XrayScene | null>(null);
  const [meshState, setMeshState] = useState<XrayMeshState | null>(null);
  const [showGhostBody, setShowGhostBody] = useState(true);
  const [pickedAssemblyId, setPickedAssemblyId] = useState<string | null>(null);
  /** Bake / disk TRS（本页只读展示，不提供滑条微调）。 */
  const [previewTransforms, setPreviewTransforms] = useState<
    Record<string, XrayTransform>
  >({});
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);

  // Hydrate transforms from scene once so viewer matches disk
  useEffect(() => {
    if (!xrayScene) return;
    setPreviewTransforms((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const layer of xrayScene.layers ?? []) {
        if (!next[layer.id] && layer.transform) {
          next[layer.id] = layer.transform;
          changed = true;
        }
      }
      if (
        !next.body &&
        xrayScene.bodyShell?.transform
      ) {
        next.body = xrayScene.bodyShell.transform;
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [xrayScene]);

  // Single-model: load hand TRS for current asset (cms991body / cabin* / …)
  useEffect(() => {
    if (viewMode !== "single" || !assetId) return;
    let cancelled = false;
    void api()
      .xrayGetTransforms()
      .then((file) => {
        if (cancelled) return;
        const t = file.layers?.[assetId];
        if (!t) return;
        setPreviewTransforms((prev) =>
          prev[assetId] ? prev : { ...prev, [assetId]: t },
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [viewMode, assetId]);

  useEffect(() => {
    Promise.all([
      api().locatorMap(),
      api().listParts(),
      api().listService(),
      Promise.resolve(api().getFx?.() ?? null).catch(() => null),
      api().cmsListAssets().catch(() => [] as CmsAsset[]),
      api().cmsGetMeshMap().catch(() => null),
      api().xrayLayers().catch(() => null),
      api().xrayGetMeshState().catch(() => null),
      Promise.resolve(api().locatorSystemsDraft?.() ?? null).catch(() => null),
      api().getVehicle().catch(() => null),
    ])
      .then(([m, p, svc, fxTable, cmsAssets, mm, xray, ms, draft, v]) => {
        setMap(m);
        setParts(p);
        setServiceRecords(svc);
        setAssets(cmsAssets);
        if (mm) setMeshMap(mm);
        if (xray) setXrayScene(xray);
        if (ms) setMeshState(ms);
        if (draft) setSystemsDraft(draft);
        if (v) setVehicle(v);
        if (isFxTable(fxTable)) setFx(fxTable);

        const focusZone =
          (initialZoneId && m.zones.find((z) => z.id === initialZoneId)) ||
          null;
        const zone = focusZone ?? m.zones[0] ?? null;
        setZoneId(zone?.id ?? null);
        setAssetId(zone?.cms?.assetId ?? null);

        const hotspotOk =
          initialHotspotId &&
          zone?.hotspots.some((h) => h.id === initialHotspotId)
            ? initialHotspotId
            : (zone?.hotspots[0]?.id ?? null);
        setActiveHotspot(hotspotOk);

        if (initialSku) {
          const part = p.find((x) => x.sku === initialSku) ?? null;
          setSelectedPart(part);
        } else {
          setSelectedPart(null);
        }
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [initialZoneId, initialHotspotId, initialSku]);

  useEffect(() => {
    if (xrayScene && (xrayScene.layers?.length ?? 0) === 0 && viewMode === "xray") {
      setViewMode("single");
    }
  }, [xrayScene, viewMode]);

  const zone: LocatorZone | null = useMemo(() => {
    if (!map || !zoneId) return null;
    return map.zones.find((z) => z.id === zoneId) ?? null;
  }, [map, zoneId]);

  const activeAsset = useMemo(() => {
    if (!assetId) return null;
    return assets.find((a) => a.id === assetId) ?? null;
  }, [assets, assetId]);

  const glbRel = activeAsset?.present ? activeAsset.rel : null;

  const xrayLayers = useMemo(
    () => (xrayScene?.layers ?? []).filter((l) => l.present),
    [xrayScene],
  );

  const zoneLinks = useMemo(() => {
    if (!meshMap || !zoneId) return [];
    return meshMap.links.filter((l) => l.zoneId === zoneId);
  }, [meshMap, zoneId]);

  const linkedMeshNames = useMemo(
    () => new Set(zoneLinks.map((l) => l.meshName)),
    [zoneLinks],
  );

  const relatedParts = useMemo(
    () => parts.filter((p) => p.locator_hotspot === activeHotspot),
    [parts, activeHotspot],
  );

  const activeBridgeJump: LocatorBridgeJump | null = useMemo(() => {
    if (!activeHotspot || !systemsDraft?.bridgeJumps) return null;
    return (
      systemsDraft.bridgeJumps.find((j) => j.fromHotspotId === activeHotspot) ??
      null
    );
  }, [activeHotspot, systemsDraft]);

  function applyBridgeJump(jump: LocatorBridgeJump) {
    const z = map?.zones.find((x) => x.id === jump.toZoneId);
    if (!z) {
      setMapMsg(`桥接目标区缺失：${jump.toZoneId}`);
      return;
    }
    setZoneId(z.id);
    setAssetId(z.cms?.assetId ?? null);
    const hotspotOk = z.hotspots.some((h) => h.id === jump.toHotspotId)
      ? jump.toHotspotId
      : (z.hotspots[0]?.id ?? null);
    setActiveHotspot(hotspotOk);
    setPickedMesh(null);
    setPickedMeshPath(null);
    setPickedAssemblyId(null);
    if (jump.sku) {
      setSelectedPart(parts.find((p) => p.sku === jump.sku) ?? null);
    } else {
      setSelectedPart(null);
    }
    setMapMsg(
      `已跳到零件锚点 ${jump.toZoneId} / ${hotspotOk}${jump.sku ? `（${jump.sku}）` : ""}`,
    );
  }

  const partServiceHistory = useMemo(() => {
    if (!selectedPart) return [];
    return serviceRecords.filter((r) => r.part_id === selectedPart.id);
  }, [serviceRecords, selectedPart]);

  const selectedOemDisp = selectedPart
    ? formatMoneyDisplay(selectedPart.oem_price, selectedPart.price_note, fx)
    : null;
  const selectedAmDisp = selectedPart
    ? formatMoneyDisplay(
        selectedPart.aftermarket_price,
        selectedPart.price_note,
        fx,
      )
    : null;
  const selectedAmBrands = useMemo(() => {
    if (!selectedPart?.aftermarket_quotes?.length) return null;
    if (selectedPart.aftermarket_quotes.length === 1) return null;
    return selectedPart.aftermarket_quotes
      .map(
        (q) =>
          `${q.brand} ${formatMoney(q.price, selectedPart.price_note, fx)}`,
      )
      .join(" · ");
  }, [selectedPart, fx]);

  const readGlb = useCallback(async (rel: string) => {
    const raw = await api().cmsReadGlb(rel);
    return toUint8(raw);
  }, []);

  const bodyPaintHex = useMemo(
    () => resolveBodyPaintHex(vehicle?.paint_name),
    [vehicle?.paint_name],
  );
  const bodyPaintPbr = useMemo(
    () => resolveBodyPaintPbr(vehicle?.paint_name),
    [vehicle?.paint_name],
  );
  const softTopHex = useMemo(
    () => resolveSoftTopHex(vehicle?.top),
    [vehicle?.top],
  );
  const interiorHex = useMemo(
    () => resolveInteriorHex(vehicle?.interior),
    [vehicle?.interior],
  );

  function switchZone(id: string) {
    const z = map?.zones.find((x) => x.id === id);
    setZoneId(id);
    setActiveHotspot(z?.hotspots[0]?.id ?? null);
    setSelectedPart(null);
    setAssetId(z?.cms?.assetId ?? null);
    setPickedMesh(null);
    setPickedMeshPath(null);
    setPickedAssemblyId(null);
    setMapMsg(null);
  }

  function applyAssemblyBridge(assemblyId: string | null) {
    if (!assemblyId || !xrayScene) return;
    const a =
      xrayScene.assemblies.find((x) => x.id === assemblyId) ??
      xrayScene.layers?.find((x) => x.id === assemblyId);
    if (!a?.bridgeZone) return;
    if (a.bridgeZone !== zoneId) {
      const z = map?.zones.find((x) => x.id === a.bridgeZone);
      if (z) {
        setZoneId(z.id);
        setAssetId(z.cms?.assetId ?? null);
      }
    }
    if (
      a.bridgeHotspot &&
      map?.zones
        .find((z) => z.id === (a.bridgeZone ?? zoneId))
        ?.hotspots.some((h) => h.id === a.bridgeHotspot)
    ) {
      setActiveHotspot(a.bridgeHotspot);
    }
  }

  async function refreshXray() {
    try {
      const [next, ms] = await Promise.all([
        api().xrayLayers(),
        api().xrayGetMeshState(),
      ]);
      setXrayScene(next);
      setMeshState(ms);
      setMapMsg("已刷新 X-ray 装配 / transforms / mesh-state");
    } catch (e) {
      setMapMsg(e instanceof Error ? e.message : String(e));
    }
  }

  async function linkMesh() {
    if (!zoneId || !pickedMesh || !activeHotspot) {
      setMapMsg("先点 3D mesh，再点 2D 热点，然后配对");
      return;
    }
    setMapBusy(true);
    setMapMsg(null);
    try {
      const next = await api().cmsUpsertMeshLink({
        zoneId,
        meshName: pickedMesh,
        hotspotId: activeHotspot,
        assetId: assetId,
        sku: selectedPart?.sku ?? null,
        note: pickedMeshPath,
      });
      setMeshMap(next);
      setMapMsg(`已写入：${pickedMesh} → ${activeHotspot}`);
    } catch (e) {
      setMapMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setMapBusy(false);
    }
  }

  async function unlinkMesh(meshName: string) {
    if (!zoneId) return;
    setMapBusy(true);
    try {
      const next = await api().cmsRemoveMeshLink(zoneId, meshName);
      setMeshMap(next);
      setMapMsg(`已删除：${meshName}`);
    } catch (e) {
      setMapMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setMapBusy(false);
    }
  }

  if (err) {
    return (
      <div>
        <h1>部件定位</h1>
        <p className="error">{err}</p>
      </div>
    );
  }

  if (!map || !zone) {
    return (
      <div>
        <h1>部件定位</h1>
        <p className="muted">加载定位图…</p>
      </div>
    );
  }

  const hotspotLabel =
    zone.hotspots.find((h) => h.id === activeHotspot)?.label ?? "未选择";
  const isPlaceholder = zone.background.source === "placeholder";
  const hasCms = Boolean(zone.cms?.present || activeAsset?.present);
  const hasXray = xrayLayers.length > 0;

  return (
    <div>
      <h1>部件定位</h1>

      <div className="locator-zone-tabs" role="tablist" aria-label="定位分区">
        {map.zones.map((z) => (
          <button
            key={z.id}
            type="button"
            role="tab"
            aria-selected={z.id === zoneId}
            className={
              z.id === zoneId ? "locator-zone-tab active" : "locator-zone-tab"
            }
            onClick={() => switchZone(z.id)}
          >
            {z.label_zh}
            {z.cms?.present ? (
              <span className="locator-badge ok">示意 3D</span>
            ) : z.status === "pending-3d" ? (
              <span className="locator-badge">待 3D</span>
            ) : null}
          </button>
        ))}
      </div>

      <div className="locator-dual">
        <section className="locator-pane">
          <div className="locator-pane-head">
            <h2>{viewMode === "xray" ? "X-ray 拼装" : "单模型 3D"}</h2>
            <div className="locator-asset-pick">
              <div className="chip-row" role="tablist" aria-label="3D 模式">
                <button
                  type="button"
                  className={`chip${viewMode === "xray" ? " active" : ""}`}
                  disabled={!hasXray}
                  onClick={() => {
                    setViewMode("xray");
                    setPickedMesh(null);
                    setPickedMeshPath(null);
                    setPickedAssemblyId(null);
                    setMapMsg(null);
                  }}
                >
                  X-ray 拼装{hasXray ? "" : "（缺）"}
                </button>
                <button
                  type="button"
                  className={`chip${viewMode === "single" ? " active" : ""}`}
                  onClick={() => {
                    setViewMode("single");
                    setPickedMesh(null);
                    setPickedMeshPath(null);
                    setPickedAssemblyId(null);
                    setMapMsg(null);
                  }}
                >
                  单模型
                </button>
              </div>
              {viewMode === "single" ? (
                <>
                  <label>
                    模型
                    <select
                      value={assetId ?? ""}
                      onChange={(e) => {
                        setAssetId(e.target.value || null);
                        setPickedMesh(null);
                        setPickedMeshPath(null);
                      }}
                    >
                      {assets.length === 0 ? (
                        <option value="">（无模型清单）</option>
                      ) : null}
                      {assets.map((a) => (
                        <option key={a.id} value={a.id} disabled={!a.present}>
                          {a.label_zh}
                          {a.present ? "" : "（缺）"}
                          {a.browseOnly ? " · 对照" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={!assetId}
                    onClick={() =>
                      void api().openXrayTuneWindow?.(assetId ?? undefined)
                    }
                    title="单独微调窗口：模型树 + 单零件 TRS"
                  >
                    3D / 零件微调
                  </button>
                </>
              ) : (
                <>
                  <label className="locator-check">
                    <input
                      type="checkbox"
                      checked={showGhostBody}
                      onChange={(e) => setShowGhostBody(e.target.checked)}
                      disabled={!xrayScene?.bodyShell?.present}
                    />
                    幽灵车壳
                  </label>
                  <button type="button" onClick={() => void refreshXray()}>
                    刷新 transforms
                  </button>
                  <button
                    type="button"
                    onClick={() => void api().openXrayTuneWindow?.()}
                    title="3D / X-ray 姿态微调（挂在部件定位下）"
                  >
                    3D / X-ray 微调
                  </button>
                </>
              )}
            </div>
          </div>
          {viewMode === "xray" ? (
            <LocatorGlbViewer
              layers={xrayLayers}
              ghostBody={xrayScene?.bodyShell ?? null}
              showGhostBody={showGhostBody}
              bodyPaintHex={bodyPaintHex}
              bodyPaintPbr={bodyPaintPbr}
              softTopHex={softTopHex}
              interiorHex={interiorHex}
              readGlb={readGlb}
              selectedMeshName={pickedMesh}
              linkedMeshNames={linkedMeshNames}
              selectedAssemblyId={pickedAssemblyId}
              previewTransforms={previewTransforms}
              previewMeshState={meshState}
              onPickMesh={(name, path, assemblyId) => {
                setPickedMesh(name);
                setPickedMeshPath(path);
                setPickedAssemblyId(assemblyId);
                applyAssemblyBridge(assemblyId);
                setMapMsg(
                  assemblyId ? `已选总成 ${assemblyId}` : name ? `已选 ${name}` : null,
                );
              }}
            />
          ) : (
            <LocatorGlbViewer
              glbRel={glbRel}
              singleAssetId={assetId}
              interiorHex={interiorHex}
              readGlb={readGlb}
              selectedMeshName={pickedMesh}
              linkedMeshNames={linkedMeshNames}
              selectedAssemblyId={assetId}
              previewTransforms={previewTransforms}
              previewMeshState={meshState}
              onPickMesh={(name, path, assemblyId) => {
                setPickedMesh(name);
                setPickedMeshPath(path);
                setPickedAssemblyId(assemblyId);
                setMapMsg(name ? `已选 ${name}` : null);
              }}
            />
          )}
          <p className="muted locator-hint">
            {viewMode === "xray" ? (
              <>
                拖转旋转 · 滚轮缩放 · 单击模型选中总成
                {pickedAssemblyId ? (
                  <>
                    {" "}
                    · assembly <code>{pickedAssemblyId}</code>
                  </>
                ) : null}
                {pickedMesh ? (
                  <>
                    {" "}
                    · <code>{pickedMesh}</code>
                  </>
                ) : null}
              </>
            ) : (
              <>
                拖转旋转 · 滚轮缩放 · 单击 mesh 高亮
                {assetId ? (
                  <>
                    {" "}
                    · <code>{assetId}</code>
                  </>
                ) : null}
                {pickedMesh ? (
                  <>
                    {" "}
                    · 当前 <code>{pickedMesh}</code>
                  </>
                ) : null}
              </>
            )}
          </p>
          {viewMode === "xray" && xrayScene?.assemblies ? (
            <p className="muted locator-hint" style={{ fontSize: 11 }}>
              层：
              {xrayLayers.map((l: XrayAssembly) => l.id).join(", ") || "无"}
              {xrayScene.bodyShell?.present && showGhostBody
                ? " + body"
                : ""}
              {" · "}
              桥接 id：
              {xrayScene.assemblies.map((a) => a.id).join(", ")}
            </p>
          ) : null}
          {viewMode === "single" && activeAsset?.attribution ? (
            <p className="muted locator-hint" style={{ fontSize: 11 }}>
              致谢：{activeAsset.attribution}
            </p>
          ) : null}
          {viewMode === "xray" && xrayScene?.bodyShell?.attribution ? (
            <p className="muted locator-hint" style={{ fontSize: 11 }}>
              致谢：{xrayScene.bodyShell.attribution}
            </p>
          ) : null}
        </section>

        <section className="locator-pane">
          <div className="locator-pane-head">
            <h2>2D 爆炸图</h2>
            {!hasCms && <span className="locator-badge">待 CMS</span>}
          </div>
          <div
            className="locator-map"
            aria-label={`${zone.label_zh} locator`}
            style={{
              backgroundImage: `url("${zone.background.dataUrl}")`,
              backgroundSize: "cover",
              backgroundPosition: "center",
            }}
          >
            {zone.hotspots.map((h) => (
              <button
                key={h.id}
                type="button"
                className={
                  h.id === activeHotspot ? "hotspot active" : "hotspot"
                }
                style={{ left: `${h.x}%`, top: `${h.y}%` }}
                title={h.label}
                onClick={() => {
                  setActiveHotspot(h.id);
                  setSelectedPart(null);
                  setMapMsg(null);
                }}
              >
                <span className="hotspot-label">{h.label}</span>
              </button>
            ))}
          </div>
          <p className="muted locator-hint">
            {isPlaceholder ? (
              <>
                占位底图。overview → <code>data/{zone.shotHint}</code>
              </>
            ) : (
              <>已加载 PETKA 底图：<code>data/{zone.shotHint}</code></>
            )}
          </p>
        </section>

        <section className="locator-pane locator-side">
          <h2>
            {zone.label_zh} · {hotspotLabel}
          </h2>

          {activeBridgeJump ? (
            <p style={{ marginBottom: 12 }}>
              <button
                type="button"
                className="ghost"
                title={`跳到 ${activeBridgeJump.toZoneId} / ${activeBridgeJump.toHotspotId}`}
                onClick={() => applyBridgeJump(activeBridgeJump)}
              >
                打开零件锚点
              </button>
            </p>
          ) : null}

          <div className="locator-map-bar">
            <div>
              <div className="muted" style={{ fontSize: 12 }}>
                3D mesh
              </div>
              <code>{pickedMesh ?? "（未选）"}</code>
            </div>
            <div className="locator-map-arrow">↔</div>
            <div>
              <div className="muted" style={{ fontSize: 12 }}>
                2D 热点
              </div>
              <code>{activeHotspot ?? "（未选）"}</code>
            </div>
            <button
              type="button"
              disabled={mapBusy || !pickedMesh || !activeHotspot}
              onClick={() => void linkMesh()}
            >
              写入对应
            </button>
          </div>
          {selectedPart && (
            <p className="muted" style={{ fontSize: 12 }}>
              可选绑定 SKU：{selectedPart.sku}（点下方零件行）
            </p>
          )}
          {mapMsg && <p className="callout">{mapMsg}</p>}

          {zoneLinks.length > 0 && (
            <div className="locator-link-list">
              <h3>本区已对</h3>
              <table>
                <thead>
                  <tr>
                    <th>mesh</th>
                    <th>热点</th>
                    <th>SKU</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {zoneLinks.map((l) => (
                    <tr key={`${l.meshName}|${l.hotspotId}`}>
                      <td>
                        <button
                          type="button"
                          className="linkish"
                          onClick={() => {
                            setPickedMesh(l.meshName);
                            setActiveHotspot(l.hotspotId);
                          }}
                        >
                          {l.meshName}
                        </button>
                      </td>
                      <td>{l.hotspotId}</td>
                      <td>{l.sku ?? "—"}</td>
                      <td>
                        <button
                          type="button"
                          disabled={mapBusy}
                          onClick={() => void unlinkMesh(l.meshName)}
                        >
                          删
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 11 }}>
                落盘 <code>.local/cms-mesh-map.json</code>
              </p>
            </div>
          )}

          {relatedParts.length === 0 ? (
            <p className="muted">该锚点暂无关联零件</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>零件</th>
                  <th>OEM</th>
                  <th>系统</th>
                </tr>
              </thead>
              <tbody>
                {relatedParts.map((p) => (
                  <tr
                    key={p.id}
                    style={{
                      cursor: "pointer",
                      background:
                        selectedPart?.id === p.id ? "#f7f0ea" : undefined,
                    }}
                    onClick={() => setSelectedPart(p)}
                  >
                    <td>{p.name_zh}</td>
                    <td>{p.oem_number ?? "—"}</td>
                    <td>{p.system}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {selectedPart ? (
            <div className="locator-part-card">
              <h3>
                {selectedPart.name_zh}
                {needsPetkaPriceVerify(
                  selectedPart.notes,
                  selectedPart.price_note,
                ) ? (
                  <span className="pill warn" style={{ marginLeft: 8 }}>
                    待 PETKA
                  </span>
                ) : isPetkaPriceVerified(
                    selectedPart.notes,
                    selectedPart.price_note,
                  ) ? (
                  <span className="pill ok" style={{ marginLeft: 8 }}>
                    PETKA
                  </span>
                ) : null}
              </h3>
              <p className="muted">
                SKU {selectedPart.sku} · 锚点{" "}
                {selectedPart.locator_hotspot ?? "—"}
                {(() => {
                  const c = currencyFromPriceNote(selectedPart.price_note);
                  return c ? ` · 源 ${c} → 展示 CNY` : " · 展示 CNY";
                })()}
              </p>
              {needsVinPetkaConfirm(selectedPart.sku, selectedPart.notes) && (
                <div className="callout warn">
                  须 VIN + 本机 PETKA 终核 OEM；价留空，勿臆造。
                </div>
              )}
              <dl className="locator-part-dl">
                <div>
                  <dt>OEM</dt>
                  <dd>{selectedPart.oem_number ?? "—"}</dd>
                </div>
                <div>
                  <dt>系统</dt>
                  <dd>{selectedPart.system}</dd>
                </div>
                <div>
                  <dt>OEM 价</dt>
                  <dd>
                    {selectedOemDisp?.primary ?? "—"}
                    {selectedOemDisp?.detail ? (
                      <div className="muted" style={{ fontSize: 12 }}>
                        {selectedOemDisp.detail}
                      </div>
                    ) : null}
                  </dd>
                </div>
                <div>
                  <dt>副厂价</dt>
                  <dd>
                    {selectedAmDisp?.primary ?? "—"}
                    {selectedAmBrands ? (
                      <div className="muted" style={{ fontSize: 12 }}>
                        {selectedAmBrands}
                      </div>
                    ) : null}
                  </dd>
                </div>
                <div>
                  <dt>展示币种</dt>
                  <dd>CNY</dd>
                </div>
                <div>
                  <dt>报价日</dt>
                  <dd>{selectedPart.price_as_of ?? "—"}</dd>
                </div>
                <div>
                  <dt>间隔</dt>
                  <dd>
                    {selectedPart.interval_km ?? "—"} km /{" "}
                    {selectedPart.interval_months ?? "—"} 月
                  </dd>
                </div>
              </dl>
              {selectedPart.notes && (
                <div className="callout">{selectedPart.notes}</div>
              )}
              {selectedPart.price_note && (
                <p className="muted">来源：{selectedPart.price_note}</p>
              )}
              <h4 className="locator-svc-heading">更换史</h4>
              {partServiceHistory.length === 0 ? (
                <p className="muted">暂无更换记录</p>
              ) : (
                <table className="locator-svc-table">
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>公里</th>
                      <th>品牌</th>
                      <th>费用</th>
                    </tr>
                  </thead>
                  <tbody>
                    {partServiceHistory.map((r) => (
                      <tr key={r.id}>
                        <td>{r.replaced_at}</td>
                        <td>{r.odometer_km}</td>
                        <td>{r.brand ?? "—"}</td>
                        <td>{r.cost ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
