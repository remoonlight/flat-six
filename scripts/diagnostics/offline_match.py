"""Match local X431 measurements, DSN selectors and captured reads. Never opens hardware.

The selector reader is a bounded bytewalk, not LAUNCH's native selector evaluator.
Selector-only matches remain candidates. Shared response coverage is not an
independent field validation and never grants a live request or write permission.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct

from .catalog import load_catalog, profile_by_id
from .decode import decode_payload
from .elm import parse_ath1_response
from .qualification import ECU_TO_CATALOG, qualify_identity
from .response_values import data_span, decode_application_response, request_from_record
from .x431_capture import ADDRESSES, envelope, paired_reads
from .x431_formula import formula_from_record
from .x431_values import classify_decode

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VARIANTS = ROOT / ".local/x431-re/2026-09-27-981982/expansion/variants.jsonl"
DEFAULT_ANALYSIS = ROOT / ".local/vehicle-analysis/981-x431-20261001"
DEFAULT_RUNS = ROOT / ".local/vehicle-runs/981-20261001T083428Z"
DSN_PATH = ROOT / ".local/x431-re/2026-09-27-decode/file-loader/decoded/DSN.BIN.dec"
DATA_PATH = DSN_PATH.with_name("9X1_ALLDATA.BIN.dec")
ADDRESS_PATH = ROOT / "docs/research/can-data/diagnostic/definitions.json"
REGISTRY_PATH = ROOT / "data/seed/diagnostics/workshop-registry.v1.json"
SOURCE_PATH = ROOT / ".local/vehicle-runs/981-x431-coding-20261001T095850Z/final/btsnoop_hci.log"
ANCHORS = {
    "variants": "8a9e0ee94c25e98b11affbc083615bbd4c8113fe9c2a820ca8e4147aa16d5c20",
    "frames": "a75e199a0283ee4511dd10a64f6f282ee2cf9a5e1adb127a1d5fb31428263c15",
    "addresses": "da5820bd72c5d60df8b20fab79492f86925774200d9962f1bbcb4a1ed6f69df0",
    "dsn": "55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f",
    "data": "f32173d768ca9f8d2caf109bc7159fa43bde816f924fea5a91f1f202a2a76a2e",
    "hci": "af9b14ea69c8c6eb2fe02a904e4e4868911f807fb651458e7400391c8d60e357",
}
FLAGS = {"executionEnabled": False, "independentLiveVerified": False,
         "liveApproved": False, "writePayload": None, "fittedClaim": False}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def verify_source(path: Path, expected: str | None = None) -> dict:
    actual = sha(path)
    if expected is not None and actual != expected.lower():
        raise ValueError(f"source-hash-mismatch:{path.name}")
    return {"path": str(path.resolve()), "sha256": actual, "bytes": path.stat().st_size,
            "anchorVerified": expected is not None}


def dump(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def explicit_address_pairs(source) -> dict:
    pairs = {}

    def visit(obj):
        if isinstance(obj, dict):
            tx, rx = obj.get("requestAddress"), obj.get("responseAddress")
            if isinstance(tx, str) and isinstance(rx, str):
                # References contain explicit "unknown-as-fact" placeholders.
                if re.fullmatch(r"[0-9A-Fa-f]{3}", tx) and re.fullmatch(r"[0-9A-Fa-f]{3}", rx):
                    a, b = int(tx, 16), int(rx, 16)
                    if a in pairs and pairs[a] != b:
                        raise ValueError("conflicting-address-source")
                    pairs[a] = b
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list):
            for v in obj:
                visit(v)
    visit(source)
    return pairs


def verify_frames(frames):
    seen = set()
    for frame in frames:
        raw = bytes.fromhex(frame["frameHex"])
        if frame["frameId"] in seen:
            raise ValueError("duplicate-frame-id")
        seen.add(frame["frameId"])
        direction = frame["stream"][1]
        if direction not in ("host_to_controller", "controller_to_host"):
            raise ValueError("unknown-frame-direction")
        pair = bytes.fromhex("f0f8" if direction == "host_to_controller" else "f8f0")
        if (hashlib.sha256(raw).hexdigest() != frame["frameSha256"].lower()
                or not envelope(raw, pair) or len(raw) != frame["length"]
                or raw[6] != frame["sequenceByte"] or raw[7] != frame["commandByte"]):
            raise ValueError(f"invalid-frame:{frame['frameId']}")


def replay_serial(raw):
    """Reconstruct already saved ATH1 bytes with their explicit TX/RX settings."""
    transactions = []
    current, tx, rx = None, None, None

    def finish():
        if current and current.get("req") and tx is not None and rx is not None:
            text = bytes.fromhex(current["rxHex"]).decode("ascii", errors="replace")
            result = parse_ath1_response(text, req_hex=current["req"], rx_id=current["rxId"],
                tx_id=current["txId"], sent_sf=current.get("sf", "").replace(" ", ""))
            transactions.append({"requestHex": current["req"].replace(" ", "").upper(),
                "txId": current["txId"], "rxId": current["rxId"], "result": result,
                "promptComplete": ">" in text})

    for row in raw:
        if row["dir"] == "tx":
            finish()
            cmd = row.get("cmd", "")
            if cmd.startswith("ATSH "):
                tx = int(cmd.split()[1], 16)
            if cmd.startswith("ATCRA "):
                rx = int(cmd.split()[1], 16)
            current = {**row, "rxHex": "", "txId": tx, "rxId": rx}
        elif row["dir"] == "rx" and current is not None:
            current["rxHex"] += row["hex"]
    finish()
    return transactions


def qualify_saved_identity(manifest, raw, catalog):
    """Ignore cached qualification/decoded fields. Require non-synthetic raw replies."""
    if manifest.get("mode") != "live" or manifest.get("simulation") is not False:
        return {"status": "ignored-simulation-or-unknown-mode", **FLAGS}
    profile_id = manifest.get("profileId")
    ecu_id = next((e for e, p in ECU_TO_CATALOG.items() if p == profile_id), None)
    if ecu_id is None:
        return {"status": "unknown-catalog-profile", **FLAGS}
    profile = profile_by_id(catalog, profile_id)
    operations = {o["id"]: o for o in profile["operations"]}
    transactions = replay_serial(raw)
    identity, errors = {}, []
    for row in manifest.get("results", []):
        if row.get("role") != "identity" or row.get("field") == "vin":
            continue
        op = operations.get(row.get("operationId"))
        if not op or not row.get("ok") or row.get("synthetic", False):
            errors.append("invalid-identity-result")
            continue
        payload = (row.get("payload_hex") or "").upper()
        matching = [t for t in transactions if t["requestHex"] == op["requestHex"].upper()
            and t["txId"] == int(profile["txId"], 16) and t["rxId"] == int(profile["rxId"], 16)]
        if (not matching or any(not t["promptComplete"] or not t["result"].get("ok")
                or t["result"].get("payload_hex", "").upper() != payload for t in matching)
                or not payload.startswith(op["positivePrefixHex"].upper())):
            errors.append(f"raw-identity-not-confirmed:{row['field']}")
            continue
        decoded = decode_payload(payload, op["decode"])
        if not decoded.get("ok") or not isinstance(decoded.get("text"), str):
            errors.append(f"identity-decode-failed:{row['field']}")
            continue
        field, value = row["field"], decoded["text"]
        if field in identity and identity[field] != value:
            errors.append(f"identity-conflict:{field}")
        identity[field] = value
    qualified = qualify_identity({"generation": "981", "ecuId": ecu_id, "identity": identity},
                                  catalog, expected_generation="981")
    if errors:
        qualified.update(status="raw-identity-rejected", observedProfileMatch=False, variantLink=None,
                         catalogProfileId=None, matchedCatalogProfileId=None)
    return {**qualified, "identity": identity, "rawErrors": errors, **FLAGS}


def selector_hits(blob: bytes, name: str) -> list[dict]:
    """Exact variant-name, u16 padded string length, u32 pointer, bounded selector.

    All occurrences are retained, including other menu subtrees. A bytewalk hit
    alone never proves generation, native predicate semantics, or ECU fitment.
    """
    needle = name.encode("ascii") + b"\0"
    hits, start = [], 0
    while True:
        pos = blob.find(needle, start)
        if pos < 0:
            break
        start = pos + 1
        if pos < 2:
            continue
        n = struct.unpack_from("<H", blob, pos - 2)[0]
        if not len(needle) <= n <= 160 or pos + n + 4 > len(blob):
            continue
        ptr = struct.unpack_from("<I", blob, pos + n)[0]
        if ptr + 4 > len(blob):
            continue
        tag, size = struct.unpack_from("<HH", blob, ptr)
        if not 2 <= size <= 80 or ptr + 4 + size > len(blob):
            continue
        raw = blob[ptr + 4:ptr + 4 + size]
        if b"\0" not in raw:
            continue
        try:
            text = raw.split(b"\0", 1)[0].decode("ascii")
        except UnicodeDecodeError:
            continue
        # Textual PDK byte selectors have an explicit 0x marker. Do not guess
        # that arbitrary ASCII IDs (e.g. 050018) are hexadecimal bytes.
        binary = re.fullmatch(r"0x([0-9A-Fa-f]{2}) ([0-9A-Fa-f]{2})", text)
        payload = bytes.fromhex("".join(binary.groups())) if binary else text.encode("ascii")
        hits.append({"variantNameOffset": pos - 2, "selectorOffset": ptr,
            "selectorTag": tag, "selectorText": text, "identityDataHex": payload.hex().upper(),
            "comparison": "explicit-hex-bytes" if binary else "ascii",
            "structureStatus": "partial-bytewalk-not-native-selector-evaluation"})
    return hits


def request_groups(rows, frames_by_id, boundary):
    """Partition by adapter stream, explicit address pair, request, capture phase."""
    grouped = {}
    for row in rows:
        ret = row.get("returnShape") or {}
        f = frames_by_id[row["requestFrameId"]]
        phase = "before-coding" if row["requestRecord"] <= boundary else "coding-workflow"
        key = (tuple((f["stream"][0], f["stream"][2])), row["txId"], ret.get("rxId"), row["requestHex"], phase)
        if key not in grouped:
            grouped[key] = {"groupId": f"capture-{len(grouped)+1:04}", "adapterStream": list(key[0]),
                "txId": row["txId"], "rxId": ret.get("rxId"), "requestHex": row["requestHex"],
                "capturePhase": phase, "statusCounts": Counter(), "samples": [], **FLAGS}
        g = grouped[key]
        g["statusCounts"][row["status"]] += 1
        if row["status"] == "positive-candidate":
            reply = frames_by_id[row["responseFrameId"]]
            g["samples"].append({"requestFrameId": row["requestFrameId"],
                "responseFrameId": row["responseFrameId"], "requestRecord": row["requestRecord"],
                "responseRecord": row["responseRecord"], "requestFrameSha256": f["frameSha256"],
                "responseFrameSha256": reply["frameSha256"], "pduHex": ret["pduHex"],
                "returnShape": ret["shape"]})
    return list(grouped.values())


def selector_candidates(variants, groups, blob, addresses):
    observed, systems, refs = defaultdict(set), defaultdict(set), defaultdict(list)
    for g in groups:
        if g["requestHex"] not in ("1A9F", "22F1A2", "22F19E"):
            continue
        n = len(bytes.fromhex(g["requestHex"]))
        for sample in g["samples"]:
            value = bytes.fromhex(sample["pduHex"])[n:].rstrip(b"\0")
            target = systems if g["requestHex"] == "22F19E" else observed
            target[g["txId"]].add(value)
            refs[g["txId"]].append({"groupId": g["groupId"], "responseFrameId": sample["responseFrameId"],
                                    "requestHex": g["requestHex"]})
    result = {}
    for variant in variants:
        module = variant["module"]
        if module not in addresses or not observed[addresses[module]]:
            continue
        hits = selector_hits(blob, variant["name"])
        tx = addresses[module]
        normalized = {b.rstrip(b" ") for b in observed[tx]}
        matching = [h for h in hits if bytes.fromhex(h["identityDataHex"]).rstrip(b" ") in normalized]
        kind = "dsn"
        if not matching:
            # Compare the entire two-component text exactly. This is still a
            # bytewalk candidate, not a native predicate interpretation.
            combined = {dsn + b"#" + system for dsn in observed[tx] for system in systems[tx]}
            matching = [h for h in hits if h["selectorText"].encode("ascii") in combined]
            kind = "dsn-and-system"
        if not matching:
            # A system name can narrow a family, but do not infer that 001246
            # satisfies the suffix #001. Its comparison semantics are unknown.
            matching = [h for h in hits if h["selectorText"].count("#") == 1
                and h["selectorText"].split("#")[0].encode("ascii") in systems[tx]]
            kind = "system-only"
        if matching:
            result[variant["profile_id"]] = {"matchingHits": matching,
                "selectorMatchKind": kind,
                "unresolvedSelectorConditions": sorted({h["selectorText"].split("#")[1] for h in matching}) if kind == "system-only" else [],
                "observedIdentityValues": [b.hex().upper() for b in sorted(observed[tx])],
                "observedSystemValues": [b.hex().upper() for b in sorted(systems[tx])],
                "captureReferences": refs[tx],
                "conflictingObservedIdentities": len(observed[tx]) > 1 or len(systems[tx]) > 1,
                "generationNotProvenByBytewalk": True, **FLAGS}
    # A full DSN/text comparison is stronger than a system-name-only match
    # within the same family. Keep tied strong matches; discard weaker family
    # alternatives instead of listing A3/A4 next to the known A7.1 gateway.
    modules = {v["profile_id"]: v["module"] for v in variants}
    strong = {modules[pid] for pid, evidence in result.items() if evidence["selectorMatchKind"] != "system-only"}
    return {pid: evidence for pid, evidence in result.items()
            if evidence["selectorMatchKind"] != "system-only" or modules[pid] not in strong}


def match_parameter(variant, rec, groups, *, identity_qualified=False, selector_candidate=False, selector_partial=False):
    request = request_from_record(rec)
    _, formula = formula_from_record(rec)
    ready = classify_decode(formula, rec.get("byteOffset"), rec.get("bitOffset"))
    status = "definition-only"
    excluded = variant.get("generation") == "982"
    applicable = [g for g in groups if request.get("ok") and g["requestHex"] == request["request"].hex().upper()]
    successes = failures = 0
    failure_reasons, examples = Counter(), []
    for group in applicable:
        # Decode each distinct raw response once per definition, then retain
        # the sample count and exact references for every repeated response.
        pdus = defaultdict(list)
        for sample in group["samples"]:
            pdus[sample["pduHex"]].append(sample)
        for pdu, samples in pdus.items():
            result = decode_application_response(rec, bytes.fromhex(pdu), mode="pdu", source=group["groupId"])
            if result.get("ok"):
                successes += len(samples)
            else:
                failures += len(samples)
                failure_reasons[result.get("reason", "unknown")] += len(samples)
            if len(examples) < 2:
                value = {k: v for k, v in result.items() if k not in ("evidence", "payload", "pdu")}
                examples.append({"groupId": group["groupId"], "responseFrameId": samples[0]["responseFrameId"],
                    "pduSha256": hashlib.sha256(bytes.fromhex(pdu)).hexdigest(),
                    "occurrences": len(samples), "decoded": value})
    if excluded:
        status = "excluded-982"
    elif not request.get("ok"):
        status = "request-unresolved"
    elif not ready.get("ok"):
        status = "decoder-unresolved"
    elif failures:
        status = "partial-decode" if successes else "response-decode-failed"
    elif successes:
        status = "offline-matched" if identity_qualified else "capture-associated-candidate"
    elif identity_qualified:
        status = "identity-matched-response-missing"
    elif selector_partial:
        status = "system-candidate-response-missing"
    elif selector_candidate:
        status = "selector-candidate-response-missing"
    return {"parameterId": f"{variant['profile_id']}:{rec.get('at')}",
        "profileId": variant["profile_id"], "module": variant["module"], "generation": variant.get("generation"),
        "name": rec.get("name"), "unit": rec.get("unit"), "status": status,
        "identityQualified": identity_qualified, "selectorCandidate": selector_candidate,
        "selectorConditionsUnresolved": selector_partial,
        "requestHex": request["request"].hex().upper() if request.get("ok") else None,
        "requestIssue": request.get("reason"), "decoderReady": ready.get("ok", False),
        "decoderIssue": ready.get("reason"), "dataSpan": data_span(rec),
        "byteOffset": rec.get("byteOffset"), "bitOffset": rec.get("bitOffset"),
        "formula": rec.get("formula"), "enumText": rec.get("enumText"),
        "source": rec.get("source"), "definitionOffset": rec.get("at"), "groupIdHex": rec.get("group_id_hex"),
        "captureGroupIds": [g["groupId"] for g in applicable],
        "decodedSampleCount": successes, "failedSampleCount": failures,
        "failureReasons": dict(failure_reasons), "examples": examples,
        "logicalParameterKey": hashlib.sha256(json.dumps([
            variant["profile_id"], rec.get("name"), rec.get("unit"),
            request["request"].hex() if request.get("ok") else None,
            rec.get("byteOffset"), rec.get("bitOffset"), rec.get("formula"), rec.get("enumText")
        ], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "independentFieldValidated": False, **FLAGS}


def write_report(output, summary, units):
    lines = ["# 981 实时数据离线匹配", "",
        "本次仅分析本地文件。未连接车辆、未访问数据库、未修改实时采集允许列表。", "",
        f"覆盖 {summary['unitCount']} 个 X431 菜单组、{summary['variantCount']} 个变体、"
        f"{summary['measurementDefinitionCount']:,} 条跨变体测量记录；这些不是本车装配数量。", "",
        "## 已有完整身份与静态变体的对应", ""]
    for unit in units:
        for v in unit["matchedVariants"]:
            n = v["measurementStatuses"].get("offline-matched", 0)
            lines += [f"- {unit['label']}：`{v['name']}`；{n}/{v['measurementCount']} 条记录可用已有响应离线解码，"
                f"合并完全重复记录后为 {v['uniqueOfflineMatchedCount']} 项，涉及 {v['captureMatchedRequestCount']} 个读取请求。"]
    lines += ["", "身份结论重新解码具名 vLinker 原始串口响应，未信任旧缓存或模拟结果。"
        "变体关联沿用已记录的静态对应，不等于完整软件标定或物理参数验收。", "",
        "## DSN 缩小后的候选", ""]
    for unit in units:
        for v in unit["selectorCandidates"]:
            lines += [f"- {unit['label']}：`{v['name']}`，{v['measurementCount']} 条测量定义，"
                f"去重为 {v['uniqueMeasurementCount']} 项；"
                "完整身份、原生选择器后续条件、对应实时响应仍缺。"]
        for v in unit["systemCandidates"]:
            lines += [f"- {unit['label']}：系统名关联到 `{v['name']}`，{v['measurementCount']} 条定义，"
                f"去重 {v['uniqueMeasurementCount']} 项；选择器版本条件仍未解码，不能称为 DSN 完整匹配。"]
    lines += ["", "DSN 指针来自精确变体名的有界字节遍历；保留全部命中偏移。"
        "PDK `42 10` 按显式 `0x42 10` 字节选择器比较，车身 `050018` 按 ASCII 比较。"
        "安全气囊按完整 `000008#Airbag.` 文本关联。PCM 系统名命中 AW7 系列，"
        "`001246` 与选择器 `#001` 的关系尚未证明，不按前缀猜测版本。"
        "变体里的 GT4 是源数据命名，不能据此推断车辆型号。", "",
        "## 其他单元", ""]
    lines += [f"- {u['label']}：有 {u['variantCount']} 个静态变体、"
        f"{u['measurementDefinitionsAcrossVariants']} 条跨变体测量记录；缺完整本车身份与匹配测量响应。"
        for u in units if u["status"] == "definition-only"]
    lines += ["", "## 文件与边界", "",
        "`selected-variants.json`：身份关联变体、DSN 候选与系统名候选的参数、单位、请求、字节位置、公式与源偏移。",
        "`parameters.jsonl`：全部变体逐记录结果；`units.json`：按单元列缺口；"
        "`selector-candidates.json`：DSN 指针证据；`identity-matches.json`：身份复核。",
        "`request-groups.json`：按适配器流、地址对、请求、抓包阶段分组，保留每次原始帧引用；"
        "`manifest.json`：输入、解码代码及输出 SHA-256。", "",
        "共享响应覆盖多个字段不代表多个独立实车验证。编码流程里的读取也不代表持续实时采集。"
        "VCI 重组应用 PDU 不当作物理 CAN 帧；所有执行、独立实车验证、装配声明标志保持 false。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def analyze(output: Path, *, variants_path=DEFAULT_VARIANTS, analysis=DEFAULT_ANALYSIS, runs=DEFAULT_RUNS):
    if output.exists():
        raise ValueError("output-already-exists-use-a-fresh-directory")
    sources = [verify_source(p, ANCHORS[k]) for k, p in (
        ("variants", variants_path), ("frames", analysis / "app-frames.jsonl"),
        ("addresses", ADDRESS_PATH), ("dsn", DSN_PATH), ("data", DATA_PATH), ("hci", SOURCE_PATH))]
    audit = json.loads((analysis / "transport-audit.json").read_text(encoding="utf-8"))
    frame_summary = json.loads((analysis / "app-frame-summary.json").read_text(encoding="utf-8"))
    if audit["sha256"].lower() != ANCHORS["hci"] or frame_summary["sourceTransportSha256"].lower() != ANCHORS["hci"]:
        raise ValueError("capture-source-chain-mismatch")
    sources += [verify_source(analysis / name) for name in ("transport-audit.json", "app-frame-summary.json")]
    sources += [verify_source(REGISTRY_PATH), verify_source(ROOT / "data/seed/diagnostics/catalog.v1.json")]
    frames = [json.loads(l) for l in (analysis / "app-frames.jsonl").open(encoding="utf-8")]
    verify_frames(frames)
    pairs = explicit_address_pairs(json.loads(ADDRESS_PATH.read_text(encoding="utf-8")))
    rows = paired_reads(frames, pairs)
    groups = request_groups(rows, {f["frameId"]: f for f in frames}, audit["checkpoints"][0]["record"])
    catalog, identity_observations, qualified = load_catalog(), [], {}
    for path in sorted(runs.glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("mode") != "live" or manifest.get("simulation") is not False:
            continue
        raw_path = path.with_name("raw.json")
        evidence = qualify_saved_identity(manifest, json.loads(raw_path.read_text(encoding="utf-8")), catalog)
        sources += [verify_source(path), verify_source(raw_path)]
        identity_observations.append({"manifest": str(path.resolve()), **evidence})
    for ecu_id in ECU_TO_CATALOG:
        evidence = [e for e in identity_observations if e.get("ecuId") == ecu_id]
        if evidence and all(e["observedProfileMatch"] for e in evidence):
            values = {json.dumps(e["identity"], sort_keys=True) for e in evidence}
            if len(values) == 1:
                qualified[evidence[0]["variantLink"]["profile_id"]] = evidence
    # Preserve the module link's candidate nature. PCM uses the explicit
    # author-reference address pair, not a claim that the local route is qualified.
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["groups"]
    addresses = dict(ADDRESSES)
    # Route all families of a menu group as candidates. Selecting only legacy
    # Head_Unit here previously missed the observed MIB family in the PCM menu.
    for ecu_id, tx, rx in ((4, 0x715, 0x77F), (70, 0x773, 0x7DD)):
        if pairs.get(tx) == rx:
            for unit in registry:
                if unit["ecuId"] == ecu_id:
                    addresses.update({module: tx for module in unit["dsnModules"]})
    with variants_path.open(encoding="utf-8") as source:
        variants = [json.loads(line) for line in source]
    selectors = selector_candidates(variants, groups, DSN_PATH.read_bytes(), addresses)
    by_module = defaultdict(list)
    for group in registry:
        for module in set(group["dsnModules"]):
            by_module[module].append(group["ecuId"])
    output.mkdir(parents=True)
    dump(output / "identity-matches.json", identity_observations)
    dump(output / "selector-candidates.json", selectors)
    dump(output / "request-groups.json", groups)
    variant_summaries, totals, selected_records = [], Counter(), {}
    with (output / "parameters.jsonl").open("x", encoding="utf-8") as target:
        for variant in variants:
            pid, module = variant["profile_id"], variant["module"]
            counts, records = Counter(), variant.get("pool_records", {}).get("measurement", {}).get("records", [])
            unique_all, unique_matched, matched_requests = set(), set(), set()
            if pid in qualified or pid in selectors:
                selected_records[pid] = []
            tx = addresses.get(module)
            applicable = [g for g in groups if tx is not None and g["txId"] == tx and g["rxId"] == pairs.get(tx)]
            for rec in records:
                source = rec.get("source") or {}
                if (source.get("sha256", "").lower() != ANCHORS["data"] or source.get("variant") != variant["name"]
                        or source.get("module") != module):
                    raise ValueError(f"definition-source-mismatch:{pid}:{rec.get('at')}")
                result = match_parameter(variant, rec, applicable, identity_qualified=pid in qualified,
                    selector_candidate=pid in selectors and not selectors[pid]["conflictingObservedIdentities"],
                    selector_partial=pid in selectors and not selectors[pid]["conflictingObservedIdentities"]
                        and selectors[pid]["selectorMatchKind"] == "system-only")
                result["ecuIds"] = by_module[module]
                unique_all.add(result["logicalParameterKey"])
                if result["status"] == "offline-matched":
                    unique_matched.add(result["logicalParameterKey"])
                    matched_requests.add(result["requestHex"])
                if pid in selected_records:
                    selected_records[pid].append(result)
                counts[result["status"]] += 1
                totals[result["status"]] += 1
                target.write(json.dumps(result, ensure_ascii=False) + "\n")
            identity_records = variant.get("pool_records", {}).get("identity", {}).get("records", [])
            identity_requests = sorted({r["derived_request_hex"] for r in identity_records if r.get("derived_request_hex")})
            status = ("excluded-982" if variant.get("generation") == "982" else
                      "identity-matched" if pid in qualified else
                      "selector-conflict" if pid in selectors and selectors[pid]["conflictingObservedIdentities"] else
                      "system-selector-candidate" if pid in selectors and selectors[pid]["selectorMatchKind"] == "system-only" else
                      "selector-candidate" if pid in selectors else "definition-only")
            variant_summaries.append({"profileId": pid, "module": module, "name": variant["name"],
                "ecuIds": by_module[module], "generation": variant.get("generation"), "status": status,
                "measurementCount": len(records), "measurementStatuses": dict(counts),
                "uniqueMeasurementCount": len(unique_all), "uniqueOfflineMatchedCount": len(unique_matched),
                "captureMatchedRequestCount": len(matched_requests),
                "selectorMatchKind": selectors.get(pid, {}).get("selectorMatchKind"),
                "identityRequestCandidates": identity_requests,
                "identityDecoderIssue": "Static identity offsets/suffix predicates unresolved; catalog decoder used only for named DME/Gateway",
                "capturePositiveSamples": sum(len(g["samples"]) for g in applicable), **FLAGS})
    units = []
    for unit in registry:
        vv = [v for v in variant_summaries if unit["ecuId"] in v["ecuIds"]]
        matched = [v for v in vv if v["status"] == "identity-matched"]
        candidates = [v for v in vv if v["status"] == "selector-candidate"]
        partial = [v for v in vv if v["status"] == "system-selector-candidate"]
        status = "identity-matched" if matched else "selector-candidate" if candidates else "system-selector-candidate" if partial else "definition-only"
        missing = ["independent-field-validation", "desktop-live-integration"]
        if not matched:
            missing += ["complete-ecu-identity-and-version", "native-selector-predicate-validation"]
        if not any(v["measurementStatuses"].get("offline-matched", 0) for v in matched):
            missing.append("qualified-realtime-response-capture")
        units.append({"ecuId": unit["ecuId"], "label": unit["label"], "status": status,
            "matchedVariants": matched, "selectorCandidates": candidates, "systemCandidates": partial, "variantCount": len(vv),
            "measurementDefinitionsAcrossVariants": sum(v["measurementCount"] for v in vv),
            "missingEvidence": missing, **FLAGS})
    dump(output / "variants.json", variant_summaries)
    dump(output / "units.json", units)
    dump(output / "selected-variants.json", [{**v, "parameters": selected_records[v["profileId"]]}
        for v in variant_summaries if v["profileId"] in selected_records])
    summary = {"targetGeneration": "981", "unitCount": len(units), "variantCount": len(variants),
        "measurementDefinitionCount": sum(v["measurementCount"] for v in variant_summaries),
        "unitStatuses": dict(Counter(u["status"] for u in units)), "parameterStatuses": dict(totals),
        "captureFrames": len(frames), "captureReadPairs": len(rows), "captureStatuses": dict(Counter(r["status"] for r in rows)),
        "captureRequestGroups": len(groups), "qualifiedVariants": list(qualified),
        "selectorCandidateVariants": [v["profileId"] for v in variant_summaries if v["status"] == "selector-candidate"],
        "systemCandidateVariants": [v["profileId"] for v in variant_summaries if v["status"] == "system-selector-candidate"],
        "noNewHardwareAccess": True, "databaseAccess": False,
        "limits": ["35 menu groups are not installed units; 291 variants are not fitted systems",
            "Selectors are exact-name bounded bytewalk candidates; no native predicate or complete calibration claim",
            "Same family left/right units stay separate; no response copied as both units' observation",
            "VCI-reassembled application PDUs, not physical CAN frames or bus topology",
            "Capture groups preserve adapter stream, address pair, request and phase",
            "Shared response fields and coding-workflow reads are not independently selected realtime measurements",
            "Offline results do not change live allowlists or the six standard PID desktop sampler"], **FLAGS}
    dump(output / "summary.json", summary)
    write_report(output, summary, units)
    decoder_paths = [Path(__file__), *(ROOT / "scripts/diagnostics" / f for f in
        ("response_values.py", "x431_values.py", "x431_formula.py", "x431_capture.py", "qualification.py", "elm.py", "isotp.py", "pair.py", "decode.py"))]
    # Refuse a report whose inputs changed while it was being generated.
    for source in sources:
        verify_source(Path(source["path"]), source["sha256"])
    manifest = {"inputs": sources, "code": [verify_source(p) for p in decoder_paths],
        "outputs": [verify_source(p) for p in sorted(output.iterdir()) if p.is_file()], **FLAGS}
    dump(output / "manifest.json", manifest)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--variants", type=Path, default=DEFAULT_VARIANTS)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--identity-runs", type=Path, default=DEFAULT_RUNS)
    args = parser.parse_args()
    print(json.dumps(analyze(args.output_dir, variants_path=args.variants, analysis=args.analysis_dir, runs=args.identity_runs),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
