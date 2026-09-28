"""Target-only workshop DTC / heading / wiring-caption index. No ECU write. No pin->address."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from scripts.diagnostics.workshop_registry import (
    BASELINE,
    DEFAULT_COVERAGE,
    DEFAULT_EXTRACT,
    DEFAULT_MANUAL_981,
    DEFAULT_MANUAL_982,
    DEFAULT_MANUAL_991,
    DEFAULT_WIRING_981,
    DEFAULT_WIRING_982,
    DEFAULT_WIRING_GT4,
    bind_obd_gateway_facts,
    repo_rel,
    sha256_file,
    verify_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPLETION = REPO_ROOT / ".local" / "x431-re" / "2026-09-27-offline-completion" / "manual"
OUTPUT_ROOT = COMPLETION
PARSER_CACHE_VERSION = 4
DEFAULT_SEED = REPO_ROOT / "data" / "seed" / "diagnostics" / "manual-evidence.v1.json"
VARIANTS = REPO_ROOT / ".local" / "x431-re" / "2026-09-27-981982" / "expansion" / "variants.jsonl"
DTC_CODES = REPO_ROOT / "data" / "seed" / "dtc" / "manuals" / "by-code" / "codes.json"
Z_ROOT = Path("Z:/porsche/981")

WATERMARK_SIZE = 22.0
# Letter + 4 hex, or letter + 4 hex + 2 symptom bytes. Length 6 is invalid.
CODE_RE = re.compile(r"(?<![0-9A-Z])([PBCU][0-9A-F]{4}(?:[0-9A-F]{2})?)(?![0-9A-F])")
CODE_LINE_RE = re.compile(r"^[PBCU][0-9A-F]{4}(?:[0-9A-F]{2})?$")
WM_RE = re.compile(r"\bWM\s*([0-9]{6})\b", re.I)
ZH_CTRL_RE = re.compile(r"诊断\s*([A-Za-z0-9/\- ]{0,40}?)\s*控制")
EN_DIAG_RE = re.compile(r"Diagnostic information\s*[-–—]\s*(.+?control unit)", re.I)
CROSS_REF_RE = re.compile(r"None of the following faults stored", re.I)
TOC_MARK = "Porsche DTC Diagnostic Information"
YEAR_RE = re.compile(r"\b(20[0-2][0-9])\b")

ECU_PHRASE = {
    1: ("DME", "DFI", "发动机"),
    2: ("PDK", "Getriebe", "变速"),
    4: ("Airbag", "气囊"),
    5: ("PSM",),
    7: ("Air-Conditioning", "Climate", "空调", "气候"),
    8: ("Instrument Cluster", "仪表板", "组合仪表", "9025"),
    9: ("Gateway", "网关", "9035", "CAN Communication"),
    18: ("Park Assist", "驻车辅助", "PDC"),
    19: ("PASM", "PADM"),
    29: ("Stopwatch", "计时器", "秒表", "9030"),
    30: ("Driver Door", "驾驶员侧车门"),
    31: ("Passenger Door", "乘客侧车门"),
    32: ("Front-End Electronics", "前端电子"),
    35: ("Rear-End Electronics", "后端电子"),
    39: ("Convertible Top", "活顶", "车顶系统", "CABRIO"),
    47: ("Headlights Left", "左侧前照灯", "左自适应前照灯"),
    48: ("Headlights Right", "右侧前照灯", "右自适应前照灯"),
    50: ("Parking Brake", "驻车制动", "停车制动", "EPB"),
    54: ("Steering Wheel Electronics", "转向盘电子", "多功能方向盘"),
    55: ("Tire", "TPM", "轮胎压力"),
    64: ("Adaptive Cruise", "ACC"),
    65: ("External Amplifier", "BOSE", "ASK", "BURMESTER", "扬声器"),
    68: ("Selector Lever", "选档杆"),
    69: ("Power Steering", "电子转向", "助力转向"),
    70: ("PCM", "Communication-Management", "收音机"),
    74: ("Front Camera", "前部摄像", "前摄像头"),
    75: ("电视调谐", "TV tuner", "Television"),
    76: ("Seat Adjust Driver", "左侧座椅", "驾驶员侧座椅"),
    78: ("Seat Adjust Passenger", "右侧座椅", "乘客侧座椅"),
    81: ("Reversing Camera", "倒车摄像"),
    87: ("Sound", "Symposer", "调音器", "发动机声音"),
    88: ("Headlights Central", "中央前照灯"),
    95: ("Lane Change", "车道变更", "车道变换"),
    96: ("Lane Change", "车道变更", "车道变换"),
    165: ("ERA", "GLONASS"),
}

# 982 workshop Diagnostic Information chapters (inclusive start, exclusive end page).
WM982_DIAG_CHAPTERS = (
    (1, "2470", 4225, 4921, "DME (DFI)"),
    (87, "2605", 4921, 4936, "Sound Symposer"),
    (64, "2785", 4936, 4944, "ACC"),
    (68, "3708", 4944, 4955, "Selector Lever"),
    (2, "3730", 4955, 5210, "PDK"),
    (19, "4316", 5347, 5433, "PASM"),
    (55, "4434", 5433, 5477, "TPM"),
    (5, "4562", 5477, 5523, "PSM"),
    (50, "4662", 5523, 5574, "Parking Brake"),
    (69, "4890", 5574, 5616, "Power Steering"),
    (30, "5773", 5616, 5654, "Driver Door"),
    (31, "5773", 5654, 5684, "Passenger Door"),
    (35, "5789", 5684, 5838, "Rear-End Electronics"),
    (39, "6196", 5838, 5870, "Convertible Top"),
    (4, "6953", 5870, 6552, "Airbag"),
    (76, "7293", 6552, 6611, "Seat Adjust Driver"),
    (78, "7293", 6611, 6671, "Seat Adjust Passenger"),
    (7, "8720", 6671, 6788, "Air-Conditioning"),
    (8, "9025", 6788, 6827, "Instrument Cluster"),
    (29, "9030", 6827, 6838, "Stopwatch"),
    (9, "9035", 6838, 6865, "Gateway"),
    (65, "9144", 6865, 7035, "External Amplifier"),
    (54, "9162", 7035, 7095, "Steering Wheel Electronics"),
    (95, "9170", 7095, 7136, "Lane Change Assist"),
    (81, "9173", 7136, 7190, "Reversing Camera"),
    (18, "9174", 7190, 7249, "Park Assist"),
    (88, "9416", 7249, 7372, "Headlights Central"),
    (32, "9449", 7372, 7527, "Front-End Electronics"),
    (47, "9457", 7527, 7627, "Headlights Left"),
    (48, "9457", 7627, 7703, "Headlights Right"),
    (74, "9638", 7703, 7798, "Front Camera"),
    (9, "9700", 7798, 7916, "CAN Communication"),
)

# Compound phrases / sheet ids. Single 左 or 前照灯 is not enough.
WIRING_CAPTION_TOKENS: dict[int, tuple[str, ...]] = {
    1: ("DME", "61C", "68B", "78B"),
    2: ("PDK", "50B"),
    4: ("气囊",),
    5: ("PSM",),
    7: ("自动空调", "气候箱"),
    8: ("仪表板", "组合仪表"),
    9: ("网关", "CAN 拓扑", "07_1", "07_2", "07A"),
    18: ("驻车辅助",),
    19: ("PASM",),
    29: ("秒表", "计时器"),
    30: ("驾驶员侧车门",),
    31: ("乘客侧车门",),
    32: ("前端电子设备",),
    35: ("后端电子设备",),
    39: ("电动活顶", "15H", "CABRIO"),
    47: ("40A", "左侧前照灯"),
    48: ("40B", "右侧前照灯"),
    50: ("停车制动", "驻车制动", "EPB", "57A"),
    54: ("多功能方向盘",),
    55: ("轮胎压力", "TPM"),
    64: ("ACC", "自适应巡航"),
    65: ("BOSE", "ASK", "BURMESTER"),
    68: ("选档杆",),
    69: ("电子转向",),
    70: ("PCM",),
    74: ("前部摄像机", "前部摄像"),
    75: ("电视调谐",),
    76: ("座椅加热线束 左", "腰部支撑线束，左侧", "左侧座椅"),
    78: ("腰部支撑线束，右侧", "右侧座椅"),
    81: ("倒车摄像",),
    87: ("发动机声音", "Symposer", "59D"),
    88: ("中央前照灯",),
    95: ("车道变更辅助", "车道变换辅助"),
    96: ("车道变更辅助", "车道变换辅助"),
    165: ("ERA-GLONASS", "ERA", "GLONASS"),
}

# Hash-bound coordinator visual sheet titles. TOC on 982 62/64 is axle harness, not the ECU.
WIRE_VISUAL_PRINTED = {
    ("wiring-982", 62): {"printedCaption": "PSM", "sheetId": "51", "ecuId": 5},
    ("wiring-982", 64): {"printedCaption": "EPB", "sheetId": "57A", "ecuId": 50},
    ("wiring-981", 11): {"printedCaption": "网关", "sheetId": "07"},
    ("wiring-982", 12): {"printedCaption": "网关", "sheetId": "07_1"},
}
WIRE982_VISUAL_PRINTED = {p: spec for (sid, p), spec in WIRE_VISUAL_PRINTED.items() if sid == "wiring-982"}

DISABLED = {"executionEnabled": False, "liveVerified": False, "writePayload": None}


def valid_dtc_token(raw: str) -> bool:
    u = raw.upper()
    return bool(CODE_LINE_RE.fullmatch(u)) and len(u) in (5, 7)


def base_code(raw: str) -> str | None:
    if not valid_dtc_token(raw):
        return None
    return raw.upper()[:5]


def codes_in_header_zone(text: str, *, ignore_cross_ref: bool) -> list[str]:
    work = text
    if ignore_cross_ref:
        work = CROSS_REF_RE.split(work, maxsplit=1)[0]
    found: list[str] = []
    seen: set[str] = set()
    for m in CODE_RE.finditer(work):
        raw = m.group(1).upper()
        if not valid_dtc_token(raw) or raw in seen:
            continue
        seen.add(raw)
        found.append(raw)
    return found


def recover_span(text: str, font: str, size: float) -> tuple[str, list[str]]:
    flags: list[str] = []
    if not text:
        return "", flags
    if size >= WATERMARK_SIZE and "SimSun" in (font or ""):
        return "", flags
    blob = text + font
    if "583622708" in blob or "中国汽车技师俱乐部" in text:
        return "", flags
    out: list[str] = []
    font = font or ""
    for ch in text:
        o = ord(ch)
        if o < 128:
            out.append(ch)
            continue
        if font.startswith("ArialUnicodeMS"):
            out.append(chr(o + 0x2D71))
            continue
        if "SimSun" in font:
            if 0x4E00 <= o <= 0x9FFF:
                out.append(ch)
                continue
            cand_o = o + 0x4A2C
            if 0x4E00 <= cand_o <= 0x9FFF:
                out.append(chr(cand_o))
            else:
                out.append("\uFFFD")
                flags.append("simsun_unmapped")
            continue
        if 0x4E00 <= o <= 0x9FFF or o in (0x3001, 0x3002, 0xFF0C):
            out.append(ch)
            continue
        out.append("\uFFFD")
        flags.append(f"unknown_font:{font[:24]}")
    return "".join(out), flags


def recover_page(page, *, sort_plain: bool = False) -> tuple[str, bool, list[str]]:
    flags: list[str] = []
    if sort_plain:
        plain = page.get_text(sort=True)
        return plain, False, flags
    parts: list[str] = []
    d = page.get_text("dict")
    for b in d.get("blocks") or []:
        if b.get("type") != 0:
            continue
        for line in b.get("lines") or []:
            bits = []
            for s in line.get("spans") or []:
                t, f = recover_span(s.get("text") or "", s.get("font") or "", float(s.get("size") or 0))
                flags.extend(f)
                bits.append(t)
            parts.append("".join(bits))
    return "\n".join(parts), bool(flags), sorted(set(flags))


def is_toc_page(text: str) -> bool:
    return TOC_MARK in text and text.count("P0") + text.count("U0") >= 8 and "Document" in text


def parse_981_header(text: str) -> dict | None:
    if is_toc_page(text):
        return None
    m = ZH_CTRL_RE.search(text.replace("\n", " "))
    if not m:
        return None
    phrase = (m.group(1) or "").strip()
    ecu = phrase_to_ecu(phrase)
    head = text[:900]
    raws = codes_in_header_zone(head, ignore_cross_ref=True)
    if not raws:
        raws = codes_in_header_zone(text.split("故障查找")[0][:1200], ignore_cross_ref=True)
    return {"kind": "zh_ctrl", "phrase": phrase, "ecuId": ecu, "rawCodes": raws, "heading": m.group(0)[:80]}


def parse_982_header(text: str) -> dict | None:
    if is_toc_page(text):
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    raw = None
    for ln in lines[:12]:
        if CODE_LINE_RE.fullmatch(ln):
            raw = ln.upper()
            break
    dm = EN_DIAG_RE.search(text[:800])
    if not raw and not dm:
        return None
    if dm is None and raw:
        # code-only top line still a header when Diagnostic information follows later on page
        if "Diagnostic information" not in text[:1200] and "Diagnosis information" not in text[:1200]:
            return None
    phrase = (dm.group(1) if dm else "")[:80]
    ecu = phrase_to_ecu(phrase + " " + (raw or ""))
    raws = [raw] if raw else []
    extra = codes_in_header_zone("\n".join(lines[:15]), ignore_cross_ref=True)
    for c in extra:
        if c not in raws:
            raws.append(c)
    heading = f"{raws[0] if raws else ''} {phrase}".strip()
    return {"kind": "en_diag", "phrase": phrase, "ecuId": ecu, "rawCodes": raws[:12], "heading": heading[:160]}


def phrase_to_ecu(phrase: str) -> int | None:
    p = phrase.lower()
    best = None
    for ecu, toks in ECU_PHRASE.items():
        if any(t.lower() in p for t in toks if len(t) >= 3):
            best = ecu
            break
    return best


def extract_needs(text: str, uncertain: bool) -> list[dict]:
    starts = [
        ("diagnosticConditions", ("诊断条件", "诊断条", "Diagnostic conditions", "Diagnosis conditions")),
        ("settingConditions", ("故障设置条件", "故障设", "Fault setting condition")),
        ("monitoredComponents", ("监控的部件", "监控的部", "Monitored component", "Monitored")),
        ("possibleCauses", ("Possible fault causes",)),
    ]
    found: list[tuple[int, str, str]] = []
    for key, labels in starts:
        best = -1
        lab = None
        for lb in labels:
            i = text.find(lb)
            if i >= 0 and (best < 0 or i < best):
                best = i
                lab = lb
        if best >= 0 and lab:
            found.append((best, key, lab))
    found.sort()
    out = []
    for i, (idx, key, lab) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else min(len(text), idx + 800)
        for stopper in ("故障查找", "Function", "功能/Function", "功能"):
            j = text.find(stopper, idx + len(lab))
            if 0 <= j < end:
                end = j
        body = text[idx:end]
        items = [ln.strip(" IJK-\t") for ln in body.splitlines() if len(ln.strip()) > 2][:12]
        if key == "settingConditions" and any("监控" in it for it in items):
            items = [it for it in items if "监控" not in it]
        out.append(
            {
                "kind": key,
                "sourceLabel": lab,
                "items": items,
                "confidence": "extracted-candidate",
                "uncertainGlyph": uncertain,
                "notAPid": True,
                "notReplacementAdvice": True,
            }
        )
    return out


def group_pages(pages: list[dict], *, generation: str, source_id: str, parser) -> tuple[list[dict], list[dict]]:
    """pages: {page1based, text, uncertainGlyph, bodyModel}."""
    groups: list[dict] = []
    toc_mentions: list[dict] = []
    current = None

    def close():
        nonlocal current
        if current:
            groups.append(current)
            current = None

    for row in pages:
        text = row["text"]
        page = row["page1based"]
        if is_toc_page(text):
            close()
            for raw in codes_in_header_zone(text, ignore_cross_ref=False):
                toc_mentions.append(
                    {
                        "generation": generation,
                        "rawCode": raw,
                        "baseCode": base_code(raw),
                        "sourceId": source_id,
                        "pages": [page],
                        "bodyEvidenceStatus": "toc_only",
                        "heading": TOC_MARK,
                        "ecuId": None,
                    }
                )
            continue
        hdr = parser(text)
        if hdr and hdr.get("rawCodes"):
            close()
            current = {
                "generation": generation,
                "sourceId": source_id,
                "ecuId": hdr.get("ecuId"),
                "heading": hdr.get("heading"),
                "phrase": hdr.get("phrase"),
                "rawCodes": list(hdr["rawCodes"]),
                "pages": [page],
                "uncertainGlyph": bool(row.get("uncertainGlyph")),
                "bodyModel": row.get("bodyModel") or "boxster",
                "text": text[:4000],
            }
            continue
        if hdr and not hdr.get("rawCodes"):
            close()
            continue
        if current:
            current["pages"].append(page)
            current["uncertainGlyph"] = current["uncertainGlyph"] or bool(row.get("uncertainGlyph"))
            current["text"] = (current.get("text") or "") + "\n" + text[:2000]
    close()
    return groups, toc_mentions


def groups_to_entries(groups: list[dict], toc_mentions: list[dict]) -> list[dict]:
    entries = []
    for g in groups:
        needs = extract_needs(g.get("text") or "", bool(g.get("uncertainGlyph")))
        status = "body_header"
        if g.get("uncertainGlyph"):
            status = "body_header_uncertain_glyph"
        for raw in g["rawCodes"]:
            bc = base_code(raw)
            entries.append(
                {
                    "generation": g["generation"],
                    "ecuId": g.get("ecuId"),
                    "rawCode": raw,
                    "baseCode": bc,
                    "sourceId": g["sourceId"],
                    "pages": g["pages"],
                    "heading": g.get("heading"),
                    "bodyEvidenceStatus": status,
                    "bodyModel": g.get("bodyModel"),
                    "needs": needs,
                    "procedureVerified": False,
                    "uncertainGlyph": bool(g.get("uncertainGlyph")),
                    **DISABLED,
                }
            )
    owned = {(e["generation"], e["rawCode"], e["sourceId"]) for e in entries}
    for t in toc_mentions:
        key = (t["generation"], t["rawCode"], t["sourceId"])
        if key in owned:
            continue
        entries.append(
            {
                **t,
                "needs": [],
                "procedureVerified": False,
                "uncertainGlyph": False,
                **DISABLED,
            }
        )
    return entries


def cache_path(name: str) -> Path:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return OUTPUT_ROOT / name


def cache_fresh(prev: dict | None, source_hash: str | None) -> bool:
    if not prev or not source_hash:
        return False
    return prev.get("sourceSha256") == source_hash and prev.get("parserCacheVersion") == PARSER_CACHE_VERSION


def heading_scan_981(pdf: Path, source_hash: str, *, body_model: str, source_id: str) -> dict:
    cache = cache_path(f"heading-index-{source_id}.json")
    if cache.is_file():
        prev = json.loads(cache.read_text(encoding="utf-8"))
        if cache_fresh(prev, source_hash):
            return prev
    import pymupdf

    locators = []
    with pymupdf.open(str(pdf)) as doc:
        n = doc.page_count
        for i in range(n):
            page = doc[i]
            raw = page.get_text()
            if not raw:
                continue
            interesting = (
                "WM " in raw
                or "P0" in raw
                or "Diagnostic" in raw
                or "2470" in raw
                or i < 120
                or i >= n - 20
                or i >= 3600
            )
            if not interesting:
                continue
            rec, unc, _flags = recover_page(page)
            blob = rec if rec.strip() else raw
            wms = WM_RE.findall(blob) or WM_RE.findall(raw)
            years = YEAR_RE.findall(blob)[:3]
            zh = ZH_CTRL_RE.findall(blob.replace("\n", " "))
            title = None
            for line in blob.splitlines():
                s = line.strip()
                if len(s) >= 8 and (s.startswith("WM") or "诊断" in s[:8] or s.startswith("Diagnostic")):
                    title = s[:160]
                    break
            if not (wms or zh or title):
                continue
            locators.append(
                {
                    "page1based": i + 1,
                    "title": (title or (zh[0] if zh else "") or (f"WM {wms[0]}" if wms else ""))[:160],
                    "wmCodes": wms[:6],
                    "ctrlPhrases": zh[:4],
                    "yearHints": years,
                    "bodyModel": body_model,
                    "evidenceClass": "heading_locator",
                    "uncertainGlyph": unc,
                }
            )
    out = {
        "sourceId": source_id,
        "sourceSha256": source_hash,
        "parserCacheVersion": PARSER_CACHE_VERSION,
        "locator": pdf.name,
        "pageCount": n,
        "headingCount": len(locators),
        "headings": locators,
    }
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def extract_982_chapters(pdf: Path, *, hash_ok: bool, source_hash: str | None = None) -> tuple[list[dict], list[dict]]:
    if not hash_ok or not pdf.is_file():
        return [], []
    cache = cache_path("dtc-982-groups.json")
    if source_hash and cache.is_file():
        prev = json.loads(cache.read_text(encoding="utf-8"))
        if cache_fresh(prev, source_hash):
            return prev.get("groups") or [], prev.get("toc") or []
    import pymupdf

    all_groups: list[dict] = []
    all_toc: list[dict] = []
    with pymupdf.open(str(pdf)) as doc:
        for ecu, wm, start, end, _label in WM982_DIAG_CHAPTERS:
            pages = []
            last = min(end - 1, doc.page_count)
            for i in range(max(1, start), last + 1):
                rec, unc, _f = recover_page(doc[i - 1], sort_plain=True)
                pages.append({"page1based": i, "text": rec, "uncertainGlyph": unc, "bodyModel": "boxster"})
            g, t = group_pages(pages, generation="982", source_id="manual-982", parser=parse_982_header)
            for row in g:
                if row.get("ecuId") is None:
                    row["ecuId"] = ecu
                row["wm"] = wm
            all_groups.extend(g)
            all_toc.extend(t)
    if source_hash:
        cache.write_text(json.dumps({"sourceSha256": source_hash, "parserCacheVersion": PARSER_CACHE_VERSION, "groups": all_groups, "toc": all_toc}, ensure_ascii=False), encoding="utf-8")
    return all_groups, all_toc


def extract_dtc_range(
    pdf: Path,
    start: int,
    end_excl: int,
    *,
    generation: str,
    source_id: str,
    parser,
    sort_plain: bool,
    body_model: str,
    hash_ok: bool,
    ascii_prefilter: bool = False,
) -> tuple[list[dict], list[dict]]:
    if not hash_ok or not pdf.is_file():
        return [], []
    import pymupdf

    pages = []
    with pymupdf.open(str(pdf)) as doc:
        last = min(end_excl - 1, doc.page_count)
        for i in range(max(1, start), last + 1):
            page = doc[i - 1]
            if ascii_prefilter and not sort_plain:
                raw = page.get_text()
                if not CODE_RE.search(raw) and "Diagnostic information" not in raw:
                    continue
            rec, unc, _f = recover_page(page, sort_plain=sort_plain)
            pages.append({"page1based": i, "text": rec, "uncertainGlyph": unc, "bodyModel": body_model})
    return group_pages(pages, generation=generation, source_id=source_id, parser=parser)


def recover_clip(page, clip) -> str:
    d = page.get_text("dict", clip=clip)
    parts = []
    for b in d.get("blocks") or []:
        if b.get("type") != 0:
            continue
        for line in b.get("lines") or []:
            bits = []
            for s in line.get("spans") or []:
                t, _f = recover_span(s.get("text") or "", s.get("font") or "", float(s.get("size") or 0))
                bits.append(t)
            parts.append("".join(bits))
    return "\n".join(parts)


def printed_caption_from_page(page, *, source_id: str, page1based: int) -> tuple[str | None, str]:
    r = page.rect
    import pymupdf

    right = recover_clip(page, pymupdf.Rect(r.width * 0.72, 40, r.width, r.height - 20))
    blob = re.sub(r"[ \t]+", " ", right)
    spec = WIRE_VISUAL_PRINTED.get((source_id, page1based))
    if spec:
        return spec["printedCaption"], "page_title_block_hash_bound_visual"
    names = (
        "PSM", "EPB", "PDK", "DME", "PASM", "PADM", "网关", "气囊", "BOSE", "PCM",
        "TPM", "ACC", "ERA-GLONASS", "前部摄像机", "倒车摄像", "电视调谐", "选档杆",
        "驾驶员侧车门", "乘客侧车门", "前端电子设备", "后端电子设备", "电子转向",
        "组合仪表", "驻车辅助", "发动机声音", "中央前照灯", "左侧前照灯", "右侧前照灯",
    )
    hit = None
    for n in names:
        if n in blob:
            hit = n
            break
    if hit:
        return hit, "page_right_region_candidate"
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip() and not ln.strip().isdigit()]
    for ln in lines:
        if re.match(r"^\d{2}[A-Z_]?", ln) or re.match(r"^\d{2}_", ln):
            return ln[:80], "page_right_region_candidate"
    return None, "unreadable_title_block"


def wiring_page_index(pdf: Path, identity: dict, source_id: str) -> list[dict]:
    if not identity.get("baselineMatch") or not pdf.is_file():
        return []
    dest = cache_path(f"wiring-pages-{source_id}.json")
    if dest.is_file() and identity.get("sha256"):
        prev = json.loads(dest.read_text(encoding="utf-8"))
        if cache_fresh(prev, identity.get("sha256")):
            return overlay_wiring_pages(prev.get("pages") or [], source_id, identity)
    import pymupdf

    rows = []
    with pymupdf.open(str(pdf)) as doc:
        toc = {int(item[2]): str(item[1])[:160] for item in (doc.get_toc() or []) if len(item) >= 3}
        for i, page in enumerate(doc):
            rec, unc, _f = recover_page(page)
            printed, cap_src = printed_caption_from_page(page, source_id=source_id, page1based=i + 1)
            spec = WIRE_VISUAL_PRINTED.get((source_id, i + 1))
            visual = None
            if spec:
                printed = spec["printedCaption"]
                cap_src = "page_title_block_hash_bound_visual"
                visual = "coordinator_visual_sheet"
            body_hint = None
            if "诊断插头插座" in rec and "网关控制单元" in rec and ("诊断 CAN" in rec or "诊断CAN" in rec):
                body_hint = "diagnostic_plug"
            if ("CAN 驱动" in rec or "驱动 CAN" in rec) and "底盘 CAN" in rec and "舒适性 CAN" in rec:
                body_hint = (body_hint + "+named_can_buses") if body_hint else "named_can_buses"
            rows.append(
                {
                    "sourceId": source_id,
                    "page1based": i + 1,
                    "printedCaption": printed,
                    "captionSource": cap_src if printed else "unreadable",
                    "tocTitle": toc.get(i + 1),
                    "firstLines": [ln.strip() for ln in rec.splitlines() if ln.strip()][:6],
                    "bodyTopologyClass": body_hint,
                    "uncertainGlyph": unc,
                    "connectivity": "pending_visual" if visual is None else "named_path_plus_coordinator_visual",
                    "visualVerified": visual == "coordinator_visual_sheet",
                    "pinPairExtracted": False,
                    "notADiagnosticAddress": True,
                }
            )
    dest.write_text(
        json.dumps({"sourceSha256": identity.get("sha256"), "parserCacheVersion": PARSER_CACHE_VERSION, "pages": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    return overlay_wiring_pages(rows, source_id, identity)


def apply_caption_honesty(pages: list[dict], source_id: str) -> list[dict]:
    out = []
    for raw in pages:
        w = dict(raw)
        spec = WIRE_VISUAL_PRINTED.get((source_id, w.get("page1based")))
        if spec:
            w["printedCaption"] = spec["printedCaption"]
            w["captionSource"] = "page_title_block_hash_bound_visual"
            w["visualVerified"] = True
            w["candidate"] = False
            w["sheetId"] = spec["sheetId"]
        elif w.get("printedCaption"):
            w["captionSource"] = "page_right_region_candidate"
            w["visualVerified"] = False
            w["candidate"] = True
        else:
            w["visualVerified"] = False
            w["candidate"] = True
        out.append(w)
    return out


def overlay_wiring_pages(pages: list[dict], source_id: str, identity: dict | None) -> list[dict]:
    rows = apply_caption_honesty(pages, source_id)
    bound = bind_obd_gateway_facts(source_id, identity)
    if not bound:
        return rows
    for w in rows:
        if w.get("page1based") != bound["pdfPage"]:
            continue
        w["pinPairExtracted"] = True
        w["sourcePinAssignmentsVerified"] = True
        w["liveContinuityVerified"] = False
        w["printedModelYear"] = bound["printedModelYear"]
        w["pinPairs"] = bound["pinPairs"]
        w["notADiagnosticAddress"] = True
    return rows


def caption_matches_ecu(ecu_id: int, w: dict) -> bool:
    tokens = WIRING_CAPTION_TOKENS.get(ecu_id) or ()
    blob = " ".join(
        [
            w.get("printedCaption") or "",
            w.get("tocTitle") or "",
        ]
    )
    if not any(t and t.lower() in blob.lower() for t in tokens):
        return False
    if ecu_id == 47:
        return "40A" in blob or "左侧前照灯" in blob
    if ecu_id == 48:
        return "40B" in blob or "右侧前照灯" in blob
    if ecu_id == 88:
        return "中央前照灯" in blob
    if ecu_id in (30, 31) and "断开点" in blob and "车门" not in blob:
        return False
    return True


def locators_for_ecu(ecu_id: int, headings: list[dict], wiring_pages: list[dict], generation: str, source_manual: str, source_wire: str) -> list[dict]:
    toks = ECU_PHRASE.get(ecu_id) or ()
    out = []
    for h in headings:
        blob = " ".join([h.get("title") or "", " ".join(h.get("ctrlPhrases") or []), " ".join(h.get("wmCodes") or "")])
        if any(t.lower() in blob.lower() for t in toks if t):
            out.append(
                {
                    "generation": generation,
                    "kind": "workshop_heading",
                    "sourceId": source_manual,
                    "page1based": h["page1based"],
                    "title": h.get("title"),
                    "wmCodes": h.get("wmCodes"),
                    "bodyModel": h.get("bodyModel"),
                    "evidenceClass": h.get("evidenceClass"),
                    "uncertainGlyph": h.get("uncertainGlyph"),
                    "notADiagnosticAddress": True,
                    "procedureVerified": False,
                }
            )
            if len([e for e in out if e["kind"] == "workshop_heading"]) >= 8:
                break
    for w in wiring_pages:
        if not caption_matches_ecu(ecu_id, w):
            continue
        out.append(
            {
                "generation": generation,
                "kind": "wiring_caption",
                "sourceId": source_wire,
                "page1based": w["page1based"],
                "printedCaption": w.get("printedCaption"),
                "tocTitle": w.get("tocTitle"),
                "captionSource": w.get("captionSource"),
                "candidate": bool(w.get("candidate", not w.get("visualVerified"))),
                "visualVerified": bool(w.get("visualVerified")),
                "connectivity": w.get("connectivity") or "pending_visual",
                "pinPairExtracted": bool(w.get("pinPairExtracted")),
                "sourcePinAssignmentsVerified": bool(w.get("sourcePinAssignmentsVerified")),
                "liveContinuityVerified": False,
                "notADiagnosticAddress": True,
            }
        )
        if len([e for e in out if e["kind"] == "wiring_caption"]) >= 6:
            break
    return out


def load_coverage_modules(path: Path) -> dict[int, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(e["ecu_id"]): list(e.get("dsn_modules") or []) for e in data.get("menu_ecus") or []}


def stream_x431_codes(variants: Path, modules_by_ecu: dict[int, list[str]]) -> dict:
    rev: dict[str, list[int]] = {}
    for ecu, mods in modules_by_ecu.items():
        for m in mods:
            rev.setdefault(m, []).append(ecu)
    unique: dict[tuple, dict] = {}
    rec_count = 0
    variant_rows = 0
    if not variants.is_file():
        return {"present": False, "unique": [], "recordCount": 0, "variantRows": 0, "duplicateRecordHits": 0}
    with variants.open(encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            variant_rows += 1
            gen_raw = o.get("generation")
            gen = gen_raw if gen_raw in ("981", "982") else None
            membership = o.get("membership") or ("confirmed" if o.get("accepted") else "candidate")
            mod = o.get("module")
            recs = ((o.get("pool_records") or {}).get("dtc") or {}).get("records") or []
            rec_count += len(recs)
            ecus = rev.get(mod) or [None]
            for r in recs:
                code = (r.get("code") or "").upper()
                if not code:
                    continue
                for ecu in ecus:
                    key = (ecu, code)
                    slot = unique.setdefault(
                        key,
                        {
                            "ecuId": ecu,
                            "x431Code": code,
                            "n": 0,
                            "statusKinds": set(),
                            "generationsSeen": set(),
                            "memberships": set(),
                            "unspecifiedGen": False,
                        },
                    )
                    slot["n"] += 1
                    slot["memberships"].add(membership)
                    if gen:
                        slot["generationsSeen"].add(gen)
                    else:
                        slot["unspecifiedGen"] = True
                    if r.get("statusKind") is not None:
                        slot["statusKinds"].add(int(r["statusKind"]))
    rows = []
    dup_hits = 0
    for slot in unique.values():
        gens = sorted(slot["generationsSeen"])
        dup_hits += max(0, slot["n"] - 1)
        unspecified = bool(slot.get("unspecifiedGen"))
        confirmed = "confirmed" in slot["memberships"]
        if unspecified:
            scope = ["981", "982"]
            memb = "candidate"
        elif gens == ["981"]:
            scope = ["981"]
            memb = "confirmed" if confirmed else "candidate"
        elif gens == ["982"]:
            scope = ["982"]
            memb = "confirmed" if confirmed else "candidate"
        else:
            scope = gens or ["981", "982"]
            memb = "candidate"
        rows.append(
            {
                "ecuId": slot["ecuId"],
                "x431Code": slot["x431Code"],
                "variantHits": slot["n"],
                "statusKinds": sorted(slot["statusKinds"]),
                "generationsSeen": gens,
                "generationExplicit": bool(gens) and not unspecified,
                "joinScope": scope,
                "membership": memb,
                "unspecifiedGen": unspecified,
                "confirmed981": bool(confirmed and "981" in gens),
                "fittedClaim": False,
            }
        )
    return {
        "present": True,
        "recordCount": rec_count,
        "uniqueCount": len(rows),
        "variantRows": variant_rows,
        "duplicateRecordHits": dup_hits,
        "unique": rows,
    }


def join_x431(entries: list[dict], x431: dict) -> list[dict]:
    by: dict[tuple, list[dict]] = {}
    for e in entries:
        if e.get("bodyEvidenceStatus") == "toc_only":
            continue
        bc = e.get("baseCode")
        by.setdefault((e.get("generation"), e.get("ecuId"), bc), []).append(
            {
                "generation": e.get("generation"),
                "rawCode": e.get("rawCode"),
                "sourceId": e.get("sourceId"),
                "pages": e.get("pages"),
                "bodyEvidenceStatus": e.get("bodyEvidenceStatus"),
            }
        )
    joins = []
    for row in x431.get("unique") or []:
        if row.get("ecuId") is None:
            continue
        bc = base_code(row["x431Code"]) or row["x431Code"]
        for gen in row.get("joinScope") or ["981", "982"]:
            manuals = by.get((gen, row["ecuId"], bc)) or []
            memb = row.get("membership") or "candidate"
            if gen == "981" and row.get("confirmed981"):
                memb = "confirmed"
            elif gen == "982" and (row.get("unspecifiedGen") or not row.get("generationExplicit")):
                memb = "candidate"
            if manuals:
                raws = [m["rawCode"] for m in manuals]
                if row["x431Code"] in raws:
                    rel = "exact_raw"
                else:
                    rel = "base_code_equal"
                if memb == "candidate":
                    rel = rel + "_candidate"
            else:
                rel = "x431_only_no_manual_body"
            joins.append(
                {
                    "generation": gen,
                    "ecuId": row["ecuId"],
                    "x431Code": row["x431Code"],
                    "membership": memb,
                    "generationExplicit": bool(row.get("generationExplicit")),
                    "manualHits": manuals[:12],
                    "relation": rel,
                    "fittedClaim": False,
                    "variantHits": row["variantHits"],
                }
            )
    return joins


def render_pages(pdf: Path, pages: tuple[int, ...], dest: Path, prefix: str, *, zoom: float = 1.2, clip=None) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        import pymupdf
    except ImportError:
        return written
    if not pdf.is_file():
        return written
    doc = pymupdf.open(str(pdf))
    try:
        for page in pages:
            if page < 1 or page > doc.page_count:
                continue
            pg = doc[page - 1]
            box = clip(pg.rect) if callable(clip) else clip
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=box)
            out = dest / f"{prefix}-p{page}.png"
            pix.save(str(out))
            written.append(str(out))
    finally:
        doc.close()
    return written


def title_clip(rect):
    import pymupdf

    return pymupdf.Rect(rect.width * 0.70, 30, rect.width, rect.height - 15)


def topology_clip(rect):
    import pymupdf

    return pymupdf.Rect(0, 60, rect.width * 0.72, rect.height - 40)


def diagnostic_topology(wire_pages: list[dict], *, generation: str, source_id: str, identity: dict | None = None) -> dict:
    plug_pages = [w["page1based"] for w in wire_pages if w.get("bodyTopologyClass") and "diagnostic_plug" in str(w.get("bodyTopologyClass"))]
    bus_pages = [w["page1based"] for w in wire_pages if w.get("bodyTopologyClass") and "named_can_buses" in str(w.get("bodyTopologyClass"))]
    bound = bind_obd_gateway_facts(source_id, identity)
    if bound:
        plug_pages = [bound["pdfPage"]]
    return {
        "generation": generation,
        "sourceId": source_id,
        "obdToGateway": {
            "status": "source_pin_assignment_verified" if bound else ("source_text_named_path" if plug_pages else "unreadable_in_text"),
            "fromLabel": "诊断插头插座",
            "via": ["诊断 CAN 高", "诊断 CAN 低"],
            "toLabel": "网关控制单元",
            "pages": plug_pages[:4],
            "printedSheet": bound["printedSheet"] if bound else None,
            "printedModelYear": bound["printedModelYear"] if bound else None,
            "pinPairs": bound["pinPairs"] if bound else [],
            "pinPairExtracted": bool(bound),
            "sourcePinAssignmentsVerified": bool(bound),
            "liveContinuityVerified": False,
            "notADiagnosticAddress": True,
            "notCanId": True,
            "note": (
                "cited diagram edition only; connector pin assignments are not CAN IDs, not all model years, and not physical continuity"
                if bound
                else "named endpoints only; pin assignments withheld without matching source hash"
            ),
        },
        "gatewayConnectorPairs": bound["gatewayConnectorPairs"] if bound else [],
        "gatewayNamedBuses": [
            {"bus": "CAN 驱动", "pages": bus_pages[:4]},
            {"bus": "底盘 CAN", "pages": bus_pages[:4]},
            {"bus": "舒适性 CAN", "pages": bus_pages[:4]},
        ],
        "remainingPins": "full_vehicle_pin_map_not_complete",
        **DISABLED,
    }


def extract_aux_pdf(path: Path, *, sid: str, role: str, note: str, max_pages: int = 30, extra_pages: tuple[int, ...] = ()) -> dict:
    row = {
        "id": sid,
        "kind": role,
        "locator": path.name,
        "present": path.is_file(),
        "status": "missing",
        "target991": False,
        "note": note,
        **DISABLED,
    }
    if not path.is_file():
        return row
    digest = sha256_file(path)
    import pymupdf

    heads = []
    codes = []
    with pymupdf.open(str(path)) as doc:
        n = doc.page_count
        for i in range(min(n, max_pages)):
            rec, unc, _f = recover_page(doc[i], sort_plain=True)
            if not rec.strip():
                rec = doc[i].get_text(sort=True)
            if i < 3 or "WM " in rec or "Diagnostic" in rec or "P0" in rec or "TI" in rec:
                heads.append({"page1based": i + 1, "preview": rec[:280], "uncertainGlyph": unc})
            codes.extend(codes_in_header_zone(rec, ignore_cross_ref=False)[:20])
        for p1 in extra_pages:
            if 1 <= p1 <= n:
                rec, unc, _f = recover_page(doc[p1 - 1], sort_plain=True)
                heads.append({"page1based": p1, "preview": rec[:360], "uncertainGlyph": unc, "targeted": True})
        row.update(
            present=True,
            status="locally_verified",
            sha256=digest,
            shaSource="hashed_this_run",
            pageCount=n,
            bytes=path.stat().st_size,
            headingSamples=[h for h in heads if h.get("targeted")] + [h for h in heads if not h.get("targeted")][:8],
            dtcTokensSample=sorted(set(codes))[:40],
        )
    return row


def pdk_and_gt4_sources(*, include_extras: bool) -> tuple[list[dict], list[dict]]:
    if not include_extras:
        return [], []
    extras_spec = [
        ("manual-cayman-981", Z_ROOT / "981_Cayman CaymanS CaymanGTS.pdf", "981_cayman_coupe", "coupe vs boxster roof/body; not a 991 target"),
        ("manual-gt4-road-wm", Z_ROOT / "981_Cayman Boxster Base S GT4 workshop manual.pdf", "981_gt4_road_letter_wm", "road GT4/Base/S letter WM; not Clubsport"),
        ("pdk-ti-3509", Z_ROOT / "PDK factory t-shoot document.pdf", "pdk_factory_ti", "PDK TI 35/09; printed applicability is 987"),
        ("pdk-training-zh", Z_ROOT / "PDK repair aftersales training_zh.pdf", "pdk_training", "aftersales training"),
        ("pdk-training-en", Z_ROOT / "PDK repair aftersales training.pdf", "pdk_training", "aftersales training"),
        ("pdk-valve", Z_ROOT / "PDK valve body and temp sensor.pdf", "pdk_component", "valve body / temp sensor"),
    ]
    rows = []
    aux = []
    for sid, path, role, note in extras_spec:
        maxp = 17 if sid == "pdk-ti-3509" else (25 if "training" in sid or sid == "pdk-valve" else 40)
        if sid == "manual-gt4-road-wm":
            maxp = 80
        extra_pages = (5066, 5067, 5068) if sid == "manual-gt4-road-wm" else ()
        rec = extract_aux_pdf(path, sid=sid, role=role, note=note, max_pages=maxp, extra_pages=extra_pages)
        if sid == "pdk-ti-3509" and rec.get("present"):
            rec["applicability"] = {
                "vehicleType": ["Boxster(987)", "Boxster S(987)", "Cayman", "Cayman S"],
                "modelYearAsOf": 2009,
                "iNo": "250",
                "date": "2012-01-17",
                "target981982Procedure": False,
                "role": "987_reference_only",
                "sourcePage": 2,
                "status": "root_verified_print",
            }
        rows.append({k: rec[k] for k in rec if k not in ("headingSamples", "dtcTokensSample")})
        aux.append(rec)
    return rows, aux


def build_manual_evidence(
    *,
    coverage_path: Path = DEFAULT_COVERAGE,
    wiring_981: Path | None = DEFAULT_WIRING_981,
    wiring_982: Path | None = DEFAULT_WIRING_982,
    wiring_gt4: Path | None = DEFAULT_WIRING_GT4,
    manual_981: Path | None = DEFAULT_MANUAL_981,
    manual_982: Path | None = DEFAULT_MANUAL_982,
    manual_991: Path | None = DEFAULT_MANUAL_991,
    variants_path: Path = VARIANTS,
    render_png: bool = False,
    skip_heavy: bool = False,
    include_extras: bool | None = None,
    output_root: Path | None = None,
) -> dict:
    global OUTPUT_ROOT
    prev_root = OUTPUT_ROOT
    if output_root is not None:
        OUTPUT_ROOT = Path(output_root)
    extras_on = (not skip_heavy) if include_extras is None else include_extras
    try:
        return _build_manual_evidence_inner(
            coverage_path=coverage_path,
            wiring_981=wiring_981,
            wiring_982=wiring_982,
            wiring_gt4=wiring_gt4,
            manual_981=manual_981,
            manual_982=manual_982,
            manual_991=manual_991,
            variants_path=variants_path,
            render_png=render_png,
            skip_heavy=skip_heavy,
            extras_on=extras_on,
        )
    finally:
        OUTPUT_ROOT = prev_root


def _build_manual_evidence_inner(
    *,
    coverage_path: Path,
    wiring_981,
    wiring_982,
    wiring_gt4,
    manual_981,
    manual_982,
    manual_991,
    variants_path: Path,
    render_png: bool,
    skip_heavy: bool,
    extras_on: bool,
) -> dict:
    identities = {
        "wiring-981": verify_source(wiring_981, "wiring-981"),
        "wiring-982": verify_source(wiring_982, "wiring-982"),
        "wiring-gt4cs": verify_source(wiring_gt4, "wiring-gt4cs"),
        "manual-981": verify_source(manual_981, "manual-981"),
        "manual-982": verify_source(manual_982, "manual-982"),
        "manual-991": verify_source(manual_991, "manual-991"),
    }
    extra_sources, aux_full = pdk_and_gt4_sources(include_extras=extras_on)
    headings_981 = {"headings": []}
    headings_cayman = {"headings": []}
    if identities["manual-981"].get("baselineMatch") and manual_981 and not skip_heavy:
        headings_981 = heading_scan_981(
            manual_981, identities["manual-981"]["sha256"], body_model="boxster", source_id="manual-981"
        )
    cay_path = Z_ROOT / "981_Cayman CaymanS CaymanGTS.pdf"
    if extras_on and cay_path.is_file() and not skip_heavy:
        headings_cayman = heading_scan_981(
            cay_path, sha256_file(cay_path), body_model="cayman_coupe", source_id="manual-cayman-981"
        )

    wire981 = wiring_page_index(wiring_981, identities["wiring-981"], "wiring-981") if wiring_981 else []
    wire982 = wiring_page_index(wiring_982, identities["wiring-982"], "wiring-982") if wiring_982 else []

    groups_981, toc_981 = [], []
    groups_982, toc_982 = [], []
    groups_cay, toc_cay = [], []
    if identities["manual-981"].get("baselineMatch") and manual_981 and not skip_heavy:
        groups_981, toc_981 = extract_dtc_range(
            manual_981, 3800, 4456, generation="981", source_id="manual-981", parser=parse_981_header, sort_plain=False, body_model="boxster", hash_ok=True
        )
        # verify earlier/later 诊断 headers outside historical window
        extra_g, extra_t = extract_dtc_range(
            manual_981,
            1,
            3800,
            generation="981",
            source_id="manual-981",
            parser=parse_981_header,
            sort_plain=False,
            body_model="boxster",
            hash_ok=True,
            ascii_prefilter=True,
        )
        groups_981.extend(extra_g)
        toc_981.extend(extra_t)
    if identities["manual-982"].get("baselineMatch") and manual_982 and not skip_heavy:
        groups_982, toc_982 = extract_982_chapters(manual_982, hash_ok=True, source_hash=identities["manual-982"].get("sha256"))
    if extras_on and cay_path.is_file() and not skip_heavy:
        groups_cay, toc_cay = extract_dtc_range(
            cay_path, 3680, 4421, generation="981", source_id="manual-cayman-981", parser=parse_981_header, sort_plain=False, body_model="cayman_coupe", hash_ok=True
        )

    entries = groups_to_entries(groups_981 + groups_982 + groups_cay, toc_981 + toc_982 + toc_cay)
    # never keep 991-owned rows
    entries = [e for e in entries if e.get("sourceId") != "manual-991" and e.get("generation") in ("981", "982")]

    modules = load_coverage_modules(coverage_path)
    x431_cache = cache_path("x431-unique-dtc.json")
    if skip_heavy:
        x431 = {"present": False, "unique": [], "recordCount": 0, "uniqueCount": 0, "variantRows": 0, "duplicateRecordHits": 0}
    elif x431_cache.is_file() and cache_fresh(json.loads(x431_cache.read_text(encoding="utf-8")), "x431"):
        x431 = json.loads(x431_cache.read_text(encoding="utf-8"))
    else:
        x431 = stream_x431_codes(variants_path, modules)
        payload = {k: v for k, v in x431.items()}
        payload["parserCacheVersion"] = PARSER_CACHE_VERSION
        payload["sourceSha256"] = "x431"
        x431_cache.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    joins = join_x431(entries, x431)
    if not skip_heavy:
        cache_path("x431-join.json").write_text(
            json.dumps(
                {
                    "parserCacheVersion": PARSER_CACHE_VERSION,
                    "recordCount": x431.get("recordCount"),
                    "uniqueCount": x431.get("uniqueCount"),
                    "variantRows": x431.get("variantRows"),
                    "duplicateRecordHits": x431.get("duplicateRecordHits"),
                    "matches": joins,
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    group_rows = []
    remaining = []
    for ecu in coverage.get("menu_ecus") or []:
        ecu_id = int(ecu["ecu_id"])
        gens = {}
        for generation in ("981", "982"):
            ev = locators_for_ecu(
                ecu_id,
                headings_981.get("headings") or [] if generation == "981" else [],
                wire981 if generation == "981" else wire982,
                generation,
                "manual-981" if generation == "981" else "manual-982",
                "wiring-981" if generation == "981" else "wiring-982",
            )
            if generation == "982":
                for ecu2, wm, start, end, label in WM982_DIAG_CHAPTERS:
                    if ecu2 == ecu_id or (ecu_id in (95, 96) and ecu2 == 95):
                        ev.append(
                            {
                                "generation": "982",
                                "kind": "workshop_toc",
                                "sourceId": "manual-982",
                                "page1based": start,
                                "tocTitle": f"{wm} {label}",
                                "matchedTokens": [wm, label],
                                "notADiagnosticAddress": True,
                                "procedureVerified": False,
                            }
                        )
            body_n = sum(1 for e in entries if e.get("ecuId") == ecu_id and e.get("generation") == generation and str(e.get("bodyEvidenceStatus", "")).startswith("body"))
            gaps = []
            if not ev:
                gaps.append("no heading/wiring caption locator")
            if body_n == 0:
                gaps.append("no per-code body header in scanned manuals")
                remaining.append(
                    {
                        "ecuId": ecu_id,
                        "generation": generation,
                        "reasonClass": "source_absent_or_not_in_scanned_headers",
                        "attempted": ["wiring TOC/captions", "WM diagnostic chapters" if generation == "982" else "981 heading scan + DTC pages"],
                    }
                )
            gens[generation] = {"evidence": ev, "gaps": gaps, "bodyDtcHeaders": body_n, **DISABLED}
        if ecu_id == 39:
            gens["981"]["gaps"].append("convertible-top wiring 15H CABRIO vs Cayman coupe (no 15H) kept distinct")
            gens["982"]["gaps"].append("982 CABRIO 15H vs 45C COUPE tail sheet are distinct body locators")
        if ecu_id == 75:
            gens["981"]["gaps"].append("TV tuner: wiring TOC token may be absent on 981 sheets")
        group_rows.append({"ecuId": ecu_id, "label": ecu.get("label"), "generations": gens, "gaps": ["menu group is not a physical-fit claim"], **DISABLED})

    renders = []
    if render_png:
        review = OUTPUT_ROOT / "review"
        if manual_981 and manual_981.is_file():
            renders += render_pages(manual_981, (3841, 3842, 3843), review, "wm981-dtc", zoom=1.5)
        if wiring_981 and wiring_981.is_file():
            renders += render_pages(wiring_981, (11,), review, "wire981-topo", zoom=1.6, clip=topology_clip)
            renders += render_pages(wiring_981, (11,), review, "wire981-title", zoom=2.0, clip=title_clip)
        if wiring_982 and wiring_982.is_file():
            renders += render_pages(wiring_982, (11, 12, 13), review, "wire982-topo", zoom=1.6, clip=topology_clip)
            renders += render_pages(wiring_982, (62, 64), review, "wire982-title", zoom=2.2, clip=title_clip)
        if extras_on:
            pdk = Z_ROOT / "PDK factory t-shoot document.pdf"
            if pdk.is_file():
                renders += render_pages(pdk, (1, 2), review, "pdk-ti", zoom=1.4)
            gt4 = Z_ROOT / "981_Cayman Boxster Base S GT4 workshop manual.pdf"
            if gt4.is_file():
                renders += render_pages(gt4, (5066, 5067), review, "gt4road", zoom=1.2)

    compact_entries = []
    for e in entries:
        compact_entries.append({k: e[k] for k in e if k != "needs"} | {"needs": [
            {kk: item[kk] for kk in item if kk != "items"} | {"itemCount": len(item.get("items") or []), "itemsPreview": (item.get("items") or [])[:4]}
            for item in e.get("needs") or []
        ]})

    if not skip_heavy:
        cache_path("dtc-groups-private.json").write_text(
            json.dumps(
                {
                    "note": "private fuller groups; not a procedure-verified claim",
                    "parserCacheVersion": PARSER_CACHE_VERSION,
                    "groups": [
                        {k: g[k] for k in g if k != "text"} | {"textPreview": (g.get("text") or "")[:800]}
                        for g in (groups_981 + groups_982 + groups_cay)
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    body_hits = sum(1 for e in compact_entries if str(e.get("bodyEvidenceStatus", "")).startswith("body"))
    toc_only = sum(1 for e in compact_entries if e.get("bodyEvidenceStatus") == "toc_only")
    unique_keys = {(e.get("generation"), e.get("ecuId"), e.get("rawCode"), e.get("sourceId")) for e in compact_entries}
    unique_base = {(e.get("generation"), e.get("ecuId"), e.get("baseCode")) for e in compact_entries if e.get("baseCode")}
    join_matched = sum(1 for j in joins if j["relation"] != "x431_only_no_manual_body")
    seed = {
        "schemaVersion": 1,
        "parserCacheVersion": PARSER_CACHE_VERSION,
        "task": "offline_manual_dtc_evidence",
        "fittedClaim": False,
        **DISABLED,
        "procedureVerified": False,
        "sources": list(identities.values()) + extra_sources,
        "groups": group_rows,
        "dtcEntries": compact_entries,
        "diagnosticTopology": [
            diagnostic_topology(wire981, generation="981", source_id="wiring-981", identity=identities["wiring-981"]),
            diagnostic_topology(wire982, generation="982", source_id="wiring-982", identity=identities["wiring-982"]),
        ],
        "auxiliaryEvidence": aux_full,
        "x431Join": {
            "variantsPresent": x431.get("present"),
            "recordCountScanned": x431.get("recordCount"),
            "uniqueCodes": x431.get("uniqueCount"),
            "variantRows": x431.get("variantRows"),
            "duplicateRecordHits": x431.get("duplicateRecordHits"),
            "matches": [j for j in joins if j["relation"] != "x431_only_no_manual_body"],
            "fullJoinLocator": ".local/x431-re/2026-09-27-offline-completion/manual/x431-join.json",
            "matched": join_matched,
            "x431Only": len(joins) - join_matched,
            "fittedClaim": False,
            "note": "unspecified-generation variants join 981 and 982 as membership=candidate; explicit 981-only stays 981",
        },
        "counts": {
            "groupCount": len(group_rows),
            "dtcEntryRows": len(compact_entries),
            "dtcUniqueGenEcuRawSource": len(unique_keys),
            "dtcUniqueGenEcuBase": len(unique_base),
            "bodyHeaderEntries": body_hits,
            "tocOnlyEntries": toc_only,
            "headingLocators981": headings_981.get("headingCount"),
            "headingLocatorsCayman": headings_cayman.get("headingCount"),
            "wiringPages981": len(wire981),
            "wiringPages982": len(wire982),
        },
        "remainingGaps": remaining
        + [
            {"reasonClass": "ocr_font_ambiguity", "note": "SimSun/unknown-font replacements flagged; not guessed"},
            {"reasonClass": "requires_vehicle", "note": "pin-by-pin connectivity and live ECU fit not established offline"},
            {"reasonClass": "gt4_cs_motorsport_separate", "note": "981GT4_CS wiring is Clubsport; not road GT4 letter WM"},
        ],
        "reviewPng": renders,
        "flags": {
            "procedureVerified": False,
            "pinMapComplete": False,
            "allManualProceduresHumanVerified": False,
            "991TargetImported": False,
        },
    }
    if not skip_heavy:
        cache_path("heading-index.json").write_text(json.dumps(headings_981, ensure_ascii=False), encoding="utf-8")
        cache_path("heading-index-cayman.json").write_text(json.dumps(headings_cayman, ensure_ascii=False), encoding="utf-8")
    return seed


def write_seed(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="ascii")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scripts.diagnostics.manual_evidence")
    p.add_argument("--out", default=str(DEFAULT_SEED))
    p.add_argument("--coverage", default=str(DEFAULT_COVERAGE))
    p.add_argument("--wiring-981", default=str(DEFAULT_WIRING_981))
    p.add_argument("--wiring-982", default=str(DEFAULT_WIRING_982))
    p.add_argument("--wiring-gt4", default=str(DEFAULT_WIRING_GT4))
    p.add_argument("--manual-981", default=str(DEFAULT_MANUAL_981))
    p.add_argument("--manual-982", default=str(DEFAULT_MANUAL_982))
    p.add_argument("--manual-991", default=str(DEFAULT_MANUAL_991))
    p.add_argument("--variants", default=str(VARIANTS))
    p.add_argument("--render-png", action="store_true")
    p.add_argument("--skip-heavy", action="store_true", help="schema/CLI without PDF chapter extract")
    p.add_argument("--include-extras", action="store_true", help="read Z: PDK/GT4/Cayman extras")
    p.add_argument("--output-root", default="")
    args = p.parse_args(argv)
    data = build_manual_evidence(
        coverage_path=Path(args.coverage),
        wiring_981=Path(args.wiring_981) if args.wiring_981 else None,
        wiring_982=Path(args.wiring_982) if args.wiring_982 else None,
        wiring_gt4=Path(args.wiring_gt4) if args.wiring_gt4 else None,
        manual_981=Path(args.manual_981) if args.manual_981 else None,
        manual_982=Path(args.manual_982) if args.manual_982 else None,
        manual_991=Path(args.manual_991) if args.manual_991 else None,
        variants_path=Path(args.variants),
        render_png=args.render_png,
        skip_heavy=args.skip_heavy,
        include_extras=True if args.include_extras else (False if args.skip_heavy else None),
        output_root=Path(args.output_root) if args.output_root else None,
    )
    out = Path(args.out)
    write_seed(data, out)
    print("out", repo_rel(out))
    print("dtcEntryRows", data["counts"]["dtcEntryRows"], "body", data["counts"]["bodyHeaderEntries"])
    print("groups", data["counts"]["groupCount"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
