import { useCallback, useEffect, useMemo, useState } from "react";
import {
  buildGarageConsumablesList,
  collectSkusForModelSelection,
  findModelsForSku,
  findSkuByOemNumber,
  formatModelChildCaption,
  formatModelOemPointer,
  applyIntervalKindMarker,
  resolveBodyPaintHex,
  resolveBodyPaintPbr,
  resolveInteriorHex,
  resolveModelOemSkus,
  resolveSoftTopHex,
  suggestOemByInput,
  type FxTable,
} from "@porsche981/domain";
import {
  LocatorGlbViewer,
  INTERIOR_ZONES,
  isBodyInteriorMeshName,
  isBodyPaintMeshName,
  type GarageStructureId,
  type GarageViewMode,
  type InteriorZoneId,
} from "../components/LocatorGlbViewer";
import {
  api,
  type GarageFlowSystem,
  type ModelOemKind,
  type ModelOemLink,
  type Part,
  type Vehicle,
  type XrayAssembly,
  type XrayMeshState,
  type XrayScene,
  type XrayTransform,
} from "../api";
import {
  DEFAULT_FX_TABLE,
  formatMoney,
  formatMoneyDisplay,
} from "../price";

/** flat-six X-RAY 结构层（车库透视芯片）。 */
const XRAY_STRUCTURE: { id: GarageStructureId; labelZh: string }[] = [
  { id: "all", labelZh: "全部" },
  { id: "mechanical", labelZh: "机械" },
  { id: "air", labelZh: "进排气" },
  { id: "lines", labelZh: "管路" },
  { id: "vacuum", labelZh: "真空" },
  { id: "wiring", labelZh: "线束" },
];

type RightTab = "model" | "consumables";

type ModelListItem = {
  key: string;
  label: string;
  kind: "mesh" | "assembly" | "flow";
  /** 关联键：mesh 名 / 装配 id / flow id */
  ref: string;
  meshName?: string;
  assemblyId?: string | null;
  /** 指向 OEM 展示；无映射为「未关联」 */
  oemPointer: string;
  linkedSkus: string[];
};

type ModelListItemBase = Omit<ModelListItem, "oemPointer" | "linkedSkus">;

function formatConsumableLife(p: {
  interval_km: number | null;
  interval_months: number | null;
} | null): string {
  if (!p) return "—";
  const km =
    p.interval_km != null ? `${p.interval_km.toLocaleString("zh-CN")} km` : null;
  const mo = p.interval_months != null ? `${p.interval_months} 月` : null;
  if (!km && !mo) return "—";
  return [km, mo].filter(Boolean).join(" / ");
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
  throw new Error("cmsReadGlb: unexpected payload");
}

function filterNames(names: readonly string[], match: RegExp | null): string[] {
  if (!match) return [...names].sort();
  return names.filter((n) => match.test(n)).sort();
}

/** 点选装配/子 mesh 时保留气路等非机械分栏（读 seed garageStructure）。 */
function structureForAssemblyId(
  layers: readonly XrayAssembly[],
  assemblyId: string,
): GarageStructureId {
  const gs = layers.find((l) => l.id === assemblyId)?.garageStructure;
  if (
    gs === "cabin" ||
    gs === "air" ||
    gs === "lines" ||
    gs === "vacuum" ||
    gs === "wiring"
  ) {
    return gs;
  }
  return "mechanical";
}

function isInteriorZoneAssembly(
  layers: readonly XrayAssembly[],
  assemblyId: string,
): boolean {
  return Boolean(layers.find((l) => l.id === assemblyId)?.interiorZone);
}

/** 透视「全部」点选装配不切子分栏；已在子分栏时对齐到装配所属层。 */
function xrayStructureForAssemblyPick(
  layers: readonly XrayAssembly[],
  assemblyId: string,
  currentStructure: GarageStructureId,
): GarageStructureId {
  if (currentStructure === "all") return "all";
  return structureForAssemblyId(layers, assemblyId);
}

/** 按左侧当前模式筛出模型块（mesh / 装配 / flow）。外观/内饰固定「全部」。 */
function modelItemsForView(input: {
  mode: GarageViewMode;
  xrayStructure: GarageStructureId;
  interiorZone: InteriorZoneId;
  meshesByAsm: Record<string, string[]>;
  layers: readonly XrayAssembly[];
  flows: readonly GarageFlowSystem[];
}): ModelListItemBase[] {
  const { mode, meshesByAsm, layers, flows, interiorZone } = input;
  const body = meshesByAsm.body ?? [];

  if (mode === "exterior") {
    // 外观 = 车壳全部子 mesh，仅排除 SM_Interior 座舱块（平铺）
    return filterNames(body, null)
      .filter((n) => !isBodyInteriorMeshName(n))
      .map((n) => ({
        key: `mesh:${n}`,
        label: n,
        kind: "mesh" as const,
        ref: n,
        meshName: n,
        assemblyId: "body",
      }));
  }

  if (mode === "interior") {
    const zone =
      INTERIOR_ZONES.find((z) => z.id === interiorZone) ?? INTERIOR_ZONES[0];
    const petkaItems: ModelListItemBase[] = layers
      .filter((l) => l.interiorZone)
      .filter(
        (l) => interiorZone === "all" || l.interiorZone === interiorZone,
      )
      .map((l) => ({
        key: `asm:${l.id}`,
        label: l.label_zh || l.label || l.id,
        kind: "assembly" as const,
        ref: l.id,
        assemblyId: l.id,
      }));
    const bodyMatch =
      zone.id === "all" ? /SM_Interior/i : zone.match;
    const bodyItems =
      bodyMatch == null
        ? []
        : filterNames(body, bodyMatch)
            .filter((n) => !isBodyPaintMeshName(n))
            .map((n) => ({
              key: `mesh:${n}`,
              label: n,
              kind: "mesh" as const,
              ref: n,
              meshName: n,
              assemblyId: "body",
            }));
    return [...petkaItems, ...bodyItems];
  }

  // xray：garageStructure 非空则归内饰/气路/管路/真空/线束；否则机械
  const flowStructs = new Set(["cabin", "air", "lines", "vacuum", "wiring"]);
  const xrayLayers = layers.filter((l) => !l.interiorZone);
  const mechLayers = xrayLayers.filter(
    (l) => !flowStructs.has(String(l.garageStructure || "")),
  );

  if (input.xrayStructure === "all") {
    return xrayLayers.map((l) => ({
      key: `asm:${l.id}`,
      label: l.label_zh || l.label || l.id,
      kind: "assembly" as const,
      ref: l.id,
      assemblyId: l.id,
    }));
  }

  if (input.xrayStructure === "mechanical") {
    return mechLayers.map((l) => ({
      key: `asm:${l.id}`,
      label: l.label_zh || l.label || l.id,
      kind: "assembly" as const,
      ref: l.id,
      assemblyId: l.id,
    }));
  }

  if (flowStructs.has(input.xrayStructure)) {
    const struct = input.xrayStructure;
    const items: ModelListItemBase[] = xrayLayers
      .filter((l) => l.garageStructure === struct)
      .map((l) => ({
        key: `asm:${l.id}`,
        label: l.label_zh || l.label || l.id,
        kind: "assembly" as const,
        ref: l.id,
        assemblyId: l.id,
      }));
    for (const f of flows.filter((fl) => fl.layer === struct)) {
      items.push({
        key: `flow:${f.id}`,
        label: f.label || f.id,
        kind: "flow" as const,
        ref: f.id,
        assemblyId: null,
      });
    }
    return items;
  }

  return [];
}

/**
 * 车库 · 零件浏览器 — 左 3D 固定；右「模型|消耗品」。
 * 模型跟分栏 mesh/装配；消耗品在右侧分栏。
 */
export function PartsBrowserPage() {
  const [parts, setParts] = useState<Part[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [selectedMeshName, setSelectedMeshName] = useState<string | null>(null);
  const [selectedAssemblyId, setSelectedAssemblyId] = useState<string | null>(
    null,
  );
  const [linkedMeshNames, setLinkedMeshNames] = useState<Set<string> | null>(
    null,
  );
  const [linkedAssemblyIds, setLinkedAssemblyIds] = useState<Set<string> | null>(
    null,
  );
  const [meshesByAsm, setMeshesByAsm] = useState<Record<string, string[]>>({});
  const [modelLinks, setModelLinks] = useState<ModelOemLink[]>([]);
  const [rightTab, setRightTab] = useState<RightTab>("model");
  /** 模型栏手风琴：展开的装配 id（其它收起） */
  const [expandedAssemblyId, setExpandedAssemblyId] = useState<string | null>(
    null,
  );
  /** 子模型/叶子：展开看价；关联按钮再开 OEM 弹窗 */
  const [expandedChildKey, setExpandedChildKey] = useState<string | null>(null);
  const [oemDialog, setOemDialog] = useState<ModelListItem | null>(null);
  /** 消耗品：改零件库 oem_number（按 sku） */
  const [partOemEdit, setPartOemEdit] = useState<{
    sku: string;
    label: string;
  } | null>(null);
  const [oemDraft, setOemDraft] = useState("");
  /** 联想查询防抖，避免每键全库扫一次卡输入 */
  const [oemSuggestQ, setOemSuggestQ] = useState("");
  const [oemDialogErr, setOemDialogErr] = useState<string | null>(null);
  const [oemSaving, setOemSaving] = useState(false);
  const [vehicle, setVehicle] = useState<Vehicle | null>(null);
  const [garage, setGarage] = useState<XrayScene | null>(null);
  const [meshState, setMeshState] = useState<XrayMeshState | null>(null);
  const [mode, setMode] = useState<GarageViewMode>("exterior");
  const [interiorZone, setInteriorZone] = useState<InteriorZoneId>("all");
  const [xrayStructure, setXrayStructure] =
    useState<GarageStructureId>("all");
  const [err, setErr] = useState<string | null>(null);
  const [fx, setFx] = useState<FxTable>(DEFAULT_FX_TABLE);

  useEffect(() => {
    Promise.all([
      api().listParts(),
      api().getVehicle().catch(() => null),
      api().xrayGarageLayers().catch(() => null),
      api().xrayGetMeshState().catch(() => null),
      api().modelOemLinksGet?.().catch(() => null) ?? Promise.resolve(null),
      Promise.resolve(api().getFx?.() ?? null).catch(() => null),
    ])
      .then(([p, v, scene, ms, links, fxTable]) => {
        setParts(p);
        if (v) setVehicle(v);
        if (scene) setGarage(scene);
        if (ms) setMeshState(ms);
        if (links?.links) setModelLinks(links.links);
        if (fxTable) setFx(fxTable);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    if (mode === "xray" && xrayStructure === "cabin") {
      setXrayStructure("all");
    }
  }, [mode, xrayStructure]);

  useEffect(() => {
    function reloadLinks() {
      void api()
        .modelOemLinksGet?.()
        .then((links) => {
          if (links?.links) setModelLinks(links.links);
        })
        .catch(() => {});
    }
    window.addEventListener("focus", reloadLinks);
    return () => window.removeEventListener("focus", reloadLinks);
  }, []);

  const readGlb = useCallback(async (rel: string) => {
    return toUint8(await api().cmsReadGlb(rel));
  }, []);

  const garageLayers = useMemo(
    () => (garage?.layers ?? []).filter((l) => l.present),
    [garage],
  );
  const garageFlows = useMemo(
    () => (garage?.flows ?? []) as GarageFlowSystem[],
    [garage],
  );

  const modelItems = useMemo(() => {
    const base = modelItemsForView({
      mode,
      xrayStructure,
      interiorZone,
      meshesByAsm,
      layers: garageLayers,
      flows: garageFlows,
    });
    return base.map((item): ModelListItem => {
      const linkedSkus = collectSkusForModelSelection(modelLinks, {
        kind: item.kind,
        ref: item.ref,
        assemblyId: item.assemblyId,
      });
      if (item.kind === "mesh") {
        const cap = formatModelChildCaption(
          item.meshName ?? item.label,
          linkedSkus,
          parts,
        );
        return {
          ...item,
          linkedSkus,
          label: cap.title,
          oemPointer: cap.detail,
        };
      }
      return {
        ...item,
        linkedSkus,
        oemPointer: formatModelOemPointer(linkedSkus, parts),
      };
    });
  }, [
    mode,
    xrayStructure,
    interiorZone,
    meshesByAsm,
    garageLayers,
    garageFlows,
    modelLinks,
    parts,
  ]);

  /** 展开装配下的子 mesh（Q4A：meshesByAsm） */
  const expandedChildren = useMemo((): ModelListItem[] => {
    if (!expandedAssemblyId) return [];
    const names = meshesByAsm[expandedAssemblyId] ?? [];
    return [...names]
      .sort((a, b) => a.localeCompare(b))
      .map((n) => {
        const linkedSkus = resolveModelOemSkus(modelLinks, {
          kind: "mesh",
          ref: n,
          assemblyId: expandedAssemblyId,
        });
        const cap = formatModelChildCaption(n, linkedSkus, parts);
        return {
          key: `mesh:${expandedAssemblyId}:${n}`,
          label: cap.title,
          kind: "mesh" as const,
          ref: n,
          meshName: n,
          assemblyId: expandedAssemblyId,
          linkedSkus,
          oemPointer: cap.detail,
        };
      });
  }, [expandedAssemblyId, meshesByAsm, modelLinks, parts]);

  const consumableRows = useMemo(
    () => buildGarageConsumablesList(parts, { carGeneration: "981" }),
    [parts],
  );

  function partForModelItem(item: ModelListItem): Part | null {
    const sku = item.linkedSkus[0];
    if (!sku) return null;
    return parts.find((p) => p.sku === sku) ?? null;
  }

  function aftermarketLines(p: Part | null): string[] {
    if (!p) return [];
    // 平替价来自 Design911 站点，多为 GBP；OEM 价仍用 price_note（teile/PETKA EUR/GBP）
    const amNote = /design911=alts/i.test(p.price_note ?? "")
      ? "currency=GBP"
      : p.price_note;
    if (p.aftermarket_quotes?.length) {
      return p.aftermarket_quotes.map(
        (q) => `${q.brand} ${formatMoney(q.price, amNote, fx)}`,
      );
    }
    if (p.aftermarket_price != null) {
      return [`副厂 ${formatMoney(p.aftermarket_price, amNote, fx)}`];
    }
    return [];
  }

  /** 点选子模型：展开价格；不直接弹关联窗 */
  function selectChildPart(
    item: ModelListItem,
    opts?: { toggle?: boolean },
  ) {
    setLinkedMeshNames(null);
    setLinkedAssemblyIds(null);
    if (item.kind === "mesh" && item.meshName) {
      setSelectedMeshName(item.meshName);
      setSelectedAssemblyId(item.assemblyId ?? "body");
    } else if (item.kind === "flow") {
      clearModelHighlight();
    }
    if (opts?.toggle === false) setExpandedChildKey(item.key);
    else setExpandedChildKey((prev) => (prev === item.key ? null : item.key));
    clearOemSelection();
  }

  const oemSuggestions = useMemo(
    () => suggestOemByInput(parts, oemSuggestQ, 10),
    [parts, oemSuggestQ],
  );

  useEffect(() => {
    if (!oemDialog && !partOemEdit) {
      setOemSuggestQ("");
      return;
    }
    const t = window.setTimeout(() => setOemSuggestQ(oemDraft), 280);
    return () => window.clearTimeout(t);
  }, [oemDraft, oemDialog, partOemEdit]);

  const bodyPaintHex = resolveBodyPaintHex(vehicle?.paint_name);
  const bodyPaintPbr = resolveBodyPaintPbr(vehicle?.paint_name);
  const softTopHex = resolveSoftTopHex(vehicle?.top);
  const interiorHex = resolveInteriorHex(vehicle?.interior);

  const bodyOk = Boolean(garage?.bodyShell?.present && garage.bodyShell.rel);
  const hasScene = bodyOk || garageLayers.length > 0;
  const cabinView = mode === "interior";

  /** Bake 姿态只读；本页不提供拖动微调。 */
  const previewTransforms = useMemo(() => {
    const next: Record<string, XrayTransform> = {};
    if (!garage) return next;
    for (const layer of garage.layers ?? []) {
      if (layer.transform) next[layer.id] = layer.transform;
    }
    for (const a of garage.assemblies) {
      if (a.transform) next[a.id] = a.transform;
    }
    return next;
  }, [garage]);

  function clearOemSelection() {
    setSelectedKey(null);
  }

  function clearModelHighlight() {
    setSelectedMeshName(null);
    setSelectedAssemblyId(null);
    setLinkedMeshNames(null);
    setLinkedAssemblyIds(null);
  }

  /** 点 OEM：高亮所有挂到该 sku 的模型（多模型 → 一 OEM）。 */
  function highlightForSku(sku: string) {
    const models = findModelsForSku(modelLinks, sku);
    const meshNames = new Set<string>();
    const asmIds = new Set<string>();
    for (const m of models) {
      if (m.kind === "mesh") {
        meshNames.add(m.ref);
        if (m.assemblyId) asmIds.add(m.assemblyId);
      } else if (m.kind === "assembly") {
        asmIds.add(m.ref);
      }
    }
    if (meshNames.size === 1 && asmIds.size <= 1) {
      setSelectedMeshName([...meshNames][0]!);
      setSelectedAssemblyId([...asmIds][0] ?? "body");
      setLinkedMeshNames(null);
      setLinkedAssemblyIds(null);
    } else if (meshNames.size > 0) {
      setSelectedMeshName(null);
      setSelectedAssemblyId(null);
      setLinkedMeshNames(meshNames);
      setLinkedAssemblyIds(asmIds.size ? asmIds : null);
    } else if (asmIds.size === 1) {
      setSelectedMeshName(null);
      setSelectedAssemblyId([...asmIds][0]!);
      setLinkedMeshNames(null);
      setLinkedAssemblyIds(null);
    } else if (asmIds.size > 1) {
      setSelectedMeshName(null);
      setSelectedAssemblyId(null);
      setLinkedMeshNames(null);
      setLinkedAssemblyIds(asmIds);
    } else {
      clearModelHighlight();
    }
  }

  function selectModelItem(item: ModelListItem) {
    setLinkedMeshNames(null);
    setLinkedAssemblyIds(null);

    // 装配父块：手风琴展开；透视件切透视，顶栏内饰 PETKA 留在内饰
    if (item.kind === "assembly" && item.assemblyId) {
      if (
        mode !== "interior" ||
        !isInteriorZoneAssembly(garageLayers, item.assemblyId)
      ) {
        setMode("xray");
        setXrayStructure(
          xrayStructureForAssemblyPick(
            garageLayers,
            item.assemblyId,
            xrayStructure,
          ),
        );
      }
      setSelectedAssemblyId(item.assemblyId);
      setSelectedMeshName(null);
      setExpandedAssemblyId((prev) =>
        prev === item.assemblyId ? null : item.assemblyId!,
      );
      setRightTab("model");
      clearOemSelection();
      return;
    }

    // 叶子 mesh / flow：展开看价；关联按钮再绑 OEM
    selectChildPart(item);
  }

  function openOemDialog(item: ModelListItem) {
    setPartOemEdit(null);
    setOemDialogErr(null);
    const sku = item.linkedSkus[0];
    const p = sku ? parts.find((x) => x.sku === sku) : null;
    setOemDraft(p?.oem_number?.trim() || "");
    setOemDialog(item);
    if (item.kind === "mesh" && item.meshName) {
      setSelectedMeshName(item.meshName);
      setSelectedAssemblyId(item.assemblyId ?? "body");
    } else if (item.kind === "flow") {
      clearModelHighlight();
    }
    setLinkedMeshNames(null);
    setLinkedAssemblyIds(null);
  }

  function openPartOemDialog(row: (typeof consumableRows)[number]) {
    const sku = row.skus[0];
    if (!sku) return;
    setOemDialog(null);
    setOemDialogErr(null);
    setOemDraft(row.oem_number?.trim() || "");
    setPartOemEdit({ sku, label: row.name_zh });
  }

  function closeOemDialog() {
    if (oemSaving) return;
    setOemDialog(null);
    setPartOemEdit(null);
    setOemDialogErr(null);
    setOemDraft("");
  }

  async function savePartOemDialog() {
    if (!partOemEdit) return;
    const oem = oemDraft.trim();
    setOemSaving(true);
    setOemDialogErr(null);
    try {
      const updated = await api().updatePartNames!({
        sku: partOemEdit.sku,
        oem_number: oem || null,
      });
      if (!updated) {
        setOemDialogErr(`未找到零件 sku=${partOemEdit.sku}`);
        return;
      }
      const next = await api().listParts();
      setParts(next);
      closeOemDialog();
    } catch (e) {
      setOemDialogErr(String(e));
    } finally {
      setOemSaving(false);
    }
  }

  async function saveOemDialog() {
    if (!oemDialog) return;
    const oem = oemDraft.trim();
    setOemSaving(true);
    setOemDialogErr(null);
    try {
      if (!oem) {
        // 空值 = 取消当前模型的 OEM 关联
        const next = await api().modelOemLinksRemove!(
          oemDialog.kind as ModelOemKind,
          oemDialog.ref,
          undefined,
          oemDialog.assemblyId,
        );
        setModelLinks(next.links);
      } else {
        const sku = findSkuByOemNumber(parts, oem);
        if (!sku) {
          setOemDialogErr("本机零件库未找到该 OEM");
          return;
        }
        const next = await api().modelOemLinksUpsert!({
          kind: oemDialog.kind as ModelOemKind,
          ref: oemDialog.ref,
          assemblyId: oemDialog.assemblyId,
          skus: [sku],
        });
        setModelLinks(next.links);
      }
      setOemDialog(null);
      setOemDraft("");
    } catch (e) {
      setOemDialogErr(String(e));
    } finally {
      setOemSaving(false);
    }
  }

  /** 零件/消耗品：点击展开详情（与模型子块同款手风琴）。 */
  function selectOemRow(key: string, skus: readonly string[]) {
    if (selectedKey === key) {
      clearOemSelection();
      clearModelHighlight();
      return;
    }
    setSelectedKey(key);
    const sku = skus[0];
    if (sku) highlightForSku(sku);
    else clearModelHighlight();
  }

  function renderOemRowExpand(row: (typeof consumableRows)[number]) {
    const p = row.parts[0] ?? null;
    const oemDisp = p
      ? formatMoneyDisplay(p.oem_price, p.price_note, fx)
      : null;
    const am = aftermarketLines(p);
    const notesDisp = applyIntervalKindMarker(p?.notes, "hard");
    return (
      <div className="parts-browser-part-expand">
        <p>
          <span className="muted">PART NO.</span>{" "}
          {row.oem_number ?? "—"}
        </p>
        <p>
          <span className="muted">OEM 价</span> {oemDisp?.primary ?? "—"}
        </p>
        <p>
          <span className="muted">副厂</span>{" "}
          {am.length ? am.join(" · ") : "—"}
        </p>
        <p>
          <span className="muted">寿命</span> {formatConsumableLife(p)}
        </p>
        {notesDisp ? <p>{notesDisp}</p> : null}
        <button
          type="button"
          className="primary"
          disabled={!row.skus[0]}
          onClick={(e) => {
            e.stopPropagation();
            openPartOemDialog(row);
          }}
        >
          关联
        </button>
      </div>
    );
  }

  function onPickFromViewer(
    meshName: string,
    _path: string,
    assemblyId: string | null,
  ) {
    const asm = assemblyId && assemblyId !== "body" ? assemblyId : "body";
    setLinkedMeshNames(null);
    setLinkedAssemblyIds(null);
    setSelectedMeshName(meshName);
    setSelectedAssemblyId(asm);
    setRightTab("model");
    clearOemSelection();

    if (asm !== "body") {
      if (mode !== "interior" || !isInteriorZoneAssembly(garageLayers, asm)) {
        setMode("xray");
        setXrayStructure(
          xrayStructureForAssemblyPick(garageLayers, asm, xrayStructure),
        );
      }
      setExpandedAssemblyId(asm);
    } else if (mode === "xray") {
      setExpandedAssemblyId(null);
    }

    const linkedSkus = resolveModelOemSkus(modelLinks, {
      kind: "mesh",
      ref: meshName,
      assemblyId: asm,
    });
    const cap = formatModelChildCaption(meshName, linkedSkus, parts);
    selectChildPart({
      key: `mesh:${asm}:${meshName}`,
      label: cap.title,
      kind: "mesh",
      ref: meshName,
      meshName,
      assemblyId: asm,
      linkedSkus,
      oemPointer: cap.detail,
    }, { toggle: false });
  }

  const listCount =
    rightTab === "model"
      ? modelItems.length +
        (expandedAssemblyId ? expandedChildren.length : 0)
      : consumableRows.length;

  function renderModelPartExpand(item: ModelListItem) {
    const p = partForModelItem(item);
    const oemDisp = p
      ? formatMoneyDisplay(p.oem_price, p.price_note, fx)
      : null;
    const am = aftermarketLines(p);
    const name =
      p && /[\u4e00-\u9fff]/.test(p.name_zh ?? "")
        ? p.name_zh
        : (p?.name_en?.trim() || p?.name_zh || null);
    return (
      <div className="parts-browser-part-expand">
        <p>
          <span className="muted">OEM</span>{" "}
          {p?.oem_number ?? item.label}
          {name ? <span className="muted"> · {name}</span> : null}
        </p>
        {p?.petka_note ? (
          <p>
            <span className="muted">备注</span> {p.petka_note}
          </p>
        ) : null}
        {p?.pr_label ? (
          <p>
            <span className="muted">PR</span> {p.pr_label}
          </p>
        ) : null}
        <p>
          <span className="muted">OEM 价</span> {oemDisp?.primary ?? "—"}
        </p>
        <p>
          <span className="muted">副厂</span>{" "}
          {am.length ? am.join(" · ") : "—"}
        </p>
        <button
          type="button"
          className="primary"
          onClick={(e) => {
            e.stopPropagation();
            openOemDialog(item);
          }}
        >
          关联
        </button>
      </div>
    );
  }

  return (
    <div className="parts-browser" data-page="parts-browser">
      <header className="page-head">
        <h1>车库 · 零件浏览器</h1>
      </header>
      {err ? <p className="error">{err}</p> : null}

      <div className="parts-browser-split">
        <section className="parts-browser-3d panel" aria-label="3D 模型">
          <div className="chip-row parts-browser-modes">
            {(
              [
                ["exterior", "外观"],
                ["interior", "内饰"],
                ["xray", "透视"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`chip${mode === id ? " active" : ""}`}
                onClick={() => {
                  setMode(id);
                  clearOemSelection();
                  clearModelHighlight();
                  setExpandedAssemblyId(null);
                  setExpandedChildKey(null);
                }}
                disabled={!hasScene}
                title={!hasScene ? "无车库 3D 资产" : undefined}
              >
                {label}
              </button>
            ))}
          </div>
          <div
            className="chip-row parts-browser-xray-structure parts-browser-subtoolbar"
            role="group"
            aria-label={
              mode === "xray"
                ? "透视结构层"
                : mode === "interior"
                  ? "内饰分栏"
                  : undefined
            }
            aria-hidden={mode !== "xray" && mode !== "interior"}
          >
            {mode === "xray"
              ? XRAY_STRUCTURE.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className={`chip${xrayStructure === c.id ? " active" : ""}`}
                    onClick={() => {
                      setXrayStructure(c.id);
                      clearOemSelection();
                      clearModelHighlight();
                      setExpandedAssemblyId(null);
                      setExpandedChildKey(null);
                    }}
                  >
                    {c.labelZh}
                  </button>
                ))
              : null}
            {mode === "interior"
              ? INTERIOR_ZONES.map((z) => (
                  <button
                    key={z.id}
                    type="button"
                    className={`chip${interiorZone === z.id ? " active" : ""}`}
                    onClick={() => {
                      setInteriorZone(z.id);
                      clearOemSelection();
                      clearModelHighlight();
                      setExpandedAssemblyId(null);
                      setExpandedChildKey(null);
                    }}
                  >
                    {z.labelZh}
                  </button>
                ))
              : null}
          </div>
          {hasScene ? (
            <LocatorGlbViewer
              layers={garageLayers}
              ghostBody={garage?.bodyShell ?? null}
              showGhostBody
              garageViewMode={mode}
              garageStructure={xrayStructure}
              garageFlows={garageFlows}
              exteriorZone="all"
              interiorZone={interiorZone}
              readGlb={readGlb}
              selectedMeshName={selectedMeshName}
              selectedAssemblyId={selectedAssemblyId}
              linkedMeshNames={linkedMeshNames ?? undefined}
              linkedAssemblyIds={linkedAssemblyIds}
              previewMeshState={meshState}
              previewTransforms={previewTransforms}
              bodyPaintHex={bodyPaintHex}
              bodyPaintPbr={bodyPaintPbr}
              softTopHex={softTopHex}
              interiorHex={interiorHex}
              hideSoftTop={cabinView}
              interiorFillLight={cabinView}
              onMeshesReady={setMeshesByAsm}
              onPickMesh={onPickFromViewer}
              onPickEmpty={() => {
                clearModelHighlight();
                clearOemSelection();
                setExpandedAssemblyId(null);
                setExpandedChildKey(null);
              }}
            />
          ) : (
            <div className="locator-glb missing">
              无车库 3D。请运行 npm run fetch:flat-six-cabins。
            </div>
          )}
        </section>

        <aside className="parts-browser-side panel" aria-label="零件">
          <h2>
            零件{" "}
            <span className="muted" style={{ fontWeight: 400 }}>
              {listCount}
            </span>
          </h2>

          <div className="chip-row" role="tablist" aria-label="清单分栏">
            {(
              [
                ["model", "模型"],
                ["consumables", "消耗品"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={rightTab === id}
                className={`chip${rightTab === id ? " active" : ""}`}
                onClick={() => {
                  setRightTab(id);
                  clearOemSelection();
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {rightTab === "model" ? (
            <>
              <ul className="parts-browser-list plain-list">
                {modelItems.map((item) => {
                  const expandedAsm =
                    item.kind === "assembly" &&
                    item.assemblyId != null &&
                    item.assemblyId === expandedAssemblyId;
                  const childOpen = expandedChildKey === item.key;
                  const active =
                    (item.kind === "mesh" &&
                      item.meshName === selectedMeshName) ||
                    (item.kind === "assembly" &&
                      item.assemblyId === selectedAssemblyId);
                  return (
                    <li key={item.key}>
                      <button
                        type="button"
                        className={
                          active
                            ? "parts-browser-item active"
                            : "parts-browser-item"
                        }
                        onClick={() => selectModelItem(item)}
                        aria-expanded={
                          item.kind === "assembly" ? expandedAsm : childOpen
                        }
                      >
                        <strong>
                          {item.kind === "assembly"
                            ? `${expandedAsm ? "▾" : "▸"} ${item.label}`
                            : `${childOpen ? "▾" : "▸"} ${item.label}`}
                        </strong>
                        <span className="muted">
                          {item.kind === "assembly"
                            ? expandedAsm
                              ? `${expandedChildren.length} 子块`
                              : (() => {
                                  const n =
                                    item.assemblyId != null
                                      ? (meshesByAsm[item.assemblyId]
                                          ?.length ?? 0)
                                      : 0;
                                  return n > 0 ? `${n} 子块` : "无子块";
                                })()
                            : item.oemPointer}
                        </span>
                      </button>
                      {item.kind !== "assembly" && childOpen
                        ? renderModelPartExpand(item)
                        : null}
                      {expandedAsm ? (
                        <ul className="parts-browser-children plain-list">
                          {expandedChildren.map((child) => {
                            const open = expandedChildKey === child.key;
                            return (
                              <li key={child.key}>
                                <button
                                  type="button"
                                  className={
                                    child.meshName === selectedMeshName
                                      ? "parts-browser-item active"
                                      : "parts-browser-item"
                                  }
                                  onClick={() => selectChildPart(child)}
                                  aria-expanded={open}
                                >
                                  <strong>
                                    {open ? "▾" : "▸"} {child.label}
                                  </strong>
                                  <span className="muted">
                                    {child.oemPointer}
                                  </span>
                                </button>
                                {open ? renderModelPartExpand(child) : null}
                              </li>
                            );
                          })}
                          {expandedChildren.length === 0 ? (
                            <li>
                              <p className="muted parts-browser-empty">
                                尚无子 mesh（等 3D 加载或 GLB 无节点名）
                              </p>
                            </li>
                          ) : null}
                        </ul>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
              {modelItems.length === 0 ? (
                <p className="muted parts-browser-empty">
                  当前分栏没有可列的模型块。
                </p>
              ) : null}
            </>
          ) : (
            <>
              <ul className="parts-browser-list plain-list">
                {consumableRows.map((r) => {
                  const open = selectedKey === r.key;
                  return (
                    <li key={r.key}>
                      <button
                        type="button"
                        className={
                          open
                            ? "parts-browser-item active"
                            : "parts-browser-item"
                        }
                        onClick={() => selectOemRow(r.key, r.skus)}
                        aria-expanded={open}
                      >
                        <strong>
                          {open ? "▾" : "▸"} {r.name_zh}
                        </strong>
                        <span className="muted">
                          {r.oem_number ?? "—"}
                        </span>
                      </button>
                      {open ? renderOemRowExpand(r) : null}
                    </li>
                  );
                })}
              </ul>
              {consumableRows.length === 0 ? (
                <p className="muted parts-browser-empty">
                  没有 981 周期更换件。
                </p>
              ) : null}
            </>
          )}
        </aside>
      </div>

      {oemDialog || partOemEdit ? (
        <div className="model-oem-dialog-backdrop" role="presentation">
          <div
            className="model-oem-dialog panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="parts-oem-dialog-title"
          >
            <h2 id="parts-oem-dialog-title">
              {partOemEdit ? "修改零件号" : "关联 OEM"}
            </h2>
            <p className="muted">
              {partOemEdit ? (
                <>
                  {partOemEdit.label}{" "}
                  <span>(sku:{partOemEdit.sku})</span>
                </>
              ) : oemDialog ? (
                <>
                  {oemDialog.label}{" "}
                  <span>
                    ({oemDialog.kind}
                    {oemDialog.assemblyId ? `:${oemDialog.assemblyId}` : ""}:
                    {oemDialog.ref})
                  </span>
                </>
              ) : null}
            </p>
            <label className="model-oem-dialog-field">
              OEM 零件号
              <input
                autoFocus
                value={oemDraft}
                onChange={(e) => setOemDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    void (partOemEdit ? savePartOemDialog() : saveOemDialog());
                  }
                  if (e.key === "Escape") closeOemDialog();
                }}
                placeholder={
                  partOemEdit
                    ? "留空保存 = 清空零件号；例如 9A1.107.224.00"
                    : "留空保存 = 取消关联；例如 9A1.107.224.00"
                }
                autoComplete="off"
              />
            </label>
            {oemSuggestions.length > 0 ? (
              <ul className="model-oem-suggest plain-list" role="listbox">
                {oemSuggestions.map((hit) => (
                  <li key={hit.sku}>
                    <button
                      type="button"
                      className="model-oem-suggest-item"
                      onClick={() => setOemDraft(hit.oem_number)}
                    >
                      <strong>{hit.oem_number}</strong>
                      <span className="muted">
                        {hit.name_zh ?? "（无中文名）"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {oemDialogErr ? <p className="error">{oemDialogErr}</p> : null}
            <div className="model-oem-dialog-actions">
              <button
                type="button"
                className="ghost"
                disabled={oemSaving}
                onClick={closeOemDialog}
              >
                取消
              </button>
              <button
                type="button"
                className="primary"
                disabled={oemSaving}
                onClick={() =>
                  void (partOemEdit ? savePartOemDialog() : saveOemDialog())
                }
              >
                保存
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
