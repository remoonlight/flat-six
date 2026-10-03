"""Import local plaintext rule summaries for 981/982; no firmware or vehicle access."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

# Explicit module/source registry. New sources are optional; the initial DME/PDK pair is required.
SOURCES = [
    ("DME", "DME-flash-rules.xml"), ("PDK", "GETRIEBE-flash-rules.xml"),
    ("DME", "flash-rules/LL_EnginContrModul1UDS.xml"),
    ("Airbag", "flash-rules/AIRBAG_9x1.xml"), ("Airbag", "flash-rules/E5K1G.xml"),
    ("Gateway", "flash-rules/GATEWAY.xml"), ("功放", "flash-rules/VERSTAERKER.xml"),
    ("左前灯", "flash-rules/SCHEINWERFER_LINKS.xml"), ("右前灯", "flash-rules/SCHEINWERFER_RECHTS.xml"),
    ("左LED前灯", "flash-rules/SCHEINWERFER_LED_LINKS_9X1_SW.xml"),
    ("左LED前灯", "flash-rules/SCHEINWERFER_LED_LINKS_9X1_DS.xml"),
    ("右LED前灯", "flash-rules/SCHEINWERFER_LED_RECHTS_9X1_SW.xml"),
    ("右LED前灯", "flash-rules/SCHEINWERFER_LED_RECHTS_9X1_DS.xml"),
]


def families(rule, description, legacy):
    product = (rule.findtext("ISTSTAND/PRODUKTSCHLUESSEL") or "").strip()
    tokens = product.split("/")
    explicit = [g for g, prefixes in [("981", ("F82",)), ("982", ("F83", "F84"))]
                if any(re.fullmatch(r"F[0-9]{2}[A-Z0-9]{2}", t) and t.startswith(prefixes) for t in tokens)]
    if explicit:
        return explicit, "product-key"
    if not product and not legacy:
        return ["981", "982"], "shared-platform"
    if any(re.fullmatch(r"F[0-9]{2}[A-Z0-9]{2}", t) for t in tokens):
        return [], "product-key"
    # Retain the original description-based importer for its two legacy source files.
    if legacy:
        explicit = [g for g in ["981", "982"] if description.startswith((g + " ", g + "S "))]
        if explicit:
            return explicit, "description"
    return [], "product-key"


def current_conditions(node):
    result = []
    for ecu in node:
        if ecu.tag != "STEUERGERAET":
            raise ValueError("Unsupported current-ECU condition: " + ecu.tag)
        link = (ecu.findtext("LOGICALLINK") or "").strip()
        if not link:
            raise ValueError("Current-ECU logical link is required")
        conditions = []
        for field in ecu:
            if list(field):
                raise ValueError("Unsupported current-ECU condition: " + field.tag)
            if field.tag in ["LOGICALLINK", "BESCHREIBUNG"]:
                continue
            if field.tag not in ["PTNR", "HWTNR", "SWVERSION"] or list(field) or not (field.text or "").strip():
                raise ValueError("Unsupported current-ECU condition: " + field.tag)
            conditions.append({"field": field.tag, "values": [field.text.strip()]})
        result.append({"logicalLink": link, "conditions": conditions})
    return result


def build_index(source: Path) -> dict:
    rules = []
    sources = []
    for ecu, filename in SOURCES:
        if filename.startswith("flash-rules/") and not (source / filename).exists():
            continue
        raw = (source / filename).read_bytes()
        sources.append({"file": filename, "sha256": hashlib.sha256(raw).hexdigest()})
        for ordinal, rule in enumerate(ET.fromstring(raw).findall(".//FLASHREGEL")):
            description = (rule.findtext("BESCHREIBUNG") or "").strip()
            generations, family_evidence = families(rule, description, not filename.startswith("flash-rules/"))
            if not generations:
                continue
            conditions = []
            current_ecus = []
            for field in rule.find("ISTSTAND"):
                if field.tag == "STEUERGERAETE":
                    current_ecus = current_conditions(field)
                    continue
                value = (field.text or "").strip()
                if field.tag not in ["PRODUKTSCHLUESSEL", "AUSSTATTUNG", "MODELLJAHR"] or list(field) or (not value and field.tag != "PRODUKTSCHLUESSEL"):
                    raise ValueError("Unsupported rule condition: " + field.tag)
                conditions.append({"field": field.tag, "values": [value]})
            targets = []
            kind = "firmware"
            for target in rule.findall("SOLLSTAND/STEUERGERAETE/STEUERGERAET"):
                if any(child.tag not in ["LOGICALLINK", "PTNR", "BESCHREIBUNG", "SESSIONNAME"] or list(child) for child in target):
                    raise ValueError("Unsupported target condition: " + description)
                logical_link = (target.findtext("LOGICALLINK") or "").strip()
                number = (target.findtext("PTNR") or "").strip()
                session = (target.findtext("SESSIONNAME") or "").strip() or None
                if number.lower() == "noflash":
                    kind = "blocked"
                    number = None
                elif not number and session and "SESD_" in session:
                    kind = "dataset"
                    number = None
                elif not number:
                    raise ValueError("Missing target identity: " + description)
                if not logical_link and kind != "blocked":
                    raise ValueError("Missing target logical link: " + description)
                targets.append({"logicalLink": logical_link or None, "softwarePartNumber": number, "session": session})
            if not conditions or not targets:
                raise ValueError("Incomplete rule: " + description)
            for generation in generations:
                suffix = "-" + generation if len(generations) > 1 else ""
                rules.append({"id": f"{ecu.lower()}-{sources[-1]['sha256'][:12]}-{ordinal}{suffix}", "generation": generation,
                              "ecu": ecu, "description": description, "conditions": conditions, "currentEcus": current_ecus,
                              "targets": targets, "kind": kind, "familyEvidence": family_evidence, "source": filename})
    return {"schemaVersion": 1, "sources": sources, "rules": rules}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / ".local/piwis-obd-research")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/diagnostics/piwis-workshop/flash-index.json")
    args = parser.parse_args()
    index = build_index(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scratch = args.output.with_suffix(".tmp")
    scratch.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scratch.replace(args.output)
    print("Imported local research rules: " + str(len(index["rules"])))
    for gen in ["981", "982"]:
        print(gen + ": " + str(sum(r["generation"] == gen for r in index["rules"])))


if __name__ == "__main__":
    main()
