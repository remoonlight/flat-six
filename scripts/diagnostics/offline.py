"""Offline 981/982 qualification + car-session plan. Never serial, never send."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .allowlist import BLOCKED_SIDS
from .catalog import live_allowed_hex
from .qualification import ECU_TO_CATALOG, capture_observed_ecu_ids_981, catalog_tx_rx, qualify_identity
from .response_values import data_span, request_from_record
from .x431_formula import formula_from_record
from . import x431_values

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COVERAGE = REPO_ROOT / "data" / "seed" / "diagnostics" / "coverage-981-982.v1.json"
DEFAULT_REGISTRY = REPO_ROOT / "data" / "seed" / "diagnostics" / "workshop-registry.v1.json"
DEFAULT_VARIANTS = REPO_ROOT / ".local" / "x431-re" / "2026-09-27-981982" / "expansion" / "variants.jsonl"
SCHEMA = 1
FLAGS = {
    "schemaVersion": SCHEMA,
    "executionEnabled": False,
    "liveVerified": False,
    "independentLiveVerified": False,
    "writePayload": None,
    "x431ReplacementComplete": False,
}


def _classify_extract(parsed: dict, byte_off, bit_off) -> dict:
    fn = getattr(x431_values, "classify_extract", None) or x431_values.classify_decode
    return fn(parsed, byte_off, bit_off)


def _flags(d: dict) -> dict:
    out = dict(FLAGS)
    out.update(d)
    return out


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_registry(path: Path | None = None) -> dict:
    p = path or DEFAULT_REGISTRY
    data = load_json(p)
    if data is None:
        return {"present": False, "path": str(p), "reason": "workshop-registry-absent"}
    return {"present": True, "path": str(p), "data": data}


def load_coverage(path: Path | None = None) -> dict:
    p = path or DEFAULT_COVERAGE
    data = load_json(p)
    if data is None:
        raise FileNotFoundError(p)
    return data


def _load_catalog(catalog: dict | None):
    if catalog is not None:
        return catalog
    from .catalog import load_catalog

    return load_catalog()


def find_variant(variants_path: Path, profile_id: str) -> dict | None:
    if not variants_path.is_file():
        return None
    with variants_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o.get("profile_id") == profile_id:
                return o
    return None


def group_measurements(records: list[dict]) -> dict:
    grouped: dict[tuple, list] = defaultdict(list)
    missing = []
    unsupported = []
    sid31 = 0
    for rec in records:
        raw_sid = rec.get("rawSID")
        wire = rec.get("wireSID")
        cand = rec.get("disabledreadrequestcandidate") or {}
        payload = cand.get("payload_hex")
        payload_u = str(payload).replace(" ", "").upper() if payload else None
        _, parsed = formula_from_record(rec)
        clf = _classify_extract(parsed, rec.get("byteOffset"), rec.get("bitOffset"))
        row = {
            "at": rec.get("at"),
            "name": rec.get("name"),
            "byteOffset": rec.get("byteOffset"),
            "bitOffset": rec.get("bitOffset"),
            "formulaKind": parsed.get("kind"),
            "formulaOk": bool(clf.get("ok")),
            "classifyReason": clf.get("reason"),
            "fieldsStatus": rec.get("fields_status"),
            "rawSID": raw_sid,
            "wireSID": wire,
            "pid": rec.get("pid"),
            "requestHex": payload_u,
            "candidateDisabled": True,
            "executionEnabled": False,
        }
        sid31_ok = raw_sid == 0x31 and wire == 0x22 and payload_u is not None and payload_u.startswith("22")
        if raw_sid == 0x31:
            if sid31_ok:
                sid31 += 1
            else:
                unsupported.append({**row, "reason": "sid31-wire-or-payload-inconsistent", "formulaOk": False})
                continue
        fields_ok = rec.get("fields_status") == "ok" and cand.get("status") == "ok"
        if not fields_ok or payload_u is None or wire is None:
            missing.append({**row, "reason": rec.get("fields_status") or cand.get("status") or "no-payload"})
            continue
        req = request_from_record(rec)
        if not req.get("ok"):
            unsupported.append({**row, "reason": req.get("reason") or "wire-payload-inconsistent", "formulaOk": False})
            continue
        if not clf.get("ok"):
            unsupported.append({**row, "reason": clf.get("reason") or "classify-extract-rejected"})
            continue
        span = data_span(rec)
        if span is None:
            unsupported.append({**row, "reason": "offset-unproven"})
            continue
        row["minDataBytes"] = span["dataMin"]
        row["minLengthKind"] = span["kind"]
        row["unknownRemaining"] = bool(span.get("unknownRemaining"))
        row["minPduBytes"] = span["dataMin"] + req["headerLen"]
        key = (int(wire), payload_u)
        grouped[key].append(row)
    requests = []
    for (wire, hx), rows in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        kinds = {r.get("minLengthKind") for r in rows}
        kind = "lowerBound" if "lowerBound" in kinds else "exact"
        data_min = max(r["minDataBytes"] for r in rows)
        header = 3 if wire == 0x22 else 2
        requests.append(
            {
                "wireSID": wire,
                "requestHex": hx,
                "executionEnabled": False,
                "liveApproved": False,
                "minResponseLength": data_min,
                "minResponseLengthKind": kind,
                "minResponseLengthExact": kind == "exact",
                "minDataBytes": data_min,
                "minPduBytes": data_min + header,
                "minResponseLengthNote": (
                    "DATA lower bound from proven offsets; PDU adds positive header; "
                    "not an exact-length reject for padding or variable text"
                ),
                "recordCount": len(rows),
                "records": rows,
                "candidateDisabled": True,
                "decoder": "response_values+x431_values after explicit responseMode",
                "countsContainDuplicates": True,
            }
        )
    return {
        "requestGroups": requests,
        "measurementRecordCount": len(records),
        "groupedRecordCount": sum(len(v) for v in grouped.values()),
        "missing": missing,
        "unsupported": unsupported,
        "sid31AliasedTo22Count": sid31,
        "candidateDisabled": True,
        "responseMustBeStrippedBeforeDecoder": True,
        "countsContainDuplicates": True,
        "liveApproved": False,
    }


def _charger_amps(factory_ops) -> int | None:
    if not isinstance(factory_ops, dict):
        return None
    read = factory_ops.get("read")
    if isinstance(read, dict) and isinstance(read.get("chargerAmps"), int):
        return read["chargerAmps"]
    return None


def _factory_procedure(generation: str, registry_info: dict, groups: list[dict]) -> dict:
    if not registry_info.get("present"):
        return {
            "authority": "workshop-registry",
            "present": False,
            "batteryChargerMinimumFromReviewedPages": None,
            "factsUnknown": True,
            "doNotPromoteAcrossGenerations": True,
            "doNotPromote991OrClubsportAsProductionDefault": True,
            "componentProtectionNotUniversalCoding": True,
            "gatewayReplacementWm903555": None,
            "clear": {"enabled": False, "writePayload": None},
            "coding": {"enabled": False, "writePayload": None},
        }
    amps = None
    gw_coding = None
    for g in groups:
        fo = g.get("factoryOps") or {}
        if amps is None:
            amps = _charger_amps(fo)
        if g.get("ecuId") == 9 and isinstance(fo.get("coding"), dict):
            gw_coding = {
                "ecuId": 9,
                "role": "gateway-replacement-reference-only",
                "notUniversalCodingRequirement": True,
                "wm": fo["coding"].get("wm"),
                "pages": fo["coding"].get("pages"),
                "locator": fo["coding"].get("locator"),
            }
    return {
        "authority": "workshop-registry generations[].factoryOps",
        "present": True,
        "batteryChargerMinimumFromReviewedPages": amps,
        "factsUnknown": amps is None,
        "doNotPromoteAcrossGenerations": True,
        "doNotPromote991OrClubsportAsProductionDefault": True,
        "componentProtectionNotUniversalCoding": True,
        "gatewayReplacementWm903555": gw_coding if generation == "982" else None,
        "clear": {"enabled": False, "writePayload": None},
        "coding": {"enabled": False, "writePayload": None},
    }


def build_plan(
    generation: str,
    *,
    identity: dict | None = None,
    variant_id: str | None = None,
    variants_path: Path | None = None,
    coverage: dict | None = None,
    registry_info: dict | None = None,
    review: dict | None = None,
    catalog: dict | None = None,
) -> dict:
    if generation not in ("981", "982"):
        raise ValueError("generation must be 981 or 982")
    cov = coverage if coverage is not None else load_coverage()
    reg = registry_info if registry_info is not None else load_registry()
    cat = _load_catalog(catalog)
    q = qualify_identity(identity, cat, expected_generation=generation) if identity is not None else None
    match_ok = bool(q and q.get("status") == "observed_profile_match" and generation == "981")
    captured_981 = capture_observed_ecu_ids_981(cat) if isinstance(cat, dict) else []
    vpath = variants_path or DEFAULT_VARIANTS
    variants_present = vpath.is_file()
    selected = None
    if variant_id:
        if not variants_present:
            selected = {"present": False, "profile_id": variant_id, "reason": "variants-file-absent"}
        else:
            found = find_variant(vpath, variant_id)
            if found is None:
                selected = {"present": False, "profile_id": variant_id, "reason": "profile-not-in-variants"}
            else:
                meas = ((found.get("pool_records") or {}).get("measurement") or {}).get("records") or []
                selected = {
                    "present": True,
                    "profile_id": found.get("profile_id"),
                    "module": found.get("module"),
                    "name": found.get("name"),
                    "membership": found.get("membership"),
                    "on_target_menu": found.get("on_target_menu"),
                    "generationToken": found.get("generation"),
                    "physicalFitClaim": False,
                    "poolCounts": {
                        k: (v.get("count") if isinstance(v, dict) else None)
                        for k, v in (found.get("pool_records") or {}).items()
                    },
                    "measurements": group_measurements(meas),
                    "executionEnabled": False,
                }
    groups = []
    menu = cov.get("menu_ecus") or []
    reg_groups = {(g.get("ecuId")): g for g in ((reg.get("data") or {}).get("groups") or [])} if reg.get("present") else {}
    for ecu in menu:
        ecu_id = ecu.get("ecu_id")
        rg = reg_groups.get(ecu_id) or {}
        gen_block = ((rg.get("generations") or {}).get(generation)) or {}
        catalog_pid = ECU_TO_CATALOG.get(ecu_id) if generation == "981" else None
        qualified_here = bool(match_ok and q and q.get("ecuId") == ecu_id)
        addr = catalog_tx_rx(cat, ecu_id) if qualified_here else None
        if generation == "982":
            addr_note = "generation-982-no-captured-address"
        elif ecu_id in captured_981 and not qualified_here:
            addr_note = "capture-observed-981-identity-not-qualified-for-this-group"
        elif addr is None:
            addr_note = "address-not-inferred"
        else:
            addr_note = None
        variant_link = q.get("variantLink") if qualified_here else None
        groups.append(
            {
                "ecuId": ecu_id,
                "label": ecu.get("label"),
                "dsnModules": list(ecu.get("dsn_modules") or []),
                "variantCount": ecu.get("variant_count"),
                "membership": ecu.get("membership"),
                "poolSummary": ecu.get("pools"),
                "fittedClaim": False,
                "wiringStatus": gen_block.get("wiringStatus"),
                "manualStatus": gen_block.get("manualStatus"),
                "addressProvenance": gen_block.get("addressProvenance") or "unobserved",
                "diagnosticAddress": addr,
                "addressNote": addr_note,
                "catalogProfileId": catalog_pid if ecu_id in ECU_TO_CATALOG else None,
                "variantLink": variant_link,
                "variantCandidates": {
                    "modules": list(ecu.get("dsn_modules") or []),
                    "softwareCompatibilityGuessed": False,
                    "note": "menu/module relationship only; not a fitted-or-compatible claim",
                },
                "factoryOps": gen_block.get("factoryOps"),
                "evidence": gen_block.get("evidence") or [],
                "blockers": list(rg.get("gaps") or gen_block.get("gaps") or []),
                "gaps": gen_block.get("gaps") or rg.get("gaps") or [],
                "clearCoding": {"enabled": False, "writePayload": None},
                "executionEnabled": False,
            }
        )
    allow = {}
    for p in cat.get("profiles") or []:
        allow[p["id"]] = sorted(live_allowed_hex(p))
    unknown_addr = [g["ecuId"] for g in groups if g["diagnosticAddress"] is None]
    qualified_ids = [q["ecuId"]] if match_ok else []
    checklist = {
        "adapterTranscriptSetup": {
            "executeNow": False,
            "notes": [
                "Do not open serial in this module",
                "Use python -m scripts.diagnostics.bench for virtual rehearsal only",
                "Live read remains catalog liveAllowed + --yes-read-only-live",
            ],
        },
        "vehicleEcuIdentity": q,
        "captureOnlyReadOnlyBaseline": {
            "catalogOperations": [
                {"profileId": p["id"], "operationId": op["id"], "requestHex": op["requestHex"], "liveAllowed": op.get("liveAllowed")}
                for p in cat.get("profiles") or []
                for op in p.get("operations") or []
            ]
            if generation == "981"
            else [],
            "982InheritsNone": generation == "982",
        },
        "groupsToVerify": [{"ecuId": g["ecuId"], "label": g["label"]} for g in groups],
        "rawLogEvidenceArchive": {
            "privateCaptures": ".local/savvycan-vlinker/2026-09-26/runs/{20260926-233624-327076,20260926-234115-903227}",
            "vinInRepo": False,
        },
        "observedVsUnobserved": {
            "captureObservedEcuIds981": captured_981,
            "identityQualifiedEcuIds": qualified_ids,
            "unknownAddressEcuIds": unknown_addr,
            "generation982CapturedAddresses": [] if generation == "982" else None,
        },
        "factoryProcedure": _factory_procedure(generation, reg, groups),
        "blockedSids": sorted(f"{s:02X}" for s in BLOCKED_SIDS),
        "liveAllowlistUnchanged": allow,
    }
    absent = []
    if not reg.get("present"):
        absent.append("workshop-registry")
    if variant_id and not variants_present:
        absent.append("variants.jsonl")
    return _flags(
        {
            "generation": generation,
            "groupCount": len(groups),
            "groups": groups,
            "identityQualification": q,
            "selectedVariant": selected,
            "workshopRegistry": {
                "present": bool(reg.get("present")),
                "path": reg.get("path"),
                "reason": reg.get("reason"),
                "sources": (reg.get("data") or {}).get("sources") if reg.get("present") else None,
            },
            "variants": {"present": variants_present, "path": str(vpath)},
            "coverageCounts": cov.get("counts"),
            "checklist": checklist,
            "absentInputs": absent,
            "cannotInferUnknownEcuAddresses": True,
            "decoderGap": "explicit responseMode data|pdu required; plan publishes DATA/PDU lower bounds for supported groups only; no live send",
            "valuesCli": "python -m scripts.diagnostics.x431_values decode --variants PATH --profile-id ID --at N --data-hex HEX",
        }
    )


def match_identity(fingerprint: dict, catalog: dict | None = None) -> dict:
    return _flags({"identityQualification": qualify_identity(fingerprint, catalog)})


def summary_doc(coverage: dict | None = None, registry_info: dict | None = None) -> dict:
    cov = coverage if coverage is not None else load_coverage()
    reg = registry_info if registry_info is not None else load_registry()
    return _flags(
        {
            "menuEcuCount": len(cov.get("menu_ecus") or []),
            "coverageCounts": cov.get("counts"),
            "workshopRegistry": {"present": bool(reg.get("present")), "groupCount": (reg.get("data") or {}).get("groupCount")},
            "observedCatalogProfiles": list(ECU_TO_CATALOG.values()),
        }
    )


def _cli_fail(code: int, reason: str, **extra) -> int:
    doc = _flags({"ok": False, "reason": reason, **extra})
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return code


def _match_exit(status: str) -> int:
    if status == "observed_profile_match":
        return 0
    if status in ("malformed_fingerprint", "malformed_identity", "malformed_ecu_id"):
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if any(a == "--send" or a.startswith("--send=") for a in argv):
        return _cli_fail(2, "send-refused")
    p = argparse.ArgumentParser(prog="python -m scripts.diagnostics.offline")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    pl.add_argument("--generation", required=True, choices=("981", "982"))
    pl.add_argument("--out", required=True)
    pl.add_argument("--variants", default=None)
    pl.add_argument("--identity", default=None)
    pl.add_argument("--variant", default=None)
    pl.add_argument("--registry", default=None)
    mt = sub.add_parser("match")
    mt.add_argument("--identity", required=True)
    mt.add_argument("--out", default=None)
    sm = sub.add_parser("summary")
    sm.add_argument("--out", default=None)
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 2)
    if args.cmd == "plan":
        ident = None
        if args.identity:
            ip = Path(args.identity)
            if not ip.is_file():
                return _cli_fail(2, "identity-path-missing", path=str(ip))
            try:
                ident = json.loads(ip.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                return _cli_fail(2, "identity-json-invalid", detail=str(e))
            if not isinstance(ident, dict):
                return _cli_fail(2, "identity-type-invalid", got=type(ident).__name__)
        vpath = Path(args.variants) if args.variants else DEFAULT_VARIANTS
        rinfo = load_registry(Path(args.registry)) if args.registry else load_registry()
        try:
            doc = build_plan(
                args.generation,
                identity=ident,
                variant_id=args.variant,
                variants_path=vpath,
                registry_info=rinfo,
            )
        except Exception as e:  # noqa: BLE001
            return _cli_fail(2, "plan-failed", detail=str(e))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("out", args.out, "groups", doc["groupCount"], "executionEnabled", False)
        q = doc.get("identityQualification")
        if ident is not None and q:
            return _match_exit(q.get("status") or "incomplete")
        return 0
    if args.cmd == "match":
        ip = Path(args.identity)
        if not ip.is_file():
            return _cli_fail(2, "identity-path-missing", path=str(ip))
        try:
            ident = json.loads(ip.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return _cli_fail(2, "identity-json-invalid", detail=str(e))
        if not isinstance(ident, dict):
            return _cli_fail(2, "identity-type-invalid", got=type(ident).__name__)
        doc = match_identity(ident)
        text = json.dumps(doc, indent=2, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(text)
        return _match_exit((doc.get("identityQualification") or {}).get("status") or "incomplete")
    doc = summary_doc()
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
