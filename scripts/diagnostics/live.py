from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import BAUD, VLIKER_MAC
from .allowlist import blocked_reason
from .catalog import live_allowed_hex, load_catalog, operation, profile_by_id
from .decode import decode_payload
from .elm import ElmClient, ElmError
from .qualification import normalize_identity_value

DTC_OPERATION_IDS = frozenset({"dme-dtc", "gw-dtc"})


def identity_ascii_matches(got, expected) -> bool:
    g = normalize_identity_value(got)
    e = normalize_identity_value(expected if isinstance(expected, str) else None)
    return g is not None and e is not None and g == e

REPO_ROOT = Path(__file__).resolve().parents[2]


def find_vlinker_port(list_ports) -> str:
    mac = VLIKER_MAC.upper()
    hits = []
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper().replace(":", "")
        if mac in hwid:
            hits.append(p.device)
    if len(hits) != 1:
        raise ElmError(f"vlinker-port-not-unique:{hits}")
    return hits[0]


def run_read(
    profile_id: str,
    operation_id: str,
    *,
    serial_module=None,
    list_ports=None,
    port=None,
    budget_s: float = 60.0,
    out_dir: Path | None = None,
) -> dict:
    catalog = load_catalog()
    profile = profile_by_id(catalog, profile_id)
    op = operation(profile, operation_id)
    if not op.get("liveAllowed"):
        raise ElmError("operation-not-live-allowed")
    if operation_id in DTC_OPERATION_IDS:
        from .sessions import run_read_dtc

        return run_read_dtc(
            profile_id,
            operation_id,
            serial_module=serial_module,
            list_ports=list_ports,
            port=port,
            budget_s=budget_s,
            out_dir=out_dir,
        )
    req = bytes.fromhex(op["requestHex"])
    allowed = live_allowed_hex(profile)
    reason = blocked_reason(req, allowed)
    if reason:
        raise ElmError(reason)
    started = datetime.now(timezone.utc)
    deadline_mono_budget = budget_s
    own_port = port is None
    restoration = {"errors": ["not-opened"]}
    client = None
    result = {
        "ok": False,
        "profileId": profile_id,
        "operationId": operation_id,
        "startedUtc": started.isoformat(),
        "hostStamp": started.isoformat(),
        "adapter": None,
        "payload_hex": None,
        "decoded": None,
        "error": None,
        "transportCaveat": (
            "vLinker clone ELM327 v2.3 CAF0/ATH1/ATS1/CFC/FC/CSM0 ISO-TP not proven on this car; "
            "this path uses official AT commands with explicit SF PCI, not ATMA or CAF1 payload lines. "
            "No live success claimed."
        ),
        "x431MustBeInactive": True,
    }
    try:
        if port is None:
            if serial_module is None:
                import serial as serial_module  # lazy
            if list_ports is None:
                import serial.tools.list_ports as list_ports
            com = find_vlinker_port(list_ports)
            port = serial_module.Serial(com, BAUD, timeout=0.05, exclusive=True)
        client = ElmClient(port, timeout_s=min(8.0, deadline_mono_budget))
        import time

        end = time.monotonic() + deadline_mono_budget
        result["adapter"] = client.validate_adapter(min(10.0, end - time.monotonic()))
        client.configure_pair(profile["txId"], profile["rxId"], end)
        req_deadline = min(end, time.monotonic() + 15.0)
        resp = client.request(op["requestHex"], req_deadline)
        result["payload_hex"] = resp.get("payload_hex")
        result["pending"] = resp.get("pending")
        result["raw"] = resp.get("raw")
        if not resp.get("ok"):
            result["error"] = resp.get("error")
        else:
            final = resp.get("payload_hex") or ""
            if not final.upper().startswith(op["positivePrefixHex"].upper()):
                result["error"] = "positive-prefix-mismatch"
            else:
                decoded = decode_payload(final, op["decode"])
                result["decoded"] = decoded
                kind = (op.get("decode") or {}).get("type")
                if not decoded.get("ok"):
                    result["error"] = decoded.get("reason") or "decode-incomplete"
                elif kind == "ascii":
                    constraints = profile.get("identityConstraints") or {}
                    exp = op.get("expectedValue")
                    got = (decoded or {}).get("text")
                    if exp is not None and not identity_ascii_matches(got, str(exp)):
                        result["identityMismatch"] = True
                        result["error"] = "identity-mismatch"
                    else:
                        result["ok"] = True
                        result["constraintsUsed"] = {k: constraints[k] for k in constraints if k != "vinBound"}
                else:
                    result["ok"] = True
                    constraints = profile.get("identityConstraints") or {}
                    result["constraintsUsed"] = {k: constraints[k] for k in constraints if k != "vinBound"}
        restoration = client.close_restore()
    except Exception as e:  # noqa: BLE001 — preserve partials
        result["error"] = str(e)
        if client is not None:
            restoration = client.close_restore()
        elif own_port and port is not None:
            try:
                port.close()
            except Exception as ce:  # noqa: BLE001
                restoration = {"errors": [str(ce)]}
    result["endedUtc"] = datetime.now(timezone.utc).isoformat()
    result["restoration"] = restoration
    dest = out_dir or (
        REPO_ROOT
        / ".local"
        / "diagnostics"
        / "live"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}")
    )
    dest.mkdir(parents=True, exist_ok=True)
    raw_log = client.raw_log if client is not None else []
    (dest / "manifest.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "raw.json").write_text(json.dumps(raw_log, indent=2) + "\n", encoding="utf-8")
    result["outDir"] = str(dest)
    return result
