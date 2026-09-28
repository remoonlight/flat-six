"""Variant membership. Shared MENU is availability, not physical fit."""
from __future__ import annotations

import re

RE_981 = re.compile(r"(?<![0-9])981(?![0-9])")
RE_982 = re.compile(r"(?<![0-9])982(?![0-9])")
RE_991 = re.compile(r"(?<![0-9])991(?![0-9])")
RE_992 = re.compile(r"(?<![0-9])992(?![0-9])")
RE_718 = re.compile(r"(?<![0-9])718(?![0-9])")
RE_986 = re.compile(r"(?<![0-9])986(?![0-9])")
RE_987 = re.compile(r"(?<![0-9])987(?![0-9])")
RE_9X1 = re.compile(r"(?i)(^|_)9x1(_|$)")
RE_918 = re.compile(r"(?i)(?<![0-9A-Za-z])918s?(?![0-9A-Za-z])")
RE_BOXSTER = re.compile(r"(?i)boxster")
RE_CAYMAN = re.compile(r"(?i)cayman")
RE_CARRERA = re.compile(r"(?i)(?<![A-Za-z0-9])Carrera(?![A-Za-z0-9])")
RE_E2 = re.compile(r"(?<![A-Za-z0-9])E2(?![A-Za-z0-9])")
RE_G1 = re.compile(r"(?<![A-Za-z0-9])G1(?![A-Za-z0-9])")


def classify_name(name: str) -> dict:
    tokens = []
    if RE_981.search(name):
        tokens.append("981")
    if RE_982.search(name):
        tokens.append("982")
    if RE_718.search(name):
        tokens.append("718")
    if RE_991.search(name):
        tokens.append("991")
    if RE_992.search(name):
        tokens.append("992")
    if RE_986.search(name):
        tokens.append("986")
    if RE_987.search(name):
        tokens.append("987")
    if RE_9X1.search(name):
        tokens.append("9x1")
    if RE_918.search(name):
        tokens.append("918")
    if RE_BOXSTER.search(name):
        tokens.append("Boxster")
    if RE_CAYMAN.search(name):
        tokens.append("Cayman")
    if RE_CARRERA.search(name):
        tokens.append("Carrera")
    if RE_E2.search(name):
        tokens.append("E2")
    if RE_G1.search(name):
        tokens.append("G1")

    if "918" in tokens:
        return _row("excluded", "918", False, "explicit 918 token; not 981/982 target", tokens)
    if "991" in tokens and "981" not in tokens:
        return _row("excluded", "991", False, "explicit 991 token; 9X1 shared file, not 981/982 catalog", tokens)
    if "992" in tokens:
        return _row("excluded", "992", False, "explicit 992 token", tokens)
    if "986" in tokens or "987" in tokens:
        gen = "986" if "986" in tokens else "987"
        return _row("excluded", gen, False, f"explicit {gen} token", tokens)
    if "Carrera" in tokens:
        return _row("excluded", "Carrera", False, "explicit Carrera variant; not Boxster/Cayman 981/982 target", tokens)
    if "981" in tokens:
        return _row("confirmed", "981", True, "explicit 981 generation token in name (digit-bounded)", tokens)
    if "982" in tokens or "718" in tokens:
        gen = "982" if "982" in tokens else "718"
        return _row("confirmed", gen, True, f"explicit {gen} generation token in name (digit-bounded)", tokens)
    if "Boxster" in tokens or "Cayman" in tokens:
        return _row(
            "candidate",
            None,
            False,
            "Boxster/Cayman label without explicit 981/982/718 token; not accepted",
            tokens,
        )
    if "9x1" in tokens:
        return _row(
            "candidate",
            "9x1",
            False,
            "9x1 family tag covers 981+991 (+possibly 982); not 981-only membership",
            tokens,
        )
    return _row(
        "candidate",
        None,
        False,
        "in 9x1 containers without explicit 981/982/718/Cayman/Boxster generation code",
        tokens,
    )


def _row(membership: str, generation: str | None, accepted: bool, reason: str, tokens: list[str]) -> dict:
    return {
        "membership": membership,
        "generation": generation,
        "confidence": membership if membership != "ambiguous" else "ambiguous",
        "accepted": accepted,
        "reason": reason,
        "tokens": tokens,
    }
