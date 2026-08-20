import type { XrayAssembly, XrayMeshState } from "../api";

export type XrayTreeSection = {
  id: string;
  labelZh: string;
  /** Load-layer id used for mesh-state keys (driveline → susp). */
  meshStateId: string;
  aliasNote?: string | null;
  /** 分组头（同组连续项只在首项带标题） */
  groupId?: string | null;
  groupZh?: string | null;
  showGroupHeader?: boolean;
};

export type XrayModelTreeProps = {
  sections: XrayTreeSection[];
  meshesByAssembly: Record<string, string[]>;
  meshState: XrayMeshState | null;
  skuByMesh: Record<string, string | null>;
  expandedIds: Set<string>;
  pickedAssemblyId: string | null;
  pickedMesh: string | null;
  onToggleExpand: (id: string) => void;
  onSelectLayer: (id: string) => void;
  onSelectMesh: (assemblyId: string, meshName: string) => void;
  onLayerVisible: (meshStateId: string, visible: boolean) => void;
  onMeshVisible: (
    meshStateId: string,
    meshName: string,
    visible: boolean,
  ) => void;
  onSkuSave: (
    assemblyId: string,
    meshName: string,
    sku: string,
  ) => void | Promise<void>;
};

export function sectionsFromScene(input: {
  assemblies: XrayAssembly[];
  bodyPresent: boolean;
}): XrayTreeSection[] {
  // 新装配为 PETKA 分组层；旧 CMS 三件仍兼容
  const hasPetka = input.assemblies.some(
    (a) => a.xrayGroup === "power" || a.xrayGroup === "drive" || a.id.startsWith("pm-"),
  );
  if (hasPetka) {
    return sectionsFromLayers(input.assemblies, input.bodyPresent);
  }
  const want = ["engine", "susp", "driveline"] as const;
  const out: XrayTreeSection[] = [];
  for (const id of want) {
    const a = input.assemblies.find((x) => x.id === id);
    if (!a) continue;
    const present = a.present || Boolean(a.aliasOfGlb);
    if (!present) continue;
    if (a.aliasOfGlb) {
      const src = input.assemblies.find((x) => x.id === a.aliasOfGlb);
      if (!src?.present) continue;
    }
    out.push({
      id,
      labelZh: a.label_zh || a.label,
      meshStateId: a.aliasOfGlb || a.id,
      aliasNote: a.aliasOfGlb ? `同 ${a.aliasOfGlb} GLB` : null,
    });
  }
  if (input.bodyPresent) {
    out.push({
      id: "body",
      labelZh: "幽灵车壳",
      meshStateId: "body",
      aliasNote: null,
    });
  }
  return out;
}

const GROUP_ORDER = ["power", "drive"] as const;
const GROUP_ZH: Record<string, string> = {
  power: "引擎+燃油系统",
  drive: "传动系统+前后轮",
};

/** PETKA / 车库层：按 xrayGroup 排序并打分组头。 */
export function sectionsFromLayers(
  assemblies: readonly XrayAssembly[],
  bodyPresent = false,
): XrayTreeSection[] {
  const loadable = assemblies.filter(
    (a) => !a.aliasOfGlb && a.present !== false && a.loadLayer !== false,
  );
  const present = loadable.filter((a) => a.present);
  const sorted = [...present].sort((a, b) => {
    const ga = a.xrayGroup || "zz";
    const gb = b.xrayGroup || "zz";
    const ia = GROUP_ORDER.indexOf(ga as (typeof GROUP_ORDER)[number]);
    const ib = GROUP_ORDER.indexOf(gb as (typeof GROUP_ORDER)[number]);
    const oa = ia < 0 ? 99 : ia;
    const ob = ib < 0 ? 99 : ib;
    if (oa !== ob) return oa - ob;
    return a.id.localeCompare(b.id);
  });

  const out: XrayTreeSection[] = [];
  let prevGroup: string | null = null;
  for (const a of sorted) {
    const groupId = a.xrayGroup || null;
    const groupZh =
      a.xrayGroupZh || (groupId ? GROUP_ZH[groupId] : null) || groupId;
    const showGroupHeader = Boolean(groupId && groupId !== prevGroup);
    if (groupId) prevGroup = groupId;
    out.push({
      id: a.id,
      labelZh: a.label_zh || a.label || a.id,
      meshStateId: a.meshStateOf || a.aliasOfGlb || a.id,
      aliasNote: a.meshStateOf
        ? `子 mesh 同 ${a.meshStateOf} · 层位姿独立`
        : a.aliasOfGlb
          ? `同 ${a.aliasOfGlb} GLB`
          : null,
      groupId,
      groupZh,
      showGroupHeader,
    });
  }
  if (bodyPresent) {
    out.push({
      id: "body",
      labelZh: "幽灵车壳",
      meshStateId: "body",
      aliasNote: null,
      groupId: "body",
      groupZh: "车壳",
      showGroupHeader: true,
    });
  }
  return out;
}

export function XrayModelTree({
  sections,
  meshesByAssembly,
  meshState,
  skuByMesh,
  expandedIds,
  pickedAssemblyId,
  pickedMesh,
  onToggleExpand,
  onSelectLayer,
  onSelectMesh,
  onLayerVisible,
  onMeshVisible,
  onSkuSave,
}: XrayModelTreeProps) {
  return (
    <aside className="xray-tune-side panel" aria-label="3D 模型列表">
      <h3 className="xray-tune-side-title">模型列表</h3>
      {sections.map((sec) => {
        const open = expandedIds.has(sec.id);
        const layerVis =
          meshState?.layers?.[sec.id]?.visible !== false;
        const meshes =
          meshesByAssembly[sec.id]?.length
            ? meshesByAssembly[sec.id]
            : (meshesByAssembly[sec.meshStateId] || []);
        const layerSelected =
          pickedAssemblyId === sec.id && !pickedMesh;
        return (
          <div key={sec.id}>
            {sec.showGroupHeader && sec.groupZh ? (
              <div className="xray-tree-group">{sec.groupZh}</div>
            ) : null}
            <div
              className={
                "xray-tree-sec" + (layerSelected ? " xray-tree-sec-active" : "")
              }
            >
            <div className="xray-tree-sec-head">
              <button
                type="button"
                className="xray-tree-expand"
                aria-expanded={open}
                onClick={() => onToggleExpand(sec.id)}
              >
                {open ? "▼" : "▶"}
              </button>
              <button
                type="button"
                className="xray-tree-layer"
                onClick={() => onSelectLayer(sec.id)}
              >
                {sec.labelZh} <code>{sec.id}</code>
              </button>
              <label className="xray-tree-vis" title="显示整层">
                <input
                  type="checkbox"
                  checked={layerVis}
                  onChange={(e) =>
                    onLayerVisible(sec.id, e.target.checked)
                  }
                />
              </label>
            </div>
            {sec.aliasNote ? (
              <p className="muted xray-tree-alias">{sec.aliasNote}</p>
            ) : null}
            {open ? (
              <ul className="xray-tree-meshes">
                {meshes.length === 0 ? (
                  <li className="muted">无 mesh（等 GLB 加载）</li>
                ) : (
                  meshes.map((name) => {
                    const entry = meshState?.layers?.[sec.meshStateId]?.meshes?.[
                      name
                    ];
                    const meshVis = entry?.visible !== false;
                    const selected =
                      pickedAssemblyId === sec.id && pickedMesh === name;
                    const sku = skuByMesh[`${sec.meshStateId}::${name}`] ?? "";
                    return (
                      <li
                        key={name}
                        className={
                          "xray-tree-mesh" +
                          (selected ? " xray-tree-mesh-active" : "")
                        }
                      >
                        <button
                          type="button"
                          className="xray-tree-mesh-name"
                          onClick={() => onSelectMesh(sec.id, name)}
                        >
                          {name}
                        </button>
                        <label className="xray-tree-vis" title="显示">
                          <input
                            type="checkbox"
                            checked={meshVis}
                            onChange={(e) =>
                              onMeshVisible(
                                sec.meshStateId,
                                name,
                                e.target.checked,
                              )
                            }
                          />
                        </label>
                        <form
                          className="xray-tree-sku"
                          onSubmit={(e) => {
                            e.preventDefault();
                            const fd = new FormData(e.currentTarget);
                            const next = String(fd.get("sku") || "").trim();
                            void onSkuSave(sec.id, name, next);
                          }}
                        >
                          <input
                            name="sku"
                            defaultValue={sku}
                            key={`${name}:${sku}`}
                            placeholder="OEM"
                            title="OEM 零件号"
                          />
                          <button type="submit">存</button>
                        </form>
                      </li>
                    );
                  })
                )}
              </ul>
            ) : null}
            </div>
          </div>
        );
      })}
    </aside>
  );
}
