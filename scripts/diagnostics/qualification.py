"""Catalog identity qualification. VIN ignored. No live claim."""
from __future__ import annotations

from .catalog import load_catalog, profile_by_id

ECU_TO_CATALOG = {
    1: "porsche-981-2014-dme",
    9: "porsche-981-2014-gateway",
}

STATIC_VARIANT_LINKS = {
    (1, "porsche-981-2014-dme"): {
        "profile_id": "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",
        "module": "DME_BDE_Continental",
        "name": "SDI9_1_981_3_4L_EU5",
        "evidence": "validation/agreement.json DSN P200 <-> SDI9_1_981_3_4L_EU5; catalog identityConstraints",
    },
    (9, "porsche-981-2014-gateway"): {
        "profile_id": "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1",
        "module": "CAN_CAN_Gateway",
        "name": "CAN_CAN_Gateway_A7_1",
        "evidence": "validation/agreement.json A7.1 <-> CAN_CAN_Gateway_A7_1; catalog identityConstraints",
    },
}

SKIP_CONSTRAINTS = frozenset({"vinBound"})
REQUIRED_IDENTITY_KEYS = {
    1: frozenset({"dsn", "software", "hardware", "porschePart", "hardwarePart"}),
    9: frozenset({"dsn", "system", "identification", "porschePart", "hardwarePart", "hardware", "dataRecord"}),
}
_FLAGS = {
    "executionEnabled": False,
    "independentLiveVerified": False,
    "liveVerified": False,
    "vinExcluded": True,
}


def normalize_identity_value(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value.strip(" \t\x00")


def _required_keys(constraints: dict) -> list[str]:
    return [k for k in constraints if k not in SKIP_CONSTRAINTS]


def _fail(status: str, **extra) -> dict:
    out = {**_FLAGS, "status": status, "catalogProfileId": None, "variantLink": None, "observedProfileMatch": False}
    out.update(extra)
    return out


def qualify_identity(fingerprint, catalog=None, *, expected_generation: str | None = None) -> dict:
    if catalog is None:
        catalog = load_catalog()
    if not isinstance(fingerprint, dict):
        return _fail("malformed_fingerprint", reason="fingerprint-not-object")
    generation = fingerprint.get("generation")
    ecu_id = fingerprint.get("ecuId")
    raw_ident = fingerprint.get("identity")
    if raw_ident is None:
        raw_ident = {}
    elif not isinstance(raw_ident, dict):
        return _fail(
            "malformed_identity",
            generation=generation,
            ecuId=ecu_id,
            reason="identity-not-object",
        )
    if isinstance(ecu_id, bool) or (ecu_id is not None and type(ecu_id) is not int):
        return _fail("malformed_ecu_id", generation=generation, ecuId=ecu_id, reason="ecuId-must-be-int")
    identity = {}
    nonstring = []
    for k, v in raw_ident.items():
        if v is not None and not isinstance(v, str):
            nonstring.append(k)
            identity[k] = None
        else:
            identity[k] = normalize_identity_value(v)
    if generation not in ("981", "982"):
        return _fail("incomplete", missingRequired=["generation"], mismatches=[], ignoredFields=[], reason="generation-not-981-or-982")
    if expected_generation and generation != expected_generation:
        return _fail(
            "plan_generation_mismatch",
            generation=generation,
            expectedGeneration=expected_generation,
            ecuId=ecu_id,
            reason="fingerprint-generation-differs-from-plan",
        )
    if ecu_id not in ECU_TO_CATALOG:
        return _fail(
            "unknown_ecu",
            generation=generation,
            ecuId=ecu_id,
            missingRequired=[],
            mismatches=[],
            ignoredFields=list(identity),
            reason="no-observed-catalog-profile-for-ecuId",
        )
    profile_id = ECU_TO_CATALOG[ecu_id]
    profiles = catalog.get("profiles") if isinstance(catalog, dict) else None
    if not profiles:
        return _fail(
            "catalog_profile_missing",
            generation=generation,
            ecuId=ecu_id,
            reason="empty-or-missing-catalog-profiles",
        )
    try:
        profile = profile_by_id(catalog, profile_id)
    except KeyError:
        return _fail(
            "catalog_profile_missing",
            generation=generation,
            ecuId=ecu_id,
            reason=f"missing-profile:{profile_id}",
        )
    constraints = profile.get("identityConstraints")
    if (profile.get("model") != "981" or not isinstance(constraints, dict)
            or not REQUIRED_IDENTITY_KEYS[ecu_id].issubset(constraints)
            or any(not isinstance(v, str) or not normalize_identity_value(v)
                   for k, v in constraints.items() if k not in SKIP_CONSTRAINTS)):
        return _fail("catalog_constraints_invalid", generation=generation, ecuId=ecu_id,
                     reason="complete-observed-identity-constraints-required")
    required = _required_keys(constraints)
    ignored = [k for k in identity if k not in set(required)]
    missing = [k for k in required if identity.get(k) is None]
    mismatches = []
    for k in required:
        got = identity.get(k)
        exp = normalize_identity_value(constraints.get(k)) if isinstance(constraints.get(k), str) else constraints.get(k)
        if isinstance(exp, str):
            exp = normalize_identity_value(exp)
        if got is None:
            continue
        if exp is None or got != exp:
            mismatches.append({"field": k, "expected": exp, "got": got})
    for k in nonstring:
        if k not in required:
            continue
        mismatches.append({"field": k, "expected": "string", "got": type(raw_ident.get(k)).__name__})
    if generation != "981":
        return _fail(
            "wrong_generation",
            generation=generation,
            ecuId=ecu_id,
            missingRequired=missing,
            mismatches=mismatches,
            ignoredFields=ignored,
            reason="981-live-profile-does-not-apply-to-other-generation",
            samePartDoesNotInherit=True,
            constraintsCompared=required,
        )
    if mismatches:
        status = "mismatch"
    elif missing:
        status = "incomplete"
    else:
        status = "observed_profile_match"
    link = None
    if status == "observed_profile_match":
        src = STATIC_VARIANT_LINKS.get((int(ecu_id), profile_id))
        if src:
            link = {"kind": "static-capture-associated", "requiresFullFingerprint": True, **src}
    return {
        **_FLAGS,
        "status": status,
        "generation": generation,
        "ecuId": ecu_id,
        "catalogProfileId": profile_id if status == "observed_profile_match" else None,
        "matchedCatalogProfileId": profile_id if status == "observed_profile_match" else None,
        "observedProfileMatch": status == "observed_profile_match",
        "missingRequired": missing,
        "mismatches": mismatches,
        "ignoredFields": ignored,
        "variantLink": link,
        "constraintsCompared": required,
    }


def catalog_tx_rx(catalog: dict, ecu_id: int) -> dict | None:
    pid = ECU_TO_CATALOG.get(ecu_id)
    if not pid or not isinstance(catalog, dict):
        return None
    try:
        p = profile_by_id(catalog, pid)
    except KeyError:
        return None
    tx, rx = p.get("txId"), p.get("rxId")
    if (not tx or not rx or p.get("model") != "981"
            or not any(op.get("evidenceStatus") == "observed-x431-capture"
                       for op in p.get("operations", []))):
        return None
    return {
        "txId": tx,
        "rxId": rx,
        "catalogProfileId": pid,
        "provenance": "observed-x431-capture",
        "generation": "981",
    }


def capture_observed_ecu_ids_981(catalog: dict) -> list[int]:
    return [eid for eid in ECU_TO_CATALOG if catalog_tx_rx(catalog, eid)]
