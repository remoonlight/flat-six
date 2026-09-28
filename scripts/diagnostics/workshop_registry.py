"""Join X431 menu ECU groups to local wiring/manual locators. No ECU write. No pin->CAN-ID."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFLINE = REPO_ROOT / ".local" / "x431-re" / "2026-09-27-offline"
DEFAULT_COVERAGE = REPO_ROOT / "data" / "seed" / "diagnostics" / "coverage-981-982.v1.json"
DEFAULT_CATALOG = REPO_ROOT / "data" / "seed" / "diagnostics" / "catalog.v1.json"
DEFAULT_WIRING_981 = Path(r"C:/Users/Ric/Desktop/981/接线图/981.pdf")
DEFAULT_WIRING_982 = Path(r"C:/Users/Ric/Desktop/981/接线图/982.pdf")
DEFAULT_WIRING_GT4 = Path(r"C:/Users/Ric/Desktop/981/接线图/981GT4_CS.pdf")
DEFAULT_MANUAL_981 = Path(r"C:/Users/Ric/Desktop/981/Boxster BoxsterS BoxsterGTS (981).pdf")
DEFAULT_MANUAL_982 = Path(r"C:/Users/Ric/Desktop/981/Porsche 718 Boxster BoxsterS BoxsterGTS (982).pdf")
DEFAULT_MANUAL_991 = Path(r"Z:/porsche/981/991_Factory Service Manual.pdf")
DEFAULT_OUT = REPO_ROOT / "data" / "seed" / "diagnostics" / "workshop-registry.v1.json"
DEFAULT_EXTRACT = REPO_ROOT / ".local" / "obd-analysis" / "manual" / "extract"
DEFAULT_MANUAL_REPORT = REPO_ROOT / ".local" / "obd-analysis" / "manual-report.md"
DTC_CODES = REPO_ROOT / "data" / "seed" / "dtc" / "manuals" / "by-code" / "codes.json"
DTC_981 = REPO_ROOT / "data" / "seed" / "dtc" / "manuals" / "981" / "obd-faults.md"
DTC_982 = REPO_ROOT / "data" / "seed" / "dtc" / "manuals" / "982" / "obd-faults.md"
LOCAL_MANUAL = OFFLINE / "manual"
INVENTORY_PATH = OFFLINE / "pdf-inventory.json"
TOC_CACHE = OFFLINE / "pdf-toc.json"

# Coordinator-verified SHA256 (uppercase compare). Facts bind only on match.
BASELINE = {
    "wiring-981": {
        "sha256": "40E4C38CFBEA097EDBA39E5ECA57A4C93D08A489F5248DED9985BA16153DD5BD",
        "pages": 62,
        "bytes": 114569995,
        "basename": "981.pdf",
    },
    "wiring-982": {
        "sha256": "225E241226EA72FDDB8C3DE3D8FB851B714C2897C768291C2114276EF79DD55F",
        "pages": 79,
        "bytes": 145904909,
        "basename": "982.pdf",
    },
    "wiring-gt4cs": {
        "sha256": "9156960F294E02E61DBBCFF3E4A2695CAEDFB3995DAE2CBF1BC3ECDD895DBE46",
        "pages": 46,
        "bytes": 80905462,
        "basename": "981GT4_CS.pdf",
    },
    "manual-981": {
        "sha256": "366E165914400046685FAE9D36A91120EEAC565597B4CED2AC2920804DA58DF5",
        "pages": 4455,
        "bytes": 230248573,
        "basename": "Boxster BoxsterS BoxsterGTS (981).pdf",
    },
    "manual-982": {
        "sha256": "8191D809B1B21938F97A80963BD619BD2EA0142186EF495ACDC98351F3A8BBFA",
        "pages": 8160,
        "bytes": 200575931,
        "basename": "Porsche 718 Boxster BoxsterS BoxsterGTS (982).pdf",
    },
    "manual-991": {
        "sha256": "7887939B6CDE19903B82731C477823CBCF873E3A47879331C48CA18FA8B6ABDC",
        "pages": 8256,
        "bytes": 358302150,
        "basename": "991_Factory Service Manual.pdf",
    },
}

CATALOG_ADDR = {
    "porsche-981-2014-dme": {"ecuId": 1, "txId": "7E0", "rxId": "7E8"},
    "porsche-981-2014-gateway": {"ecuId": 9, "txId": "710", "rxId": "77A"},
}

# Wiring TOC title tokens. Hits are candidates, not pin-verified connectivity.
WIRING_TOC_TOKENS: dict[int, tuple[str, ...]] = {
    1: ("DME", "61C", "68B", "78B"),
    2: ("PDK", "50B"),
    4: ("气囊",),
    5: ("PSM",),
    7: ("空调", "气候箱"),
    8: ("仪表板", "组合仪表", "仪表 驾驶舱"),
    9: ("网关", "CAN 网络", "CAN 拓扑", "CAN 网关结构"),
    18: ("驻车辅助",),
    19: ("PASM", "PADM"),
    29: ("秒表", "计时器"),
    30: ("驾驶员侧车门",),
    31: ("乘客侧车门",),
    32: ("前端电子设备",),
    35: ("后端电子设备",),
    39: ("活顶", "车顶系统", "CABRIO"),
    47: ("40A", "左侧前照灯"),
    48: ("40B", "右侧前照灯"),
    50: ("停车制动", "驻车制动"),
    54: ("转向 柱", "多功能方向盘", "预选"),
    55: ("轮胎压力", "TPM"),
    64: ("ACC", "自动距离", "自适应巡航"),
    65: ("BOSE", "ASK", "扬声器", "BURMESTER"),
    68: ("选档杆",),
    69: ("电子转向",),
    70: ("PCM", "收音机", "PCM 4"),
    74: ("前部摄像机", "前部摄像", "前摄像头"),
    75: ("电视调谐",),
    76: ("左侧座椅", "座椅加热线束 左", "腰部支撑线束，左侧"),
    78: ("右侧座椅", "腰部支撑线束，右侧"),
    81: ("倒车摄像",),
    87: ("调音器", "发动机声音", "Symposer"),
    88: ("中央前照灯",),
    95: ("车道变更", "车道变换"),
    96: ("车道变更", "车道变换"),
    165: ("ERA", "GLONASS"),
}

COMPLETION_ROOT = REPO_ROOT / ".local" / "x431-re" / "2026-09-27-offline-completion"
COORDINATOR_FACTS_PATHS = (
    COMPLETION_ROOT / "NEW" / "coordinator-wiring-facts.json",
    COMPLETION_ROOT / "coordinator-wiring-facts.json",
)

# 982 TOC may label axle harnesses; coordinator visually verified these sheets
# on baseline SHA 225E2412...55F. Not pin connectivity / not CAN IDs.
WIRE982_VISUAL_SHEETS = {
    5: {
        "page1based": 62,
        "printed": "PSM / control unit PSM",
        "reviewPng": "source-review/wire982-62-top.png",
    },
    50: {
        "page1based": 64,
        "printed": "EPB control unit / EPB switch",
        "reviewPng": "source-review/wire982-64-top.png",
    },
}


def visual_sheet_hits(ecu_id: int, generation: str, identities: dict[str, dict]) -> list[dict]:
    if generation != "982" or ecu_id not in WIRE982_VISUAL_SHEETS:
        return []
    ident = identities.get("wiring-982") or {}
    if not ident.get("baselineMatch"):
        return []
    spec = WIRE982_VISUAL_SHEETS[ecu_id]
    return [
        {
            "generation": "982",
            "kind": "visual_sheet",
            "role": "visual_sheet_verified",
            "sourceId": "wiring-982",
            "locator": ident.get("locator") or BASELINE["wiring-982"]["basename"],
            "page1based": spec["page1based"],
            "printedLabels": spec["printed"],
            "reviewPng": spec["reviewPng"],
            "sourceSha256": ident.get("sha256"),
            "connectivity": "not_pin_verified",
            "notADiagnosticAddress": True,
        }
    ]


def load_coordinator_wiring_facts() -> dict | None:
    for path in COORDINATOR_FACTS_PATHS:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def bind_obd_gateway_facts(source_id: str, identity: dict | None) -> dict | None:
    ident = identity or {}
    digest = ident.get("sha256")
    if not ident.get("baselineMatch") or not digest:
        return None
    data = load_coordinator_wiring_facts()
    if not data:
        return None
    src = next((s for s in data.get("sources") or [] if s.get("id") == source_id), None)
    if not src or src.get("sha256") != digest:
        return None
    return {
        "printedModelYear": src.get("printedModelYear"),
        "printedSheet": src.get("printedSheet"),
        "printedModel": src.get("printedModel"),
        "pdfPage": src.get("pdfPage"),
        "sha256": src.get("sha256"),
        "pinPairs": list(data.get("obdToGateway") or []),
        "gatewayConnectorPairs": list(data.get("gatewayConnectorPairs") or []),
        "crops": list(data.get("crops") or []),
        "sourcePinAssignmentsVerified": True,
        "liveContinuityVerified": False,
        "notCanAddressEvidence": True,
        "allModelYears": False,
        "fittedClaim": False,
    }


def obd_path_hits(ecu_id: int, generation: str, identities: dict[str, dict]) -> list[dict]:
    if ecu_id != 9:
        return []
    sid = "wiring-981" if generation == "981" else "wiring-982"
    bound = bind_obd_gateway_facts(sid, identities.get(sid) or {})
    if not bound:
        return []
    return [
        {
            "generation": generation,
            "kind": "obd_gateway_pin_path",
            "role": "source_pin_assignment_verified",
            "sourceId": sid,
            "page1based": bound["pdfPage"],
            "printedSheet": bound["printedSheet"],
            "printedModelYear": bound["printedModelYear"],
            "pinPairs": bound["pinPairs"],
            "gatewayConnectorPairs": bound["gatewayConnectorPairs"],
            "sourcePinAssignmentsVerified": True,
            "liveContinuityVerified": False,
            "notADiagnosticAddress": True,
            "notCanId": True,
            "sourceSha256": bound["sha256"],
            "crops": bound["crops"],
            **DISABLED,
        }
    ]


WM_TOC_TOKENS: dict[int, tuple[str, ...]] = {
    1: ("DME", "2470", "DFI"),
    2: ("PDK", "3730", "变速箱"),
    4: ("气囊", "安全气囊", "6953", "Airbag"),
    5: ("PSM", "4562"),
    7: ("空调", "8720", "Air-Conditioning"),
    8: ("组合仪表", "仪表板", "9025", "Instrument Cluster"),
    9: ("网关", "Gateway", "9035", "903555", "903519"),
    18: ("驻车辅助", "PDC", "9174", "Park Assist"),
    19: ("PASM", "4316"),
    29: ("秒表", "计时器", "9030", "Stopwatch"),
    30: ("驾驶员侧车门", "Driver Door", "5773"),
    31: ("乘客侧车门", "Passenger Door"),
    32: ("前端电子", "9449", "Front-End Electronics"),
    35: ("后端电子", "5789", "Rear-End Electronics"),
    39: ("活顶", "Convertible Top", "6196", "CABRIO"),
    47: ("9457", "Headlights Left", "左侧前照灯"),
    48: ("Headlights Right", "右侧前照灯"),
    50: ("驻车制动", "EPB", "4662", "Parking Brake"),
    54: ("转向盘", "9162", "Steering Wheel Electronics"),
    55: ("轮胎压力", "4434", "TPM"),
    64: ("ACC", "2785", "Adaptive Cruise"),
    65: ("9144", "External Amplifier", "BOSE"),
    68: ("选档杆", "3708", "Selector Lever"),
    69: ("4890", "Power Steering", "电子转向"),
    70: ("PCM", "9110", "911019"),
    74: ("9638", "Front Camera", "前摄像头"),
    75: ("电视调谐", "TV"),
    76: ("7293", "Seat Adjust Driver", "驾驶员侧座椅"),
    78: ("Seat Adjust Passenger", "乘客侧座椅"),
    81: ("9173", "Reversing Camera", "倒车摄像"),
    87: ("2605", "Sound", "Symposer", "调音器"),
    88: ("9416", "Headlights Central", "中央前照灯"),
    95: ("9170", "Lane Change"),
    96: ("9170", "Lane Change"),
    165: ("ERA", "GLONASS"),
}

COMPLETION_MANUAL = REPO_ROOT / ".local" / "x431-re" / "2026-09-27-offline-completion" / "manual"

DISABLED = {
    "executionEnabled": False,
    "liveVerified": False,
    "writePayload": None,
}


def repo_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().upper()


def page_count(path: Path) -> int | None:
    try:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            return doc.page_count
    except Exception:
        try:
            from pypdf import PdfReader

            return len(PdfReader(str(path)).pages)
        except Exception:
            return None


def verify_source(path: Path | None, key: str) -> dict:
    base = BASELINE[key]
    row = {
        "id": key,
        "kind": key,
        "locator": base["basename"],
        "present": False,
        "status": "missing",
        "baselineMatch": False,
        "sha256": None,
        "pageCount": None,
        "bytes": None,
        "note": "optional original not supplied",
    }
    if path is None or not path.is_file():
        if path is not None:
            row["locator"] = path.name
            row["note"] = "path set but file absent"
        return row
    size = path.stat().st_size
    pages = page_count(path)
    digest = sha256_file(path)
    row.update(
        present=True,
        bytes=size,
        pageCount=pages,
        locator=path.name,
        status="locally_verified",
        sha256=digest,
        shaSource="hashed_this_run",
    )
    pages_ok = pages == base["pages"]
    row["baselineMatch"] = digest == base["sha256"] and pages_ok
    if not row["baselineMatch"]:
        row["note"] = "present but SHA256/pages do not match coordinator baseline; hash-specific facts withheld"
    else:
        row["note"] = "baseline SHA256 and page count matched (content hashed this run)"
    return row


def load_toc(path: Path | None, identity: dict) -> list:
    if path is None or not path.is_file():
        return []
    if identity.get("baselineMatch") and TOC_CACHE.is_file():
        cache = json.loads(TOC_CACHE.read_text(encoding="utf-8"))
        cached = cache.get(path.name)
        if cached is not None:
            return cached
    try:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            return doc.get_toc() or []
    except Exception:
        return []


def toc_hits(toc: list, tokens: tuple[str, ...], *, generation: str, source_id: str, basename: str, limit: int = 8) -> list[dict]:
    out = []
    if not tokens:
        return out
    for item in toc:
        if len(item) < 3:
            continue
        title = str(item[1])
        page = int(item[2])
        matched = [t for t in tokens if t and t.lower() in title.lower()]
        if not matched:
            continue
        out.append(
            {
                "generation": generation,
                "kind": "wiring_diagram" if source_id.startswith("wiring") else "workshop_toc",
                "sourceId": source_id,
                "locator": basename,
                "page1based": page,
                "tocTitle": title[:160],
                "matchedTokens": matched,
                "connectivity": "tentative_unverified_visual",
                "notADiagnosticAddress": True,
            }
        )
        if len(out) >= limit:
            break
    return out


def catalog_observed(catalog_path: Path) -> dict[int, dict]:
    found: dict[int, dict] = {}
    if not catalog_path.is_file():
        return found
    try:
        cat = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return found
    for p in cat.get("profiles") or []:
        spec = CATALOG_ADDR.get(p.get("id") or "")
        if not spec:
            continue
        if p.get("model") != "981":
            continue
        if p.get("txId") != spec["txId"] or p.get("rxId") != spec["rxId"]:
            continue
        ops = p.get("operations") or []
        if not any(op.get("evidenceStatus") == "observed-x431-capture" for op in ops):
            continue
        found[spec["ecuId"]] = {
            "txId": spec["txId"],
            "rxId": spec["rxId"],
            "source": "data/seed/diagnostics/catalog.v1.json",
            "catalogProfileId": p["id"],
            "notFromWiringPins": True,
        }
    return found


def factory_ops(generation: str, ecu_id: int, identities: dict[str, dict]) -> dict:
    block = {
        **DISABLED,
        "read": "unresolved",
        "clear": "unresolved",
        "coding": "unresolved",
        "note": "No generic charger/tool fact promoted. Hash-specific WM facts only.",
        "transportUnknown": True,
        "writeRecoveryUnknown": True,
    }
    m981 = identities.get("manual-981") or {}
    m982 = identities.get("manual-982") or {}
    if generation == "981" and m981.get("baselineMatch"):
        block.update(
            {
                "read": {
                    "status": "factory_procedure_context",
                    "wm": "033500",
                    "pages": [89, 90],
                    "locator": BASELINE["manual-981"]["basename"],
                    "chargerAmps": 40,
                    "tool": "PIWIS II 9818",
                    "connector": "cabin left fuse box below",
                    "notIndependentPermission": True,
                    "sourceReview": "coordinator visual pages 89-90",
                },
                "clear": {
                    "status": "factory_procedure_context",
                    "wm": "033500",
                    "pages": [89, 90],
                    "confirmationRequired": True,
                    "notIndependentPermission": True,
                },
                "note": "981 WM 033500 charger >=40 A is procedure context, not a protocol allowlist.",
            }
        )
    if generation == "982" and m982.get("baselineMatch"):
        block.update(
            {
                "read": {
                    "status": "factory_procedure_context",
                    "wm": "033500",
                    "pages": [60, 61],
                    "locator": BASELINE["manual-982"]["basename"],
                    "chargerAmps": 90,
                    "tool": "PIWIS 3 9900",
                    "textExtraction": "garbled; facts from coordinator visual",
                    "notIndependentPermission": True,
                    "applicability": "footer MY2017 982 codes; not all future 982",
                },
                "clear": {
                    "status": "factory_procedure_context",
                    "wm": "033500",
                    "pages": [60, 61],
                    "confirmationRequired": True,
                    "notIndependentPermission": True,
                    "textExtraction": "garbled; facts from coordinator visual",
                },
                "note": "982 WM 033500 charger >=90 A is procedure context, not a protocol allowlist.",
            }
        )
        if ecu_id == 9:
            block["coding"] = {
                "status": "factory_workflow_evidence",
                "wm": "903555",
                "pages": [3726, 3727],
                "locator": BASELINE["manual-982"]["basename"],
                "readBeforeReplacement": True,
                "writeAfterReplacement": True,
                "ignitionOffForPhysicalSwap": True,
                "componentProtectionPpnLogin": True,
                "automaticCoding": True,
                "independentRawPayload": False,
                "rollbackClaim": False,
                "textExtraction": "garbled; facts from coordinator visual",
            }
    return block


def scan_extract_dir(extract: Path) -> dict[str, list[str]]:
    by_gen: dict[str, list[str]] = {"981": [], "982": []}
    if not extract.is_dir():
        return by_gen
    for p in sorted(extract.iterdir()):
        if not p.is_file():
            continue
        name = p.name.lower()
        rel = repo_rel(p)
        if name.startswith("982_"):
            by_gen["982"].append(rel)
        elif name.startswith(("981_", "cayman_981", "cayman_ws", "pdk_")):
            by_gen["981"].append(rel)
    return by_gen


def excerpt_hits(files: list[str], ecu_id: int, generation: str) -> list[dict]:
    extra = {1: ("dme",), 2: ("pdk",), 5: ("psm",)}.get(ecu_id, ())
    keys = tuple(t.lower() for t in (WIRING_TOC_TOKENS.get(ecu_id) or ()) + extra)
    out = []
    for rel in files:
        low = rel.lower()
        hit = (ecu_id == 2 and "pdk_" in low) or any(k in low for k in keys if len(k) > 2)
        if not hit:
            continue
        out.append(
            {
                "generation": generation,
                "kind": "cached_excerpt",
                "sourceId": "obd-analysis-extract",
                "locator": rel,
                "originalStatus": "cached_excerpt_unverified_original",
                "notADiagnosticAddress": True,
            }
        )
    return out[:6]


def dtc_index_evidence(generation: str, ecu_id: int) -> list[dict]:
    path = DTC_981 if generation == "981" else DTC_982
    if not path.is_file() or ecu_id not in (1, 5, 9):
        return []
    return [
        {
            "generation": generation,
            "kind": "manual_index",
            "sourceId": f"dtc-manuals-{generation}",
            "locator": repo_rel(path),
            "note": "module-adjacent archive locator; not whole-procedure verified",
            "notADiagnosticAddress": True,
        }
    ]


def source_entry(**kwargs) -> dict:
    return kwargs


def build_source_manifest(
    identities: dict[str, dict],
    extract: Path,
    manual_root: Path | None,
) -> list[dict]:
    rows = [identities[k] for k in ("wiring-981", "wiring-982", "wiring-gt4cs", "manual-981", "manual-982", "manual-991")]
    if extract.is_dir():
        rows.append(
            source_entry(
                id="cached-extract",
                kind="workshop-excerpt",
                locator=repo_rel(extract),
                present=True,
                status="cached_excerpt_unverified_original",
                note="historical extract; original PDF not re-verified this run",
                fileCount=len(list(extract.glob("*"))),
            )
        )
    else:
        rows.append(
            source_entry(
                id="cached-extract",
                kind="workshop-excerpt",
                locator=None,
                present=False,
                status="missing",
                note="extract dir absent",
            )
        )
    report = DEFAULT_MANUAL_REPORT
    rows.append(
        source_entry(
            id="manual-report",
            kind="historical-audit",
            locator=repo_rel(report) if report.is_file() else None,
            present=report.is_file(),
            status="cached_excerpt_unverified_original" if report.is_file() else "missing",
            note="historical; not current original verification",
        )
    )
    for sid, p in (
        ("source-inventory", extract.parent / "source-inventory.json"),
        ("requirements", extract.parent / "requirements.json"),
        ("manual-data-needs", REPO_ROOT / "data" / "seed" / "obd" / "manual-data-needs.json"),
    ):
        rows.append(
            source_entry(
                id=sid,
                kind="referenced-absent",
                locator=repo_rel(p),
                present=p.is_file(),
                status="locally_verified" if p.is_file() else "missing",
                note="checked this run",
            )
        )
    rows.append(
        source_entry(
            id="dtc-codes",
            kind="global-manual-code-index",
            locator="data/seed/dtc/manuals/by-code/codes.json",
            present=DTC_CODES.is_file(),
            status="locally_verified" if DTC_CODES.is_file() else "missing",
            note="global index only; does not resolve per-ECU manualStatus",
        )
    )
    if manual_root is None:
        rows.append(
            source_entry(
                id="manual-root",
                kind="workshop-originals",
                locator=None,
                present=False,
                status="missing",
                note="optional --manual-root not passed",
            )
        )
    elif manual_root.is_dir():
        pdfs = sorted(p.name for p in manual_root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
        rows.append(
            source_entry(
                id="manual-root",
                kind="workshop-originals",
                locator=manual_root.name,
                present=True,
                status="locally_verified",
                note="top-level PDFs listed; facts still require baseline hash match",
                pdfNames=pdfs,
                pdfCount=len(pdfs),
            )
        )
    else:
        rows.append(
            source_entry(
                id="manual-root",
                kind="workshop-originals",
                locator=str(manual_root),
                present=False,
                status="missing",
                note="--manual-root not a directory",
            )
        )
    return rows


def render_pages(pdf: Path, pages: tuple[int, ...], dest: Path, prefix: str) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        import pymupdf
    except ImportError:
        return written
    if not pdf.is_file():
        return written
    try:
        doc = pymupdf.open(str(pdf))
    except Exception:
        return written
    try:
        for page in pages:
            if page < 1 or page > doc.page_count:
                continue
            pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(0.35, 0.35))
            out = dest / f"{prefix}-p{page}.png"
            pix.save(str(out))
            written.append(out.name)
    finally:
        doc.close()
    return written


def special_variant_block(identity: dict, toc: list, *, role: str, note: str) -> dict:
    locators = []
    for item in toc[:40]:
        if len(item) < 3:
            continue
        locators.append({"tocTitle": str(item[1])[:120], "page1based": int(item[2])})
    return {
        "role": role,
        "present": identity.get("present"),
        "status": identity.get("status"),
        "baselineMatch": identity.get("baselineMatch"),
        "locator": identity.get("locator"),
        "sha256": identity.get("sha256"),
        "pageCount": identity.get("pageCount"),
        "notProductionDefault": True,
        "note": note,
        "tocLocatorsSample": locators[:12],
        **DISABLED,
    }


def build_registry(
    *,
    coverage_path: Path = DEFAULT_COVERAGE,
    catalog_path: Path = DEFAULT_CATALOG,
    wiring_981: Path | None = DEFAULT_WIRING_981,
    wiring_982: Path | None = DEFAULT_WIRING_982,
    wiring_gt4: Path | None = DEFAULT_WIRING_GT4,
    manual_981: Path | None = DEFAULT_MANUAL_981,
    manual_982: Path | None = DEFAULT_MANUAL_982,
    manual_991: Path | None = DEFAULT_MANUAL_991,
    manual_root: Path | None = None,
    extract: Path = DEFAULT_EXTRACT,
    render_png: bool = False,
) -> dict:
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    groups_src = coverage.get("menu_ecus") or []
    extract_files = scan_extract_dir(extract)
    identities = {
        "wiring-981": verify_source(wiring_981, "wiring-981"),
        "wiring-982": verify_source(wiring_982, "wiring-982"),
        "wiring-gt4cs": verify_source(wiring_gt4, "wiring-gt4cs"),
        "manual-981": verify_source(manual_981, "manual-981"),
        "manual-982": verify_source(manual_982, "manual-982"),
        "manual-991": verify_source(manual_991, "manual-991"),
    }
    toc_w981 = load_toc(wiring_981, identities["wiring-981"])
    toc_w982 = load_toc(wiring_982, identities["wiring-982"])
    toc_gt4 = load_toc(wiring_gt4, identities["wiring-gt4cs"])
    toc_m982 = load_toc(manual_982, identities["manual-982"])
    toc_m991 = load_toc(manual_991, identities["manual-991"])
    observed = catalog_observed(catalog_path)
    if render_png:
        if wiring_981 and wiring_981.is_file():
            render_pages(wiring_981, (11, 12), LOCAL_MANUAL, "wire981")
        if wiring_982 and wiring_982.is_file():
            render_pages(wiring_982, (11, 12, 13), LOCAL_MANUAL, "wire982")
        if manual_981 and manual_981.is_file():
            render_pages(manual_981, (89, 90), LOCAL_MANUAL, "wm981-033500")
        if manual_982 and manual_982.is_file():
            render_pages(manual_982, (60, 61, 3726, 3727), LOCAL_MANUAL, "wm982")

    groups = []
    for ecu in groups_src:
        ecu_id = int(ecu["ecu_id"])
        tokens = WIRING_TOC_TOKENS.get(ecu_id) or ()
        wm_tokens = WM_TOC_TOKENS.get(ecu_id) or ()
        gen = {}
        for generation in ("981", "982"):
            evidence = []
            gaps = []
            if generation == "981":
                ident = identities["wiring-981"]
                toc = toc_w981
                src_id = "wiring-981"
                basename = ident.get("locator") or "981.pdf"
            else:
                ident = identities["wiring-982"]
                toc = toc_w982
                src_id = "wiring-982"
                basename = ident.get("locator") or "982.pdf"
            if ident.get("present"):
                hits = toc_hits(toc, tokens, generation=generation, source_id=src_id, basename=basename)
                evidence.extend(hits)
                visual = visual_sheet_hits(ecu_id, generation, identities)
                evidence.extend(visual)
                obd_path = obd_path_hits(ecu_id, generation, identities)
                evidence.extend(obd_path)
                wiring_status = "partial" if (hits or visual or obd_path) else "absent"
                if not hits and not visual and not obd_path:
                    gaps.append(f"no wiring TOC title hit in {basename}")
                elif not hits and visual:
                    gaps.append(f"TOC title miss in {basename}; coordinator visual sheet used")
            else:
                wiring_status = "absent"
                gaps.append(f"{generation} wiring original missing")
            if generation == "982" and identities["manual-982"].get("baselineMatch"):
                evidence.extend(
                    toc_hits(
                        toc_m982,
                        wm_tokens,
                        generation="982",
                        source_id="manual-982",
                        basename=BASELINE["manual-982"]["basename"],
                        limit=8,
                    )
                )
            elif generation == "981" and identities["manual-981"].get("present"):
                if not identities["manual-981"].get("baselineMatch"):
                    gaps.append("981 workshop PDF present but baseline hash/pages mismatch")
                elif ecu_id in (1, 9):
                    evidence.append(
                        {
                            "generation": "981",
                            "kind": "workshop_selected_pages",
                            "sourceId": "manual-981",
                            "locator": BASELINE["manual-981"]["basename"],
                            "page1based": 89,
                            "section": "WM 033500",
                            "note": "no PDF TOC; selected coordinator-reviewed pages; not full-manual completeness",
                            "notADiagnosticAddress": True,
                        }
                    )
            elif generation == "981":
                gaps.append("981 workshop original missing")
            elif not identities["manual-982"].get("present"):
                gaps.append("982 workshop original missing")
            evidence.extend(excerpt_hits(extract_files.get(generation) or [], ecu_id, generation))
            evidence.extend(dtc_index_evidence(generation, ecu_id))
            try:
                from scripts.diagnostics.manual_evidence import locators_for_ecu

                hpath = COMPLETION_MANUAL / ("heading-index.json" if generation == "981" else "heading-index.json")
                headings = []
                if generation == "981" and hpath.is_file():
                    headings = (json.loads(hpath.read_text(encoding="utf-8")).get("headings") or [])
                wcache = COMPLETION_MANUAL / f"wiring-pages-{'wiring-981' if generation == '981' else 'wiring-982'}.json"
                wpages = []
                if wcache.is_file():
                    blob = json.loads(wcache.read_text(encoding="utf-8"))
                    if blob.get("sourceSha256") == ident.get("sha256"):
                        from scripts.diagnostics.manual_evidence import overlay_wiring_pages

                        wpages = overlay_wiring_pages(blob.get("pages") or [], src_id, ident)
                extra = locators_for_ecu(
                    ecu_id,
                    headings,
                    wpages,
                    generation,
                    "manual-981" if generation == "981" else "manual-982",
                    src_id,
                )
                evidence.extend(extra)
            except Exception:
                pass
            addr = None
            provenance = "unobserved"
            if generation == "981" and ecu_id in observed:
                addr = observed[ecu_id]
                provenance = "observed"
            kinds = {e.get("kind") for e in evidence}
            module_kinds = kinds & {
                "wiring_diagram",
                "workshop_toc",
                "workshop_selected_pages",
                "workshop_heading",
                "wiring_caption",
                "cached_excerpt",
                "manual_index",
                "visual_sheet",
                "obd_gateway_pin_path",
            }
            if "workshop_toc" in kinds or "workshop_selected_pages" in kinds or "workshop_heading" in kinds:
                manual_status = "selected_original_locator"
            elif "cached_excerpt" in kinds or "manual_index" in kinds:
                manual_status = "cached_excerpt_or_archive_locator"
            else:
                manual_status = "absent"
            if not module_kinds and generation == "982":
                gaps.append("no module-specific 982 workshop/wiring locator")
            gen[generation] = {
                "wiringStatus": wiring_status,
                "manualStatus": manual_status,
                "addressProvenance": provenance,
                "diagnosticAddress": addr,
                "factoryOps": factory_ops(generation, ecu_id, identities),
                "evidence": evidence,
                "gaps": gaps,
                **DISABLED,
            }
        group_gaps = [
            "menu group is not a physical-fit claim",
            "wiring pins / buses / diagnostic CAN addresses are separate facts",
        ]
        if ecu_id not in observed:
            group_gaps.append("no captured diagnostic address in catalog")
        groups.append(
            {
                "ecuId": ecu_id,
                "label": ecu.get("label"),
                "dsnModules": list(ecu.get("dsn_modules") or []),
                "variantCount": ecu.get("variant_count"),
                "fittedClaim": False,
                "generations": gen,
                "gaps": group_gaps,
                **DISABLED,
            }
        )

    global_index = None
    if DTC_CODES.is_file():
        stats = json.loads(DTC_CODES.read_text(encoding="utf-8")).get("stats") or {}
        global_index = {
            "locator": "data/seed/dtc/manuals/by-code/codes.json",
            "codeCount981": (stats.get("model_coverage") or {}).get("981"),
            "codeCount982": (stats.get("model_coverage") or {}).get("982"),
            "note": "global index; file existence does not verify a module procedure",
        }

    return {
        "schemaVersion": 1,
        "task": "offline_wiring_manual_ecu_registry",
        "canonicalSystemId": "menu_ecu_id",
        "fittedClaim": False,
        **DISABLED,
        "observedAddressPairsOnly": [
            {"ecuId": 1, "txId": "7E0", "rxId": "7E8", "generation": "981"},
            {"ecuId": 9, "txId": "710", "rxId": "77A", "generation": "981"},
        ],
        "catalogPresent": catalog_path.is_file(),
        "catalogLocator": "data/seed/diagnostics/catalog.v1.json" if catalog_path.is_file() else None,
        "coverageLocator": "data/seed/diagnostics/coverage-981-982.v1.json",
        "groupCount": len(groups),
        "globalManualCodeIndex": global_index,
        "specialVariants": {
            "gt4Clubsport": special_variant_block(
                identities["wiring-gt4cs"],
                toc_gt4,
                role="motorsport_wiring_reference",
                note="Clubsport wiring; not production 981/982 default",
            ),
            "factory991": special_variant_block(
                identities["manual-991"],
                [t for t in toc_m991 if len(t) > 1 and ("033500" in str(t[1]) or "903555" in str(t[1]) or "GATEWAY" in str(t[1]).upper())][:15],
                role="method_reference_only",
                note="991 factory manual; no 991 target groups; no auto 981 fallback",
            ),
        },
        "sources": build_source_manifest(identities, extract, manual_root),
        "groups": groups,
    }


def write_registry(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="ascii")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m scripts.diagnostics.workshop_registry",
        description="Build 35-group wiring/manual registry. Read-only. No pin-to-address inference.",
    )
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--coverage", default=str(DEFAULT_COVERAGE))
    p.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    p.add_argument("--wiring-981", default=str(DEFAULT_WIRING_981))
    p.add_argument("--wiring-982", default=str(DEFAULT_WIRING_982))
    p.add_argument("--wiring-gt4", default=str(DEFAULT_WIRING_GT4))
    p.add_argument("--manual-981", default=str(DEFAULT_MANUAL_981))
    p.add_argument("--manual-982", default=str(DEFAULT_MANUAL_982))
    p.add_argument("--manual-991", default=str(DEFAULT_MANUAL_991))
    p.add_argument("--manual-root", default="")
    p.add_argument("--extract", default=str(DEFAULT_EXTRACT))
    p.add_argument("--render-png", action="store_true")
    args = p.parse_args(argv)
    data = build_registry(
        coverage_path=Path(args.coverage),
        catalog_path=Path(args.catalog),
        wiring_981=Path(args.wiring_981) if args.wiring_981 else None,
        wiring_982=Path(args.wiring_982) if args.wiring_982 else None,
        wiring_gt4=Path(args.wiring_gt4) if args.wiring_gt4 else None,
        manual_981=Path(args.manual_981) if args.manual_981 else None,
        manual_982=Path(args.manual_982) if args.manual_982 else None,
        manual_991=Path(args.manual_991) if args.manual_991 else None,
        manual_root=Path(args.manual_root) if args.manual_root else None,
        extract=Path(args.extract),
        render_png=args.render_png,
    )
    out = Path(args.out)
    write_registry(data, out)
    print("out", repo_rel(out))
    print("groups", data["groupCount"])
    print("sources", len(data["sources"]))
    print("missingSources", sum(1 for s in data["sources"] if s.get("status") == "missing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
