"""Typed, bounded manufacturer reads through an explicitly selected adapter.

Requires a newly compiled, hash-bound private preparation bundle and fresh
source-defined identity/version reads. No renderer requests, addresses, formulas or fixture PDUs
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
import time

from .decode import redact_vin
from .sessions import ManualReadRetry
from .elm import ElmClient, ElmError
from .manufacturer_acquisition import DME_PROFILE, GATEWAY_PROFILE, READ_PROFILES, FAULT_READ_PROFILES, read_profile_targets, read_profile_generations
from .manufacturer_transport import _client_cycles, _empty_result, prepared_read_contract
from .realtime_preparation import ROOT, DEFAULT_OUTPUT, load, verify_source

# Keep explicit runtime dependencies bound to the compiler manifest. A legacy
# rehearsal-only manifest cannot silently authorize the new hardware entry.
READ_CODE = (
    'scripts/__init__.py', 'scripts/diagnostics/__init__.py',
    'scripts/diagnostics/manufacturer_read.py',
    'scripts/diagnostics/manufacturer_acquisition.py',
    'scripts/diagnostics/manufacturer_transport.py',
    'scripts/diagnostics/manufacturer_dtc.py', 'scripts/diagnostics/manufacturer_coding.py',
    'scripts/diagnostics/manufacturer_coding_fields.py',
    'scripts/diagnostics/coding_named_edits.py',
    'scripts/diagnostics/coding_numeric.py',
    'scripts/diagnostics/coding_text.py',
    'scripts/diagnostics/gateway_coding_source.py', 'scripts/diagnostics/gateway-coding-scopes.json',
    'scripts/diagnostics/dme_coding_source.py', 'scripts/diagnostics/dme-coding-scopes.json',
    'scripts/diagnostics/pdk_coding_source.py', 'scripts/diagnostics/pdk-coding-scopes.json',
    'scripts/diagnostics/uds_coding_source.py', 'scripts/diagnostics/uds-coding-scopes.json',
    'scripts/diagnostics/mode50_vnci.py',
    'scripts/diagnostics/mode30_transport.py', 'scripts/diagnostics/mode30_vnci.py',
    'scripts/diagnostics/mode30_elm.py',
    'scripts/diagnostics/mode30_source.py', 'scripts/diagnostics/mode30_clients.py',
    'scripts/diagnostics/realtime_preparation.py',
    'scripts/diagnostics/response_values.py', 'scripts/diagnostics/x431_values.py',
    'scripts/diagnostics/x431_formula.py', 'scripts/x431_re/request_fields.py',
    'scripts/diagnostics/elm.py', 'scripts/diagnostics/capture_clock.py', 'scripts/diagnostics/connection.py',
    'scripts/diagnostics/tcp_port.py',
    'scripts/diagnostics/catalog.py', 'scripts/diagnostics/qualification.py',
    'scripts/diagnostics/decode.py', 'scripts/diagnostics/sessions.py',
    'scripts/diagnostics/allowlist.py', 'scripts/diagnostics/session_simulator.py',
    'scripts/diagnostics/offline_match.py', 'scripts/diagnostics/isotp.py',
    'scripts/diagnostics/pair.py', 'scripts/diagnostics/can_monitor.py',
    'scripts/diagnostics/vnci.py',
    'scripts/diagnostics/pdk_read.py', 'scripts/diagnostics/source_catalog.py',
    'scripts/diagnostics/uds_source_read.py', 'scripts/diagnostics/swsg_source.py', 'scripts/diagnostics/bose_source.py', 'scripts/diagnostics/headlamp_source.py', 'scripts/diagnostics/mode50_transport.py',
    'scripts/diagnostics/x431_command_origin.py', 'scripts/diagnostics/x431_variant_selection.py',
    'scripts/diagnostics/x431_version_response.py', 'scripts/diagnostics/x431_response_contract.py',
    'scripts/x431_re/gag_lib.py', 'scripts/x431_re/gag_codec.py', 'scripts/x431_re/ggp_index.py', 'scripts/x431_re/sysdata.py',
)
CATALOG_FILE = 'data/seed/diagnostics/catalog.v1.json'
SOURCE = 'independent-manufacturer-read'


def _verify(path, expected):
    if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('manufacturer-source-hash-invalid')
    return verify_source(path, expected)


class _SourceFormatError(ValueError):
    def __init__(self, cause):
        super().__init__('manufacturer-read-bundle-required')
        self.source_detail = (type(cause).__name__ + ': ' + str(cause))[:4096]


def verified_profile(request, root=None):
    # This phase only reads local definitions. Normalize malformed JSON shapes
    # here, before the adapter factory exists; never infer this after opening.
    try:
        return _verified_profile(request, root)
    except (AttributeError, IndexError, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise _SourceFormatError(error) from error


def _verified_profile(request, root=None):
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
    # The immutable shared definition stays intact. Only the validated typed
    # target is added to this session-owned copy after its file hash is checked.
    profile = {**profile, 'selectedEcuId': request['ecuId'],'requestedGeneration':request['generation']}
    if request.get('sourceBound') is True: profile['sourceBound'] = True
    prepared_read_contract(profile, request['parameterIds'], identity_only=request.get('action') in ('manufacturer-identify','manufacturer-read-dtc','manufacturer-read-coding'))
    return profile


def _validate(request):
    if isinstance(request, dict) and request.get('action') == 'manufacturer-identify-vehicle':
        allowed = {'action', 'deviceId', 'adapterModel', 'serialBaud', 'confirmedReadOnly', 'x431Inactive', 'budgetSeconds'}
        if set(request) - allowed:
            raise ValueError('manufacturer-forbidden-field')
        return _validate(_vehicle_request(request))
    fields = {'action', 'generation', 'ecuId', 'profileId', 'parameterIds',
              'deviceId', 'adapterModel', 'serialBaud', 'confirmedReadOnly', 'x431Inactive', 'cycleCount', 'budgetSeconds', 'sourceBound', 'expectedVehicleIdentitySha256', 'completeOriginalRequired'}
    if not isinstance(request, dict) or set(request) - fields:
        raise ValueError('manufacturer-forbidden-field')
    identifying = request.get('action') in ('manufacturer-identify','manufacturer-read-dtc','manufacturer-read-coding')
    if 'completeOriginalRequired' in request and (request.get('action') != 'manufacturer-read-coding'
            or type(request['completeOriginalRequired']) is not bool):
        raise ValueError('manufacturer-complete-scope-flag-invalid')
    if 'expectedVehicleIdentitySha256' in request and (request.get('action') not in ('manufacturer-read-dtc','manufacturer-read-coding')
            or not isinstance(request['expectedVehicleIdentitySha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', request['expectedVehicleIdentitySha256'])):
        raise ValueError('manufacturer-vehicle-binding-invalid')
    if request.get('action') not in ('manufacturer-read', 'manufacturer-identify','manufacturer-read-dtc','manufacturer-read-coding'):
        raise ValueError('manufacturer-action-invalid')
    pid = request.get('profileId')
    route = READ_PROFILES.get(pid) if isinstance(pid, str) else None
    if request['action'] == 'manufacturer-read-dtc' and isinstance(pid, str) and pid not in FAULT_READ_PROFILES:
        raise ValueError('manufacturer-dtc-source-unqualified')
    if (request.get('generation') not in read_profile_generations(pid) or type(request.get('ecuId')) is not int
            or route is None or request['ecuId'] not in read_profile_targets(pid)):
        raise ValueError('manufacturer-profile-unqualified')
    if 'sourceBound' in request and (request['sourceBound'] is not True or request['ecuId'] not in (1,9)):
        raise ValueError('manufacturer-profile-unqualified')
    if request.get('action') in ('manufacturer-read-dtc','manufacturer-read-coding') and request['ecuId'] in (1,9) and request.get('sourceBound') is not True:
        raise ValueError('manufacturer-profile-unqualified')
    if request['generation']=='982' and pid==GATEWAY_PROFILE and request.get('sourceBound') is not True:
        raise ValueError('manufacturer-profile-unqualified')
    ids = request.get('parameterIds')
    if (not isinstance(ids, list) or (ids != [] if identifying else not 1 <= len(ids) <= 12)
            or any(not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key) for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('manufacturer-selection-invalid')
    if request.get('confirmedReadOnly') is not True or request.get('x431Inactive') is not True:
        raise ValueError('manufacturer-read-preconditions-required')
    from .connection import valid_device_id
    device = request.get('deviceId')
    if not valid_device_id(device):
        raise ValueError('device-id-invalid')
    from .connection import validate_serial_baud
    validate_serial_baud(device, request.get('serialBaud'))
    if device.startswith('pt3g:'):
        raise ValueError('pt3g-vehicle-transport-not-qualified')
    from .connection import ELM_MODELS
    if 'adapterModel' in request and (not isinstance(request['adapterModel'], str) or request['adapterModel'] not in ELM_MODELS | {'VNCI', 'PT3G'}):
        raise ValueError('adapter-model-invalid')
    cycles = request.get('cycleCount', 1)
    if identifying and (cycles != 1 or request['profileId'] != next(pid for pid in READ_PROFILES if request['ecuId'] in read_profile_targets(pid) and request['generation'] in read_profile_generations(pid))):
        raise ValueError('manufacturer-identify-request-invalid')
    # None denotes continuous reads, always bounded by the session's ten minutes.
    if cycles is not None and (type(cycles) is not int or not 1 <= cycles <= 1000):
        raise ValueError('manufacturer-cycles-invalid')
    budget = request.get('budgetSeconds', 30)
    if type(budget) not in (int, float) or not 0 < budget <= 60:
        raise ValueError('manufacturer-budget-invalid')


def _vehicle_request(request):
    # The Gateway source route is shared. 981 is only the probe's source
    # catalogue context; the actual vehicle generation comes from its fresh VIN.
    nominal = next(pid for pid in READ_PROFILES if 9 in read_profile_targets(pid))
    return {**request, 'action': 'manufacturer-identify', 'generation': '981', 'ecuId': 9,
            'profileId': nominal, 'parameterIds': [], 'sourceBound': True, 'cycleCount': 1}


def read_manufacturer(request, *, root=None, cancel_event=None, on_cycle=None, on_connection=None, on_frame=None, on_pdu=None, on_coding_anchor=None, check_only=False, wait_retry=None):
    """Production chooses the physical factory; caller cannot inject byte traffic."""
    out = _empty_result(SOURCE)
    out['simulation'] = False
    open_attempted = False
    batch_deadline=time.monotonic()+10*60
    try:
        _validate(request)
        vehicle_identifying = request['action'] == 'manufacturer-identify-vehicle'
        if vehicle_identifying:
            request = _vehicle_request(request)
        profile = verified_profile(request, root)
        identifying = request['action'] in ('manufacturer-identify','manufacturer-read-dtc','manufacturer-read-coding')
        candidates = [verified_profile({**request, 'profileId': pid}, root)
                      for pid in (FAULT_READ_PROFILES if request['action']=='manufacturer-read-dtc' or vehicle_identifying else READ_PROFILES)
                      if request['ecuId'] in read_profile_targets(pid) and request['generation'] in read_profile_generations(pid)
                      and (request['ecuId'] not in (1,9) or request.get('sourceBound') is True
                           or pid==request['profileId'])] if identifying else None
        device = request['deviceId']
        transport_kind = 'd-pdu-iso-tp' if device.startswith('vnci:') else 'elm-iso-tp'
        out['transport']['kind'] = transport_kind
        initial_contract=prepared_read_contract(profile,request['parameterIds'],
            transport_kind=transport_kind,identity_only=identifying) if request['ecuId'] in (88,76,78,30,31) or check_only else {}
        if check_only:
            if request['action']=='manufacturer-read-coding':
                from .manufacturer_coding import source_coding_plan
                out['codingReadPlan']=source_coding_plan(profile,initial_contract)
            out.update(ok=True, checkOnly=True, readContract=initial_contract)
            return out
        vehicle_anchor = None
        if request['action'] in ('manufacturer-read-dtc','manufacturer-read-coding'):
            # Some source-defined units (camera, sound, radar, emergency call)
            # do not publish a VIN. Never invent a VIN request for those units.
            # Qualify the gateway on this same owned head before opening them.
            # A complete coding read may contain many individually bounded
            # requests. Its default total budget follows the existing desktop
            # task deadline; explicit shorter budgets still constrain it.
            batch_deadline=min(batch_deadline,time.monotonic()+request.get('budgetSeconds',
                600 if request['action']=='manufacturer-read-coding' else 30))
            needs_anchor=any(not any(item.get('requestHex') in ('1A90','22F190') and item.get('decoderReady')
                for item in candidate.get('identity',[])) for candidate in candidates)
            if needs_anchor:
                remaining=batch_deadline-time.monotonic()
                if remaining<=0:raise ElmError('deadline-expired')
                anchor_request={key:request[key] for key in ('deviceId','adapterModel','serialBaud','confirmedReadOnly','x431Inactive') if key in request}
                anchor_request.update(action='manufacturer-identify-vehicle',budgetSeconds=
                    min(request.get('budgetSeconds',30),remaining))
                vehicle_anchor=read_manufacturer(anchor_request,root=root,cancel_event=cancel_event,
                    on_cycle=on_cycle,on_frame=on_frame,on_pdu=on_pdu)
                open_attempted=vehicle_anchor['transport'].get('openAttempted',False)
                if (not vehicle_anchor.get('ok') or not vehicle_anchor['transport'].get('closed')
                        or vehicle_anchor['transport'].get('closeErrors')):
                    out=vehicle_anchor;out.update(ok=False,error=out.get('error') or 'manufacturer-close-failed');return out
                if (vehicle_anchor.get('vehicleGeneration')!=request['generation']
                        or request.get('expectedVehicleIdentitySha256') and request['expectedVehicleIdentitySha256']!=vehicle_anchor.get('vehicleIdentitySha256')):
                    out=vehicle_anchor;out.update(ok=False,error='vehicle-identity-mismatch');return out
                # Separate gateway observations from the target's identical DID
                # numbers only after the owned gateway session has closed.
                if request['action']=='manufacturer-read-coding' and on_coding_anchor:
                    on_coding_anchor(vehicle_anchor)
        def factory(cancel):
            nonlocal open_attempted
            # An attempted constructor/open can acquire resources before it
            # raises. Only failures before this boundary prove none were opened.
            open_attempted = True
            if transport_kind == 'd-pdu-iso-tp':
                from .vnci import DpuClient
                from .mode50_vnci import Mode50DpuClient
                from .mode30_clients import Mode30DpuClient
                client_type={'porsche-mode50':Mode50DpuClient,'porsche-tp20':Mode30DpuClient}.get(initial_contract.get('transportAddressing',{}).get('kind'),DpuClient)
                return client_type(device, cancel_event=cancel, pdu_sink=on_pdu)
            from .connection import open_selected_port
            port, _com = open_selected_port(device, adapter_model=request.get('adapterModel'), serial_baud=request.get('serialBaud'))
            from .mode50_transport import Mode50ElmClient
            from .mode30_clients import Mode30ElmClient
            client_type={'porsche-mode50':Mode50ElmClient,'porsche-tp20':Mode30ElmClient}.get(initial_contract.get('transportAddressing',{}).get('kind'),ElmClient)
            return client_type(port, cancel_event=cancel, frame_sink=on_frame, pdu_sink=on_pdu,
                expected_model=request.get('adapterModel'))
        route_discovery = None
        probe_routes = []
        if request['ecuId']==87:
            # Two exact source links exist. Probe identities only, require one
            # matching link, then open it afresh before publishing or reading.
            # A driver/adapter/close fault aborts; this is not head reconnection.
            all_candidates = candidates or [verified_profile({**request,
                'action':'manufacturer-identify','profileId':pid,'parameterIds':[]},root)
                for pid in READ_PROFILES if 87 in read_profile_targets(pid)]
            probe_routes=[({'sourceLink':link},[{**p,'selectedSourceLink':link} for p in all_candidates])
                          for link in (0,1)]
        elif identifying and request['ecuId']==1:
            from .pdk_read import DME_VERSIONS
            from .uds_source_read import DME_SOURCE_UDS_VERSIONS
            families=[({'applicationProtocol':protocol},options) for protocol,options in (
                ('kwp',[p for p in candidates if p['profileId'] in DME_VERSIONS]),
                ('uds',[p for p in candidates if p['profileId'] in DME_SOURCE_UDS_VERSIONS])) if options]
            # Keep the existing one-family path. Shared source states can
            # add a second family; qualify each independently, never merge its
            # identity layout with the other protocol or prefer a first hit.
            if len(families)>1:probe_routes=families
        elif identifying and request['ecuId'] in (65,70,75):
            from .uds_source_read import MEDIA_VERSIONS, BOSE_PROFILE
            # These source families share a CAN pair but declare different
            # session/protocol recipes. Discover with identities only, never
            # combine their selectors or borrow measurements across families.
            # The independently qualified PIWIS BOSE recipe enters the extended
            # session before reading its selectors. Probe it separately; the
            # candidate guard must still reject mixed command plans.
            probe_routes=[(tag,options) for tag,options in (
                ({'applicationProtocol':'kwp'},[p for p in candidates if p['profileId'] not in MEDIA_VERSIONS]),
                ({'applicationProtocol':'uds'},[p for p in candidates if p['profileId'] in MEDIA_VERSIONS and p['profileId'] != BOSE_PROFILE]),
                ({'applicationProtocol':'uds','sourceBridge':'piwis-bose002'},[p for p in candidates if p['profileId'] == BOSE_PROFILE])) if options]
        elif identifying and request['ecuId'] == 47 and request['action'] == 'manufacturer-read-dtc':
            from .swsg_source import VERSIONS as SWSG_VERSIONS
            from .headlamp_source import VERSION_RULES as LED_VERSIONS
            # Both source families use 725/78F, but their selector/identity
            # formats are different. Keep their complete contracts separate.
            probe_routes=[(tag, options) for tag, options in (
                ({'sourceBridge':'piwis-swsg-normal'},[p for p in candidates if p['profileId'] in SWSG_VERSIONS]),
                ({'sourceBridge':'piwis-led-identity'},[p for p in candidates if p['profileId'] in LED_VERSIONS])) if options]
        if probe_routes:
            route_deadline=time.monotonic()+request.get('budgetSeconds',30)
            matches=[];probes=[]
            for tag,options in probe_routes:
                remaining=route_deadline-time.monotonic()
                if remaining<=0:raise ElmError('deadline-expired')
                probe=_client_cycles(options[0],[],factory,physical=True,source=SOURCE,cycles=1,
                    budget_s=remaining,cancel_event=cancel_event,streaming=True,
                    transport_kind=transport_kind,identify_profiles=options,batch_deadline=batch_deadline)
                probes.append({**tag,'ok':probe['ok'],'error':probe.get('error')})
                if not probe['transport']['closed'] or any(not str(e).startswith('poisoned:') for e in probe['transport']['closeErrors']):
                    out=probe;return out
                if probe['ok']:
                    if probe.get('identifiedProfileId') not in {p['profileId'] for p in options}:
                        out=probe;out.update(ok=False,error='identity-mismatch');return out
                    matches.append((tag,options,probe['identifiedProfileId']))
                    if tag.get('sourceBridge') == 'piwis-swsg-normal':
                        # Source rules are disjoint: normal SWSG's six-byte
                        # DSNs never start with the LED rules' three-byte major
                        # version. A complete normal identity therefore needs
                        # no LED-specific F19E/F1DF probe on that ECU.
                        from .swsg_source import VERSIONS as normal_versions
                        from .headlamp_source import VERSION_RULES as led_rules
                        led_majors={r.split('#')[1] for rules in led_rules.values() for r in rules}
                        if any(dsn[:3] in led_majors for dsn in normal_versions.values()):
                            raise ElmError('manufacturer-identify-route-conflict')
                        break
                elif probe.get('error') not in ('NO DATA','no-data','negative-response',
                        'identity-mismatch','identity-decode-failed','wrong-sid-or-did-echo'):
                    out=probe;return out
            if len(matches)!=1:
                out=probe;out.update(ok=False,error='manufacturer-source-route-ambiguous' if matches else 'manufacturer-source-route-unidentified')
                return out
            tag,options,actual_profile=matches[0]
            if not identifying and actual_profile!=request['profileId']:
                out=probe;out.update(ok=False,error='identity-mismatch');return out
            profile=next(p for p in options if p['profileId']==actual_profile)
            # Normal reads keep the requested selection; identity probes loaded
            # the same immutable records without selecting any measurements.
            candidates=options if identifying else None
            remaining=route_deadline-time.monotonic()
            if remaining<=0:raise ElmError('deadline-expired')
            route_discovery={**({'selectedSourceLink':tag['sourceLink']} if 'sourceLink' in tag else tag),
                             'probes':probes,'uniqueMatch':True}
        qualification_budget=(request.get('budgetSeconds',30) if request['action']=='manufacturer-read-coding'
            else remaining if route_discovery else request.get('budgetSeconds',30))
        out = _client_cycles(profile, request['parameterIds'], factory, physical=True, source=SOURCE,
            cycles=request.get('cycleCount', 1), budget_s=min(qualification_budget,batch_deadline-time.monotonic()),
            interval_s=1 if request.get('cycleCount', 1) is None or request.get('cycleCount', 1) > 1 else 0,
            cancel_event=cancel_event, on_cycle=on_cycle, on_connection=on_connection,
            streaming=True, transport_kind=transport_kind, wait_retry=wait_retry, identify_profiles=candidates,
            batch_deadline=batch_deadline, read_dtcs=request['action']=='manufacturer-read-dtc',
            read_coding=request['action']=='manufacturer-read-coding',
            require_complete_coding=request.get('completeOriginalRequired', False),
            expected_vehicle_identity_sha256=request.get('expectedVehicleIdentitySha256'),vehicle_anchor_result=vehicle_anchor,
            vehicle_probe=vehicle_identifying)
        out['deviceId'] = device
        if vehicle_identifying and out['ok'] and out.get('vehicleGeneration') not in ('981', '982'):
            out.update(ok=False, error='vehicle-model-unidentified')
        if route_discovery:out['sourceRouteDiscovery']=route_discovery
    except (ValueError, KeyError, TypeError, OSError, ElmError, ImportError) as error:
        out.update(ok=False, error=str(error))
        if isinstance(error, _SourceFormatError):
            out['sourceErrorDetail'] = error.source_detail
    finally:
        out['transport']['openAttempted'] = open_attempted
        out.setdefault('requestCount', len(out.get('transactions', [])))
    return out


def _redact(value):
    if isinstance(value, str): return redact_vin(value)
    if isinstance(value, list): return [_redact(item) for item in value]
    if isinstance(value, dict): return {key: _redact(item) for key, item in value.items()}
    return value



def run(source, sink, *, check_only=False):
    cancel = threading.Event()
    retry_control = ManualReadRetry(cancel)
    dispatch_started = False
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
                while not cancel.is_set():
                    line = source.readline(256)
                    try: control = json.loads(line) if line.endswith('\n') else None
                    except (ValueError, TypeError): control = None
                    if (isinstance(control, dict) and set(control) == {'action', 'episode', 'attempt'}
                            and control['action'] == 'retry'):
                        retry_control.retry(control['episode'], control['attempt'])
                        continue
                    # Stop, EOF, malformed and arbitrary new requests end this batch.
                    cancel.set()
            threading.Thread(target=controls, daemon=True).start()
        dispatch_started = True
        result = read_manufacturer(request, check_only=check_only, cancel_event=cancel, wait_retry=retry_control.wait,
            on_coding_anchor=lambda value: emit('coding-vehicle-anchor', value),
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
        # An exception after dispatch (including output failure) cannot prove
        # that the adapter factory was never entered.
        result['transport']['openAttempted'] = dispatch_started
        result['requestCount'] = 0
        emit('finished', result)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='verify typed selection and bundle; never open hardware')
    args = parser.parse_args()
    return run(sys.stdin, sys.stdout, check_only=args.check_only)


if __name__ == '__main__':
    raise SystemExit(main())
