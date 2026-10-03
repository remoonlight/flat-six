"""Standard Mode 01 PID decode for the frozen 981 engine session. Not vehicle evidence."""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_SEED = REPO_ROOT / "data" / "seed" / "diagnostics" / "engine-obd.v1.json"
ENGINE_PROFILE = "porsche-981-2014-dme"
SUPPORT_REQ = "0100"


class EngineSpecError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _hex(value) -> str:
    if not isinstance(value, str):
        raise EngineSpecError("engine-spec-invalid")
    hx = value.replace(" ", "").upper()
    if len(hx) < 2 or len(hx) % 2:
        raise EngineSpecError("engine-spec-invalid")
    try:
        bytes.fromhex(hx)
    except ValueError as e:
        raise EngineSpecError("engine-spec-invalid") from e
    return hx


def load_engine_spec() -> dict:
    try:
        data = json.loads(ENGINE_SEED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise EngineSpecError("engine-spec-unavailable") from e
    if data.get("schemaVersion") != 1 or data.get("profileId") != ENGINE_PROFILE:
        raise EngineSpecError("engine-spec-invalid")
    if data.get("evidenceStatus") != "standard-obd-unverified-on-vehicle":
        raise EngineSpecError("engine-spec-invalid")
    pair = data.get("pair") or {}
    if pair.get("txId") != "7E0" or pair.get("rxId") != "7E8":
        raise EngineSpecError("engine-spec-invalid")
    support = data.get("supportQuery") or {}
    if _hex(support.get("requestHex")) != SUPPORT_REQ or int(support.get("responseLen") or 0) != 6:
        raise EngineSpecError("engine-spec-invalid")
    pids = data.get("pids")
    if not isinstance(pids, list) or len(pids) != 6:
        raise EngineSpecError("engine-spec-invalid")
    opts = data.get("options") or {}
    cycles = opts.get("sampleCycles") or {}
    interval = opts.get("intervalMs") or {}
    if (cycles.get("min"), cycles.get("max"), cycles.get("default")) != (1, 10, 5):
        raise EngineSpecError("engine-spec-invalid")
    if (interval.get("min"), interval.get("max"), interval.get("default")) != (500, 5000, 1000):
        raise EngineSpecError("engine-spec-invalid")
    out = []
    seen = set()
    for row in pids:
        if not isinstance(row, dict):
            raise EngineSpecError("engine-spec-invalid")
        pid = str(row.get("pid") or "").upper()
        req = _hex(row.get("requestHex"))
        if len(pid) != 2 or req != "01" + pid or pid in seen:
            raise EngineSpecError("engine-spec-invalid")
        seen.add(pid)
        rlen = int(row.get("responseLen") or 0)
        if rlen < 3:
            raise EngineSpecError("engine-spec-invalid")
        out.append(
            {
                "pid": pid,
                "requestHex": req,
                "label": str(row.get("label") or ""),
                "unit": str(row.get("unit") or ""),
                "responseLen": rlen,
                "formula": str(row.get("formula") or ""),
            }
        )
    expected = ("04", "05", "0C", "0D", "0F", "11")
    if tuple(p["pid"] for p in out) != expected:
        raise EngineSpecError("engine-spec-invalid")
    return {
        "evidenceStatus": data["evidenceStatus"],
        "liveVerified": False,
        "supportQuery": {"requestHex": SUPPORT_REQ, "positivePrefixHex": "4100", "responseLen": 6},
        "pids": out,
        "options": {
            "sampleCycles": {"min": 1, "max": 10, "default": 5},
            "intervalMs": {"min": 500, "max": 5000, "default": 1000},
        },
        "authorizedHex": frozenset({SUPPORT_REQ, *(p["requestHex"] for p in out)}),
    }


def pid_bit_supported(mask: bytes, pid_hex: str) -> bool:
    pid = int(pid_hex, 16)
    if pid < 1 or pid > 32 or len(mask) != 4:
        return False
    idx = pid - 1
    return bool(mask[idx // 8] & (1 << (7 - (idx % 8))))


def parse_0100(payload_hex: str) -> bytes:
    try:
        raw = bytes.fromhex((payload_hex or "").replace(" ", ""))
    except ValueError as e:
        raise EngineSpecError("engine-pid-malformed") from e
    if len(raw) != 6 or raw[0] != 0x41 or raw[1] != 0x00:
        raise EngineSpecError("engine-pid-length-mismatch" if len(raw) != 6 else "engine-pid-echo-mismatch")
    return raw[2:6]


def decode_mode01(pid_row: dict, payload_hex: str) -> float:
    try:
        raw = bytes.fromhex((payload_hex or "").replace(" ", ""))
    except ValueError as e:
        raise EngineSpecError("engine-pid-malformed") from e
    pid = int(pid_row["pid"], 16)
    want = int(pid_row["responseLen"])
    if len(raw) != want:
        raise EngineSpecError("engine-pid-length-mismatch")
    if raw[0] != 0x41 or raw[1] != pid:
        raise EngineSpecError("engine-pid-echo-mismatch")
    data = raw[2:]
    formula = pid_row["formula"]
    if formula == "A*100/255":
        return data[0] * 100.0 / 255.0
    if formula == "A-40":
        return float(data[0] - 40)
    if formula == "(256*A+B)/4":
        if len(data) != 2:
            raise EngineSpecError("engine-pid-length-mismatch")
        return (256 * data[0] + data[1]) / 4.0
    if formula == "A":
        return float(data[0])
    raise EngineSpecError("engine-spec-invalid")


def select_pids(spec: dict, selected_pids=None) -> list[dict]:
    """Only select known definitions; omission preserves the legacy six-PID API."""
    if selected_pids is None:
        return spec["pids"]
    known = {row["pid"] for row in spec["pids"]}
    if (not isinstance(selected_pids, list) or not 1 <= len(selected_pids) <= 12
            or any(not isinstance(pid, str) or pid not in known for pid in selected_pids)
            or len(set(selected_pids)) != len(selected_pids)):
        raise EngineSpecError("invalid-selected-pids")
    return [row for row in spec["pids"] if row["pid"] in selected_pids]


def split_support(spec: dict, mask: bytes) -> tuple[list[dict], list[str]]:
    supported = []
    unsupported = []
    for row in spec["pids"]:
        if pid_bit_supported(mask, row["pid"]):
            supported.append(row)
        else:
            unsupported.append(row["pid"])
    return supported, unsupported
