"""Typed, bounded manufacturer reads through an explicitly selected adapter.

Requires a newly compiled, hash-bound private preparation bundle and a fresh
complete DME/Gateway identity. No renderer requests, addresses, formulas or fixture PDUs
are accepted. This software entry does not establish physical qualification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading

from .decode import redact_vin
from .elm import ElmClient, ElmError
from .manufacturer_acquisition import DME_PROFILE, READ_PROFILES
from .manufacturer_transport import _client_cycles, _empty_result, prepared_read_contract
from .realtime_preparation import ROOT, DEFAULT_OUTPUT, load, verify_source

# Keep explicit runtime dependencies bound to the compiler manifest. A legacy
# rehearsal-only manifest cannot silently authorize the new hardware entry.
READ_CODE = (
    'scripts/__init__.py', 'scripts/diagnostics/__init__.py',
    'scripts/diagnostics/manufacturer_read.py',
    'scripts/diagnostics/manufacturer_acquisition.py',
    'scripts/diagnostics/manufacturer_transport.py',
    'scripts/diagnostics/realtime_preparation.py',
    'scripts/diagnostics/response_values.py', 'scripts/diagnostics/x431_values.py',
    'scripts/diagnostics/x431_formula.py', 'scripts/x431_re/request_fields.py',
    'scripts/diagnostics/elm.py', 'scripts/diagnostics/capture_clock.py', 'scripts/diagnostics/connection.py',
    'scripts/diagnostics/catalog.py', 'scripts/diagnostics/qualification.py',
    'scripts/diagnostics/decode.py', 'scripts/diagnostics/sessions.py',
    'scripts/diagnostics/allowlist.py', 'scripts/diagnostics/session_simulator.py',
    'scripts/diagnostics/offline_match.py', 'scripts/diagnostics/isotp.py',
    'scripts/diagnostics/pair.py', 'scripts/diagnostics/can_monitor.py',
    'scripts/diagnostics/vnci.py',
)
CATALOG_FILE = 'data/seed/diagnostics/catalog.v1.json'
SOURCE = 'independent-manufacturer-read'


def _verify(path, expected):
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('manufacturer-source-hash-invalid')
    return verify_source(path, expected)


def verified_profile(request, root=None):
    """Load only typed IDs from a compiled private bundle, without fixture access."""
    root = Path(root or os.environ.get('PORSCHE981_REALTIME_PREPARATION') or DEFAULT_OUTPUT).resolve()
    manifest = load(root / 'manifest.json')
    read = manifest.get('manufacturerRead') or {}
    if manifest.get('schemaVersion') != 2 or read.get('schemaVersion') != 1:
        raise ValueError('manufacturer-read-bundle-required')
    known = {}
    for item in manifest['code']:
        file = item['path']
        if (not isinstance(file, str) or not file.startswith('scripts/') or '\\' in file
                or '..' in Path(file).parts or Path(file).is_absolute() or file in known):
            raise ValueError('manufacturer-code-path-invalid')
        path = (ROOT / file).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError('manufacturer-code-path-invalid')
        _verify(path, item.get('sha256'))
        known[file] = item
    if not set(READ_CODE).issubset(known):
        raise ValueError('manufacturer-read-code-missing')
    if read.get('catalogPath') != CATALOG_FILE:
        raise ValueError('manufacturer-catalog-path-invalid')
    _verify(ROOT / CATALOG_FILE, read.get('catalogSha256'))
    indexes = [item for item in manifest['outputs'] if item['path'] == 'index.json']
    if len(indexes) != 1:
        raise ValueError('manufacturer-index-binding-invalid')
    _verify(root / 'index.json', indexes[0].get('sha256'))
    rows = [p for p in load(root / 'index.json')['profiles'] if p['profileId'] == request['profileId']]
    if len(rows) != 1:
        raise ValueError('unknown-profile')
    row = rows[0]
    if request['ecuId'] not in row['ecuIds']:
        raise ValueError('profile-ecu-mismatch')
    file = row['file']
    if not isinstance(file, str) or not re.fullmatch(r'profiles/[0-9a-f]{64}\.json', file):
        raise ValueError('invalid-profile-file')
    path = (root / file).resolve()
    if not path.is_relative_to(root):
        raise ValueError('invalid-profile-file')
    _verify(path, row.get('sha256'))
    profile = load(path)
    if profile.get('profileId') != request['profileId'] or profile.get('ecuIds') != row['ecuIds']:
        raise ValueError('manufacturer-profile-binding-invalid')
    prepared_read_contract(profile, request['parameterIds'])
    return profile


def _validate(request):
    fields = {'action', 'generation', 'ecuId', 'profileId', 'parameterIds',
              'deviceId', 'adapterModel', 'confirmedReadOnly', 'x431Inactive', 'cycleCount', 'budgetSeconds'}
    if not isinstance(request, dict) or set(request) - fields:
        raise ValueError('manufacturer-forbidden-field')
    if request.get('action') != 'manufacturer-read':
        raise ValueError('manufacturer-action-invalid')
    pid = request.get('profileId')
    route = READ_PROFILES.get(pid) if isinstance(pid, str) else None
    if (request.get('generation') != '981' or type(request.get('ecuId')) is not int
            or route is None or request['ecuId'] != route[0]):
        raise ValueError('manufacturer-profile-unqualified')
    ids = request.get('parameterIds')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 12
            or any(not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key) for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('manufacturer-selection-invalid')
    if request.get('confirmedReadOnly') is not True or request.get('x431Inactive') is not True:
        raise ValueError('manufacturer-read-preconditions-required')
    from .connection import valid_device_id
    device = request.get('deviceId')
    if not valid_device_id(device):
        raise ValueError('device-id-invalid')
    if device.startswith('pt3g:'):
        raise ValueError('pt3g-vehicle-transport-not-qualified')
    from .connection import ELM_MODELS
    if 'adapterModel' in request and (not isinstance(request['adapterModel'], str) or request['adapterModel'] not in ELM_MODELS | {'VNCI', 'PT3G'}):
        raise ValueError('adapter-model-invalid')
    cycles = request.get('cycleCount', 1)
    # None denotes continuous reads, always bounded by the session's ten minutes.
    if cycles is not None and (type(cycles) is not int or not 1 <= cycles <= 1000):
        raise ValueError('manufacturer-cycles-invalid')
    budget = request.get('budgetSeconds', 30)
    if type(budget) not in (int, float) or not 0 < budget <= 60:
        raise ValueError('manufacturer-budget-invalid')


def read_manufacturer(request, *, root=None, cancel_event=None, on_cycle=None, on_connection=None, on_frame=None, on_pdu=None, check_only=False):
    """Production chooses the physical factory; caller cannot inject byte traffic."""
    out = _empty_result(SOURCE)
    out['simulation'] = False
    try:
        _validate(request)
        profile = verified_profile(request, root)
        device = request['deviceId']
        transport_kind = 'd-pdu-iso-tp' if device.startswith('vnci:') else 'elm-iso-tp'
        out['transport']['kind'] = transport_kind
        if check_only:
            out.update(ok=True, checkOnly=True, readContract=prepared_read_contract(profile,
                request['parameterIds'], transport_kind=transport_kind))
            return out
        def factory(cancel):
            if transport_kind == 'd-pdu-iso-tp':
                from .vnci import DpuClient
                return DpuClient(device, cancel_event=cancel, pdu_sink=on_pdu)
            from .connection import open_selected_port
            port, _com = open_selected_port(device, adapter_model=request.get('adapterModel'))
            return ElmClient(port, cancel_event=cancel, frame_sink=on_frame, pdu_sink=on_pdu,
                expected_model=request.get('adapterModel'))
        out = _client_cycles(profile, request['parameterIds'], factory, physical=True, source=SOURCE,
            cycles=request.get('cycleCount', 1), budget_s=request.get('budgetSeconds', 30),
            interval_s=1 if request.get('cycleCount', 1) is None or request.get('cycleCount', 1) > 1 else 0,
            cancel_event=cancel_event, on_cycle=on_cycle, on_connection=on_connection,
            streaming=True, transport_kind=transport_kind)
        out['deviceId'] = device
    except (ValueError, KeyError, TypeError, OSError, ElmError, ImportError) as error:
        out.update(ok=False, error=str(error))
    return out


def _redact(value):
    if isinstance(value, str): return redact_vin(value)
    if isinstance(value, list): return [_redact(item) for item in value]
    if isinstance(value, dict): return {key: _redact(item) for key, item in value.items()}
    return value


def run(source, sink, *, check_only=False):
    cancel = threading.Event()
    def emit(event, result):
        sink.write(json.dumps({'event': event, 'result': _redact(result)}, ensure_ascii=False) + '\n')
        sink.flush()
    try:
        line = source.readline(65537)
        if len(line.encode('utf-8')) > 65536 or not line.endswith('\n'):
            raise ValueError('stream-request-limit')
        request = json.loads(line)
        _validate(request)
        if not check_only:
            def controls():
                # Only stop is supported; EOF/invalid/new requests stop as well.
                source.readline(256)
                cancel.set()
            threading.Thread(target=controls, daemon=True).start()
        result = read_manufacturer(request, check_only=check_only, cancel_event=cancel,
            on_cycle=lambda value: emit('cycle', value),
            on_connection=lambda value: emit('connection-state', value),
            on_pdu=lambda pdu: emit('diagnostic-pdu', {'executionEnabled': True, 'liveVerified': False,
                'writePayload': None, 'simulation': False, 'sourceKind': SOURCE, 'pdu': pdu}),
            on_frame=lambda frame: emit('can-frame', {'executionEnabled': True, 'liveVerified': False,
                'writePayload': None, 'simulation': False, 'sourceKind': SOURCE, 'frame': frame}))
        result.pop('samples', None)
        result.pop('transactions', None)
        result.pop('connectionEvents', None)
        emit('finished', result)
        return 0 if result.get('ok') else 1
    except (ValueError, TypeError, OSError) as error:
        result = _empty_result(SOURCE)
        result.update(error=str(error), simulation=False)
        emit('finished', result)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='verify typed selection and bundle; never open hardware')
    args = parser.parse_args()
    return run(sys.stdin, sys.stdout, check_only=args.check_only)


if __name__ == '__main__':
    raise SystemExit(main())
