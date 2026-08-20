"""
Export CMS2021 Porsche 991.2 engine + chassis (+ body) via UnityPy.

Local-only → .local/cms-rip/  ADR 004
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import UnityPy

ROOT = Path(r"D:\code\porsche981")
OUT = ROOT / ".local" / "cms-rip"
ENGINE_DIR = OUT / "engine_b61_porsche"
CHASSIS_DIR = OUT / "porsche991_chassis"
CAR_DIR = OUT / "porsche991_body"
DATA = Path(
    r"D:\SteamLibrary\steamapps\common\Car Mechanic Simulator 2021"
    r"\Car Mechanic Simulator 2021_Data"
)
RESOURCES = DATA / "resources.assets"
CAR_CMS = DATA / "StreamingAssets" / "Cars" / "Porsche991" / "car_porsche991.cms"
CONFIG = DATA / "StreamingAssets" / "Cars" / "Porsche991" / "config.txt"

ENGINE_MESH_RE = re.compile(r"(^b61_)|(^t_b61_)", re.I)
# ponytail: w_b61_* 属 #Exhaust_B61 装配，不进引擎层（避免与排气双份错位）

# Assembly / bay roots used by Porsche991 (from config.txt)
CHASSIS_ROOTS = [
    "#FrontRightDoubleWishbonePowered4",
    "#RearRightDeloreanShort",
    "#FrontCenterShortDiffPowered",
    "#Driveshaft",
    "#BrakePump",
    "#ABS",
    "#FuelTank",
    "#Exhaust_B61",
    "#AirIntake_B61",
    "#Cooling2",
    "#Battery",
    "#CoolantReservoir1",
    "#PowerSteeringReservoir1",
    "#WasherReservoir1",
    "#FuseBox1",
    "#ECU2",
]
EXTRA_MESH_NAMES = {
    "rim_porshe_991_carrera_4s",
    "tire_sport",
}

# Free meshes placed in car frame (not under a suspension/parts root).
# (suffix, meshName, pos_xyz_from_axle_fn returning (x,y,z), mirror_x)
def _extra_car_mesh_specs(axle: dict) -> list[tuple[str, str, list[float], bool]]:
    fy, ry = float(axle["hubY"]), float(axle["hubY"])
    fz, rz = float(axle["frontZ"]), float(axle["rearZ"])
    ft, rt = float(axle["halfTrackFront"]), float(axle["halfTrackRear"])
    return [
        # anti-roll bars（游戏悬架根未带入）
        ("STAB_F", "stabilizatorPrzod_1", [0.0, fy - 0.02, fz], False),
        ("STAB_R", "stabilizatorTyl_1", [0.0, ry - 0.02, rz], False),
        # rear stab end-links（DeloreanShort 无此件）
        ("STAB_L_RL", "lacznikStabTyl_1", [-rt * 0.85, ry, rz], True),
        ("STAB_L_RR", "lacznikStabTyl_1", [rt * 0.85, ry, rz], False),
    ]


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "mesh"


def oname(obj) -> str:
    try:
        d = obj.read()
        return getattr(d, "name", None) or getattr(d, "m_Name", None) or ""
    except Exception:
        return ""


def mesh_to_obj(mesh, name: str) -> str | None:
    try:
        exported = mesh.export()
        text = exported.decode("utf-8", errors="replace") if isinstance(exported, bytes) else str(exported)
        if "v " not in text:
            return None
        if not text.lstrip().startswith("o "):
            text = f"o {name}\n" + text
        return text
    except Exception as e:
        print(f"  export fail {name}: {e}", flush=True)
        return None


def mat4_identity():
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def mat4_mul(a, b):
    out = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            out[i][j] = (
                a[i][0] * b[0][j]
                + a[i][1] * b[1][j]
                + a[i][2] * b[2][j]
                + a[i][3] * b[3][j]
            )
    return out


def quat_to_mat3(q) -> list[list[float]]:
    x, y, z, w = float(q.x), float(q.y), float(q.z), float(q.w)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ]


def local_matrix_from_tr(tr) -> list[list[float]]:
    p = tr.m_LocalPosition
    r = tr.m_LocalRotation
    s = tr.m_LocalScale
    rot = quat_to_mat3(r)
    sx, sy, sz = float(s.x), float(s.y), float(s.z)
    M = mat4_identity()
    M[0][0], M[0][1], M[0][2] = rot[0][0] * sx, rot[0][1] * sy, rot[0][2] * sz
    M[1][0], M[1][1], M[1][2] = rot[1][0] * sx, rot[1][1] * sy, rot[1][2] * sz
    M[2][0], M[2][1], M[2][2] = rot[2][0] * sx, rot[2][1] * sy, rot[2][2] * sz
    M[0][3], M[1][3], M[2][3] = float(p.x), float(p.y), float(p.z)
    return M


def world_matrix_from_tr(by_id: dict, tr_obj) -> list[list[float]]:
    mats = []
    cur = tr_obj
    for _ in range(48):
        t = cur.read()
        mats.append(local_matrix_from_tr(t))
        father = getattr(t, "m_Father", None)
        pid = getattr(father, "path_id", 0) if father is not None else 0
        if not pid or pid not in by_id:
            break
        cur = by_id[pid]
    W = mat4_identity()
    for m in reversed(mats):
        W = mat4_mul(W, m)
    return W


def mat4_translation(tx: float, ty: float, tz: float):
    M = mat4_identity()
    M[0][3], M[1][3], M[2][3] = tx, ty, tz
    return M


def apply_mat4(M, x: float, y: float, z: float, w: float = 1.0):
    return (
        M[0][0] * x + M[0][1] * y + M[0][2] * z + M[0][3] * w,
        M[1][0] * x + M[1][1] * y + M[1][2] * z + M[1][3] * w,
        M[2][0] * x + M[2][1] * y + M[2][2] * z + M[2][3] * w,
    )


def bake_obj_transform(obj_text: str, M) -> str:
    """Bake a 4x4 into OBJ v / vn lines (mesh.export is local-space only)."""
    lines = []
    for raw in obj_text.splitlines():
        if raw.startswith("v "):
            parts = raw.split()
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            nx, ny, nz = apply_mat4(M, x, y, z, 1.0)
            lines.append(f"v {nx:.7f} {ny:.7f} {nz:.7f}")
        elif raw.startswith("vn "):
            parts = raw.split()
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            nx, ny, nz = apply_mat4(M, x, y, z, 0.0)
            # renormalize
            length = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            lines.append(f"vn {nx / length:.7f} {ny / length:.7f} {nz / length:.7f}")
        else:
            lines.append(raw)
    return "\n".join(lines) + ("\n" if obj_text.endswith("\n") else "")


def go_components(by_id: dict, go_obj) -> dict:
    g = go_obj.read()
    out = {}
    for c in g.m_Component:
        cob = by_id.get(c.component.path_id)
        if cob:
            out[cob.type.name] = cob
    return out


def mat4_invert_rigid(M):
    """Invert TRS matrix that is rotation+uniform-ish scale+translation (no shear)."""
    # R|t ; assume columns 0..2 are basis. Invert as [R^T | -R^T t] ignoring non-uniform scale.
    r00, r01, r02, tx = M[0]
    r10, r11, r12, ty = M[1]
    r20, r21, r22, tz = M[2]
    # transpose rotation part
    inv = mat4_identity()
    inv[0][0], inv[0][1], inv[0][2] = r00, r10, r20
    inv[1][0], inv[1][1], inv[1][2] = r01, r11, r21
    inv[2][0], inv[2][1], inv[2][2] = r02, r12, r22
    inv[0][3] = -(inv[0][0] * tx + inv[0][1] * ty + inv[0][2] * tz)
    inv[1][3] = -(inv[1][0] * tx + inv[1][1] * ty + inv[1][2] * tz)
    inv[2][3] = -(inv[2][0] * tx + inv[2][1] * ty + inv[2][2] * tz)
    return inv


def mat4_mirror_x():
    M = mat4_identity()
    M[0][0] = -1.0
    return M


def mat4_unity_trs(pos, euler_deg, scale) -> list:
    """Unity Quaternion.Euler(x,y,z) then non-uniform scale + translation."""
    import math

    px, py, pz = [float(x) for x in pos]
    rx, ry, rz = [math.radians(float(a)) for a in euler_deg]
    if isinstance(scale, (int, float)):
        sx = sy = sz = float(scale)
    else:
        sx, sy, sz = [float(x) for x in scale]
    # Unity Euler → quaternion (same as Quaternion.Euler)
    cx, sx_ = math.cos(rx * 0.5), math.sin(rx * 0.5)
    cy, sy_ = math.cos(ry * 0.5), math.sin(ry * 0.5)
    cz, sz_ = math.cos(rz * 0.5), math.sin(rz * 0.5)
    qx = sx_ * cy * cz + cx * sy_ * sz_
    qy = cx * sy_ * cz - sx_ * cy * sz_
    qz = cx * cy * sz_ + sx_ * sy_ * cz
    qw = cx * cy * cz - sx_ * sy_ * sz_

    class _Q:
        pass

    q = _Q()
    q.x, q.y, q.z, q.w = qx, qy, qz, qw
    rot = quat_to_mat3(q)
    M = mat4_identity()
    M[0][0], M[0][1], M[0][2] = rot[0][0] * sx, rot[0][1] * sy, rot[0][2] * sz
    M[1][0], M[1][1], M[1][2] = rot[1][0] * sx, rot[1][1] * sy, rot[1][2] * sz
    M[2][0], M[2][1], M[2][2] = rot[2][0] * sx, rot[2][1] * sy, rot[2][2] * sz
    M[0][3], M[1][3], M[2][3] = px, py, pz
    return M


def parse_cms_vec(s: str) -> list[float]:
    return [float(x.strip()) for x in String_split_csv(s)]


def String_split_csv(s: str) -> list[str]:
    return [p for p in s.split(",")]


def parse_porsche991_config(path: Path) -> dict:
    """Parse CMS car config.txt into section dicts."""
    sections: dict[str, dict[str, str]] = {}
    cur = None
    if not path.exists():
        return sections
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
            sections[cur] = {}
            continue
        if cur is None or "=" not in line:
            continue
        k, v = line.split("=", 1)
        sections[cur][k.strip()] = v.strip()
    return sections


def car_layout_from_config(sections: dict) -> dict:
    """
    Build 991.2 car-frame placements from config.txt (CMS +Z forward, +Y up).
    Returns engine mat + named assembly placements (may include mirrored copies).
    """
    eng = sections.get("6_engine") or {}
    engine_M = mat4_unity_trs(
        parse_cms_vec(eng.get("position", "0,0,0")),
        parse_cms_vec(eng.get("rotation", "0,0,0")),
        float(eng.get("scale", "1") or 1),
    )

    sus = sections.get("2_suspension") or {}
    front_z = float(sus.get("frontAxleStart", "1.167"))
    wheel_base = float(sus.get("wheelBase", "2.45"))
    rear_z = front_z - wheel_base
    ft = float(sus.get("frontTrack", "1.54")) * 0.5
    rt = float(sus.get("rearTrack", "1.54")) * 0.5
    hy = float(sus.get("height", "0.2"))
    hyr = float(sus.get("heightRear", hy))

    # list of (export_suffix, root_key, car_M, mirror_x)
    instances: list[tuple[str, str, list, bool]] = [
        ("FR", "#FrontRightDoubleWishbonePowered4", mat4_unity_trs([ft, hy, front_z], [0, 0, 0], 1), False),
        ("FL", "#FrontRightDoubleWishbonePowered4", mat4_unity_trs([-ft, hy, front_z], [0, 0, 0], 1), True),
        ("RR", "#RearRightDeloreanShort", mat4_unity_trs([rt, hyr, rear_z], [0, 0, 0], 1), False),
        ("RL", "#RearRightDeloreanShort", mat4_unity_trs([-rt, hyr, rear_z], [0, 0, 0], 1), True),
        ("FC", "#FrontCenterShortDiffPowered", mat4_unity_trs([0.0, hy, front_z], [0, 0, 0], 1), False),
    ]

    ds = sections.get("7_driveshaft") or {}
    if ds:
        instances.append(
            (
                "DS",
                "#Driveshaft",
                mat4_unity_trs(
                    parse_cms_vec(ds.get("position", "0,0,0")),
                    parse_cms_vec(ds.get("rotation", "0,0,0")),
                    float(ds.get("scale", "1") or 1),
                ),
                False,
            )
        )

    part_i = 0
    for sec_name, kv in sections.items():
        if not sec_name.startswith("parts"):
            continue
        name = kv.get("name")
        if not name:
            continue
        key = "#" + name.lstrip("#")
        instances.append(
            (
                f"P{part_i}",
                key,
                mat4_unity_trs(
                    parse_cms_vec(kv.get("position", "0,0,0")),
                    parse_cms_vec(kv.get("rotation", "0,0,0")),
                    float(kv.get("scale", "1") or 1),
                ),
                False,
            )
        )
        part_i += 1

    return {
        "engine_M": engine_M,
        "engine": {
            "position": parse_cms_vec(eng.get("position", "0,0,0")),
            "rotation": parse_cms_vec(eng.get("rotation", "0,0,0")),
            "scale": float(eng.get("scale", "1") or 1),
        },
        "axle": {
            "frontZ": front_z,
            "rearZ": rear_z,
            "halfTrackFront": ft,
            "halfTrackRear": rt,
            "hubY": hy,
            "wheelBase": wheel_base,
        },
        "instances": instances,
    }


def tr_pid_for_go(by_id: dict, go_pid: int) -> int | None:
    go_obj = by_id.get(go_pid)
    if not go_obj:
        return None
    go = go_obj.read()
    for c in go.m_Component:
        cob = by_id.get(c.component.path_id)
        if cob and cob.type.name == "Transform":
            return cob.path_id
    return None


def mesh_locals_under_root(by_id: dict, root_go_pid: int) -> dict[str, list]:
    """mesh name → matrix local to assembly root Transform."""
    root_tr = tr_pid_for_go(by_id, root_go_pid)
    if root_tr is None:
        return {}
    mesh_names = collect_meshes_under_go(by_id, root_go_pid)
    out: dict[str, list] = {}
    for obj in by_id.values():
        if obj.type.name != "GameObject":
            continue
        comps = go_components(by_id, obj)
        if "MeshFilter" not in comps or "Transform" not in comps:
            continue
        mf = comps["MeshFilter"].read()
        mid = mf.m_Mesh.path_id if mf.m_Mesh else None
        if not mid or mid not in by_id:
            continue
        mname = oname(by_id[mid])
        if mname not in mesh_names:
            continue
        # must be under root
        cur = comps["Transform"]
        under = False
        for _ in range(48):
            if cur.path_id == root_tr:
                under = True
                break
            t = cur.read()
            father = getattr(t, "m_Father", None)
            pid = getattr(father, "path_id", 0) if father is not None else 0
            if not pid or pid not in by_id:
                break
            cur = by_id[pid]
        if not under:
            continue
        Wc = world_matrix_from_tr(by_id, comps["Transform"])
        Wr = world_matrix_from_tr(by_id, by_id[root_tr])
        out[mname] = mat4_mul(mat4_invert_rigid(Wr), Wc)
    return out


def collect_engine_pose_matrices(
    by_id: dict, env, mesh_names: set[str], car_M=None
) -> dict[str, list]:
    """
    Unity Mesh assets are local-space. Scene instance *(0) seats covers correctly;
    intake/diff may sit far away. Prefer inst0 near engine core; otherwise map prefab
    relative pose through blok_1 into the instance frame; recenter to origin;
    optionally multiply by CMS config [6_engine] car-frame TRS.
    """
    prefab: dict[str, list] = {}
    inst: dict[str, list] = {}
    for obj in env.objects:
        if obj.type.name != "GameObject":
            continue
        name = oname(obj)
        comps = go_components(by_id, obj)
        if "Transform" not in comps:
            continue
        W = world_matrix_from_tr(by_id, comps["Transform"])
        if name in mesh_names:
            prefab[name] = W
        elif name.endswith("(0)") and name[:-3] in mesh_names:
            inst[name[:-3]] = W

    core = [
        "b61_blok_1",
        "b61_blok_2",
        "b61_glowica_1",
        "b61_glowica_2",
        "b61_gearbox",
        "b61_miska_olejowa",
    ]
    core_pts = [inst[n] for n in core if n in inst]
    if core_pts:
        ox = sum(m[0][3] for m in core_pts) / len(core_pts)
        oy = sum(m[1][3] for m in core_pts) / len(core_pts)
        oz = sum(m[2][3] for m in core_pts) / len(core_pts)
    else:
        ox = oy = oz = 0.0

    anchor_inst = inst.get("b61_blok_1")
    anchor_pref = prefab.get("b61_blok_1")
    pref_to_inst = None
    if anchor_inst is not None and anchor_pref is not None:
        pref_to_inst = mat4_mul(anchor_inst, mat4_invert_rigid(anchor_pref))

    chosen: dict[str, list] = {}
    for name in mesh_names:
        use = None
        src = "none"
        if name in inst:
            m = inst[name]
            dx, dy, dz = m[0][3] - ox, m[1][3] - oy, m[2][3] - oz
            if dx * dx + dy * dy + dz * dz <= 1.2 * 1.2:
                use, src = m, "inst0"
        if use is None and name in prefab and pref_to_inst is not None:
            use = mat4_mul(pref_to_inst, prefab[name])
            src = "prefab->inst"
        if use is None and name in prefab:
            use, src = prefab[name], "prefab"
        if use is None:
            use, src = mat4_identity(), "identity"
        chosen[name] = use
        print(
            f"  pose {name}: {src} t=({use[0][3]:+.3f},{use[1][3]:+.3f},{use[2][3]:+.3f})",
            flush=True,
        )

    # Recenter so assembly centroid of chosen translations ≈ origin
    if chosen:
        cx = sum(m[0][3] for m in chosen.values()) / len(chosen)
        cy = sum(m[1][3] for m in chosen.values()) / len(chosen)
        cz = sum(m[2][3] for m in chosen.values()) / len(chosen)
        shift = mat4_translation(-cx, -cy, -cz)
        for name in list(chosen):
            chosen[name] = mat4_mul(shift, chosen[name])

    # CMS config [6_engine] into shared car frame
    if car_M is not None:
        for name in list(chosen):
            chosen[name] = mat4_mul(car_M, chosen[name])
        print(
            f"  engine carFrame t=({car_M[0][3]:+.3f},{car_M[1][3]:+.3f},{car_M[2][3]:+.3f})",
            flush=True,
        )
    return chosen


def collect_body_pose_matrices(by_id: dict, env) -> dict[str, list]:
    """
    Body meshes in car_porsche991.cms are local-space; GameObject Transforms place
    them. Bake Unity world matrices as-is (scene is -Z forward). Do NOT apply Y180:
    that mirrors L/R and flips lids relative to the shell. Skip collider.
    """
    poses: dict[str, list] = {}
    for obj in env.objects:
        if obj.type.name != "GameObject":
            continue
        comps = go_components(by_id, obj)
        mf = comps.get("MeshFilter") or comps.get("SkinnedMeshRenderer")
        tr = comps.get("Transform")
        if not mf or not tr:
            continue
        mread = mf.read()
        mesh_ref = getattr(mread, "m_Mesh", None)
        mpid = getattr(mesh_ref, "path_id", 0) if mesh_ref is not None else 0
        if not mpid or mpid not in by_id:
            continue
        mname = oname(by_id[mpid])
        if not mname or mname.lower() == "collider":
            continue
        poses[mname] = world_matrix_from_tr(by_id, tr)
        print(
            f"  body pose {mname}: t=({poses[mname][0][3]:+.3f},{poses[mname][1][3]:+.3f},{poses[mname][2][3]:+.3f})",
            flush=True,
        )
    print(f"  body poses={len(poses)} (Unity native, -Z forward)", flush=True)
    return poses


def export_chassis_car_frame(
    by_id: dict,
    env,
    go_map: dict[str, list[int]],
    layout: dict,
    dest_dir: Path,
) -> list[tuple[str, str]]:
    """
    Place chassis / bay parts into CMS car frame using config.txt placements
    and Unity local poses under each assembly root. Left corners = mirror X.
    """
    import shutil

    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # cache locals per root key
    locals_by_root: dict[str, dict[str, list]] = {}
    mesh_obj_cache: dict[str, str] = {}

    def mesh_obj(name: str) -> str | None:
        if name in mesh_obj_cache:
            return mesh_obj_cache[name]
        for obj in env.objects:
            if obj.type.name != "Mesh":
                continue
            if oname(obj) != name:
                continue
            text = mesh_to_obj(obj.read(), name)
            if text:
                mesh_obj_cache[name] = text
            return text
        return None

    exported: list[tuple[str, str]] = []
    seen_export: set[str] = set()

    for suffix, root_key, car_M, mirror in layout["instances"]:
        pids = go_map.get(root_key, [])
        if not pids:
            print(f"  skip {root_key} ({suffix}): no GO", flush=True)
            continue
        if root_key not in locals_by_root:
            locals_by_root[root_key] = mesh_locals_under_root(by_id, pids[0])
            print(
                f"  root {root_key}: locals={len(locals_by_root[root_key])}",
                flush=True,
            )
        locals_map = locals_by_root[root_key]
        for mesh_name, local_M in locals_map.items():
            raw = mesh_obj(mesh_name)
            if not raw:
                continue
            M = local_M
            if mirror:
                M = mat4_mul(mat4_mirror_x(), M)
            M = mat4_mul(car_M, M)
            out_name = f"{mesh_name}__{suffix}"
            if out_name in seen_export:
                continue
            seen_export.add(out_name)
            text = bake_obj_transform(raw, M)
            (dest_dir / f"{safe_name(out_name)}.obj").write_text(text, encoding="utf-8")
            exported.append((out_name, text))

    print(f"  chassis car-frame parts={len(exported)}", flush=True)

    # Extra free meshes (stabilizer bars / end-links)
    for suffix, mesh_name, pos, mirror in _extra_car_mesh_specs(layout["axle"]):
        raw = mesh_obj(mesh_name)
        if not raw:
            print(f"  extra miss {mesh_name}", flush=True)
            continue
        car_M = mat4_unity_trs(pos, [0, 0, 0], 1)
        M = mat4_identity()
        if mirror:
            M = mat4_mul(mat4_mirror_x(), M)
        M = mat4_mul(car_M, M)
        out_name = f"{mesh_name}__{suffix}"
        if out_name in seen_export:
            continue
        seen_export.add(out_name)
        text = bake_obj_transform(raw, M)
        (dest_dir / f"{safe_name(out_name)}.obj").write_text(text, encoding="utf-8")
        exported.append((out_name, text))
        print(f"  extra {out_name} @ {pos}", flush=True)

    print(f"  chassis car-frame parts+extra={len(exported)}", flush=True)
    return exported

def write_combined_obj(parts: list[tuple[str, str]], dest: Path) -> dict:
    v_off = vt_off = vn_off = 0
    lines = [f"# combined parts={len(parts)}"]
    stats = {"parts": 0, "vertices": 0}
    for name, obj_text in parts:
        lines.append(f"\no {name}")
        local_v = local_vt = local_vn = 0
        for raw in obj_text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("o "):
                continue
            if line.startswith("v "):
                lines.append(line)
                local_v += 1
            elif line.startswith("vt "):
                lines.append(line)
                local_vt += 1
            elif line.startswith("vn "):
                lines.append(line)
                local_vn += 1
            elif line.startswith("f "):
                faces = []
                for tok in line.split()[1:]:
                    bits = tok.split("/")
                    out = []
                    for i, b in enumerate(bits):
                        if not b:
                            out.append("")
                            continue
                        idx = int(b)
                        if i == 0:
                            out.append(str(idx + v_off if idx > 0 else idx))
                        elif i == 1:
                            out.append(str(idx + vt_off if idx > 0 else idx))
                        else:
                            out.append(str(idx + vn_off if idx > 0 else idx))
                    faces.append("/".join(out))
                lines.append("f " + " ".join(faces))
            elif line.startswith("usemtl ") or line.startswith("mtllib "):
                lines.append(line)
        v_off += local_v
        vt_off += local_vt
        vn_off += local_vn
        stats["parts"] += 1
        stats["vertices"] += local_v
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats["bytes"] = dest.stat().st_size
    return stats


def try_obj_to_glb(obj_path: Path, glb_path: Path) -> bool:
    try:
        import trimesh
    except ImportError:
        return False
    try:
        scene = trimesh.load(str(obj_path))
        scene.export(str(glb_path))
        return glb_path.exists() and glb_path.stat().st_size > 0
    except Exception as e:
        print(f"trimesh glb fail: {e}", flush=True)
        return False


def pbr_for_mesh_name(name: str) -> tuple[list[float], float, float]:
    """Per-part PBR when CMS Texture2D is not exported (name heuristics)."""
    n = (name or "").lower()

    def rgba(hex6: str) -> list[float]:
        h = hex6.lstrip("#")
        return [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)] + [1.0]

    if any(k in n for k in ("filtr", "filter", "oil", "miska", "wlew")):
        return rgba("dc7820"), 0.15, 0.55
    if any(k in n for k in ("pasek", "belt", "slizg", "rubber", "rolka")):
        return rgba("1a1a1a"), 0.05, 0.9
    if any(k in n for k in ("glowica", "head", "pokrywa", "cover", "nakladka", "wkladka")):
        return rgba("c4c8cc"), 0.35, 0.4
    if any(k in n for k in ("blok", "block", "kadlub")):
        return rgba("8a9098"), 0.45, 0.45
    if any(k in n for k in ("kolektor", "intake", "manifold", "dolot", "przepust")):
        return rgba("4a4e54"), 0.25, 0.5
    if any(k in n for k in ("turbo", "w_b61", "t_b61", "exhaust", "wydech", "koncowy", "srodkowy")):
        return rgba("5a6068"), 0.55, 0.35
    if any(
        k in n
        for k in ("gear", "skrzynia", "dyfer", "diff", "wal", "walek", "lancuch", "listwa", "pompa")
    ):
        return rgba("505860"), 0.6, 0.4
    return rgba("9aa0a8"), 0.3, 0.5


def parts_to_glb(parts: list[tuple[str, str]], glb_path: Path, tmp_dir: Path) -> dict:
    """Export each part as its own glTF mesh (keep names for picking / tint)."""
    try:
        import trimesh
        from trimesh.visual.material import PBRMaterial
        from trimesh.visual.texture import TextureVisuals
    except ImportError:
        return {"ok": False, "bytes": 0, "parts": 0}
    tmp_dir.mkdir(parents=True, exist_ok=True)
    scene = trimesh.Scene()
    added = 0
    for name, obj_text in parts:
        part_path = tmp_dir / f"{safe_name(name)}.obj"
        part_path.write_text(obj_text, encoding="utf-8")
        try:
            geom = trimesh.load(str(part_path), force="mesh")
        except Exception as e:
            print(f"  glb skip {name}: {e}", flush=True)
            continue
        if geom is None or getattr(geom, "is_empty", False):
            continue
        # Avoid trimesh default shared gray + 2×2 stub PNG — assign per-part PBR.
        color, metal, rough = pbr_for_mesh_name(name)
        try:
            geom.visual = TextureVisuals(
                material=PBRMaterial(
                    baseColorFactor=color,
                    metallicFactor=metal,
                    roughnessFactor=rough,
                    doubleSided=True,
                )
            )
        except Exception as e:
            print(f"  pbr skip {name}: {e}", flush=True)
        scene.add_geometry(geom, geom_name=name, node_name=name)
        added += 1
    if added == 0:
        return {"ok": False, "bytes": 0, "parts": 0}
    try:
        scene.export(str(glb_path))
    except Exception as e:
        print(f"trimesh multi-mesh glb fail: {e}", flush=True)
        return {"ok": False, "bytes": 0, "parts": added}
    return {
        "ok": glb_path.exists() and glb_path.stat().st_size > 0,
        "bytes": glb_path.stat().st_size if glb_path.exists() else 0,
        "parts": added,
    }


def export_named_meshes(
    env, names: set[str], dest_dir: Path, poses: dict[str, list] | None = None
) -> list[tuple[str, str]]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    want = {n.lower() for n in names}
    exported: list[tuple[str, str]] = []
    seen: set[str] = set()
    for obj in env.objects:
        if obj.type.name != "Mesh":
            continue
        name = oname(obj)
        if name.lower() not in want:
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        mesh = obj.read()
        text = mesh_to_obj(mesh, name)
        if not text:
            continue
        if poses and name in poses:
            text = bake_obj_transform(text, poses[name])
        (dest_dir / f"{safe_name(name)}.obj").write_text(text, encoding="utf-8")
        exported.append((name, text))
        print(f"  mesh {name}", flush=True)
    return exported


def export_meshes_matching(
    env, pred, dest_dir: Path, poses: dict[str, list] | None = None
) -> list[tuple[str, str]]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    exported: list[tuple[str, str]] = []
    seen: set[str] = set()
    for obj in env.objects:
        if obj.type.name != "Mesh":
            continue
        name = oname(obj)
        if not pred(name):
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        mesh = obj.read()
        text = mesh_to_obj(mesh, name)
        if not text:
            continue
        if poses and name in poses:
            text = bake_obj_transform(text, poses[name])
        (dest_dir / f"{safe_name(name)}.obj").write_text(text, encoding="utf-8")
        exported.append((name, text))
        print(f"  mesh {name}", flush=True)
    return exported


def collect_meshes_under_go(by_id: dict, go_pid: int) -> set[str]:
    """Walk Transform children; collect MeshFilter / SkinnedMeshRenderer mesh names."""
    go_obj = by_id.get(go_pid)
    if not go_obj:
        return set()
    go = go_obj.read()
    tr_pid = None
    for c in go.m_Component:
        cob = by_id.get(c.component.path_id)
        if cob and cob.type.name == "Transform":
            tr_pid = cob.path_id
            break
    if tr_pid is None:
        return set()

    meshes: set[str] = set()
    stack = [tr_pid]
    seen: set[int] = set()
    while stack:
        tid = stack.pop()
        if tid in seen:
            continue
        seen.add(tid)
        tob = by_id.get(tid)
        if not tob:
            continue
        tr = tob.read()
        gob = by_id.get(tr.m_GameObject.path_id)
        if gob:
            g = gob.read()
            for c in g.m_Component:
                cob = by_id.get(c.component.path_id)
                if not cob:
                    continue
                if cob.type.name == "MeshFilter":
                    mf = cob.read()
                    mid = mf.m_Mesh.path_id if mf.m_Mesh else None
                    if mid and mid in by_id:
                        meshes.add(oname(by_id[mid]))
                elif cob.type.name == "SkinnedMeshRenderer":
                    sm = cob.read()
                    mesh_ptr = getattr(sm, "m_Mesh", None)
                    mid = mesh_ptr.path_id if mesh_ptr else None
                    if mid and mid in by_id:
                        meshes.add(oname(by_id[mid]))
        for ch in getattr(tr, "m_Children", None) or []:
            stack.append(ch.path_id)
    return {m for m in meshes if m}


def find_go_pids(by_id: dict, env, names: list[str]) -> dict[str, list[int]]:
    want = set(names) | {n.lstrip("#") for n in names} | {"#" + n.lstrip("#") for n in names}
    found: dict[str, list[int]] = {}
    for obj in env.objects:
        if obj.type.name != "GameObject":
            continue
        n = oname(obj)
        if n in want or n.lstrip("#") in {x.lstrip("#") for x in want}:
            key = "#" + n.lstrip("#")
            found.setdefault(key, []).append(obj.path_id)
    return found


def parse_config_rim(config_path: Path) -> set[str]:
    extra = set(EXTRA_MESH_NAMES)
    if not config_path.exists():
        return extra
    text = config_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.strip().startswith("rim="):
            extra.add(line.split("=", 1)[1].strip())
        if line.strip().startswith("tire="):
            extra.add(line.split("=", 1)[1].strip())
    return extra


def export_body_car_frame(report: dict) -> int:
    """Bake car_porsche991.cms body meshes into CMS car frame (+Z forward)."""
    print("=== car body cms (bake Unity world poses, -Z forward) ===", flush=True)
    if not CAR_CMS.exists():
        print(f"NO car cms at {CAR_CMS}", flush=True)
        return 1
    car_env = UnityPy.load(str(CAR_CMS))
    car_by_id = {o.path_id: o for o in car_env.objects}
    body_poses = collect_body_pose_matrices(car_by_id, car_env)
    body_parts = export_meshes_matching(
        car_env,
        lambda n: bool(n) and n.lower() != "collider",
        CAR_DIR / "parts",
        poses=body_poses,
    )
    if not body_parts:
        print("NO body meshes", flush=True)
        return 1
    b_obj = CAR_DIR / "body.obj"
    report["outputs"]["body.obj"] = write_combined_obj(body_parts, b_obj)
    b_glb = CAR_DIR / "body.glb"
    report["outputs"]["body.glb"] = parts_to_glb(
        body_parts, b_glb, CAR_DIR / "parts"
    )
    print(f"body parts={len(body_parts)} glb={report['outputs']['body.glb']}", flush=True)
    return 0


def export_body_only(report: dict) -> int:
    rc = export_body_car_frame(report)
    report_path = OUT / "export-report.json"
    if report_path.exists():
        try:
            prev = json.loads(report_path.read_text(encoding="utf-8"))
            for k in ("engine.obj", "engine.glb", "chassis.obj", "chassis.glb"):
                if k in prev.get("outputs", {}) and k not in report["outputs"]:
                    report["outputs"][k] = prev["outputs"][k]
            if "chassisWalk" in prev:
                report["chassisWalk"] = prev["chassisWalk"]
        except Exception:
            pass
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("report", report_path, flush=True)
    print(json.dumps({"body_glb": report["outputs"].get("body.glb")}, indent=2), flush=True)
    return rc


def main() -> int:
    engine_only = "--engine-only" in sys.argv
    body_only = "--body-only" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "vehicle": "Porsche991 / 2016 Carrera (991.2)",
        "engineInternalId": "engine_b61_porsche",
        "chassisRoots": CHASSIS_ROOTS,
        "outputs": {},
        "enginePoseBake": True,
        "bodyPoseBake": True,
        "carFrameFromConfig": True,
    }

    print("=== load config + resources.assets ===", flush=True)
    sections = parse_porsche991_config(CONFIG)
    layout = car_layout_from_config(sections)
    (OUT / "car-layout.json").write_text(
        json.dumps(
            {
                "engine": layout["engine"],
                "axle": layout["axle"],
                "instanceCount": len(layout["instances"]),
                "instances": [
                    {"suffix": s, "root": r, "mirror": m}
                    for s, r, _M, m in layout["instances"]
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    report["carLayout"] = {
        "engine": layout["engine"],
        "axle": layout["axle"],
        "instances": len(layout["instances"]),
    }
    print(
        f"  engine pos={layout['engine']['position']} rot={layout['engine']['rotation']} scale={layout['engine']['scale']}",
        flush=True,
    )
    print(f"  axle frontZ={layout['axle']['frontZ']} rearZ={layout['axle']['rearZ']}", flush=True)

    if body_only:
        return export_body_only(report)

    env = UnityPy.load(str(RESOURCES))
    by_id = {o.path_id: o for o in env.objects}

    # --- engine ---
    print("=== engine meshes (bake Unity poses + config car frame) ===", flush=True)
    engine_names = {
        oname(o)
        for o in env.objects
        if o.type.name == "Mesh" and ENGINE_MESH_RE.search(oname(o))
    }
    engine_poses = collect_engine_pose_matrices(
        by_id, env, engine_names, car_M=layout["engine_M"]
    )
    engine_parts = export_meshes_matching(
        env,
        lambda n: bool(ENGINE_MESH_RE.search(n)),
        ENGINE_DIR / "parts",
        poses=engine_poses,
    )
    if not engine_parts:
        print("NO engine meshes", flush=True)
        return 1
    eng_obj = ENGINE_DIR / "engine.obj"
    report["outputs"]["engine.obj"] = write_combined_obj(engine_parts, eng_obj)
    eng_glb = ENGINE_DIR / "engine.glb"
    report["outputs"]["engine.glb"] = parts_to_glb(
        engine_parts, eng_glb, ENGINE_DIR / "parts"
    )
    print(f"engine parts={len(engine_parts)} glb={report['outputs']['engine.glb']}", flush=True)

    if engine_only:
        report_path = OUT / "export-report.json"
        # merge with previous chassis/body stats if present
        if report_path.exists():
            try:
                prev = json.loads(report_path.read_text(encoding="utf-8"))
                for k in ("chassis.obj", "chassis.glb", "body.obj", "body.glb"):
                    if k in prev.get("outputs", {}) and k not in report["outputs"]:
                        report["outputs"][k] = prev["outputs"][k]
                if "chassisWalk" in prev:
                    report["chassisWalk"] = prev["chassisWalk"]
                if "chassisMeshCount" in prev:
                    report["chassisMeshCount"] = prev["chassisMeshCount"]
            except Exception:
                pass
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("report", report_path, flush=True)
        print(json.dumps({"engine_parts": len(engine_parts), "engine_glb": report["outputs"]["engine.glb"]}, indent=2), flush=True)
        return 0

    # --- chassis via assembly trees + config car frame ---
    print("=== chassis assembly walk ===", flush=True)
    go_map = find_go_pids(by_id, env, CHASSIS_ROOTS)
    mesh_names: set[str] = set()
    walk_report = {}
    for root in CHASSIS_ROOTS:
        key = "#" + root.lstrip("#")
        pids = go_map.get(key, [])
        collected: set[str] = set()
        for pid in pids:
            collected |= collect_meshes_under_go(by_id, pid)
        walk_report[key] = {"go_count": len(pids), "meshes": sorted(collected)}
        mesh_names |= collected
        print(f"  {key}: gos={len(pids)} meshes={len(collected)}", flush=True)

    rim_extra = parse_config_rim(CONFIG)
    mesh_names |= rim_extra
    mesh_names = {m for m in mesh_names if m}
    report["chassisWalk"] = walk_report
    report["chassisMeshCount"] = len(mesh_names)

    print(f"=== export chassis in car frame ({len(layout['instances'])} placements) ===", flush=True)
    chassis_parts = export_chassis_car_frame(
        by_id, env, go_map, layout, CHASSIS_DIR / "parts"
    )
    if chassis_parts:
        ch_obj = CHASSIS_DIR / "chassis.obj"
        report["outputs"]["chassis.obj"] = write_combined_obj(chassis_parts, ch_obj)
        ch_glb = CHASSIS_DIR / "chassis.glb"
        report["outputs"]["chassis.glb"] = parts_to_glb(
            chassis_parts, ch_glb, CHASSIS_DIR / "parts"
        )
        print(f"chassis parts={len(chassis_parts)} glb={report['outputs']['chassis.glb']}", flush=True)
    else:
        print("NO chassis meshes", flush=True)
        return 1

    # --- body ---
    if export_body_car_frame(report) != 0 and not engine_only:
        # body optional for App; continue if engine/chassis ok
        print("body export failed (non-fatal for App)", flush=True)

    report_path = OUT / "export-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("report", report_path, flush=True)
    print(
        json.dumps(
            {
                "engine_parts": report.get("outputs", {}).get("engine.obj", {}).get("parts"),
                "chassis_parts": report.get("outputs", {}).get("chassis.obj", {}).get("parts"),
                "engine_glb": report["outputs"].get("engine.glb"),
                "chassis_glb": report["outputs"].get("chassis.glb"),
                "body_glb": report["outputs"].get("body.glb"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
