/**
 * Assert engine.glb has separate meshes and blok ≠ gearbox pile-up.
 * Run: node scripts/cms-rip-engine-pose.selfcheck.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const glb = path.join(root, ".local/cms-rip/engine_b61_porsche/engine.glb");
const exportPy = path.join(root, "scripts/cms_rip_export.py");

if (!fs.existsSync(glb)) {
  throw new Error("missing engine.glb — run npm run export:cms-rip:engine");
}
const src = fs.readFileSync(exportPy, "utf8");
for (const needle of [
  "bake_obj_transform",
  "collect_engine_pose_matrices",
  "parts_to_glb",
  "--engine-only",
]) {
  if (!src.includes(needle)) {
    throw new Error(`cms_rip_export.py missing ${needle}`);
  }
}

const py = `
import struct, json, sys
from pathlib import Path
p = Path(r"""${glb.replace(/\\/g, "/")}""")
data = p.read_bytes()
off = 12
chunk_len, chunk_type = struct.unpack_from("<I4s", data, off)
off += 8
js = json.loads(data[off:off+chunk_len])
meshes = js.get("meshes") or []
nodes = js.get("nodes") or []
names = [n.get("name") for n in nodes if n.get("mesh") is not None]
print("mesh_nodes", len(names))
if len(names) < 20:
    raise SystemExit(f"expected >=20 named meshes, got {len(names)}")
need = {"b61_blok_1", "b61_blok_2", "b61_gearbox", "b61_glowica_1"}
missing = need - set(names)
if missing:
    raise SystemExit(f"missing meshes: {sorted(missing)}")
# translation presence: at least some nodes should differ (baked into verts, so
# check accessor mins via trimesh if available)
try:
    import trimesh, numpy as np
    s = trimesh.load(str(p), force="scene")
    c1 = s.geometry["b61_blok_1"].centroid
    cg = s.geometry["b61_gearbox"].centroid
    ch = s.geometry["b61_glowica_1"].centroid
    d_bg = float(np.linalg.norm(c1 - cg))
    d_bh = float(np.linalg.norm(c1 - ch))
    print(f"dist_blok_gearbox={d_bg:.3f} dist_blok_head={d_bh:.3f}")
    if d_bg < 0.15:
        raise SystemExit("blok/gearbox still stacked (<0.15m)")
    if d_bh < 0.05:
        raise SystemExit("blok/head still stacked (<0.05m)")
except ImportError:
    print("trimesh missing; skipped centroid asserts")
print("cms-rip-engine-pose.selfcheck: OK")
`;

execFileSync("python", ["-c", py], { stdio: "inherit", cwd: root });
