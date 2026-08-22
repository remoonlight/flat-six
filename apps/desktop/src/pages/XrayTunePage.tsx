import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type CmsMeshMap,
  type XrayMeshState,
  type XrayScene,
  type XrayTransform,
} from "../api";
import { LocatorGlbViewer, VIEW_PRESETS, type GizmoMode, type ViewPresetId } from "../components/LocatorGlbViewer";
import {
  sectionsFromLayers,
  sectionsFromScene,
  XrayModelTree,
  type XrayTreeSection,
} from "../components/XrayModelTree";
import {
  identityTransform,
  XrayTransformSliders,
} from "../components/XrayTransformSliders";

const DEG90 = Math.PI / 2;

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

function hashQuery(hash = window.location.hash): URLSearchParams {
  const raw = hash.replace(/^#/, "");
  const qIdx = raw.indexOf("?");
  if (qIdx < 0) return new URLSearchParams();
  return new URLSearchParams(raw.slice(qIdx + 1));
}

function tuneAssetFromHash(hash = window.location.hash): string | null {
  const id = hashQuery(hash).get("asset");
  return id?.trim() || null;
}

function tuneGarageFromHash(hash = window.location.hash): boolean {
  return hashQuery(hash).get("scene") === "garage";
}

function meshStateIdFor(
  assemblyId: string,
  scene: XrayScene | null,
  singleAssetId: string | null,
): string {
  if (singleAssetId) return assemblyId;
  if (assemblyId === "body") return "body";
  const a = scene?.assemblies.find((x) => x.id === assemblyId);
  return a?.meshStateOf || a?.aliasOfGlb || assemblyId;
}

/** Standalone window: Locator X-ray, garage 透视 (?scene=garage), or single GLB (?asset=). */
export function XrayTunePage() {
  const [tuneAssetId, setTuneAssetId] = useState(() => tuneAssetFromHash());
  const [garageMode, setGarageMode] = useState(() => tuneGarageFromHash());
  const [singleGlbRel, setSingleGlbRel] = useState<string | null>(null);
  const [singleLabelZh, setSingleLabelZh] = useState("");
  const [xrayScene, setXrayScene] = useState<XrayScene | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showGhostBody, setShowGhostBody] = useState(true);
  const [pickedAssemblyId, setPickedAssemblyId] = useState<string | null>(null);
  const [pickedMesh, setPickedMesh] = useState<string | null>(null);
  const [previewTransforms, setPreviewTransforms] = useState<
    Record<string, XrayTransform>
  >({});
  const [meshState, setMeshState] = useState<XrayMeshState | null>(null);
  const [meshesByAssembly, setMeshesByAssembly] = useState<
    Record<string, string[]>
  >({});
  const [meshMap, setMeshMap] = useState<CmsMeshMap | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());
  const [ready, setReady] = useState(false);
  const [gizmoMode, setGizmoMode] = useState<GizmoMode>("translate");
  const [viewPreset, setViewPreset] = useState<{
    id: ViewPresetId;
    seq: number;
  } | null>(null);
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const meshPersistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const onHash = () => {
      setTuneAssetId(tuneAssetFromHash());
      setGarageMode(tuneGarageFromHash());
      setReady(false);
      setErr(null);
      setMeshesByAssembly({});
      setPickedMesh(null);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setErr(null);

    if (tuneAssetId) {
      setGarageMode(false);
      Promise.all([
        api().cmsListAssets(),
        api().xrayGetMeshState(),
        api().xrayGetTransforms(),
        api().cmsGetMeshMap(),
      ])
        .then(([assets, ms, tf, map]) => {
          if (cancelled) return;
          const a = assets.find((x) => x.id === tuneAssetId);
          if (!a?.present || !a.rel) {
            setErr(`模型 ${tuneAssetId} 不可用（缺 GLB 或未在清单中）`);
            return;
          }
          setSingleGlbRel(a.rel);
          setSingleLabelZh(a.label_zh || tuneAssetId);
          setMeshState(ms);
          setMeshMap(map);
          setXrayScene(null);
          const t = tf.layers?.[tuneAssetId];
          setPreviewTransforms(t ? { [tuneAssetId]: t } : {});
          setPickedAssemblyId(tuneAssetId);
          setExpandedIds(new Set([tuneAssetId]));
          setReady(true);
        })
        .catch((e) => {
          if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
        });
      return () => {
        cancelled = true;
      };
    }

    setSingleGlbRel(null);
    setSingleLabelZh("");
    const loadScene = garageMode
      ? api().xrayGarageLayers()
      : api().xrayLayers();
    Promise.all([loadScene, api().xrayGetMeshState(), api().cmsGetMeshMap()])
      .then(([x, ms, map]) => {
        if (cancelled) return;
        setXrayScene(x);
        setMeshState(ms);
        setMeshMap(map);
        setShowGhostBody(!garageMode && Boolean(x.bodyShell?.present));
        const first =
          x.layers?.find((l) => l.present)?.id ??
          x.assemblies.find((a) => a.present)?.id ??
          "engine";
        setPickedAssemblyId(first);
        setExpandedIds(new Set([first]));
        setReady(true);
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tuneAssetId, garageMode]);

  useEffect(() => {
    if (!xrayScene || tuneAssetId) return;
    setPreviewTransforms((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const layer of xrayScene.layers ?? []) {
        if (!next[layer.id] && layer.transform) {
          next[layer.id] = layer.transform;
          changed = true;
        }
      }
      for (const a of xrayScene.assemblies) {
        if (!next[a.id] && a.transform) {
          next[a.id] = a.transform;
          changed = true;
        }
      }
      if (!next.body && xrayScene.bodyShell?.transform) {
        next.body = xrayScene.bodyShell.transform;
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [xrayScene, tuneAssetId]);

  const xrayLayers = useMemo(
    () => (xrayScene?.layers ?? []).filter((l) => l.present),
    [xrayScene],
  );

  const treeSections = useMemo((): XrayTreeSection[] => {
    if (tuneAssetId) {
      return [
        {
          id: tuneAssetId,
          labelZh: singleLabelZh || tuneAssetId,
          meshStateId: tuneAssetId,
          aliasNote: null,
        },
      ];
    }
    if (garageMode) {
      return sectionsFromLayers(
        xrayScene?.assemblies ?? [],
        Boolean(xrayScene?.bodyShell?.present),
      );
    }
    return sectionsFromScene({
      assemblies: xrayScene?.assemblies ?? [],
      bodyPresent: Boolean(xrayScene?.bodyShell?.present),
    });
  }, [tuneAssetId, singleLabelZh, xrayScene, garageMode]);

  const skuByMesh = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const link of meshMap?.links ?? []) {
      const asset = link.assetId || "";
      const key = `${asset || "?"}::${link.meshName}`;
      out[key] = link.sku ?? null;
      if (
        asset === "engine" ||
        asset === "susp" ||
        asset === "body" ||
        asset === "cabin981"
      ) {
        out[`${asset}::${link.meshName}`] = link.sku ?? null;
      }
      out[`susp::${link.meshName}`] =
        out[`susp::${link.meshName}`] ?? link.sku ?? null;
      if (tuneAssetId) {
        out[`${tuneAssetId}::${link.meshName}`] =
          out[`${tuneAssetId}::${link.meshName}`] ?? link.sku ?? null;
      }
    }
    return out;
  }, [meshMap, tuneAssetId]);

  const gizmoTargetId = pickedAssemblyId;
  const meshMode = Boolean(pickedMesh && gizmoTargetId);

  const gizmoLabelZh = useMemo(() => {
    if (!gizmoTargetId) return "";
    if (meshMode) return `${gizmoTargetId} / ${pickedMesh}`;
    if (tuneAssetId) return singleLabelZh || gizmoTargetId;
    if (!xrayScene) return gizmoTargetId;
    if (gizmoTargetId === "body") return "幽灵车壳";
    const a =
      xrayScene.assemblies.find((x) => x.id === gizmoTargetId) ??
      xrayScene.layers?.find((x) => x.id === gizmoTargetId);
    return a?.label_zh || a?.label || gizmoTargetId;
  }, [
    gizmoTargetId,
    xrayScene,
    meshMode,
    pickedMesh,
    tuneAssetId,
    singleLabelZh,
  ]);

  const gizmoValue = useMemo((): XrayTransform => {
    if (!gizmoTargetId) return identityTransform();
    if (meshMode && pickedMesh) {
      const sid = meshStateIdFor(gizmoTargetId, xrayScene, tuneAssetId);
      const entry = meshState?.layers?.[sid]?.meshes?.[pickedMesh];
      return entry?.transform ?? identityTransform();
    }
    if (previewTransforms[gizmoTargetId]) {
      return previewTransforms[gizmoTargetId];
    }
    if (tuneAssetId) return identityTransform();
    if (gizmoTargetId === "body") {
      return xrayScene?.bodyShell?.transform ?? identityTransform();
    }
    const a =
      xrayScene?.layers?.find((x) => x.id === gizmoTargetId) ??
      xrayScene?.assemblies.find((x) => x.id === gizmoTargetId);
    return a?.transform ?? identityTransform();
  }, [
    gizmoTargetId,
    previewTransforms,
    xrayScene,
    meshMode,
    pickedMesh,
    meshState,
    tuneAssetId,
  ]);

  const readGlb = useCallback(async (rel: string) => {
    const raw = await api().cmsReadGlb(rel);
    return toUint8(raw);
  }, []);

  function schedulePersist(assemblyId: string, transform: XrayTransform) {
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => {
      void api()
        .xraySetTransform(assemblyId, transform)
        .then(() => setMsg(`已保存层 ${assemblyId}`))
        .catch((e) => setMsg(e instanceof Error ? e.message : String(e)));
    }, 200);
  }

  function scheduleMeshPersist(
    assemblyId: string,
    meshName: string,
    transform: XrayTransform,
  ) {
    if (meshPersistTimer.current) clearTimeout(meshPersistTimer.current);
    meshPersistTimer.current = setTimeout(() => {
      void api()
        .xraySetMeshEntry(assemblyId, meshName, { transform })
        .then((s) => {
          setMeshState(s);
          setMsg(`已保存 mesh ${meshName}`);
        })
        .catch((e) => setMsg(e instanceof Error ? e.message : String(e)));
    }, 200);
  }

  function updateGizmoTransform(next: XrayTransform) {
    if (!gizmoTargetId) return;
    if (meshMode && pickedMesh) {
      const sid = meshStateIdFor(gizmoTargetId, xrayScene, tuneAssetId);
      setMeshState((prev) => {
        const layers = { ...(prev?.layers || {}) };
        const layer = {
          visible: layers[sid]?.visible !== false,
          meshes: { ...(layers[sid]?.meshes || {}) },
        };
        layer.meshes[pickedMesh] = {
          visible: layer.meshes[pickedMesh]?.visible !== false,
          transform: next,
        };
        layers[sid] = layer;
        return {
          version: 1,
          note: prev?.note || "",
          layers,
        };
      });
      scheduleMeshPersist(sid, pickedMesh, next);
      return;
    }
    setPreviewTransforms((prev) => ({ ...prev, [gizmoTargetId]: next }));
    schedulePersist(gizmoTargetId, next);
  }

  function resetGizmoTransform() {
    if (!gizmoTargetId) return;
    updateGizmoTransform(identityTransform());
  }

  function nudgeRotation(axis: 0 | 1 | 2, deltaRad: number) {
    const next = {
      position: [...gizmoValue.position] as [number, number, number],
      rotationEuler: [...gizmoValue.rotationEuler] as [number, number, number],
      scale: [...gizmoValue.scale] as [number, number, number],
    };
    next.rotationEuler[axis] += deltaRad;
    updateGizmoTransform(next);
  }

  function applyView(id: ViewPresetId) {
    setViewPreset((prev) => ({ id, seq: (prev?.seq ?? 0) + 1 }));
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }
      const k = e.key.toLowerCase();
      if (k === "w") setGizmoMode("translate");
      else if (k === "e") setGizmoMode("rotate");
      else if (k === "r") setGizmoMode("scale");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function refresh() {
    try {
      if (tuneAssetId) {
        const [assets, ms, tf, map] = await Promise.all([
          api().cmsListAssets(),
          api().xrayGetMeshState(),
          api().xrayGetTransforms(),
          api().cmsGetMeshMap(),
        ]);
        const a = assets.find((x) => x.id === tuneAssetId);
        if (a?.rel) setSingleGlbRel(a.rel);
        setMeshState(ms);
        setMeshMap(map);
        const t = tf.layers?.[tuneAssetId];
        if (t) setPreviewTransforms({ [tuneAssetId]: t });
        setMsg("已刷新");
        return;
      }
      const loadScene = garageMode
        ? api().xrayGarageLayers()
        : api().xrayLayers();
      const [next, ms, map] = await Promise.all([
        loadScene,
        api().xrayGetMeshState(),
        api().cmsGetMeshMap(),
      ]);
      setXrayScene(next);
      setMeshState(ms);
      setMeshMap(map);
      setMsg("已刷新");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveSku(assemblyId: string, meshName: string, sku: string) {
    try {
      const sid = meshStateIdFor(assemblyId, xrayScene, tuneAssetId);
      let zoneId = assemblyId;
      let hotspotId = assemblyId;
      if (!tuneAssetId) {
        const bridge = await api().xrayBridge(assemblyId);
        zoneId = bridge?.zoneId || assemblyId;
        hotspotId = bridge?.hotspotId || assemblyId;
      }
      const map = await api().cmsUpsertMeshLink({
        zoneId,
        hotspotId,
        meshName,
        assetId: sid,
        sku: sku || null,
      });
      setMeshMap(map);
      setMsg(sku ? `已存 OEM ${meshName} → ${sku}` : `已清空 OEM ${meshName}`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  const title = tuneAssetId
    ? `模型姿态微调 · ${singleLabelZh || tuneAssetId}`
    : garageMode
      ? "车库透视 · 姿态微调"
      : "X-ray 姿态微调";

  if (err) {
    return (
      <div className="xray-tune">
        <h1>{title}</h1>
        <p className="error">{err}</p>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="xray-tune">
        <h1>{title}</h1>
        <p className="muted">加载中…</p>
      </div>
    );
  }

  return (
    <div className="xray-tune" data-page="xray-tune">
      <header className="xray-tune-head">
        <h1>{title}</h1>
        <div className="xray-tune-actions">
          {!tuneAssetId ? (
            <label className="locator-check">
              <input
                type="checkbox"
                checked={showGhostBody}
                onChange={(e) => setShowGhostBody(e.target.checked)}
                disabled={!xrayScene?.bodyShell?.present}
              />
              幽灵车壳
            </label>
          ) : null}
          <button type="button" onClick={() => void refresh()}>
            刷新
          </button>
        </div>
      </header>

      {!tuneAssetId && xrayScene?.caveatZh ? (
        <p className="muted xray-tune-caveat">{xrayScene.caveatZh}</p>
      ) : null}
      {tuneAssetId ? (
        <p className="muted xray-tune-caveat">
          单模型零件微调 · 层 TRS →{" "}
          <code>xray-transforms.json[{tuneAssetId}]</code> · 零件 →{" "}
          <code>xray-mesh-state.json</code>
        </p>
      ) : null}
      {garageMode && !tuneAssetId ? (
        <p className="muted xray-tune-caveat">
          车库透视：CMS 引擎 + PETKA 底盘/排气 · 缩放为整体等比 · 落盘{" "}
          <code>xray-transforms.json</code>
        </p>
      ) : null}

      <div className="xray-tune-main">
        <div className="xray-tune-viewer">
          <div className="xray-tune-viewport-bar" role="toolbar" aria-label="视口工具">
            <div className="xray-tune-toolbar-group">
              <span className="xray-tune-toolbar-label">视角</span>
              {VIEW_PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={
                    viewPreset?.id === p.id ? "is-active" : undefined
                  }
                  onClick={() => applyView(p.id)}
                >
                  {p.labelZh}
                </button>
              ))}
            </div>
            <div className="xray-tune-toolbar-group">
              <span className="xray-tune-toolbar-label">Gizmo</span>
              {(
                [
                  ["translate", "移动", "W"],
                  ["rotate", "旋转", "E"],
                  ["scale", "缩放", "R"],
                ] as const
              ).map(([mode, label, key]) => (
                <button
                  key={mode}
                  type="button"
                  className={gizmoMode === mode ? "is-active" : undefined}
                  disabled={!gizmoTargetId}
                  title={`${label} (${key})`}
                  onClick={() => setGizmoMode(mode)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="xray-tune-toolbar-group">
              <span className="xray-tune-toolbar-label">转 ±90°</span>
              {(
                [
                  ["X+", 0, DEG90],
                  ["X−", 0, -DEG90],
                  ["Y+", 1, DEG90],
                  ["Y−", 1, -DEG90],
                  ["Z+", 2, DEG90],
                  ["Z−", 2, -DEG90],
                ] as const
              ).map(([label, axis, delta]) => (
                <button
                  key={label}
                  type="button"
                  disabled={!gizmoTargetId}
                  onClick={() => nudgeRotation(axis, delta)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {tuneAssetId && singleGlbRel ? (
            <LocatorGlbViewer
              glbRel={singleGlbRel}
              singleAssetId={tuneAssetId}
              readGlb={readGlb}
              selectedMeshName={pickedMesh}
              selectedAssemblyId={tuneAssetId}
              previewTransforms={previewTransforms}
              previewMeshState={meshState}
              onMeshesReady={setMeshesByAssembly}
              gizmoEnabled
              gizmoMode={gizmoMode}
              gizmoAssemblyId={gizmoTargetId}
              gizmoMeshName={pickedMesh}
              viewPreset={viewPreset}
              onGizmoTransformChange={(assemblyId, transform, meshName) => {
                setPickedAssemblyId(assemblyId);
                if (meshName) {
                  setPickedMesh(meshName);
                  const sid = meshStateIdFor(assemblyId, xrayScene, tuneAssetId);
                  setMeshState((prev) => {
                    const layers = { ...(prev?.layers || {}) };
                    const layer = {
                      visible: layers[sid]?.visible !== false,
                      meshes: { ...(layers[sid]?.meshes || {}) },
                    };
                    layer.meshes[meshName] = {
                      visible: layer.meshes[meshName]?.visible !== false,
                      transform,
                    };
                    layers[sid] = layer;
                    return {
                      version: 1,
                      note: prev?.note || "",
                      layers,
                    };
                  });
                  scheduleMeshPersist(sid, meshName, transform);
                } else {
                  setPickedMesh(null);
                  setPreviewTransforms((prev) => ({
                    ...prev,
                    [assemblyId]: transform,
                  }));
                  schedulePersist(assemblyId, transform);
                }
              }}
              onPickMesh={(name, _path, assemblyId) => {
                setPickedMesh(name);
                setPickedAssemblyId(assemblyId || tuneAssetId);
                setExpandedIds((prev) =>
                  new Set(prev).add(assemblyId || tuneAssetId),
                );
                setMsg(`已选 ${tuneAssetId}/${name}，拖 Gizmo 或滑条调 TRS`);
              }}
            />
          ) : (
            <LocatorGlbViewer
              layers={xrayLayers}
              ghostBody={xrayScene?.bodyShell ?? null}
              showGhostBody={showGhostBody}
              readGlb={readGlb}
              selectedMeshName={pickedMesh}
              selectedAssemblyId={
                pickedAssemblyId
                  ? meshStateIdFor(pickedAssemblyId, xrayScene, null)
                  : null
              }
              previewTransforms={previewTransforms}
              previewMeshState={meshState}
              onMeshesReady={setMeshesByAssembly}
              gizmoEnabled
              gizmoMode={gizmoMode}
              gizmoAssemblyId={gizmoTargetId}
              gizmoMeshName={pickedMesh}
              viewPreset={viewPreset}
              onGizmoTransformChange={(assemblyId, transform, meshName) => {
                setPickedAssemblyId(assemblyId);
                if (meshName) {
                  setPickedMesh(meshName);
                  const sid = meshStateIdFor(assemblyId, xrayScene, null);
                  setMeshState((prev) => {
                    const layers = { ...(prev?.layers || {}) };
                    const layer = {
                      visible: layers[sid]?.visible !== false,
                      meshes: { ...(layers[sid]?.meshes || {}) },
                    };
                    layer.meshes[meshName] = {
                      visible: layer.meshes[meshName]?.visible !== false,
                      transform,
                    };
                    layers[sid] = layer;
                    return {
                      version: 1,
                      note: prev?.note || "",
                      layers,
                    };
                  });
                  scheduleMeshPersist(sid, meshName, transform);
                } else {
                  setPickedMesh(null);
                  setPreviewTransforms((prev) => ({
                    ...prev,
                    [assemblyId]: transform,
                  }));
                  schedulePersist(assemblyId, transform);
                }
              }}
              onPickMesh={(name, _path, assemblyId) => {
                setPickedMesh(name);
                if (assemblyId) {
                  setPickedAssemblyId(assemblyId);
                  setExpandedIds((prev) => new Set(prev).add(assemblyId));
                }
                setMsg(
                  assemblyId
                    ? `已选 ${assemblyId}/${name}，拖 Gizmo 或滑条调 TRS`
                    : null,
                );
              }}
            />
          )}
        </div>

        <XrayModelTree
          sections={treeSections}
          meshesByAssembly={meshesByAssembly}
          meshState={meshState}
          skuByMesh={skuByMesh}
          expandedIds={expandedIds}
          pickedAssemblyId={pickedAssemblyId}
          pickedMesh={pickedMesh}
          onToggleExpand={(id) => {
            setExpandedIds((prev) => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            });
          }}
          onSelectLayer={(id) => {
            setPickedAssemblyId(id);
            setPickedMesh(null);
            setMsg(`已选层 ${id}`);
          }}
          onSelectMesh={(assemblyId, meshName) => {
            setPickedAssemblyId(assemblyId);
            setPickedMesh(meshName);
            setExpandedIds((prev) => new Set(prev).add(assemblyId));
            setMsg(`已选 ${assemblyId}/${meshName}`);
          }}
          onLayerVisible={(sid, visible) => {
            setMeshState((prev) => {
              const layers = { ...(prev?.layers || {}) };
              layers[sid] = {
                visible,
                meshes: { ...(layers[sid]?.meshes || {}) },
              };
              return { version: 1, note: prev?.note || "", layers };
            });
            void api()
              .xraySetLayerVisible(sid, visible)
              .then(setMeshState)
              .catch((e) =>
                setMsg(e instanceof Error ? e.message : String(e)),
              );
          }}
          onMeshVisible={(sid, meshName, visible) => {
            setMeshState((prev) => {
              const layers = { ...(prev?.layers || {}) };
              const layer = {
                visible: layers[sid]?.visible !== false,
                meshes: { ...(layers[sid]?.meshes || {}) },
              };
              const prevMesh = layer.meshes[meshName];
              layer.meshes[meshName] = {
                visible,
                transform: prevMesh?.transform ?? identityTransform(),
              };
              layers[sid] = layer;
              return { version: 1, note: prev?.note || "", layers };
            });
            void api()
              .xraySetMeshEntry(sid, meshName, { visible })
              .then(setMeshState)
              .catch((e) =>
                setMsg(e instanceof Error ? e.message : String(e)),
              );
          }}
          onSkuSave={saveSku}
        />
      </div>

      <p className="muted locator-hint">
        右侧点大块调层 · 展开调零件 · 视口拖 RGB 轴 / W·E·R 切换模式 · world = 层 ×
        mesh · 落盘 <code>.local/xray-transforms.json</code> /{" "}
        <code>.local/xray-mesh-state.json</code>
        {pickedMesh ? (
          <>
            {" "}
            · mesh <code>{pickedMesh}</code>
          </>
        ) : null}
        {msg ? <> · {msg}</> : null}
      </p>

      {gizmoTargetId ? (
        <XrayTransformSliders
          assemblyId={
            meshMode && pickedMesh
              ? `${gizmoTargetId}/${pickedMesh}`
              : gizmoTargetId
          }
          labelZh={gizmoLabelZh}
          value={gizmoValue}
          onChange={updateGizmoTransform}
          onReset={resetGizmoTransform}
          persistHint={
            meshMode
              ? ".local/xray-mesh-state.json"
              : ".local/xray-transforms.json"
          }
          uniformScale
        />
      ) : (
        <p className="muted">请选择微调层或零件</p>
      )}
    </div>
  );
}
