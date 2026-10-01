"""Offline, capture-specific LAUNCH return extraction. No transport or ECU writes.

Long replies contain a VCI-reassembled PDU after an FF length, not a sequence
of physical CAN frames. Unknown two-byte trailers remain uninterpreted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
from functools import reduce
import hashlib
import json
import re
from operator import xor
from pathlib import Path
import unicodedata

from .x431_values import decode_record

ROOT = Path(__file__).resolve().parents[2]
FLAGS = {"executionEnabled": False, "writePayload": None, "independentLiveVerified": False}
DME = "9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5"
EXACT = {"DME_BDE_Continental": DME, "CAN_CAN_Gateway": "9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1"}
ADDRESSES = {"DME_BDE_Continental": 0x7E0, "CAN_CAN_Gateway": 0x710,
             "Getriebesteuerung_PDK": 0x71E, "BCM_vorne": 0x70E, "BCM_hinten": 0x70D}


def envelope(frame: bytes, direction: bytes) -> bool:
    return (len(frame) >= 10 and frame[:2] == b"\x55\xaa" and frame[2:4] == direction
            and int.from_bytes(frame[4:6], "big") + 7 == len(frame)
            and reduce(xor, frame[2:-1], 0) == frame[-1])


def read_request(frame: bytes):
    if not envelope(frame, b"\xf0\xf8") or len(frame) != 29 or frame[7:9] != b"\x27\x01":
        return None
    if frame[9:18] != bytes.fromhex("640001ff020d610108"):
        return None
    address = int.from_bytes(frame[18:20], "big")
    n = frame[20]
    if address & 31 or not 1 <= n <= 7 or frame[21] not in (0x1A, 0x21, 0x22):
        return None
    return {"txId": address >> 5, "requestHex": frame[21:21+n].hex().upper()}


def extract_return(frame: bytes):
    if not envelope(frame, b"\xf8\xf0") or len(frame) < 29:
        return None
    if frame[7:11] != bytes.fromhex("67010000") or frame[12:16] != bytes.fromhex("55aa0b08"):
        return None
    address = int.from_bytes(frame[16:18], "big")
    if address & 31:
        return None
    pci = frame[18]
    if 1 <= pci <= 7:
        if len(frame) != 29 or frame[11] != 1:
            return None
        pdu, padding, shape = frame[19:19+pci], frame[19+pci:26], "single"
    elif pci >> 4 == 1:
        n = ((pci & 15) << 8) | frame[19]
        # Exact reassembled VCI layout and frame-count consistency only.
        if n <= 7 or len(frame) != n + 23 or frame[11] != 1 + (n - 6 + 6) // 7:
            return None
        pdu, padding, shape = frame[20:20+n], b"", "vci-reassembled-long"
    else:
        return None
    return {"rxId": address >> 5, "pduHex": pdu.hex().upper(), "shape": shape,
            "paddingHex": padding.hex(), "uninterpretedTrailerHex": frame[-3:-1].hex()}


def paired_reads(frames, address_pairs):
    rows = []
    for i, frame in enumerate(frames):
        if frame["stream"][1] != "host_to_controller":
            continue
        request = read_request(bytes.fromhex(frame["frameHex"]))
        if request is None:
            continue
        row = {**request, "requestFrameId": frame["frameId"], "requestRecord": frame["startRecord"],
               "status": "unpaired", **FLAGS}
        reply = frames[i+1] if i+1 < len(frames) else None
        if (reply and reply["stream"][1] == "controller_to_host" and reply["stream"][0] == frame["stream"][0]
                and reply["stream"][2] == frame["stream"][2] and reply["sequenceByte"] == frame["sequenceByte"]
                and reply["commandByte"] == (frame["commandByte"] | 0x40)):
            value = extract_return(bytes.fromhex(reply["frameHex"]))
            row.update(responseFrameId=reply["frameId"], responseRecord=reply["startRecord"],
                       returnShape=value, status="unqualified-return")
            if value is not None:
                req, pdu = bytes.fromhex(request["requestHex"]), bytes.fromhex(value["pduHex"])
                if address_pairs.get(request["txId"]) != value["rxId"]:
                    row["status"] = "address-unqualified"
                elif len(pdu) == 3 and pdu[:2] == bytes((0x7F, req[0])):
                    row.update(status="pending" if pdu[2] == 0x78 else "negative", nrc=pdu[2])
                elif pdu.startswith(bytes((req[0] + 0x40,)) + req[1:]):
                    row.update(status="positive-candidate", dataHex=pdu[len(req):].hex())
                else:
                    row["status"] = "echo-mismatch"
        rows.append(row)
    return rows


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(value):
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def matches_display(value, displayed, declared_unit=None):
    if not value.get("ok"):
        return False
    text = value.get("text") or value.get("display")
    if isinstance(text, str) and norm(text) == norm(displayed):
        return True
    physical = value.get("phys")
    if physical is None and value.get("formulaKind") == "IDENTICAL" and type(value.get("value")) is int:
        physical = str(value["value"])
    if not value.get("numeric") or not isinstance(physical, str):
        return False
    unit = value.get("unit", declared_unit) or ""
    annotation = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*（单位原文：(.*?)）\s*", displayed)
    if annotation:
        displayed = annotation[1] + " " + annotation[2]
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*(.*?)\s*", displayed)
    if not match or norm(match[2]) != norm(unit):
        return False
    try:
        return Decimal(match[1]) == Decimal(physical)
    except InvalidOperation:
        return False


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def analyze(analysis, variants, output):
    output.mkdir(parents=True, exist_ok=False)
    address_source = ROOT / "docs/research/can-data/diagnostic/definitions.json"
    source = json.loads(address_source.read_text(encoding="utf-8"))
    # Resolve explicit source pairs; never use a generic TX+8 rule.
    pairs = {}
    def visit(obj):
        if isinstance(obj, dict):
            tx, rx = obj.get("requestAddress"), obj.get("responseAddress")
            if isinstance(tx, str) and isinstance(rx, str):
                try:
                    a, b = int(tx, 16), int(rx, 16)
                    if a in pairs and pairs[a] != b:
                        raise ValueError("conflicting-address-source")
                    pairs[a] = b
                except ValueError as exc:
                    if str(exc) == "conflicting-address-source":
                        raise
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list):
            for v in obj:
                visit(v)
    visit(source)
    frames = [json.loads(l) for l in (analysis / "app-frames.jsonl").open(encoding="utf-8")]
    rows = paired_reads(frames, pairs)
    dump(output / "read-pairs.json", rows)
    boundary = json.loads((analysis / "transport-audit.json").read_text(encoding="utf-8"))["checkpoints"][0]["record"]
    originals = json.loads((analysis / "original-values.json").read_text(encoding="utf-8"))
    candidates = json.loads((analysis / "original-definition-candidates.json").read_text(encoding="utf-8"))
    wanted = {m["definitionId"] for c in candidates for m in c["candidates"]}
    definitions = {}
    for line in (analysis / "coding-definitions.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        if row["definitionId"] in wanted:
            definitions[row["definitionId"]] = row
    comparisons = []
    for obs, candidate in zip(originals, candidates):
        assert obs["observationId"] == candidate["observationId"]
        evaluated = []
        for match in candidate["candidates"]:
            if obs["module"] in EXACT and match["profileId"] != EXACT[obs["module"]]:
                continue
            definition = definitions[match["definitionId"]]["sourceRecord"]
            for row in rows:
                if (row["requestRecord"] <= boundary or row["status"] != "positive-candidate"
                        or row["txId"] != ADDRESSES.get(obs["module"])
                        or row["requestHex"] != (definition.get("read_request_candidate_hex") or "").upper()):
                    continue
                value = decode_record(definition, bytes.fromhex(row["dataHex"]))
                text = value.get("text") or value.get("display")
                evaluated.append({"definitionId": match["definitionId"], "profileId": match["profileId"],
                    "requestFrameId": row["requestFrameId"], "responseFrameId": row["responseFrameId"],
                    "decoded": value, "matchesScreenshot": matches_display(value, obs["displayedValue"], definition.get("unit"))})
        matches = [e for e in evaluated if e["matchesScreenshot"]]
        comparisons.append({**obs, "matchingCandidates": matches, "evaluations": evaluated, "evaluationCount": len(evaluated),
            "status": "raw-and-screen-agree" if matches else "not-confirmed",
            "vehicleVariantQualified": obs["module"] in EXACT, **FLAGS})
    dump(output / "original-comparisons.json", comparisons)
    profile = next(json.loads(l) for l in variants.open(encoding="utf-8") if json.loads(l)["profile_id"] == DME)
    measured = []
    with (output / "engine-measurement-candidates.jsonl").open("x", encoding="utf-8") as target:
        for definition in profile["pool_records"]["measurement"]["records"]:
            request = (definition.get("disabledreadrequestcandidate") or {}).get("payload_hex", "").upper()
            applicable = [r for r in rows if r["requestRecord"] <= boundary and r["status"] == "positive-candidate"
                          and r["txId"] == 0x7E0 and r["requestHex"] == request]
            if not applicable:
                continue
            measured.append({"name": definition["name"], "at": definition["at"], "requestHex": request,
                             "groupId": definition["group_id_hex"], "samples": len(applicable)})
            for row in applicable:
                result = {"profileId": DME, "field": definition["name"], "definitionOffset": definition["at"],
                          "requestHex": request, "requestFrameId": row["requestFrameId"],
                          "responseFrameId": row["responseFrameId"], "responseRecord": row["responseRecord"],
                          "decoded": {k: v for k, v in decode_record(definition, bytes.fromhex(row["dataHex"])).items()
                                      if k in ("ok", "reason", "raw", "value", "phys", "display", "text", "unit", "numeric")}, **FLAGS}
                target.write(json.dumps(result, ensure_ascii=False) + "\n")
    dump(output / "engine-covered-fields.json", measured)
    summary = {"readPairs": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
        "returnShapes": dict(Counter((r.get("returnShape") or {}).get("shape") for r in rows)),
        "codingReadPairs": sum(r["requestRecord"] > boundary for r in rows),
        "originalRows": len(comparisons), "rawScreenAgreements": sum(bool(c["matchingCandidates"]) for c in comparisons),
        "agreementsByModule": dict(Counter(c["module"] for c in comparisons if c["matchingCandidates"])),
        "engineCoveredFields": len(measured), "engineDefinitionFields": profile["pools"]["measurement"]["count"],
        "inputs": [{"path": str(p), "sha256": sha(p)} for p in (analysis / "app-frames.jsonl", variants, address_source)],
        "limits": ["VCI reassembled application return, not physical ISO-TP capture", "Body/PDK version identities remain unqualified",
                   "Engine definitions may share response blocks; coverage does not imply UI-selected or independently verified fields",
                   "No new live command or write authorization"], **FLAGS}
    dump(output / "summary.json", summary)
    dump(output / "manifest.json", [{"file": p.name, "sha256": sha(p), "bytes": p.stat().st_size}
                                    for p in output.iterdir() if p.is_file()])
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--variants", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.analysis_dir, args.variants, args.output_dir), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
