"""Separately confirmed standard OBD engine field experiment; no automatic fallback."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import VLIKER_MAC
from .elm import ElmClient, ElmError
from .engine_obd import ENGINE_PROFILE, decode_mode01, load_engine_spec, parse_0100, split_support
from .hashutil import sha256_file
from .qualification import qualify_identity
from .session_simulator import SessionSimPort
from .sessions import DEFAULT_ARTIFACT_ROOT, _FileLock, _atomic_write, _open_live_port

TASK = "standard-engine-field"
SOURCE = "https://www.elmelectronics.com/wp-content/uploads/2020/05/ELM327DSL.pdf#page=43"


def utc():
    return datetime.now(timezone.utc).isoformat()


def plan(cycles=5, interval_ms=1000):
    if type(cycles) is not int or not 1 <= cycles <= 10:
        raise ValueError("sample-cycles-out-of-range")
    if type(interval_ms) is not int or not 500 <= interval_ms <= 5000:
        raise ValueError("interval-ms-out-of-range")
    spec = load_engine_spec()
    return dict(sessionTask=TASK, txId="7DF", rxId="7E8", ecuSessionControl=None,
                supportRequest="0100", pids=spec["pids"], sampleCycles=cycles,
                intervalMs=interval_ms, source=SOURCE, maxRequests=1 + 6 * cycles,
                automaticFallback=False, retries=0, liveVerified=False,
                identityNote="Prior DME identity and same-vehicle user declaration; no VIN read in this run")


def baseline(path, live):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("profileId") != ENGINE_PROFILE or data.get("ok") is not True
            or data.get("sessionTask") != "read" or data.get("status") != "completed"):
        raise ValueError("qualified-dme-read-required")
    if live and (data.get("mode") != "live" or data.get("simulation") is not False):
        raise ValueError("synthetic-identity-not-live-evidence")
    identity = {r["field"]: r["decoded"]["text"] for r in data.get("results", [])
                if r.get("role") == "identity" and r.get("ok") is True}
    q = qualify_identity(dict(generation="981", ecuId=1, identity=identity))
    if not q["observedProfileMatch"] or data.get("restoration", {}).get("errors") != []:
        raise ValueError("baseline-identity-or-cleanup-invalid")
    raw = path.parent / "raw.json"
    if not raw.is_file():
        raise ValueError("baseline-raw-missing")
    return dict(manifestPath=str(path.resolve()), manifestSha256=sha256_file(path),
                rawSha256=sha256_file(raw), runId=data.get("runId"), identity=identity,
                qualification=q, vinVerified=False)


def confirmation(path, evidence):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if (data.get("sessionTask") != TASK or data.get("adapterMac") != VLIKER_MAC
            or data.get("baselineManifestSha256") != evidence["manifestSha256"]
            or not isinstance(data.get("userStatement"), str)
            or not data["userStatement"].strip()):
        raise ValueError("standard-engine-confirmation-required")
    for field in ("x431InactiveConfirmed", "sameVehicleConfirmed", "stationaryConfirmed", "standardAddressingConfirmed"):
        if data.get(field) is not True:
            raise ValueError("standard-engine-confirmation-required:" + field)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["confirmedUtc"])).total_seconds()
    if not 0 <= age <= 3600:
        raise ValueError("standard-engine-confirmation-stale")
    return data


def run(*, identity_manifest, artifact_root, mode="simulation", confirmation_file=None,
        yes_standard_engine_live=False, cycles=5, interval_ms=1000, port=None):
    result = dict(type="result", sessionTask=TASK, mode=mode, simulation=mode == "simulation",
                  ok=False, status="failed", error=None, startedUtc=utc(), results=[], samples=[],
                  supportedPids=None, unsupportedPids=None, completedCycles=0, writePayload=None,
                  restoration=dict(errors=["not-opened"], ecuRestorationProven=False),
                  standardEngineReadObserved=False, liveVerified=False, artifactDir=None)
    client = None
    lock = None
    held = False
    dest = None
    try:
        if mode not in ("simulation", "live"):
            raise ValueError("invalid-mode")
        result["plan"] = plan(cycles, interval_ms)
        spec = load_engine_spec()
        result["identityEvidence"] = baseline(identity_manifest, mode == "live")
        if mode == "live":
            if yes_standard_engine_live is not True or confirmation_file is None:
                raise ValueError("standard-engine-confirmation-required")
            result["onsiteConfirmation"] = confirmation(confirmation_file, result["identityEvidence"])
        lock = _FileLock(mode, Path(artifact_root))
        lock.acquire()
        held = True
        dest = Path(artifact_root) / ("standard-" + uuid.uuid4().hex)
        dest.mkdir(parents=True, exist_ok=False)
        result["artifactDir"] = str(dest.resolve())
        _atomic_write(dest / "manifest.json", result)
        if port is None:
            port = SessionSimPort(ENGINE_PROFILE) if mode == "simulation" else _open_live_port()
        client = ElmClient(port)
        deadline = time.monotonic() + 60
        result["adapter"] = client.validate_adapter()
        client.configure_standard_engine(deadline)

        def persist():
            _atomic_write(dest / "raw.json", client.raw_log)
            _atomic_write(dest / "manifest.json", result)

        def request(hx):
            if hx not in spec["authorizedHex"]:
                raise ValueError("standard-engine-payload-not-allowed")
            response = client.request(hx, min(deadline, time.monotonic() + 15))
            result["results"].append(dict(requestHex=hx, txId="7DF", rxId="7E8",
                                           capturedUtc=utc(), **response))
            persist()
            if not response["ok"]:
                raise ElmError(response.get("error") or "standard-engine-response-failed")
            return response["payload_hex"]

        mask = parse_0100(request("0100"))
        supported, unsupported = split_support(spec, mask)
        result["supportedPids"] = [row["pid"] for row in supported]
        result["unsupportedPids"] = unsupported
        persist()
        if not supported:
            raise ValueError("no-supported-engine-pids")
        for cycle in range(1, cycles + 1):
            for row in supported:
                payload = request(row["requestHex"])
                result["samples"].append(dict(pid=row["pid"], label=row["label"], unit=row["unit"],
                    value=decode_mode01(row, payload), payloadHex=payload, cycle=cycle,
                    capturedUtc=utc(), synthetic=mode == "simulation", responderId="7E8"))
                persist()
            result["completedCycles"] = cycle
            persist()
            if cycle < cycles:
                wait_until = time.monotonic() + interval_ms / 1000
                if wait_until >= deadline:
                    raise ElmError("deadline-expired")
                time.sleep(interval_ms / 1000)
        result.update(ok=True, status="completed", standardEngineReadObserved=mode == "live")
    except (Exception, KeyboardInterrupt) as error:
        result["error"] = "cancelled" if isinstance(error, KeyboardInterrupt) else str(error)
    finally:
        if client is not None:
            result["restoration"] = dict(**client.close_restore(), ecuRestorationProven=False,
                                           note="ATPC is adapter close only")
        elif port is not None:
            try:
                port.close()
                result["restoration"]["errors"] = []
            except Exception as error:
                result["restoration"]["errors"] = [str(error)]
        if result["restoration"]["errors"] and result["ok"]:
            result.update(ok=False, status="failed", error="adapter-close-failed", standardEngineReadObserved=False)
        result["endedUtc"] = utc()
        try:
            if dest is not None:
                _atomic_write(dest / "raw.json", client.raw_log if client is not None else [])
                _atomic_write(dest / "manifest.json", result)
        finally:
            if held:
                lock.release()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--mode", choices=("simulation", "live"), default="simulation")
    parser.add_argument("--identity-manifest", type=Path)
    parser.add_argument("--confirmation-file", type=Path)
    parser.add_argument("--yes-standard-engine-live", action="store_true")
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--sample-cycles", type=int, default=5)
    parser.add_argument("--interval-ms", type=int, default=1000)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        output = dict(type="plan", ok=True, plan=plan(args.sample_cycles, args.interval_ms))
    else:
        if not args.identity_manifest:
            parser.error("run requires --identity-manifest")
        output = run(identity_manifest=args.identity_manifest, artifact_root=args.artifact_root, mode=args.mode,
                     confirmation_file=args.confirmation_file, yes_standard_engine_live=args.yes_standard_engine_live,
                     cycles=args.sample_cycles, interval_ms=args.interval_ms)
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
