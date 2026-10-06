"""Host OBD adapter discovery + ATRV voltage. No ECU bytes. Does not open ports on list."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Any

from . import BAUD, VLIKER_MAC
from .elm import ElmClient, ElmError

DEVICE_ID_RE = re.compile(r"^(?:bt:[0-9A-F]{12}|(?:vnci|pt3g):[0-9]{1,16})$")
VOLT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V", re.I)
VOLT_MIN = 6.0
VOLT_MAX = 20.0
# Product design default, NOT a measured X431 voltage period. X431 evidence is ACL retention, not ATRV Hz.
ATRV_INTERVAL_S = 2.0
MONITOR_AT_TIMEOUT_S = 4.0
RAW_LOG_CAP = 8
KNOWN_VLIKER = VLIKER_MAC.upper().replace(":", "")
PS_TIMEOUT_S = 8.0

GUIDANCE_NO_COM = "已配对，但没有可用的 Bluetooth SPP 串口。请在 Windows 蓝牙设置中为该适配器打开串行端口后再刷新。"
UNRESOLVED_HINT = "系统未给出可识别的适配器名称。连接前请选择实际型号（vLinker 或 OBDLink MX+）。"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_voltage_volts(text: str | None) -> float | None:
    if not text:
        return None
    blob = text.replace("\x00", "")
    if re.search(r"-\s*\d", blob):
        return None
    upper = blob.upper()
    if any(tok in upper for tok in ("ERROR", "UNABLE", "NO DATA", "?")):
        return None
    lines = [line.strip() for line in blob.replace(">", "\n").splitlines() if line.strip() and line.strip().upper() != "ATRV"]
    hits = [VOLT_RE.fullmatch(line) for line in lines]
    if any(hit is None for hit in hits):
        return None
    if len(hits) != 1:
        return None
    try:
        v = float(hits[0].group(1))
    except ValueError:
        return None
    if not math.isfinite(v) or v <= 0 or v < VOLT_MIN or v > VOLT_MAX:
        return None
    return v


def extract_bt_mac(text: str | None) -> str | None:
    if not text:
        return None
    u = text.upper().replace(":", "")
    m = re.search(r"DEV_([0-9A-F]{12})", u)
    if m:
        return m.group(1)
    m = re.search(r"&([0-9A-F]{12})_C", u)
    if m:
        return m.group(1)
    return None


def _blob(row: dict) -> str:
    parts = [row.get(k) or "" for k in ("name", "description", "pnp", "hwid", "friendlyName", "instanceId", "device")]
    return " ".join(str(p) for p in parts).upper()


def is_amt_sol(row: dict) -> bool:
    b = _blob(row)
    return "AMT" in b and "SOL" in b


def classify_brand(name: str | None, mac: str | None) -> str:
    n = (name or "").upper()
    if "VLINKER" in n or (mac and mac == KNOWN_VLIKER):
        return "vLinker"
    if "OBDLINK" in n and re.search(r"\bMX\s*\+", n):
        return "OBDLink MX+"
    if "OBDLINK" in n:
        return "unresolved"
    if mac == KNOWN_VLIKER:
        return "vLinker"
    return "unresolved"


def is_obd_name(name: str | None) -> bool:
    n = (name or "").upper()
    return "VLINKER" in n or "OBDLINK" in n


def device_id_for_mac(mac: str) -> str:
    return "bt:" + mac.upper()


def valid_device_id(value: str | None) -> bool:
    return isinstance(value, str) and bool(DEVICE_ID_RE.fullmatch(value))


def _ps_json(script: str, runner=None) -> tuple[Any, str | None]:
    run = runner or subprocess.run
    try:
        r = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=PS_TIMEOUT_S,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return None, "powershell-missing"
    except subprocess.TimeoutExpired:
        return None, "powershell-timeout"
    except OSError as e:
        return None, f"powershell:{e}"
    if r.returncode != 0:
        return None, ((r.stderr or r.stdout or "powershell-failed")[:400])
    raw = (r.stdout or "").strip()
    if not raw:
        return [], None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "powershell-json"
    if isinstance(data, dict):
        return [data], None
    if isinstance(data, list):
        return data, None
    return None, "powershell-shape"


def windows_serial_rows(runner=None) -> tuple[list[dict], str | None]:
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [Console]::OutputEncoding; "
        # Win32_SerialPort can stall in its CIM provider. Use present, healthy
        # PnP ports and retain pyserial as the independent OS listing source.
        "Get-PnpDevice -Class Ports -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Status -eq 'OK' -and $_.FriendlyName -match '\\(COM[0-9]+\\)' } | "
        "Select-Object @{Name='DeviceID';Expression={[regex]::Match($_.FriendlyName,'COM[0-9]+').Value}}, "
        "@{Name='Name';Expression={$_.FriendlyName}}, @{Name='Description';Expression={$_.FriendlyName}}, "
        "@{Name='PNPDeviceID';Expression={$_.InstanceId}} | "
        "ConvertTo-Json -Compress"
    )
    data, err = _ps_json(script, runner)
    if err:
        return [], err
    out = []
    for row in data or []:
        out.append(
            {
                "device": row.get("DeviceID") or row.get("device"),
                "name": row.get("Name") or row.get("name"),
                "description": row.get("Description") or row.get("description"),
                "pnp": row.get("PNPDeviceID") or row.get("pnp"),
            }
        )
    return out, None


def windows_bluetooth_rows(runner=None) -> tuple[list[dict], str | None]:
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [Console]::OutputEncoding; "
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        "Select-Object Status, FriendlyName, InstanceId | ConvertTo-Json -Compress"
    )
    data, err = _ps_json(script, runner)
    if err:
        return [], err
    out = []
    for row in data or []:
        out.append(
            {
                "status": row.get("Status") or row.get("status"),
                "friendlyName": row.get("FriendlyName") or row.get("friendlyName"),
                "instanceId": row.get("InstanceId") or row.get("instanceId"),
            }
        )
    return out, None


def pyserial_rows(list_ports) -> tuple[list[dict], str | None]:
    if list_ports is None:
        return [], "pyserial-missing"
    try:
        ports = list_ports.comports()
    except Exception as e:  # noqa: BLE001
        return [], f"pyserial:{e}"
    rows = []
    for p in ports:
        rows.append(
            {
                "device": getattr(p, "device", None),
                "name": getattr(p, "name", None),
                "description": getattr(p, "description", None),
                "hwid": getattr(p, "hwid", None),
            }
        )
    return rows, None


def collect_snapshot(
    *,
    platform: str | None = None,
    serial_rows=None,
    bluetooth_rows=None,
    pyserial=None,
    ps_runner=None,
    errors: list | None = None,
    vnci_rows=None,
    pt3g_rows=None,
) -> dict:
    plat = platform if platform is not None else sys.platform
    native_discovery = plat == "win32" and all(v is None for v in (serial_rows, bluetooth_rows, pyserial, ps_runner, vnci_rows, pt3g_rows))
    errs = list(errors or [])
    serial = list(serial_rows) if serial_rows is not None else None
    bluetooth = list(bluetooth_rows) if bluetooth_rows is not None else None
    serial_ps = list(pyserial) if pyserial is not None else None
    if plat != "win32" and serial is None and bluetooth is None and serial_ps is None:
        return {"platform": plat, "serial": [], "bluetooth": [], "pyserial": [], "errors": ["unsupported-platform"]}
    if serial is None:
        if plat == "win32":
            serial, e = windows_serial_rows(ps_runner)
            if e:
                errs.append(e)
        else:
            serial = []
    if bluetooth is None:
        if plat == "win32":
            bluetooth, e = windows_bluetooth_rows(ps_runner)
            if e:
                errs.append(e)
        else:
            bluetooth = []
    if serial_ps is None:
        try:
            import serial.tools.list_ports as lp  # lazy
        except Exception:
            serial_ps, e = [], "pyserial-missing"
            errs.append(e)
        else:
            serial_ps, e = pyserial_rows(lp)
            if e:
                errs.append(e)
    vnci = list(vnci_rows or [])
    if vnci_rows is None and native_discovery:
        from .vnci import discover
        try:
            vnci = discover()
        except Exception as e:
            errs.append("vnci:" + str(e))
    pt3g = list(pt3g_rows or [])
    if pt3g_rows is None and native_discovery:
        from .pt3g import discover
        try:
            pt3g = discover()
        except Exception as e:
            errs.append("pt3g:" + str(e))
    return {"platform": plat, "serial": serial or [], "bluetooth": bluetooth or [], "pyserial": serial_ps or [], "vnci": vnci, "pt3g": pt3g, "errors": errs}


def enumerate_devices(snapshot: dict | None = None, **collect_kw) -> dict:
    snap = snapshot if snapshot is not None else collect_snapshot(**collect_kw)
    by_mac: dict[str, dict] = {}

    def ensure(mac: str) -> dict:
        mid = mac.upper()
        row = by_mac.get(mid)
        if row is None:
            row = {
                "id": device_id_for_mac(mid),
                "mac": mid,
                "brand": "unresolved",
                "name": None,
                "comPort": None,
                "available": False,
                "paired": False,
                "osStatus": None,
                "guidance": None,
                "identitySource": [],
            }
            by_mac[mid] = row
        return row

    for bt in snap.get("bluetooth") or []:
        mac = extract_bt_mac(bt.get("instanceId") or "")
        name = bt.get("friendlyName")
        st = str(bt.get("status") or "").upper()
        if not mac:
            continue
        if st not in ("OK",):
            continue
        if not is_obd_name(name) and mac != KNOWN_VLIKER:
            continue
        rec = ensure(mac)
        rec["paired"] = True
        rec["osStatus"] = bt.get("status")
        rec["name"] = name or rec["name"]
        rec["brand"] = classify_brand(rec["name"], mac)
        rec["identitySource"].append("bluetooth-pnp")

    seen_ports: dict[str, str] = {}
    ambiguous_macs: set[str] = set()
    for src_key in ("serial", "pyserial"):
        for row in snap.get(src_key) or []:
            if is_amt_sol(row):
                continue
            com = (row.get("device") or "").upper()
            mac = extract_bt_mac((row.get("pnp") or "") + " " + (row.get("hwid") or ""))
            if not mac:
                blob = _blob(row)
                if KNOWN_VLIKER in blob:
                    mac = KNOWN_VLIKER
            if not mac or not com:
                continue
            prev_mac = seen_ports.get(com)
            if prev_mac and prev_mac != mac:
                ambiguous_macs.update((prev_mac, mac))
                prev = ensure(prev_mac)
                prev["comPort"] = None
                prev["available"] = False
                prev["guidance"] = "同一串口对应多个适配器身份，已拒绝猜测。"
                rec = ensure(mac)
                rec["available"] = False
                rec["comPort"] = None
                rec["guidance"] = "同一串口对应多个适配器身份，已拒绝猜测。"
                continue
            rec = ensure(mac)
            if rec.get("comPort") and rec["comPort"].upper() != com:
                ambiguous_macs.add(mac)
                rec["available"] = False
                rec["comPort"] = None
                rec["guidance"] = "同一适配器出现多个串口，已拒绝猜测。"
                rec["identitySource"].append(src_key)
                continue
            seen_ports[com] = mac
            rec["comPort"] = row.get("device") or rec["comPort"]
            if not rec["name"]:
                rec["name"] = row.get("name") or row.get("description")
            rec["brand"] = classify_brand(rec["name"], mac)
            rec["identitySource"].append(src_key)

    devices = []
    for rec in by_mac.values():
        known = rec["brand"] in ("vLinker", "OBDLink MX+") or rec["mac"] == KNOWN_VLIKER or is_obd_name(rec["name"])
        if not known and not rec["paired"] and not rec["comPort"]:
            continue
        if not known and rec["comPort"] and not rec["paired"] and rec["mac"] != KNOWN_VLIKER:
            continue
        rec["brand"] = classify_brand(rec["name"], rec["mac"])
        if rec["mac"] in ambiguous_macs:
            rec["comPort"] = None
        rec["available"] = bool(rec["comPort"])
        if not rec["available"]:
            rec["guidance"] = rec.get("guidance") or (GUIDANCE_NO_COM if rec["paired"] else None)
        elif rec["brand"] == "unresolved":
            rec["guidance"] = rec.get("guidance") or UNRESOLVED_HINT
        rec["identitySource"] = sorted(set(rec["identitySource"]))
        devices.append(rec)
    devices.sort(key=lambda d: d["id"])
    devices.extend(snap.get("vnci") or [])
    devices.extend(snap.get("pt3g") or [])
    return {
        "ok": True,
        "devices": devices,
        "errors": list(snap.get("errors") or []),
        "platform": snap.get("platform"),
        "openedPort": False,
    }


def resolve_port(device_id: str, snapshot: dict | None = None, **collect_kw) -> str:
    if not valid_device_id(device_id) or not device_id.startswith("bt:"):
        raise ElmError("device-id-invalid")
    listed = enumerate_devices(snapshot, **collect_kw)
    hits = [d for d in listed["devices"] if d["id"] == device_id]
    if len(hits) != 1:
        raise ElmError("device-identity-missing")
    rec = hits[0]
    if not rec.get("available") or not rec.get("comPort"):
        raise ElmError("device-port-unavailable")
    others = [d for d in listed["devices"] if d["id"] != device_id and d.get("comPort") == rec["comPort"]]
    if others:
        raise ElmError("device-port-not-unique")
    return rec["comPort"]


def open_selected_port(device_id: str, *, serial_module=None, snapshot=None, **collect_kw):
    com = resolve_port(device_id, snapshot, **collect_kw)
    if serial_module is None:
        import serial as serial_module  # lazy
    return serial_module.Serial(com, BAUD, timeout=0.05, exclusive=True), com


def probe_adapter(device_id: str, *, kind: str = "connect", serial_module=None, snapshot=None, port=None, **collect_kw) -> dict:
    own = port is None
    client = None
    opened = None
    out = {
        "ok": False,
        "deviceId": device_id,
        "kind": kind,
        "comPort": None,
        "ati": None,
        "atdpn": None,
        "atrv": None,
        "volts": None,
        "voltageSource": None,
        "error": None,
        "openedPort": False,
        "liveVerified": False,
        "writePayload": None,
        "simulation": False,
        "at": _utc(),
    }
    try:
        if port is None:
            port, com = open_selected_port(device_id, serial_module=serial_module, snapshot=snapshot, **collect_kw)
            opened = port
            out["comPort"] = com
            out["openedPort"] = True
        else:
            out["comPort"] = "injected"
            out["openedPort"] = True
        client = ElmClient(port, timeout_s=8.0)
        if kind == "voltage":
            atrv = client.send_at("ATRV")
            out["atrv"] = atrv
        else:
            ident = client.validate_adapter(10.0)
            out["ati"] = ident.get("ati")
            out["atdpn"] = ident.get("atdpn")
            out["atrv"] = ident.get("atrv")
        volts = parse_voltage_volts(out.get("atrv"))
        if volts is None:
            out["error"] = "atrv-unparsed"
            return out
        out["volts"] = volts
        out["voltageSource"] = "atrv"
        out["ok"] = True
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
        return out
    finally:
        if client is not None:
            try:
                client.close_restore()
            except Exception:  # noqa: BLE001
                pass
        elif own and opened is not None:
            try:
                opened.close()
            except Exception:  # noqa: BLE001
                pass


def _live_probe_blocked() -> bool:
    return os.environ.get("PORSCHE981_SESSION_DENY_LIVE") == "1" or os.environ.get("PORSCHE981_HEADLESS") == "1"


def _monitor_error(exc: BaseException) -> str:
    if isinstance(exc, ElmError):
        s = str(exc)
        for code in (
            "adapter-identity-mismatch",
            "device-port-unavailable",
            "device-identity-missing",
            "device-id-invalid",
            "device-port-not-unique",
            "port-io",
            "prompt-timeout",
            "cancelled",
        ):
            if s == code or s.startswith(code):
                return code
        return s.split(":", 1)[0] or "port-io"
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return "port-io"
    return "port-io"


def _wait_interval(interval_s: float, stop, sleeper, default_sleep) -> None:
    if sleeper is default_sleep:
        stop.wait(timeout=interval_s)
        return
    sleeper(interval_s)


def _emit(outf, doc: dict) -> None:
    outf.write(json.dumps(doc, ensure_ascii=False) + "\n")
    outf.flush()


def _trim_raw(client) -> None:
    log = getattr(client, "raw_log", None)
    if isinstance(log, list) and len(log) > RAW_LOG_CAP:
        del log[: len(log) - RAW_LOG_CAP]


def _watch_stdin_stop(inf, stop) -> None:
    def run():
        while not stop.is_set():
            line = inf.readline()
            if not line:
                stop.set()
                return
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                stop.set()
                return
            if isinstance(doc, dict) and doc.get("action") in ("stop", "cancel"):
                stop.set()
                return

    threading.Thread(target=run, daemon=True).start()


def run_voltage_monitor(
    device_id: str,
    *,
    serial_module=None,
    snapshot=None,
    port=None,
    stdout=None,
    stdin=None,
    sleep_fn=None,
    clock=None,
    interval_s: float = ATRV_INTERVAL_S,
    stop_event=None,
    max_samples: int | None = None,
    **collect_kw,
) -> int:
    """Persistent ATRV on one serial open. Identity once. No ECU bytes."""
    import time

    outf = stdout or sys.stdout
    flags = {"liveVerified": False, "writePayload": None, "simulation": False}
    if _live_probe_blocked():
        _emit(outf, {"ok": False, "type": "error", "error": "live-probe-disabled", **flags})
        return 2
    if not valid_device_id(device_id):
        _emit(outf, {"ok": False, "type": "error", "error": "device-id-invalid", **flags})
        return 2

    if device_id.startswith("vnci:"):
        from .vnci import run_monitor
        return run_monitor(device_id, stdout=outf, stdin=stdin, stop_event=stop_event, max_samples=max_samples)
    if device_id.startswith("pt3g:"):
        from .pt3g import run_monitor
        return run_monitor(device_id, stdout=outf, stdin=stdin, stop_event=stop_event, max_samples=max_samples)

    own = port is None
    client = None
    opened = None
    serial_opens = 0
    stop = stop_event or threading.Event()
    sleeper = sleep_fn or time.sleep
    if stdin is not None and stop_event is None:
        _watch_stdin_stop(stdin, stop)

    def fail(err: str, extra=None) -> int:
        _emit(outf, {"ok": False, "type": "error", "error": err, "serialOpens": serial_opens, **flags, **(extra or {})})
        return 1

    try:
        if port is None:
            com = resolve_port(device_id, snapshot, **collect_kw)
            if stop.is_set():
                _emit(outf, {"ok": True, "type": "stopped", "serialOpens": serial_opens, **flags})
                return 0
            if serial_module is None:
                import serial as serial_module  # lazy
            port = serial_module.Serial(com, BAUD, timeout=0.05, exclusive=True)
            opened = port
            serial_opens += 1
        else:
            com = "injected"
            serial_opens = 1
        client = ElmClient(port, timeout_s=MONITOR_AT_TIMEOUT_S, clock=clock, sleeper=sleeper, cancel_event=stop)
        ident = client.validate_adapter(10.0)
        _trim_raw(client)
        volts = parse_voltage_volts(ident.get("atrv"))
        samples = 1
        _emit(
            outf,
            {
                "ok": True,
                "type": "handshake",
                "deviceId": device_id,
                "comPort": com,
                "ati": ident.get("ati"),
                "atdpn": ident.get("atdpn"),
                "atrv": ident.get("atrv"),
                "volts": volts,
                "voltageSource": "atrv",
                "serialOpens": serial_opens,
                "identityOnce": True,
                "commOk": True,
                "at": _utc(),
                **flags,
            },
        )
        while not stop.is_set() and (max_samples is None or samples < max_samples):
            _wait_interval(interval_s, stop, sleeper, time.sleep)
            if stop.is_set():
                break
            atrv = client.send_at("ATRV")
            _trim_raw(client)
            volts = parse_voltage_volts(atrv)
            samples += 1
            _emit(
                outf,
                {
                    "ok": True,
                    "type": "reading",
                    "commOk": True,
                    "deviceId": device_id,
                    "atrv": atrv,
                    "volts": volts,
                    "voltageSource": "atrv",
                    "serialOpens": serial_opens,
                    "at": _utc(),
                    **flags,
                },
            )
        _emit(outf, {"ok": True, "type": "stopped", "serialOpens": serial_opens, **flags})
        return 0
    except Exception as e:  # noqa: BLE001
        return fail(_monitor_error(e))
    finally:
        if client is not None:
            try:
                client.close_restore()
            except Exception:  # noqa: BLE001
                pass
        elif own and opened is not None:
            try:
                opened.close()
            except Exception:  # noqa: BLE001
                pass


def stdio_loop(stdin=None, stdout=None) -> int:
    inf = stdin or sys.stdin
    outf = stdout or sys.stdout
    raw = inf.readline()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        req = json.loads(raw)
    except json.JSONDecodeError:
        outf.write(json.dumps({"ok": False, "error": "stdin-json", "liveVerified": False, "writePayload": None}) + "\n")
        return 2
    if not isinstance(req, dict):
        outf.write(json.dumps({"ok": False, "error": "stdin-not-object", "liveVerified": False, "writePayload": None}) + "\n")
        return 2
    extra = set(req) - {"action", "deviceId", "kind", "purpose", "canNetwork"}
    if extra:
        outf.write(json.dumps({"ok": False, "error": "unexpected-keys", "liveVerified": False, "writePayload": None}) + "\n")
        return 2
    action = req.get("action")
    if action == "list":
        from .host_devices import collect_host_devices
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            host = pool.submit(collect_host_devices)
            doc = enumerate_devices()
            doc["host"] = host.result()
        doc["errors"].extend(doc["host"].get("errors") or [])
        outf.write(json.dumps(doc, ensure_ascii=False) + "\n")
        return 0
    if action == "monitor":
        did = req.get("deviceId")
        if req.get("purpose", "diagnostic") not in ("diagnostic", "internal"):
            _emit(outf, {"ok": False, "type": "error", "error": "invalid-purpose"})
            return 2
        if _live_probe_blocked():
            outf.write(
                json.dumps({"ok": False, "type": "error", "error": "live-probe-disabled", "liveVerified": False, "writePayload": None, "simulation": False})
                + "\n"
            )
            return 2
        if req.get("purpose") == "internal":
            from .internal_stream import run_internal_monitor
            return run_internal_monitor(did, req.get("canNetwork"), stdout=outf, stdin=inf)
        return run_voltage_monitor(did, stdout=outf, stdin=inf)
    if action == "probe":
        did = req.get("deviceId")
        kind = req.get("kind") or "connect"
        if kind not in ("connect", "voltage"):
            outf.write(json.dumps({"ok": False, "error": "invalid-kind", "liveVerified": False, "writePayload": None}) + "\n")
            return 2
        if _live_probe_blocked():
            outf.write(
                json.dumps({"ok": False, "error": "live-probe-disabled", "liveVerified": False, "writePayload": None, "simulation": False})
                + "\n"
            )
            return 2
        out = probe_adapter(did, kind=kind)
        outf.write(json.dumps(out, ensure_ascii=False) + "\n")
        return 0 if out.get("ok") else 1
    outf.write(json.dumps({"ok": False, "error": "invalid-action", "liveVerified": False, "writePayload": None}) + "\n")
    return 2


def main(argv=None) -> int:
    return stdio_loop()


if __name__ == "__main__":
    raise SystemExit(main())
