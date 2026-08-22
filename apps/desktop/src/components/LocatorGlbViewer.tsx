import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type {
  GarageFlowSystem,
  XrayAssembly,
  XrayBodyShell,
  XrayMeshState,
  XrayTransform,
} from "../api";

export type GarageViewMode = "exterior" | "interior" | "xray";
export type GarageStructureId =
  | "all"
  | "mechanical"
  | "cabin"
  | "air"
  | "lines"
  | "vacuum"
  | "wiring";

/** X-ray 微调：视口三轴 Gizmo 模式（对齐常见 DCC / Tripo 快捷）。 */
export type GizmoMode = "translate" | "rotate" | "scale";

/** 车坐标视角预设：+Z 车头，+Y 上。 */
export type ViewPresetId =
  | "front"
  | "back"
  | "left"
  | "right"
  | "top"
  | "bottom"
  | "iso";

export const VIEW_PRESETS: ReadonlyArray<{
  id: ViewPresetId;
  labelZh: string;
}> = [
  { id: "front", labelZh: "前" },
  { id: "back", labelZh: "后" },
  { id: "left", labelZh: "左" },
  { id: "right", labelZh: "右" },
  { id: "top", labelZh: "上" },
  { id: "bottom", labelZh: "下" },
  { id: "iso", labelZh: "透视" },
];

/** 外观分栏。开盖角对齐 CMS Porsche991 config.txt `doorAngle=70`。 */
export type ExteriorZoneId =
  | "all"
  | "front-bumper"
  | "front-trunk"
  | "soft-top"
  | "rear-trunk"
  | "rear-bumper";

/** CMS `[1_other] doorAngle` — 车门/前盖/后盖统一开角。 */
export const CMS_DOOR_ANGLE_DEG = 70;

type ExteriorZoneDef = {
  id: ExteriorZoneId;
  labelZh: string;
  labelEn: string;
  /** 命中则高亮；null = 全部 */
  match: RegExp | null;
  /** 前舱/后舱开盖；hinge rear=盖板后缘（前备箱），front=盖板前缘（后盖） */
  lid?: { match: RegExp; hinge: "front" | "rear"; angleDeg: number };
};

export const EXTERIOR_ZONES: readonly ExteriorZoneDef[] = [
  { id: "all", labelZh: "全部", labelEn: "ALL", match: null },
  {
    id: "front-bumper",
    labelZh: "前保险杠",
    labelEn: "FRONT BUMPER",
    match: /SM_FrontKit/i,
  },
  {
    id: "front-trunk",
    labelZh: "前舱",
    labelEn: "FRONT TRUNK",
    match: /SM_Hood|SM_FrontKit/i,
    lid: {
      match: /SM_Hood/i,
      hinge: "rear",
      angleDeg: CMS_DOOR_ANGLE_DEG,
    },
  },
  {
    id: "soft-top",
    labelZh: "顶蓬机构",
    labelEn: "SOFT TOP",
    match: /SM_SoftTop/i,
  },
  {
    id: "rear-trunk",
    labelZh: "后舱",
    labelEn: "REAR DECK",
    // ponytail: flat-six boxster-real 无独立后盖 mesh（CMS 有 trunk/engine_cover）；
    // 后舱仅聚焦后部套件；有 SM_Hood 以外的 deck/trunk 名时再开盖。
    match: /SM_RearKit|SM_Base/i,
    lid: {
      match: /SM_(Trunk|Engine_?Cover|Deck|RearHood)/i,
      hinge: "front",
      angleDeg: CMS_DOOR_ANGLE_DEG,
    },
  },
  {
    id: "rear-bumper",
    labelZh: "后保险杠",
    labelEn: "REAR BUMPER",
    match: /SM_RearKit/i,
  },
];

/**
 * 内饰分栏。flat-six boxster-real 无独立车门/座椅/中控 mesh，
 * 仅材质子块（Details_INT / Details_MAT / Light）+ 镜头预设区分。
 */
export type InteriorZoneId =
  | "all"
  | "door-trim"
  | "dashboard"
  | "console"
  | "seats"
  | "liner";

type InteriorZoneDef = {
  id: InteriorZoneId;
  labelZh: string;
  labelEn: string;
  match: RegExp | null;
  /** 车坐标镜头：target + eye */
  look?: { target: [number, number, number]; eye: [number, number, number] };
};

export const INTERIOR_ZONES: readonly InteriorZoneDef[] = [
  {
    id: "all",
    labelZh: "全部",
    labelEn: "ALL",
    match: /SM_Interior/i,
    look: { target: [0, 0.4, 0.1], eye: [0.15, 0.55, -1.35] },
  },
  {
    id: "door-trim",
    labelZh: "车门饰板",
    labelEn: "DOOR TRIM",
    match: /Details_INT/i,
    look: { target: [0.55, 0.4, 0.05], eye: [0.05, 0.5, -0.55] },
  },
  {
    id: "dashboard",
    labelZh: "仪表板",
    labelEn: "DASHBOARD",
    match: /Details_MAT/i,
    look: { target: [0, 0.55, 0.55], eye: [0, 0.5, -0.35] },
  },
  {
    id: "console",
    labelZh: "中控台",
    labelEn: "CONSOLE",
    match: /Details_MAT/i,
    look: { target: [0, 0.28, 0.2], eye: [0, 0.75, -0.55] },
  },
  {
    id: "seats",
    labelZh: "座椅",
    labelEn: "SEATS",
    match: /Details_MAT/i,
    look: { target: [0.25, 0.35, -0.05], eye: [0.9, 0.55, -0.7] },
  },
  {
    id: "liner",
    labelZh: "内衬",
    labelEn: "TRIM LINER",
    match: /(?!)/,
    look: { target: [0, 0.35, 0], eye: [0, 0.55, -1.1] },
  },
];

export type LocatorGlbViewerProps = {
  /** Single-model mode: relative path under .local/ */
  glbRel?: string | null;
  /**
   * Single-model asset id (e.g. cms991body). TRS via previewTransforms[id]
   * → .local/xray-transforms.json.
   */
  singleAssetId?: string | null;
  /** X-ray mode: loadable layers (deduped). */
  layers?: ReadonlyArray<XrayAssembly> | null;
  ghostBody?: XrayBodyShell | null;
  showGhostBody?: boolean;
  /**
   * 车库统一场景：外观/内饰/透视同一套 body+assemblies，只靠显隐切换。
   * 设了之后忽略「单 GLB / 纯透视」二选一，始终按 layers+ghostBody 加载。
   */
  garageViewMode?: GarageViewMode | null;
  garageStructure?: GarageStructureId;
  garageFlows?: ReadonlyArray<GarageFlowSystem> | null;
  /** 外观分栏；仅 garageViewMode=exterior 时生效 */
  exteriorZone?: ExteriorZoneId;
  /** 内饰分栏；仅 garageViewMode=interior 时生效 */
  interiorZone?: InteriorZoneId;
  readGlb: (rel: string) => Promise<Uint8Array>;
  selectedMeshName: string | null;
  linkedMeshNames?: ReadonlySet<string>;
  selectedAssemblyId?: string | null;
  /**
   * Live TRS overrides (assembly id → transform). Does not reload GLB.
   * Missing keys fall back to layer/body transform from props.
   */
  previewTransforms?: Readonly<Record<string, XrayTransform>> | null;
  /** Per-mesh TRS + layer/mesh visibility (layer × mesh). */
  previewMeshState?: XrayMeshState | null;
  /** Vehicle settings → ghost body paint (X-ray only). */
  bodyPaintHex?: string | null;
  /** Brochure finish → metalness/roughness on Car_Paint* (with bodyPaintHex). */
  bodyPaintPbr?: { metalness: number; roughness: number } | null;
  /** Vehicle settings → ghost soft-top tint (X-ray only). */
  softTopHex?: string | null;
  /** Vehicle settings → cabin leather tint (interior zone + matching body meshes). */
  interiorHex?: string | null;
  /** Hide soft-top / convertible roof meshes (interior view). */
  hideSoftTop?: boolean;
  /** Extra cabin fill light (interior view). */
  interiorFillLight?: boolean;
  /** After X-ray layers load: mesh names keyed by load-layer id (incl. body). */
  onMeshesReady?: (byAssembly: Record<string, string[]>) => void;
  /** 多装配同时高亮（点 OEM → 多模型）。 */
  linkedAssemblyIds?: ReadonlySet<string> | null;
  onPickMesh: (
    meshName: string,
    meshPath: string,
    assemblyId: string | null,
  ) => void;
  /** 点到空白（无 mesh）时取消选中；未传则忽略。 */
  onPickEmpty?: () => void;
  /** X-ray 微调：启用视口 TransformControls。 */
  gizmoEnabled?: boolean;
  gizmoMode?: GizmoMode;
  /** 层 id（含 body / singleAssetId）。 */
  gizmoAssemblyId?: string | null;
  /** 有值时拖子 mesh，否则拖层 wrap。 */
  gizmoMeshName?: string | null;
  onGizmoTransformChange?: (
    assemblyId: string,
    transform: XrayTransform,
    meshName: string | null,
  ) => void;
  /** 视角预设；seq 变化时触发一次。 */
  viewPreset?: { id: ViewPresetId; seq: number } | null;
};

function meshPath(obj: THREE.Object3D): string {
  const parts: string[] = [];
  let cur: THREE.Object3D | null = obj;
  while (cur && cur.type !== "Scene") {
    if (cur.name) parts.unshift(cur.name);
    cur = cur.parent;
  }
  return parts.join("/") || obj.uuid;
}

function meshLabel(obj: THREE.Object3D): string {
  return obj.name || `unnamed:${obj.type}:${obj.uuid.slice(0, 8)}`;
}

function parseHotspot(s: string): THREE.Vector3 {
  const parts = String(s || "0 0 0")
    .trim()
    .split(/\s+/)
    .map(Number);
  return new THREE.Vector3(parts[0] || 0, parts[1] || 0, parts[2] || 0);
}

/** Name heuristics when GLB has no usable COLOR_0 (export often gray). */
function colorForMeshName(name: string): THREE.Color {
  const n = name.toLowerCase();
  if (/filtr|filter|oil/.test(n)) return new THREE.Color(0xdc7820);
  if (/glowica|head|pokrywa|cover|nakladka/.test(n)) return new THREE.Color(0xc4c8cc);
  if (/blok|block|kadlub/.test(n)) return new THREE.Color(0x8a9098);
  if (/kolektor|intake|manifold|dolot/.test(n)) return new THREE.Color(0x4a4e54);
  if (/turbo|w_b61|t_b61/.test(n)) return new THREE.Color(0x6a7078);
  if (/gear|skrzynia|dyfer|diff/.test(n)) return new THREE.Color(0x505860);
  if (/brake|abs|pump|tarcza|klock|zacisk/.test(n)) return new THREE.Color(0x2a2c30);
  if (/tire|opon|rubber/.test(n)) return new THREE.Color(0x1a1a1a);
  if (/rim|felga|wheel/.test(n)) return new THREE.Color(0xd0d4d8);
  if (/exhaust|wydech/.test(n)) return new THREE.Color(0x3a3a3a);
  if (/battery|akumulator/.test(n)) return new THREE.Color(0x2f5f3a);
  if (/cool|chlod|reservoir/.test(n)) return new THREE.Color(0x3a6a8a);
  if (/fuel|bak/.test(n)) return new THREE.Color(0x4a4038);
  if (/window|glass|szyba/.test(n)) return new THREE.Color(0x6a8aaa);
  if (/taillight|rear.?light|lampa_tyl/.test(n)) return new THREE.Color(0xa01818);
  if (/headlight|front.?light|lampa/.test(n)) return new THREE.Color(0xe8eef4);
  if (/mirror|lusterko/.test(n)) return new THREE.Color(0x2a2a2a);
  if (/bumper|zderzak/.test(n)) return new THREE.Color(0x1e1e1e);
  if (/hood|bonnet|maska|trunk|bagaz|engine_cover/.test(n))
    return new THREE.Color(0xc0c4c8);
  if (/door|drzwi|fender|blotnik|body|details/.test(n))
    return new THREE.Color(0xb8bcc2);
  if (/collider/.test(n)) return new THREE.Color(0x444444);
  return new THREE.Color(0x9aa0a8);
}

function isSoftTopMeshName(name: string): boolean {
  return /softtop|soft.?top|convertible.?top|dach|hood.?fabric/i.test(name);
}

function applySoftTopVisibility(
  root: THREE.Object3D,
  hide: boolean,
): void {
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    if (isSoftTopMeshName(meshLabel(m)) || isSoftTopMeshName(m.name)) {
      m.visible = !hide;
    }
  });
}

/**
 * Cabin colour targets (flat-six boxster-real):
 * - SM_Interior_*_Details_MAT ≈ bulk cabin (seats/dash/trim) — ~21k verts
 * - SM_Interior_*_Details_INT ≈ small cabin sub-block — ~1.4k verts
 * Never tint Car_Paint / Light / SoftTop / exterior Details_MAT (FrontKit/RearKit).
 */
function isInteriorLeatherMeshName(name: string): boolean {
  const n = name.toLowerCase();
  if (isBodyPaintMeshName(n) || isGlassMeshName(n) || isSoftTopMeshName(n)) {
    return false;
  }
  if (/light|lamp/i.test(n)) return false;
  // Must be under SM_Interior so exterior Details_MAT panels stay untouched.
  if (!/sm_interior/i.test(n) && !/seat|leather|upholstery|kanapa|siedzenie/i.test(n)) {
    return false;
  }
  return /details_mat|details_int|seat|leather|upholstery|kanapa|siedzenie|dash[_-]?leather/i.test(
    n,
  );
}

/** flat-six body GLB names paint as SM_*_Car_Paint_* — never treat as cabin. */
export function isBodyPaintMeshName(name: string): boolean {
  return /car[_-]?paint|paint[_-]?base|body[_-]?paint/i.test(name);
}

/** Wheels / tires / brakes must never take body paint. */
function isRollingStockMeshName(name: string): boolean {
  return /tire|opon|rubber|rim|felga|wheel|brake|disk|hub|rotor|caliper|tire_brake/i.test(
    name,
  );
}

function tintGhostMesh(
  mesh: THREE.Mesh,
  hex: string,
  bases: Map<string, ShadeBase>,
  /** Vehicle settings: always overwrite albedo (ignore texture preserve lerp). */
  forceFull = false,
  /** Keep albedo map — multiply tint (interior partial recolour). */
  keepMap = false,
  /** Body paint finish (omit for soft-top / interior). */
  pbr?: { metalness: number; roughness: number } | null,
) {
  const color = new THREE.Color(hex);
  const mats = Array.isArray(mesh.material)
    ? mesh.material
    : mesh.material
      ? [mesh.material]
      : [];
  const next: THREE.Material[] = [];
  let changed = false;
  for (const raw of mats) {
    let mat = raw as THREE.MeshStandardMaterial;
    if (!mat || !("color" in mat)) {
      next.push(raw as THREE.Material);
      continue;
    }
    const base = bases.get(mesh.uuid);
    if (!forceFull && base?.preserve) {
      mat.color.copy(base.color).lerp(color, 0.65);
    } else {
      // Clone so shared GLB materials stay intact.
      if (forceFull && "map" in mat && mat.map) {
        mat = mat.clone();
        if (!keepMap) mat.map = null;
        changed = true;
      }
      mat.color.copy(color);
    }
    if (pbr && "metalness" in mat) {
      mat.metalness = pbr.metalness;
      mat.roughness = pbr.roughness;
    }
    mat.needsUpdate = true;
    if (base) {
      base.color.copy(mat.color);
      if (pbr) {
        base.metalness = pbr.metalness;
        base.roughness = pbr.roughness;
      }
    }
    next.push(mat);
  }
  if (changed) {
    mesh.material = next.length === 1 ? next[0]! : next;
  }
}

function applyGhostVehiclePaint(
  root: THREE.Object3D,
  bodyPaintHex: string | null | undefined,
  softTopHex: string | null | undefined,
  bases: Map<string, ShadeBase>,
  /** When true, only tint meshes marked userData.ghost (X-ray body shell). */
  ghostOnly = true,
  bodyPaintPbr?: { metalness: number; roughness: number } | null,
) {
  if (!bodyPaintHex && !softTopHex) return;
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (ghostOnly && !mesh.userData?.ghost) return;
    const name = meshLabel(mesh);
    if (isGlassMeshName(name) || isGlassMeshName(mesh.name)) return;
    if (isRollingStockMeshName(name) || isRollingStockMeshName(mesh.name)) {
      return;
    }
    if (softTopHex && isSoftTopMeshName(name)) {
      tintGhostMesh(mesh, softTopHex, bases, true);
      return;
    }
    // Body colour only on Car_Paint* — never Disk/Hub/Brake/tire/details.
    if (bodyPaintHex && isBodyPaintMeshName(name)) {
      tintGhostMesh(mesh, bodyPaintHex, bases, true, false, bodyPaintPbr);
    }
  });
}

function isGlassMeshName(name: string): boolean {
  return /window|glass|szyba|windshield|windscreen|side.?glass|rear.?glass|scheinwerfer.?glas|light.?glass|透明/i.test(
    name,
  );
}

/** Clear, near-neutral glass (no blue cast). */
function applyGlassMaterials(root: THREE.Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const name = meshLabel(mesh);
    if (!isGlassMeshName(name) && !isGlassMeshName(mesh.name)) return;
    const old = mesh.material;
    if (Array.isArray(old)) {
      for (const m of old) m.dispose();
    } else if (old) {
      old.dispose();
    }
    mesh.material = new THREE.MeshPhysicalMaterial({
      // Near-clear glass — no blue tint (was 0xa8c4d8).
      color: 0xf4f6f7,
      metalness: 0,
      roughness: 0.02,
      transmission: 0.97,
      thickness: 0.08,
      ior: 1.45,
      transparent: true,
      opacity: 0.22,
      side: THREE.DoubleSide,
      depthWrite: false,
      envMapIntensity: 0.25,
    });
  });
}

function applyInteriorLeatherTint(
  root: THREE.Object3D,
  interiorHex: string | null | undefined,
  bases: Map<string, ShadeBase>,
) {
  if (!interiorHex) return;
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const name = meshLabel(mesh);
    if (!isInteriorLeatherMeshName(name) && !isInteriorLeatherMeshName(mesh.name)) {
      return;
    }
    // Partial recolour: keep albedo map (color × texture), do not flood-fill.
    tintGhostMesh(mesh, interiorHex, bases, true, true);
  });
}

function disposeObject(root: THREE.Object3D) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh) {
      mesh.geometry?.dispose();
      const mats = Array.isArray(mesh.material)
        ? mesh.material
        : mesh.material
          ? [mesh.material]
          : [];
      for (const m of mats) m.dispose();
    }
  });
}

type ShadeBase = {
  color: THREE.Color;
  metalness: number;
  roughness: number;
  vertexColors: boolean;
  preserve?: boolean;
  opacity: number;
  transparent: boolean;
};

/** True only for real albedo/normal/etc maps — reject trimesh 1×1–4×4 stubs. */
function mapIsUsable(tex: THREE.Texture | null | undefined): boolean {
  if (!tex) return false;
  const img = tex.image as
    | { width?: number; height?: number; videoWidth?: number; videoHeight?: number }
    | undefined;
  const w = img?.width ?? img?.videoWidth ?? 0;
  const h = img?.height ?? img?.videoHeight ?? 0;
  // ponytail: unknown size (still decoding) → treat as real; only drop known stubs
  if (!w || !h) return true;
  return w > 4 && h > 4;
}

function materialHasTexture(mat: THREE.Material): boolean {
  const m = mat as THREE.MeshStandardMaterial;
  return (
    mapIsUsable(m.map) ||
    mapIsUsable(m.normalMap) ||
    mapIsUsable(m.roughnessMap) ||
    mapIsUsable(m.metalnessMap) ||
    mapIsUsable(m.aoMap) ||
    mapIsUsable(m.emissiveMap)
  );
}

function shadeLoadedModel(root: THREE.Object3D, bases: Map<string, ShadeBase>) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;

    const geo = mesh.geometry;
    if (!geo.getAttribute("normal")) {
      geo.computeVertexNormals();
    }

    const existing = mesh.material;
    const existingOne = Array.isArray(existing) ? existing[0] : existing;
    if (existingOne && materialHasTexture(existingOne as THREE.Material)) {
      const mats = Array.isArray(existing) ? existing : [existing];
      for (const raw of mats) {
        const m = raw as THREE.MeshStandardMaterial;
        if ("emissive" in m) {
          bases.set(mesh.uuid, {
            color: m.color?.clone?.() ?? new THREE.Color(0xffffff),
            metalness: m.metalness ?? 0.2,
            roughness: m.roughness ?? 0.5,
            vertexColors: Boolean(m.vertexColors),
            preserve: true,
            opacity: m.opacity ?? 1,
            transparent: Boolean(m.transparent),
          });
        }
      }
      return;
    }

    const hasColor = Boolean(geo.getAttribute("color"));
    const name = meshLabel(mesh);
    const color = hasColor
      ? new THREE.Color(0xffffff)
      : colorForMeshName(name);
    const metalness = /blok|gear|diff|rim|brake|turbo|exhaust/i.test(name)
      ? 0.55
      : 0.2;
    const roughness = /tire|rubber|filtr|filter/i.test(name) ? 0.85 : 0.45;

    const mat = new THREE.MeshStandardMaterial({
      color,
      metalness,
      roughness,
      vertexColors: hasColor,
      side: THREE.DoubleSide,
    });
    if (Array.isArray(mesh.material)) {
      for (const m of mesh.material) m.dispose();
    } else if (mesh.material) {
      mesh.material.dispose();
    }
    mesh.material = mat;
    bases.set(mesh.uuid, {
      color: color.clone(),
      metalness,
      roughness,
      vertexColors: hasColor,
      preserve: false,
      opacity: 1,
      transparent: false,
    });
  });
}

function applyGhostOpacity(root: THREE.Object3D, opacity: number) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material)
      ? mesh.material
      : mesh.material
        ? [mesh.material]
        : [];
    for (const raw of mats) {
      const mat = raw as THREE.MeshStandardMaterial;
      if (!mat) continue;
      mat.transparent = true;
      mat.opacity = opacity;
      mat.depthWrite = false;
      mat.needsUpdate = true;
    }
  });
}

/** 外观实色 ↔ 透视幽灵；依赖 shade 时写入的 bases.opacity。 */
function setShellOpacity(
  root: THREE.Object3D,
  bases: Map<string, ShadeBase>,
  ghost: boolean,
  ghostOpacity: number,
) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const base = bases.get(mesh.uuid);
    const mats = Array.isArray(mesh.material)
      ? mesh.material
      : mesh.material
        ? [mesh.material]
        : [];
    for (const raw of mats) {
      const mat = raw as THREE.MeshStandardMaterial;
      if (!mat) continue;
      if (ghost) {
        mat.transparent = true;
        mat.opacity = ghostOpacity;
        mat.depthWrite = false;
      } else {
        mat.transparent = Boolean(base?.transparent);
        mat.opacity = base?.opacity ?? 1;
        mat.depthWrite = true;
      }
      mat.needsUpdate = true;
    }
  });
}

function setGroupDimmed(root: THREE.Object3D, dimmed: boolean) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mats = Array.isArray(mesh.material)
      ? mesh.material
      : mesh.material
        ? [mesh.material]
        : [];
    for (const raw of mats) {
      const mat = raw as THREE.MeshStandardMaterial;
      if (!mat) continue;
      if (dimmed) {
        mat.transparent = true;
        mat.opacity = Math.min(mat.opacity ?? 1, 0.22);
        mat.depthWrite = false;
      } else {
        mat.opacity = 1;
        mat.transparent = false;
        mat.depthWrite = true;
      }
      mat.needsUpdate = true;
    }
  });
}

function objectMatches(obj: THREE.Object3D, re: RegExp): boolean {
  let o: THREE.Object3D | null = obj;
  while (o) {
    if (re.test(o.name) || re.test(meshLabel(o))) return true;
    o = o.parent;
  }
  return false;
}

type LidRest = {
  position: THREE.Vector3;
  quaternion: THREE.Quaternion;
  scale: THREE.Vector3;
};

function ensureLidRest(obj: THREE.Object3D): LidRest {
  if (!obj.userData.lidRest) {
    obj.userData.lidRest = {
      position: obj.position.clone(),
      quaternion: obj.quaternion.clone(),
      scale: obj.scale.clone(),
    } satisfies LidRest;
  }
  return obj.userData.lidRest as LidRest;
}

/** 按 CMS doorAngle 绕盖板铰链开合（车坐标 +Z 车头，+Y 上）。 */
function applyCmsLidOpen(
  obj: THREE.Object3D,
  hinge: "front" | "rear",
  angleDeg: number,
  open: boolean,
) {
  const rest = ensureLidRest(obj);
  obj.position.copy(rest.position);
  obj.quaternion.copy(rest.quaternion);
  obj.scale.copy(rest.scale);
  if (!open || !angleDeg) return;

  obj.updateWorldMatrix(true, false);
  const box = new THREE.Box3().setFromObject(obj);
  if (box.isEmpty()) return;
  const hingeWorld = new THREE.Vector3(
    (box.min.x + box.max.x) * 0.5,
    box.min.y,
    hinge === "rear" ? box.min.z : box.max.z,
  );
  const parent = obj.parent;
  if (!parent) return;
  const hingeLocal = parent.worldToLocal(hingeWorld.clone());
  // 前盖铰链在后缘：+Rx 抬起车头侧；后盖铰链在前缘：−Rx 抬起车尾侧
  const signed =
    hinge === "rear"
      ? THREE.MathUtils.degToRad(angleDeg)
      : -THREE.MathUtils.degToRad(angleDeg);
  const q = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(1, 0, 0),
    signed,
  );
  const pos = obj.position.clone().sub(hingeLocal);
  pos.applyQuaternion(q);
  obj.position.copy(hingeLocal).add(pos);
  obj.quaternion.premultiply(q);
}

function resetAllLids(root: THREE.Object3D) {
  root.traverse((o) => {
    const rest = o.userData.lidRest as LidRest | undefined;
    if (!rest) return;
    o.position.copy(rest.position);
    o.quaternion.copy(rest.quaternion);
    o.scale.copy(rest.scale);
  });
}

/** 分栏点选：非焦点当空气（不进 Raycaster intersects）。 */
const ZONE_PICK_NO_RAYCAST: THREE.Mesh["raycast"] = () => {};

function clearZonePickMarks(root: THREE.Object3D) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    delete mesh.userData.zoneFocus;
    delete mesh.userData.pickIgnore;
    if (mesh.raycast === ZONE_PICK_NO_RAYCAST) {
      mesh.raycast = THREE.Mesh.prototype.raycast;
    }
  });
}

function markZonePick(mesh: THREE.Mesh, focus: boolean) {
  if (focus) {
    mesh.userData.zoneFocus = true;
    delete mesh.userData.pickIgnore;
    mesh.raycast = THREE.Mesh.prototype.raycast;
  } else {
    mesh.userData.zoneFocus = false;
    mesh.userData.pickIgnore = true;
    mesh.raycast = ZONE_PICK_NO_RAYCAST;
  }
}

function applyExteriorZoneFocus(
  bodyRoot: THREE.Object3D,
  zone: ExteriorZoneDef | undefined,
  bases: Map<string, ShadeBase>,
) {
  resetAllLids(bodyRoot);
  const match = zone?.match ?? null;
  const focusRoots: THREE.Object3D[] = [];

  bodyRoot.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const hit = !match || objectMatches(mesh, match);
    if (hit) focusRoots.push(mesh);
    markZonePick(mesh, hit);
    // 恢复实色后再按需变暗
    const base = bases.get(mesh.uuid);
    const mats = Array.isArray(mesh.material)
      ? mesh.material
      : mesh.material
        ? [mesh.material]
        : [];
    for (const raw of mats) {
      const mat = raw as THREE.MeshStandardMaterial;
      if (!mat) continue;
      mat.transparent = Boolean(base?.transparent);
      mat.opacity = hit ? (base?.opacity ?? 1) : 0.12;
      mat.depthWrite = hit;
      if (!hit) mat.transparent = true;
      mat.needsUpdate = true;
    }
  });

  if (zone?.lid) {
    const lids: THREE.Object3D[] = [];
    bodyRoot.traverse((o) => {
      if (/_MAT_/i.test(o.name)) return;
      if (zone.lid!.match.test(o.name)) lids.push(o);
    });
    for (const lid of lids) {
      applyCmsLidOpen(lid, zone.lid.hinge, zone.lid.angleDeg, true);
    }
  }

  return focusRoots;
}

/** 内饰分栏：整车保留，车身为透明结构；焦点舱内子 mesh 更实。 */
function applyInteriorZoneFocus(
  bodyRoot: THREE.Object3D,
  zone: InteriorZoneDef,
  _bases: Map<string, ShadeBase>,
) {
  const match = zone.match ?? /SM_Interior/i;
  const focusRoots: THREE.Object3D[] = [];
  const shellOpacity = 0.14;
  const focusOpacity = 0.92;

  bodyRoot.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const softTop =
      isSoftTopMeshName(meshLabel(mesh)) || isSoftTopMeshName(mesh.name);
    // 内饰：顶篷隐藏（不半透挡视线）
    if (softTop) {
      mesh.visible = false;
      markZonePick(mesh, false);
      return;
    }
    mesh.visible = true;
    const paint =
      isBodyPaintMeshName(meshLabel(mesh)) || isBodyPaintMeshName(mesh.name);
    // 座舱文件里的 Car_Paint 归外观，不当内饰焦点
    const underInterior =
      !paint && objectMatches(mesh, /SM_Interior/i);
    // 顶篷永不进内饰点选（含「全部」）
    const hit = underInterior && objectMatches(mesh, match);
    if (hit) focusRoots.push(mesh);
    // 含「全部」：外板等非舱内一律点穿；仅 SM_Interior（及分栏 match）可点
    markZonePick(mesh, hit);
    const mats = Array.isArray(mesh.material)
      ? mesh.material
      : mesh.material
        ? [mesh.material]
        : [];
    for (const raw of mats) {
      const mat = raw as THREE.MeshStandardMaterial;
      if (!mat) continue;
      mat.transparent = true;
      mat.opacity = hit ? focusOpacity : shellOpacity;
      mat.depthWrite = hit;
      mat.needsUpdate = true;
    }
  });

  return focusRoots;
}

function restoreBodyMeshVisibility(bodyRoot: THREE.Object3D) {
  bodyRoot.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.visible = true;
  });
}

function buildFlowRoot(flows: ReadonlyArray<GarageFlowSystem>): THREE.Group {
  const root = new THREE.Group();
  root.name = "garageFlows";
  for (const flow of flows) {
    const sys = new THREE.Group();
    sys.name = `flow:${flow.id}`;
    sys.userData.flowLayer = flow.layer;
    for (const path of flow.paths) {
      if (!path.points?.length) continue;
      const pts = path.points.map(
        (p) => new THREE.Vector3(Number(p[0]), Number(p[1]), Number(p[2])),
      );
      if (pts.length < 2) continue;
      const curve = new THREE.CatmullRomCurve3(pts, Boolean(path.closed));
      const segs = Math.max(16, pts.length * 6);
      const geo = new THREE.TubeGeometry(
        curve,
        segs,
        flow.radius,
        8,
        Boolean(path.closed),
      );
      const mat = new THREE.MeshStandardMaterial({
        color: flow.pipe.color,
        metalness: flow.pipe.metalness,
        roughness: flow.pipe.roughness,
        emissive: new THREE.Color(flow.color),
        emissiveIntensity: 0.2,
      });
      sys.add(new THREE.Mesh(geo, mat));
    }
    for (const node of flow.nodes ?? []) {
      const [w, h, d] = node.size;
      const box = new THREE.Mesh(
        new THREE.BoxGeometry(w, h, d),
        new THREE.MeshStandardMaterial({
          color: node.color,
          metalness: 0.35,
          roughness: 0.55,
        }),
      );
      box.position.set(node.at[0], node.at[1], node.at[2]);
      box.name = node.id;
      sys.add(box);
    }
    root.add(sys);
  }
  return root;
}

function applyHideInUnified(root: THREE.Object3D, hide: string[]) {
  if (!hide.length) return;
  const set = new Set(hide);
  root.traverse((o) => {
    if (set.has(o.name)) o.visible = false;
  });
}

function applyManualTransform(root: THREE.Object3D, t: XrayTransform | null) {
  if (!t) return;
  const [px, py, pz] = t.position;
  const [rx, ry, rz] = t.rotationEuler;
  const [sx, sy, sz] = t.scale;
  root.position.x += px || 0;
  root.position.y += py || 0;
  root.position.z += pz || 0;
  root.rotation.x += rx || 0;
  root.rotation.y += ry || 0;
  root.rotation.z += rz || 0;
  root.scale.x *= sx ?? 1;
  root.scale.y *= sy ?? 1;
  root.scale.z *= sz ?? 1;
}

function resetLocalTransform(root: THREE.Object3D) {
  root.position.set(0, 0, 0);
  root.rotation.set(0, 0, 0);
  root.scale.set(1, 1, 1);
}

type BasePlacement = {
  position: THREE.Vector3;
  rotation: THREE.Euler;
  scale: THREE.Vector3;
};

function storeBasePlacement(root: THREE.Object3D) {
  root.userData.basePlacement = {
    position: root.position.clone(),
    rotation: root.rotation.clone(),
    scale: root.scale.clone(),
  } satisfies BasePlacement;
}

function restoreBasePlacement(root: THREE.Object3D): boolean {
  const b = root.userData.basePlacement as BasePlacement | undefined;
  if (!b) return false;
  root.position.copy(b.position);
  root.rotation.copy(b.rotation);
  root.scale.copy(b.scale);
  return true;
}

/** 从当前物体 TRS 反推 seed 里的 manual transform（相对 basePlacement / baseLocal）。 */
function readManualTransform(obj: THREE.Object3D): XrayTransform {
  const base =
    (obj.userData.basePlacement as BasePlacement | undefined) ||
    (obj.userData.baseLocal as BasePlacement | undefined);
  if (!base) {
    return {
      position: [obj.position.x, obj.position.y, obj.position.z],
      rotationEuler: [obj.rotation.x, obj.rotation.y, obj.rotation.z],
      scale: [obj.scale.x, obj.scale.y, obj.scale.z],
    };
  }
  const sx = base.scale.x || 1;
  const sy = base.scale.y || 1;
  const sz = base.scale.z || 1;
  return {
    position: [
      obj.position.x - base.position.x,
      obj.position.y - base.position.y,
      obj.position.z - base.position.z,
    ],
    rotationEuler: [
      obj.rotation.x - base.rotation.x,
      obj.rotation.y - base.rotation.y,
      obj.rotation.z - base.rotation.z,
    ],
    scale: [obj.scale.x / sx, obj.scale.y / sy, obj.scale.z / sz],
  };
}

function findMeshByName(
  root: THREE.Object3D,
  name: string,
): THREE.Mesh | null {
  let hit: THREE.Mesh | null = null;
  root.traverse((o) => {
    if (hit) return;
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (meshLabel(mesh) === name || mesh.name === name) hit = mesh;
  });
  return hit;
}

/**
 * Place assembly like flat-six UnifiedSceneClient.AssemblyMesh.
 * Does NOT recenter the whole scene — car-space preserved.
 * Caches hotspot/normalize pose in userData.basePlacement (manual TRS applied after).
 * @param side bilateral: -1 | +1；0 = 非镜像
 */
function placeAssembly(
  root: THREE.Object3D,
  assembly: XrayAssembly,
  side: -1 | 0 | 1 = 0,
) {
  applyHideInUnified(root, assembly.hideInUnified || []);
  const hotspot = parseHotspot(assembly.hotspot3d);
  if (assembly.bilateral && side !== 0) {
    hotspot.x = side * (assembly.lateralOffset ?? 0.75);
  }

  if (assembly.carSpace) {
    const ws = assembly.worldScale ?? 1;
    const sx = assembly.mirrorX ? -ws : ws;
    root.position.copy(hotspot);
    root.rotation.set(0, 0, 0);
    root.scale.set(sx, ws, ws);
    storeBasePlacement(root);
    applyManualTransform(root, assembly.transform);
    return;
  }

  // Normalized (bilateral: instance at ±lateralOffset)
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const targetRadius = assembly.displayRadius ?? 0.65;
  const scale = targetRadius / Math.max(sphere.radius, 0.001);
  const center = box.getCenter(new THREE.Vector3()).multiplyScalar(scale);
  let sx = scale;
  if (side < 0) sx = -scale;
  else if (assembly.mirrorX) sx = -scale;
  root.scale.set(sx, scale, scale);
  root.rotation.set(0, 0, 0);
  root.position.set(
    hotspot.x - (side < 0 ? -center.x : center.x),
    hotspot.y - center.y,
    hotspot.z - center.z,
  );
  storeBasePlacement(root);
  applyManualTransform(root, assembly.transform);
}

async function loadGlbScene(
  rel: string,
  readGlb: (rel: string) => Promise<Uint8Array>,
): Promise<{ scene: THREE.Group; objectUrl: string }> {
  const bytes = await readGlb(rel);
  const u8 =
    bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes as ArrayBuffer);
  // Copy to a clean ArrayBuffer — SharedArrayBuffer / subarray breaks GLTFLoader.
  const ab = u8.buffer.slice(
    u8.byteOffset,
    u8.byteOffset + u8.byteLength,
  ) as ArrayBuffer;
  // parse() avoids blob: texture URLs (loadAsync+createObjectURL 易崩/白屏)
  const scene = await new Promise<THREE.Group>((resolve, reject) => {
    new GLTFLoader().parse(
      ab,
      "",
      (gltf) => resolve(gltf.scene),
      (err) =>
        reject(err instanceof Error ? err : new Error(String(err))),
    );
  });
  return { scene, objectUrl: "" };
}

/**
 * CMS / X-ray GLB viewer: orbit + click-to-pick mesh.
 * Single mode frames+centers one model; X-ray keeps flat-six car coordinates.
 */
function storeMeshBaseLocal(root: THREE.Object3D) {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || mesh.userData.baseLocal) return;
    mesh.userData.baseLocal = {
      position: mesh.position.clone(),
      rotation: mesh.rotation.clone(),
      scale: mesh.scale.clone(),
    };
  });
}

function meshStateLayerId(assembly: {
  id: string;
  meshStateOf?: string | null;
  aliasOfGlb?: string | null;
}): string {
  return assembly.meshStateOf || assembly.aliasOfGlb || assembly.id;
}

function applyMeshStateToWrap(
  wrap: THREE.Object3D,
  meshStateKey: string,
  meshState: XrayMeshState | null | undefined,
  hideInUnified: string[],
  visibilityKey?: string,
) {
  const visKey = visibilityKey ?? meshStateKey;
  const meshLayer = meshState?.layers?.[meshStateKey];
  const visLayer = meshState?.layers?.[visKey];
  const layerVisible = visLayer?.visible !== false;
  wrap.visible = layerVisible;
  const hideSet = new Set(hideInUnified || []);
  wrap.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (!mesh.userData.baseLocal) {
      mesh.userData.baseLocal = {
        position: mesh.position.clone(),
        rotation: mesh.rotation.clone(),
        scale: mesh.scale.clone(),
      };
    }
    const bl = mesh.userData.baseLocal as BasePlacement;
    mesh.position.copy(bl.position);
    mesh.rotation.copy(bl.rotation);
    mesh.scale.copy(bl.scale);
    const name = meshLabel(mesh);
    const entry = meshLayer?.meshes?.[name];
    applyManualTransform(mesh, entry?.transform ?? null);
    if (hideSet.has(mesh.name) || hideSet.has(name)) {
      mesh.visible = false;
      return;
    }
    mesh.visible = layerVisible && entry?.visible !== false;
  });
}

type LayerEntry = {
  wrap: THREE.Group;
  assembly: XrayAssembly;
  /** bilateral 时子实例（各有 basePlacement） */
  sides?: THREE.Group[];
};

type Runtime = {
  pickRoot: THREE.Object3D;
  bases: Map<string, ShadeBase>;
  applyHighlights: () => void;
  layerEntries: Map<string, LayerEntry>;
  bodyWrap: THREE.Group | null;
  /** Single-model adjustable root (TRS from previewTransforms[singleAssetId]). */
  singleWrap: THREE.Group | null;
  singleAssetId: string | null;
  flowRoot: THREE.Group | null;
  ghostBodyTf: XrayTransform | null;
  ghostOpacity: number;
  reapplyTransforms: (
    preview: Readonly<Record<string, XrayTransform>> | null | undefined,
    meshState?: XrayMeshState | null,
  ) => void;
  applyGarageVisibility: (
    mode: GarageViewMode,
    structure: GarageStructureId,
    exteriorZone: ExteriorZoneId,
    interiorZone: InteriorZoneId,
  ) => void;
  /** 车库透视：不拆 WebGL，按需灌机械层 */
  loadGarageMech?: (
    onProgress?: (done: number, total: number) => void,
  ) => Promise<void>;
  unloadGarageMech?: () => void;
  frameFocus: (objects: THREE.Object3D[]) => void;
  frameLook: (
    target: [number, number, number],
    eye: [number, number, number],
  ) => void;
  cabinFill: THREE.PointLight | null;
  attachGizmo: (
    assemblyId: string | null,
    meshName: string | null,
    mode: GizmoMode,
  ) => void;
  applyViewPreset: (id: ViewPresetId) => void;
  gizmoDragging: () => boolean;
};

export function LocatorGlbViewer({
  glbRel = null,
  singleAssetId = null,
  layers = null,
  ghostBody = null,
  showGhostBody = true,
  garageViewMode = null,
  garageStructure = "all",
  garageFlows = null,
  exteriorZone = "all",
  interiorZone = "all",
  readGlb,
  selectedMeshName,
  linkedMeshNames,
  selectedAssemblyId = null,
  previewTransforms = null,
  previewMeshState = null,
  bodyPaintHex = null,
  bodyPaintPbr = null,
  softTopHex = null,
  interiorHex = null,
  hideSoftTop = false,
  interiorFillLight = false,
  onMeshesReady,
  linkedAssemblyIds = null,
  onPickMesh,
  onPickEmpty,
  gizmoEnabled = false,
  gizmoMode = "translate",
  gizmoAssemblyId = null,
  gizmoMeshName = null,
  onGizmoTransformChange,
  viewPreset = null,
}: LocatorGlbViewerProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [loadProgress, setLoadProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const pickRef = useRef(onPickMesh);
  pickRef.current = onPickMesh;
  const pickEmptyRef = useRef(onPickEmpty);
  pickEmptyRef.current = onPickEmpty;
  const meshesReadyRef = useRef(onMeshesReady);
  meshesReadyRef.current = onMeshesReady;
  const gizmoChangeRef = useRef(onGizmoTransformChange);
  gizmoChangeRef.current = onGizmoTransformChange;
  const gizmoEnabledRef = useRef(gizmoEnabled);
  gizmoEnabledRef.current = gizmoEnabled;
  const selectedRef = useRef(selectedMeshName);
  selectedRef.current = selectedMeshName;
  const linkedRef = useRef(linkedMeshNames);
  linkedRef.current = linkedMeshNames;
  const selectedAsmRef = useRef(selectedAssemblyId);
  selectedAsmRef.current = selectedAssemblyId;
  const linkedAsmRef = useRef(linkedAssemblyIds);
  linkedAsmRef.current = linkedAssemblyIds;
  const previewRef = useRef(previewTransforms);
  previewRef.current = previewTransforms;
  const meshStateRef = useRef(previewMeshState);
  meshStateRef.current = previewMeshState;
  const bodyPaintRef = useRef(bodyPaintHex);
  bodyPaintRef.current = bodyPaintHex;
  const bodyPaintPbrRef = useRef(bodyPaintPbr);
  bodyPaintPbrRef.current = bodyPaintPbr;
  const softTopRef = useRef(softTopHex);
  softTopRef.current = softTopHex;
  const interiorRef = useRef(interiorHex);
  interiorRef.current = interiorHex;
  const hideSoftTopRef = useRef(hideSoftTop);
  hideSoftTopRef.current = hideSoftTop;
  const fillLightRef = useRef(interiorFillLight);
  fillLightRef.current = interiorFillLight;
  const garageModeRef = useRef(garageViewMode);
  garageModeRef.current = garageViewMode;
  const garageStructureRef = useRef(garageStructure);
  garageStructureRef.current = garageStructure;
  const exteriorZoneRef = useRef(exteriorZone);
  exteriorZoneRef.current = exteriorZone;
  const interiorZoneRef = useRef(interiorZone);
  interiorZoneRef.current = interiorZone;
  const runtimeRef = useRef<Runtime | null>(null);

  const garageUnified = garageViewMode != null;
  // layers!=null → X-ray 模式（可为空：仅幽灵壳）；车库统一场景强制 X-ray 加载路径
  const xrayMode = garageUnified || layers != null;
  const hasContent =
    (xrayMode &&
      ((layers?.length ?? 0) > 0 ||
        Boolean(showGhostBody && ghostBody?.present))) ||
    Boolean(glbRel);

  // ponytail: omit transform from reload key — sliders update via reapplyTransforms
  const layersKey = xrayMode
    ? (layers ?? [])
        .map(
          (l) =>
            `${l.id}:${l.glbRel}:${l.hotspot3d}:${l.carSpace}:${l.worldScale}:${l.bilateral ? 1 : 0}`,
        )
        .join("|")
    : "";
  const ghostKey =
    showGhostBody && ghostBody?.present
      ? `${ghostBody.rel}:${ghostBody.opacity}`
      : "";
  const flowsKey = garageFlows?.map((f) => f.id).join("|") ?? "";

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !hasContent) return;

    let disposed = false;
    const objectUrls: string[] = [];
    const bases = new Map<string, ShadeBase>();
    const worldRoot = new THREE.Group();
    worldRoot.name = "locatorWorld";

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x2a2826);
    scene.add(worldRoot);

    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 200);
    camera.position.set(1.2, 0.9, 1.6);

    const presentMechCount =
      layers?.filter((l) => l.present).length ?? 0;
    /** 多于 2 层时主循环不灌满，改走渐进 loadGarageMech（含 Locator / 微调窗） */
    const deferManyMech = presentMechCount > 2;

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 0.2, 0);

    const transformControls = new TransformControls(camera, renderer.domElement);
    transformControls.setMode("translate");
    transformControls.enabled = false;
    let gizmoDragging = false;
    let gizmoAssembly: string | null = null;
    let gizmoMesh: string | null = null;
    transformControls.addEventListener("objectChange", () => {
      const obj = transformControls.object;
      if (!obj || !gizmoAssembly) return;
      gizmoChangeRef.current?.(
        gizmoAssembly,
        readManualTransform(obj),
        gizmoMesh,
      );
    });
    scene.add(transformControls.getHelper());
    const worldAxes = new THREE.AxesHelper(0.35);
    worldAxes.visible = false;
    scene.add(worldAxes);

    scene.add(new THREE.HemisphereLight(0xf0f4ff, 0x3a342c, 0.85));
    const key = new THREE.DirectionalLight(0xffffff, 1.35);
    key.position.set(2.2, 3.2, 1.6);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xc8d8ff, 0.45);
    fill.position.set(-2.4, 1.2, -1.2);
    scene.add(fill);
    const rim = new THREE.DirectionalLight(0xffe6c8, 0.35);
    rim.position.set(0.2, 1.5, -2.5);
    scene.add(rim);
    // ponytail: cabin fill — one point light; ceiling ≈ intensity 0.9
    const cabinFill = new THREE.PointLight(0xfff2e0, fillLightRef.current ? 0.9 : 0, 8, 2);
    cabinFill.position.set(0, 0.55, 0.1);
    cabinFill.name = "cabinFill";
    scene.add(cabinFill);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();

    const loadMechLayers =
      xrayMode && layers != null && !garageUnified && !deferManyMech;
    const loadBodyShell = Boolean(showGhostBody && ghostBody?.present);
    const loadFlows =
      Boolean(garageFlows?.length) && !garageUnified && !deferManyMech;

    function applyHighlights() {
      const selected = selectedRef.current;
      const linked = linkedRef.current;
      const selAsm = selectedAsmRef.current;
      const linkedAsms = linkedAsmRef.current;
      worldRoot.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        const base = bases.get(mesh.uuid);
        if (!base) return;
        const name = meshLabel(mesh);
        const asmId = (mesh.userData?.assemblyId as string | undefined) ?? null;
        const isGhost = Boolean(mesh.userData?.ghost);
        const asmLinked = Boolean(asmId && linkedAsms?.has(asmId));
        // mesh 名命中，或仅选中装配时整层高亮，或 OEM 反查的多装配
        const isSel =
          (selected != null &&
            name === selected &&
            (!selAsm || asmId === selAsm)) ||
          (selected == null && selAsm != null && asmId === selAsm) ||
          (selected == null && !selAsm && asmLinked);
        const isLinked =
          (linked?.has(name) ?? false) || (asmLinked && !isSel);
        const mats = Array.isArray(mesh.material)
          ? mesh.material
          : mesh.material
            ? [mesh.material]
            : [];
        for (const raw of mats) {
          const mat = raw as THREE.MeshStandardMaterial;
          if (!mat || !("emissive" in mat)) continue;
          // 幽灵壳不还原漆色，但仍必须清/写 emissive，否则换选后旧高亮残留
          if (!base.preserve && !isGhost) {
            mat.color.copy(base.color);
            mat.metalness = base.metalness;
            mat.roughness = base.roughness;
            mat.vertexColors = base.vertexColors;
          }
          if (isSel) {
            mat.emissive.setHex(0xb45028);
            mat.emissiveIntensity = isGhost ? 0.65 : 0.45;
          } else if (isLinked) {
            mat.emissive.setHex(0x3a6b4a);
            mat.emissiveIntensity = isGhost ? 0.35 : 0.22;
          } else {
            mat.emissive.setHex(0x000000);
            mat.emissiveIntensity = 0;
          }
          mat.needsUpdate = true;
        }
      });
      applyFocusDim();
    }

    const layerEntries = new Map<string, LayerEntry>();
    let bodyWrap: THREE.Group | null = null;
    let singleWrap: THREE.Group | null = null;
    let flowRoot: THREE.Group | null = null;
    const ghostOpacity = ghostBody?.opacity ?? 0.08;

    function applyFocusDim(structureOverride?: GarageStructureId) {
      const selected = selectedRef.current;
      const selAsm = selectedAsmRef.current;
      const structure = structureOverride ?? garageStructureRef.current;
      const flowStructure =
        structure === "cabin" ||
        structure === "air" ||
        structure === "lines" ||
        structure === "vacuum" ||
        structure === "wiring";
      const focusAsm =
        selected &&
        selAsm &&
        selAsm !== "body" &&
        layerEntries.has(selAsm)
          ? selAsm
          : null;
      for (const [id, entry] of layerEntries) {
        const layerStruct = entry.assembly.garageStructure ?? "mechanical";
        if (focusAsm) {
          setGroupDimmed(entry.wrap, id !== focusAsm);
        } else if (structure === "all") {
          setGroupDimmed(entry.wrap, false);
        } else if (structure === "mechanical") {
          setGroupDimmed(entry.wrap, layerStruct !== "mechanical");
        } else if (flowStructure) {
          setGroupDimmed(entry.wrap, layerStruct !== structure);
        } else {
          setGroupDimmed(entry.wrap, false);
        }
      }
    }

    function reapplyTransforms(
      preview: Readonly<Record<string, XrayTransform>> | null | undefined,
      meshState?: XrayMeshState | null,
    ) {
      if (gizmoDragging) return;
      const ms = meshState ?? meshStateRef.current;
      for (const [id, entry] of layerEntries) {
        const t = preview?.[id] ?? entry.assembly.transform;
        const targets = entry.sides?.length ? entry.sides : [entry.wrap];
        for (const target of targets) {
          if (restoreBasePlacement(target)) {
            applyManualTransform(target, t);
            if (id === "susp") {
              applyManualTransform(target, preview?.driveline ?? null);
            }
          } else {
            resetLocalTransform(target);
            const side = (target.userData.placementSide as -1 | 0 | 1) ?? 0;
            placeAssembly(
              target,
              { ...entry.assembly, transform: t },
              side,
            );
            if (id === "susp") {
              applyManualTransform(target, preview?.driveline ?? null);
            }
          }
        }
        applyMeshStateToWrap(
          entry.wrap,
          meshStateLayerId(entry.assembly),
          ms,
          entry.assembly.hideInUnified || [],
          entry.assembly.id,
        );
      }
      if (bodyWrap) {
        resetLocalTransform(bodyWrap);
        applyManualTransform(
          bodyWrap,
          preview?.body ?? runtimeRef.current?.ghostBodyTf ?? null,
        );
        applyMeshStateToWrap(bodyWrap, "body", ms, []);
      }
      if (singleWrap && singleAssetId) {
        if (restoreBasePlacement(singleWrap)) {
          applyManualTransform(singleWrap, preview?.[singleAssetId] ?? null);
        } else {
          resetLocalTransform(singleWrap);
          applyManualTransform(singleWrap, preview?.[singleAssetId] ?? null);
        }
        applyMeshStateToWrap(singleWrap, singleAssetId, ms, []);
      }
    }

    function frameFocus(objects: THREE.Object3D[]) {
      const box = new THREE.Box3();
      for (const o of objects) {
        if (o.visible) box.expandByObject(o);
      }
      if (box.isEmpty()) {
        frameWorld(worldRoot);
        return;
      }
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z, 0.001);
      controls.target.copy(center);
      camera.position.set(
        center.x + maxDim * 1.2,
        center.y + maxDim * 0.7,
        center.z + maxDim * 1.4,
      );
      camera.near = maxDim / 200;
      camera.far = maxDim * 50;
      camera.updateProjectionMatrix();
      controls.update();
    }

    function frameLook(
      target: [number, number, number],
      eye: [number, number, number],
    ) {
      controls.target.set(target[0], target[1], target[2]);
      camera.position.set(eye[0], eye[1], eye[2]);
      camera.near = 0.05;
      camera.far = 80;
      camera.updateProjectionMatrix();
      controls.update();
    }

    /** 车库透视：车身幽灵半透（seed opacity=1 是外观实色，不能直接用）。 */
    const GARAGE_XRAY_BODY_OPACITY = 0.14;

    function markBodyUnpickable(root: THREE.Object3D) {
      root.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (!mesh.isMesh) return;
        markZonePick(mesh, false);
      });
    }

    function applyGarageVisibility(
      mode: GarageViewMode,
      structure: GarageStructureId,
      extZoneId: ExteriorZoneId = "all",
      intZoneId: InteriorZoneId = "all",
    ) {
      const xrayOn = mode === "xray";
      const extZone =
        EXTERIOR_ZONES.find((z) => z.id === extZoneId) ?? EXTERIOR_ZONES[0];
      const intZone =
        INTERIOR_ZONES.find((z) => z.id === intZoneId) ?? INTERIOR_ZONES[0];

      if (bodyWrap) {
        bodyWrap.visible = true;
        if (!xrayOn) {
          restoreBodyMeshVisibility(bodyWrap);
          setShellOpacity(bodyWrap, bases, false, 1);
          resetAllLids(bodyWrap);
          clearZonePickMarks(bodyWrap);
          // ponytail: 切换外观/内饰/透视与分栏不改相机，保留用户当前视角
          if (mode === "exterior" && extZone && extZone.id !== "all") {
            applyExteriorZoneFocus(bodyWrap, extZone, bases);
          } else if (mode === "interior") {
            applyInteriorZoneFocus(bodyWrap, intZone, bases);
          }
        } else {
          // 透视：车身半透幽灵，射线点穿（不可点）
          restoreBodyMeshVisibility(bodyWrap);
          resetAllLids(bodyWrap);
          setShellOpacity(bodyWrap, bases, true, GARAGE_XRAY_BODY_OPACITY);
          markBodyUnpickable(bodyWrap);
        }
      }
      for (const entry of layerEntries.values()) {
        const iz = entry.assembly.interiorZone as InteriorZoneId | null | undefined;
        if (xrayOn) {
          // interiorZone 件仅顶栏内饰，不进车库透视
          if (iz) {
            entry.wrap.visible = false;
            markBodyUnpickable(entry.wrap);
          } else {
            entry.wrap.visible = true;
            clearZonePickMarks(entry.wrap);
          }
        } else if (mode === "interior" && iz) {
          entry.wrap.visible = intZoneId === "all" || intZoneId === iz;
          if (entry.wrap.visible) clearZonePickMarks(entry.wrap);
          else markBodyUnpickable(entry.wrap);
        } else {
          entry.wrap.visible = false;
          markBodyUnpickable(entry.wrap);
        }
      }
      if (xrayOn) applyFocusDim(structure);
      if (flowRoot) {
        const showFlows = xrayOn && structure !== "mechanical";
        flowRoot.visible = showFlows;
        for (const child of flowRoot.children) {
          const fl = child.userData.flowLayer as string;
          child.visible =
            showFlows && (structure === "all" || structure === fl);
        }
        if (showFlows) clearZonePickMarks(flowRoot);
        else markBodyUnpickable(flowRoot);
      }
      // restore / zone focus 会把 mesh.visible 拉回 true；再套一次顶篷显隐
      applySoftTopVisibility(worldRoot, hideSoftTopRef.current);
    }

    let mechLoadGen = 0;

    async function addMechLayer(layer: XrayAssembly): Promise<boolean> {
      if (!layer.present || layerEntries.has(layer.id)) return false;
      let glbScene: THREE.Group;
      let objectUrl: string;
      try {
        ({ scene: glbScene, objectUrl } = await loadGlbScene(
          layer.glbRel,
          readGlb,
        ));
      } catch (e) {
        console.error("glb_load_fail", layer.id, layer.glbRel, e);
        return false;
      }
      if (disposed) {
        disposeObject(glbScene);
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        return false;
      }
      if (objectUrl) objectUrls.push(objectUrl);
      shadeLoadedModel(glbScene, bases);
      applyGlassMaterials(glbScene);
      glbScene.traverse((o) => {
        o.userData.assemblyId = layer.id;
      });
      storeMeshBaseLocal(glbScene);

      const preview = previewRef.current;
      const t = preview?.[layer.id] ?? layer.transform;
      const parent = new THREE.Group();
      parent.name = `assembly:${layer.id}`;
      parent.userData.assemblyId = layer.id;

      const sides: THREE.Group[] = [];
      const sideList: (-1 | 0 | 1)[] = layer.bilateral ? [-1, 1] : [0];
      for (let i = 0; i < sideList.length; i++) {
        const side = sideList[i]!;
        const piece =
          i === 0 ? glbScene : (glbScene.clone(true) as THREE.Group);
        if (i > 0) {
          shadeLoadedModel(piece, bases);
          piece.traverse((o) => {
            o.userData.assemblyId = layer.id;
          });
          storeMeshBaseLocal(piece);
        }
        const wrap = new THREE.Group();
        wrap.name =
          side === 0
            ? `assembly:${layer.id}`
            : `assembly:${layer.id}:${side < 0 ? "L" : "R"}`;
        wrap.userData.assemblyId = layer.id;
        wrap.userData.placementSide = side;
        wrap.add(piece);
        placeAssembly(wrap, { ...layer, transform: t }, side);
        if (layer.id === "susp") {
          applyManualTransform(wrap, preview?.driveline ?? null);
        }
        parent.add(wrap);
        sides.push(wrap);
      }

      applyMeshStateToWrap(
        parent,
        meshStateLayerId(layer),
        meshStateRef.current,
        layer.hideInUnified || [],
        layer.id,
      );
      if (layer.id === "interior") {
        applyInteriorLeatherTint(parent, interiorRef.current, bases);
      }
      layerEntries.set(layer.id, {
        wrap: parent,
        assembly: layer,
        sides,
      });
      worldRoot.add(parent);
      // 外观/内饰预加载机械层时立刻不可点（不等整批 load 结束）
      if (
        garageUnified &&
        (garageModeRef.current ?? "exterior") !== "xray"
      ) {
        parent.visible = false;
        markBodyUnpickable(parent);
      }
      return true;
    }

    async function loadGarageMech(
      onProgress?: (done: number, total: number) => void,
    ) {
      if (!layers?.length) return;
      const gen = ++mechLoadGen;
      const present = layers.filter((l) => l.present);
      const total = present.length;
      let done = present.filter((l) => layerEntries.has(l.id)).length;
      onProgress?.(done, total);
      for (const layer of present) {
        if (disposed || gen !== mechLoadGen) return;
        if (layerEntries.has(layer.id)) continue;
        await addMechLayer(layer);
        done += 1;
        onProgress?.(done, total);
        // 让出主线程，避免连续解码把页面卡白
        await new Promise((r) => setTimeout(r, 0));
      }
      if (disposed || gen !== mechLoadGen) return;

      if (garageFlows?.length && !flowRoot) {
        flowRoot = buildFlowRoot(garageFlows);
        worldRoot.add(flowRoot);
        if (runtimeRef.current) runtimeRef.current.flowRoot = flowRoot;
      }

      const byAsm: Record<string, string[]> = {};
      for (const [id, entry] of layerEntries) {
        const names: string[] = [];
        const seen = new Set<string>();
        entry.wrap.traverse((o) => {
          const m = o as THREE.Mesh;
          if (!m.isMesh) return;
          const n = meshLabel(m);
          if (seen.has(n)) return;
          seen.add(n);
          names.push(n);
        });
        names.sort();
        byAsm[id] = names;
      }
      if (bodyWrap) {
        const names: string[] = [];
        const seen = new Set<string>();
        bodyWrap.traverse((o) => {
          const m = o as THREE.Mesh;
          if (!m.isMesh) return;
          const n = meshLabel(m);
          if (seen.has(n)) return;
          seen.add(n);
          names.push(n);
        });
        names.sort();
        byAsm.body = names;
      }
      meshesReadyRef.current?.(byAsm);
      // 车库统一场景：切透视加载机械层时不重框，保持用户当前视角/页面构图
      if (!garageUnified) {
        frameWorld(worldRoot);
      }
      applyHighlights();
      applyGarageVisibility(
        garageModeRef.current ?? "xray",
        garageStructureRef.current,
        exteriorZoneRef.current,
        interiorZoneRef.current,
      );
    }

    function unloadGarageMech() {
      mechLoadGen += 1;
      for (const [id, entry] of [...layerEntries.entries()]) {
        worldRoot.remove(entry.wrap);
        disposeObject(entry.wrap);
        layerEntries.delete(id);
      }
      if (flowRoot) {
        worldRoot.remove(flowRoot);
        disposeObject(flowRoot);
        flowRoot = null;
        if (runtimeRef.current) runtimeRef.current.flowRoot = null;
      }
    }

    function attachGizmo(
      assemblyId: string | null,
      meshName: string | null,
      mode: GizmoMode,
    ) {
      transformControls.detach();
      transformControls.enabled = false;
      gizmoAssembly = null;
      gizmoMesh = null;
      if (!gizmoEnabledRef.current || !assemblyId) {
        worldAxes.visible = false;
        return;
      }
      worldAxes.visible = true;

      let target: THREE.Object3D | null = null;
      if (singleWrap && singleAssetId && assemblyId === singleAssetId) {
        target = meshName
          ? findMeshByName(singleWrap, meshName)
          : singleWrap;
      } else if (assemblyId === "body" && bodyWrap) {
        target = meshName ? findMeshByName(bodyWrap, meshName) : bodyWrap;
        if (target === bodyWrap && !bodyWrap.userData.basePlacement) {
          bodyWrap.userData.basePlacement = {
            position: new THREE.Vector3(0, 0, 0),
            rotation: new THREE.Euler(0, 0, 0),
            scale: new THREE.Vector3(1, 1, 1),
          };
        }
      } else {
        const entry = layerEntries.get(assemblyId);
        if (entry) {
          const side0 = entry.sides?.[0] ?? entry.wrap;
          target = meshName ? findMeshByName(entry.wrap, meshName) : side0;
        }
      }
      if (!target) return;
      transformControls.setMode(mode);
      transformControls.attach(target);
      transformControls.enabled = true;
      gizmoAssembly = assemblyId;
      gizmoMesh = meshName;
    }

    function applyViewPreset(id: ViewPresetId) {
      const box = new THREE.Box3().setFromObject(worldRoot);
      const center = box.isEmpty()
        ? new THREE.Vector3(0, 0.2, 0)
        : box.getCenter(new THREE.Vector3());
      const size = box.isEmpty()
        ? new THREE.Vector3(2, 1, 4)
        : box.getSize(new THREE.Vector3());
      const dist = Math.max(size.x, size.y, size.z, 0.5) * 1.6;
      const eye = center.clone();
      switch (id) {
        case "front":
          eye.set(center.x, center.y, center.z + dist);
          break;
        case "back":
          eye.set(center.x, center.y, center.z - dist);
          break;
        case "left":
          eye.set(center.x - dist, center.y, center.z);
          break;
        case "right":
          eye.set(center.x + dist, center.y, center.z);
          break;
        case "top":
          eye.set(center.x, center.y + dist, center.z + 0.01);
          break;
        case "bottom":
          eye.set(center.x, center.y - dist, center.z + 0.01);
          break;
        default:
          eye.set(
            center.x + dist * 0.75,
            center.y + dist * 0.55,
            center.z + dist * 0.85,
          );
      }
      controls.target.copy(center);
      camera.position.copy(eye);
      camera.near = dist / 200;
      camera.far = dist * 40;
      camera.updateProjectionMatrix();
      controls.update();
    }

    transformControls.addEventListener("dragging-changed", (event: { value?: boolean }) => {
      const dragging = Boolean(event.value);
      gizmoDragging = dragging;
      controls.enabled = !dragging;
      if (!dragging && gizmoAssembly) {
        reapplyTransforms(previewRef.current, meshStateRef.current);
        attachGizmo(
          gizmoAssembly,
          gizmoMesh,
          transformControls.mode as GizmoMode,
        );
      }
    });

    runtimeRef.current = {
      pickRoot: worldRoot,
      bases,
      applyHighlights,
      layerEntries,
      bodyWrap: null,
      singleWrap: null,
      singleAssetId: singleAssetId ? String(singleAssetId) : null,
      flowRoot: null,
      ghostBodyTf: ghostBody?.transform ?? null,
      ghostOpacity,
      reapplyTransforms,
      applyGarageVisibility,
      loadGarageMech,
      unloadGarageMech,
      frameFocus,
      frameLook,
      cabinFill,
      attachGizmo,
      applyViewPreset,
      gizmoDragging: () => gizmoDragging,
    };

    function resize() {
      const w = host.clientWidth || 320;
      const h = host.clientHeight || 240;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    /** X-ray: frame camera to whole world without moving models. */
    function frameWorld(root: THREE.Object3D) {
      const box = new THREE.Box3().setFromObject(root);
      if (box.isEmpty()) return;
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z, 0.001);
      controls.target.copy(center);
      camera.position.set(
        center.x + maxDim * 1.1,
        center.y + maxDim * 0.7,
        center.z + maxDim * 1.3,
      );
      camera.near = maxDim / 200;
      camera.far = maxDim * 50;
      camera.updateProjectionMatrix();
      controls.update();
    }

    /** 左键拖动轨道后松手会冒泡 click；用位移阈值区分单击点选 */
    const PICK_DRAG_PX = 5;
    let pointerDownX = 0;
    let pointerDownY = 0;
    let pointerDragged = false;

    function onPointerDown(ev: PointerEvent) {
      if (ev.button !== 0) return;
      pointerDownX = ev.clientX;
      pointerDownY = ev.clientY;
      pointerDragged = false;
    }

    function onPointerMove(ev: PointerEvent) {
      if ((ev.buttons & 1) === 0) return;
      const dx = ev.clientX - pointerDownX;
      const dy = ev.clientY - pointerDownY;
      if (dx * dx + dy * dy > PICK_DRAG_PX * PICK_DRAG_PX) {
        pointerDragged = true;
      }
    }

    function onClick(ev: MouseEvent) {
      if (pointerDragged) {
        pointerDragged = false;
        return;
      }
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      // 非焦点已设空 raycast（pickIgnore），不进 intersects；再 solid 优先于 ghost
      const hits = raycaster.intersectObject(worldRoot, true);
      const mode = garageModeRef.current;
      const solid = hits.find((h) => {
        const m = h.object as THREE.Mesh;
        if (!m.isMesh || m.userData?.pickIgnore || m.userData?.ghost) {
          return false;
        }
        const asm = m.userData?.assemblyId as string | undefined;
        if (garageUnified && mode !== "xray" && asm && asm !== "body") {
          const iz = layerEntries.get(asm)?.assembly.interiorZone;
          if (!iz || mode !== "interior") return false;
          const z = interiorZoneRef.current;
          if (z !== "all" && z !== iz) return false;
        }
        return true;
      });
      const ghost = hits.find((h) => {
        const m = h.object as THREE.Mesh;
        return m.isMesh && !m.userData?.pickIgnore && m.userData?.ghost;
      });
      const hit = solid ?? ghost;
      if (!hit) {
        pickEmptyRef.current?.();
        return;
      }
      const obj = hit.object;
      const asmId =
        (obj.userData?.assemblyId as string | undefined) ??
        (obj.parent?.userData?.assemblyId as string | undefined) ??
        null;
      pickRef.current(meshLabel(obj), meshPath(obj), asmId);
    }
    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("click", onClick);
    const onContextLost = (ev: Event) => {
      ev.preventDefault();
      setViewerError("WebGL 上下文丢失（显存可能不足）。请切回外观后再开透视。");
    };
    canvas.addEventListener("webglcontextlost", onContextLost);

    let raf = 0;
    function tick() {
      if (disposed) return;
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);

    (async () => {
      try {
        if (loadMechLayers && layers) {
          for (const layer of layers) {
            if (!layer.present) continue;
            await addMechLayer(layer);
            await new Promise((r) => setTimeout(r, 0));
          }
        }

        if (loadBodyShell && ghostBody?.present) {
            if (garageUnified) {
              const mechN = layers?.filter((l) => l.present).length ?? 0;
              setLoadProgress({ done: 0, total: Math.max(1 + mechN, 1) });
            }
            const { scene: bodyScene, objectUrl } = await loadGlbScene(
              ghostBody.rel,
              readGlb,
            );
            if (disposed) {
              disposeObject(bodyScene);
              if (objectUrl) URL.revokeObjectURL(objectUrl);
              return;
            }
            if (objectUrl) objectUrls.push(objectUrl);
            const wrap = new THREE.Group();
            wrap.name = "bodyShell";
            wrap.userData.ghost = true;
            wrap.userData.assemblyId = "body";
            shadeLoadedModel(bodyScene, bases);
            applyGlassMaterials(bodyScene);
            applyGhostVehiclePaint(
              bodyScene,
              bodyPaintRef.current,
              softTopRef.current,
              bases,
              true,
              bodyPaintPbrRef.current,
            );
            applyInteriorLeatherTint(
              bodyScene,
              interiorRef.current,
              bases,
            );
            // 车库：初始按模式；透视=半透幽灵车身（不可点）
            if (garageUnified) {
              const gm = garageModeRef.current ?? "exterior";
              if (gm === "xray") {
                setShellOpacity(
                  bodyScene,
                  bases,
                  true,
                  GARAGE_XRAY_BODY_OPACITY,
                );
                markBodyUnpickable(bodyScene);
              } else {
                setShellOpacity(bodyScene, bases, false, 1);
              }
            } else {
              applyGhostOpacity(bodyScene, ghostBody.opacity ?? 0.16);
            }
            bodyScene.traverse((o) => {
              o.userData.ghost = true;
              o.userData.assemblyId = "body";
            });
            wrap.add(bodyScene);
            storeMeshBaseLocal(bodyScene);
            const preview = previewRef.current;
            applyManualTransform(
              wrap,
              preview?.body ?? ghostBody.transform,
            );
            applyMeshStateToWrap(wrap, "body", meshStateRef.current, []);
            bodyWrap = wrap;
            if (runtimeRef.current) {
              runtimeRef.current.bodyWrap = wrap;
              runtimeRef.current.ghostBodyTf = ghostBody.transform ?? null;
            }
            worldRoot.add(wrap);
            if (garageUnified) {
              const mechN = layers?.filter((l) => l.present).length ?? 0;
              setLoadProgress({ done: 1, total: Math.max(1 + mechN, 1) });
            }
        }

        if (loadFlows && garageFlows?.length) {
            flowRoot = buildFlowRoot(garageFlows);
            worldRoot.add(flowRoot);
            if (runtimeRef.current) runtimeRef.current.flowRoot = flowRoot;
        }

        if (garageUnified) {
            applyGarageVisibility(
              garageModeRef.current ?? "exterior",
              garageStructureRef.current,
              exteriorZoneRef.current,
              interiorZoneRef.current,
            );
            applySoftTopVisibility(
              worldRoot,
              hideSoftTopRef.current,
            );
        }

        if (loadMechLayers || loadBodyShell || loadFlows) {
          const byAsm: Record<string, string[]> = {};
          for (const [id, entry] of layerEntries) {
            const names: string[] = [];
            const seen = new Set<string>();
            entry.wrap.traverse((o) => {
              const m = o as THREE.Mesh;
              if (!m.isMesh) return;
              const n = meshLabel(m);
              if (seen.has(n)) return;
              seen.add(n);
              names.push(n);
            });
            names.sort();
            byAsm[id] = names;
          }
          if (bodyWrap) {
            const names: string[] = [];
            const seen = new Set<string>();
            bodyWrap.traverse((o) => {
              const m = o as THREE.Mesh;
              if (!m.isMesh) return;
              const n = meshLabel(m);
              if (seen.has(n)) return;
              seen.add(n);
              names.push(n);
            });
            names.sort();
            byAsm.body = names;
          }
          meshesReadyRef.current?.(byAsm);

          frameWorld(worldRoot);
          applyHighlights();

          // 车库：进页即灌满机械层，进度含车身
          if (garageUnified && !disposed) {
            const bodyBit = bodyWrap ? 1 : 0;
            const mechN = layers?.filter((l) => l.present).length ?? 0;
            setViewerError(null);
            try {
              if (mechN > 0) {
                await loadGarageMech((done, total) => {
                  if (!disposed) {
                    setLoadProgress({
                      done: bodyBit + done,
                      total: Math.max(bodyBit + total, 1),
                    });
                  }
                });
              }
            } catch (e) {
              if (!disposed) {
                setViewerError(
                  e instanceof Error ? e.message : "模型加载失败",
                );
              }
            } finally {
              if (!disposed) setLoadProgress(null);
            }
          }

          // Locator / X-ray 微调：19 层渐进加载，避免一次灌爆
          if (deferManyMech && !garageUnified && !disposed) {
            setViewerError(null);
            try {
              await loadGarageMech((done, total) => {
                if (!disposed) setLoadProgress({ done, total });
              });
            } catch (e) {
              if (!disposed) {
                setViewerError(
                  e instanceof Error ? e.message : "机械层加载失败",
                );
              }
            } finally {
              if (!disposed) setLoadProgress(null);
            }
          }
        } else if (glbRel) {
          const { scene: glbScene, objectUrl } = await loadGlbScene(
            glbRel,
            readGlb,
          );
          if (disposed) {
            disposeObject(glbScene);
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            return;
          }
          if (objectUrl) objectUrls.push(objectUrl);
          shadeLoadedModel(glbScene, bases);
          applyGlassMaterials(glbScene);
          applyGhostVehiclePaint(
            glbScene,
            bodyPaintRef.current,
            softTopRef.current,
            bases,
            false,
            bodyPaintPbrRef.current,
          );
          applyInteriorLeatherTint(glbScene, interiorRef.current, bases);
          applySoftTopVisibility(glbScene, hideSoftTopRef.current);

          // Center mesh in local space, then TRS on wrapper (hand-tune / persist).
          const box = new THREE.Box3().setFromObject(glbScene);
          const center = box.getCenter(new THREE.Vector3());
          glbScene.position.sub(center);

          const wrap = new THREE.Group();
          wrap.name = singleAssetId
            ? `single:${singleAssetId}`
            : "single:model";
          wrap.add(glbScene);
          storeBasePlacement(wrap);
          const sid = singleAssetId ? String(singleAssetId) : null;
          const t = sid ? previewRef.current?.[sid] ?? null : null;
          applyManualTransform(wrap, t);

          singleWrap = wrap;
          worldRoot.add(wrap);
          if (runtimeRef.current) {
            runtimeRef.current.singleWrap = wrap;
            runtimeRef.current.singleAssetId = sid;
            runtimeRef.current.bodyWrap = null;
          }

          if (sid) {
            wrap.traverse((o) => {
              o.userData.assemblyId = sid;
            });
            storeMeshBaseLocal(wrap);
            applyMeshStateToWrap(wrap, sid, meshStateRef.current, []);
            const names: string[] = [];
            const seen = new Set<string>();
            wrap.traverse((o) => {
              const m = o as THREE.Mesh;
              if (!m.isMesh) return;
              const n = meshLabel(m);
              if (seen.has(n)) return;
              seen.add(n);
              names.push(n);
            });
            names.sort();
            meshesReadyRef.current?.({ [sid]: names });
          }

          frameWorld(wrap);
          applyHighlights();
        }
      } catch (e) {
        console.error("glb load failed", e);
      }
    })();

    return () => {
      disposed = true;
      runtimeRef.current = null;
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("click", onClick);
      renderer.domElement.removeEventListener(
        "webglcontextlost",
        onContextLost,
      );
      controls.dispose();
      transformControls.dispose();
      disposeObject(worldRoot);
      scene.remove(worldRoot);
      renderer.dispose();
      if (renderer.domElement.parentNode === host) {
        host.removeChild(renderer.domElement);
      }
      for (const u of objectUrls) URL.revokeObjectURL(u);
    };
  }, [
    glbRel,
    singleAssetId,
    layersKey,
    ghostKey,
    flowsKey,
    garageUnified,
    xrayMode,
    hasContent,
    readGlb,
    showGhostBody,
    garageFlows,
  ]);

  useEffect(() => {
    runtimeRef.current?.applyHighlights();
  }, [selectedMeshName, linkedMeshNames, selectedAssemblyId, linkedAssemblyIds]);

  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt || !garageViewMode) return;
    // 切模式只改显隐；进页完整预加载（含进度）在主加载循环
    rt.applyGarageVisibility(
      garageViewMode,
      garageStructure,
      exteriorZone,
      interiorZone,
    );
  }, [garageViewMode, garageStructure, exteriorZone, interiorZone, layersKey]);

  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt) return;
    applySoftTopVisibility(rt.pickRoot, hideSoftTop);
    if (rt.bodyWrap) applySoftTopVisibility(rt.bodyWrap, hideSoftTop);
    for (const entry of rt.layerEntries.values()) {
      applySoftTopVisibility(entry.wrap, hideSoftTop);
    }
    rt.applyHighlights();
  }, [hideSoftTop]);

  // Keep seed assembly.transform in sync when parent refreshes xray layers
  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt || !layers) return;
    for (const layer of layers) {
      const entry = rt.layerEntries.get(layer.id);
      if (entry) entry.assembly = layer;
    }
    if (ghostBody) rt.ghostBodyTf = ghostBody.transform ?? null;
    rt.reapplyTransforms(previewRef.current, meshStateRef.current);
  }, [layers, ghostBody]);

  useEffect(() => {
    runtimeRef.current?.reapplyTransforms(
      previewTransforms,
      meshStateRef.current,
    );
  }, [previewTransforms]);

  useEffect(() => {
    runtimeRef.current?.reapplyTransforms(
      previewRef.current,
      previewMeshState,
    );
  }, [previewMeshState]);

  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt) return;
    if (rt.gizmoDragging()) return;
    rt.attachGizmo(
      gizmoEnabled ? gizmoAssemblyId : null,
      gizmoMeshName,
      gizmoMode,
    );
  }, [
    gizmoEnabled,
    gizmoAssemblyId,
    gizmoMeshName,
    gizmoMode,
    layersKey,
    ghostKey,
    glbRel,
    loadProgress,
  ]);

  useEffect(() => {
    if (!viewPreset) return;
    runtimeRef.current?.applyViewPreset(viewPreset.id);
  }, [viewPreset?.seq, viewPreset?.id]);

  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt) return;
    if (rt.bodyWrap) {
      applyGhostVehiclePaint(
        rt.bodyWrap,
        bodyPaintHex,
        softTopHex,
        rt.bases,
        true,
        bodyPaintPbr,
      );
      applyInteriorLeatherTint(rt.bodyWrap, interiorHex, rt.bases);
    }
    // Exterior / interior single-GLB mode: tint whole pick root (not ghost-only).
    if (!rt.layerEntries.size) {
      applyGhostVehiclePaint(
        rt.pickRoot,
        bodyPaintHex,
        softTopHex,
        rt.bases,
        false,
        bodyPaintPbr,
      );
      applyInteriorLeatherTint(rt.pickRoot, interiorHex, rt.bases);
    }
    rt.applyHighlights();
  }, [bodyPaintHex, bodyPaintPbr, softTopHex, interiorHex]);

  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt?.cabinFill) return;
    rt.cabinFill.intensity = interiorFillLight ? 0.9 : 0;
  }, [interiorFillLight]);

  useEffect(() => {
    const rt = runtimeRef.current;
    if (!rt) return;
    for (const [id, entry] of rt.layerEntries) {
      if (id === "interior") {
        applyInteriorLeatherTint(entry.wrap, interiorHex, rt.bases);
      }
    }
    if (!rt.layerEntries.size && !rt.bodyWrap) {
      applyInteriorLeatherTint(rt.pickRoot, interiorHex, rt.bases);
    }
    rt.applyHighlights();
  }, [interiorHex]);

  if (!hasContent) {
    return (
      <div className="locator-glb missing">
        无本机 GLB。引擎/底盘：`npm run export:cms-rip`；车身：见 `data/seed/flat-six/README.md`；X-ray：见 `data/seed/xray/README.md`。
      </div>
    );
  }

  return (
    <div className="locator-glb-wrap">
      <div
        className="locator-glb"
        ref={hostRef}
        aria-label={xrayMode ? "X-ray 3D viewer" : "CMS 3D viewer"}
      />
      {loadProgress ? (
        <div className="locator-glb-overlay" aria-live="polite">
          模型加载 {loadProgress.done}/{loadProgress.total}
        </div>
      ) : null}
      {viewerError ? (
        <div className="locator-glb-overlay error" role="alert">
          {viewerError}
        </div>
      ) : null}
    </div>
  );
}
