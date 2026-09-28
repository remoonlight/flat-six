"""Field-semantic GAG joins. Never pick EXPRESS/TEXT by high-byte heuristic."""
from __future__ import annotations

try:
    from .gag_lib import compact_express, compact_text
except ImportError:
    from gag_lib import compact_express, compact_text


def dstream_labels(lang, ids: list[int]) -> tuple[list[dict], int, int]:
    labels = []
    unresolved = 0
    source_empty = 0
    if lang is None:
        return (
            [{"id_hex": f"{i:08X}", "text": None, "namespace": "DSTREAM_CN.GAG"} for i in ids],
            len(ids),
            0,
        )
    for idu in ids:
        t = compact_text(lang.lookup_id("DSTREAM_CN.GAG", idu))
        labels.append({"id_hex": f"{idu:08X}", "text": t, "namespace": "DSTREAM_CN.GAG"})
        if t is None:
            unresolved += 1
        elif t == "":
            source_empty += 1
    return labels, unresolved, source_empty


def express_formula(lang, formula_id: int | None) -> tuple[dict | None, int]:
    if formula_id is None:
        return None, 1
    if lang is None:
        return {"id_hex": f"{formula_id:08X}", "execution_enabled": False}, 1
    row = compact_express(lang.lookup_id("EXPRESS.GAG", formula_id))
    if not row or row.get("text") is None:
        return {"id_hex": f"{formula_id:08X}", "execution_enabled": False}, 1
    return row, 0


def enum_text(lang, formula: dict | None) -> tuple[dict, int, int, int]:
    """TEXTTABLE option IDs -> TEXT_CN only. Empty string != missing."""
    ids = ((formula or {}).get("express") or {}).get("text_ids") or []
    if not ids:
        return {}, 0, 0, 1
    out = {}
    unresolved = 0
    source_empty = 0
    if lang is None:
        return out, len(ids), 0, 0
    for idu in ids:
        t = compact_text(lang.lookup_id("TEXT_CN.GAG", idu))
        key = f"{idu:08X}"
        out[key] = t
        if t is None:
            unresolved += 1
        elif t == "":
            source_empty += 1
    return out, unresolved, source_empty, 0


def unit_label(lang, formula: dict | None) -> tuple[str | None, int, int, int]:
    uid = ((formula or {}).get("express") or {}).get("unit_id")
    if not uid:
        return None, 0, 0, 1
    if lang is None:
        return None, 1, 0, 0
    t = compact_text(lang.lookup_id("DSTREAMU_CN.GAG", uid))
    if t is None:
        return None, 1, 0, 0
    if t == "":
        return "", 0, 1, 0
    return t, 0, 0, 0


def join_status(*, labels_unresolved: int, formula_unresolved: int, enum_unresolved: int, unit_unresolved: int = 0) -> str:
    if labels_unresolved or formula_unresolved or enum_unresolved or unit_unresolved:
        return "structurally_decoded_gag_incomplete"
    return "structurally_decoded_gag_joined"
