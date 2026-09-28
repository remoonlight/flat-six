"""Classify by variant and parent module."""
from __future__ import annotations

try:
    from .membership import classify_name
except ImportError:
    from membership import classify_name


def classify_variant(module: str, name: str) -> dict:
    row = classify_name(name)
    if row["membership"] == "excluded":
        return row
    if module == "Verdeck_Targa" or module.lower() == "verdeck_targa":
        tokens = list(row["tokens"])
        if "Targa" not in tokens:
            tokens.append("Targa")
        return {
            "membership": "excluded",
            "generation": None,
            "confidence": "excluded",
            "accepted": False,
            "reason": "Verdeck_Targa entire module excluded; not Boxster/Cayman; shared MENU does not confirm fit",
            "tokens": tokens,
        }
    if module.startswith("BCM_") and ("E2" in row["tokens"] or "G1" in row["tokens"]):
        if not ({"981", "982", "9x1"} & set(row["tokens"])):
            return {
                "membership": "excluded",
                "generation": row["tokens"][0] if row["tokens"] else None,
                "confidence": "excluded",
                "accepted": False,
                "reason": "BCM standalone E2/G1 platform token without 9x1/981/982; other DSN family, not target",
                "tokens": row["tokens"],
            }
    return row
