"""Archive local PIWIS 981/982 reference containers; never connect to a vehicle."""
import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

try:
    from .import_piwis_workshop import SOURCES, build_index, families
except ImportError:
    from import_piwis_workshop import SOURCES, build_index, families

ROOT = Path(__file__).resolve().parents[1]
FAMILY_NAMES = {"981": "Boxster / Cayman (981)", "982": "718 Boxster / 718 Cayman (982)"}
SOURCE_PATHS = {name: "flash-data/regeln/" + ("DME.xml" if name == "DME-flash-rules.xml" else
               "GETRIEBE.xml" if name == "GETRIEBE-flash-rules.xml" else Path(name).name)
                for _, name in SOURCES}
SUPPORT = {"cts-ecu.xml", "cts-platform.xml", "vehicle-definition.xml", "vp.properties",
           "installation/version-info.properties", "flash-data/regeln/GETRIEBE_9x1_ABLAUF.xml",
           "flash-data/regeln/DME.asl", "flash-data/regeln/DME_UDS.asl",
           "flash-data/regeln/GETRIEBE.asl", "flash-data/regeln/GATEWAY.asl",
           "flash-data/regeln/LL_EnginContrModul1UDS.asl"}
CONFIGS = {"psm_rollenmodus_config.xml", "PAG_Gesamtfahrzeug_Rollenmodus.xml",
           "PAG_9x1_PDKit_Calibration_Prepare.xml", "PAG_9x1_PDK_Oelbefuellung.xml",
           "automatic_programming_config.xml", "action_programming_config.xml"}
# Shared 9x1 containers for systems present in the 981/982 research catalog.
# No vehicle/version qualification is inferred from their presence. Keep 991-specific systems out.
SHARED_SYSTEM_FILES = [
    (r"^FL_(?:9x1_)?BCM_HINTEN_", "后车身", "BCM_HINTEN"),
    (r"^FL_(?:9x1_)?BCM_VORNE_", "前车身", "BCM_VORNE"),
    (r"^FL_9x1_EPB_", "电子驻车制动", "PARKBREMSE"),
    (r"^FL_9x1_EPS_", "转向助力", "EPS"),
    (r"^FL_9x1_PASM\+PADM_", "PASM / PADM", "PASM"),
    (r"^FL_9x1_BKE_", "空调控制面板", "BEDIENKLIMAEINHEIT"),
    (r"^FL_E2_Kombilenkstockmodul_", "转向柱模块", "KLSM"),
    (r"^FL_9x1_CAN_CAN-Gateway_", "Gateway（历史容器）", "GATEWAY"),
]


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Unsafe archive path: " + value)
    return path


def token_match(number, name):
    return bool(number and re.search(r"(?<![A-Z0-9])" + re.escape(number) + r"(?![A-Z0-9])", name))


def rule_matches_file(rule, name):
    return any(token_match(t["softwarePartNumber"], name) or
               (t["session"] and t["session"].startswith("SESD_") and
                name == "DB_" + t["session"][5:] + "_container.pdx") for t in rule["targets"])


def selection(inventory, index):
    selected = []
    source_paths = set(SOURCE_PATHS.values())
    seen = set()
    for file in inventory["files"]:
        rel = str(safe_relative(file["relativePath"]))
        if rel in seen:
            raise ValueError("Duplicate source path: " + rel)
        seen.add(rel)
        name = PurePosixPath(rel).name
        suffix = PurePosixPath(rel).suffix.lower()
        rules = [r for r in index["rules"] if rule_matches_file(r, name)] if suffix in {".pdx", ".odx"} else []
        named = re.match(r"^FL_(981|982)_", name)
        shared = next(((label, link) for pattern, label, link in SHARED_SYSTEM_FILES if re.match(pattern, name, re.I)), None) if suffix in {".pdx", ".odx"} else None
        if rules or named or shared or rel in source_paths or rel in SUPPORT or (rel.startswith("cts-config/") and name in CONFIGS):
            selected.append({**file, "matchedRuleIds": [r["id"] for r in rules],
                             "namedFamily": named.group(1) if named else None,
                             "sharedSystem": {"name": shared[0], "logicalLink": shared[1], "candidateFamilies": ["981", "982"],
                                              "evidence": "9x1 shared system/container filename; exact vehicle applicability unverified"} if shared else None,
                             "selectionBasis": "rule-target" if rules else "explicit-family-filename" if named else "shared-platform-system-unverified" if shared else "support-source"})
    missing = source_paths - {f["relativePath"] for f in selected}
    if missing:
        raise ValueError("Required source files absent from inventory: " + str(sorted(missing)))
    return selected


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def ssh_options(known_hosts):
    return ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + str(known_hosts), "-o", "ConnectTimeout=10"]


def fetch_files(selected, inventory, archive, host, known_hosts):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", host):
        raise ValueError("Invalid SSH host")
    # Hash the exact selected source files. No source mutation and no diagnostic application invocation.
    source_paths = [f.get("sourcePath", inventory["platformRoot"].replace("\\", "/") + "/" + f["relativePath"]) for f in selected]
    if any(any(c in p for c in ["'", '"', "`", "$", "\n", "\r"]) for p in source_paths):
        raise ValueError("Unsupported remote path")
    ps = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n$paths=@(" + ",".join("'" + p + "'" for p in source_paths) + ")\n"
    ps += "@($paths | ForEach-Object { $f=Get-Item -LiteralPath $_ -ErrorAction Stop; [ordered]@{sourcePath=$_;sha256=(Get-FileHash -LiteralPath $_ -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower();bytes=$f.Length;lastWriteTimeUtc=$f.LastWriteTimeUtc.ToString('o')} }) | ConvertTo-Json -Depth 4 -Compress\n"
    result = subprocess.run(["ssh", *ssh_options(known_hosts), host, "powershell", "-NoProfile", "-NonInteractive", "-Command", "-"],
                            input=ps.encode("utf-8"), capture_output=True, timeout=120)
    scratch = archive.parent / ".scratch" / archive.name
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "source-hashes.stderr").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError("Remote hash inventory failed; see " + str(scratch / "source-hashes.stderr"))
    remote_hashes = json.loads(result.stdout.decode("utf-8-sig"))
    write_json(archive / "source-hashes.json", remote_hashes)
    by_path = {f["sourcePath"]: f for f in remote_hashes}
    if set(by_path) != set(source_paths):
        raise ValueError("Remote hash response does not cover the selected files")
    needed = []
    for f, source in zip(selected, source_paths):
        h = by_path[source]
        if h["bytes"] != f["bytes"] or h["lastWriteTimeUtc"] != f["lastWriteTimeUtc"]:
            raise ValueError("Source changed after inventory: " + f["relativePath"])
        f["sha256"] = h["sha256"]
        local = archive / "files" / safe_relative(f["relativePath"])
        local.parent.mkdir(parents=True, exist_ok=True)
        if local.exists() and hashlib.sha256(local.read_bytes()).hexdigest() != f["sha256"]:
            raise ValueError("Existing archived original differs; use a new --archive snapshot: " + f["relativePath"])
        if not local.exists():
            needed.append((f, source, local))
    # One connection per directory/batch. Transfer only the selected original files.
    batches = {}
    for f, source, local in needed:
        batches.setdefault(local.parent, []).append((f, source, local))
    complete = len(selected) - len(needed)
    for directory, entries in batches.items():
        for start in range(0, len(entries), 12):
            batch = entries[start:start + 12]
            quoted = any(" " in source for _, source, _ in batch)
            sources = [host + ':' + ('"' + source + '"' if " " in source else source) for _, source, _ in batch]
            # Windows legacy SCP preserves a quote in its requested basename comparison.
            # For paths with spaces, trust the authenticated source and validate exact size/hash afterward.
            result = subprocess.run(["scp", "-O", *(["-T"] if quoted else []), *ssh_options(known_hosts), *sources, str(directory)], capture_output=True, timeout=180)
            if result.returncode:
                (scratch / "transfer.stderr").write_bytes(result.stderr)
                raise RuntimeError("SCP failed; see " + str(scratch / "transfer.stderr"))
            for f, _, local in batch:
                if local.stat().st_size != f["bytes"] or hashlib.sha256(local.read_bytes()).hexdigest() != f["sha256"]:
                    raise ValueError("Transferred file hash mismatch: " + f["relativePath"])
            complete += len(batch)
            print(f"Archived and hash-verified {complete}/{len(selected)}", flush=True)


def odx_metadata(raw, member):
    root = ET.fromstring(raw)
    # Only descriptive identity/revision metadata. No request, security material or flash payload export.
    def text(node, field):
        return node.findtext(field)
    revisions = [{"date": text(n, "DATE"), "revision": text(n, "REVISION-LABEL"), "state": text(n, "STATE")}
                 for n in root.iter("DOC-REVISION")]
    idents = [{"name": text(n, "SHORT-NAME"), "values": [v.text for v in n.iter("IDENT-VALUE") if v.text]}
              for n in root.iter("OWN-IDENT")]
    created = re.search(rb"created by [^\r\n<>]*? on (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)", raw[:4096])
    return {"member": member, "odxModelVersion": root.get("MODEL-VERSION"),
            "partNumbers": sorted({n.text.strip() for n in root.iter("PARTNUMBER") if n.text and n.text.strip()}),
            "sessions": [text(n, "SHORT-NAME") for n in root.iter("SESSION")],
            "sessionDescriptions": [{"name": text(n, "SHORT-NAME"),
                                     "sessionReference": n.find("SESSION-SNREF").get("SHORT-NAME") if n.find("SESSION-SNREF") is not None else None}
                                    for n in root.iter("SESSION-DESC")],
            "ownIdentifiers": idents, "documentRevisions": revisions,
            "exportedAt": created.group(1).decode("ascii") if created else None}


def file_metadata(path):
    if path.suffix.lower() == ".odx":
        return {"odx": [odx_metadata(path.read_bytes(), path.name)]}
    if path.suffix.lower() != ".pdx":
        return {}
    with zipfile.ZipFile(path) as package:
        members = [{"name": f.filename, "bytes": f.file_size, "crc32": f"{f.CRC:08x}"} for f in package.infolist()]
        failed = package.testzip()
        if failed:
            raise ValueError("PDX CRC failure: " + str(path) + " / " + failed)
        odx = []
        if "index.xml" in package.namelist():
            catalog = ET.fromstring(package.read("index.xml"))
            missing = [n.text for n in catalog.iter("FILE") if n.text and n.text not in package.namelist()]
            if missing:
                raise ValueError("PDX catalog member missing: " + str(missing))
        for entry in package.infolist():
            if entry.filename.lower().endswith((".odx-f", ".odx")):
                if entry.file_size > 64 * 1024 * 1024:
                    raise ValueError("Unexpected ODX metadata size")
                odx.append(odx_metadata(package.read(entry), entry.filename))
        # Keep containers intact. Never extract their BIN payloads onto disk.
        return {"members": members, "odx": odx}


def history(path):
    root = ET.fromstring(path.read_bytes())
    result = []
    for n in root.findall("AENDERUNGSHISTORIE/AENDERUNGSSTAND"):
        date = (n.findtext("DATUM") or "").strip()
        try:
            iso = datetime.strptime(date, "%d.%m.%Y").date().isoformat()
        except ValueError:
            iso = None
        result.append({"dateRaw": date, "date": iso, "version": (n.findtext("VERSION") or "").strip(),
                       "description": (n.findtext("BESCHREIBUNG") or "").strip()})
    return result


def csv_write(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def cell(value):
    return str(value or "未记录").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def properties(path):
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def file_system_hint(name):
    for token, label in [("Kombiinstrument", "仪表"), ("_DME_", "DME"), ("_A1_", "PDK"),
                         ("Verstaerker", "功放"), ("SWSG", "前灯控制模块"), ("_LED_", "LED前灯")]:
        if token.lower() in name.lower():
            return label
    return "共享配套文件/系统见原件"


def finalize(archive, inventory, selected, index, research, docs):
    expected = {h["sourcePath"]: h for h in json.loads((archive / "source-hashes.json").read_text(encoding="utf-8"))}
    for f in selected:
        local = archive / "files" / safe_relative(f["relativePath"])
        source = f.get("sourcePath", inventory["platformRoot"].replace("\\", "/") + "/" + f["relativePath"])
        digest = hashlib.sha256(local.read_bytes()).hexdigest()
        if local.stat().st_size != f["bytes"] or digest != expected[source]["sha256"]:
            raise ValueError("Archive integrity failure: " + f["relativePath"])
        f.update({"sha256": digest, "metadata": file_metadata(local)})
    raw_sources = {filename: archive / "files" / relative for filename, relative in SOURCE_PATHS.items()}
    for s in index["sources"]:
        if hashlib.sha256(raw_sources[s["file"]].read_bytes()).hexdigest() != s["sha256"]:
            raise ValueError("Archived rule file differs from research index: " + s["file"])
    histories = {name: history(path) for name, path in raw_sources.items()}
    rules = []
    for r in index["rules"]:
        files = [f["relativePath"] for f in selected if r["id"] in f["matchedRuleIds"]]
        source_history = histories[r["source"]]
        dates = [h["date"] for h in source_history if h["date"]]
        rules.append({**r, "modelFamilyName": FAMILY_NAMES[r["generation"]], "archivedFiles": files,
                      "ruleTableHistory": source_history,
                      "firmwareReleaseDate": None, "vehicleApplicability": "unverified",
                      "archiveStatus": "blocked-no-firmware" if r["kind"] == "blocked" else "file-present" if files else "file-missing",
                      "latestRuleTableDate": max(dates) if dates else None})
    missing = [r["id"] for r in rules if r["archiveStatus"] == "file-missing"]
    if missing:
        raise ValueError("Non-blocked rule has no archived target: " + str(missing))
    now = datetime.now(timezone.utc).isoformat()
    previous = json.loads((archive / "manifest.json").read_text(encoding="utf-8")) if (archive / "manifest.json").exists() else {}
    application = properties(archive / "files/installation/version-info.properties")
    platform = properties(archive / "files/vp.properties")
    manifest = {"schemaVersion": 1, "archivedAtUtc": previous.get("archivedAtUtc", now), "verifiedAtUtc": now,
                "sourceObservedAtUtc": inventory["observedAtUtc"],
                "sourceHostname": inventory["hostname"], "sourcePlatformRoot": inventory["platformRoot"],
                "generations": ["981", "982"], "executionEnabled": False,
                "versions": {"pidtApplication": application.get("version.application"),
                             "pidtContentScripts": application.get("version.content.scripts"),
                             "platformScripts": platform.get("version.BR.Scripts")},
                "timestampSemantics": {"lastWriteTimeUtc": "Source filesystem timestamp, not firmware release date",
                                       "documentRevisions": "ODX document/template history, not vehicle software release date",
                                       "exportedAt": "ODX exporter creation timestamp, not firmware release date",
                                       "ruleTableHistory": "Shared rule table revision history, not a per-target release date"},
                "files": selected, "rules": rules}
    write_json(archive / "manifest.json", manifest)
    write_json(archive / "flash-index.json", index)
    write_json(archive / "rule-table-histories.json", histories)
    docs.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in selected:
        matched = [r for r in rules if r["id"] in f["matchedRuleIds"]]
        gens = sorted({r["generation"] for r in matched} | ({f["namedFamily"]} if f["namedFamily"] else set()) |
                      ({"981", "982"} if f["sharedSystem"] else set()))
        odx = f["metadata"].get("odx", [])
        rows.append({"file": f["relativePath"], "systems": " / ".join(sorted({r["ecu"] for r in matched})) or
                     (f["sharedSystem"]["name"] if f["sharedSystem"] else file_system_hint(f["relativePath"])),
                     "generations": " / ".join(gens) or "981 / 982（共享配套）",
                     "modelNames": " / ".join(FAMILY_NAMES[g] for g in gens) or "共享平台资料",
                     "variantDescriptions": " ; ".join(sorted({r["description"] for r in matched})),
                     "selectionBasis": f["selectionBasis"],
                     "familyEvidence": " / ".join(sorted({r["familyEvidence"] for r in matched} |
                                                           ({"file-name"} if f["namedFamily"] else set()) |
                                                           ({"shared-platform-system-unverified"} if f["sharedSystem"] else set()))),
                     "vehicleApplicability": "未核实",
                     "softwarePartNumbers": " / ".join(sorted({n for o in odx for n in o["partNumbers"]})),
                     "softwareIdentifiers": " ; ".join(sorted({n + '=' + '/'.join(i['values']) for o in odx for i in o['ownIdentifiers'] if (n := i['name'])})),
                     "softwareVersionCounters": " / ".join(sorted({v for o in odx for i in o['ownIdentifiers'] if 'ApplSwVer' in (i['name'] or '') for v in i['values']})),
                     "sessions": " / ".join(sorted({s for o in odx for s in o["sessions"] if s})),
                     "sessionDescriptionNames": " / ".join(sorted({s['name'] for o in odx for s in o['sessionDescriptions'] if s['name']})),
                     "sourceLastWriteUtc": f["lastWriteTimeUtc"], "sourceCreationUtc": f["creationTimeUtc"],
                     "odxExportDates": " / ".join(sorted({o["exportedAt"] for o in odx if o["exportedAt"]})),
                     "odxDocumentLastDate": " / ".join(sorted({max(v['date'] for v in o['documentRevisions'] if v['date']) for o in odx if any(v['date'] for v in o['documentRevisions'])})),
                     "firmwareReleaseDate": "未核实", "bytes": f["bytes"], "sha256": f["sha256"]})
    csv_write(archive / "files.csv", rows, list(rows[0]))
    rule_rows = [{"ruleId": r["id"], "generation": r["generation"], "modelName": r["modelFamilyName"], "system": r["ecu"],
                  "variant": r["description"], "kind": r["kind"], "familyEvidence": r["familyEvidence"],
                  "conditions": json.dumps(r["conditions"], ensure_ascii=False),
                  "currentEcuConditions": json.dumps(r["currentEcus"], ensure_ascii=False),
                  "targets": json.dumps(r["targets"], ensure_ascii=False), "files": " ; ".join(r["archivedFiles"]),
                  "ruleSource": r["source"], "ruleTableLatestDate": r["latestRuleTableDate"],
                  "ruleTableVersions": " / ".join(h["version"] for h in r["ruleTableHistory"][:3]),
                  "firmwareReleaseDate": "未核实", "archiveStatus": r["archiveStatus"]} for r in rules]
    csv_write(archive / "rules.csv", rule_rows, list(rule_rows[0]))
    timeline_rows = [{"source": s, **h} for s, hs in histories.items() for h in hs]
    csv_write(archive / "timeline.csv", timeline_rows, ["source", "dateRaw", "date", "version", "description"])
    firmware = [f for f in selected if PurePosixPath(f["relativePath"]).suffix in {".pdx", ".odx"}]
    rel = archive.relative_to(ROOT).as_posix() if archive.is_relative_to(ROOT) else str(archive)
    lines = ["# PIWIS 981 / 982 文件归档", "", "归档日期：" + manifest["archivedAtUtc"] + "。仅离线资料归档。", "",
             "原件位置：`" + rel + "/files/`。原始固件容器保持完整，共享规则原件可能包含其他车型分支；其他车型专属容器未复制。本轮扫描范围为共享 9x1 平台的 flash-data 与配套维护配置，不代表所有外部更新介质。", "",
             f"本次保存 {len(selected)} 个原件，其中 {len(firmware)} 个 PDX/ODX 容器，{sum(f['bytes'] for f in selected)/1048576:.2f} MiB。",
             f"包含 {len(rules)} 条 981/982 展示记录；共享规则可能在两个车系下各显示一次，不等于固件数量。所有原件与主机 SHA-256 相符，PDX 内部 CRC 已检查。", "",
             "- [981/982 文件与版本 Markdown 索引](981-982-files.md)",
             "- [规则修订时间线 Markdown](981-982-timeline.md)",
             "- [文件、系统、车型、版本及时间 CSV](../../../../" + rel + "/files.csv)",
             "- [规则、车型配置与文件对应表](../../../../" + rel + "/rules.csv)",
             "- [规则版本时间线 CSV](../../../../" + rel + "/timeline.csv)",
             "- [完整归档清单与容器元数据](../../../../" + rel + "/manifest.json)", "",
             "## 版本与时间的含义", "", f"PIWIS 应用版本 `{manifest['versions']['pidtApplication']}`；共享脚本版本 `{manifest['versions']['platformScripts']}`。二者不等于 ECU 固件版本，原始 version-info.properties 与 vp.properties 已保留。",
             "源文件修改时间、创建时间、ODX 导出时间、ODX 文档修订历史、规则表修订日期分列记录。ODX 历史可能继承旧模板，不能当作本车型发布日期。未核实的固件发布日期留空/标为未核实，不从零件号或文件时间推算。", "",
             "## 系统与文件覆盖", "", "| 系统 | 981 规则 | 982 规则 | 文件依据 |", "|---|---:|---:|---|"]
    for ecu in sorted({r["ecu"] for r in rules}):
        counts = [sum(r["ecu"] == ecu and r["generation"] == g for r in rules) for g in ["981", "982"]]
        lines.append(f"| {ecu} | {counts[0]} | {counts[1]} | 目标软件号/数据集 session；共享适用性仍待核实 |")
    for system in sorted({f["sharedSystem"]["name"] for f in selected if f["sharedSystem"]}):
        count = sum(f["sharedSystem"] is not None and f["sharedSystem"]["name"] == system for f in selected)
        lines.append(f"| {system} | — | — | {count} 个共享系统候选容器；尚未建立 981/982 精确版本适用性 |")
    cluster = [f for f in selected if file_system_hint(f["relativePath"]) == "仪表"]
    lines += [f"| 仪表 | — | — | 已保存 {len(cluster)} 个文件名明确标注 981 的容器；未建立精确车型/硬件规则映射 |",
              "| PCM | — | — | 本轮未找到更新 CD/SD 介质，保留既有官方通告/菜单依据 |", "",
              "文件名明确标出 981/982、但未被当前规则引用的历史容器也保留；其身份来自文件名，不表示当前车辆适用。`NO FLASH` 分支只归档限制规则，不生成固件目标。", "",
              "## 规则表版本时间节点", "", "| 来源 | 时间范围（规则表） | 最近日期的版本 | 最近描述原文 |", "|---|---|---|---|"]
    for name, hs in histories.items():
        valid = sorted((h for h in hs if h["date"]), key=lambda h: h["date"])
        latest = valid[-1] if valid else {}
        span = valid[0]["date"] + " ～ " + valid[-1]["date"] if valid else "未记录"
        lines.append("| " + " | ".join(cell(v) for v in [name, span, latest.get("version"), latest.get("description")]) + " |")
    lines += ["", "## 其他车型", "", "[其他车型 Markdown 索引](other-models.md) 仅保存文件名、系统线索、源时间和原始规则描述，不保存其专属 PDX/ODX。共享容器若被 981/982 规则引用，或属于本轮明确列出的共享系统候选，保留共同原件并标明待核实依据，不据此宣称车型适用。", "",
              "## 复核", "", "```powershell", "python scripts/archive_piwis_workshop.py --verify", "```", "",
              "本目录 Markdown 为项目索引；`.local/piwis-archive/` 内原件、清单与 CSV 仅保存在当前项目电脑，不随 Git 或安装包发布。归档不授予刷写资格。"]
    (docs / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    file_lines = ["# 981 / 982 文件、系统、车型、版本与时间", "",
                  "原件保持完整；下表为本次源快照的文件索引。车型是规则关联/文件名证据，具体变体条件见 rules.csv。共享平台记录不证明当前车辆适用。",
                  "软件版本计数只取 ODX 的 ApplSwVer 标识；未记录则保留软件号、session 和原始文件名，不能将规则版本当成软件版本。",
                  "日期分别表示文件修改时间、ODX 导出时间和文档修订时间，不是固件发布日期。ODX 时间缺少时区时保留原文。", "",
                  "| 文件原件 | 系统 | 研究车系 / 名称 | 车型证据（适用性均待核实） | 软件号 | 软件版本计数 | 源修改 UTC | ODX 导出时间 | 文档最近修订 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if PurePosixPath(r["file"]).suffix not in {".pdx", ".odx"}:
            continue
        link = "[" + PurePosixPath(r["file"]).name + "](../../../../" + rel + "/files/" + r["file"] + ")"
        file_lines.append("| " + " | ".join(cell(v) for v in [link, r["systems"], r["generations"] + " · " + r["modelNames"], r["familyEvidence"],
                            r["softwarePartNumbers"], r["softwareVersionCounters"], r["sourceLastWriteUtc"],
                            r["odxExportDates"], r["odxDocumentLastDate"]]) + " |")
    file_lines += ["", "## 配套原件", "", "| 原件 | 源修改 UTC | SHA-256 |", "|---|---|---|"]
    for r in rows:
        if PurePosixPath(r["file"]).suffix not in {".pdx", ".odx"}:
            link = "[" + r["file"] + "](../../../../" + rel + "/files/" + r["file"] + ")"
            file_lines.append("| " + " | ".join(cell(v) for v in [link, r["sourceLastWriteUtc"], r["sha256"]]) + " |")
    (docs / "981-982-files.md").write_text("\n".join(file_lines) + "\n", encoding="utf-8")
    timeline_lines = ["# 981 / 982 关联规则表的修订时间线", "",
                      "共享规则表可能包含 991 等分支；整表修订日期不是每一个 981/982 固件目标的发布日期。下面保留修订描述原文与表版本，固件发布日期尚未核实。", "",
                      "| 规则来源 | 日期原文 | 日期 ISO（可解析时） | 表版本 | 修订描述原文 |", "|---|---|---|---|---|"]
    for r in sorted(timeline_rows, key=lambda r: (r["date"] or "", r["source"], r["version"])):
        timeline_lines.append("| " + " | ".join(cell(r[k]) for k in ["source", "dateRaw", "date", "version", "description"]) + " |")
    (docs / "981-982-timeline.md").write_text("\n".join(timeline_lines) + "\n", encoding="utf-8")
    write_other_models(docs, inventory, selected, research)
    print(f"Verified archive: {len(selected)} originals; {len(firmware)} PDX/ODX; {len(rules)} rules", flush=True)


def write_other_models(docs, inventory, selected, research):
    paths = {f["relativePath"] for f in selected}
    other_files = [f for f in inventory["files"] if f["relativePath"] not in paths and PurePosixPath(f["relativePath"]).suffix.lower() in {".pdx", ".odx"}]
    lines = ["# 其他车型：仅 Markdown 索引", "", "观察时间：" + inventory["observedAtUtc"], "",
             "以下为共享 9x1 安装目录中未纳入 981/982 归档的容器。文件名的 991、991II、970 等仅为分类线索；不凭软件号前缀推定适用车型。具体 VIN、代际、年款、硬件和版本仍需匹配原始规则。",
             "991.1 / 991.2 接口契约另见项目 `docs/piwis-991-interface.md`；本次未创建其他车型执行入口。", "",
             "## 其他车型/未确定车型文件线索", "", "版本末尾标识直接取文件名，未解释其软件/数据集含义；不替代实际 ECU 软件版本。", "",
             "| 文件名 | 车型/系统线索（仅文件名） | 末尾版本标识（文件名原文） | 源修改时间 UTC | 大小 |", "|---|---|---|---|---|"]
    for f in other_files:
        name = PurePosixPath(f["relativePath"]).name
        hints = " / ".join(p for p in name.removesuffix("_container.pdx").split("_")[:3] if p not in {"FL", "DB"})
        tail = "_".join(PurePosixPath(name).stem.removesuffix("_container").split("_")[-2:])
        lines.append("| " + " | ".join(cell(v) for v in [name, hints, tail, f["lastWriteTimeUtc"], f["bytes"]]) + " |")
    lines += ["", "## 原规则中的其他车型分支", "", "产品键明确指向其他车型的行仅记于本 Markdown。9x1 通用规则不自动归为 991.1 或 991.2。", "",
              "| 规则源 / 系统 | 原始车型描述 | 产品键 | 年款原文 | 目标软件 / session |", "|---|---|---|---|---|"]
    extra_sources = [("SWA3 Master", "flash-rules/SWA3_Master.xml"), ("SWA3 Slave", "flash-rules/SWA3_Slave.xml")]
    for ecu, filename in [*SOURCES, *extra_sources]:
        path = research / filename
        if not path.exists():
            continue
        for r in ET.fromstring(path.read_bytes()).findall(".//FLASHREGEL"):
            desc = (r.findtext("BESCHREIBUNG") or "").strip()
            gens, _ = families(r, desc, not filename.startswith("flash-rules/"))
            if gens:
                continue
            targets = [" / ".join(filter(None, [t.findtext("PTNR"), t.findtext("SESSIONNAME")])) for t in r.findall("SOLLSTAND/STEUERGERAETE/STEUERGERAET")]
            lines.append("| " + " | ".join(cell(v) for v in [filename + " / " + ecu, desc, r.findtext("ISTSTAND/PRODUKTSCHLUESSEL"), r.findtext("ISTSTAND/MODELLJAHR"), " ; ".join(targets)]) + " |")
    lines += ["", "## 相关规则表的版本节点（共享整表）", "",
              "这些是规则表修订节点，不是上述容器的固件发布日期。", "",
              "| 规则来源 | 日期原文 | 表版本 | 说明原文 |", "|---|---|---|---|"]
    for _, filename in [*SOURCES, *extra_sources]:
        path = research / filename
        if path.exists():
            revisions = history(path)
            if revisions:
                # Shared table history is retained in full in 981/982 timeline; other-model MD keeps the current head.
                h = revisions[0]
                lines.append("| " + " | ".join(cell(v) for v in [filename, h["dateRaw"], h["version"], h["description"]]) + " |")
    lines += ["", f"本索引记录 {len(other_files)} 个未归档容器；时间是主机文件修改时间，固件发布日期未核实。文件名中的版本标识保留原文。其他车型专属文件均未复制。"]
    (docs / "other-models.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_archive(archive):
    """Verify a portable archive using only its retained originals and provenance."""
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    expected = {h["sourcePath"]: h for h in json.loads((archive / "source-hashes.json").read_text(encoding="utf-8"))}
    index = json.loads((archive / "flash-index.json").read_text(encoding="utf-8"))
    by_file = {f["relativePath"]: f for f in manifest["files"]}
    actual = {p.relative_to(archive / "files").as_posix() for p in (archive / "files").rglob("*") if p.is_file()}
    if actual != set(by_file):
        raise ValueError("Archive file set differs from manifest")
    for rel, f in by_file.items():
        path = archive / "files" / safe_relative(rel)
        h = expected[f["sourcePath"]]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.stat().st_size != f["bytes"] or f["bytes"] != h["bytes"] or digest != f["sha256"] or digest != h["sha256"]:
            raise ValueError("Archive integrity failure: " + rel)
        if file_metadata(path) != f["metadata"]:
            raise ValueError("Archived container metadata differs: " + rel)
    if len(expected) != len(by_file):
        raise ValueError("Source hash coverage differs from archive")
    application = properties(archive / "files/installation/version-info.properties")
    platform = properties(archive / "files/vp.properties")
    if manifest["versions"] != {"pidtApplication": application.get("version.application"),
                                "pidtContentScripts": application.get("version.content.scripts"),
                                "platformScripts": platform.get("version.BR.Scripts")}:
        raise ValueError("Version provenance differs from retained properties")
    for source in index["sources"]:
        if by_file[SOURCE_PATHS[source["file"]]]["sha256"] != source["sha256"]:
            raise ValueError("Rule source hash mismatch")
    rules = {r["id"]: r for r in manifest["rules"]}
    if len(rules) != len(index["rules"]):
        raise ValueError("Rule coverage differs from retained index")
    for r in index["rules"]:
        if r["generation"] not in FAMILY_NAMES or any(rules[r["id"]][k] != v for k, v in r.items()):
            raise ValueError("Rule scope or content differs from retained index")
        links = rules[r["id"]]["archivedFiles"]
        if r["kind"] != "blocked" and not links:
            raise ValueError("Target container missing: " + r["id"])
        for link in links:
            if link not in by_file or not rule_matches_file(r, PurePosixPath(link).name):
                raise ValueError("Rule/file association differs: " + r["id"])
            odx = by_file[link]["metadata"]["odx"]
            numbers = {n for o in odx for n in o["partNumbers"]}
            sessions = {s for o in odx for s in o["sessions"]} | {s["name"] for o in odx for s in o["sessionDescriptions"]}
            for target in r["targets"]:
                number, session = target["softwarePartNumber"], target["session"]
                if token_match(number, PurePosixPath(link).name) and number not in numbers:
                    raise ValueError("Rule/ODX software identity mismatch: " + link)
                if r["kind"] == "dataset" and session not in sessions:
                    raise ValueError("Rule/ODX dataset descriptor mismatch: " + link)
    with (archive / "files.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_files = list(csv.DictReader(stream))
    if len(csv_files) != len(by_file) or {r["file"] for r in csv_files} != set(by_file) or any(r["sha256"] != by_file[r["file"]]["sha256"] for r in csv_files):
        raise ValueError("CSV file index differs from manifest")
    with (archive / "rules.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rules = list(csv.DictReader(stream))
    if len(csv_rules) != len(rules) or {r["ruleId"] for r in csv_rules} != set(rules):
        raise ValueError("CSV rule identities differ from manifest")
    for row in csv_rules:
        r = rules[row["ruleId"]]
        if (r["generation"] != row["generation"] or r["modelFamilyName"] != row["modelName"] or r["ecu"] != row["system"] or
            r["conditions"] != json.loads(row["conditions"]) or r["currentEcus"] != json.loads(row["currentEcuConditions"]) or
            r["targets"] != json.loads(row["targets"])):
            raise ValueError("CSV model/system/condition/target differs: " + row["ruleId"])
    print(f"PASS self-contained archive: {len(by_file)} files, {len(rules)} rules; source hashes, PDX CRC/catalog, metadata and links verified")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--research", type=Path, default=ROOT / ".local/piwis-obd-research")
    p.add_argument("--inventory", type=Path, default=ROOT / ".local/piwis-obd-research/archive-inventory.stdout")
    p.add_argument("--archive", type=Path, default=ROOT / ".local/piwis-archive/981-982/2026-10-01")
    p.add_argument("--docs", type=Path, default=ROOT / "data/seed/piwis/archive")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--catalog", action="store_true", help="Rebuild metadata/Markdown from retained originals and local research inventory")
    p.add_argument("--host")
    p.add_argument("--known-hosts", type=Path)
    args = p.parse_args()
    if args.verify and not args.fetch and not args.catalog:
        verify_archive(args.archive)
        return
    inventory = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    index = build_index(args.research)
    selected = selection(inventory, index)
    args.archive.mkdir(parents=True, exist_ok=True)
    write_json(args.archive / "selection.json", {"sourceObservedAtUtc": inventory["observedAtUtc"], "files": selected})
    print(f"Selected {len(selected)} originals, {sum(f['bytes'] for f in selected)/1048576:.2f} MiB", flush=True)
    if args.fetch:
        if not args.host or not args.known_hosts:
            p.error("--fetch requires --host and --known-hosts")
        fetch_files(selected, inventory, args.archive, args.host, args.known_hosts)
    if args.catalog or args.fetch:
        finalize(args.archive, inventory, selected, index, args.research, args.docs)
        verify_archive(args.archive)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
