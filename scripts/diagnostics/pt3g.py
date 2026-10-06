"""Pinned E70/PT3G adapter-only USB connection. No CAN links or ECU primitives.

The ACTIA-labelled module is supplied by the local E70 service, not the ACTIA
installer. Firmware/vehicle transport remain unqualified. Only enumeration,
module connection, battery voltage and release are exposed here.
"""
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .elm import ElmError
from .vnci import DataItem, ModuleItem, U32, PTR, UNDEF, ID_UNDEF, _check, _display_voltage

DEVICE_RE = re.compile(r'^pt3g:([0-9]{1,16})$')
SUPPORT_SHA256 = {
    'PDU_VCI.dll': '3ef53a849b98bdbbcf46038c9c89e2a409b0ad63c853563d5990c9e1cb169e15',
    'pdu2.dll': '0ff808bf02b7df3f21dca777ba2b5d3fc546048fdd2e3fc34d4aa7757c441dba',
    'db.dll': '3a1360e03b45d5eef56ab9052232e34ccea0f6a80999f12af0bddf2b5ea88175',
    'Marstool564.dll': '231d2efe6586394d23e3ea06c2567e4f10d6a7ec151b0198d192c77c703284aa',
    'PassthruManage.dll': 'de733647636ffdba4f2282be7cddba3f95b171baa377b0dcb3586a80d5e072af',
    'MDF_VCI.xml': '51b3c90527d6c200bffd47f9556c542122dc50eafdf49c81068f18ac69ecae64',
    'PDU_VCI.ini': '1628c12f271911cdb7eeb91aceeb94f96e3db24835e552cf3f593fc673aea4d3',
    '../E70_PT3G_SERVICE.exe': '7851c4b999ab7b74f7c6278f653900120c04da61635bf454e277d39b48b1e432',
}


def vendor_root(env=None):
    env = os.environ if env is None else env
    return Path(env.get('PORSCHE981_PT3G_ROOT') or 'C:/ProgramData/PORSCHE-VCI/X64').expanduser().resolve()


def verify_vendor_files(root):
    for name, expected in SUPPORT_SHA256.items():
        try:
            with (root / name).open('rb') as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
        except OSError:
            raise ElmError('pt3g-driver-support-missing:' + name) from None
        if actual != expected:
            raise ElmError('pt3g-driver-support-not-qualified:' + name)
    ini = (root / 'PDU_VCI.ini').read_text(encoding='utf-8-sig')
    if re.findall(r'^\s*15765StartDetect\s*=\s*(\d+)\s*$', ini, re.M) != ['0']:
        raise ElmError('pt3g-automatic-protocol-detection-not-disabled')


def module_device(row):
    pairs = re.findall(r"([A-Za-z]+)='([^']*)'", row.get('name', ''))
    fields = dict(pairs)
    serial = fields.get('SerialNumber', '')
    if (len(fields) != len(pairs) or row.get('type') != 11
            or fields.get('ModuleName') != 'PT3G-VCI' or fields.get('ConnectionType') != 'USB'
            or fields.get('IP') != '127.0.0.1' or not re.fullmatch(r'[0-9]{1,16}', serial)):
        return None
    return {'id': 'pt3g:' + serial, 'brand': 'PT3G', 'name': 'PT3G / E70 · USB',
        'transport': 'd-pdu-usb', 'serial': serial, 'address': fields['IP'], 'comPort': None,
        'available': row.get('status') == 0x8063, 'paired': False,
        'osStatus': 'available' if row.get('status') == 0x8063 else 'unavailable',
        'guidance': 'USB 诊断头连接与供电监测；车辆读取、设码及内网监听尚未资格化。',
        'identitySource': ['d-pdu-module-serial'], 'vehicleTransportQualified': False}


class NativeAdapter:
    """Bind only adapter APIs; no CAN link/primitive exports are bound."""
    def __init__(self, root=None):
        if sys.platform != 'win32' or c.sizeof(PTR) != 8:
            raise ElmError('pt3g-requires-windows-x64-python')
        root = Path(root if root is not None else vendor_root()).resolve()
        verify_vendor_files(root)
        self.module, self.constructed = None, False
        self._voltage_oid = None
        self._directory = os.add_dll_directory(str(root))
        try:
            self.dll = c.WinDLL(str(root / 'PDU_VCI.dll'))
            signatures = {
                'PDUConstruct': [c.c_char_p, PTR], 'PDUDestruct': [],
                'PDUGetModuleIds': [c.POINTER(c.POINTER(ModuleItem))], 'PDUDestroyItem': [PTR],
                'PDUModuleConnect': [U32], 'PDUModuleDisconnect': [U32],
                'PDUGetObjectId': [U32, c.c_char_p, c.POINTER(U32)],
                'PDUIoCtl': [U32, U32, U32, PTR, c.POINTER(c.POINTER(DataItem))],
            }
            self._allowed = frozenset(signatures)
            for name, args in signatures.items():
                fn = getattr(self.dll, name)
                fn.argtypes, fn.restype = args, U32
            self.call('PDUConstruct', None, None)
            self.constructed = True
        except Exception:
            self._directory.close()
            raise

    def call(self, name, *args):
        if name not in self._allowed:
            raise ElmError('pt3g-adapter-only-api')
        if name == 'PDUGetObjectId' and (args[0] != 0x8023 or args[1] != b'PDU_IOCTL_READ_VBATT'):
            raise ElmError('pt3g-adapter-only-object')
        if name == 'PDUIoCtl' and (args[1] != UNDEF or args[2] != self._voltage_oid or args[3] is not None):
            raise ElmError('pt3g-adapter-only-ioctl')
        _check(getattr(self.dll, name)(*args), name)

    def modules(self):
        ptr = c.POINTER(ModuleItem)()
        self.call('PDUGetModuleIds', c.byref(ptr))
        if not ptr:
            raise ElmError('d-pdu:module-item-missing')
        try:
            item = ptr.contents
            if item.type != 0x1600 or item.count > 64 or (item.count and not item.data):
                raise ElmError('d-pdu:invalid-module-item')
            return [{'type': m.type, 'handle': m.handle, 'name': (m.name or b'').decode('latin-1'),
                     'info': (m.info or b'').decode('latin-1'), 'status': m.status}
                    for m in item.data[:item.count]]
        finally:
            self.call('PDUDestroyItem', ptr)

    def connect_module(self, handle):
        self.call('PDUModuleConnect', handle)
        self.module = handle

    def voltage(self):
        oid = U32(ID_UNDEF)
        self.call('PDUGetObjectId', 0x8023, b'PDU_IOCTL_READ_VBATT', c.byref(oid))
        if oid.value in (ID_UNDEF, UNDEF):
            raise ElmError('pt3g-voltage-object-unsupported')
        self._voltage_oid = oid.value
        ptr = c.POINTER(DataItem)()
        self.call('PDUIoCtl', self.module, UNDEF, oid.value, None, c.byref(ptr))
        if not ptr:
            raise ElmError('d-pdu:voltage-item-missing')
        try:
            if ptr.contents.type != 0x1000 or not ptr.contents.data:
                raise ElmError('d-pdu:invalid-voltage-item')
            return c.cast(ptr.contents.data, c.POINTER(U32)).contents.value / 1000.0
        finally:
            self.call('PDUDestroyItem', ptr)

    def close(self):
        errors = []
        actions = [('PDUModuleDisconnect', (self.module,))] if self.module is not None else []
        if self.constructed:
            actions.append(('PDUDestruct', ()))
        for name, args in actions:
            try:
                self.call(name, *args)
            except Exception as error:
                errors.append(str(error))
        self.module, self.constructed = None, False
        self._directory.close()
        return {'errors': errors, 'adapterClose': 'D-PDU module disconnect/destruct',
                'ecuRestorationProven': False}


def discover():
    if sys.platform != 'win32' or not (vendor_root() / 'PDU_VCI.dll').is_file():
        return []
    native = NativeAdapter()
    try:
        return [device for row in native.modules() if (device := module_device(row)) is not None]
    finally:
        if native.close()['errors']:
            raise ElmError('pt3g-discovery-cleanup-failed')


class AdapterClient:
    def __init__(self, device_id, *, cancel_event=None, native=None):
        if not isinstance(device_id, str) or not DEVICE_RE.fullmatch(device_id):
            raise ElmError('pt3g-device-id-invalid')
        self.device_id, self.cancel_event = device_id, cancel_event
        self.native = native if native is not None else NativeAdapter()

    def validate_adapter(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ElmError('cancelled')
        matches = [(row, device) for row in self.native.modules()
                   if (device := module_device(row)) is not None and device['id'] == self.device_id]
        if len(matches) != 1:
            raise ElmError('pt3g-device-identity-missing-or-ambiguous')
        row, device = matches[0]
        if not device['available']:
            raise ElmError('pt3g-device-in-use')
        self.native.connect_module(row['handle'])
        volts = self.native.voltage()
        return {'ati': 'PT3G / E70 ' + device['serial'], 'volts': _display_voltage(volts),
                'voltageSource': 'd-pdu-vbatt', 'commOk': True, 'deviceId': self.device_id,
                'transport': 'd-pdu-usb', 'vehicleTransportQualified': False}

    def close_restore(self):
        return self.native.close()


def run_monitor(device_id, **kwargs):
    from .vnci import run_monitor as monitor
    return monitor(device_id, client_factory=AdapterClient, **kwargs)


def main():
    try:
        print(json.dumps({'ok': True, 'devices': discover(), 'ecuRequestsSent': 0,
                          'vehicleTransportQualified': False}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error), 'ecuRequestsSent': 0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
