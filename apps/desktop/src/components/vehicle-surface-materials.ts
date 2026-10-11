import * as THREE from "three";
import type { BodyPaintPbr } from "@porsche981/domain";

// Visual approximations of woven hood fabric and basecoat/clearcoat paint.
// Porsche documents the colour families and layered paint, not measured BRDFs.
export const SOFT_TOP_PBR = { metalness: 0, roughness: 0.92 };
type Surface = "fabric" | "paint";
type SurfaceMaps = {
  weaveNormal: THREE.DataTexture;
  weaveRoughness: THREE.DataTexture;
  flakeNormal: THREE.DataTexture;
  flakeRoughness: THREE.DataTexture;
};
const mapsByViewer = new WeakMap<object, SurfaceMaps>();

function surfaceMaps(owner: object): SurfaceMaps {
  const existing = mapsByViewer.get(owner);
  if (existing) return existing;
  const size = 128;
  const weave = new Float32Array(size * size);
  const tau = Math.PI * 2;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = x / 8, v = y / 8;
      const warp = Math.pow(0.5 + 0.5 * Math.cos(tau * u), 2);
      const weft = Math.pow(0.5 + 0.5 * Math.cos(tau * v), 2);
      weave[y * size + x] = (Math.floor(u) + Math.floor(v)) % 2
        ? warp * 0.7 + weft * 0.3 : weft * 0.7 + warp * 0.3;
    }
  }
  function texture(name: string, repeat: number,
    pixel: (x: number, y: number) => [number, number, number]) {
    const data = new Uint8Array(size * size * 4);
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const offset = (y * size + x) * 4;
        const rgb = pixel(x, y);
        data.set(rgb.map(n => Math.round(THREE.MathUtils.clamp(n, 0, 1) * 255)), offset);
        data[offset + 3] = 255;
      }
    }
    const map = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
    map.name = name;
    map.wrapS = map.wrapT = THREE.RepeatWrapping;
    map.repeat.set(repeat, repeat);
    map.magFilter = THREE.LinearFilter;
    map.minFilter = THREE.LinearMipmapLinearFilter;
    map.generateMipmaps = true;
    map.anisotropy = 8;
    map.needsUpdate = true;
    return map;
  }
  const height = (x: number, y: number) => weave[((y + size) % size) * size + (x + size) % size]!;
  const noise = (x: number, y: number) => {
    const hash = Math.imul(x + 1, 374761393) ^ Math.imul(y + 1, 668265263);
    return ((Math.imul(hash ^ (hash >>> 13), 1274126177) >>> 0) / 4294967295);
  };
  const maps: SurfaceMaps = {
    weaveNormal: texture("garage-fabric-weave-normal", 64, (x, y) => {
      const n = new THREE.Vector3(height(x - 1, y) - height(x + 1, y),
        height(x, y - 1) - height(x, y + 1), 1).normalize();
      return [n.x * 0.5 + 0.5, n.y * 0.5 + 0.5, n.z * 0.5 + 0.5];
    }),
    weaveRoughness: texture("garage-fabric-weave-roughness", 64, (x, y) => {
      const r = 0.9 + height(x, y) * 0.1;
      return [r, r, r];
    }),
    flakeNormal: texture("garage-paint-flake-normal", 96, (x, y) => {
      const n = new THREE.Vector3(noise(x, y) - 0.5, noise(y + 37, x + 19) - 0.5, 1).normalize();
      return [n.x * 0.5 + 0.5, n.y * 0.5 + 0.5, n.z * 0.5 + 0.5];
    }),
    flakeRoughness: texture("garage-paint-flake-roughness", 96, (x, y) => {
      const r = 0.75 + noise(x, y) * 0.25;
      return [r, r, r];
    }),
  };
  mapsByViewer.set(owner, maps);
  return maps;
}

export function applyVehicleSurfaceMaterial(
  mesh: THREE.Mesh,
  surface: Surface,
  owner: object,
  pbr: BodyPaintPbr = SOFT_TOP_PBR,
) {
  const source = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  const next = source.map(raw => {
    let mat = raw as THREE.MeshPhysicalMaterial;
    if (!mat.isMeshPhysicalMaterial || mat.userData.garageVehicleSurface !== surface) {
      const replacement = new THREE.MeshPhysicalMaterial();
      // MeshPhysicalMaterial.copy expects a physical source; the standard copy
      // preserves common render settings for both source types.
      THREE.MeshStandardMaterial.prototype.copy.call(replacement, raw);
      replacement.defines = { STANDARD: "", PHYSICAL: "" };
      replacement.userData.garageVehicleSurface = surface;
      mat = replacement;
      raw.dispose();
    }
    const fabric = surface === "fabric";
    const flakes = !fabric && pbr.metalness > 0.3;
    const maps = surfaceMaps(owner);
    const uv = Boolean(mesh.geometry.getAttribute("uv"));
    // Remove baked pigment/finish maps so a saved source colour cannot stain
    // the vehicle colour. Geometry, AO, opacity and mesh placement are retained.
    mat.map = mat.metalnessMap = mat.bumpMap = null;
    mat.clearcoatMap = mat.clearcoatRoughnessMap = mat.clearcoatNormalMap = null;
    mat.normalMap = uv ? (fabric ? maps.weaveNormal : flakes ? maps.flakeNormal : null) : null;
    mat.roughnessMap = uv ? (fabric ? maps.weaveRoughness : flakes ? maps.flakeRoughness : null) : null;
    mat.normalScale.setScalar(fabric ? 0.22 : 0.12);
    mat.metalness = pbr.metalness;
    mat.roughness = pbr.roughness;
    mat.clearcoat = fabric ? 0 : 1;
    mat.clearcoatRoughness = 0.09;
    mat.sheen = fabric ? 0.25 : 0;
    mat.sheenColor.set(0x6b6b6b);
    mat.sheenRoughness = 0.95;
    mat.ior = 1.5;
    mat.specularIntensity = fabric ? 0.35 : 1;
    mat.envMapIntensity = fabric ? 0.5 : 1;
    mat.transmission = 0;
    mat.needsUpdate = true;
    return mat;
  });
  mesh.material = Array.isArray(mesh.material) ? next : next[0]!;
}

export function disposeVehicleSurfaceMaps(owner: object) {
  const maps = mapsByViewer.get(owner);
  if (!maps) return;
  Object.values(maps).forEach(map => map.dispose());
  mapsByViewer.delete(owner);
}
