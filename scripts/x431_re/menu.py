"""MENU.BIN walker. Multi-parent edges preserved."""
from __future__ import annotations

import struct
from collections import defaultdict

HDR = 0x23
TARGET_MODELS = {
    2550: {"key": "cayman_981", "text_id": 0xFF00001A},
    2597: {"key": "cayman_982", "text_id": 0xFF00001B},
    2644: {"key": "boxster_981", "text_id": 0xFF00000D},
    2691: {"key": "boxster_982", "text_id": 0xFF000012},
}
SYS_SELECT_OFF = 2738
MANUAL_OFF = 180
ROOT_OFF = 0


def parse_menu_node(blob: bytes, off: int) -> dict:
    if off < 0 or off + 2 > len(blob):
        raise ValueError(f"MENU truncated header at {off}")
    total = struct.unpack_from("<H", blob, off)[0]
    if total < HDR or off + total > len(blob):
        raise ValueError(f"MENU bad totalLen {total} at {off}")
    text_id = struct.unpack_from("<I", blob, off + 6)[0]
    save_byte = blob[off + 0x1E]
    up_task = blob[off + 0x1F]
    down_u16 = struct.unpack_from("<H", blob, off + 0x20)[0]
    ecu_id = blob[off + 0x20]
    family = blob[off + 0x21]
    child_n = blob[off + 0x22]
    expect = HDR + 4 * child_n
    if total != expect:
        raise ValueError(f"MENU len {total} != {expect} at {off}")
    children = []
    p = off + HDR
    for i in range(child_n):
        child = struct.unpack_from("<I", blob, p)[0]
        if child >= len(blob):
            raise ValueError(f"MENU child oob {child} at {off} i={i}")
        children.append(child)
        p += 4
    return {
        "off": off,
        "length": total,
        "text_id": text_id,
        "text_id_hex": f"{text_id:08X}",
        "save_byte": save_byte,
        "up_task_id": up_task,
        "ecu_id": ecu_id,
        "family": family,
        "down_task_u16": down_u16,
        "child_count": child_n,
        "children": children,
    }


def walk_menu(blob: bytes, root: int = 0) -> dict:
    nodes: dict[int, dict] = {}
    parents: dict[int, list[int]] = defaultdict(list)
    q = [root]
    seen = set()
    while q:
        o = q.pop(0)
        if o in seen:
            continue
        seen.add(o)
        rec = parse_menu_node(blob, o)
        nodes[o] = rec
        for c in rec["children"]:
            parents[c].append(o)
            if c not in seen:
                q.append(c)
    return {"node_count": len(nodes), "nodes": nodes, "parents_map": dict(parents)}


CAYMAN_GROUP_OFF = 520
BOXSTER_GROUP_OFF = 571
ECU_FAMILY = 13


def target_routes(walked: dict) -> dict:
    nodes = walked["nodes"]
    pm = walked["parents_map"]
    manual = nodes[MANUAL_OFF]
    sysn = nodes[SYS_SELECT_OFF]
    models = {}
    for off, meta in TARGET_MODELS.items():
        n = nodes[off]
        models[meta["key"]] = {
            "off": off,
            "text_id_hex": n["text_id_hex"],
            "want_text_id": f"{meta['text_id']:08X}",
            "text_id_ok": n["text_id"] == meta["text_id"],
            "parents": pm.get(off, []),
            "children": n["children"],
            "has_sys_select": SYS_SELECT_OFF in n["children"],
        }
    return {
        "root": ROOT_OFF,
        "manual_off": MANUAL_OFF,
        "manual_children": manual["children"],
        "sys_select_off": SYS_SELECT_OFF,
        "sys_select_parents": pm.get(SYS_SELECT_OFF, []),
        "sys_select_child_count": sysn["child_count"],
        "sys_select_children": sysn["children"],
        "models": models,
        "all_four_share_sys_select": all(m["has_sys_select"] for m in models.values())
        and set(pm.get(SYS_SELECT_OFF, [])) == set(TARGET_MODELS),
    }


def require_target_layout(walked: dict) -> dict:
    """Reject wrong MENU.BIN: four model TEXT ids, manual→group→model→sys, family 13."""
    nodes = walked["nodes"]
    pm = walked["parents_map"]
    for off in (ROOT_OFF, MANUAL_OFF, SYS_SELECT_OFF, CAYMAN_GROUP_OFF, BOXSTER_GROUP_OFF, *TARGET_MODELS):
        if off not in nodes:
            raise ValueError(f"unsupported MENU layout: missing node {off}")
    if CAYMAN_GROUP_OFF not in nodes[MANUAL_OFF]["children"] or BOXSTER_GROUP_OFF not in nodes[MANUAL_OFF]["children"]:
        raise ValueError("unsupported MENU layout: manual child edges")
    if 2550 not in nodes[CAYMAN_GROUP_OFF]["children"] or 2597 not in nodes[CAYMAN_GROUP_OFF]["children"]:
        raise ValueError("unsupported MENU layout: cayman group children")
    if 2644 not in nodes[BOXSTER_GROUP_OFF]["children"] or 2691 not in nodes[BOXSTER_GROUP_OFF]["children"]:
        raise ValueError("unsupported MENU layout: boxster group children")
    routes = target_routes(walked)
    for key, m in routes["models"].items():
        if not m["text_id_ok"]:
            raise ValueError(f"unsupported MENU layout: {key} text_id {m['text_id_hex']} != {m['want_text_id']}")
        if not m["has_sys_select"]:
            raise ValueError(f"unsupported MENU layout: {key} missing system-select child")
    if set(pm.get(SYS_SELECT_OFF, [])) != set(TARGET_MODELS):
        raise ValueError("unsupported MENU layout: system-select parents")
    bad_fam = [c for c in routes["sys_select_children"] if nodes[c]["family"] != ECU_FAMILY]
    if bad_fam:
        raise ValueError(f"unsupported MENU layout: ECU family not {ECU_FAMILY} at {bad_fam[:3]}")
    if len(routes["sys_select_children"]) != 35:
        raise ValueError("unsupported MENU layout: system-select ECU count")
    return routes
