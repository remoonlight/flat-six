import * as THREE from "three";
import { resolveInteriorAppearance, type InteriorAppearance } from "@porsche981/domain";
import type { ModelOemLink } from "../api";
import review from "../../../../data/seed/vehicle/interior-material-review.json";
import { applyVehicleSurfaceMaterial } from "./vehicle-surface-materials";

type Rule = { kind: string; region?: string; fixedBlack?: boolean; zone?: string };
type Finish = "leather" | "leatherette" | "alcantara" | "polymer" | "carpet" | "foam" | "carbon" | "control" | "silver" | "brushed-silver" | "metal" | "mirror" | "webbing" | "mesh" | "wheel-black" | "body-paint";
export type ReviewedPaint = { hex: string; pbr: { metalness: number; roughness: number } };
type Surface = { finish: Finish; hex: string; region: string };
type Piece = { triangles: number[]; box: THREE.Box3; vertices: number[] };
type ReviewedState = {
  originalGeometry: THREE.BufferGeometry;
  originalMaterial: THREE.Material | THREE.Material[];
  geometry: THREE.BufferGeometry;
  pieces: Piece[];
  materials: THREE.MeshPhysicalMaterial[];
  signature: string;
};
const rules: Record<string, Rule> = review.parts;
const states = new WeakMap<THREE.Mesh, ReviewedState>();
const mapsByOwner = new WeakMap<object, Record<string, THREE.DataTexture>>();

function ruleForRefs(refs: ReadonlySet<string>, links: readonly ModelOemLink[]): Rule | null {
  const modelMaterials: Record<string, Rule> = review.modelMaterials;
  for (const ref of refs) if (modelMaterials[ref]) return modelMaterials[ref]!;
  for (const link of links) {
    if (link.kind !== "mesh" || !refs.has(link.ref) || ![null, "body", "garage-primary"].includes(link.assemblyId ?? null)) continue;
    for (const sku of link.skus) {
      const rule = rules[sku.replace(/^\d{3}-/, "")];
      if (rule) return rule;
    }
  }
  return null;
}

/** Keep model/OEM/transform keys stable while correcting the browser's functional grouping. */
export function reviewedInteriorZone(ref: string, links: readonly ModelOemLink[]): string | null {
  const modelZones: Record<string, string> = review.modelZones;
  return ruleForRefs(new Set([ref]), links)?.zone ?? modelZones[ref] ?? null;
}

/** Connectivity follows original geometry; vertex welding only discovers UV/normal seams. */
function connectedPieces(geometry: THREE.BufferGeometry): Piece[] {
  const pos = geometry.getAttribute("position"), index = geometry.index;
  const count = index?.count ?? pos.count;
  const ids = Array.from({ length: count }, (_, i) => index?.getX(i) ?? i);
  const parent = Array.from({ length: pos.count }, (_, i) => i);
  function find(i: number): number {
    while (parent[i] !== i) { parent[i] = parent[parent[i]!]!; i = parent[i]!; }
    return i;
  }
  const join = (a: number, b: number) => { parent[find(a)] = find(b); };
  const shared = new Map<string, number>();
  for (const i of new Set(ids)) {
    const key = [pos.getX(i), pos.getY(i), pos.getZ(i)].map(v => Math.round(v * 1e6)).join(",");
    const other = shared.get(key);
    if (other != null) join(i, other); else shared.set(key, i);
  }
  for (let i = 0; i < ids.length; i += 3) { join(ids[i]!, ids[i + 1]!); join(ids[i]!, ids[i + 2]!); }
  const pieces = new Map<number, Piece>();
  for (let i = 0; i < ids.length; i += 3) {
    const root = find(ids[i]!);
    if (!pieces.has(root)) pieces.set(root, { triangles: [], box: new THREE.Box3(), vertices: [] });
    pieces.get(root)!.triangles.push(i);
  }
  for (const piece of pieces.values()) {
    piece.vertices = [...new Set(piece.triangles.flatMap(t => ids.slice(t, t + 3)))];
    for (const i of piece.vertices) piece.box.expandByPoint(new THREE.Vector3().fromBufferAttribute(pos, i));
  }
  return [...pieces.values()];
}

function surfaceMaps(owner: object): Record<string, THREE.DataTexture> {
  const found = mapsByOwner.get(owner);
  if (found) return found;
  const size = 64, heights: Record<string, Float32Array> = {};
  const noise = (x: number, y: number) => {
    const n = Math.imul(x + 41, 374761393) ^ Math.imul(y + 97, 668265263);
    return (Math.imul(n ^ (n >>> 13), 1274126177) >>> 0) / 4294967295;
  };
  for (const finish of ["leather", "leatherette", "alcantara", "carpet", "carbon", "brushed-silver", "webbing"]) {
    heights[finish] = Float32Array.from({ length: size * size }, (_, i) => {
      const x = i % size, y = Math.floor(i / size);
      if (finish === "carbon") return ((Math.floor(x / 4) + Math.floor(y / 4)) % 2 ? Math.sin(x * Math.PI / 4) : Math.sin(y * Math.PI / 4)) * 0.35 + 0.5;
      if (finish === "brushed-silver") return noise(0, y) * 0.25;
      if (finish === "webbing") return (Math.sin(x * Math.PI / 2) + Math.sin(y * Math.PI / 2)) * 0.2 + 0.5;
      return noise(x, y) * (finish === "leatherette" ? 0.45 : 1);
    });
  }
  const maps: Record<string, THREE.DataTexture> = {};
  for (const [finish, h] of Object.entries(heights)) {
    const data = new Uint8Array(size * size * 4);
    const at = (x: number, y: number) => h[((y + size) % size) * size + (x + size) % size]!;
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
      const n = new THREE.Vector3(at(x - 1, y) - at(x + 1, y), at(x, y - 1) - at(x, y + 1), 2).normalize();
      data.set([Math.round((n.x * 0.5 + 0.5) * 255), Math.round((n.y * 0.5 + 0.5) * 255), Math.round((n.z * 0.5 + 0.5) * 255), 255], (y * size + x) * 4);
    }
    const map = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
    map.name = `garage-interior-${finish}-grain`;
    map.wrapS = map.wrapT = THREE.RepeatWrapping;
    map.repeat.setScalar(finish === "carbon" ? 8 : finish === "carpet" ? 24 : 16);
    map.magFilter = THREE.LinearFilter; map.minFilter = THREE.LinearMipmapLinearFilter;
    map.generateMipmaps = true; map.anisotropy = 8; map.needsUpdate = true;
    maps[finish] = map;
  }
  const carbonColour = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const horizontal = (Math.floor(x / 4) + Math.floor(y / 4)) % 2 === 0;
    const strand = horizontal ? y % 4 : x % 4;
    const value = Math.round((0.55 + strand * 0.08 + noise(x, y) * 0.04) * 255);
    carbonColour.set([value, value, value, 255], (y * size + x) * 4);
  }
  const carbonMap = new THREE.DataTexture(carbonColour, size, size, THREE.RGBAFormat);
  carbonMap.name = "garage-interior-carbon-weave";
  carbonMap.colorSpace = THREE.SRGBColorSpace;
  carbonMap.wrapS = carbonMap.wrapT = THREE.RepeatWrapping;
  carbonMap.repeat.setScalar(8); carbonMap.generateMipmaps = true;
  carbonMap.magFilter = THREE.LinearFilter; carbonMap.minFilter = THREE.LinearMipmapLinearFilter;
  carbonMap.anisotropy = 8; carbonMap.needsUpdate = true;
  maps.carbonColour = carbonMap;
  mapsByOwner.set(owner, maps);
  return maps;
}

function upholstery(style: InteriorAppearance, region: string): Surface {
  const accent = ["seat-centre", "grip", "door-pull", "lid", "cowl"].includes(region);
  const lower = ["lower", "seat-outer", "door-insert", "door-pull"].includes(region);
  const contrast = style.layout === "package" ? accent : style.layout === "two-tone" && lower;
  const hex = contrast ? style.secondaryHex : style.primaryHex;
  let finish: Finish = style.upholstery;
  if (style.layout === "gts") finish = ["seat-centre", "door-insert", "door-pull", "grip", "lid", "lower", "steering-rim"].includes(region) ? "alcantara" : "leather";
  if (style.upholstery === "leatherette") {
    if (style.layout === "package" && accent) finish = "leather";
    if (style.layout === "solid" && region === "seat-centre") finish = "alcantara";
    if (region === "steering-rim" || region === "grip") finish = "leather";
  }
  // Full two-tone seats are in the lower colour, including their centres.
  return { finish, hex: style.layout === "two-tone" && region === "seat-centre" ? style.secondaryHex : hex, region };
}

function surfaceFor(rule: Rule, style: InteriorAppearance, source: string, piece: Piece, index: number, paint?: ReviewedPaint): Surface {
  if (rule.kind === "body-paint") return { finish: "body-paint", hex: paint!.hex, region: "painted-trim" };
  if (rule.kind === "wheel-black") return index === 0
    ? { finish: "wheel-black", hex: "#121315", region: "wheel" }
    : { finish: "metal", hex: "#9B9FA3", region: "wheel-fastener" };
  const lowerHex = style.layout === "two-tone" ? style.secondaryHex : style.primaryHex;
  if (rule.kind === "silver") return { finish: "silver", hex: "#AEB3B8", region: "galvano-silver" };
  if (rule.kind === "brushed-silver") return { finish: "brushed-silver", hex: "#B8BDC2", region: "brushed-silver" };
  if (rule.kind === "metal") return { finish: "metal", hex: "#9B9FA3", region: "hardware" };
  if (rule.kind === "mirror") return { finish: "mirror", hex: "#E5E7E9", region: "mirror" };
  if (rule.kind === "interior-mirror") return /Source__hq_body_284$/.test(source)
    ? { finish: "mirror", hex: "#E5E7E9", region: "mirror" }
    : { finish: "polymer", hex: "#1A1A1C", region: "mirror-housing" };
  if (rule.kind === "seat-belt") {
    if (/SUB2/.test(source)) return { finish: "metal", hex: "#B8BDC2", region: "belt-tongue" };
    if (/SUB1/.test(source)) return { finish: "polymer", hex: "#1A1A1C", region: "belt-hardware" };
    return { finish: "webbing", hex: "#19191A", region: "belt-webbing" };
  }
  if (rule.kind === "belt-latch") return /interior_082/.test(source)
    ? { finish: "control", hex: "#1A1A1C", region: "belt-release" }
    : { finish: "polymer", hex: "#1A1A1C", region: "belt-latch" };
  if (rule.kind === "mesh" || rule.kind === "windbreak" && /GRID/.test(source)) return { finish: "mesh", hex: "#19191A", region: "net" };
  if (rule.kind === "windbreak") return { finish: "polymer", hex: "#19191A", region: "windbreak-frame" };
  if (rule.kind === "shifter") {
    if (/interior_067/.test(source)) return { finish: "silver", hex: "#AEB3B8", region: "shifter-insert" };
    return upholstery(style, "grip");
  }
  if (rule.kind === "carpet") return { finish: "carpet", hex: rule.fixedBlack ? "#19191A" : style.carpetHex, region: "carpet" };
  if (rule.kind === "foam") return { finish: "foam", hex: "#171719", region: "foam" };
  if (rule.kind === "polymer" || rule.kind === "vent") return { finish: "polymer", hex: rule.fixedBlack || rule.kind === "vent" ? "#1A1A1C" : rule.region === "lower" ? lowerHex : style.primaryHex, region: "polymer" };
  if (rule.kind === "control") return { finish: "control", hex: "#1A1A1C", region: "control" };
  if (rule.kind === "visor") {
    if (/PLASTIC/.test(source)) return { finish: "polymer", hex: "#171719", region: "visor-hardware" };
    return { finish: style.upholstery, hex: ["7J0", "DM5"].includes(style.code) ? style.primaryHex : "#1A1A1C", region: "visor" };
  }
  if (rule.kind === "seat-centre") return upholstery(style, "seat-centre");
  if (rule.kind === "seat") {
    // The joined source has a separate central backrest surface, bolsters and headrest.
    const width = piece.box.max.x - piece.box.min.x;
    const centralBackrest = index === 0 && width < 0.42;
    return upholstery(style, centralBackrest ? "seat-centre" : "seat-outer");
  }
  // Disconnected passenger airbag-cover layers belong to the upper dashboard too.
  // The contrasting instrument cowl has its own explicit OEM/region rule.
  if (rule.kind === "dashboard") return upholstery(style, "upper");
  if (rule.kind === "door-panel") {
    // Original disconnected upper rail and middle panels retain their boundaries.
    if (/interior_065/.test(source)) return upholstery(style, index === 0 ? "upper" : "door-insert");
    if (/interior_054/.test(source)) return upholstery(style, index === 0 ? "upper" : index === 1 ? "door-pull" : "door-insert");
    if (/interior_064/.test(source)) return upholstery(style, "lower");
    return { finish: "polymer", hex: lowerHex, region: "door-hardware" };
  }
  if (rule.kind === "steering-wheel") {
    if (/interior_071/.test(source)) return upholstery(style, "upper"); // Airbag cover stays leather.
    // Original half-rim pieces are leather; the top arc is the carbon insert.
    const topArc = piece.box.min.y > 0.14;
    const rim = piece.box.max.y > 0.15 && piece.box.min.y < 0.05;
    if (topArc) return { finish: "carbon", hex: "#25272A", region: "carbon-rim" };
    if (rim) return upholstery(style, "steering-rim");
    return { finish: "polymer", hex: "#202124", region: "wheel-hardware" };
  }
  return upholstery(style, rule.region ?? "upper");
}

function matchingRule(mesh: THREE.Mesh, links: readonly ModelOemLink[]): Rule | null {
  const refs = new Set<string>();
  for (let n: THREE.Object3D | null = mesh; n; n = n.parent) {
    if (n.userData.garagePartName) refs.add(n.userData.garagePartName);
    refs.add(n.name);
    if (n.userData.garageCategory === "cabin" && n.userData.sourceObject)
      refs.add(`Cabin__${n.userData.garageZone}__${n.userData.sourceObject}`);
  }
  return ruleForRefs(refs, links);
}

function materialFor(raw: THREE.Material, surface: Surface, geometry: THREE.BufferGeometry, owner: object, paint?: ReviewedPaint): THREE.MeshPhysicalMaterial {
  const mat = new THREE.MeshPhysicalMaterial();
  THREE.MeshStandardMaterial.prototype.copy.call(mat, raw);
  mat.defines = { STANDARD: "", PHYSICAL: "" };
  const { finish, hex, region } = surface;
  mat.name = `reviewed-interior:${finish}:${region}`;
  mat.userData.garageInteriorSurface = { ...surface };
  if (finish === "body-paint") {
    const holder = new THREE.Mesh(geometry, mat);
    applyVehicleSurfaceMaterial(holder, "paint", owner, paint!.pbr);
    const painted = holder.material as THREE.MeshPhysicalMaterial;
    painted.color.set(hex);
    return painted;
  }
  mat.metalness = finish === "mirror" ? 1 : ["silver", "brushed-silver", "metal"].includes(finish) ? 0.75 : 0;
  mat.anisotropy = finish === "brushed-silver" && geometry.getAttribute("uv") ? 0.45 : 0;
  mat.clearcoat = ["carbon", "wheel-black"].includes(finish) ? 1 : 0;
  mat.clearcoatRoughness = finish === "wheel-black" ? 0.09 : 0.25;
  mat.roughness = { leather: 0.54, leatherette: 0.62, alcantara: 0.95, polymer: 0.58, carpet: 0.98, foam: 1, carbon: 0.32, control: 0.42, silver: 0.34, "brushed-silver": 0.38, metal: 0.3, mirror: 0.025, webbing: 0.94, mesh: 0.94, "wheel-black": 0.22 }[finish];
  mat.sheen = finish === "alcantara" ? 0.45 : finish === "carpet" ? 0.2 : finish === "leather" ? 0.08 : 0;
  mat.sheenColor.set(hex); mat.sheenRoughness = 0.95;
  mat.transmission = 0; mat.ior = 1.5;
  // Keep switch labels/graphics and their albedo; other audited surfaces use setting colours.
  if (finish === "mesh") {
    // Retain the source net's holes, UV texture and alpha instead of painting a solid panel.
    if (mat.map || mat.alphaMap) mat.alphaTest = Math.max(mat.alphaTest, 0.5);
  } else if (finish !== "control") {
    mat.color.set(hex); mat.map = null; mat.vertexColors = false;
    mat.metalnessMap = mat.roughnessMap = mat.bumpMap = null;
    mat.clearcoatMap = mat.clearcoatNormalMap = mat.clearcoatRoughnessMap = null;
    const grain = surfaceMaps(owner)[finish];
    mat.normalMap = geometry.getAttribute("uv") && grain ? grain : null;
    if (finish === "carbon" && geometry.getAttribute("uv")) {
      mat.map = surfaceMaps(owner).carbonColour;
      mat.envMapIntensity = 0.55;
    }
    mat.normalScale.setScalar(finish === "carpet" ? 0.32 : finish === "leather" ? 0.16 : 0.1);
  }
  mat.needsUpdate = true;
  return mat;
}

export function applyReviewedInteriorMaterials(root: THREE.Object3D, interior: string | null | undefined,
  links: readonly ModelOemLink[], owner: object, paint?: ReviewedPaint): void {
  const style = resolveInteriorAppearance(interior);
  root.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh || mesh.userData.sourceRole !== "primary" || !["cabin", "exterior"].includes(mesh.userData.garageCategory)) return;
    const rule = matchingRule(mesh, links);
    let state = states.get(mesh);
    if (!rule || rule.kind === "soft-top" || rule.kind === "body-paint" && !paint ||
        mesh.userData.garageCategory === "exterior" && !["body-paint", "wheel-black"].includes(rule.kind)) {
      if (state) restoreReviewedInteriorMesh(mesh);
      return;
    }
    const signature = JSON.stringify({ style, rule, paint: rule.kind === "body-paint" ? paint : undefined });
    if (state?.signature === signature) return;
    if (!state) {
      const originalGeometry = mesh.geometry, geometry = originalGeometry.clone();
      state = { originalGeometry, originalMaterial: mesh.material, geometry, pieces: connectedPieces(geometry), materials: [], signature: "" };
      states.set(mesh, state);
      mesh.geometry = geometry;
      if (!geometry.getAttribute("uv") && rule.kind === "steering-wheel") {
        const pos = geometry.getAttribute("position"), uv = new Float32Array(pos.count * 2);
        for (let i = 0; i < pos.count; i++) { uv[i * 2] = pos.getX(i) / 0.4 + 0.5; uv[i * 2 + 1] = pos.getY(i) / 0.4 + 0.5; }
        geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
      }
    }
    state.materials.forEach(m => m.dispose());
    state.materials = [];
    const raw = Array.isArray(state.originalMaterial) ? state.originalMaterial[0]! : state.originalMaterial;
    const materialIds = new Map<string, number>();
    const triangleMaterials = new Map<number, number>();
    for (const [i, piece] of state.pieces.entries()) {
      const surface = surfaceFor(rule, style, mesh.userData.sourceObject ?? mesh.name, piece, i, paint);
      const key = JSON.stringify(surface);
      if (!materialIds.has(key)) {
        materialIds.set(key, state.materials.length);
        state.materials.push(materialFor(raw, surface, state.geometry, owner, paint));
      }
      for (const t of piece.triangles) triangleMaterials.set(t, materialIds.get(key)!);
    }
    state.geometry.clearGroups();
    const count = state.geometry.index?.count ?? state.geometry.getAttribute("position").count;
    let start = 0, current = triangleMaterials.get(0) ?? 0;
    for (let t = 3; t <= count; t += 3) {
      const next = triangleMaterials.get(t);
      if (t === count || next !== current) {
        state.geometry.addGroup(start, t - start, current);
        start = t; current = next ?? 0;
      }
    }
    mesh.material = state.materials;
    state.signature = signature;
  });
}

export function restoreReviewedInteriorMesh(mesh: THREE.Mesh): void {
  const state = states.get(mesh);
  if (!state) return;
  state.materials.forEach(m => m.dispose()); state.geometry.dispose();
  mesh.geometry = state.originalGeometry; mesh.material = state.originalMaterial;
  states.delete(mesh);
}

export function disposeReviewedInteriorMaps(owner: object): void {
  const maps = mapsByOwner.get(owner);
  if (maps) Object.values(maps).forEach(map => map.dispose());
  mapsByOwner.delete(owner);
}
