from __future__ import annotations

import json
from pathlib import Path

from .allowlist import BLOCKED_SIDS, blocked_reason

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = REPO_ROOT / "data" / "seed" / "diagnostics" / "catalog.v1.json"
PUBLIC_CATALOG = REPO_ROOT / "data" / "seed" / "diagnostic-reference-981" / "catalog.json"
PUBLIC_MANIFEST = REPO_ROOT / "data" / "seed" / "diagnostic-reference-981" / "manifest.json"
OBSERVED_PROFILE_IDS = ("porsche-981-2014-dme", "porsche-981-2014-gateway")


def load_catalog(path: Path | None = None) -> dict:
    p = path or DEFAULT_CATALOG
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1:
        raise ValueError("catalog schemaVersion must be 1")
    return data


def load_public_reference() -> dict | None:
    if not PUBLIC_CATALOG.is_file() or not PUBLIC_MANIFEST.is_file():
        return None
    cat = json.loads(PUBLIC_CATALOG.read_text(encoding="utf-8"))
    man = json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    return {"catalog": cat, "manifest": man}


def _entry_brief(e: dict) -> dict:
    od = e.get("originalDefinition") or {}
    return {
        "id": e.get("id"),
        "kind": e.get("kind"),
        "sourceId": e.get("sourceId"),
        "executionEnabled": False,
        "decoderReady": bool(e.get("decoderReady")),
        "evidenceStatus": e.get("evidenceStatus"),
        "applicabilityClass": e.get("applicabilityClass"),
        "unit": e.get("unit"),
        "formula": e.get("formula"),
        "payloads": e.get("payloads"),
        "label": (od.get("signalName") or od.get("parameterName") or od.get("title")
                  or od.get("subFunction") or od.get("function") or e.get("id")),
        "system": od.get("system") or od.get("ecu"),
        "cannotEnterAllowlist": True,
    }


def public_cli_block() -> dict:
    ref = load_public_reference()
    if ref is None:
        return {"present": False}
    cat = ref["catalog"]
    entries = cat.get("entries") or []
    engine_kinds = {"broadcast-signal", "measurement-channel-reference"}
    engine = [_entry_brief(e) for e in entries if e.get("kind") in engine_kinds]
    engine += [
        _entry_brief(e)
        for e in entries
        if e.get("kind") == "diagnostic-read-definition"
        and e.get("applicabilityClass") == "standard-if-supported"
    ]
    coding = [_entry_brief(e) for e in entries if e.get("kind") == "coding-menu-reference"]
    return {
        "present": True,
        "path": "data/seed/diagnostic-reference-981/catalog.json",
        "manifest": ref["manifest"],
        "executionEnabled": False,
        "cannotEnterAllowlist": True,
        "observedProfileIds": list(OBSERVED_PROFILE_IDS),
        "observedCatalogFile": "data/seed/diagnostics/catalog.v1.json",
        "coverage": cat.get("coverage"),
        "unsupported": cat.get("unsupported"),
        "conflicts": cat.get("conflicts"),
        "engineCandidates": engine,
        "codingCandidates": coding,
        "definitionGaps": [
            "No executable live engine PID/measurement in catalog.v1.json; only identity + DTC captures.",
            "Public engine rows are candidates (broadcast/channel/standard-if-supported); formulas/support bitmaps unverified on this car.",
            "Coding remains disabled; public coding rows are X431 menu text with payloads null.",
        ],
    }


def catalog_cli_json(catalog: dict | None = None) -> dict:
    cat = catalog or load_catalog()
    profiles = []
    for p in cat["profiles"]:
        profiles.append(
            {
                "id": p["id"],
                "ecu": p.get("ecu"),
                "txId": p.get("txId"),
                "rxId": p.get("rxId"),
                "codingEnabled": bool((p.get("coding") or {}).get("enabled")),
                "operations": [
                    {
                        "id": op["id"],
                        "requestHex": op["requestHex"],
                        "liveAllowed": bool(op.get("liveAllowed")),
                        "label": op.get("label"),
                    }
                    for op in p.get("operations") or []
                ],
            }
        )
    return {
        "schemaVersion": cat.get("schemaVersion"),
        "evidenceStatusNote": cat.get("evidenceStatusNote"),
        "observedProfiles": profiles,
        "publicReference": public_cli_block(),
        "liveAllowlistSource": "data/seed/diagnostics/catalog.v1.json liveAllowed only",
        "x431ReplacementComplete": False,
    }


def profile_by_id(catalog: dict, profile_id: str) -> dict:
    for p in catalog["profiles"]:
        if p["id"] == profile_id:
            return p
    raise KeyError(profile_id)


def live_allowed_hex(profile: dict) -> set[str]:
    out = set()
    for op in profile.get("operations") or []:
        if not op.get("liveAllowed"):
            continue
        hx = op["requestHex"].upper()
        raw = bytes.fromhex(hx)
        if blocked_reason(raw, {hx}) is None and raw[0] not in BLOCKED_SIDS:
            out.add(hx)
    return out


def operation(profile: dict, op_id: str) -> dict:
    for op in profile.get("operations") or []:
        if op["id"] == op_id:
            return op
    raise KeyError(op_id)
