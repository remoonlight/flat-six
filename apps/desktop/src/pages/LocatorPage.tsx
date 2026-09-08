import { useCallback, useEffect, useMemo, useState } from "react";
import {
  resolveBodyPaintHex,
  resolveBodyPaintPbr,
  resolveInteriorHex,
  resolveSoftTopHex,
} from "@porsche981/domain";
import {
  api,
  type CmsAsset,
  type CmsMeshMap,
  type Vehicle,
  type XrayMeshState,
  type XrayScene,
  type XrayTransform,
} from "../api";
import { LocatorGlbViewer } from "../components/LocatorGlbViewer";

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
  /** Optional deep-link from fault KB (R5)：切到该区默认 GLB。 */
  initialZoneId?: string | null;
  initialHotspotId?: string | null;
  initialSku?: string | null;
};

export function LocatorPage({
  initialZoneId = null,
}: LocatorPageProps = {}) {
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [assets, setAssets] = useState<CmsAsset[]>([]);
  const [assetId, setAssetId] = useState<string | null>(null);
  const [meshMap, setMeshMap] = useState<CmsMeshMap | null>(null);
  const [pickedMesh, setPickedMesh] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"single" | "xray">("single");
  const [xrayScene, setXrayScene] = useState<XrayScene | null>(null);
  const [meshState, setMeshState] = useState<XrayMeshState | null>(null);
  const [showGhostBody, setShowGhostBody] = useState(true);
  const [pickedAssemblyId, setPickedAssemblyId] = useState<string | null>(null);
  const [previewTransforms, setPreviewTransforms] = useState<
    Record<string, XrayTransform>
  >({});
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);

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
      if (!next.body && xrayScene.bodyShell?.transform) {
        next.body = xrayScene.bodyShell.transform;
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [xrayScene]);

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
      api().locatorMap().catch(() => null),
      api().cmsListAssets().catch(() => [] as CmsAsset[]),
      api().cmsGetMeshMap().catch(() => null),
      api().xrayLayers().catch(() => null),
      api().xrayGetMeshState().catch(() => null),
      api().getVehicle().catch(() => null),
    ])
      .then(([m, cmsAssets, mm, xray, ms, v]) => {
        setAssets(cmsAssets);
        if (mm) setMeshMap(mm);
        if (xray) setXrayScene(xray);
        if (ms) setMeshState(ms);
        if (v) setVehicle(v);

        const focusZone =
          (initialZoneId && m?.zones.find((z) => z.id === initialZoneId)) ||
          null;
        const zone = focusZone ?? m?.zones[0] ?? null;
        setAssetId(zone?.cms?.assetId ?? cmsAssets.find((a) => a.present)?.id ?? null);
        setReady(true);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [initialZoneId]);

  useEffect(() => {
    if (xrayScene && (xrayScene.layers?.length ?? 0) === 0 && viewMode === "xray") {
      setViewMode("single");
    }
  }, [xrayScene, viewMode]);

  const activeAsset = useMemo(() => {
    if (!assetId) return null;
    return assets.find((a) => a.id === assetId) ?? null;
  }, [assets, assetId]);

  const glbRel = activeAsset?.present ? activeAsset.rel : null;

  const xrayLayers = useMemo(
    () => (xrayScene?.layers ?? []).filter((l) => l.present),
    [xrayScene],
  );

  const linkedMeshNames = useMemo(() => {
    if (!meshMap) return new Set<string>();
    return new Set(meshMap.links.map((l) => l.meshName));
  }, [meshMap]);

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

  if (err) {
    return (
      <div>
        <h1>部件定位</h1>
        <p className="error">{err}</p>
      </div>
    );
  }

  if (!ready) {
    return (
      <div>
        <h1>部件定位</h1>
        <p className="muted">加载 3D…</p>
      </div>
    );
  }

  const hasXray = xrayLayers.length > 0;

  return (
    <div>
      <h1>部件定位</h1>

      <div className="locator-dual">
        <section className="locator-pane">
          <div className="locator-pane-head">
            <h2>{viewMode === "xray" ? "X-ray 拼装" : "单模型 3D"}</h2>
            <div className="locator-asset-pick">
              <div className="chip-row" role="tablist" aria-label="3D 模式">
                <button
                  type="button"
                  className={`chip${viewMode === "single" ? " active" : ""}`}
                  onClick={() => {
                    setViewMode("single");
                    setPickedMesh(null);
                    setPickedAssemblyId(null);
                  }}
                >
                  单模型 3D
                </button>
                <button
                  type="button"
                  className={`chip${viewMode === "xray" ? " active" : ""}`}
                  disabled={!hasXray}
                  onClick={() => {
                    setViewMode("xray");
                    setPickedMesh(null);
                    setPickedAssemblyId(null);
                  }}
                >
                  X-ray 拼装{hasXray ? "" : "（缺）"}
                </button>
              </div>
              {viewMode === "single" ? (
                <>
                  <label>
                    模型显示
                    <select
                      value={assetId ?? ""}
                      onChange={(e) => {
                        setAssetId(e.target.value || null);
                        setPickedMesh(null);
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
                    模型微调
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
                  <button
                    type="button"
                    onClick={() => void api().openXrayTuneWindow?.()}
                    title="3D / X-ray 姿态微调（挂在部件定位下）"
                  >
                    模型微调
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
              onPickMesh={(name, _path, assemblyId) => {
                setPickedMesh(name);
                setPickedAssemblyId(assemblyId);
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
              onPickMesh={(name, _path, assemblyId) => {
                setPickedMesh(name);
                setPickedAssemblyId(assemblyId);
              }}
            />
          )}
          <p className="muted locator-hint">
            拖转旋转 · 滚轮缩放
            {viewMode === "xray" && pickedAssemblyId ? (
              <>
                {" "}
                · <code>{pickedAssemblyId}</code>
              </>
            ) : null}
            {viewMode === "single" && assetId ? (
              <>
                {" "}
                · <code>{assetId}</code>
              </>
            ) : null}
            {pickedMesh ? (
              <>
                {" "}
                · <code>{pickedMesh}</code>
              </>
            ) : null}
          </p>
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
      </div>
    </div>
  );
}
