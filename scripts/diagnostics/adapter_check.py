"""Powered-adapter-only AT/STI preflight. No ECU bytes. Coordinator owns real hardware."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .connection import ELM_MODELS, ELM_SERIAL_BAUDS, open_selected_port, valid_device_id, validate_serial_baud
from .elm import SETTING_CMDS_BEFORE_IDS, ElmClient, ElmError, _setting_ok, adapter_identity_ok, stn_firmware_identity_ok
from .hashutil import sha256_file
from .sessions import DEFAULT_ARTIFACT_ROOT, SessionError, _FileLock

PAIR_IDS = (("7E0", "7E8"), ("710", "77A"))
QUERIES = ("ATI", "STI", "ATDPN", "ATRV")
CLEANUP = ("ATCSM1", "ATCFC0", "ATPC", "ATWS", "ATDPN")
CLEANUP_REQUIRE_OK = frozenset({"ATCSM1", "ATCFC0", "ATPC"})
CMD_TIMEOUT_S = 4.0
TOTAL_BUDGET_S = 90.0
REOPEN_COUNT = 2
_VOLT = re.compile(r"\d+(?:\.\d+)?\s*V", re.I)
RESTORATION_NOTE = (
    "ATWS resets volatile defaults; original adapter state is not fully knowable or restorable"
)
_ARTIFACT_NAMES = ("manifest.json", "raw-traffic.json")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _at_bytes(cmd: str) -> bytes:
    return (cmd.strip() + "\r").encode("ascii")


def allowed_payloads() -> frozenset[bytes]:
    cmds = list(QUERIES) + list(SETTING_CMDS_BEFORE_IDS) + list(CLEANUP)
    for tx, rx in PAIR_IDS:
        cmds.extend(
            [f"ATSH {tx}", f"ATCRA {rx}", f"ATFCSH {tx}", "ATFCSD 300000", "ATFCSM1", "ATCSM0"]
        )
    return frozenset(_at_bytes(c) for c in cmds)


ALLOWED_AT = allowed_payloads()


def parse_voltage_string(atrv: str | None) -> str | None:
    if not atrv:
        return None
    m = _VOLT.search(atrv)
    return m.group(0).replace(" ", "") if m else None


def parse_protocol_string(atdpn: str | None) -> str | None:
    if not atdpn:
        return None
    lines = [ln.strip() for ln in atdpn.replace("\r", "\n").split("\n") if ln.strip() and ln.strip() != ">"]
    lines = [ln for ln in lines if ln.upper() != "ATDPN"]
    return lines[0] if lines else None


def _no_error_token(text: str) -> bool:
    lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n") if ln.strip() and ln.strip() != ">"]
    if any(ln == "?" for ln in lines):
        return False
    blob = "\n".join(lines).upper()
    return "ERROR" not in blob and "UNABLE" not in blob


def command_accepted(cmd: str, text: str) -> bool:
    if not text or not _no_error_token(text):
        return False
    key = cmd.strip().upper()
    if key == "ATI":
        return adapter_identity_ok(text)
    if key == "STI":
        return stn_firmware_identity_ok(text)
    if key == "ATDPN":
        tok = parse_protocol_string(text)
        return bool(tok and re.fullmatch(r"A?[0-9A-C]", tok.upper()))
    if key == "ATRV":
        return parse_voltage_string(text) is not None
    if key == "ATWS":
        return adapter_identity_ok(text)
    return _setting_ok(text)


def _apply_records(verified: dict[str, bool], records: list) -> None:
    verified.clear()
    for rec in records:
        cmd = rec.get("command")
        if not cmd:
            continue
        ok = bool(rec.get("success"))
        verified[cmd] = verified[cmd] and ok if cmd in verified else ok


class AtOnlyGuard:
    """Deny any non-allowlisted bytes before the underlying write."""

    def __init__(self, inner, traffic: list):
        self._inner = inner
        self.traffic = traffic
        self.denied = None
        self._close_attempted = False
        self._close_error = None

    def write(self, data: bytes) -> int:
        if data not in ALLOWED_AT:
            self.denied = data
            raise ElmError("at-only-denied:" + data.decode("latin-1", errors="replace"))
        self.traffic.append({"utc": _utc(), "dir": "tx", "hex": data.hex().upper()})
        return self._inner.write(data)

    def read(self, size: int = 1) -> bytes:
        chunk = self._inner.read(size)
        if chunk:
            self.traffic.append({"utc": _utc(), "dir": "rx", "hex": chunk.hex().upper()})
        return chunk

    @property
    def write_timeout(self):
        return self._inner.write_timeout

    @write_timeout.setter
    def write_timeout(self, value):
        self._inner.write_timeout = value

    def close(self) -> None:
        if not self._close_attempted:
            self._close_attempted = True
            try:
                self._inner.close()
            except Exception as error:
                self._close_error = error
        if self._close_error is not None:
            raise self._close_error


class BoundedElm(ElmClient):
    """Clamp every AT command to min(global end, now+4s). Record completion, not TX alone."""

    def __init__(self, port, *, global_end: float, records: list, timeout_s: float = CMD_TIMEOUT_S, expected_model=None):
        super().__init__(port, timeout_s=timeout_s, expected_model=expected_model)
        self._global_end = global_end
        self.cmd_records = records

    def _clamp_deadline(self, deadline: float | None) -> float:
        cap = min(self._global_end, self._now() + CMD_TIMEOUT_S)
        return cap if deadline is None else min(deadline, cap)

    def _send_at_unlocked(self, cmd: str, deadline: float | None = None, *, require_ok: bool = False) -> str:
        dl = self._clamp_deadline(deadline)
        rec = {"command": cmd, "utc": _utc(), "success": False, "response": None, "error": None}
        try:
            text = ElmClient._send_at_unlocked(self, cmd, dl, require_ok=require_ok)
            rec["response"] = text
            rec["success"] = command_accepted(cmd, text)
            if not rec["success"]:
                rec["error"] = "response-invalid"
                raise ElmError(f"response-invalid:{cmd}:{text}")
            return text
        except Exception as e:  # noqa: BLE001
            rec["error"] = str(e)
            rec["success"] = False
            raise
        finally:
            self.cmd_records.append(rec)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _nonempty_artifacts(out_dir: Path) -> bool:
    for name in _ARTIFACT_NAMES:
        p = out_dir / name
        if p.is_file() and p.stat().st_size > 0:
            return True
    return False


def _open_port(port_factory, serial_module, list_ports, *, device_id, adapter_model, serial_baud, snapshot):
    if port_factory is not None:
        return port_factory(), None
    if snapshot is None and list_ports is not None:
        from .connection import pyserial_rows
        rows, errors = pyserial_rows(list_ports)
        snapshot = {'platform': 'win32', 'serial': [], 'bluetooth': [], 'pyserial': rows, 'errors': [errors] if errors else []}
    return open_selected_port(device_id, serial_module=serial_module, snapshot=snapshot,
        adapter_model=adapter_model, serial_baud=serial_baud)


def _close_quiet(port) -> list[str]:
    errors = []
    try:
        port.close()
    except Exception as e:  # noqa: BLE001
        errors.append(f"close:{e}")
    return errors


def _record_skip(records: list, cmd: str, error: str) -> None:
    records.append(
        {"command": cmd, "utc": _utc(), "success": False, "response": None, "error": error}
    )


def _cleanup_cmds(client: BoundedElm, end: float) -> dict:
    errors = []
    texts = {}
    for cmd in CLEANUP:
        if client._poisoned or client._closed:
            err = "client-poisoned:" + (client._poison_reason or "")
            errors.append(f"{cmd}:{err}")
            _record_skip(client.cmd_records, cmd, err)
            continue
        n = len(client.cmd_records)
        try:
            texts[cmd] = client.send_at(cmd, end, require_ok=cmd in CLEANUP_REQUIRE_OK)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cmd}:{e}")
            if len(client.cmd_records) == n:
                _record_skip(client.cmd_records, cmd, str(e))
    return {"errors": errors, "texts": texts, "note": RESTORATION_NOTE, "fullStateRestoreClaimed": False}


def _reopen_close_cmd(client: BoundedElm, end: float) -> dict:
    errors = []
    if client._poisoned or client._closed:
        err = "client-poisoned:" + (client._poison_reason or "")
        errors.append(f"ATPC:{err}")
        _record_skip(client.cmd_records, "ATPC", err)
        return {"errors": errors}
    n = len(client.cmd_records)
    try:
        client.send_at("ATPC", end, require_ok=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"ATPC:{e}")
        if len(client.cmd_records) == n:
            _record_skip(client.cmd_records, "ATPC", str(e))
    return {"errors": errors}


def run_adapter_check(
    *,
    confirmed_powered_bench: bool,
    out_dir: Path,
    port_factory=None,
    serial_module=None,
    list_ports=None,
    device_id=None,
    adapter_model=None,
    serial_baud=None,
    snapshot=None,
) -> dict:
    started = datetime.now(timezone.utc)
    out_dir = Path(out_dir)
    traffic: list = []
    verified: dict[str, bool] = {}
    connections: list = []
    records: list = []
    result = {
        "ok": False,
        "kind": "powered-adapter-only",
        "simulation": port_factory is not None,
        "deviceId": device_id,
        "adapterModel": adapter_model,
        "serialBaud": serial_baud,
        "hardwareQualified": False,
        "poweredBenchConfirmed": confirmed_powered_bench is True,
        "ecuRequestsSent": 0,
        "canWiresConnected": False,
        "canCommunicationVerified": False,
        "isotpVerified": False,
        "independentLiveVerified": False,
        "writePayload": None,
        "adapterCommandsVerified": verified,
        "commandRecords": records,
        "reopenCount": 0,
        "startedUtc": started.isoformat(),
        "error": None,
        "port": None,
        "connections": connections,
        "restoration": None,
        "initialProtocol": None,
        "finalProtocol": None,
        "voltageString": None,
        "atiText": None,
        "osOpenLimitation": "TOTAL_BUDGET_S excludes OS Serial() time if the OS blocks on open",
        "commandTimeoutS": CMD_TIMEOUT_S,
        "artifactDir": str(out_dir),
        "rawSha256": None,
        "lockReleased": False,
    }

    def persist() -> None:
        raw_path = out_dir / "raw-traffic.json"
        _write_json(raw_path, traffic)
        result["rawSha256"] = sha256_file(raw_path)
        result["endedUtc"] = _utc()
        result["ecuRequestsSent"] = sum(
            1
            for e in traffic
            if e.get("dir") == "tx"
            and not bytes.fromhex(e.get("hex") or "00").upper().startswith(b"AT")
            and bytes.fromhex(e.get("hex") or "00").upper() != b"STI\r"
        )
        _apply_records(verified, records)
        result["adapterCommandsVerified"] = dict(verified)
        result["ok"] = (
            result["error"] is None
            and result["reopenCount"] == REOPEN_COUNT
            and len(connections) == 1 + REOPEN_COUNT
            and all(c.get("ok") for c in connections)
            and bool(verified)
            and all(verified.values())
        )
        _write_json(out_dir / "manifest.json", {k: v for k, v in result.items()})

    if _nonempty_artifacts(out_dir):
        result["error"] = "output-artifacts-exist"
        return result

    if confirmed_powered_bench is not True:
        result["error"] = "powered-bench-confirmation-required"
        persist()
        return result

    try:
        if device_id is not None and (not valid_device_id(device_id) or device_id.startswith(('vnci:', 'pt3g:'))):
            raise ElmError('device-id-invalid-for-ELM-bench')
        if adapter_model is not None and (not isinstance(adapter_model, str) or adapter_model not in ELM_MODELS):
            raise ElmError('adapter-model-invalid')
        if port_factory is None and (device_id is None or adapter_model is None):
            raise ElmError('explicit-device-and-model-required')
        validate_serial_baud(device_id, serial_baud)
    except (ElmError, TypeError) as error:
        result['error'] = str(error)
        persist()
        return result

    lock = _FileLock("live", DEFAULT_ARTIFACT_ROOT)
    try:
        lock.acquire()
    except SessionError as e:
        result["error"] = e.code
        result["lockReleased"] = False
        persist()
        return result

    try:
        end = time.monotonic() + TOTAL_BUDGET_S
        for i in range(1 + REOPEN_COUNT):
            if time.monotonic() >= end:
                result["error"] = result["error"] or "total-budget-exceeded"
                break
            rec = {"index": i, "kind": "configure" if i == 0 else "reopen-query", "ok": False, "error": None}
            connections.append(rec)
            t_open = time.monotonic()
            port = None
            com = None
            guard = None
            client = None
            try:
                port, com = _open_port(port_factory, serial_module, list_ports, device_id=device_id,
                    adapter_model=adapter_model, serial_baud=serial_baud, snapshot=snapshot)
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"open:{e}"
                result["error"] = rec["error"]
                break
            finally:
                end += time.monotonic() - t_open
            if com:
                result["port"] = com
            try:
                guard = AtOnlyGuard(port, traffic)
                client = BoundedElm(guard, global_end=end, records=records, timeout_s=CMD_TIMEOUT_S, expected_model=adapter_model)
                ident = client.validate_adapter(min(CMD_TIMEOUT_S * 3, max(0.1, end - time.monotonic())))
                rec["adapter"] = {
                    "ati": ident.get("ati"),
                    "sti": ident.get("sti"),
                    "atdpn": ident.get("atdpn"),
                    "atrv": ident.get("atrv"),
                    "voltageString": parse_voltage_string(ident.get("atrv")),
                    "protocolString": parse_protocol_string(ident.get("atdpn")),
                }
                if i == 0:
                    result["atiText"] = ident.get("ati")
                    result["voltageString"] = rec["adapter"]["voltageString"]
                    result["initialProtocol"] = rec["adapter"]["protocolString"]
                    for tx, rx in PAIR_IDS:
                        client.configure_pair(tx, rx, min(end, time.monotonic() + CMD_TIMEOUT_S * 14))
                else:
                    if (ident.get('ati') != result['atiText']
                            or ident.get('sti') != connections[0]['adapter'].get('sti')):
                        raise ElmError('adapter-identity-changed')
                    q = _reopen_close_cmd(client, end)
                    if q["errors"]:
                        rec["error"] = ";".join(q["errors"])
                        result["error"] = result["error"] or rec["error"]
                    else:
                        rec["ok"] = True
                        result["reopenCount"] += 1
            except Exception as e:  # noqa: BLE001
                rec["error"] = str(e)
                result["error"] = result["error"] or rec["error"]
            finally:
                try:
                    if i == 0 and client is not None:
                        rest = _cleanup_cmds(client, end)
                        result["restoration"] = {
                            "errors": rest["errors"],
                            "fullStateRestoreClaimed": False,
                            "note": rest["note"],
                            "initialProtocol": result["initialProtocol"],
                            "finalProtocol": parse_protocol_string(rest["texts"].get("ATDPN")),
                        }
                        result["finalProtocol"] = result["restoration"]["finalProtocol"]
                        if rest["errors"]:
                            rec["error"] = rec["error"] or "cleanup-failed"
                            result["error"] = result["error"] or "cleanup-failed"
                        if rec["error"] is None:
                            rec["ok"] = True
                finally:
                    closer = guard if guard is not None else port
                    if closer is not None:
                        close_errors = _close_quiet(closer)
                        rec["closeErrors"] = close_errors
                        rec["portClosed"] = not close_errors
                        if close_errors:
                            rec["ok"] = False
                            rec["error"] = rec["error"] or ";".join(close_errors)
                            result["error"] = result["error"] or rec["error"]
            if result["error"] is not None:
                break
        if result["error"] is None and (
            result["reopenCount"] != REOPEN_COUNT or not connections or not connections[0].get("ok")
        ):
            result["error"] = result["error"] or "incomplete-connections"
    finally:
        lock.release()
        result["lockReleased"] = True
        persist()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.diagnostics.adapter_check")
    parser.add_argument("--yes-powered-bench", action="store_true", help="Confirm power-only bench: CAN wires disconnected and no vehicle attached")
    parser.add_argument("--out", required=True, help="Fresh output directory; existing evidence is never overwritten")
    parser.add_argument('--device-id', help='Explicit stable bt:/usb: identity or configured tcp: literal endpoint')
    parser.add_argument('--adapter-model', choices=sorted(ELM_MODELS), help='Actual ELM family; required for physical checks')
    parser.add_argument('--serial-baud', type=int, choices=ELM_SERIAL_BAUDS, help='Match existing UART settings; omit for model default')
    args = parser.parse_args(argv)
    if not args.yes_powered_bench:
        print("refusing: pass --yes-powered-bench; will not enumerate or open serial", file=sys.stderr)
        return 2
    if not args.device_id or not args.adapter_model:
        print('refusing: supply explicit --device-id and --adapter-model; will not enumerate or open', file=sys.stderr)
        return 2
    out = run_adapter_check(confirmed_powered_bench=True, out_dir=Path(args.out),
        device_id=args.device_id, adapter_model=args.adapter_model, serial_baud=args.serial_baud)
    printable = {k: v for k, v in out.items() if k != "connections"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
