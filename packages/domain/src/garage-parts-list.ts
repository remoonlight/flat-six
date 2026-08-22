/**
 * 车库右侧零件清单：按 3D 分栏热点过滤、OEM 去重、世代标签（981/718）。
 * 热点对齐 parts.locator_hotspot + garage assemblies.bridgeHotspot。
 */

import { normalizeOemForSku } from "./petka-csv.js";

/** 左侧 3D 顶栏（消耗品在右侧 OEM 区分栏，不进顶栏）。 */
export type GaragePartsMode = "exterior" | "interior" | "xray";

export type ExteriorZoneId =
  | "all"
  | "front-bumper"
  | "front-trunk"
  | "soft-top"
  | "rear-trunk"
  | "rear-bumper";

export type InteriorZoneId =
  | "all"
  | "door-trim"
  | "dashboard"
  | "console"
  | "seats"
  | "liner";

export type GarageStructureId =
  | "all"
  | "mechanical"
  | "cabin"
  | "air"
  | "lines"
  | "vacuum"
  | "wiring";

export type GaragePartRef = {
  sku: string;
  name_zh: string;
  oem_number: string | null;
  system: string;
  generation: string | null;
  interval_km: number | null;
  interval_months: number | null;
  locator_hotspot: string | null;
};

/** 外观分栏 → locator 热点（跟 mesh 区语义对齐，零件靠 hotspot 挂）。 */
export const EXTERIOR_ZONE_HOTSPOTS: Record<
  ExteriorZoneId,
  readonly string[]
> = {
  all: ["body-paint"],
  "front-bumper": [],
  "front-trunk": ["front-trunk", "wipers"],
  "soft-top": ["int-softtop"],
  "rear-trunk": ["engine-bay", "eng-intake", "eng-block", "eng-exhaust"],
  "rear-bumper": ["eng-exhaust"],
};

/** 内饰分栏 → interior 热点。 */
export const INTERIOR_ZONE_HOTSPOTS: Record<
  InteriorZoneId,
  readonly string[]
> = {
  all: [],
  "door-trim": ["int-details-int"],
  dashboard: ["int-details-mat", "int-cabin-light"],
  console: ["int-details-mat", "int-cabin-filter"],
  seats: ["int-details-mat"],
  liner: ["front-trunk", "engine-bay"],
};

/** 透视机械总成默认 bridge（与 garage-assemblies.json 一致）。 */
export const XRAY_MECHANICAL_HOTSPOTS = [
  "engine-bay",
  "eng-intake",
  "eng-block",
  "eng-exhaust",
  "underbody",
  "front-left",
  "rear-left",
] as const;

/** 气路/管路/真空暂无零件锚点；线束挂电子导航占位（多数零件仍无 hotspot）。 */
export const XRAY_FLOW_HOTSPOTS: Record<
  Exclude<GarageStructureId, "all" | "mechanical">,
  readonly string[]
> = {
  cabin: [],
  air: [],
  lines: [],
  vacuum: [],
  wiring: [
    "elec-low-voltage",
    "elec-gateway-comfort",
    "elec-gateway-drive",
    "elec-gateway-crash",
    "elec-fuse-box",
  ],
};

export function isConsumablePart(p: {
  interval_km: number | null;
  interval_months: number | null;
}): boolean {
  return p.interval_km != null || p.interval_months != null;
}

/**
 * 当前车为 981：只留 981 / 未标世代（保养子集）；剔除 718/982。
 */
export function isPartFor981Car(p: {
  generation: string | null | undefined;
}): boolean {
  const d = generationDisplayLabel(p.generation);
  return d !== "718";
}

/** DB `981`|`982` → 展示 `981`|`718`（用户口中的 718 = 982）。 */
export function generationDisplayLabel(
  generation: string | null | undefined,
): string | null {
  if (generation == null || generation === "") return null;
  const g = generation.trim().toUpperCase();
  if (g === "981") return "981";
  if (g === "982" || g === "718") return "718";
  return generation.trim();
}

/** 多个世代合成「981」「718」「981+718」。 */
export function mergeGenerationLabel(
  generations: ReadonlyArray<string | null | undefined>,
): string {
  const labels = new Set<string>();
  for (const g of generations) {
    const d = generationDisplayLabel(g);
    if (d) labels.add(d);
  }
  if (labels.size === 0) return "—";
  const ordered = ["981", "718"].filter((x) => labels.has(x));
  for (const x of labels) {
    if (x !== "981" && x !== "718") ordered.push(x);
  }
  return ordered.join("+");
}

export function hotspotsForGarageView(input: {
  mode: GaragePartsMode;
  exteriorZone: ExteriorZoneId;
  interiorZone: InteriorZoneId;
  xrayStructure: GarageStructureId;
  /** 可选：从 garage assemblies 收集的 bridgeHotspot，优先于默认机械表。 */
  assemblyBridgeHotspots?: readonly string[];
}): ReadonlySet<string> | "all-linked" {
  const { mode } = input;

  if (mode === "exterior") {
    if (input.exteriorZone === "all") {
      return new Set(Object.values(EXTERIOR_ZONE_HOTSPOTS).flat());
    }
    return new Set(EXTERIOR_ZONE_HOTSPOTS[input.exteriorZone]);
  }

  if (mode === "interior") {
    if (input.interiorZone === "all") {
      return new Set([
        "int-details-int",
        "int-cabin-light",
        "int-details-mat",
        "int-softtop",
        "int-cabin-filter",
      ]);
    }
    return new Set(INTERIOR_ZONE_HOTSPOTS[input.interiorZone]);
  }

  // xray
  if (input.xrayStructure === "all" || input.xrayStructure === "mechanical") {
    const fromAsm = input.assemblyBridgeHotspots?.filter(Boolean) ?? [];
    return new Set(
      fromAsm.length > 0 ? fromAsm : [...XRAY_MECHANICAL_HOTSPOTS],
    );
  }
  return new Set(XRAY_FLOW_HOTSPOTS[input.xrayStructure]);
}

export function filterPartsByGarageView<T extends GaragePartRef>(
  parts: readonly T[],
  scope: ReadonlySet<string> | "all-linked",
): T[] {
  if (scope === "all-linked") {
    return parts.filter((p) => p.locator_hotspot);
  }
  if (scope.size === 0) return [];
  return parts.filter(
    (p) => p.locator_hotspot != null && scope.has(p.locator_hotspot),
  );
}

export type MergedOemPartRow<T extends GaragePartRef = GaragePartRef> = {
  /** 排序/去重键；无 OEM 时用 sku。 */
  key: string;
  oem_number: string | null;
  name_zh: string;
  /** 「981」「718」「981+718」「—」 */
  generationLabel: string;
  system: string;
  locator_hotspot: string | null;
  skus: string[];
  parts: T[];
};

/**
 * 按 OEM 去重合并（无 OEM 按 sku 各留一行），OEM 字典序；
 * 同 OEM 多世代合成一行，generationLabel 如 981+718。
 */
export function mergePartsByOem<T extends GaragePartRef>(
  parts: readonly T[],
): MergedOemPartRow<T>[] {
  const buckets = new Map<string, T[]>();
  for (const p of parts) {
    const key = p.oem_number?.trim()
      ? normalizeOemForSku(p.oem_number)
      : `sku:${p.sku}`;
    const list = buckets.get(key);
    if (list) list.push(p);
    else buckets.set(key, [p]);
  }

  const rows: MergedOemPartRow<T>[] = [];
  for (const [key, group] of buckets) {
    group.sort((a, b) => a.sku.localeCompare(b.sku));
    const primary =
      group.find((p) => generationDisplayLabel(p.generation) === "981") ??
      group[0]!;
    rows.push({
      key,
      oem_number: primary.oem_number,
      name_zh: primary.name_zh,
      generationLabel: mergeGenerationLabel(group.map((p) => p.generation)),
      system: primary.system,
      locator_hotspot: primary.locator_hotspot,
      skus: group.map((p) => p.sku),
      parts: group,
    });
  }

  rows.sort((a, b) => {
    const ao = a.oem_number ?? "";
    const bo = b.oem_number ?? "";
    if (ao && bo) {
      const c = normalizeOemForSku(ao).localeCompare(normalizeOemForSku(bo));
      if (c !== 0) return c;
    } else if (ao) return -1;
    else if (bo) return 1;
    return a.key.localeCompare(b.key);
  });
  return rows;
}

export type GarageOemListOpts = {
  /** 当前车世代；981 → 剔 718/982。默认不筛。 */
  carGeneration?: "981" | "718" | null;
};

function applyCarGenerationFilter<T extends GaragePartRef>(
  parts: readonly T[],
  carGeneration: GarageOemListOpts["carGeneration"],
): T[] {
  if (carGeneration === "981") return parts.filter(isPartFor981Car);
  if (carGeneration === "718") {
    return parts.filter((p) => {
      const d = generationDisplayLabel(p.generation);
      return d === "718" || d == null;
    });
  }
  return [...parts];
}

/** 当前分栏 OEM（A+B 的 B）；无热点绑定时为空，不滥竽充数。 */
export function buildGaragePartsList<T extends GaragePartRef>(
  parts: readonly T[],
  view: {
    mode: GaragePartsMode;
    exteriorZone: ExteriorZoneId;
    interiorZone: InteriorZoneId;
    xrayStructure: GarageStructureId;
    assemblyBridgeHotspots?: readonly string[];
  },
  opts: GarageOemListOpts = {},
): MergedOemPartRow<T>[] {
  const scoped = filterPartsByGarageView(
    applyCarGenerationFilter(parts, opts.carGeneration),
    hotspotsForGarageView(view),
  );
  return mergePartsByOem(scoped);
}

/** 消耗品分栏：周期件；不跟 3D 分栏联动，仍可按车世代筛。 */
export function buildGarageConsumablesList<T extends GaragePartRef>(
  parts: readonly T[],
  opts: GarageOemListOpts = {},
): MergedOemPartRow<T>[] {
  const base = applyCarGenerationFilter(parts, opts.carGeneration).filter(
    isConsumablePart,
  );
  return mergePartsByOem(base);
}
