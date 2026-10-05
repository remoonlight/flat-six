"""Bounded 981 DME/Gateway read-only session runner. Stdio contract is frozen."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .allowlist import blocked_reason, clear_transaction_blocked, engine_transaction_blocked
from .catalog import DEFAULT_CATALOG, catalog_cli_json, live_allowed_hex, load_catalog, operation, profile_by_id
from .decode import decode_payload
from .elm import ElmClient, ElmError
from .engine_obd import ENGINE_PROFILE, EngineSpecError, decode_mode01, load_engine_spec, parse_0100, select_pids, split_support
from .hashutil import sha256_file
from .qualification import qualify_identity
from .session_simulator import SCENARIOS, SessionSimPort

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / ".local" / "diagnostics" / "sessions"
CLEAR_SEED = REPO_ROOT / "data" / "seed" / "diagnostics" / "dtc-clear.v1.json"
NAMED_PROFILES = ("porsche-981-2014-dme", "porsche-981-2014-gateway")
ALLOWED_KEYS = frozenset(
    {
        "action",
        "profileId",
        "mode",
        "scenario",
        "operationIds",
        "resumeRunId",
        "confirmedReadOnly",
        "x431Inactive",
        "sessionTask",
        "confirmedClearDtc",
        "sampleCycles",
        "intervalMs",
        "selectedPids",
        "deviceId",
    }
)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
MAX_STDIN = 65536
SESSION_BY_PROFILE = {
    "porsche-981-2014-dme": ("1089", "5089"),
    "porsche-981-2014-gateway": ("1003", "5003"),
}
IDENTITY_FIELDS = {
    "porsche-981-2014-dme": (
        ("dsn", "dme-dsn"),
        ("software", "dme-software"),
        ("hardware", "dme-hardware"),
        ("porschePart", "dme-porsche-part"),
        ("hardwarePart", "dme-hardware-part"),
    ),
    "porsche-981-2014-gateway": (
        ("dsn", "gw-dsn-f1a2"),
        ("system", "gw-system-f197"),
        ("identification", "gw-ident-f19e"),
        ("porschePart", "gw-part-f187"),
        ("hardwarePart", "gw-hw-part-f191"),
        ("hardware", "gw-hw-ver-f1a3"),
        ("dataRecord", "gw-data-record-f182"),
    ),
}
VIN_BY_PROFILE = {
    "porsche-981-2014-dme": "dme-vin",
    "porsche-981-2014-gateway": "gw-vin",
}
DTC_BY_PROFILE = {
    "porsche-981-2014-dme": "dme-dtc",
    "porsche-981-2014-gateway": "gw-dtc",
}
ECU_ID = {"porsche-981-2014-dme": 1, "porsche-981-2014-gateway": 9}
SESSION_HEX_ALLOW = frozenset({"1089", "1003"})
_THREAD_LOCKS = {"simulation": threading.Lock(), "live": threading.Lock()}
PROFILE_CLEAR = {
    "porsche-981-2014-dme": {
        "requestHex": "14FF00",
        "expectedPositiveHex": "54FF00",
        "protocol": "kwp-cdi-16bit-group-echo",
        "requestLen": 3,
        "responseLen": 3,
    },
    "porsche-981-2014-gateway": {
        "requestHex": "14FFFFFF",
        "expectedPositiveHex": "54",
        "protocol": "uds-14-empty-positive",
        "requestLen": 4,
        "responseLen": 1,
    },
}

RESTORATION_NOTE = "Adapter close only; ECU restoration not proven"


class SessionError(RuntimeError):
    def __init__(self, code: str, *, exit_code: int = 2):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def catalog_fingerprint() -> str:
    return sha256_file(DEFAULT_CATALOG)


def load_clear_spec() -> dict:
    data = json.loads(CLEAR_SEED.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("profiles"), dict):
        raise SessionError("clear-spec-invalid")
    return data


def engine_capability() -> dict:
    try:
        spec = load_engine_spec()
    except EngineSpecError as e:
        return {"available": False, "reason": e.code, "liveVerified": False, "evidenceStatus": None}
    return {
        "available": True,
        "liveVerified": False,
        "evidenceStatus": spec["evidenceStatus"],
        "supportQuery": spec["supportQuery"],
        "pids": spec["pids"],
        "options": spec["options"],
        "authorizedHex": sorted(spec["authorizedHex"]),
        "acquisitionRoute": {"protocol": "standard-mode01", "txId": "7DF", "rxId": "7E8", "sessionControl": False, "automaticFallback": False, "retries": 0},
    }


def engine_auth_for(profile_id: str) -> dict:
    if profile_id != ENGINE_PROFILE:
        raise SessionError("engine-profile-unsupported")
    cap = engine_capability()
    if not cap.get("available"):
        raise SessionError(cap.get("reason") or "engine-spec-unavailable")
    return cap


def _bounded_int(name: str, value, lo: int, hi: int, default: int):
    if value is None:
        return default
    if isinstance(value, bool) or type(value) is not int:
        raise SessionError(f"{name}-type")
    if value < lo or value > hi:
        raise SessionError(f"{name}-range")
    return value


def clear_auth_for(profile_id: str) -> dict:
    known = PROFILE_CLEAR.get(profile_id)
    if not known:
        raise SessionError("clear-profile-unsupported")
    spec = load_clear_spec()
    row = spec["profiles"].get(profile_id)
    if not isinstance(row, dict):
        raise SessionError("clear-profile-unsupported")
    req = str(row.get("requestHex") or "").replace(" ", "").upper()
    pos = str(row.get("expectedPositiveHex") or "").replace(" ", "").upper()
    if not req or not pos:
        raise SessionError("clear-spec-gap")
    if req != known["requestHex"] or pos != known["expectedPositiveHex"]:
        raise SessionError("clear-spec-mismatch")
    try:
        req_b = bytes.fromhex(req)
        pos_b = bytes.fromhex(pos)
    except ValueError as e:
        raise SessionError("clear-spec-invalid") from e
    if len(req_b) != known["requestLen"] or len(pos_b) != known["responseLen"]:
        raise SessionError("clear-spec-mismatch")
    return {
        "requestHex": req,
        "expectedPositiveHex": pos,
        "protocol": known["protocol"],
        "liveClearEnabled": bool(row.get("liveClearEnabled")),
        "sourceObservedResponse": False,
        "responseGap": spec.get("responseGap"),
        "boundVariant": row.get("boundVariant"),
        "evidence": row.get("evidence") or [],
        "positiveReference": row.get("positiveReference"),
        "liveVerified": False,
        "available": True,
    }


def clear_capability(profile_id: str) -> dict:
    try:
        return clear_auth_for(profile_id)
    except (SessionError, OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        reason = e.code if isinstance(e, SessionError) else "clear-spec-unavailable"
        return {"available": False, "reason": reason, "liveVerified": False, "sourceObservedResponse": False}


def new_run_id() -> str:
    return "s" + uuid.uuid4().hex


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id) or Path(run_id).name != run_id:
        raise SessionError("invalid-run-id")
    return run_id


def _profile(catalog: dict, profile_id: str) -> dict:
    if profile_id not in NAMED_PROFILES:
        raise SessionError("unknown-profile")
    p = profile_by_id(catalog, profile_id)
    if p.get("model") != "981":
        raise SessionError("generation-not-981")
    return p


def _dependent_ids(profile_id: str, operation_ids) -> list[str]:
    dtc = DTC_BY_PROFILE[profile_id]
    vin = VIN_BY_PROFILE[profile_id]
    allowed = {dtc, vin}
    if operation_ids is None:
        return [dtc]
    if not isinstance(operation_ids, list) or any(not isinstance(x, str) for x in operation_ids):
        raise SessionError("operationIds-type")
    if len(set(operation_ids)) != len(operation_ids):
        raise SessionError("operationIds-duplicate")
    for oid in operation_ids:
        if oid not in allowed:
            raise SessionError("operationIds-not-dependent")
    return list(operation_ids)


def blocked_candidates(catalog: dict, profile: dict) -> list[dict]:
    pub = catalog_cli_json(catalog)["publicReference"]
    blocked = [
        {
            "reason": "offline-plan-not-executable-authority",
            "detail": "Derived/exported offline plan cannot authorize live requests",
        },
        {
            "reason": "session-not-arbitrary-sid10",
            "detail": f"session allowlist {sorted(SESSION_HEX_ALLOW)}; profile uses {SESSION_BY_PROFILE[profile['id']][0]}",
        },
    ]
    for op in profile.get("operations") or []:
        if not op.get("liveAllowed"):
            blocked.append({"operationId": op["id"], "reason": "not-liveAllowed"})
    if pub.get("present"):
        blocked.append(
            {
                "reason": "public-candidates-not-admitted",
                "engineCandidates": len(pub.get("engineCandidates") or []),
                "codingCandidates": len(pub.get("codingCandidates") or []),
                "executionEnabled": False,
            }
        )
    return blocked


def build_plan(
    profile_id: str,
    operation_ids=None,
    *,
    session_task: str = "read",
    sample_cycles=None,
    interval_ms=None,
    selected_pids=None,
) -> dict:
    if session_task not in ("read", "clear", "engine"):
        raise SessionError("invalid-session-task")
    catalog = load_catalog()
    profile = _profile(catalog, profile_id)
    sess, pos = SESSION_BY_PROFILE[profile_id]
    ident = [{"field": f, "operationId": oid} for f, oid in IDENTITY_FIELDS[profile_id]]
    if session_task != "engine" and (sample_cycles is not None or interval_ms is not None or selected_pids is not None):
        raise SessionError("engine-options-without-task")
    if session_task == "engine":
        if operation_ids is not None:
            raise SessionError("engine-operationIds-forbidden")
        eng = engine_auth_for(profile_id)
        try:
            definitions = select_pids(eng, selected_pids)
        except EngineSpecError as e:
            raise SessionError(e.code) from e
        opts = eng["options"]
        cycles = _bounded_int("sampleCycles", sample_cycles, opts["sampleCycles"]["min"], opts["sampleCycles"]["max"], opts["sampleCycles"]["default"])
        interval = _bounded_int("intervalMs", interval_ms, opts["intervalMs"]["min"], opts["intervalMs"]["max"], opts["intervalMs"]["default"])
        deps = []
    else:
        eng = engine_capability() if profile_id == ENGINE_PROFILE else {"available": False, "reason": "engine-profile-unsupported", "liveVerified": False}
        cycles = None
        interval = None
        deps = []
        for oid in _dependent_ids(profile_id, operation_ids):
            op = operation(profile, oid)
            if not op.get("liveAllowed"):
                raise SessionError("dependent-not-liveAllowed")
            kind = (op.get("decode") or {}).get("type")
            if kind not in ("kwp-dtc-18", "uds-dtc-19-02", "vin-ascii"):
                raise SessionError("dependent-kind")
            deps.append(
                {
                    "operationId": oid,
                    "requestHex": op["requestHex"].upper(),
                    "kind": kind,
                }
            )
    allowed = live_allowed_hex(profile)
    for item in ident:
        op = operation(profile, item["operationId"])
        raw = bytes.fromhex(op["requestHex"])
        if blocked_reason(raw, allowed):
            raise SessionError("identity-not-allowlisted")
    for d in deps:
        raw = bytes.fromhex(d["requestHex"])
        if blocked_reason(raw, allowed):
            raise SessionError("dependent-not-allowlisted")
    cap = clear_capability(profile_id)
    if session_task == "clear":
        if not cap.get("available"):
            raise SessionError(cap.get("reason") or "clear-spec-unavailable")
        auth = cap
    else:
        auth = cap if cap.get("available") else None
    plan = {
        "profileId": profile_id,
        "generation": "981",
        "session": {
            "requestHex": sess,
            "expectedPositiveHex": pos,
            "requestEvidence": "source-observed",
            "allowlist": sorted(SESSION_HEX_ALLOW),
        },
        "identityOperations": ident,
        "dependentOperations": deps,
        "catalogSha256": catalog_fingerprint(),
        "blockedCandidates": blocked_candidates(catalog, profile),
        "liveVerified": False,
        "writePayload": None,
        "ecuRestoreCommands": [],
        "clearCapability": cap,
        "clearAuthorization": auth,
        "engineCapability": eng,
        "engineAuthorization": eng if session_task == "engine" else None,
        "engine": {
            "definitions": (definitions if session_task == "engine" else eng.get("pids") if eng.get("available") else None),
            "selectedPids": ([row["pid"] for row in definitions] if session_task == "engine" else None),
            "options": (eng.get("options") if eng.get("available") else None),
            "evidenceStatus": eng.get("evidenceStatus"),
            "sampleCycles": cycles,
            "intervalMs": interval,
            "acquisitionRoute": eng.get("acquisitionRoute"),
        },
    }
    return plan


class _FileLock:
    def __init__(self, mode: str, artifact_root: Path):
        self.mode = mode
        self.path = artifact_root / f".exclusive-{mode}"
        self._fd = None
        self._thread_held = False

    def acquire(self) -> None:
        lock = _THREAD_LOCKS[self.mode]
        if not lock.acquire(blocking=False):
            raise SessionError("session-lock-busy")
        self._thread_held = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError as e:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            lock.release()
            self._thread_held = False
            raise SessionError("session-lock-busy") from e
        except Exception:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                self._fd = None
            lock.release()
            self._thread_held = False
            raise

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        if self._thread_held:
            _THREAD_LOCKS[self.mode].release()
            self._thread_held = False


def _atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _load_checkpoint(dir_path: Path) -> dict:
    p = dir_path / "checkpoint.json"
    if not p.is_file():
        raise SessionError("checkpoint-missing")
    return json.loads(p.read_text(encoding="utf-8"))


def _plan_key(plan: dict) -> dict:
    return {
        "session": plan["session"],
        "identityOperations": plan["identityOperations"],
        "dependentOperations": plan["dependentOperations"],
        "profileId": plan["profileId"],
        "generation": plan["generation"],
        "catalogSha256": plan["catalogSha256"],
    }


def _resume_dependent_valid(rec, plan_dep: dict, profile: dict) -> bool:
    if not isinstance(rec, dict) or rec.get("ok") is not True:
        return False
    if rec.get("operationId") != plan_dep["operationId"]:
        return False
    hx = rec.get("payload_hex")
    if not isinstance(hx, str) or not hx:
        return False
    try:
        op = operation(profile, rec["operationId"])
    except KeyError:
        return False
    if not hx.upper().startswith(str(op.get("positivePrefixHex") or "").upper()):
        return False
    decoded = rec.get("decoded")
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        return False
    again = decode_payload(hx, op["decode"])
    return bool(again.get("ok"))


def _validate_resume_evidence(prev: dict, plan: dict, profile: dict) -> tuple[set[str], list]:
    if prev.get("clearCheckpoint") is True or prev.get("sessionTask") == "clear":
        raise SessionError("clear-checkpoint-not-resumable")
    q = prev.get("identityQualification")
    ids = prev.get("completedDependentIds")
    rows = prev.get("dependentResults")
    if not isinstance(ids, list) or not isinstance(rows, list):
        raise SessionError("resume-evidence-invalid")
    # An interrupted identity phase has no dependent evidence to reuse. Restart
    # its full qualification; only skipping completed reads needs prior proof.
    if ids and (not isinstance(q, dict) or q.get("observedProfileMatch") is not True):
        raise SessionError("resume-evidence-invalid")
    if any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise SessionError("resume-evidence-invalid")
    by_id = {}
    for rec in rows:
        if not isinstance(rec, dict) or not isinstance(rec.get("operationId"), str):
            raise SessionError("resume-evidence-invalid")
        oid = rec["operationId"]
        if oid in by_id:
            raise SessionError("resume-evidence-invalid")
        by_id[oid] = rec
    plan_deps = {d["operationId"]: d for d in plan["dependentOperations"]}
    if any(oid not in plan_deps for oid in by_id):
        raise SessionError("resume-evidence-invalid")
    for oid in ids:
        if oid not in plan_deps:
            raise SessionError("resume-evidence-invalid")
        rec = by_id.get(oid)
        if rec is None or not _resume_dependent_valid(rec, plan_deps[oid], profile):
            raise SessionError("resume-evidence-invalid")
    return set(ids), [by_id[oid] for oid in ids]


def _pipe_available(stream) -> int | None:
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, io.UnsupportedOperation):
        return None
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(fd)
        avail = wintypes.DWORD()
        if not ctypes.windll.kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None):
            return 0
        return int(avail.value)
    import select

    ready, _, _ = select.select([stream], [], [], 0)
    return 1 if ready else 0
    p = dir_path / "checkpoint.json"
    if not p.is_file():
        raise SessionError("checkpoint-missing")
    return json.loads(p.read_text(encoding="utf-8"))


def prepare(profile_id: str, operation_ids=None, *, session_task: str = "read", sample_cycles=None, interval_ms=None, selected_pids=None) -> dict:
    try:
        plan = build_plan(
            profile_id,
            operation_ids,
            session_task=session_task,
            sample_cycles=sample_cycles,
            interval_ms=interval_ms,
            selected_pids=selected_pids,
        )
        return {"type": "plan", "ok": True, "plan": plan, "error": None}
    except SessionError as e:
        return {"type": "plan", "ok": False, "plan": None, "error": e.code}


def _emit(sink, obj) -> None:
    if sink is None:
        return
    sink(obj)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_live_port(serial_module=None, list_ports=None, *, device_id=None, discovery=None):
    from .connection import KNOWN_VLIKER, open_selected_port, pyserial_rows

    if not device_id:
        device_id = "bt:" + KNOWN_VLIKER
    snapshot = discovery
    if snapshot is None and list_ports is not None:
        rows, _err = pyserial_rows(list_ports)
        snapshot = {"platform": "win32", "serial": [], "bluetooth": [], "pyserial": rows, "errors": []}
    port, _com = open_selected_port(device_id, serial_module=serial_module, snapshot=snapshot)
    return port


def _one_request(
    client: ElmClient,
    req_hex: str,
    deadline: float,
    allowed: set[str] | None,
    *,
    clear_authorized: str | None = None,
    engine_authorized=None,
) -> dict:
    raw = bytes.fromhex(req_hex)
    if clear_authorized is not None:
        reason = clear_transaction_blocked(raw, clear_authorized)
        if reason:
            return {"ok": False, "error": reason, "payload_hex": None, "raw": None, "pending": []}
    elif engine_authorized is not None:
        reason = engine_transaction_blocked(raw, engine_authorized)
        if reason:
            return {"ok": False, "error": reason, "payload_hex": None, "raw": None, "pending": []}
    elif allowed is not None:
        reason = blocked_reason(raw, allowed)
        if reason:
            return {"ok": False, "error": reason, "payload_hex": None, "raw": None, "pending": []}
    else:
        if req_hex.upper() not in SESSION_HEX_ALLOW:
            return {"ok": False, "error": "session-not-allowlisted", "payload_hex": None, "raw": None, "pending": []}
    return client.request(req_hex, deadline)


def run_session(
    *,
    profile_id: str,
    mode: str = "simulation",
    scenario: str = "success",
    operation_ids=None,
    resume_run_id: str | None = None,
    confirmed_read_only: bool = False,
    x431_inactive: bool = False,
    session_task: str = "read",
    confirmed_clear_dtc: bool = False,
    sample_cycles=None,
    interval_ms=None,
    selected_pids=None,
    port=None,
    budget_s: float = 60.0,
    artifact_root: Path | None = None,
    cancel_event: threading.Event | None = None,
    progress_sink=None,
    serial_module=None,
    list_ports=None,
    device_id=None,
    discovery=None,
    retain_artifacts: bool = True,
) -> dict:
    started = datetime.now(timezone.utc)
    root = Path(artifact_root) if artifact_root is not None else DEFAULT_ARTIFACT_ROOT
    simulation = mode == "simulation"
    live_verified = False
    result = {
        "type": "result",
        "ok": False,
        "mode": mode,
        "runId": None,
        "profileId": profile_id,
        "status": "failed",
        "error": None,
        "results": [],
        "identityQualification": None,
        "restoration": {"errors": ["not-opened"], "ecuRestorationProven": False, "note": RESTORATION_NOTE},
        "simulation": simulation,
        "liveVerified": live_verified,
        "writePayload": None,
        "sessionTask": "read",
        "artifactDir": None,
        "startedUtc": started.isoformat(),
        "hostStamp": started.isoformat(),
    }
    if not retain_artifacts and (session_task == "clear" or resume_run_id is not None):
        result["error"] = "transient-task-not-allowed"
        return result
    if not isinstance(profile_id, str):
        result["error"] = "profileId-type"
        return result
    if not isinstance(mode, str) or mode not in ("simulation", "live"):
        result["error"] = "invalid-mode"
        return result
    simulation = mode == "simulation"
    if not isinstance(scenario, str):
        result["error"] = "invalid-scenario"
        return result
    if simulation and scenario not in SCENARIOS:
        result["error"] = "invalid-scenario"
        return result
    if not simulation and scenario != "success":
        result["error"] = "scenario-live-forbidden"
        return result
    if confirmed_read_only is not True and confirmed_read_only is not False:
        result["error"] = "confirmedReadOnly-type"
        return result
    if x431_inactive is not True and x431_inactive is not False:
        result["error"] = "x431Inactive-type"
        return result
    if session_task is None:
        session_task = "read"
    if not isinstance(session_task, str) or session_task not in ("read", "clear", "engine"):
        result["error"] = "invalid-session-task"
        return result
    result["sessionTask"] = session_task
    if confirmed_clear_dtc is not True and confirmed_clear_dtc is not False:
        result["error"] = "confirmedClearDtc-type"
        return result
    if session_task == "clear":
        if resume_run_id is not None:
            result["error"] = "clear-resume-forbidden"
            return result
        if operation_ids is not None:
            result["error"] = "clear-operationIds-forbidden"
            return result
        if confirmed_read_only is True:
            result["error"] = "clear-conflicts-read-only"
            return result
        if mode == "live" and not (confirmed_clear_dtc is True and x431_inactive is True):
            result["error"] = "live-clear-confirmation-required"
            return result
    elif confirmed_clear_dtc is True:
        result["error"] = "clear-flag-without-task"
        return result
    if session_task == "engine":
        if resume_run_id is not None:
            result["error"] = "engine-resume-forbidden"
            return result
        if operation_ids is not None:
            result["error"] = "engine-operationIds-forbidden"
            return result
        if profile_id != ENGINE_PROFILE:
            result["error"] = "engine-profile-unsupported"
            return result
        if mode == "live" and not (confirmed_read_only is True and x431_inactive is True):
            result["error"] = "live-confirmations-required"
            return result
    elif sample_cycles is not None or interval_ms is not None or selected_pids is not None:
        result["error"] = "engine-options-without-task"
        return result
    if mode == "live" and session_task == "read" and not (confirmed_read_only is True and x431_inactive is True):
        result["error"] = "live-confirmations-required"
        return result
    if resume_run_id is not None and not isinstance(resume_run_id, str):
        result["error"] = "resumeRunId-type"
        return result

    try:
        plan = build_plan(
            profile_id,
            operation_ids,
            session_task=session_task,
            sample_cycles=sample_cycles,
            interval_ms=interval_ms,
            selected_pids=selected_pids,
        )
    except SessionError as e:
        result["error"] = e.code
        return result

    skip_dependents: set[str] = set()
    prior_results: list = []
    resume_from = None
    catalog = load_catalog()
    if resume_run_id:
        try:
            rid = validate_run_id(resume_run_id)
            prev = _load_checkpoint(root / rid)
            profile_for_resume = _profile(catalog, profile_id)
            skip_dependents, prior_results = _validate_resume_evidence(prev, plan, profile_for_resume)
        except SessionError as e:
            result["error"] = e.code
            return result
        except (OSError, json.JSONDecodeError, KeyError):
            result["error"] = "checkpoint-unreadable"
            return result
        if prev.get("mode") == "simulation" and mode == "live":
            result["error"] = "simulation-checkpoint-not-live"
            return result
        if prev.get("mode") != mode or prev.get("profileId") != profile_id:
            result["error"] = "resume-mismatch"
            return result
        if prev.get("catalogSha256") != plan["catalogSha256"]:
            result["error"] = "catalog-fingerprint-mismatch"
            return result
        if prev.get("plan") != _plan_key(plan):
            result["error"] = "plan-mismatch"
            return result
        resume_from = rid

    plan_key = _plan_key(plan)
    own_port = port is None
    client = None
    lock = _FileLock(mode, root)
    lock_held = False
    cleaned = False
    profile = profile_by_id(catalog, profile_id)
    allowed = live_allowed_hex(profile)
    identity: dict[str, str | None] = {}
    results: list[dict] = []
    status = "failed"
    error = None
    persist_error = None
    cancel = cancel_event or threading.Event()
    engine_state = None
    if session_task == "engine":
        engine_state = {
            "selectedPids": plan["engine"]["selectedPids"],
            "supportedPids": [],
            "unsupportedPids": [],
            "samples": [],
            "completedCycles": 0,
            "sampleCycles": plan["engine"]["sampleCycles"],
            "intervalMs": plan["engine"]["intervalMs"],
        }
        result["engine"] = engine_state

    def persist(extra=None) -> None:
        if not retain_artifacts:
            return
        ck = {
            "mode": mode,
            "profileId": profile_id,
            "catalogSha256": plan["catalogSha256"],
            "plan": plan_key,
            "completedDependentIds": [
                r["operationId"] for r in results if r.get("role") == "dependent" and r.get("ok") is True
            ],
            "dependentResults": [r for r in results if r.get("role") == "dependent"],
            "identityQualification": result.get("identityQualification"),
            "simulation": simulation,
            "liveVerified": False,
            "resumeFromRunId": resume_from,
            "sessionTask": session_task,
            "clearCheckpoint": session_task == "clear",
        }
        if engine_state is not None:
            ck["engine"] = engine_state
        if extra:
            ck.update(extra)
        _atomic_write(dest / "checkpoint.json", ck)
        _atomic_write(dest / "manifest.json", {k: result[k] for k in result if k != "raw"})
        raw = client.raw_log if client is not None else []
        _atomic_write(dest / "raw.json", raw)
        if engine_state is not None:
            _atomic_write(dest / "engine.json", engine_state)

    def cleanup() -> None:
        nonlocal cleaned, persist_error, error, status
        if cleaned:
            return
        cleaned = True
        restoration = {"errors": [], "ecuRestorationProven": False, "note": RESTORATION_NOTE, "adapterClose": "ATPC"}
        try:
            if client is not None:
                restoration = {
                    **client.close_restore(),
                    "ecuRestorationProven": False,
                    "note": RESTORATION_NOTE,
                    "adapterClose": "D-PDU disconnect/destruct" if device_id and device_id.startswith("vnci:") else "ATPC",
                }
            elif own_port and port is not None:
                try:
                    port.close()
                except Exception as ce:  # noqa: BLE001
                    restoration["errors"] = [str(ce)]
            result["restoration"] = restoration
            try:
                persist()
            except Exception as pe:  # noqa: BLE001
                persist_error = str(pe)
                result["ok"] = False
                if status == "completed":
                    status = "failed"
                if not error:
                    error = "persist-failed"
                result["error"] = error
                result["status"] = status
        finally:
            if lock_held:
                lock.release()

    try:
        lock.acquire()
        lock_held = True
    except SessionError as e:
        result["error"] = e.code
        result["status"] = "failed"
        return result

    run_id = new_run_id()
    result["runId"] = run_id
    result["resumeFromRunId"] = resume_from
    dest = root / run_id
    result["artifactDir"] = None

    vnci_live = mode == "live" and isinstance(device_id, str) and device_id.startswith("vnci:")
    try:
        if retain_artifacts:
            dest.mkdir(parents=True, exist_ok=True)
            result["artifactDir"] = str(dest)
        vnci_live = mode == "live" and isinstance(device_id, str) and device_id.startswith("vnci:")
        if vnci_live:
            if session_task == "clear":
                raise ElmError("vnci-clear-not-validated")
            if session_task == "engine":
                raise ElmError("vnci-engine-route-not-validated")
            if port is not None:
                raise ElmError("vnci-serial-injection-forbidden")
            from .vnci import DpuClient
            client = DpuClient(device_id, cancel_event=cancel)
        elif port is None:
            if simulation:
                port = SessionSimPort(profile_id, scenario)
            else:
                port = _open_live_port(
                    serial_module,
                    list_ports,
                    device_id=device_id,
                    discovery=discovery,
                )
        if client is None:
            client = ElmClient(port, timeout_s=min(8.0, budget_s), cancel_event=cancel,
                frame_sink=(lambda frame: _emit(progress_sink, {"type": "can-frame", "runId": run_id, "profileId": profile_id,
                    "simulation": simulation, "frame": frame})) if progress_sink else None)
        end = time.monotonic() + budget_s
        ident_ops = IDENTITY_FIELDS[profile_id]
        dep_ops = plan["dependentOperations"]
        extra = (1 + len(dep_ops)) if session_task == "clear" else 0
        if session_task == "engine":
            extra = 1 + int(engine_state["sampleCycles"] or 0) * len(engine_state["selectedPids"])
        total = 1 + len(ident_ops) + len(dep_ops) + extra
        completed = 0
        t0 = time.monotonic()

        def stage_guard() -> None:
            if cancel.is_set():
                raise ElmError("cancelled")
            if time.monotonic() >= end:
                raise ElmError("deadline-expired")

        def prog(stage: str, operation_id=None) -> None:
            ev = {
                "type": "progress",
                "runId": run_id,
                "stage": stage,
                "profileId": profile_id,
                "operationId": operation_id,
                "completed": completed,
                "total": total,
            }
            if stage == "engine" and engine_state is not None:
                last = engine_state["samples"][-1]["cycle"] if engine_state["samples"] else None
                ev["engine"] = {
                    **engine_state,
                    "samples": list(engine_state["samples"]),
                }
            _emit(progress_sink, ev)
            stage_guard()

        def transport_fault(exc) -> bool:
            message = str(exc)
            return isinstance(exc, (OSError, ConnectionError)) or message == "disconnect" or message.startswith(("port-io:", "short-write", "device-port-unavailable", "device-identity-missing"))

        def close_faulted_client() -> None:
            closed = client.close_restore()
            # A poisoned old link is expected; an actual close failure blocks a new open.
            if any(str(item).startswith("close:") for item in closed.get("errors", [])) or getattr(port, "is_open", False):
                raise ElmError("recovery-close-failed")

        def transaction(req_hex, deadline, allow, **kwargs):
            nonlocal client, port
            try:
                return _one_request(client, req_hex, deadline, allow, **kwargs)
            except (ElmError, OSError, ConnectionError) as first:
                if not transport_fault(first) or session_task not in ("read", "engine") or mode != "live" or not own_port or not isinstance(device_id, str) or not device_id.startswith("bt:"):
                    raise
                expected_identity = dict(identity)
                prior_raw = list(client.raw_log)
                close_faulted_client()
                episode = {"unfinishedRequestHex": req_hex, "attempts": [], "recovered": False}
                result.setdefault("recovery", []).append(episode)
                for attempt in range(1, 4):
                    stage_guard()
                    prog("reconnecting")
                    entry = {"attempt": attempt, "ok": False}
                    episode["attempts"].append(entry)
                    try:
                        port = _open_live_port(serial_module, list_ports, device_id=device_id, discovery=None)
                        client = ElmClient(port, timeout_s=min(8.0, budget_s), cancel_event=cancel,
                            frame_sink=(lambda frame: _emit(progress_sink, {"type": "can-frame", "runId": run_id, "profileId": profile_id,
                                "simulation": simulation, "frame": frame})) if progress_sink else None)
                        client.raw_log.extend(prior_raw)
                        stage_guard()
                        client.validate_adapter(min(10.0, max(0.0, end - time.monotonic())))
                        client.configure_pair(profile["txId"], profile["rxId"], end)
                        session_hex, positive = SESSION_BY_PROFILE[profile_id]
                        session_response = _one_request(client, session_hex, min(end, time.monotonic() + 15.0), None)
                        if not session_response.get("ok") or not (session_response.get("payload_hex") or "").upper().startswith(positive):
                            raise ElmError(session_response.get("error") or "recovery-session-mismatch")
                        recovered_identity = {}
                        for field, oid in ident_ops:
                            stage_guard()
                            op = operation(profile, oid)
                            response = _one_request(client, op["requestHex"], min(end, time.monotonic() + 15.0), allowed)
                            payload = response.get("payload_hex") or ""
                            if not response.get("ok") or not payload.upper().startswith(op["positivePrefixHex"].upper()):
                                raise ElmError(response.get("error") or "recovery-identity-invalid")
                            decoded = decode_payload(payload, op["decode"])
                            if not decoded.get("ok"):
                                raise ElmError("recovery-identity-invalid")
                            recovered_identity[field] = decoded.get("text")
                        qualified = qualify_identity({"generation": "981", "ecuId": ECU_ID[profile_id], "identity": recovered_identity}, catalog, expected_generation="981")
                        if not qualified.get("observedProfileMatch") or any(recovered_identity.get(k) != v for k, v in expected_identity.items()):
                            raise ElmError("identity-mismatch")
                        if session_task == "engine" and kwargs.get("engine_authorized"):
                            client.send_at("ATPC", end, require_ok=True)
                            client.configure_standard_engine(end)
                            if req_hex != "0100":
                                support = _one_request(client, "0100", min(end, time.monotonic() + 15.0), None, engine_authorized=kwargs["engine_authorized"])
                                if not support.get("ok"):
                                    raise ElmError(support.get("error") or "engine-support-failed")
                                mask = parse_0100(support.get("payload_hex") or "")
                                supported, _unsupported = split_support(spec, mask)
                                if req_hex not in {row["requestHex"] for row in supported}:
                                    raise ElmError("engine-support-changed")
                        # Continue only the unfinished read; prior completed data stays intact.
                        reply = _one_request(client, req_hex, min(end, time.monotonic() + 15.0), allow, **kwargs)
                        entry["ok"] = True
                        episode["recovered"] = True
                        return reply
                    except (ElmError, OSError, ConnectionError) as recovery_error:
                        entry["error"] = str(recovery_error)
                        if not transport_fault(recovery_error):
                            raise
                        prior_raw = list(client.raw_log)
                        close_faulted_client()
                        if attempt == 3:
                            raise ElmError("recovery-exhausted") from recovery_error
                        if cancel.wait(0.25 * attempt):
                            raise ElmError("cancelled")
                raise ElmError("recovery-exhausted")

        stage_guard()
        result["adapter"] = client.validate_adapter(min(10.0, max(0.0, end - time.monotonic())))
        stage_guard()
        client.configure_pair(profile["txId"], profile["rxId"], end)
        prog("configure")

        sess_hex, sess_pos = SESSION_BY_PROFILE[profile_id]
        stage_guard()
        req_deadline = min(end, time.monotonic() + 15.0)
        sess = transaction( sess_hex, req_deadline, None)
        sess_rec = {
            "role": "session",
            "requestHex": sess_hex,
            "ok": bool(sess.get("ok") and (sess.get("payload_hex") or "").upper().startswith(sess_pos)),
            "payload_hex": sess.get("payload_hex"),
            "error": sess.get("error"),
            "requestEvidence": "source-observed",
            "synthetic": simulation,
        }
        if sess.get("ok") and not sess_rec["ok"]:
            sess_rec["error"] = "session-subfunction-mismatch"
        results.append(sess_rec)
        completed += 1
        prog("session")
        persist()
        if not sess_rec["ok"]:
            error = sess_rec["error"] or "session-failed"
            status = "cancelled" if error == "cancelled" else "failed"
            raise ElmError(error)

        for field, oid in ident_ops:
            stage_guard()
            op = operation(profile, oid)
            req_deadline = min(end, time.monotonic() + 15.0)
            resp = transaction( op["requestHex"], req_deadline, allowed)
            rec = {
                "role": "identity",
                "field": field,
                "operationId": oid,
                "ok": False,
                "payload_hex": resp.get("payload_hex"),
                "error": resp.get("error"),
                "decoded": None,
            }
            if not resp.get("ok"):
                rec["error"] = resp.get("error") or "identity-request-failed"
            else:
                final = resp.get("payload_hex") or ""
                if not final.upper().startswith(op["positivePrefixHex"].upper()):
                    rec["error"] = "positive-prefix-mismatch"
                else:
                    decoded = decode_payload(final, op["decode"])
                    rec["decoded"] = decoded
                    if not decoded.get("ok"):
                        rec["error"] = decoded.get("reason") or "identity-decode-failed"
                    else:
                        rec["ok"] = True
                        identity[field] = decoded.get("text")
            results.append(rec)
            completed += 1
            prog("identity", oid)
            persist()
            if not rec["ok"]:
                error = rec["error"] or "identity-failed"
                raise ElmError(error)

        q = qualify_identity(
            {"generation": "981", "ecuId": ECU_ID[profile_id], "identity": identity},
            catalog,
            expected_generation="981",
        )
        result["identityQualification"] = q
        persist()
        if not q.get("observedProfileMatch"):
            error = "identity-mismatch"
            raise ElmError(error)

        if session_task == "engine":
            try:
                spec = load_engine_spec()
                spec["pids"] = select_pids(spec, plan["engine"]["selectedPids"])
                spec["authorizedHex"] = frozenset(["0100"] + [row["requestHex"] for row in spec["pids"]])
            except EngineSpecError as e:
                error = e.code
                raise ElmError(error) from e
            auth = spec["authorizedHex"]
            # Identity is qualified on the named manufacturer route first. Mode
            # 01 is a separate, fixed functional route: the old 1089/7E0 route
            # returned 7F0111 on this car. Never retry or guess another session.
            stage_guard()
            client.send_at("ATPC", end, require_ok=True)
            client.configure_standard_engine(end)
            engine_state["acquisitionRoute"] = plan["engine"]["acquisitionRoute"]
            persist()

            def wait_interval() -> None:
                until = time.monotonic() + engine_state["intervalMs"] / 1000.0
                while True:
                    stage_guard()
                    now = time.monotonic()
                    if now >= until:
                        return
                    cancel.wait(timeout=min(0.05, until - now, max(0.0, end - now)))

            stage_guard()
            req_deadline = min(end, time.monotonic() + 15.0)
            resp = transaction( "0100", req_deadline, None, engine_authorized=auth)
            if not resp.get("ok"):
                persist()
                error = resp.get("error") or "engine-support-failed"
                raise ElmError(error)
            try:
                mask = parse_0100(resp.get("payload_hex") or "")
            except EngineSpecError as e:
                persist()
                error = e.code
                raise ElmError(error) from e
            supported_rows, unsupported = split_support(spec, mask)
            engine_state["supportedPids"] = [p["pid"] for p in supported_rows]
            engine_state["unsupportedPids"] = unsupported
            persist()
            if not supported_rows:
                error = "no-supported-engine-pids"
                raise ElmError(error)
            total = 1 + len(ident_ops) + 1 + engine_state["sampleCycles"] * len(supported_rows)
            completed += 1
            prog("engine")
            n_cycles = engine_state["sampleCycles"]
            for cycle in range(1, n_cycles + 1):
                for row in supported_rows:
                    stage_guard()
                    req_deadline = min(end, time.monotonic() + 15.0)
                    resp = transaction( row["requestHex"], req_deadline, None, engine_authorized=auth)
                    if not resp.get("ok"):
                        persist()
                        error = resp.get("error") or "engine-pid-failed"
                        raise ElmError(error)
                    try:
                        value = decode_mode01(row, resp.get("payload_hex") or "")
                    except EngineSpecError as e:
                        persist()
                        error = e.code
                        raise ElmError(error) from e
                    engine_state["samples"].append(
                        {
                            "pid": row["pid"],
                            "label": row["label"],
                            "value": value,
                            "unit": row["unit"],
                            "capturedUtc": _utc(),
                            "elapsedMs": int((time.monotonic() - t0) * 1000),
                            "cycle": cycle,
                            "synthetic": simulation,
                        }
                    )
                    completed += 1
                    persist()
                    prog("engine")
                engine_state["completedCycles"] = cycle
                persist()
                if cycle < n_cycles:
                    wait_interval()

        for d in dep_ops:
            oid = d["operationId"]
            if oid in skip_dependents:
                kept = next((r for r in prior_results if r.get("operationId") == oid), None)
                if kept is None:
                    raise SessionError("resume-evidence-invalid")
                rec = dict(kept)
                rec["skippedResume"] = True
                rec["historical"] = True
                rec["freshlyRead"] = False
                rec["sourceRunId"] = resume_from
                rec["role"] = "dependent"
                rec["operationId"] = oid
                results.append(rec)
                completed += 1
                prog("read", oid)
                persist()
                continue
            stage_guard()
            op = operation(profile, oid)
            req_deadline = min(end, time.monotonic() + 15.0)
            resp = transaction( op["requestHex"], req_deadline, allowed)
            rec = {
                "role": "dependent",
                "operationId": oid,
                "ok": False,
                "payload_hex": resp.get("payload_hex"),
                "error": resp.get("error"),
                "decoded": None,
            }
            if not resp.get("ok"):
                rec["error"] = resp.get("error") or "dependent-failed"
            else:
                final = resp.get("payload_hex") or ""
                if not final.upper().startswith(op["positivePrefixHex"].upper()):
                    rec["error"] = "positive-prefix-mismatch"
                else:
                    decoded = decode_payload(final, op["decode"])
                    rec["decoded"] = decoded
                    rec["ok"] = bool(decoded.get("ok"))
                    if not rec["ok"]:
                        rec["error"] = decoded.get("reason") or "decode-incomplete"
            rec["capturedUtc"] = _utc()
            rec["sourceRunId"] = run_id
            rec["freshlyRead"] = True
            rec["historical"] = False
            rec["synthetic"] = simulation
            results.append(rec)
            completed += 1
            prog("read", oid)
            persist()
            if not rec["ok"]:
                error = rec["error"] or "dependent-failed"
                raise ElmError(error)

        if session_task == "clear":
            auth = plan.get("clearAuthorization") or {}
            req_hex = auth.get("requestHex")
            pos_hex = auth.get("expectedPositiveHex")
            known = PROFILE_CLEAR.get(profile_id) or {}
            if (
                req_hex != known.get("requestHex")
                or pos_hex != known.get("expectedPositiveHex")
                or not req_hex
                or not pos_hex
            ):
                error = "clear-spec-invalid"
                raise SessionError("clear-spec-invalid")
            if mode == "live" and not auth.get("liveClearEnabled"):
                error = "live-clear-disabled"
                raise SessionError("live-clear-disabled")
            pre = [dict(r) for r in results if r.get("role") == "dependent"]
            ident_rows = [dict(r) for r in results if r.get("role") == "identity"]
            if not pre or any(r.get("ok") is not True or r.get("freshlyRead") is not True for r in pre):
                error = "pre-clear-invalid"
                raise ElmError(error)
            if result.get("identityQualification") is None or not result["identityQualification"].get("observedProfileMatch"):
                error = "pre-clear-invalid"
                raise ElmError(error)
            persist()
            snap = {
                "kind": "pre-clear",
                "runId": run_id,
                "profileId": profile_id,
                "sessionTask": "clear",
                "identityQualification": result.get("identityQualification"),
                "identityResults": ident_rows,
                "dependentResults": pre,
                "raw": list(client.raw_log if client is not None else []),
                "capturedUtc": _utc(),
            }
            _atomic_write(dest / "pre-clear.json", snap)
            if not (dest / "pre-clear.json").is_file():
                error = "pre-clear-persist-failed"
                raise ElmError(error)
            result["preClear"] = {
                "runId": run_id,
                "artifactDir": str(dest),
                "snapshot": "pre-clear.json",
                "capturedUtc": snap["capturedUtc"],
                "results": pre,
            }
            stage_guard()
            prog("clear")
            req_deadline = min(end, time.monotonic() + 15.0)
            resp = transaction( req_hex, req_deadline, None, clear_authorized=req_hex)
            got = (resp.get("payload_hex") or "").upper()
            pending = resp.get("pending") or []
            clear_err = resp.get("error")
            if clear_err == "nrc78-prompt-without-final":
                clear_err = "nrc78-timeout"
            clear_succeeded = bool(resp.get("ok") and got == pos_hex)
            if clear_succeeded:
                clear_err = None
            elif resp.get("ok") and got != pos_hex:
                clear_err = "clear-positive-mismatch"
            elif not clear_err:
                clear_err = "clear-failed"
            result["clear"] = {
                "requestHex": req_hex,
                "expectedPositiveHex": pos_hex,
                "protocol": known.get("protocol"),
                "payload_hex": resp.get("payload_hex"),
                "ok": clear_succeeded,
                "error": clear_err,
                "pending": pending,
                "attemptCount": 1,
                "clearSucceeded": clear_succeeded,
                "sourceObservedResponse": False,
            }
            persist()
            if not clear_succeeded:
                error = clear_err
                raise ElmError(error)
            results[:] = [r for r in results if r.get("role") != "dependent"]
            post_ok = True
            post_err = None
            for d in dep_ops:
                oid = d["operationId"]
                stage_guard()
                op = operation(profile, oid)
                req_deadline = min(end, time.monotonic() + 15.0)
                resp = transaction( op["requestHex"], req_deadline, allowed)
                rec = {
                    "role": "dependent",
                    "operationId": oid,
                    "ok": False,
                    "payload_hex": resp.get("payload_hex"),
                    "error": resp.get("error"),
                    "decoded": None,
                }
                if not resp.get("ok"):
                    rec["error"] = resp.get("error") or "dependent-failed"
                else:
                    final = resp.get("payload_hex") or ""
                    if not final.upper().startswith(op["positivePrefixHex"].upper()):
                        rec["error"] = "positive-prefix-mismatch"
                    else:
                        decoded = decode_payload(final, op["decode"])
                        rec["decoded"] = decoded
                        rec["ok"] = bool(decoded.get("ok"))
                        if not rec["ok"]:
                            rec["error"] = decoded.get("reason") or "decode-incomplete"
                rec["capturedUtc"] = _utc()
                rec["sourceRunId"] = run_id
                rec["freshlyRead"] = True
                rec["historical"] = False
                rec["synthetic"] = simulation
                rec["postClear"] = True
                results.append(rec)
                completed += 1
                prog("read", oid)
                persist()
                if not rec["ok"]:
                    post_ok = False
                    post_err = rec["error"] or "post-clear-read-failed"
                    break
            if not post_ok:
                hist = []
                for r in pre:
                    row = dict(r)
                    row["historical"] = True
                    row["freshlyRead"] = False
                    row["postClear"] = False
                    hist.append(row)
                posts = [r for r in results if r.get("role") == "dependent"]
                results[:] = [r for r in results if r.get("role") != "dependent"] + hist + posts
                result["clear"]["outcome"] = "unknown"
                result["clear"]["residualDtc"] = None
                error = post_err
                raise ElmError(error)
            residual = False
            after_counts = []
            for r in results:
                if r.get("role") != "dependent" or not r.get("postClear"):
                    continue
                kind = ((operation(profile, r["operationId"]).get("decode") or {}).get("type"))
                if kind not in ("kwp-dtc-18", "uds-dtc-19-02"):
                    continue
                recs = (r.get("decoded") or {}).get("records")
                if not isinstance(recs, list):
                    error = "post-clear-read-failed"
                    raise ElmError(error)
                after_counts.append(len(recs))
                if recs:
                    residual = True
            result["clear"]["dtcCountAfter"] = after_counts[0] if len(after_counts) == 1 else after_counts
            result["clear"]["residualDtc"] = residual
            result["clear"]["outcome"] = "cleared-residual" if residual else "cleared-zero"
            if residual:
                error = "residual-dtc-after-clear"
                raise ElmError(error)

        status = "cancelled" if cancel.is_set() else "completed"
        if status == "completed":
            result["ok"] = True
            error = None
    except KeyboardInterrupt:
        error = "cancelled"
        status = "cancelled"
        result["ok"] = False
    except ElmError as e:
        error = str(e)
        status = "cancelled" if error == "cancelled" else "failed"
    except SessionError as e:
        error = e.code
        status = "failed"
    except Exception as e:  # noqa: BLE001
        error = str(e)
        status = "failed"
    finally:
        if session_task == "read" and error in ("NO DATA", "UNABLE TO CONNECT", "negative-response") and client is not None and not vnci_live and not cancel.is_set():
            try:
                from .elm import adapter_identity_ok
                if adapter_identity_ok(client.send_at("ATI")):
                    result["linkHealth"] = "verified"
                    result["candidateFailure"] = "no-response" if error != "negative-response" else "negative-response"
            except Exception:
                pass
        result["results"] = results
        result["endedUtc"] = _utc()
        result["simulation"] = simulation
        result["liveVerified"] = False
        result["writePayload"] = None
        result["error"] = error
        result["status"] = status
        result["ok"] = status == "completed"
        cleanup()
        if client is not None and not vnci_live:
            from .can_monitor import parse_frame
            received, line = [], bytearray()
            discard_line = False
            for chunk in client.raw_log:
                if chunk.get("dir") != "rx" or chunk.get("note") or type(chunk.get("timestampUs")) is not int:
                    line.clear()
                    discard_line = False
                    continue
                for byte in bytes.fromhex(chunk.get("hex", "")):
                    if byte in (10, 13, 62):
                        frame = None if discard_line else parse_frame(bytes(line).strip())
                        line.clear()
                        discard_line = False
                        if frame:
                            can_id, extended, data = frame
                            received.append({"canId": can_id, "extended": extended, "dataHex": data.hex().upper(),
                                "timestampUs": chunk["timestampUs"], "timestampSource": "host-chunk-arrival"})
                    elif not discard_line:
                        if len(line) < 4096:
                            line.append(byte)
                        else:
                            line.clear()
                            discard_line = True
            result["receivedCanFrames"] = received
            result["captureScope"] = "adapter-reported-received-CAN-only; no reconstructed-PDU or invented TX"
        if persist_error:
            result["ok"] = False
            if result["status"] == "completed":
                result["status"] = "failed"
            result["error"] = result.get("error") or "persist-failed"
    return result


def run_read_dtc(
    profile_id: str,
    operation_id: str,
    *,
    serial_module=None,
    list_ports=None,
    port=None,
    budget_s: float = 60.0,
    out_dir: Path | None = None,
) -> dict:
    mode = "simulation" if port is not None else "live"
    out = run_session(
        profile_id=profile_id,
        mode=mode,
        scenario="success",
        operation_ids=[operation_id],
        confirmed_read_only=True,
        x431_inactive=True,
        port=port,
        budget_s=budget_s,
        artifact_root=Path(out_dir) if out_dir is not None else None,
        serial_module=serial_module,
        list_ports=list_ports,
    )
    dtc = next((r for r in out.get("results") or [] if r.get("operationId") == operation_id), None)
    mapped = {
        "ok": bool(out.get("ok") and dtc and dtc.get("ok")),
        "profileId": profile_id,
        "operationId": operation_id,
        "startedUtc": out.get("startedUtc"),
        "hostStamp": out.get("hostStamp"),
        "adapter": out.get("adapter"),
        "payload_hex": (dtc or {}).get("payload_hex"),
        "decoded": (dtc or {}).get("decoded"),
        "error": out.get("error") or (dtc or {}).get("error"),
        "restoration": out.get("restoration"),
        "endedUtc": out.get("endedUtc"),
        "outDir": out.get("artifactDir"),
        "identityQualification": out.get("identityQualification"),
        "simulation": out.get("simulation"),
        "liveVerified": False,
        "writePayload": None,
        "session": out,
        "x431MustBeInactive": True,
        "transportCaveat": (
            "vLinker clone ELM327 v2.3 CAF0/ATH1/ATS1/CFC/FC/CSM0 ISO-TP not proven on this car; "
            "session 10 89/10 03 is source-observed; ATPC does not prove ECU restore."
        ),
    }
    return mapped


def _parse_request(raw: str) -> dict:
    if len(raw.encode("utf-8")) > MAX_STDIN:
        raise SessionError("stdin-oversize")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SessionError("stdin-json") from e
    if not isinstance(obj, dict):
        raise SessionError("stdin-not-object")
    extra = set(obj) - ALLOWED_KEYS
    if extra:
        raise SessionError("unexpected-keys")
    if "action" in obj and not isinstance(obj.get("action"), str):
        raise SessionError("invalid-action")
    action = obj.get("action")
    if action not in ("prepare", "run"):
        raise SessionError("invalid-action")
    pid = obj.get("profileId")
    if not isinstance(pid, str):
        raise SessionError("profileId-type")
    mode = obj.get("mode", "simulation")
    if not isinstance(mode, str) or mode not in ("simulation", "live"):
        raise SessionError("invalid-mode")
    scenario = obj.get("scenario", "success")
    if not isinstance(scenario, str):
        raise SessionError("scenario-type")
    if "scenario" in obj and mode == "live":
        raise SessionError("scenario-live-forbidden")
    if mode == "simulation" and scenario not in SCENARIOS:
        raise SessionError("invalid-scenario")
    if "operationIds" in obj and obj["operationIds"] is not None and not isinstance(obj["operationIds"], list):
        raise SessionError("operationIds-type")
    if "resumeRunId" in obj and obj["resumeRunId"] is not None and not isinstance(obj["resumeRunId"], str):
        raise SessionError("resumeRunId-type")
    for k in ("confirmedReadOnly", "x431Inactive", "confirmedClearDtc"):
        if k in obj and not isinstance(obj[k], bool):
            raise SessionError(f"{k}-type")
    if "sessionTask" in obj and obj["sessionTask"] is not None:
        if not isinstance(obj["sessionTask"], str) or obj["sessionTask"] not in ("read", "clear", "engine"):
            raise SessionError("invalid-session-task")
    task = obj.get("sessionTask") or "read"
    if "selectedPids" in obj:
        if task != "engine":
            raise SessionError("engine-options-without-task")
        try:
            if obj["selectedPids"] is None:
                raise EngineSpecError("invalid-selected-pids")
            select_pids(load_engine_spec(), obj["selectedPids"])
        except EngineSpecError as e:
            raise SessionError(e.code) from e
    for opt in ("sampleCycles", "intervalMs"):
        if opt not in obj:
            continue
        if task != "engine":
            raise SessionError("engine-options-without-task")
        val = obj[opt]
        if val is None:
            continue
        if isinstance(val, bool) or type(val) is not int:
            raise SessionError(f"{opt}-type")
    if "deviceId" in obj:
        from .connection import valid_device_id

        if obj["deviceId"] is not None and not valid_device_id(obj["deviceId"]):
            raise SessionError("deviceId-invalid")
    return obj


def _print_ndjson(obj, file=None) -> None:
    out = file or sys.stdout
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def stdio_loop(stdin=None, stdout=None, *, artifact_root: Path | None = None) -> int:
    inf = stdin or sys.stdin
    outf = stdout or sys.stdout
    try:
        input_fd = inf.fileno()
    except (AttributeError, OSError, io.UnsupportedOperation):
        raw = inf.readline(MAX_STDIN + 1)
    else:
        # Do not let TextIOWrapper read ahead into the following cancel line:
        # the watcher below consumes the same pipe via os.read.
        first_line = bytearray()
        while len(first_line) <= MAX_STDIN:
            byte = os.read(input_fd, 1)
            if not byte:
                break
            first_line.extend(byte)
            if byte == b"\n":
                break
        raw = bytes(first_line)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if len(raw.encode("utf-8")) > MAX_STDIN:
        _print_ndjson({"type": "result", "ok": False, "error": "stdin-oversize", "liveVerified": False, "writePayload": None, "simulation": True}, outf)
        return 2
    try:
        req = _parse_request(raw.strip())
    except SessionError as e:
        _print_ndjson(
            {"type": "result", "ok": False, "error": e.code, "status": "failed", "liveVerified": False, "writePayload": None, "simulation": True},
            outf,
        )
        return 2
    if req["action"] == "prepare":
        plan = prepare(
            req.get("profileId"),
            req.get("operationIds"),
            session_task=req.get("sessionTask") or "read",
            sample_cycles=req.get("sampleCycles"),
            interval_ms=req.get("intervalMs"),
            selected_pids=req.get("selectedPids"),
        )
        _print_ndjson(plan, outf)
        return 0 if plan.get("ok") else 2
    cancel = threading.Event()
    stop_watch = threading.Event()

    def watch() -> None:
        buf = b""
        while not stop_watch.is_set():
            n = _pipe_available(inf)
            if n is None:
                if stop_watch.wait(0.05):
                    return
                continue
            if n <= 0:
                if stop_watch.wait(0.05):
                    return
                continue
            if stop_watch.is_set():
                return
            try:
                chunk = os.read(inf.fileno(), min(n, 4096))
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("action") == "cancel":
                    cancel.set()
                    return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        def sink(ev):
            _print_ndjson(ev, outf)

        out = run_session(
            profile_id=req["profileId"],
            mode=req.get("mode", "simulation"),
            scenario=req.get("scenario", "success"),
            operation_ids=req.get("operationIds"),
            resume_run_id=req.get("resumeRunId"),
            confirmed_read_only=req["confirmedReadOnly"] if "confirmedReadOnly" in req else False,
            x431_inactive=req["x431Inactive"] if "x431Inactive" in req else False,
            session_task=req.get("sessionTask") or "read",
            confirmed_clear_dtc=req["confirmedClearDtc"] if "confirmedClearDtc" in req else False,
            sample_cycles=req.get("sampleCycles"),
            interval_ms=req.get("intervalMs"),
            selected_pids=req.get("selectedPids"),
            cancel_event=cancel,
            progress_sink=sink,
            artifact_root=artifact_root,
            device_id=req.get("deviceId"),
            retain_artifacts=req.get("sessionTask") == "clear" or os.environ.get("PORSCHE981_SESSION_TRANSIENT") != "1",
        )
        _print_ndjson(out, outf)
        if out.get("status") == "cancelled":
            return 1
        if not out.get("ok"):
            return 1 if out.get("error") not in (
                "invalid-mode",
                "invalid-scenario",
                "live-confirmations-required",
                "live-clear-confirmation-required",
                "clear-conflicts-read-only",
                "clear-resume-forbidden",
                "clear-operationIds-forbidden",
                "invalid-session-task",
                "engine-operationIds-forbidden",
                "engine-resume-forbidden",
                "engine-profile-unsupported",
                "engine-options-without-task",
                "sampleCycles-type",
                "sampleCycles-range",
                "intervalMs-type",
                "intervalMs-range",
                "clear-flag-without-task",
                "unknown-profile",
                "operationIds-not-dependent",
                "unexpected-keys",
                "simulation-checkpoint-not-live",
                "resume-mismatch",
                "plan-mismatch",
                "catalog-fingerprint-mismatch",
                "invalid-run-id",
                "deviceId-invalid",
                "scenario-type",
                "confirmedReadOnly-type",
                "x431Inactive-type",
            ) else 2
        return 0
    except KeyboardInterrupt:
        cancel.set()
        _print_ndjson({"type": "result", "ok": False, "status": "cancelled", "error": "cancelled", "liveVerified": False, "writePayload": None}, outf)
        return 1
    finally:
        stop_watch.set()
        watcher.join(0.3)


def _cli_artifact_root(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path | None:
    global_root = args.artifact_root_global
    cmd_root = getattr(args, "artifact_root_cmd", None)
    if global_root and cmd_root and Path(global_root) != Path(cmd_root):
        parser.error("conflicting --artifact-root")
    chosen = global_root or cmd_root
    return Path(chosen) if chosen else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.diagnostics.sessions")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--artifact-root", dest="artifact_root_global", default=None, metavar="DIR")
    sub = parser.add_subparsers(dest="cmd")
    p_prep = sub.add_parser("prepare")
    p_prep.add_argument("--profile", required=True)
    p_prep.add_argument("--session-task", default="read", choices=("read", "clear", "engine"))
    p_prep.add_argument("--sample-cycles", type=int, default=None)
    p_prep.add_argument("--interval-ms", type=int, default=None)
    p_prep.add_argument("--artifact-root", dest="artifact_root_cmd", default=None, metavar="DIR")
    p_run = sub.add_parser("run")
    p_run.add_argument("--profile", required=True)
    p_run.add_argument("--mode", default="simulation", choices=("simulation", "live"))
    p_run.add_argument("--scenario", default="success", choices=SCENARIOS)
    p_run.add_argument("--operation", action="append", dest="operations")
    p_run.add_argument("--session-task", default="read", choices=("read", "clear", "engine"))
    p_run.add_argument("--sample-cycles", type=int, default=None)
    p_run.add_argument("--interval-ms", type=int, default=None)
    p_run.add_argument("--yes-read-only-live", action="store_true")
    p_run.add_argument("--x431-inactive", action="store_true")
    p_run.add_argument("--yes-clear-dtc", action="store_true")
    p_run.add_argument("--artifact-root", dest="artifact_root_cmd", default=None, metavar="DIR")
    args = parser.parse_args(argv)
    root = _cli_artifact_root(parser, args)
    if args.stdio:
        return stdio_loop(artifact_root=root)
    if args.cmd is None:
        parser.print_help()
        return 2
    if args.cmd == "prepare":
        plan = prepare(
            args.profile,
            session_task=args.session_task,
            sample_cycles=args.sample_cycles,
            interval_ms=args.interval_ms,
        )
        _print_ndjson(plan)
        return 0 if plan.get("ok") else 2
    out = run_session(
        profile_id=args.profile,
        mode=args.mode,
        scenario=args.scenario,
        operation_ids=args.operations,
        confirmed_read_only=args.yes_read_only_live,
        x431_inactive=args.x431_inactive,
        session_task=args.session_task,
        confirmed_clear_dtc=args.yes_clear_dtc,
        sample_cycles=args.sample_cycles,
        interval_ms=args.interval_ms,
        artifact_root=root,
    )
    _print_ndjson(out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(1)
