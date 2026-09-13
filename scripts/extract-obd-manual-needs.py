#!/usr/bin/env python3
"""Read-only PDF candidate extraction, not verified procedures. Outputs .local/obd-analysis/manual/."""
from __future__ import annotations

import json
import re
import time
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local" / "obd-analysis" / "manual"
EXTRACT = OUT / "extract"
PAGES = OUT / "pages"
EXTRACT.mkdir(parents=True, exist_ok=True)
PAGES.mkdir(parents=True, exist_ok=True)

PY = sys.executable
OFFSET_ARIAL = 0x2D71
OFFSET_SIMSUN = 0x4A2C

# Arial leftovers that do NOT become the intended glyph via +0x2d71.
# U+318F + 0x2d71 = 开 — do NOT map to slash.
ARIAL_PUNCT = {
    "᧨": "，",
    "᧧": "：",
    "ˈ": "，",
    "˄": "（",
    "˅": "）",
    "᧤": "（",
    "᧥": "）",
    "Ǆ": "。",
    "ᇬ": "，",
    "ᇭ": "。",
}

DTC8 = re.compile(r"([PBCU][0-9A-Fa-f]{4}[0-9A-Fa-f]{2})")
DTC5 = re.compile(r"([PBCU][0-9A-Fa-f]{4})(?![0-9A-Fa-f])")
HEAD_981 = re.compile(r"诊断\s*(.+?)\s*控制")
TOC_981 = set(range(3831, 3841))
PDFS = {
    "981_boxster": r"Z:\porsche\981\981_Boxster BoxsterS BoxsterGTS.pdf",
    "982_boxster": r"Z:\porsche\981\982_Boxster BoxsterS BoxsterGTS.pdf",
    "981_cayman": r"Z:\porsche\981\981_Cayman CaymanS CaymanGTS.pdf",
    "cayman_ws": r"Z:\porsche\981\981_Cayman Boxster Base S GT4 workshop manual.pdf",
    "pdk_ti": r"Z:\porsche\981\PDK factory t-shoot document.pdf",
}

SECTION_MAP = {
    "诊断条": "诊断条件",
    "诊断条件": "诊断条件",
    "故障设": "故障设置",
    "故障设置": "故障设置",
    "监控的部": "监控的部件",
    "监控的捷": "监控的部件",
    "故障查找": "故障查找",
    "Diagnostic conditions": "诊断条件",
    "Possible fault causes": "故障原因",
    "Fault setting condition": "故障设置",
    "Fault finding": "故障查找",
}

STOP_HEADS = ("功能", "Function", "Impacts", "功能说明")

ENABLEMENT = (
    "诊断持续运行",
    "持续运行",
    "点火打开",
    "点火开关",
    "short test",
    "Ignition on",
    "engine running",
    "Perform a short test",
)

DATA_NAME_RULES = [
    (r"发动机转速|Engine speed", "发动机转速", "continuous", "转/分"),
    (r"机油温度|Engine oil temperature", "发动机机油温度", "continuous", "°C"),
    (r"冷却液温度|coolant", "冷却液温度", "continuous", "°C"),
    (r"扭矩|Torque", "扭矩", "continuous", "Nm"),
    (r"油门踏板|accelerator pedal", "油门踏板值", "continuous", "%"),
    (r"缺火率|misfire", "缺火率", "continuous", "%"),
    (r"制动灯开关|Brake light switch", "制动灯开关", "once", None),
    (r"凸轮轴", "凸轮轴调节", "continuous", None),
    (r"压力传感器|Pressure sensor", "压力传感器", "continuous", None),
]


def shift_arial(ch: str) -> str:
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return ch
    if ch in ARIAL_PUNCT:
        return ARIAL_PUNCT[ch]
    cand = o + OFFSET_ARIAL
    if 0xD800 <= cand <= 0xDFFF:
        return ch
    return chr(cand)


def shift_simsun(ch: str) -> str:
    o = ord(ch)
    if 0x20 <= o <= 0x7E:
        return ch
    cand = o + OFFSET_SIMSUN
    if 0xD800 <= cand <= 0xDFFF:
        return ch
    return chr(cand)


def has_uncertain_glyph(s: str) -> bool:
    return any(0x3400 <= ord(c) <= 0x4DBF for c in s)


def scrub_str(s: str) -> str:
    return "".join(ch if not (0xD800 <= ord(ch) <= 0xDFFF) else "?" for ch in s)


def json_safe(obj):
    if isinstance(obj, str):
        return scrub_str(obj)
    if isinstance(obj, list):
        return [json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {scrub_str(k) if isinstance(k, str) else k: json_safe(v) for k, v in obj.items()}
    return obj


def is_watermark_span(font: str, text: str, size: float) -> bool:
    if "中国汽车" in text or "QQ:" in text:
        return True
    return font == "SimSun" and size >= 20


def recover_span(font: str, text: str, size: float) -> str:
    if is_watermark_span(font, text, size):
        return ""
    if "ArialUnicode" in font:
        return "".join(shift_arial(c) for c in text)
    if font == "SimSun":
        return "".join(shift_simsun(c) for c in text)
    return text


def page_text_recovered(page) -> tuple[str, dict]:
    rows: dict[float, list[str]] = {}
    watermarks = []
    fonts = Counter()
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            y = round(line["bbox"][1] / 2) * 2
            parts = []
            for sp in line.get("spans", []):
                font = sp.get("font", "")
                text = sp.get("text", "")
                size = float(sp.get("size") or 0)
                fonts[font] += 1
                if is_watermark_span(font, text, size):
                    if text.strip():
                        watermarks.append(text.strip())
                    continue
                parts.append(recover_span(font, text, size))
            rows.setdefault(y, []).append("".join(parts))
    body = "\n".join("".join(rows[y]) for y in sorted(rows) if "".join(rows[y]).strip())
    return body, {"fonts": dict(fonts), "watermark": sorted(set(watermarks))}


def full_to_base(code: str) -> str:
    c = code.upper()
    return c[:5] if len(c) >= 5 else c


def looks_like_toc(text: str, page: int, model: str) -> bool:
    if model == "981" and page in TOC_981:
        return True
    if "Porsche DTC Diagnostic Information" in text and text.count("……") > 8:
        return True
    dotted = len(re.findall(r"[PBCU][0-9A-Fa-f]{4}.*\.{8,}", text))
    if dotted >= 8 and "Diagnostic conditions" not in text and "诊断条" not in text and "诊断条件" not in text:
        return True
    return False


def header_codes_981(header_blob: str) -> list[str]:
    codes = []
    for m in DTC8.findall(header_blob):
        u = m.upper()
        if u not in codes:
            codes.append(u)
    return codes


def normalize_pdf_text(t: str) -> str:
    return (t or "").replace("\xa0", " ").replace("\xad", "-")


def normalize_dtc_owner(code: str) -> str:
    u = code.upper()
    if re.fullmatch(r"[PBCU][0-9A-F]{4}", u):
        return u + "00"
    return u


def parse_toc_map(text: str) -> dict[str, int]:
    m = {}
    for code, num in re.findall(r"([PBCU][0-9A-Fa-f]{4,8})\s*\.{3,}\s*(\d+)", text):
        if re.fullmatch(r"0{4,}", code[1:]):
            continue
        m[normalize_dtc_owner(code)] = int(num)
    return m


def header_codes_982(header_blob: str) -> list[str]:
    blob = re.split(r"None of the following|following faults stored|下列故障", header_blob, flags=re.I)[0]
    codes = []
    for ln in blob.splitlines()[:12]:
        ln = ln.strip()
        m = re.match(r"^([PBCU][0-9A-Fa-f]{4,8})\b", ln)
        if not m or "Diagnostic" in ln or "Diagnosis" in ln:
            continue
        u = normalize_dtc_owner(m.group(1))
        if u not in codes:
            codes.append(u)
    return codes


def section_name(ln: str) -> str | None:
    s = ln.strip()
    for k, v in SECTION_MAP.items():
        if s.startswith(k) or s == k:
            return v
    if s in ("诊断条", "故障设", "监控的部"):
        return SECTION_MAP.get(s)
    return None


def is_stop(ln: str) -> bool:
    s = ln.strip()
    return s in STOP_HEADS or s.startswith("Function") or s.startswith("功能")


def parse_bullets(lines, start: int):
    items = []
    i = start
    while i < len(lines):
        ln = lines[i]
        if is_stop(ln) or section_name(ln) or HEAD_981.search(ln.replace(" ", "")):
            break
        if ln.startswith(("I ", "J ", "K ", "I", "J", "K")) and len(ln) > 1:
            body = ln[1:].strip() if ln[1:2] == " " else ln[1:].strip()
            if body:
                items.append(body)
        i += 1
    return items, i


def extract_981_stream(pages: list[dict], toc_pages: set[int]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}
    owners: list[str] = []
    section = None
    module = None
    title = ""
    stopped = False

    def ensure(code: str):
        grouped.setdefault(
            code,
            {
                "fullCode": code,
                "pages": [],
                "module": None,
                "title": "",
                "sections": defaultdict(list),
            },
        )
        return grouped[code]

    for rec in pages:
        page = rec["page"]
        text = rec["text"]
        if page in toc_pages or looks_like_toc(text, page, "981"):
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        i = 0
        while i < len(lines):
            ln = lines[i]
            if is_stop(ln):
                stopped = True
                i += 1
                continue
            hm = HEAD_981.search(ln.replace(" ", ""))
            if hm:
                stopped = False
                module = hm.group(1)
                # header blob until first section
                blob_lines = [ln]
                j = i + 1
                while j < len(lines) and not section_name(lines[j]) and not HEAD_981.search(lines[j].replace(" ", "")):
                    blob_lines.append(lines[j])
                    j += 1
                blob = "\n".join(blob_lines)
                owners = header_codes_981(blob)
                title = re.sub(DTC8, "", blob)
                title = re.sub(r"诊断.*?控制", "", title)
                title = re.sub(r"\s+", " ", title).strip(" ：:·")[:120]
                section = None
                for c in owners:
                    d = ensure(c)
                    d["module"] = module
                    if title:
                        d["title"] = title
                    if page not in d["pages"]:
                        d["pages"].append(page)
                i = j
                continue
            sn = section_name(ln)
            if sn:
                section = sn
                stopped = False
                i += 1
                continue
            if stopped or not owners or not section:
                i += 1
                continue
            if ln[:1] in "IJK" and len(ln) > 1:
                body = ln[1:].strip()
                if not body:
                    i += 1
                    continue
                for c in owners:
                    d = ensure(c)
                    d["sections"][section].append({"text": body, "page": page})
                    if page not in d["pages"]:
                        d["pages"].append(page)
            i += 1
    for d in grouped.values():
        d["sections"] = dict(d["sections"])
    return grouped


def extract_982_stream(pages: list[dict]) -> dict[str, dict]:
    """Header codes from physical top (fitz sort=True). No TOC invert."""
    grouped: dict[str, dict] = {}
    owners: list[str] = []
    section = None
    module = None
    stopped = False

    def ensure(code: str):
        grouped.setdefault(
            code,
            {
                "fullCode": code,
                "pages": [],
                "module": None,
                "title": "",
                "sections": defaultdict(list),
            },
        )
        return grouped[code]

    for rec in pages:
        page = rec["page"]
        text = normalize_pdf_text(rec["text"])
        if looks_like_toc(text, page, "982"):
            owners = []
            section = None
            continue
        diag = "Diagnostic information" in text or "Diagnosis information" in text
        if not diag and not owners:
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        i = 0
        while i < len(lines):
            ln = lines[i]
            if is_stop(ln):
                stopped = True
                i += 1
                continue
            if ln.startswith("Diagnostic information") or ln.startswith("Diagnosis information"):
                stopped = False
                module = re.sub(r"Diagnos(tic|is) information\s*[-–—]?\s*", "", ln, flags=re.I)
                module = module.replace("control unit", "").strip()
                blob_lines = []
                k = i - 1
                while k >= 0 and len(blob_lines) < 6:
                    blob_lines.insert(0, lines[k])
                    k -= 1
                blob_lines.append(ln)
                j = i + 1
                while j < len(lines) and not section_name(lines[j]) and not lines[j].startswith(("Diagnostic information", "Diagnosis information")):
                    blob_lines.append(lines[j])
                    j += 1
                blob = "\n".join(blob_lines)
                from_header = header_codes_982(blob)
                if from_header:
                    owners = from_header
                title = ""
                for bl in blob_lines:
                    if bl.startswith(("Diagnostic conditions", "Possible fault", "K  INFORMATION", "K INFORMATION", "Diagnostic information", "Diagnosis information")):
                        continue
                    if re.match(r"^[PBCU][0-9A-Fa-f]{4,8}$", bl) or re.fullmatch(r"\d+", bl):
                        continue
                    title = bl
                    break
                for c in owners:
                    d = ensure(c)
                    d["module"] = module or d["module"]
                    if title:
                        d["title"] = title[:160]
                    if page not in d["pages"]:
                        d["pages"].append(page)
                i = j
                continue
            sn = section_name(ln)
            if sn:
                section = sn
                stopped = False
                i += 1
                continue
            if stopped or not owners or not section:
                i += 1
                continue
            if ln[:1] in "IJK" or ln.startswith(("•", "-", "♦")):
                body = ln[1:].strip() if ln[:1] in "IJK" else ln.lstrip("•-♦ ").strip()
                if body:
                    for c in owners:
                        d = ensure(c)
                        d["sections"][section].append({"text": body, "page": page})
                        if page not in d["pages"]:
                            d["pages"].append(page)
            i += 1
    for d in grouped.values():
        d["sections"] = dict(d["sections"])
    return grouped


def classify_need(item: str, page: int, file: str, module: str | None) -> dict | None:
    if any(x.lower() in item.lower() for x in ENABLEMENT) and not re.search(r"转速|扭矩|温度|speed|temperature|torque", item, re.I):
        return None  # enablement, not a PID
    uncertain = has_uncertain_glyph(item)
    name = None
    kind = "unverified"
    unit = None
    for pat, nm, k, un in DATA_NAME_RULES:
        if re.search(pat, item, re.I):
            name, kind, unit = nm, k, un
            break
    if name is None:
        if any(x in item for x in ("检查", "更换", "拆", "测量", "插头", "测量", "faulty", "Replace", "Check", "leaking")):
            name = item[:40]
            kind = "manual"
        else:
            return None
    # command vs observation
    if re.search(r"加速|踩下|actuate|actuate brake|Perform a short test|执行.{0,6}测试", item, re.I):
        kind = "manual"
    return {
        "name": name,
        "kind": kind,
        "moduleHint": module,
        "unit": unit,
        "condition": item[:200],
        "evidence": {"file": file, "page": page, "excerpt": item[:160]},
        "confidence": "extracted-candidate",
        "uncertainGlyph": uncertain,
    }


def load_pages(path: str, start: int, end: int, recover: bool) -> list[dict]:
    print(f"LOAD {Path(path).name} {start}-{end} recover={recover}", flush=True)
    doc = fitz.open(path)
    end = min(end, len(doc))
    out = []
    t0 = time.time()
    for i in range(start - 1, end):
        if (i + 1) % 250 == 0:
            print(f"  page {i+1} {time.time()-t0:.1f}s", flush=True)
        if recover:
            text, meta = page_text_recovered(doc[i])
        else:
            text = normalize_pdf_text(doc[i].get_text(sort=True) or "")
            meta = {"sort": True}
        out.append({"page": i + 1, "file": Path(path).name, "text": text, "meta": meta})
    doc.close()
    print(f"  done n={len(out)} {time.time()-t0:.1f}s", flush=True)
    return out


def render_png(path: str, page_1: int, dest: Path):
    doc = fitz.open(path)
    pix = doc[page_1 - 1].get_pixmap(matrix=fitz.Matrix(1.15, 1.15), alpha=False)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pix.save(dest.as_posix())
    doc.close()


def main():
    t0 = time.time()
    codes_json = json.loads((ROOT / "data/seed/dtc/manuals/by-code/codes.json").read_text(encoding="utf-8"))
    union = [c for c in codes_json["codes"] if set(c.get("models") or []) & {"981", "982"}]
    idx_982_pages = []
    for c in union:
        for p in c.get("pages") or []:
            if p.get("model") == "982":
                idx_982_pages.append(p.get("page") or 0)
    max_982_idx = max(idx_982_pages) if idx_982_pages else 5195
    min_982_idx = min(x for x in idx_982_pages if x) if idx_982_pages else 4220
    print(f"union={len(union)} 982 index pages {min_982_idx}-{max_982_idx}", flush=True)

    # visual samples only (not used as verified status)
    for p, name in [(3841, "981-fitz-p3841.png"), (3962, "981-fitz-p3962.png"), (3963, "981-fitz-p3963.png"), (4033, "981-fitz-p4033.png"), (3999, "981-fitz-p3999.png")]:
        render_png(PDFS["981_boxster"], p, PAGES / name)
    render_png(PDFS["982_boxster"], 4242, PAGES / "982-fitz-p4242.png")
    render_png(PDFS["982_boxster"], max_982_idx, PAGES / f"982-fitz-p{max_982_idx:04d}.png")

    p981 = load_pages(PDFS["981_boxster"], 3831, 4455, True)
    (EXTRACT / "981_recovered_p3962.txt").write_text(next(x["text"] for x in p981 if x["page"] == 3962), encoding="utf-8")
    (EXTRACT / "981_recovered_p3841.txt").write_text(next(x["text"] for x in p981 if x["page"] == 3841), encoding="utf-8")
    (EXTRACT / "981_recovered_p3963.txt").write_text(next(x["text"] for x in p981 if x["page"] == 3963), encoding="utf-8")
    (EXTRACT / "981_recovered_p4033.txt").write_text(next(x["text"] for x in p981 if x["page"] == 4033), encoding="utf-8")
    (EXTRACT / "981_recovered_p3999.txt").write_text(next(x["text"] for x in p981 if x["page"] == 3999), encoding="utf-8")

    p_cay = load_pages(PDFS["981_cayman"], 3700, 4420, True)
    # 982: cover index refs + margin
    p982_start = min(4220, min_982_idx)
    p982_end = max(5220, max_982_idx + 15)
    p982 = load_pages(PDFS["982_boxster"], p982_start, p982_end, False)
    pdk = load_pages(PDFS["pdk_ti"], 1, 17, False)
    (EXTRACT / "982_sorted_p4242.txt").write_text(next(x["text"] for x in p982 if x["page"] == 4242), encoding="utf-8")

    g981 = extract_981_stream(p981, TOC_981)
    # cayman: detect toc via looks_like_toc
    g_cay = extract_981_stream(p_cay, set())
    g982 = extract_982_stream(p982)

    # last 982 diagnostic page
    last_diag = max(
        (
            r["page"]
            for r in p982
            if "Diagnostic information" in normalize_pdf_text(r["text"])
            or "Diagnosis information" in normalize_pdf_text(r["text"])
        ),
        default=None,
    )

    (EXTRACT / "981_grouped.json").write_text(json.dumps(json_safe(g981), ensure_ascii=False, indent=1), encoding="utf-8")
    (EXTRACT / "982_grouped.json").write_text(json.dumps(json_safe(g982), ensure_ascii=False, indent=1), encoding="utf-8")
    (EXTRACT / "cayman_grouped.json").write_text(json.dumps(json_safe(g_cay), ensure_ascii=False, indent=1), encoding="utf-8")

    pdk_text = "\n".join(r["text"] for r in pdk)
    pdk_codes = sorted(set(DTC5.findall(pdk_text)))
    (EXTRACT / "pdk_all.txt").write_text(scrub_str(pdk_text), encoding="utf-8")
    (EXTRACT / "pdk_codes.json").write_text(json.dumps(pdk_codes, ensure_ascii=False, indent=1), encoding="utf-8")

    index_bases = {c["code"].upper() for c in union}
    extra = []
    for src, g in (("981_boxster", g981), ("982_boxster", g982), ("981_cayman", g_cay)):
        for full, d in g.items():
            base = full_to_base(full)
            if base not in index_bases and full not in index_bases:
                extra.append(
                    {
                        "fullCode": full,
                        "base": base,
                        "source": src,
                        "pages": d["pages"],
                        "title": d.get("title"),
                        "module": d.get("module"),
                    }
                )
    extra.sort(key=lambda x: x["fullCode"])
    (EXTRACT / "extra-codes.json").write_text(json.dumps(json_safe(extra), ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "extra-codes.json").write_text(json.dumps(json_safe(extra), ensure_ascii=False, indent=1), encoding="utf-8")

    file981 = "981_Boxster BoxsterS BoxsterGTS.pdf"
    file982 = "982_Boxster BoxsterS BoxsterGTS.pdf"

    def find_group(g, code):
        hits = []
        for k, d in g.items():
            if k == code or full_to_base(k) == code:
                hits.append(d)
        return hits

    requirements = []
    status = Counter()
    for rec in union:
        code = rec["code"]
        hits981 = find_group(g981, code)
        hits982 = find_group(g982, code)
        needs = []
        missing = []
        variants = []
        for p in rec.get("pages") or []:
            if p.get("model") not in ("981", "982"):
                continue
            ctx = p.get("kind") or "index"
            if p.get("page") in TOC_981:
                ctx = "toc"
            variants.append(
                {
                    "model": p.get("model"),
                    "file": p.get("file"),
                    "page": p.get("page"),
                    "fullCode": code,
                    "context": ctx,
                }
            )
        for d in hits981:
            for pg in d["pages"]:
                variants.append(
                    {
                        "model": "981",
                        "file": file981,
                        "page": pg,
                        "fullCode": d["fullCode"],
                        "context": "diagnostic-body",
                    }
                )
            for sec, items in (d.get("sections") or {}).items():
                if sec == "故障设置":
                    for it in items:
                        missing.append(f"故障设置（ECU置位条件，非更换阈值） p{it['page']}: {it['text'][:120]}")
                    continue
                if sec == "诊断条件":
                    for it in items:
                        n = classify_need(it["text"], it["page"], file981, d.get("module"))
                        if n:
                            n["role"] = "diagnostic-condition"
                            needs.append(n)
                        else:
                            missing.append(f"诊断使能/非PID p{it['page']}: {it['text'][:100]}")
                    continue
                if sec == "故障查找":
                    for it in items:
                        needs.append(
                            {
                                "name": it["text"][:40],
                                "kind": "manual",
                                "moduleHint": d.get("module"),
                                "unit": None,
                                "condition": it["text"][:200],
                                "evidence": {"file": file981, "page": it["page"], "excerpt": it["text"][:160]},
                                "confidence": "extracted-candidate",
                                "role": "fault-finding-step",
                                "uncertainGlyph": has_uncertain_glyph(it["text"]),
                            }
                        )
                    continue
                if sec == "监控的部件":
                    for it in items:
                        needs.append(
                            {
                                "name": it["text"][:40],
                                "kind": "unverified",
                                "moduleHint": d.get("module"),
                                "unit": None,
                                "condition": it["text"][:200],
                                "evidence": {"file": file981, "page": it["page"], "excerpt": it["text"][:160]},
                                "confidence": "extracted-candidate",
                                "role": "monitored-component",
                            }
                        )
        for d in hits982:
            for pg in d["pages"]:
                variants.append(
                    {
                        "model": "982",
                        "file": file982,
                        "page": pg,
                        "fullCode": d["fullCode"],
                        "context": "diagnostic-body",
                    }
                )
            for sec, items in (d.get("sections") or {}).items():
                if sec == "故障设置":
                    for it in items:
                        missing.append(f"982 故障设置（置位条件） p{it['page']}: {it['text'][:120]}")
                    continue
                if sec in ("诊断条件", "故障原因", "故障查找"):
                    for it in items:
                        n = classify_need(it["text"], it["page"], file982, d.get("module"))
                        if n:
                            n["role"] = "diagnostic-condition" if sec == "诊断条件" else ("fault-finding-step" if sec == "故障查找" else "possible-cause")
                            if sec == "故障查找":
                                n["kind"] = "manual"
                            needs.append(n)
                        elif sec == "诊断条件":
                            missing.append(f"982 诊断使能/非PID p{it['page']}: {it['text'][:100]}")
        if "982" in (rec.get("models") or []) and "981" not in (rec.get("models") or []):
            missing.append("982-only：对 2014 981 适用性未核实")

        # dedupe variants
        seen = set()
        uniq = []
        for v in variants:
            key = (v.get("file"), v.get("page"), v.get("fullCode"), v.get("context"), v.get("model"))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(v)

        body_pages = [v for v in uniq if v.get("context") == "diagnostic-body"]
        if body_pages:
            review = "partial-evidence"
        else:
            review = "index-only"
            missing.append("无主键头明细页（仅目录/索引或交叉引用，未作为该码诊断正文）")

        title = ""
        if hits981:
            title = hits981[0].get("title") or ""
        if not title and hits982:
            title = hits982[0].get("title") or ""
        if not title:
            title = rec.get("title_zh") or rec.get("en_gloss") or ""

        requirements.append(
            {
                "code": code,
                "title": title,
                "models": [m for m in rec.get("models") or [] if m in ("981", "982")],
                "variants": uniq,
                "reviewStatus": review,
                "needs": needs,
                "missing": missing,
            }
        )
        status[review] += 1

    (OUT / "requirements.json").write_text(json.dumps(json_safe(requirements), ensure_ascii=False, indent=1), encoding="utf-8")

    def check(code, expect_pages, forbid_pages=None):
        r = next(x for x in requirements if x["code"] == code)
        ev = sorted({n["evidence"]["page"] for n in r["needs"]})
        forbid_pages = forbid_pages or []
        return {
            "code": code,
            "need_pages": ev,
            "expect_any": expect_pages,
            "ok": (not expect_pages or any(p in ev for p in expect_pages)) and not any(p in ev for p in forbid_pages),
            "status": r["reviewStatus"],
            "n_needs": len(r["needs"]),
            "title": r["title"][:80],
        }

    audits = [
        check("P0301", [3962, 3963]),
        check("P0571", [4033]),
        check("P0420", [3999]),
        check("P000A", [3841, 4242]),
        check("P0010", [], forbid_pages=[4242]),
    ]

    inventory = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": PY,
        "recovery": {
            "arial": "non-ASCII +0x2d71 including already-CJK; ASCII kept; Arial punct map WITHOUT U+318F (maps to 开)",
            "simsun": "non-ASCII +0x4a2c only; watermark stripped",
            "reviewStatusPolicy": "no automatic procedure-verified; all body extracts are extracted-candidate / partial-evidence",
            "fitz982": "get_text(sort=True); NBSP/soft-hyphen normalized; header code at physical top",
        },
        "files": {
            "981_Boxster BoxsterS BoxsterGTS.pdf": {"pages": 4455, "toc_excluded": [3831, 3840], "detail_loaded": [3831, 4455]},
            "982_Boxster BoxsterS BoxsterGTS.pdf": {
                "pages": 8160,
                "loaded": [p982_start, p982_end],
                "index_refs_minmax": [min_982_idx, max_982_idx],
                "last_diagnostic_information_page": last_diag,
            },
            "981_Cayman CaymanS CaymanGTS.pdf": {"loaded": [3700, 4420]},
            "PDK factory t-shoot document.pdf": {"codes": pdk_codes},
        },
        "index_union_981_982": len(union),
        "grouped_981_header_owned": len(g981),
        "grouped_982_header_owned": len(g982),
        "grouped_cayman_header_owned": len(g_cay),
        "status_counts": dict(status),
        "extra_codes_header_owned": extra,
        "extra_count": len(extra),
        "page_attribution_audit": audits,
        "visually_inspected_not_auto_verified": [
            "pages/981-fitz-p3841.png",
            "pages/981-fitz-p3962.png",
            "pages/981-fitz-p3963.png",
            "pages/981-fitz-p3999.png",
            "pages/981-fitz-p4033.png",
            "pages/982-fitz-p4242.png",
        ],
        "elapsed_s": round(time.time() - t0, 1),
        "gaps": [
            "procedure-verified count is 0 by policy until human completes full step pages",
            "981 continuation after 功能 not treated as checks; other leftover CJK on continuation pages may still truncate (p3963 layout)",
            "982 pages beyond last Diagnostic information not treated as DME sheets",
            "cayman letter WM has 033500 only, no per-DTC PIWIS sheets",
            "982-only applicability to 2014 981 unverified",
        ],
    }
    (OUT / "source-inventory.json").write_text(json.dumps(json_safe(inventory), ensure_ascii=False, indent=1), encoding="utf-8")
    print("STATUS", dict(status), "extra", len(extra), "audits", audits, "last982", last_diag, flush=True)


if __name__ == "__main__":
    main()
