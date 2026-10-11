import * as THREE from "three";
import type { ModelOemLink } from "../api";
import review from "../../../../data/seed/vehicle/xray-material-review.json";

type Profile = { hex: string; metalness: number; roughness: number };
type Rule = { finish: string; description: string };
type Base = { preserve?: boolean };
type State = { original: THREE.Material | THREE.Material[]; originalGeometry: THREE.BufferGeometry; geometry?: THREE.BufferGeometry; materials: THREE.MeshPhysicalMaterial[]; signature: string; preserve?: boolean };
const states = new WeakMap<THREE.Mesh, State>();
const rules: Record<string, Rule> = review.parts;
const profiles: Record<string, Profile> = review.profiles;
const grains = new WeakMap<object, THREE.DataTexture>();

function carpetGrain(owner: object): THREE.DataTexture {
  const existing = grains.get(owner);
  if (existing) return existing;
  const data = new Uint8Array(64 * 64 * 4);
  for (let i = 0; i < 64 * 64; i++) {
    const n = (Math.imul(i + 71, 1103515245) >>> 16) & 255;
    data.set([112 + n % 32, 112 + (n * 7) % 32, 250, 255], i * 4);
  }
  const map = new THREE.DataTexture(data, 64, 64, THREE.RGBAFormat);
  map.name = "reviewed-xray-carpet-grain";
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.repeat.setScalar(20); map.generateMipmaps = true;
  map.minFilter = THREE.LinearMipmapLinearFilter; map.needsUpdate = true;
  grains.set(owner, map);
  return map;
}

export function applyReviewedXrayMaterials(root: THREE.Object3D, links: readonly ModelOemLink[], bases: Map<string, Base>): void {
  const byAssembly = new Map<string, Map<string, ModelOemLink>>();
  for (const link of links) {
    if (link.kind !== "mesh" || !link.assemblyId?.startsWith("pm-")) continue;
    if (!byAssembly.has(link.assemblyId)) byAssembly.set(link.assemblyId, new Map());
    byAssembly.get(link.assemblyId)!.set(link.ref, link);
  }
  root.traverse(o => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const assembly = mesh.userData.assemblyId as string | undefined;
    const scoped = assembly ? byAssembly.get(assembly) : null;
    let link: ModelOemLink | undefined;
    for (let p: THREE.Object3D | null = mesh; p && !link; p = p.parent) link = scoped?.get(p.userData.garagePartName ?? p.name);
    const sku = link?.skus.find(s => rules[s]);
    const rule = sku ? rules[sku] : null;
    let state = states.get(mesh);
    const base = bases.get(mesh.uuid);
    if (!rule) {
      if (state) {
        state.materials.forEach(m => m.dispose()); mesh.material = state.original;
        if (state.geometry) { state.geometry.dispose(); mesh.geometry = state.originalGeometry; }
        if (base) base.preserve = state.preserve;
        states.delete(mesh);
      }
      return;
    }
    const signature = JSON.stringify({ sku, rule });
    if (state?.signature === signature) return;
    if (!state) { state = { original: mesh.material, originalGeometry: mesh.geometry, materials: [], signature: "", preserve: base?.preserve }; states.set(mesh, state); }
    if (rule.finish === "carpet" && !mesh.geometry.getAttribute("uv")) {
      // Generated PETKA meshes lack UVs. Project only a cloned geometry's new UVs;
      // original positions, indices, groups and shared source geometry stay intact.
      const geometry = state.originalGeometry.clone(), pos = geometry.getAttribute("position");
      geometry.computeBoundingBox();
      const size = geometry.boundingBox!.getSize(new THREE.Vector3()).toArray();
      const axes = [0, 1, 2].sort((a, b) => size[b]! - size[a]!);
      const uv = new Float32Array(pos.count * 2);
      for (let i = 0; i < pos.count; i++) {
        uv[i * 2] = pos.getComponent(i, axes[0]!);
        uv[i * 2 + 1] = pos.getComponent(i, axes[1]!);
      }
      geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
      state.geometry = geometry; mesh.geometry = geometry;
    }
    state.materials.forEach(m => m.dispose());
    const profile = profiles[rule.finish]!;
    state.materials = (Array.isArray(state.original) ? state.original : [state.original]).map(raw => {
      const mat = new THREE.MeshPhysicalMaterial();
      THREE.MeshStandardMaterial.prototype.copy.call(mat, raw);
      mat.defines = { STANDARD: "", PHYSICAL: "" };
      mat.name = `reviewed-xray:${rule.finish}`;
      mat.userData.garageXraySurface = { sku, finish: rule.finish, ...profile };
      mat.color.set(profile.hex); mat.metalness = profile.metalness; mat.roughness = profile.roughness;
      mat.map = mat.metalnessMap = mat.roughnessMap = mat.bumpMap = null;
      mat.vertexColors = false; mat.transmission = 0; mat.clearcoat = 0;
      mat.opacity = 1; mat.transparent = false; mat.depthWrite = true;
      mat.normalMap = rule.finish === "carpet" && mesh.geometry.getAttribute("uv") ? carpetGrain(bases) : null;
      mat.normalScale.setScalar(.3); mat.needsUpdate = true;
      return mat;
    });
    mesh.material = Array.isArray(state.original) ? state.materials : state.materials[0]!;
    state.signature = signature;
    if (base) base.preserve = true;
  });
}

export function disposeReviewedXrayMaps(owner: object): void {
  grains.get(owner)?.dispose(); grains.delete(owner);
}

export function restoreReviewedXrayMesh(mesh: THREE.Mesh): void {
  const state = states.get(mesh);
  if (!state) return;
  state.materials.forEach(m => m.dispose()); mesh.material = state.original; states.delete(mesh);
  if (state.geometry) { state.geometry.dispose(); mesh.geometry = state.originalGeometry; }
}
