"""Local VNCI/VAS6154A D-PDU transport. ISO-TP is owned by the vendor API.

Only the named 981 read catalog and the frozen Mode 01 set may be sent.
No security access, coding, clear, STARTCOMM, flashing or arbitrary IOCTL API.
ABI reference: https://github.com/DiagProf/ISO22900.II (ISO 22900-2).
Vendor binaries/configuration remain under .local/, outside source control.
"""
from __future__ import annotations

import ctypes as c
import hashlib
import json
import ipaddress
import math
import os
import re
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from .allowlist import blocked_reason
from .elm import ElmError
from .pair import response_matches

U32 = c.c_uint32
PTR = c.c_void_p
UNDEF = 0xFFFFFFFF
ID_UNDEF = 0xFFFFFFFE
DEVICE_RE = re.compile(r"^vnci:([0-9]{1,16})$")
ENGINE_READS = frozenset({"0100", "0104", "0105", "010C", "010D", "010F", "0111"})
DRIVER_VERSION = "29.0.0"
FIRMWARE_IMAGE = "29.0r00-14769-2964"
LIBRARY_SHA256 = "d4cfdd2c2a8238d1a925be7425624ed916e791f26e237d1455908183984a326f"
SUPPORT_SHA256 = {
    'CDF_VW.xml': 'd2f8c936ff49e127a90ae6692e0240506d0af9cea24e10287cd2687ffd6eee4c',
    'MDF_VW.xml': 'ce340ac78973fa59c866978acb7ab1e6bea63a1911bbdbaef4104ba1b240cd29',
    'PDUAPI_VW.ini': 'd6928bd11e879282fa26b8ac2ceec697d0b562b52d85208b4ce18b4a01d574fa',
    'RDFSetup.dll': 'd3d7147b215d9ed32ba61d81590ecfaa5875b77aa7fcd150a261532061d05385',
    'VAS6154Locate_64.dll': '1a0ef0debcbdf5f6ca7ebaf65733c3f06b94dc8a5cd8c6453e5ff6e58d4ccf75',
    'VAS6154/VAS6154-D-PDU_API-29.0.0.para': '8b32594e666457af91f7764d08bad74756e50e7125eb0361249bbe2a3591b33f',
    'VAS6154/VAS6154-D-PDU_API-29.0.0.tgz': '31760389d887eabe1eb0351cff9a9fd5a3e984609b6e2c95f498e5969cda4696',
    'VAS6154/VAS6154_29.0r00-14769-2964-20230801': 'baa1792fe84ba909e6cc1011b14962f9daa66406b15e0dfa1f869d346421bb68',
}


def vendor_root(env=None):
    env = os.environ if env is None else env
    if env.get('PORSCHE981_VNCI_ROOT'):
        return Path(env['PORSCHE981_VNCI_ROOT']).expanduser().resolve()
    base = Path(env.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    return base / '.local/vnci-support/vendor/VW_PDUAPI_OS'


def verify_vendor_files(root):
    # Local qualification pins the support files too: missing metadata can
    # silently hide devices; drift must not let the vendor attempt an update.
    for name, expected in {'PDUAPI_VW.dll': LIBRARY_SHA256, **SUPPORT_SHA256}.items():
        try:
            with (Path(root) / name).open('rb') as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
        except OSError:
            raise ElmError('vnci-driver-missing' if name == 'PDUAPI_VW.dll'
                           else 'vnci-driver-support-missing:' + name) from None
        if actual != expected:
            raise ElmError('vnci-driver-version-not-qualified' if name == 'PDUAPI_VW.dll'
                           else 'vnci-driver-support-not-qualified:' + name)


def _display_voltage(volts):
    # A completed native response proves adapter communication independently
    # of whether its OBD battery input has a usable voltage reading.
    return volts if (isinstance(volts, (int, float)) and not isinstance(volts, bool)
                     and math.isfinite(volts) and 6 <= volts <= 20) else None


class Module(c.Structure):
    _fields_ = [("type", U32), ("handle", U32), ("name", c.c_char_p),
                ("info", c.c_char_p), ("status", U32)]


class ModuleItem(c.Structure):
    _fields_ = [("type", U32), ("count", U32), ("data", c.POINTER(Module))]


class DataItem(c.Structure):
    _fields_ = [("type", U32), ("data", PTR)]


class Flag(c.Structure):
    _fields_ = [("count", U32), ("data", c.POINTER(c.c_uint8))]


class Pin(c.Structure):
    _fields_ = [("number", U32), ("type", U32)]


class Resource(c.Structure):
    _fields_ = [("bus", U32), ("protocol", U32), ("count", U32), ("pins", c.POINTER(Pin))]


class Param(c.Structure):
    _fields_ = [("type", U32), ("id", U32), ("data_type", U32), ("category", U32), ("data", PTR)]


class UniqueData(c.Structure):
    _fields_ = [("id", U32), ("count", U32), ("params", c.POINTER(Param))]


class UniqueTable(c.Structure):
    _fields_ = [("type", U32), ("count", U32), ("data", c.POINTER(UniqueData))]


class Control(c.Structure):
    _fields_ = [("time", U32), ("send_cycles", c.c_int32), ("receive_cycles", c.c_int32),
                ("temporary", U32), ("flags", Flag), ("expected_count", U32), ("expected", PTR)]


class Event(c.Structure):
    _fields_ = [("type", U32), ("cop", U32), ("tag", PTR), ("timestamp", U32), ("data", PTR)]


class Result(c.Structure):
    _fields_ = [("flags", Flag), ("unique_id", U32), ("acceptance", U32), ("timestamp_flags", Flag),
                ("tx_done", U32), ("rx_start", U32), ("extra", PTR), ("size", U32), ("data", PTR)]


def _check(code, name):
    if code:
        raise ElmError(f"d-pdu:{name}:0x{code:08X}")


def module_device(row):
    """Bind by the native serial, never a cached hMod or a COM port."""
    fields = dict(re.findall(r"([A-Za-z]+)='([^']*)'", row.get("name", "")))
    serial = fields.get("SerialNumber", "")
    if (row.get("type") != 14 or fields.get("ModuleName") != "VAS6154A"
            or fields.get("ConnectionType") != "USB" or not re.fullmatch(r"[0-9]{1,16}", serial)):
        return None
    return {"id": "vnci:" + serial, "brand": "VNCI", "name": "VAS6154A · USB",
            "transport": "d-pdu-usb", "serial": serial, "address": fields.get("IP"),
            "comPort": None, "available": row.get("status") == 0x8063, "paired": False,
            "osStatus": "available" if row.get("status") == 0x8063 else "unavailable",
            "guidance": "USB D-PDU；接车通电后可读电压与具名只读诊断。设码尚未验证。",
            "identitySource": ["d-pdu-module-serial"]}


def require_matching_firmware(address, *, fetch=None):
    """Avoid connecting a mismatched VCI which the vendor may try to update."""
    try:
        ip = ipaddress.IPv4Address(address)
    except (ValueError, TypeError):
        raise ElmError("vnci-usb-address-invalid") from None
    if ip not in ipaddress.IPv4Network("192.168.13.0/24"):
        raise ElmError("vnci-usb-address-not-qualified")
    url = f"http://{ip}/cgi-bin/formular.fcgi/GSINF"
    try:
        if fetch is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=3) as response:
                data = response.read(65537)
        else:
            data = fetch(url)
        if len(data) > 65536:
            raise ValueError("invalid-device-xml")
        # The observed ACTIA page declares only these two fixed text entities.
        # Remove that exact declaration; never accept or expand arbitrary DTDs.
        declaration = b'<!DOCTYPE formular [ <!ENTITY nbsp "&#160;"> <!ENTITY copy "&#xA9;"> ]>'
        data = data.replace(declaration, b"", 1)
        if b"<!ENTITY" in data or b"<!DOCTYPE" in data:
            raise ValueError("invalid-device-xml")
        data = data.replace(b"&nbsp;", b"&#160;").replace(b"&copy;", b"&#xA9;")
        xml = ET.fromstring(data)
        api = xml.findall(".//formdata[@key='VersionDIAGvers']")
        image = xml.findall(".//formdata[@key='VersionSystem']")
        if (len(api) != 1 or len(image) != 1 or api[0].text != DRIVER_VERSION
                or image[0].text != FIRMWARE_IMAGE):
            raise ElmError("vnci-firmware-mismatch-no-auto-update")
    except ElmError:
        raise
    except Exception as e:
        raise ElmError("vnci-firmware-check-failed") from e


class NativeDpu:
    """Small stdcall binding. Calls run in the existing bounded Python worker."""
    def __init__(self, root=None):
        if sys.platform != "win32" or c.sizeof(PTR) != 8:
            raise ElmError("vnci-requires-windows-x64-python")
        root = Path(root if root is not None else vendor_root()).resolve()
        verify_vendor_files(root)
        self._directory = os.add_dll_directory(str(root))
        self.dll = c.WinDLL(str(root / "PDUAPI_VW.dll"))
        self.module = None
        self.link = None
        self.online = False
        self.constructed = False
        signatures = {
            "PDUConstruct": [c.c_char_p, PTR], "PDUDestruct": [],
            "PDUGetModuleIds": [c.POINTER(c.POINTER(ModuleItem))], "PDUDestroyItem": [PTR],
            "PDUModuleConnect": [U32], "PDUModuleDisconnect": [U32],
            "PDUGetObjectId": [U32, c.c_char_p, c.POINTER(U32)],
            "PDUIoCtl": [U32, U32, U32, PTR, c.POINTER(c.POINTER(DataItem))],
            "PDUCreateComLogicalLink": [U32, c.POINTER(Resource), U32, PTR, c.POINTER(U32), c.POINTER(Flag)],
            "PDUDestroyComLogicalLink": [U32, U32], "PDUConnect": [U32, U32], "PDUDisconnect": [U32, U32],
            "PDUGetComParam": [U32, U32, U32, c.POINTER(c.POINTER(Param))],
            "PDUSetComParam": [U32, U32, c.POINTER(Param)],
            "PDUGetUniqueRespIdTable": [U32, U32, c.POINTER(c.POINTER(UniqueTable))],
            "PDUSetUniqueRespIdTable": [U32, U32, c.POINTER(UniqueTable)],
            "PDUStartComPrimitive": [U32, U32, U32, U32, PTR, c.POINTER(Control), PTR, c.POINTER(U32)],
            "PDUCancelComPrimitive": [U32, U32, U32],
            "PDUGetEventItem": [U32, U32, c.POINTER(c.POINTER(Event))],
        }
        for name, args in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, U32
        try:
            self.call("PDUConstruct", None, None)
            self.constructed = True
        except Exception:
            self._directory.close()
            raise

    def call(self, name, *args):
        _check(getattr(self.dll, name)(*args), name)

    def object_id(self, kind, name):
        value = U32(ID_UNDEF)
        self.call("PDUGetObjectId", kind, name.encode("ascii"), c.byref(value))
        if value.value in (ID_UNDEF, UNDEF):
            raise ElmError("d-pdu:unsupported-object:" + name)
        return value.value

    def modules(self):
        ptr = c.POINTER(ModuleItem)()
        self.call("PDUGetModuleIds", c.byref(ptr))
        if not ptr:
            raise ElmError("d-pdu:module-item-missing")
        try:
            item = ptr.contents
            if item.type != 0x1600 or item.count > 64 or (item.count and not item.data):
                raise ElmError("d-pdu:invalid-module-item")
            return [{"type": m.type, "handle": m.handle, "name": (m.name or b"").decode("latin-1"),
                     "info": (m.info or b"").decode("latin-1"), "status": m.status}
                    for m in item.data[:item.count]]
        finally:
            self.call("PDUDestroyItem", ptr)

    def connect_module(self, handle):
        self.call("PDUModuleConnect", handle)
        self.module = handle

    def check_firmware(self, device):
        require_matching_firmware(device.get("address"))

    def voltage(self):
        ptr = c.POINTER(DataItem)()
        oid = self.object_id(0x8023, "PDU_IOCTL_READ_VBATT")
        self.call("PDUIoCtl", self.module, UNDEF, oid, None, c.byref(ptr))
        if not ptr:
            raise ElmError("d-pdu:voltage-item-missing")
        try:
            if ptr.contents.type != 0x1000 or not ptr.contents.data:
                raise ElmError("d-pdu:invalid-voltage-item")
            return c.cast(ptr.contents.data, c.POINTER(U32)).contents.value / 1000.0
        finally:
            self.call("PDUDestroyItem", ptr)

    def _uint(self, item):
        if item.type != 0x1200 or item.data_type != 0x105 or not item.data:
            raise ElmError("d-pdu:invalid-uint-param")
        return c.cast(item.data, c.POINTER(U32))

    def set_param(self, name, value):
        ptr = c.POINTER(Param)()
        oid = self.object_id(0x8024, name)
        self.call("PDUGetComParam", self.module, self.link, oid, c.byref(ptr))
        if not ptr:
            raise ElmError("d-pdu:param-missing:" + name)
        try:
            self._uint(ptr.contents).contents.value = value
            self.call("PDUSetComParam", self.module, self.link, ptr)
        finally:
            self.call("PDUDestroyItem", ptr)

    def create_link(self, protocol, tx, rx, *, connect=True):
        if self.link is not None:
            raise ElmError("d-pdu:link-already-created")
        pins = (Pin * 2)(Pin(6, self.object_id(0x8025, "HI")), Pin(14, self.object_id(0x8025, "LOW")))
        resource = Resource(self.object_id(0x8022, "ISO_11898_2_DWCAN"),
                            self.object_id(0x8021, protocol), 2, pins)
        data = (c.c_uint8 * 4)(0x40, 0, 0, 0)
        flag = Flag(4, data)
        link = U32(UNDEF)
        self.call("PDUCreateComLogicalLink", self.module, c.byref(resource), ID_UNDEF,
                  None, c.byref(link), c.byref(flag))
        self.link = link.value
        # Prevent spontaneous tester-present and automatic resend on NRC21/23.
        for name, value in {"CP_Baudrate": 500000, "CP_RequestAddrMode": 2 if tx == 0x7DF else 1,
                            "CP_TesterPresentHandling": 0, "CP_RC21Handling": 0,
                            "CP_RC23Handling": 0, "CP_RC78Handling": 1,
                            "CP_RC78CompletionTimeout": 8000000,
                            "CP_CanFuncReqId": 0x7DF, "CP_CanFuncReqFormat": 5}.items():
            self.set_param(name, value)
        table = c.POINTER(UniqueTable)()
        self.call("PDUGetUniqueRespIdTable", self.module, self.link, c.byref(table))
        if not table:
            raise ElmError("d-pdu:response-table-missing")
        try:
            t = table.contents
            if t.type != 0x1700 or t.count != 1 or not t.data:
                raise ElmError("d-pdu:ambiguous-response-table")
            entry = t.data[0]
            if not 1 <= entry.count <= 64 or not entry.params:
                raise ElmError("d-pdu:invalid-response-params")
            values = {"CP_CanPhysReqId": 0x7E0 if tx == 0x7DF else tx,
                      "CP_CanPhysReqFormat": 5, "CP_CanRespUSDTId": rx, "CP_CanRespUSDTFormat": 5,
                      "CP_CanRespUUDTId": UNDEF}
            ids = {self.object_id(0x8024, name): value for name, value in values.items()}
            found = set()
            for param in entry.params[:entry.count]:
                if param.id in ids:
                    self._uint(param).contents.value = ids[param.id]
                    found.add(param.id)
            if found != set(ids):
                raise ElmError("d-pdu:incomplete-response-addressing")
            entry.id = 1
            self.call("PDUSetUniqueRespIdTable", self.module, self.link, table)
        finally:
            self.call("PDUDestroyItem", table)
        if connect:
            self.call("PDUConnect", self.module, self.link)
            self.online = True

    def start(self, payload):
        # One transmit, one receive. No STARTCOMM or automatic tester-present.
        flags_data = (c.c_uint8 * 4)(0, 0, 0, 0)
        control = Control(0, 1, 1, 0, Flag(4, flags_data), 0, None)
        data = (c.c_uint8 * len(payload)).from_buffer_copy(payload)
        cop = U32(UNDEF)
        self.call("PDUStartComPrimitive", self.module, self.link, 0x8004, len(payload),
                  data, c.byref(control), None, c.byref(cop))
        return cop.value

    def event(self):
        ptr = c.POINTER(Event)()
        code = self.dll.PDUGetEventItem(self.module, self.link, c.byref(ptr))
        if code == 0x71:
            return None
        _check(code, "PDUGetEventItem")
        if not ptr:
            raise ElmError("d-pdu:event-missing")
        try:
            ev = ptr.contents
            out = {"type": ev.type, "cop": ev.cop, "timestamp": ev.timestamp}
            if not ev.data:
                raise ElmError("d-pdu:event-data-missing")
            if ev.type == 0x1300:
                r = c.cast(ev.data, c.POINTER(Result)).contents
                if not 1 <= r.size <= 4095 or not r.data:
                    raise ElmError("d-pdu:invalid-result-size")
                if r.flags.count > 4 or (r.flags.count and not r.flags.data):
                    raise ElmError("d-pdu:invalid-result-flags")
                out.update(payload=c.string_at(r.data, r.size), unique_id=r.unique_id,
                           flags=bytes(r.flags.data[:r.flags.count]))
            elif ev.type in (0x1301, 0x1302, 0x1303):
                out["value"] = c.cast(ev.data, c.POINTER(U32)).contents.value
            else:
                raise ElmError("d-pdu:unknown-event-type")
            return out
        finally:
            self.call("PDUDestroyItem", ptr)

    def cancel(self, cop):
        self.call("PDUCancelComPrimitive", self.module, self.link, cop)

    def close(self):
        errors = []
        actions = []
        if self.online:
            actions.append(("PDUDisconnect", (self.module, self.link)))
        if self.link is not None:
            actions.append(("PDUDestroyComLogicalLink", (self.module, self.link)))
        if self.module is not None:
            actions.append(("PDUModuleDisconnect", (self.module,)))
        if self.constructed:
            actions.append(("PDUDestruct", ()))
        for name, args in actions:
            try:
                self.call(name, *args)
            except Exception as e:
                errors.append(str(e))
        self.module = self.link = None
        self.online = self.constructed = False
        self._directory.close()
        return {"errors": errors, "adapterClose": "D-PDU disconnect/destruct", "ecuRestorationProven": False}


def discover():
    if sys.platform != "win32" or not (vendor_root() / "PDUAPI_VW.dll").is_file():
        return []
    native = NativeDpu()
    try:
        return [device for row in native.modules() if (device := module_device(row)) is not None]
    finally:
        if native.close()["errors"]:
            raise ElmError("vnci-discovery-cleanup-failed")


class DpuClient:
    def __init__(self, device_id, *, cancel_event=None, native=None):
        if not isinstance(device_id, str) or not DEVICE_RE.fullmatch(device_id):
            raise ElmError("vnci-device-id-invalid")
        self.device_id = device_id
        self.native = native or NativeDpu()
        self.raw_log = []
        self.cancel_event = cancel_event
        self._closed = False
        self._poisoned = False
        self._configured = False
        self._lock = threading.Lock()
        self.allowed = set()
        self.identity = None

    def _guard(self, deadline):
        if self._closed or self._poisoned:
            raise ElmError("client-poisoned")
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ElmError("cancelled")
        if time.monotonic() >= deadline:
            raise ElmError("deadline-expired")

    def validate_adapter(self, budget_s=10):
        self._guard(time.monotonic() + budget_s)
        matches = [(r, d) for r in self.native.modules()
                   if (d := module_device(r)) is not None and d["id"] == self.device_id]
        if len(matches) != 1:
            raise ElmError("vnci-device-identity-missing-or-ambiguous")
        row, device = matches[0]
        if not device["available"]:
            raise ElmError("vnci-device-in-use")
        self.native.check_firmware(device)
        self.native.connect_module(row["handle"])
        self.identity = device
        volts = self.native.voltage()
        self.raw_log.append({"kind": "adapter-voltage", "volts": _display_voltage(volts),
                             "reportedVolts": volts if isinstance(volts, (int, float))
                             and not isinstance(volts, bool) and math.isfinite(volts) else None,
                             "source": "d-pdu-vbatt", "commOk": True})
        return {"ati": "VNCI VAS6154A " + device["serial"], "atdpn": None,
                "atrv": None, "volts": _display_voltage(volts), "voltageSource": "d-pdu-vbatt", "commOk": True,
                "deviceId": self.device_id, "transport": "d-pdu-usb"}

    def configure_pair(self, tx_hex, rx_hex, deadline):
        from .catalog import load_catalog, live_allowed_hex
        self._guard(deadline)
        pairs = [p for p in load_catalog()["profiles"]
                 if p["id"] in ("porsche-981-2014-dme", "porsche-981-2014-gateway")
                 and p["txId"] == tx_hex and p["rxId"] == rx_hex]
        if len(pairs) != 1:
            raise ElmError("vnci-address-not-authorized")
        profile = pairs[0]
        self.allowed = live_allowed_hex(profile) | {"1089" if profile["ecu"] == "DME" else "1003"}
        if profile["ecu"] == "DME":
            self.allowed |= ENGINE_READS
        protocol = "ISO_14230_3_on_ISO_15765_2" if profile["ecu"] == "DME" else "ISO_15765_3_on_ISO_15765_2"
        self.native.create_link(protocol, int(tx_hex, 16), int(rx_hex, 16))
        self._configured = True

    def configure_standard_engine(self, deadline):
        self._guard(deadline)
        self.allowed = set(ENGINE_READS)
        self.native.create_link("ISO_OBD_on_ISO_15765_4", 0x7DF, 0x7E8)
        self._configured = True

    def request(self, req_hex, deadline):
        self._guard(deadline)
        if not self._configured:
            raise ElmError("not-configured")
        try:
            payload = bytes.fromhex(req_hex)
        except (ValueError, TypeError):
            raise ElmError("invalid-request-hex") from None
        reason = blocked_reason(payload, self.allowed)
        if reason:
            raise ElmError(reason)
        if not self._lock.acquire(blocking=False):
            raise ElmError("request-in-flight")
        cop = None
        response = None
        try:
            # A leftover event cannot be reused as the new request's response.
            for _ in range(32):
                prior = self.native.event()
                if prior is None:
                    break
                if prior["type"] != 0x1301 or prior["cop"] != UNDEF or prior["value"] not in (0x8050, 0x8051):
                    raise ElmError("desynchronized-rx")
            else:
                raise ElmError("d-pdu:setup-event-cap")
            attempt = {"dir": "tx", "hex": payload.hex().upper(), "layer": "diagnostic-pdu",
                       "cop": None, "sendState": "attempted"}
            self.raw_log.append(attempt)
            cop = self.native.start(payload)
            attempt.update(cop=cop, sendState="api-accepted")
            while True:
                self._guard(deadline)
                ev = self.native.event()
                if ev is None:
                    time.sleep(0.01)
                    continue
                if ev["cop"] != cop:
                    raise ElmError("d-pdu:foreign-com-primitive")
                if ev["type"] == 0x1302:
                    raise ElmError(f"d-pdu:event-error:0x{ev['value']:X}")
                if ev["type"] == 0x1300:
                    data = ev["payload"]
                    self.raw_log.append({"dir": "rx", "hex": data.hex().upper(), "layer": "diagnostic-pdu",
                                         "cop": cop, "timestampUs": ev["timestamp"], "uniqueId": ev["unique_id"]})
                    match = response_matches(payload, data)
                    if (ev["unique_id"] != 1 or any(ev["flags"]) or response is not None
                            or match not in ("positive-echo", "negative")):
                        raise ElmError("d-pdu:malformed-or-unmatched-response")
                    response = {"ok": match == "positive-echo", "payload_hex": data.hex().upper(),
                                "error": None if match == "positive-echo" else "negative-response",
                                "pending": [], "raw": data.hex().upper()}
                if ev["type"] == 0x1301:
                    if ev["value"] == 0x8012:
                        if response is None:
                            raise ElmError("d-pdu:finished-without-response")
                        return response
                    if ev["value"] == 0x8013:
                        raise ElmError("cancelled")
        except Exception:
            self._poisoned = True
            if cop is not None:
                try:
                    self.native.cancel(cop)
                except Exception:
                    pass
            raise
        finally:
            self._lock.release()

    def close_restore(self):
        if self._closed:
            return {"errors": [], "adapterClose": "D-PDU disconnect/destruct", "ecuRestorationProven": False}
        self._closed = True
        self._configured = False
        return self.native.close()


def run_monitor(device_id, *, stdout, stdin=None, stop_event=None, max_samples=None, client_factory=DpuClient):
    from .connection import _emit, _utc, _watch_stdin_stop
    stop = stop_event or threading.Event()
    if stdin is not None and stop_event is None:
        _watch_stdin_stop(stdin, stop)
    client = None
    base = {"deviceId": device_id, "simulation": False, "liveVerified": False, "writePayload": None,
            "voltageSource": "d-pdu-vbatt", "serialOpens": 0}
    status = 0
    try:
        client = client_factory(device_id, cancel_event=stop)
        adapter = client.validate_adapter()
        count = 1
        _emit(stdout, {**base, **adapter, "ok": True, "type": "handshake", "identityOnce": True, "at": _utc()})
        while not stop.is_set() and (max_samples is None or count < max_samples):
            if stop.wait(2):
                break
            volts = client.native.voltage()
            _emit(stdout, {**base, "ok": True, "type": "reading", "volts": _display_voltage(volts),
                           "commOk": True, "at": _utc()})
            count += 1
    except Exception as e:
        _emit(stdout, {**base, "ok": False, "type": "error", "error": str(e)})
        status = 1
    finally:
        if client is not None:
            cleanup = client.close_restore()
            if cleanup["errors"]:
                status = 1
                _emit(stdout, {**base, "ok": False, "type": "error", "error": device_id.split(':', 1)[0] + "-cleanup-failed", "restoration": cleanup})
            else:
                _emit(stdout, {**base, "ok": True, "type": "stopped", "restoration": cleanup})
    return status


def main():
    try:
        print(json.dumps({"ok": True, "devices": discover(), "ecuRequestsSent": 0,
                          "codingEnabled": False, "liveVerified": False}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "ecuRequestsSent": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
