"""Offline diagnostics workbench. JSON stdin/stdout. Never serial, never send."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .decode import redact_vin
from .offline import DEFAULT_COVERAGE, DEFAULT_REGISTRY, DEFAULT_VARIANTS, build_plan, load_json, match_identity, summary_doc
from .response_values import decode_application_response
from .x431_values import preview_coding, formula_from_record, decode_record
from .realtime_preparation import READY_ACTIONS, handle_ready

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or REPO_ROOT)
PROTOCOL_SEED = DATA_ROOT / "data" / "seed" / "diagnostics" / "protocol-inventory.v1.json"
VALUE_SEED = DATA_ROOT / "data" / "seed" / "diagnostics" / "value-support.v1.json"
MANUAL_SEED = DATA_ROOT / "data" / "seed" / "diagnostics" / "manual-evidence.v1.json"
INDEX_NAME = "workbench-index.v1.json"
ACTIONS = frozenset({"summary", "plan", "variants", "records", "match", "decode", "preview", "coding-options", "replay"}) | READY_ACTIONS
CATEGORIES = frozenset({"identity", "measurement", "coding", "dtc", "routine"})
FORBIDDEN = frozenset(
    {
        "send",
        "path",
        "out",
        "outPath",
        "formula",
        "serialPort",
        "executable",
        "args",
        "inputFile",
        "variantsPath",
        "command",
    }
)
FLAGS = {"executionEnabled": False, "liveVerified": False, "writePayload": None}
MAX_STDIN = 256 * 1024
MAX_LIMIT = 100
MAX_OFFSET = 1_000_000
MAX_SEARCH = 80
MAX_HEX_CHARS = 4096
MAX_IDENTITY_BYTES = 16_384
MAX_PROFILE = 240


def _flags(d: dict) -> dict:
    out = dict(FLAGS)
    out.update(d)
    return out


def _err(code: str, **extra) -> dict:
    return _flags({"ok": False, "error": code, **extra})


def _ok(d: dict) -> dict:
    return _flags({"ok": True, **d})


def _optional_seed(path: Path, key: str) -> dict:
    data = load_json(path)
    if data is None:
        return {key: {"present": False, "ready": False, "path": str(path)}}
    return {key: {"present": True, "ready": True, "path": str(path), "schemaVersion": data.get("schemaVersion")}}


def _variants_path() -> Path:
    env = (os.environ.get("PORSCHE981_VARIANTS") or "").strip()
    return Path(env) if env else DEFAULT_VARIANTS


def _hex_bytes(s: str) -> bytes:
    h = s.strip().replace(" ", "").upper()
    if not h or len(h) % 2:
        raise ValueError("odd-or-empty-hex")
    if any(c not in "0123456789ABCDEF" for c in h):
        raise ValueError("non-hex")
    return bytes.fromhex(h)


def _formula_kind(rec: dict) -> str | None:
    f = rec.get("formula")
    if isinstance(f, dict):
        exp = (f.get("express") or {}).get("formula_kind")
        if isinstance(exp, str) and exp:
            return exp
        t = f.get("text") or ""
    elif isinstance(f, str):
        t = f
    else:
        return None
    return t.split(":", 1)[0] if t else None


def _display_name(rec: dict) -> str | None:
    name = rec.get("name")
    if isinstance(name, str) and name and not name.startswith("SubIndexNum="):
        return name
    labels = rec.get("labels")
    if isinstance(labels, list):
        texts = [lab.get("text") for lab in labels if isinstance(lab, dict) and lab.get("text")]
        if texts:
            return texts[-1]
    if isinstance(labels, str) and labels:
        return labels
    return name if isinstance(name, str) else None


def _label_source(rec: dict) -> str:
    if "labels" not in rec and not rec.get("name"):
        return "missing"
    labels = rec.get("labels")
    if labels in (None, [], ""):
        if rec.get("name"):
            return "present"
        return "empty"
    return "present"


def _enum_source(rec: dict) -> str:
    kind = _formula_kind(rec)
    et = rec.get("enumText")
    if kind and kind != "TEXTTABLE" and rec.get("code") is None:
        return "not_applicable"
    if "enumText" not in rec and rec.get("text") is None:
        return "missing"
    if et in ({}, None) and not rec.get("text"):
        return "empty"
    return "present"


def _request_candidate(rec: dict) -> dict | None:
    cand = rec.get("disabledreadrequestcandidate")
    if isinstance(cand, dict):
        return {
            "payloadHex": cand.get("payload_hex"),
            "status": cand.get("status"),
            "disabled": True,
            "liveApproved": False,
        }
    hx = rec.get("read_request_candidate_hex") or rec.get("derived_request_hex") or rec.get("request_prefix_hex")
    if hx:
        return {"payloadHex": hx, "status": rec.get("request_status") or "disabled_candidate", "disabled": True}
    return None


def _source_pages(rec: dict) -> dict | None:
    src = rec.get("source")
    if not isinstance(src, dict):
        return None
    return {
        "file": src.get("file"),
        "offset": src.get("offset"),
        "sha256": src.get("sha256"),
        "variant": src.get("variant"),
        "module": src.get("module"),
        "manualPages": src.get("pages") or src.get("manualPages") or [],
    }


def slim_record(rec: dict, category: str) -> dict:
    dtc = None
    if category == "dtc":
        text = rec.get("text")
        dtc = {
            "code": rec.get("code") or rec.get("numeric_dtc_hex"),
            "text": text,
            "textStatus": "empty" if text in ("", None) else "present",
            "numericHex": rec.get("numeric_dtc_hex"),
        }
    return {
        "at": rec.get("at"),
        "category": category,
        "name": rec.get("name"),
        "displayName": _display_name(rec),
        "unit": rec.get("unit"),
        "byteOffset": rec.get("byteOffset"),
        "bitOffset": rec.get("bitOffset"),
        "formulaKind": _formula_kind(rec),
        "fieldsStatus": rec.get("fields_status"),
        "joinStatus": rec.get("join_status"),
        "labelSource": _label_source(rec),
        "enumSource": _enum_source(rec),
        "requestCandidate": _request_candidate(rec),
        "source": _source_pages(rec),
        "dtc": dtc,
        "unsupportedReason": rec.get("fields_status") if rec.get("fields_status") not in (None, "ok") else None,
    }


def _module_ecus(coverage: dict) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for ecu in coverage.get("menu_ecus") or []:
        eid = ecu.get("ecu_id")
        if type(eid) is not int:
            continue
        for mod in ecu.get("dsn_modules") or []:
            if isinstance(mod, str):
                out.setdefault(mod, []).append(eid)
    return out


def _matches_generation(row: dict, generation: str) -> bool:
    g = row.get("generation")
    return g in (None, "9x1", generation)


def _index_path(variants: Path) -> Path:
    return variants.parent / INDEX_NAME


def _stat(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtimeNs": st.st_mtime_ns}


def load_or_build_index(variants: Path, coverage: dict) -> dict:
    if not variants.is_file():
        return {"present": False, "path": str(variants), "rows": []}
    want = _stat(variants)
    ip = _index_path(variants)
    if ip.is_file():
        try:
            cached = json.loads(ip.read_text(encoding="utf-8"))
            if cached.get("stat") == want and isinstance(cached.get("rows"), list):
                cached["present"] = True
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    mods = _module_ecus(coverage)
    rows = []
    with variants.open(encoding="utf-8") as fh:
        while True:
            offset = fh.tell()
            line = fh.readline()
            if not line:
                break
            if not line.strip():
                continue
            o = json.loads(line)
            module = o.get("module")
            pools = o.get("pool_records") or {}
            rows.append(
                {
                    "offset": offset,
                    "profileId": o.get("profile_id"),
                    "name": o.get("name"),
                    "module": module,
                    "generation": o.get("generation"),
                    "membership": o.get("membership"),
                    "onTargetMenu": bool(o.get("on_target_menu")),
                    "observedCapture": bool(o.get("observedcapture")),
                    "accepted": bool(o.get("accepted")),
                    "ecuIds": list(mods.get(module, [])),
                    "poolCounts": {
                        k: (v.get("count") if isinstance(v, dict) else None) for k, v in pools.items()
                    },
                }
            )
    doc = {"present": True, "path": str(variants), "stat": want, "rows": rows}
    ip.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def _load_variant(variants: Path, row: dict) -> dict:
    with variants.open(encoding="utf-8") as fh:
        fh.seek(int(row["offset"]))
        return json.loads(fh.readline())


def _find_row(index: dict, profile_id: str) -> dict | None:
    for row in index.get("rows") or []:
        if row.get("profileId") == profile_id:
            return row
    return None


def _find_record(variant: dict, category: str, at: int) -> dict | None:
    blob = (variant.get("pool_records") or {}).get(category) or {}
    for rec in blob.get("records") or []:
        if rec.get("at") == at:
            return rec
    return None


def _validate(req) -> dict | None:
    if not isinstance(req, dict):
        return _err("malformed_request")
    bad = sorted(FORBIDDEN & set(req))
    if bad:
        return _err("forbidden_field", fields=bad)
    action = req.get("action")
    if action not in ACTIONS:
        return _err("invalid_action", action=action)
    ids = req.get("parameterIds")
    if ids is not None and (not isinstance(ids, list) or len(ids) > 12
        or any(not isinstance(k, str) or len(k) != 64 or any(c not in '0123456789abcdef' for c in k) for k in ids)
        or len(set(ids)) != len(ids)):
        return _err("invalid_parameter_selection")
    gen = req.get("generation")
    if gen is not None and gen not in ("981", "982"):
        return _err("wrong_generation", generation=gen)
    ecu = req.get("ecuId")
    if ecu is not None and type(ecu) is not int:
        return _err("malformed_ecu_id")
    pid = req.get("profileId")
    if pid is not None and (not isinstance(pid, str) or not pid or len(pid) > MAX_PROFILE):
        return _err("malformed_profile_id")
    cat = req.get("category")
    if cat is not None and cat not in CATEGORIES:
        return _err("invalid_category", category=cat)
    group = req.get("groupId")
    if group is not None and (not isinstance(group, str) or (group != 'ungrouped' and (len(group) != 8 or any(c not in '0123456789ABCDEF' for c in group)))):
        return _err("invalid_group_id")
    for key, lo, hi in (("offset", 0, MAX_OFFSET), ("limit", 1, MAX_LIMIT), ("recordAt", 0, 2**31 - 1)):
        val = req.get(key)
        if val is None:
            continue
        if type(val) is not int or val < lo or val > hi:
            return _err("cap_limit", field=key)
    search = req.get("search")
    if search is not None and (not isinstance(search, str) or len(search) > MAX_SEARCH):
        return _err("cap_limit", field="search")
    hx = req.get("dataHex")
    if hx is not None and (not isinstance(hx, str) or len(hx) > MAX_HEX_CHARS):
        return _err("cap_limit", field="dataHex")
    mode = req.get("responseMode")
    if mode is not None and mode not in ("data", "pdu"):
        return _err("invalid_response_mode", responseMode=mode)
    raw = req.get("rawValue")
    if raw is not None and type(raw) is not int:
        return _err("malformed_raw_value")
    ident = req.get("identity")
    if ident is not None:
        if not isinstance(ident, dict):
            return _err("malformed_identity")
        blob = json.dumps(ident, ensure_ascii=False)
        if len(blob.encode("utf-8")) > MAX_IDENTITY_BYTES:
            return _err("cap_limit", field="identity")
    return None


_SEED_CACHE: dict[str, dict | None] = {}


def _cached_seed(path: Path) -> dict | None:
    key = str(path)
    if key not in _SEED_CACHE:
        _SEED_CACHE[key] = load_json(path)
    return _SEED_CACHE[key]


def _compact_protocol() -> dict:
    data = _cached_seed(PROTOCOL_SEED)
    if data is None:
        return {"present": False, "ready": False, "path": str(PROTOCOL_SEED)}
    groups = []
    for g in data.get("groups") or []:
        gens = {}
        for gk, block in (g.get("generations") or {}).items():
            if not isinstance(block, dict):
                continue
            addr = block.get("address") or {}
            wf = (block.get("workflow") or {}).get("addressing") or {}
            gens[gk] = {
                "status": block.get("status"),
                "addressClass": addr.get("class") or wf.get("class"),
                "txId": addr.get("txId") or wf.get("txId"),
                "rxId": addr.get("rxId") or wf.get("rxId"),
                "confirmedNameCount": len(block.get("confirmedNames") or []),
                "candidateCount": len(block.get("variantCandidates") or []),
                "gaps": list((block.get("gaps") or [])[:8]),
            }
        groups.append(
            {
                "ecuId": g.get("ecuId"),
                "label": g.get("label"),
                "streamedVariantCount": g.get("streamedVariantCount"),
                "generations": gens,
            }
        )
    counts = data.get("counts") or {}
    out = {
        "present": True,
        "ready": bool(groups) and bool(counts),
        "schemaVersion": data.get("schemaVersion"),
        "counts": {
            "menuGroups": counts.get("menuGroups"),
            "streamedVariants": counts.get("streamedVariants"),
            "coverageConfirmed": counts.get("coverageConfirmed"),
            "coverageCandidate": counts.get("coverageCandidate"),
            "membershipFromStream": counts.get("membershipFromStream"),
        },
        "agreementGate": (data.get("agreement") or {}).get("gate"),
        "scope": data.get("scope"),
        "groups": groups,
    }
    sysd = data.get("sysdata")
    if isinstance(sysd, dict):
        out["sysdata"] = _compact_sysdata(sysd)
    for key in ("systems", "sysData", "staticFacts", "sys_data"):
        if key in data:
            val = data[key]
            out[key] = val if not isinstance(val, list) else val[:35]
    return out


def _compact_sysdata(block: dict) -> dict:
    cvs = block.get("captureVsStatic") if isinstance(block.get("captureVsStatic"), dict) else {}
    bindings = []
    for e in (block.get("ecuIndex") or [])[:35]:
        if not isinstance(e, dict):
            continue
        links = []
        for ln in (e.get("links") or [])[:8]:
            if not isinstance(ln, dict):
                continue
            pair_n = ln.get("pairCount")
            unknown = bool(ln.get("selectedPairUnknown")) or (type(pair_n) is int and pair_n > 1)
            choice = ln.get("pairChoice") if isinstance(ln.get("pairChoice"), dict) else {}
            selected = choice.get("selectedPair")
            if unknown or selected is None:
                tx_hex = None
                rx_hex = None
                pair_selected = False
            else:
                tx_hex = ln.get("txHex")
                rx_hex = ln.get("rxHex")
                pair_selected = True
            links.append(
                {
                    "name": ln.get("name"),
                    "pairCount": pair_n,
                    "selectedPairUnknown": unknown or selected is None,
                    "pairSelected": pair_selected,
                    "txHex": tx_hex,
                    "rxHex": rx_hex,
                    "addressClass": "static-derivation-candidate",
                    "liveEnabled": False,
                    "observed": False,
                }
            )
        bindings.append(
            {
                "ecuId": e.get("ecuId"),
                "variantCount": e.get("variantCount"),
                "links": links,
            }
        )
    return {
        "checked": block.get("checked"),
        "present": block.get("present"),
        "systemCount": block.get("systemCount"),
        "class": cvs.get("class"),
        "staticVsObserved": {
            "kind": "static-candidate",
            "observedCapture": False,
            "dmeExact": cvs.get("dmeExact"),
            "gwExact": cvs.get("gwExact"),
            "dmeBoundName": cvs.get("dmeBoundName"),
            "gwBoundName": cvs.get("gwBoundName"),
        },
        "xmlDriveLinksClass": (block.get("xmlDriveLinks") or {}).get("class")
        if isinstance(block.get("xmlDriveLinks"), dict)
        else None,
        "ecuBindings": bindings,
        "addressLiveEnabled": False,
    }


def _compact_values() -> dict:
    data = _cached_seed(VALUE_SEED)
    if data is None:
        return {"present": False, "ready": False, "path": str(VALUE_SEED)}
    return {
        "present": True,
        "ready": True,
        "schemaVersion": data.get("schemaVersion"),
        "variants": data.get("variants"),
        "decodeCounts": data.get("decodeCounts"),
        "previewCounts": data.get("previewCounts"),
        "remainingReasonCounts": data.get("remainingReasonCounts"),
        "deltaSupportedVsBaseline": data.get("deltaSupportedVsBaseline"),
    }


def _source_is_991_target(src: dict | None, row: dict) -> bool:
    if not src:
        sid = str(row.get("sourceId") or "")
        return "991" in sid
    if src.get("target991") is True:
        return True
    sid = str(src.get("id") or row.get("sourceId") or "")
    kind = str(src.get("kind") or "")
    return sid.startswith("manual-991") or kind == "manual-991" or "991" in sid


def _manual_bundle() -> dict:
    data = _cached_seed(MANUAL_SEED)
    if data is None:
        return {"present": False, "data": None, "index": {}, "baseIndex": {}}
    idx: dict[tuple, list] = {}
    base_idx: dict[tuple, list] = {}
    sources = {s.get("id"): s for s in (data.get("sources") or []) if isinstance(s, dict)}
    for row in data.get("dtcEntries") or []:
        src = sources.get(row.get("sourceId"))
        if _source_is_991_target(src, row):
            continue
        key = (row.get("generation"), row.get("ecuId"), row.get("rawCode"))
        idx.setdefault(key, []).append(row)
        bkey = (row.get("generation"), row.get("ecuId"), row.get("baseCode"))
        if row.get("baseCode"):
            base_idx.setdefault(bkey, []).append(row)
    return {"present": True, "data": data, "index": idx, "baseIndex": base_idx, "sources": sources}


def _slim_manual_hit(row: dict, src: dict | None, *, relation: str) -> dict:
    kind = (src or {}).get("kind") or ""
    loc = str((src or {}).get("locator") or "")
    cayman = "cayman" in kind.lower() or "cayman" in loc.lower()
    needs = []
    for n in (row.get("needs") or [])[:6]:
        if not isinstance(n, dict):
            continue
        needs.append(
            {
                "kind": n.get("kind"),
                "sourceLabel": n.get("sourceLabel"),
                "itemCount": n.get("itemCount"),
                "itemsPreview": list(n.get("itemsPreview") or [])[:8],
                "uncertainGlyph": bool(n.get("uncertainGlyph")),
                "confidence": n.get("confidence"),
            }
        )
    return {
        "relation": relation,
        "generation": row.get("generation"),
        "ecuId": row.get("ecuId"),
        "rawCode": row.get("rawCode"),
        "baseCode": row.get("baseCode"),
        "sourceId": row.get("sourceId"),
        "docName": (src or {}).get("locator"),
        "sourceHash": (src or {}).get("sha256"),
        "pageCount": (src or {}).get("pageCount"),
        "pages": row.get("pages") or [],
        "heading": row.get("heading"),
        "bodyEvidenceStatus": row.get("bodyEvidenceStatus"),
        "bodyVsToc": row.get("bodyEvidenceStatus"),
        "bodyModel": row.get("bodyModel"),
        "bodyApplicability": "cayman-coupe" if cayman else (row.get("bodyModel") or "unspecified"),
        "caymanBody": cayman,
        "target991": False,
        "needs": needs,
        "procedureVerified": False,
        "fittedClaim": False,
        "uncertainGlyph": row.get("uncertainGlyph"),
    }


def join_manual_dtc(generation: str | None, ecu_id: int | None, rec: dict) -> dict:
    empty = {"exactHits": [], "relatedBaseCodeHits": []}
    bundle = _manual_bundle()
    if not bundle.get("present") or generation not in ("981", "982") or type(ecu_id) is not int:
        return empty
    sources = bundle.get("sources") or {}
    code = rec.get("code") or rec.get("numeric_dtc_hex")
    if not isinstance(code, str) or not code:
        return empty
    exact_rows = (bundle.get("index") or {}).get((generation, ecu_id, code)) or []
    exact = [
        _slim_manual_hit(h, sources.get(h.get("sourceId")), relation="exact")
        for h in exact_rows[:8]
    ]
    base = (exact_rows[0].get("baseCode") if exact_rows else None) or rec.get("baseCode") or code
    if isinstance(code, str) and len(code) == 7 and code.endswith("00") and not exact_rows:
        base = code[:-2]
    related = []
    seen = set()
    for h in (bundle.get("baseIndex") or {}).get((generation, ecu_id, base)) or []:
        raw = h.get("rawCode")
        key = (raw, h.get("sourceId"), tuple(h.get("pages") or []))
        if raw == code or key in seen:
            continue
        seen.add(key)
        related.append(_slim_manual_hit(h, sources.get(h.get("sourceId")), relation="base_code_only"))
        if len(related) >= 12:
            break
    return {"exactHits": exact, "relatedBaseCodeHits": related}


def system_evidence(generation: str, ecu_id: int | None) -> dict:
    out: dict = {"registry": None, "manual": None, "protocol": None}
    if generation not in ("981", "982"):
        return out
    reg = load_json(DEFAULT_REGISTRY) or {}
    for g in (reg.get("groups") or []):
        if type(ecu_id) is int and g.get("ecuId") != ecu_id:
            continue
        if type(ecu_id) is not int:
            break
        block = ((g.get("generations") or {}).get(generation)) or {}
        out["registry"] = {
            "ecuId": g.get("ecuId"),
            "label": g.get("label"),
            "wiringStatus": block.get("wiringStatus"),
            "manualStatus": block.get("manualStatus"),
            "pages": ((block.get("factoryOps") or {}).get("read") or {}).get("pages"),
            "locator": ((block.get("factoryOps") or {}).get("read") or {}).get("locator"),
            "wm": ((block.get("factoryOps") or {}).get("read") or {}).get("wm"),
            "evidence": [
                {
                    "kind": e.get("kind"),
                    "locator": e.get("locator"),
                    "page": e.get("page1based"),
                    "tocTitle": e.get("tocTitle"),
                }
                for e in (block.get("evidence") or [])[:8]
            ],
            "gaps": (block.get("gaps") or [])[:8],
        }
        break
    manual = _manual_bundle()
    if manual.get("present") and type(ecu_id) is int:
        sources = (manual["data"] or {}).get("sources") or []
        for g in (manual["data"] or {}).get("groups") or []:
            if g.get("ecuId") != ecu_id:
                continue
            ev = ((g.get("generations") or {}).get(generation) or {}).get("evidence") or []
            out["manual"] = {
                "ecuId": ecu_id,
                "label": g.get("label"),
                "headings": [
                    {
                        "title": e.get("title"),
                        "wmCodes": e.get("wmCodes"),
                        "page": e.get("page1based"),
                        "sourceId": e.get("sourceId"),
                        "docName": next((s.get("locator") for s in sources if s.get("id") == e.get("sourceId")), None),
                        "evidenceClass": e.get("evidenceClass"),
                    }
                    for e in ev[:8]
                ],
            }
            break
    proto = _compact_protocol()
    if proto.get("present") and type(ecu_id) is int:
        out["protocol"] = next((g for g in proto.get("groups") or [] if g.get("ecuId") == ecu_id), None)
    return out


def _profile_guard(req: dict, row: dict) -> dict | None:
    gen = req.get("generation")
    if gen not in ("981", "982"):
        return _err("wrong_generation", generation=gen)
    vg = row.get("generation")
    if vg in ("981", "982") and vg != gen:
        return _err("generation_profile_mismatch", profileGeneration=vg, generation=gen)
    ecu = req.get("ecuId")
    if type(ecu) is int and ecu not in (row.get("ecuIds") or []):
        return _err("ecu_profile_mismatch", ecuId=ecu, ecuIds=row.get("ecuIds"))
    return None


def _search_hit(rec: dict, category: str, q: str) -> bool:
    if not q:
        return True
    n = q.casefold()
    slim = slim_record(rec, category)
    parts = [
        str(slim.get("displayName") or ""),
        str(slim.get("name") or ""),
        str(slim.get("at") or ""),
        str((slim.get("dtc") or {}).get("code") or ""),
        str((slim.get("dtc") or {}).get("text") or ""),
        str((slim.get("requestCandidate") or {}).get("payloadHex") or ""),
    ]
    return any(n in p.casefold() for p in parts if p)


def handle(req: dict) -> dict:
    err = _validate(req)
    if err:
        return err
    action = req["action"]
    if action in READY_ACTIONS:
        return handle_ready(req)
    variants = _variants_path()
    if action == "summary":
        try:
            base = summary_doc()
        except FileNotFoundError:
            return _err("coverage-absent")
        cov = load_json(DEFAULT_COVERAGE) or {}
        menu = [{"ecuId": e.get("ecu_id"), "label": e.get("label")} for e in (cov.get("menu_ecus") or [])]
        proto = _compact_protocol()
        vals = _compact_values()
        manual = _manual_bundle()
        extra = {
            "menuEcus": menu,
            "variants": {
                "present": variants.is_file(),
                "path": str(variants),
                "streamed": proto.get("counts", {}).get("streamedVariants") if variants.is_file() else 0,
            },
            "coveragePresent": DEFAULT_COVERAGE.is_file(),
            "registryPresent": DEFAULT_REGISTRY.is_file(),
            "protocolInventory": proto,
            "valueSupport": vals,
            "manualEvidence": {
                "present": bool(manual.get("present")),
                "ready": bool(manual.get("present")),
                "schemaVersion": ((manual.get("data") or {}).get("schemaVersion") if manual.get("present") else None),
                "dtcEntryCount": len((manual.get("data") or {}).get("dtcEntries") or []) if manual.get("present") else 0,
                "sourceCount": len((manual.get("data") or {}).get("sources") or []) if manual.get("present") else 0,
                "sources": [
                    {
                        "id": s.get("id"),
                        "locator": s.get("locator"),
                        "pageCount": s.get("pageCount"),
                        "sha256": s.get("sha256"),
                        "status": s.get("status"),
                    }
                    for s in ((manual.get("data") or {}).get("sources") or [])[:12]
                ]
                if manual.get("present")
                else [],
            },
        }
        extra["protocolNotReady"] = not proto.get("ready")
        extra["valueSupportNotReady"] = not vals.get("ready")
        extra["manualNotReady"] = not extra["manualEvidence"]["present"]
        return _ok({**base, **extra, "ok": True})
    if action == "plan":
        gen = req.get("generation")
        if gen not in ("981", "982"):
            return _err("wrong_generation", generation=gen)
        ident = req.get("identity")
        try:
            doc = build_plan(gen, identity=ident, variant_id=req.get("profileId"), variants_path=variants)
        except ValueError as e:
            return _err("plan_failed", detail=str(e))
        ecu = req.get("ecuId")
        doc = dict(doc)
        doc["sourceEvidence"] = {
            "protocol": _compact_protocol().get("counts"),
            "valueSupport": {k: (_compact_values().get(k)) for k in ("decodeCounts", "previewCounts", "remainingReasonCounts")},
            "system": system_evidence(gen, ecu if type(ecu) is int else None),
        }
        doc["carChecklist"] = {
            "executionEnabled": False,
            "vehicleActionsDisabled": True,
            "clearDisabled": True,
            "writePayload": None,
            "factoryProcedure": (doc.get("checklist") or {}).get("factoryProcedure"),
            "identityQualification": doc.get("identityQualification"),
        }
        return _ok(doc)
    if action == "match":
        ident = req.get("identity")
        if not isinstance(ident, dict):
            return _err("malformed_identity")
        fp = dict(ident)
        if "generation" not in fp and req.get("generation"):
            fp["generation"] = req["generation"]
        if "ecuId" not in fp and type(req.get("ecuId")) is int:
            fp["ecuId"] = req["ecuId"]
        if "identity" not in fp and all(k not in fp for k in ("dsn",)):
            return _err("malformed_identity")
        if "identity" not in fp:
            inner = {k: v for k, v in fp.items() if k not in ("generation", "ecuId")}
            fp = {"generation": fp.get("generation"), "ecuId": fp.get("ecuId"), "identity": inner}
        doc = match_identity(fp)
        return _ok(doc)
    if action == "replay":
        from .bench import run_bench

        result = run_bench()
        cases = []
        for c in result.get("cases") or []:
            kind = c.get("evidenceKind")
            cases.append(
                {
                    "id": c.get("id"),
                    "ok": c.get("ok"),
                    "kind": kind,
                    "evidenceKind": kind,
                    "skipped": bool((c.get("detail") or {}).get("skipped")),
                    "detail": c.get("detail"),
                }
            )
        summary = result.get("summary") or {}
        return _ok(
            {
                "summary": {
                    **summary,
                    "realCount": sum(1 for c in cases if c["evidenceKind"] == "capture_replay"),
                    "syntheticCount": sum(1 for c in cases if c["evidenceKind"] == "synthetic"),
                    "skippedCount": sum(1 for c in cases if c["skipped"]),
                    "vinRedacted": True,
                },
                "cases": cases,
                "independentLiveVerified": False,
            }
        )
    try:
        coverage = load_json(DEFAULT_COVERAGE) or {}
    except OSError:
        coverage = {}
    index = load_or_build_index(variants, coverage)
    if action == "variants":
        if not index.get("present"):
            return _ok({"present": False, "total": 0, "offset": 0, "items": [], "reason": "variants-absent"})
        gen = req.get("generation")
        if gen not in ("981", "982"):
            return _err("wrong_generation", generation=gen)
        ecu = req.get("ecuId")
        q = (req.get("search") or "").strip()
        items = []
        for row in index["rows"]:
            if not _matches_generation(row, gen):
                continue
            if type(ecu) is int and ecu not in (row.get("ecuIds") or []):
                continue
            hay = " ".join(str(row.get(k) or "") for k in ("profileId", "name", "module", "membership"))
            if q and q.casefold() not in hay.casefold():
                continue
            items.append(
                {
                    "profileId": row["profileId"],
                    "name": row["name"],
                    "module": row["module"],
                    "generation": row.get("generation"),
                    "membership": row.get("membership"),
                    "observedCapture": row.get("observedCapture"),
                    "onTargetMenu": row.get("onTargetMenu"),
                    "ecuIds": row.get("ecuIds") or [],
                    "poolCounts": row.get("poolCounts") or {},
                    "generationShared": row.get("generation") in (None, "9x1"),
                }
            )
        off = int(req.get("offset") or 0)
        lim = int(req.get("limit") or 40)
        return _ok(
            {
                "present": True,
                "total": len(items),
                "offset": off,
                "items": items[off : off + lim],
            }
        )
    pid = req.get("profileId")
    if not pid:
        return _err("malformed_profile_id")
    row = _find_row(index, pid) if index.get("present") else None
    if row is None:
        return _err("unknown_profile", profileId=pid)
    guarded = _profile_guard(req, row)
    if guarded:
        return guarded
    variant = _load_variant(variants, row)
    if action == "records":
        cat = req.get("category")
        if cat not in CATEGORIES:
            return _err("invalid_category", category=cat)
        recs = ((variant.get("pool_records") or {}).get(cat) or {}).get("records") or []
        at = req.get("recordAt")
        q = (req.get("search") or "").strip()
        slim = []
        ecu = req.get("ecuId")
        gen = req.get("generation")
        for rec in recs:
            if type(at) is int and rec.get("at") != at:
                continue
            if not _search_hit(rec, cat, q):
                continue
            item = slim_record(rec, cat)
            if cat == "dtc":
                item["manual"] = join_manual_dtc(gen, ecu if type(ecu) is int else (row.get("ecuIds") or [None])[0], rec)
            slim.append(item)
        off = int(req.get("offset") or 0)
        lim = int(req.get("limit") or 40)
        return _ok(
            {
                "profileId": pid,
                "category": cat,
                "total": len(slim),
                "offset": off,
                "items": slim[off : off + lim],
                "selected": slim[0] if type(at) is int and len(slim) == 1 else None,
                "systemEvidence": system_evidence(gen, ecu if type(ecu) is int else (row.get("ecuIds") or [None])[0]),
            }
        )
    cat = req.get("category")
    at = req.get("recordAt")
    if cat not in CATEGORIES:
        return _err("invalid_category", category=cat)
    if action in ("preview", "coding-options") and cat != "coding":
        return _err("preview_coding_only", category=cat)
    if type(at) is not int:
        return _err("cap_limit", field="recordAt")
    rec = _find_record(variant, cat, at)
    if rec is None:
        return _err("unknown_record", profileId=pid, category=cat, recordAt=at)
    hx = req.get("dataHex")
    if not isinstance(hx, str):
        return _err("missing_binary")
    try:
        data = _hex_bytes(hx)
    except ValueError as e:
        return _err("missing_binary", detail=str(e))
    if action == "coding-options":
        _text, parsed = formula_from_record(rec)
        if not parsed.get("ok") or parsed.get("kind") != "TEXTTABLE" or not 0 < parsed.get("bitLength", 0) <= 16:
            return _err("coding_options_not_defined")
        options = []
        for raw in range(1 << parsed["bitLength"]):
            preview = preview_coding(rec, data, raw)
            if not preview.get("ok"):
                continue
            decoded = decode_record(rec, bytes.fromhex(preview["afterHex"]))
            if decoded.get("textStatus") == "resolved" and decoded.get("text"):
                options.append({"rawValue": raw, "label": decoded["text"]})
                if len(options) > 256:
                    return _err("coding_options_limit")
        return _ok({"options": options, "decoded": decode_record(rec, data), "record": slim_record(rec, cat)})
    if action == "decode":
        mode = req.get("responseMode") or "data"
        if mode not in ("data", "pdu"):
            return _err("invalid_response_mode", responseMode=mode)
        out = decode_application_response(rec, data, mode)
        return _flags({**out, "ok": bool(out.get("ok")), "error": None if out.get("ok") else out.get("reason"), "record": slim_record(rec, cat)})
    raw = req.get("rawValue")
    if type(raw) is not int:
        return _err("malformed_raw_value")
    out = preview_coding(rec, data, raw)
    return _flags({**out, "ok": bool(out.get("ok")), "error": None if out.get("ok") else out.get("reason"), "record": slim_record(rec, cat)})


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read(MAX_STDIN + 1)
    if len(raw) > MAX_STDIN:
        print(json.dumps(_err("stdin_too_large"), ensure_ascii=False))
        return 2
    if not raw.strip():
        print(json.dumps(_err("empty_request"), ensure_ascii=False))
        return 2
    try:
        req = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps(_err("malformed_json", detail=str(e)), ensure_ascii=False))
        return 2
    out = handle(req)
    text = json.dumps(out, ensure_ascii=False, default=str)
    # never emit a raw VIN
    print(redact_vin(text) if isinstance(text, str) else text)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
