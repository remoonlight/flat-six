"""PT3G read transport implementation with physical execution still blocked.

Uses the shared D-PDU configuration, request/response and cleanup implementation.
Only metadata APIs can currently reach the real DLL: no matching physical-head
firmware/release evidence exists. Injected rehearsal is not vehicle qualification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .elm import ElmError
from .pt3g import DEVICE_RE, NativeAdapter, module_device, vendor_root
from .pt3g_read_source import read_source_contract
from .pt3g_transport_source import audit_transport_source
from .vnci import DpuClient, NativeDpu, U32, UINT_PARAMETER_CLASSES, _check

METADATA_APIS = frozenset(('PDUConstruct', 'PDUDestruct', 'PDUDestroyItem',
                          'PDUGetObjectId', 'PDUGetModuleIds'))
BLOCK_REASON = 'pt3g-vehicle-transport-not-qualified'
GUARDS = dict(hardwareIdentityQualified=False, firmwareQualified=False,
              vehicleTransportQualified=False, physicalReleaseProven=False,
              executionEnabled=False, ecuRequestsSent=0)
PARAMETER_CLASSES = {'COM': 3, 'ERRHDL': 4, 'BUSTYPE': 5, 'UNIQUE_ID': 6, 'TESTER_PRESENT': 7}


def prepare_read_transport(root):
    """Validate both source routes and their compatibility with shared setup."""
    root = Path(root)
    source = read_source_contract(root / 'MDF_VCI.xml')
    transport = audit_transport_source(root)
    for route in source['routes']:
        for param in route['parameters']:
            if PARAMETER_CLASSES.get(param['parameterClass']) != UINT_PARAMETER_CLASSES.get(param['name']):
                raise ElmError('pt3g-read-parameter-class-not-compatible:' + param['name'])
    return dict(kind='pt3g-read-transport-preparation', sourceContract=source,
                transportSource=transport, plannedApiNames=list(NativeDpu.export_signatures()), **GUARDS)


class NativeReadTransport(NativeDpu):
    def __init__(self, root=None):
        root = Path(root if root is not None else vendor_root()).resolve()
        self.preparation = prepare_read_transport(root)
        self.contract = self.preparation['sourceContract']
        self._source_objects = {(r['objectType'], r['name'].encode('ascii')): r['sourceId']
                                for r in self.contract['objectQueries']}
        self.link, self.online = None, False
        # Adapter initialization verifies the entire private package and effective
        # INI before loading this vendor's DLL. It calls Construct only.
        NativeAdapter.__init__(self, root)
        self.bound_api_names = []
        try:
            for name, args in self.export_signatures().items():
                fn = getattr(self.dll, name)
                fn.argtypes, fn.restype = args, U32
                self.bound_api_names.append(name)
        except Exception as error:
            cleanup = self.close()
            if cleanup['errors']:
                raise ElmError('pt3g-read-binding-cleanup-failed:' + ';'.join(cleanup['errors'])) from error
            raise

    def call(self, name, *args):
        if name not in METADATA_APIS:
            self._require_physical_qualification()
            if name not in self.export_signatures():
                raise ElmError('pt3g-read-api-not-qualified')
            _check(getattr(self.dll, name)(*args), name)
            return
        if name == 'PDUGetObjectId':
            if (len(args) != 3 or type(args[0]) is not int or type(args[1]) is not bytes
                    or (args[0], args[1]) not in self._source_objects):
                raise ElmError('pt3g-read-object-not-in-source-contract')
            _check(self.dll.PDUGetObjectId(*args), name)
        else:
            NativeAdapter.call(self, name, *args)

    def object_id(self, kind, name):
        actual = super().object_id(kind, name)
        if actual != self._source_objects.get((kind, name.encode('ascii'))):
            raise ElmError('pt3g-read-native-object-id-mismatch:' + name)
        return actual

    def check_firmware(self, device):
        # The wrapper's driver-version query returned zero metadata, and its
        # delegated API failed. Neither proves this physical head's firmware.
        raise ElmError('pt3g-firmware-not-qualified')

    @staticmethod
    def _require_physical_qualification():
        # No Boolean flag or imported/source metadata can turn this into a
        # physical transport authorization. Its missing evidence is explicit.
        raise ElmError(BLOCK_REASON)

    def create_link(self, protocol, tx, rx, *, connect=True):
        matches = [r for r in self.contract['routes'] if r['protocol'] == protocol
                   and r['txId'] == f'{tx:03X}' and r['rxId'] == f'{rx:03X}'] if (
                       type(tx) is int and type(rx) is int and type(connect) is bool) else []
        if len(matches) != 1:
            raise ElmError('pt3g-read-route-not-in-source-contract')
        return super().create_link(protocol, tx, rx, connect=connect)

    def event(self):
        # NativeDpu.event accesses PDUGetEventItem directly for the empty-queue
        # return code. Guard that entry as well as the normal call dispatcher.
        self._require_physical_qualification()
        return super().event()


class ReadClient(DpuClient):
    _device_pattern = DEVICE_RE
    _device_brand = 'pt3g'
    _adapter_label = 'PT3G / E70'

    @staticmethod
    def _new_native():
        return NativeReadTransport()

    @staticmethod
    def _device_from_module(row):
        return module_device(row)

    def validate_adapter(self, budget_s=10):
        return super().validate_adapter(budget_s) | dict(vehicleTransportQualified=False)


def probe_read_transport_metadata(root):
    """Construct, source object queries, destruct; no module/link/primitive."""
    native = NativeReadTransport(root)
    rows = []
    error = None
    try:
        for row in native.contract['objectQueries']:
            rows.append(row | dict(actualId=native.object_id(row['objectType'], row['name'])))
    except Exception as problem:
        error = str(problem)
    finally:
        cleanup = native.close()
    return dict(ok=error is None and not cleanup['errors'], error=error,
                kind='pt3g-read-transport-metadata-probe',
                preparation=native.preparation, objects=rows, cleanup=cleanup,
                configuration=native.configuration, boundApiNames=native.bound_api_names, **GUARDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root', type=Path, required=True)
    parser.add_argument('--probe-metadata', action='store_true')
    args = parser.parse_args()
    report = probe_read_transport_metadata(args.driver_root) if args.probe_metadata else prepare_read_transport(args.driver_root)
    print(json.dumps(report, indent=2))
    return 0 if report.get('ok', True) else 2


if __name__ == '__main__':
    raise SystemExit(main())
