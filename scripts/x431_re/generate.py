#!/usr/bin/env python3
"""Extract 981/982 five-pool ECU definitions from decoded DSN/9X1 + GGP + MENU.

Research-only. executionEnabled=false. No live vehicle commands.
CLI takes source files/dirs; no dated paths baked into the library.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not __package__:
    sys.path.insert(0, str(HERE))

if __package__:
    from .classify import classify_variant
    from .gag_lib import GgpLanguage, compact_text, sha256_path
    from .index_parse import (
        parse_9x1_index,
        parse_dsn_index,
        parse_l2_u16unk_id_name_off,
        parse_l2_u32count_id_name_off_extra,
    )
    from .joins import dstream_labels, enum_text, express_formula, join_status, unit_label
    from .measurement_fields import parse_measurement_fields
    from .menu import require_target_layout, target_routes, walk_menu
    from .odx_pools import (
        POOL_NAMES,
        parse_dtc_labels,
        parse_five_pointers,
        parse_flagged_ptr_index,
        parse_id_chain_table,
        pool_end,
    )
    from .request_fields import derived_read_request, parse_coding_suffix8, parse_identity_suffix7
else:
    from classify import classify_variant
    from gag_lib import GgpLanguage, compact_text, sha256_path
    from index_parse import (
        parse_9x1_index,
        parse_dsn_index,
        parse_l2_u16unk_id_name_off,
        parse_l2_u32count_id_name_off_extra,
    )
    from joins import dstream_labels, enum_text, express_formula, join_status, unit_label
    from measurement_fields import parse_measurement_fields
    from menu import require_target_layout, target_routes, walk_menu
    from odx_pools import (
        POOL_NAMES,
        parse_dtc_labels,
        parse_five_pointers,
        parse_flagged_ptr_index,
        parse_id_chain_table,
        pool_end,
    )
    from request_fields import derived_read_request, parse_coding_suffix8, parse_identity_suffix7

EU5_NAME = "SDI9_1_981_3_4L_EU5"
GW_VARIANT = "CAN_CAN_Gateway_A7_1"
MARKERS = {
    "executionEnabled": False,
    "independentVehicleVerified": False,
    "observedcapture": False,
    "independentlive": False,
    "write_status": "DATA_ONLY_DISABLED",
    "staticUnobserved": True,
}
JOIN_KEYS = (
    "labels_unresolved",
    "labels_source_empty",
    "formulas_unresolved",
    "enums_unresolved",
    "enums_source_empty",
    "enums_not_applicable",
    "units_unresolved",
    "units_source_empty",
    "units_not_applicable",
    "measurement_fields_ok",
    "measurement_fields_unsupported",
)


def empty_join() -> dict:
    return {k: 0 for k in JOIN_KEYS}


def dsn_family_limit(entries: list[dict], fam_off: int, blob_len: int) -> int:
    greater = sorted({e["off"] for e in entries if e["off"] > fam_off})
    return greater[0] if greater else blob_len


def walk_dsn_9x1(dsn: bytes) -> dict:
    idx = parse_dsn_index(dsn)
    fam = [e for e in idx["entries"] if e["name"] == "9x1"]
    if not fam:
        raise ValueError("DSN has no 9x1 family")
    fam_off = fam[0]["off"]
    limit = dsn_family_limit(idx["entries"], fam_off, len(dsn))
    l2 = parse_l2_u16unk_id_name_off(dsn, fam_off, limit)
    rows = []
    for e in l2["entries"]:
        stub = e["off"] >= limit - 8
        rows.append({**e, "pointer_ok": not stub and 0 < e["off"] < len(dsn), "stub": stub})
    return {"family_off": fam_off, "limit": limit, "entries": rows}


def all_variants(x9: bytes) -> list[dict]:
    idx = parse_9x1_index(x9)
    out = []
    for e in idx["entries"]:
        try:
            l2 = parse_l2_u32count_id_name_off_extra(x9, e["off"])
        except ValueError as err:
            out.append({"module": e["name"], "index_off": e["off"], "l2_error": str(err), "variants": []})
            continue
        recs = []
        for v in l2["entries"]:
            recs.append({"module": e["name"], "name": v["name"], "off": v["off"]})
        out.append({"module": e["name"], "index_off": e["off"], "l2_error": None, "variants": recs})
    return out


def next_variant_map(modules: list[dict], blob_len: int) -> dict[tuple[str, str], int]:
    flat = []
    for m in modules:
        for v in m["variants"]:
            flat.append((v["off"], v["module"], v["name"]))
    flat.sort()
    nxt = {}
    for i, (off, mod, name) in enumerate(flat):
        nxt[(mod, name)] = flat[i + 1][0] if i + 1 < len(flat) else blob_len
    return nxt


def resolve_pool(name: str, raw: dict, lang, src: dict) -> tuple[list[dict], dict]:
    join = empty_join()
    out = []
    if name in ("identity", "coding"):
        for rec in raw.get("records") or []:
            labels, lu, le = dstream_labels(lang, rec["ids"])
            join["labels_unresolved"] += lu
            join["labels_source_empty"] += le
            prefix = bytes.fromhex(rec["request_prefix_hex"])
            req = derived_read_request(prefix)
            if name == "coding":
                suf = parse_coding_suffix8(bytes.fromhex(rec["suffix_hex"]))
                fid = suf.get("formula_id") if suf.get("ok") else None
            else:
                suf = parse_identity_suffix7(bytes.fromhex(rec["suffix_hex"]))
                fid = suf.get("formula_id") if suf.get("ok") else None
            formula, fu = express_formula(lang, fid)
            join["formulas_unresolved"] += fu
            enums, eu, ee, ena = enum_text(lang, formula)
            join["enums_unresolved"] += eu
            join["enums_source_empty"] += ee
            join["enums_not_applicable"] += ena
            unit, uu, ue, una = unit_label(lang, formula)
            join["units_unresolved"] += uu
            join["units_source_empty"] += ue
            join["units_not_applicable"] += una
            item = {
                "at": rec["at"],
                "request_prefix_hex": rec["request_prefix_hex"],
                "readSID": req.get("readSID"),
                "companionSID": req.get("companionSID"),
                "pid": req.get("pid"),
                "chain": rec["chain"],
                "suffix_hex": rec["suffix_hex"],
                "labels": labels,
                "formula": formula,
                "enumText": enums,
                "unit": unit,
                "join_status": join_status(
                    labels_unresolved=lu, formula_unresolved=fu, enum_unresolved=eu, unit_unresolved=uu
                ),
                "source": src,
                **MARKERS,
                "writePayload": None,
            }
            if name == "coding":
                item["propertyByte"] = suf.get("propertyByte")
                item["property_status"] = suf.get("property_status", "unresolved")
                item["byteOffset"] = suf.get("byteOffset")
                item["bitOffset"] = suf.get("bitOffset")
                item["request_status"] = "disabled_candidate"
                item["derived_request_hex"] = None
                item["read_request_candidate_hex"] = req.get("request_hex") if req.get("status") == "ok" else None
            else:
                item["suffix_head"] = suf
                allow = req.get("readSID") in (0x1A, 0x22) and req.get("status") == "ok"
                item["derived_request_hex"] = req.get("request_hex") if allow else None
                item["request_status"] = "derived_1A_22" if allow else req.get("status")
            out.append(item)
        return out, join
    if name == "measurement":
        for g in raw.get("groups") or []:
            gid = g.get("gag_hex")
            fmt = int(g.get("format_flag") or 0)
            for row in g.get("rows") or []:
                ds = compact_text(lang.lookup_id("DSTREAM_CN.GAG", row["dstream_name_id"])) if lang else None
                formula, fu = express_formula(lang, row["express_id"])
                unit, uu, ue, una = unit_label(lang, formula)
                enums, eu, ee, ena = enum_text(lang, formula)
                lu = 1 if ds is None else 0
                le = 1 if ds == "" else 0
                join["labels_unresolved"] += lu
                join["labels_source_empty"] += le
                join["formulas_unresolved"] += fu
                join["units_unresolved"] += uu
                join["units_source_empty"] += ue
                join["units_not_applicable"] += una
                join["enums_unresolved"] += eu
                join["enums_source_empty"] += ee
                join["enums_not_applicable"] += ena
                mf = parse_measurement_fields(bytes.fromhex(row["raw_hex"]), fmt)
                if mf.get("ok"):
                    join["measurement_fields_ok"] += 1
                else:
                    join["measurement_fields_unsupported"] += 1
                out.append(
                    {
                        "at": row["at"],
                        "group_id_hex": gid,
                        "raw_hex": row["raw_hex"],
                        "format_flag": fmt,
                        "dstream_name_id_hex": row["dstream_name_id_hex"],
                        "name": ds,
                        "express_id_hex": row["express_id_hex"],
                        "formula": formula,
                        "enumText": enums,
                        "unit": unit,
                        "readSID": mf.get("wireSID"),
                        "rawSID": mf.get("rawSID"),
                        "wireSID": mf.get("wireSID"),
                        "pid": mf.get("PID"),
                        "byteOffset": mf.get("byteOffset"),
                        "bitOffset": mf.get("bitOffset"),
                        "nameID": mf.get("nameID"),
                        "formulaID": mf.get("formulaID"),
                        "disabledreadrequestcandidate": mf.get("disabledreadrequestcandidate"),
                        "fields_status": mf.get("status"),
                        "join_status": join_status(
                            labels_unresolved=lu, formula_unresolved=fu, enum_unresolved=eu, unit_unresolved=uu
                        ),
                        "source": src,
                        **MARKERS,
                    }
                )
        return out, join
    if name == "routine":
        for g in raw.get("groups") or []:
            for leaf in g.get("leaves") or []:
                labels = []
                unclassified = [{"kind": "raw_hex_not_gag8", "token": t} for t in (leaf.get("raw_hex_lits") or [])]
                lu = le = 0
                for idu in leaf["ids"]:
                    t = compact_text(lang.lookup_id("DSTREAM_CN.GAG", idu)) if lang else None
                    if t is None:
                        unclassified.append({"kind": "raw_8hex_no_dstream", "token": f"{idu:08X}"})
                    else:
                        labels.append({"id_hex": f"{idu:08X}", "text": t, "namespace": "DSTREAM_CN.GAG"})
                        if t == "":
                            le += 1
                join["labels_unresolved"] += lu
                join["labels_source_empty"] += le
                out.append(
                    {
                        "at": leaf["at"],
                        "group_id_hex": g.get("gag_hex"),
                        "payload_kind": g.get("payload_kind"),
                        "raw_hex": leaf["raw_hex"],
                        "ids": leaf.get("id_hex"),
                        "labels": labels,
                        "unclassified_raw": unclassified,
                        "label_namespace": "DSTREAM_CN.GAG",
                        "join_status": "dstream_if_present_else_raw",
                        "source": src,
                        **MARKERS,
                    }
                )
            for row in g.get("rows") or []:
                out.append({"at": row["at"], "raw_hex": row["raw_hex"], "note": "routine_16B_row", "source": src, **MARKERS})
        return out, join
    if name == "dtc":
        for rec in raw.get("records") or []:
            text = compact_text(lang.lookup_id("TEXT_CN.GAG", rec["text_id"])) if lang else None
            if text is None:
                join["labels_unresolved"] += 1
            elif text == "":
                join["labels_source_empty"] += 1
            out.append(
                {
                    "at": rec["at"],
                    "code": rec["code"],
                    "numeric_dtc_hex": rec["numeric_dtc_hex"],
                    "text_id_hex": rec["text_id_hex"],
                    "text": text,
                    "statusKind": rec["statusKind"],
                    "live_status_byte": None,
                    "join_status": join_status(labels_unresolved=1 if text is None else 0, formula_unresolved=0, enum_unresolved=0),
                    "source": src,
                    **MARKERS,
                }
            )
        return out, join
    return out, join


def slim_pool_fail(name: str, ptr: int, err: str) -> dict:
    return {
        "pool": name,
        "status": "unsupported",
        "pointer": ptr,
        "count": None,
        "decoded": 0,
        "empty": 0,
        "missing": 0,
        "unsupported": 1,
        "error": err,
        **MARKERS,
    }


def extract_pools(x9: bytes, variant_off: int, next_off: int, lang: GgpLanguage | None, src: dict) -> dict:
    try:
        five = parse_five_pointers(x9, variant_off)
    except ValueError as e:
        return {"pointer_error": str(e), "pools": {}, **MARKERS}
    ptrs = five["pointers"]
    pools = {}
    parsers = {
        "identity": lambda s, e: parse_id_chain_table(x9, s, e, 7, "identity"),
        "measurement": lambda s, e: parse_flagged_ptr_index(x9, s, e, "measurement"),
        "coding": lambda s, e: parse_id_chain_table(x9, s, e, 8, "coding"),
        "routine": lambda s, e: parse_flagged_ptr_index(x9, s, e, "routine"),
        "dtc": lambda s, e: parse_dtc_labels(x9, s, e, "dtc"),
    }
    for i, name in enumerate(POOL_NAMES):
        ptr = ptrs[i]
        if ptr == 0:
            pools[name] = {
                "pool": name,
                "status": "missing",
                "pointer": 0,
                "count": 0,
                "decoded": 0,
                "empty": 0,
                "missing": 1,
                "unsupported": 0,
                **MARKERS,
            }
            continue
        end = pool_end(ptrs, i, next_off)
        try:
            raw = parsers[name](ptr, end)
        except ValueError as err:
            pools[name] = slim_pool_fail(name, ptr, str(err))
            pools[name]["end"] = end
            continue
        recs, join = resolve_pool(name, raw, lang, src)
        n = raw.get("count") or raw.get("item_count") or 0
        if name in ("measurement", "routine"):
            n = sum(
                g.get("item_count") or len(g.get("rows") or []) or len(g.get("leaves") or [])
                for g in raw.get("groups") or []
            )
        struct_status = raw.get("status", "decoded")
        pools[name] = {
            "pool": name,
            "status": struct_status,
            "pointer": ptr,
            "end": raw.get("end"),
            "kind": raw.get("kind"),
            "count": n if name in ("measurement", "routine") else raw.get("count"),
            "group_count": raw.get("count") if name in ("measurement", "routine") else None,
            "decoded": n,
            "empty": 1 if struct_status == "empty" else 0,
            "missing": 0,
            "unsupported": 0,
            "join": join,
            "fully_resolved": False,
            "service_header": raw.get("service_header"),
            "trailing_pad": raw.get("trailing_pad", 0),
            "records": recs,
            **MARKERS,
        }
    return {"five": five, "pools": pools, **MARKERS}


def pool_stats(p: dict) -> dict:
    return {
        "status": p.get("status"),
        "count": p.get("count"),
        "decoded": p.get("decoded", 0),
        "empty": p.get("empty", 0),
        "missing": p.get("missing", 0),
        "unsupported": p.get("unsupported", 0),
        "error": p.get("error"),
        "pointer": p.get("pointer"),
        "join": p.get("join"),
        "fully_resolved": False,
    }


def generate(args: argparse.Namespace) -> dict:
    dsn_path = Path(args.dsn_decoded)
    x9_path = Path(args.x9_decoded)
    menu_path = Path(args.menu)
    ggp_path = Path(args.ggp) if args.ggp else None
    dsn = dsn_path.read_bytes()
    x9 = x9_path.read_bytes()
    menu = menu_path.read_bytes()
    src_hashes = {
        "dsn_decoded": {"file": dsn_path.name, "sha256": sha256_path(dsn_path), "bytes": len(dsn)},
        "x9_decoded": {"file": x9_path.name, "sha256": sha256_path(x9_path), "bytes": len(x9)},
        "menu": {"file": menu_path.name, "sha256": sha256_path(menu_path), "bytes": len(menu)},
    }
    lang = None
    if ggp_path:
        lang = GgpLanguage(ggp_path)
        src_hashes["ggp"] = {"file": ggp_path.name, "sha256": lang.ggp_sha256, "bytes": ggp_path.stat().st_size}

    dsn9 = walk_dsn_9x1(dsn)
    modules = all_variants(x9)
    nxt = next_variant_map(modules, len(x9))
    walked = walk_menu(menu)
    routes = require_target_layout(walked)
    by_id: dict[int, list[dict]] = defaultdict(list)
    for e in dsn9["entries"]:
        if not e["stub"]:
            by_id[e["id"]].append(e)
    x9_by_name = {m["module"]: m for m in modules}

    menu_ecus = []
    mapped_modules: set[str] = set()
    for off in routes["sys_select_children"]:
        n = walked["nodes"][off]
        dsn_rows = by_id.get(n["ecu_id"], [])
        mods = []
        for row in dsn_rows:
            mapped_modules.add(row["name"])
            mods.append(row["name"])
        menu_ecus.append(
            {
                "menu_off": off,
                "ecu_id": n["ecu_id"],
                "family": n["family"],
                "text_id_hex": n["text_id_hex"],
                "dsn_modules": mods,
                "parents_sys_select": True,
                "model_parents": list(routes["models"]),
            }
        )

    unmapped = sorted(set(x9_by_name) - mapped_modules)
    for e in menu_ecus:
        tid = int(e["text_id_hex"], 16)
        e["label"] = compact_text(lang.lookup_id("TEXT_CN.GAG", tid)) if lang else None

    buckets = {
        "source_total": 0,
        "target_eligible": 0,
        "excluded": 0,
        "ambiguous": 0,
        "candidate": 0,
        "confirmed": 0,
        "unmapped_module_variants": 0,
        "dropped": 0,
    }
    detailed = []
    audit = []
    ecu_summaries = []
    cat_totals = {k: {"decoded": 0, "empty": 0, "missing": 0, "unsupported": 0, **empty_join()} for k in POOL_NAMES}

    eligible_modules = mapped_modules
    for m in modules:
        for v in m["variants"]:
            buckets["source_total"] += 1
            cls = classify_variant(m["module"], v["name"])
            on_menu = m["module"] in eligible_modules
            mem = cls["membership"]
            if mem == "excluded":
                buckets["excluded"] += 1
                audit.append(
                    {
                        "profile_id": f"9x1:{m['module']}:{v['name']}",
                        "module": m["module"],
                        "name": v["name"],
                        "off": v["off"],
                        "reason": cls["reason"],
                        "tokens": cls["tokens"],
                        "on_target_menu": on_menu,
                    }
                )
                continue
            if not on_menu:
                buckets["unmapped_module_variants"] += 1
                continue
            buckets["target_eligible"] += 1
            buckets[mem] = buckets.get(mem, 0) + 1
            src = {
                "file": x9_path.name,
                "sha256": src_hashes["x9_decoded"]["sha256"],
                "offset": v["off"],
                "variant": v["name"],
                "module": m["module"],
            }
            extracted = extract_pools(x9, v["off"], nxt[(m["module"], v["name"])], lang, src)
            rec = {
                "profile_id": f"9x1:{m['module']}:{v['name']}",
                "module": m["module"],
                "name": v["name"],
                "off": v["off"],
                **cls,
                "on_target_menu": True,
                "physical_fit": "confirmed_generation_label" if cls["accepted"] else "unverified",
                "pools": {k: pool_stats(p) for k, p in (extracted["pools"].items() if extracted else [])},
                "pointer_error": None if not extracted else extracted.get("pointer_error"),
                **MARKERS,
            }
            if extracted and "five" in extracted:
                rec["odx_pointers"] = extracted["five"]["token_fields"]
            detailed.append({"summary": rec, "extracted": extracted})
            if extracted:
                for k, p in extracted["pools"].items():
                    cat_totals[k]["decoded"] += p.get("decoded") or 0
                    cat_totals[k]["empty"] += p.get("empty") or 0
                    cat_totals[k]["missing"] += p.get("missing") or 0
                    cat_totals[k]["unsupported"] += p.get("unsupported") or 0
                    jn = p.get("join") or {}
                    for jk in JOIN_KEYS:
                        cat_totals[k][jk] += jn.get(jk) or 0

    accounted = (
        buckets["excluded"]
        + buckets["ambiguous"]
        + buckets["candidate"]
        + buckets["confirmed"]
        + buckets["unmapped_module_variants"]
    )
    buckets["dropped"] = buckets["source_total"] - accounted

    for ecu in menu_ecus:
        vars_ = [d for d in detailed if d["summary"]["module"] in ecu["dsn_modules"]]
        pool_agg = {k: {"decoded": 0, "empty": 0, "missing": 0, "unsupported": 0, "variants_attempted": 0} for k in POOL_NAMES}
        for d in vars_:
            for k, st in d["summary"]["pools"].items():
                pool_agg[k]["decoded"] += st.get("decoded") or 0
                pool_agg[k]["empty"] += st.get("empty") or 0
                pool_agg[k]["missing"] += st.get("missing") or 0
                pool_agg[k]["unsupported"] += st.get("unsupported") or 0
                pool_agg[k]["variants_attempted"] += 1
        ecu_summaries.append(
            {
                **ecu,
                "variant_count": len(vars_),
                "membership": {
                    "confirmed": sum(1 for d in vars_ if d["summary"]["membership"] == "confirmed"),
                    "candidate": sum(1 for d in vars_ if d["summary"]["membership"] == "candidate"),
                    "ambiguous": sum(1 for d in vars_ if d["summary"]["membership"] == "ambiguous"),
                    "excluded": sum(1 for d in vars_ if d["summary"]["membership"] == "excluded"),
                },
                "pools": pool_agg,
                **MARKERS,
            }
        )

    eu5 = next((d for d in detailed if d["summary"]["name"] == EU5_NAME), None)
    gw = next((d for d in detailed if d["summary"]["name"] == GW_VARIANT), None)
    gw_hit = None
    if gw and gw["extracted"]:
        recs = (gw["extracted"]["pools"].get("dtc") or {}).get("records") or []
        gw_hit = next((r for r in recs if r.get("code") == "C13002"), None)

    compact = {
        "schemaVersion": 1,
        **MARKERS,
        "task": "offline_981_982_five_pool_expansion",
        "stable_id_scheme": "9x1:{module}:{variant_name}",
        "native_abi": {
            "GetOdxSysAdd": {
                "0x40a250": "identity",
                "0x40a254": "measurement",
                "0x40a258": "coding",
                "0x40a25c": "routine",
                "0x40a260": "dtc",
            },
            "rejected": "TOKEN+0x604..0x614 / dtc_id_chains as third pool",
        },
        "provenance": src_hashes,
        "counts": {
            **buckets,
            "menu_sys_ecus": len(menu_ecus),
            "menu_target_routes": 4,
            "unmapped_9x1_modules": unmapped,
            "all_four_share_sys_select": routes["all_four_share_sys_select"],
            "sys_select_parents": routes["sys_select_parents"],
        },
        "category_totals": cat_totals,
        "menu_ecus": ecu_summaries,
        "regressions": {
            "eu5": {
                "present": eu5 is not None,
                "dtc": (eu5["summary"]["pools"].get("dtc") or {}).get("count") if eu5 else None,
                "measurement": (eu5["summary"]["pools"].get("measurement") or {}).get("count") if eu5 else None,
                "coding": (eu5["summary"]["pools"].get("coding") or {}).get("count") if eu5 else None,
            },
            "gateway_a71_C13002": {
                "present": gw_hit is not None,
                "text_id_hex": (gw_hit or {}).get("text_id_hex"),
                "text": (gw_hit or {}).get("text"),
            },
        },
        "gaps": [
            "coding/parameters unobserved on vehicle; Dynamicparameters unobserved",
            "suffix byte/bit: coding within-one-byte LSB proven; property byte unresolved; no multibyte write claim",
            "identity derived_request_hex only 1A/22; coding 21/22 read candidate disabled",
            "no ready-to-replay coding write payload",
            "shared MENU ECU is selectable under four models; physical fit unverified except explicit 981 names",
            "not a complete real-vehicle compatibility claim",
            "structurally decoded != GAG-complete; see category_totals unresolved vs source_empty vs not_applicable",
            "measurement disabled read candidate only; SID31 aliases wire 22; never send 31",
        ],
        "notes": "Compact tracked summary. Full per-variant records stay in the local expansion output.",
    }

    return {
        "compact": compact,
        "detailed": detailed,
        "audit": audit,
        "routes": routes,
        "menu_ecu_count": len(menu_ecus),
        "src_hashes": src_hashes,
    }


def write_outputs(result: dict, out_local: Path, out_summary: Path) -> None:
    out_local.mkdir(parents=True, exist_ok=True)
    (out_local / "summary.json").write_text(json.dumps(result["compact"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    jsonl = out_local / "variants.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for d in result["detailed"]:
            row = {**d["summary"]}
            ext = d.get("extracted") or {}
            pools = {}
            for name, p in (ext.get("pools") or {}).items():
                pools[name] = {k: p[k] for k in p if k != "records"}
                pools[name]["records"] = p.get("records") or []
            row["pool_records"] = pools
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    audit_path = out_local / "audit-excluded.jsonl"
    with audit_path.open("w", encoding="utf-8") as f:
        for row in result.get("audit") or []:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    slim = dict(result["compact"])
    slim["menu_ecus"] = [
        {
            "menu_off": e["menu_off"],
            "label": e.get("label"),
            "ecu_id": e["ecu_id"],
            "dsn_modules": e["dsn_modules"],
            "variant_count": e["variant_count"],
            "membership": e["membership"],
            "pools": e["pools"],
        }
        for e in slim["menu_ecus"]
    ]
    out_summary.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline 981/982 five-pool extract")
    ap.add_argument("--dsn-decoded", required=True)
    ap.add_argument("--x9-decoded", required=True)
    ap.add_argument("--menu", required=True)
    ap.add_argument("--ggp", default="")
    ap.add_argument("--menu-labels", default="")
    ap.add_argument("--out-local", required=True)
    ap.add_argument("--out-summary", required=True)
    args = ap.parse_args(argv)
    try:
        result = generate(args)
    except ValueError as e:
        if str(e).startswith("unsupported MENU"):
            print(e, file=sys.stderr)
            return 2
        raise
    write_outputs(result, Path(args.out_local), Path(args.out_summary))
    c = result["compact"]["counts"]
    print(
        "ok",
        "menu",
        c["menu_sys_ecus"],
        "source",
        c["source_total"],
        "eligible",
        c["target_eligible"],
        "confirmed",
        c["confirmed"],
        "dropped",
        c["dropped"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
