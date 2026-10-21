"""Shared manufacturer read session and isolated historical/synthetic entries.

Uses the production ELM framing/parser and catalog session/identity qualification.
The public entry rejects live before creating a port. Historical PDUs are private
test fixtures; neither simulated identities nor this rehearsal qualify live reads.
"""
from __future__ import annotations

import threading
import time
import json
import hashlib
import math
import itertools
import re
from collections import deque
from datetime import datetime, timezone

from .catalog import load_catalog, operation, profile_by_id
from .decode import decode_payload
from .elm import ElmClient, ElmError, parameter_unavailable_pdu
from .manufacturer_acquisition import DME_PROFILE, FAULT_ONLY_PROFILES, matched_read_profile, _decode_cycle, historical_fixtures
from .qualification import qualify_identity
from .realtime_preparation import FLAGS, plan_for, record_from_parameter, display_name
from .response_values import request_from_record, data_span, decode_application_response
from .session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from .sessions import IDENTITY_FIELDS, SESSION_BY_PROFILE

CATALOG_PROFILE = 'porsche-981-2014-dme'


def identity_details(contract, identity):
    """Display source-named fields from the identity replies already qualified.

    This has no transport, cached vehicle values or guessed DID/name mapping.
    Grouped records retain their individual names and source order.
    """
    if contract.get('sourceIdentityKind') == 'piwis-led-identity':
        from .headlamp_source import decode_identity
        decoded = decode_identity(contract['profileId'], identity)
        fields = (('ASAM 标识', 'asamIdentifier', '22F19E', 0),
                  ('诊断主版本', 'diagnosticMajorVersion', '22F1A2', 0),
                  ('诊断次版本', 'diagnosticMinorVersion', '22F1A2', 1),
                  ('零件号', 'sparePartNumber', '22F187', 0),
                  ('软件版本', 'softwareVersion', '22F189', 0),
                  ('硬件号', 'hardwareNumber', '22F191', 0))
        return [dict(label=label, value=decoded[key], requestHex=request, recordIndex=index,
            pduSha256=hashlib.sha256(bytes.fromhex(identity[request])).hexdigest())
            for label, key, request, index in fields]
    rows = []
    for item in contract['identityRequests']:
        hx = item['requestHex']; pdu = identity.get(hx)
        if not isinstance(pdu, str): continue
        data = bytes.fromhex(pdu)
        for index, record in enumerate(item.get('records') or [item['record']]):
            label, resolved = display_name(record)
            if not resolved: continue
            decoded = decode_application_response(record, data, mode='pdu')
            if not decoded.get('ok'): continue
            value = decoded.get('text')
            if value is None: value = decoded.get('value')
            if not isinstance(value, (str, int, float, bool)): continue
            rows.append(dict(label=label, value=str(value), requestHex=hx, recordIndex=index,
                pduSha256=hashlib.sha256(data).hexdigest()))
    return rows


def vehicle_generation_from_vin(vin):
    """Porsche PPK 16 D8 (2024-08-08), type digits at positions 7/8/12.

    NA uses A81/A82; rest-of-world retains 981/982. Both must agree
    with the documented model-year range. No inference from shared 98 alone.
    Source: https://static.nhtsa.gov/odi/tsbs/2024/MC-11006858-0001.pdf
    """
    if not isinstance(vin, str) or not re.fullmatch(r'WP0[A-HJ-NPR-Z0-9]{14}', vin):
        return None
    model = vin[6:8] + vin[11]
    year = vin[9]
    if model in ('981', 'A81') and year in 'DEFG':
        return '981'
    if model in ('982', 'A82') and year in 'HJKLMNPRSTVW':
        return '982'
    return None


def _identity_vin(contract, identity):
    vin = identity.get('vin')
    for item in contract['identityRequests']:
        hx = item['requestHex']
        if hx in ('1A90','22F190') and hx in identity:
            decoded = decode_application_response(item['record'], bytes.fromhex(identity[hx]), mode='pdu')
            vin = decoded.get('text') if decoded.get('ok') else None
    return vin


def prepared_read_contract(profile, selected, catalog=None, *, transport_kind='elm-iso-tp', identity_only=False):
    """Server-owned request/identity contract; does not grant hardware access."""
    if transport_kind not in ('elm-iso-tp', 'd-pdu-iso-tp'):
        raise ValueError('manufacturer-transport-unqualified')
    ecu_id, catalog_id, _generation = matched_read_profile(profile)
    if profile.get('profileId') in FAULT_ONLY_PROFILES and not identity_only:
        raise ValueError('manufacturer-parameters-unavailable')
    if identity_only and selected != []:
        raise ValueError('manufacturer-identify-selection-forbidden')
    plan = {'groups': [], 'blocked': []} if identity_only else plan_for(profile, selected)
    if plan['blocked']:
        if any(row['reason'] == 'parameter-record-span-mismatch' for row in plan['blocked']):
            raise ValueError('manufacturer-field-span-mismatch')
        raise ValueError('manufacturer-definition-incomplete')
    params = {p['id']: p for p in profile['parameters']}
    for key in selected:
        parameter = params[key]
        record = parameter.get('record') or record_from_parameter(parameter)
        parsed = request_from_record(record)
        if (not parsed.get('ok') or parsed['sid'] not in (0x21, 0x22)
                or parsed['request'].hex().upper() != parameter['requestHex']):
            raise ValueError('manufacturer-request-unqualified')
        span = data_span(record)
        if not span or span != parameter.get('dataSpan'):
            raise ValueError('manufacturer-field-span-mismatch')
    if catalog_id == 'porsche-981-tp20-source':
        from .mode30_source import source_contract
        route_fields = source_contract(profile, selected)
    elif catalog_id == 'porsche-981-uds-source':
        from .uds_source_read import source_contract
        # Fault-only source states may compare the same DSN plus status fields.
        # Use every original selector for discovery on that shared route; the
        # normal continuous-parameter contract remains unchanged.
        full_selectors = identity_only and any(spec[0] == ecu_id for spec in FAULT_ONLY_PROFILES.values())
        route_fields = source_contract(profile, selected, identity_only=True) if full_selectors else source_contract(profile, selected)
    elif catalog_id in ('porsche-981-pdk-source','porsche-981-selector-source','porsche-981-kwp-source'):
        from .pdk_read import source_contract
        route_fields = source_contract(profile, selected)
    else:
        named = profile_by_id(catalog or load_catalog(), catalog_id)
        hx, positive = SESSION_BY_PROFILE[catalog_id]
        identity = [dict(field=field, **operation(named, oid))
                    for field, oid in IDENTITY_FIELDS[catalog_id]]
        route_fields = {'catalogProfileId': catalog_id, 'ecuId': ecu_id,
            'txId': int(named['txId'], 16), 'rxId': int(named['rxId'], 16),
            'session': {'requestHex': hx, 'positivePrefixHex': positive},
            'identityRequests': identity, 'identityConstraints': named['identityConstraints']}
    contract = {'schemaVersion': 1, 'kind': 'prepared-manufacturer-read',
        'profileId': profile['profileId'], 'generation': profile.get('requestedGeneration','981'), **route_fields,
        'groups': plan['groups'], 'selectionLimit': 12, 'intervalClaim': None,
        'transportKind': transport_kind, 'rawCanFramesAvailable': transport_kind == 'elm-iso-tp',
        'reconnectAttemptsPerInterruption': None, 'manualRetryRequired': True,
        'qualificationRequired': ['independent-adapter/session', 'fresh-complete-identity',
                                  'request-response-and-field-agreement', 'observed-cadence'],
        'executionEnabled': False, 'liveVerified': False, 'writePayload': None}
    contract['sha256'] = hashlib.sha256(json.dumps(contract, sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
    return contract


def transport_rehearsal(profile, selected, groups, *, mode='simulation',
                        port_factory=None, cancel_event=None, budget_s=30, cycles=1,
                        on_cycle=None, on_connection=None, streaming=False):
    # Resolve and validate historical references before opening any port.
    if mode != 'simulation':
        return _transport_cycle(profile, selected, None, mode=mode)
    try:
        fixtures = historical_fixtures(profile, selected, groups)
    except (ValueError, KeyError, TypeError) as error:
        result = _empty_result()
        result['error'] = str(error)
        return result
    def factory():
        _ecu, catalog_id, _generation = matched_read_profile(profile)
        port = SessionSimPort(catalog_id)
        rx_id = int(profile_by_id(load_catalog(), catalog_id)['rxId'], 16)
        for hx, pdu in fixtures.items():
            port.ecu_map[hx] = ath1_prompt(isotp_ath1_lines(rx_id, pdu))
        return port
    return _transport_cycle(profile, selected, port_factory or factory,
                            cancel_event=cancel_event, budget_s=budget_s, cycles=cycles,
                            interval_s=1 if type(cycles) is int and cycles > 1 else 0,
                            on_cycle=on_cycle, on_connection=on_connection, streaming=streaming)


def synthetic_transport_cycles(profile, selected, *, port_factory, cycles=1,
                               cancel_event=None, budget_s=30, mode='simulation'):
    """Exercise prepared reads against a test-owned changing ECU, never live.

    No historical response or example is required. The caller supplies an
    explicitly synthetic BytePort; this is not exposed as a desktop live route.
    """
    return _transport_cycle(profile, selected, port_factory, cycles=cycles, mode=mode,
                            source='synthetic-ecu', cancel_event=cancel_event, budget_s=budget_s)


def _empty_result(source='historical-fixture-rehearsal'):
    out = {'ok': False, 'error': None, 'sourceKind': 'historical-fixture-rehearsal',
           'simulation': True, 'vehicleDataCollected': False, 'liveVerified': False,
           **FLAGS,
           'samples': [], 'transactions': [], 'unavailableParameters': [], 'invalidParameters': [], 'completedCycles': 0,
           'connectionEvents': [], 'connectionEventCount': 0,
           'transport': {'kind': 'injected-byte-port', 'qualification': None,
                         'recovery': [], 'closed': False, 'closeErrors': [], 'rawLog': []}}
    out['sourceKind'] = source
    return out


def _transport_cycle(profile, selected, factory, *, mode='simulation', cycles=1,
                     source='historical-fixture-rehearsal', cancel_event=None, budget_s=30,
                     interval_s=0, on_cycle=None, on_connection=None, streaming=False):
    if mode != 'simulation':
        out = _empty_result(source)
        out['error'] = 'manufacturer-transport-unqualified'
        return out
    return _client_cycles(profile, selected, lambda cancel: ElmClient(factory(), cancel_event=cancel),
        cycles=cycles, source=source, cancel_event=cancel_event, budget_s=budget_s,
        interval_s=interval_s, on_cycle=on_cycle, on_connection=on_connection, streaming=streaming)


def _client_cycles(profile, selected, client_factory, *, cycles=1,
                   source='synthetic-ecu', physical=False, cancel_event=None, budget_s=30,
                   interval_s=0, on_cycle=None, on_connection=None, streaming=False, transport_kind='elm-iso-tp', wait_retry=None,
                   identify_profiles=None, batch_deadline=None, read_dtcs=False, expected_vehicle_identity_sha256=None, vehicle_anchor_result=None,
                   vehicle_probe=False, read_coding=False, require_complete_coding=False):
    """Private engine. Physical entry must verify definitions and select its own factory."""
    out = _empty_result(source)
    out['simulation'] = not physical
    if physical:
        out['transport']['kind'] = transport_kind
    cancel = cancel_event or threading.Event()
    client = None
    connection_epoch = 0
    batch_expired = False
    try:
        if isinstance(budget_s, bool) or not math.isfinite(float(budget_s)) or not 0 < float(budget_s) <= 60:
            raise ValueError('manufacturer-budget-invalid')
        if ((cycles is None and not streaming) or (cycles is not None
                and (type(cycles) is not int or cycles < 1 or (not streaming and cycles > 1000)))):
            raise ValueError('manufacturer-cycles-invalid')
        if isinstance(interval_s, bool) or not math.isfinite(float(interval_s)) or not 0 <= float(interval_s) <= 60:
            raise ValueError('manufacturer-interval-invalid')
        interval_s = float(interval_s)
        deadline = time.monotonic() + float(budget_s)
        session_deadline = time.monotonic() + 10 * 60 if streaming else deadline
        if batch_deadline is not None:
            if type(batch_deadline) not in (int,float) or not math.isfinite(batch_deadline):
                raise ValueError('manufacturer-budget-invalid')
            session_deadline=min(session_deadline,batch_deadline)
            deadline=min(deadline,session_deadline)
        catalog = load_catalog()
        identifying = identify_profiles is not None
        if type(read_coding) is not bool or read_coding and read_dtcs:
            raise ValueError('manufacturer-coding-selection-forbidden')
        if type(require_complete_coding) is not bool or require_complete_coding and not read_coding:
            raise ValueError('manufacturer-coding-selection-forbidden')
        source_read = read_dtcs or read_coding
        if source_read and (not identifying or selected != [] or cycles != 1):
            raise ValueError('manufacturer-dtc-selection-forbidden')
        if expected_vehicle_identity_sha256 is not None and (not source_read
                or not isinstance(expected_vehicle_identity_sha256,str)
                or not re.fullmatch('[a-f0-9]{64}',expected_vehicle_identity_sha256)):
            raise ValueError('manufacturer-vehicle-binding-invalid')
        contract = prepared_read_contract(profile, selected, catalog, transport_kind=transport_kind, identity_only=identifying)
        if type(vehicle_probe) is not bool or vehicle_probe and (not identifying or source_read or contract['ecuId']!=9):
            raise ValueError('manufacturer-vehicle-probe-unqualified')
        candidates = []
        if identifying:
            if selected != [] or cycles != 1 or not identify_profiles:
                raise ValueError('manufacturer-identify-selection-forbidden')
            for candidate in identify_profiles:
                option = prepared_read_contract(candidate, [], catalog, transport_kind=transport_kind, identity_only=True)
                from .pdk_read import SOURCE_CATALOG
                # The original KWP sequence resolves the two-byte DSN before
                # reading the chosen state's remaining identity fields.
                dsn_layout = all(c.get('identityLayoutResolvedByDsn') is True
                    and c.get('sourceCatalogId') == SOURCE_CATALOG
                    and c.get('sourceQualification') in ('pdk-dsn-and-full-identity','kwp-source-version-and-identity')
                    and c.get('session',{}).get('requestHex') == '1089'
                    and re.fullmatch('[0-9A-F]{4}',c.get('identityConstraints',{}).get('dsnHex',''))
                    and bool(c.get('identityRequests'))
                    and c.get('identityRequests',[{}])[0].get('requestHex') == '1A9F'
                    for c in (contract,option))
                if (option['ecuId'] != contract['ecuId'] or any(option.get(k) != contract.get(k) for k in
                        ('generation','txId', 'rxId', 'session', 'applicationProtocol', 'sourceCommands'))
                        or bool(option.get('versionSelectors'))!=bool(contract.get('versionSelectors'))
                        or option.get('versionSelectors') and any(s['requestHex'] not in option.get('sourceCommands',[]) for s in option['versionSelectors'])
                        or not contract.get('versionSelectors') and not dsn_layout and [r['requestHex'] for r in option['identityRequests']] != [r['requestHex'] for r in contract['identityRequests']]):
                    raise ValueError('manufacturer-identify-route-conflict')
                candidates.append((candidate, option))
        out['readContract'] = contract
        anchor_hash=None
        if vehicle_anchor_result is not None:
            anchor=vehicle_anchor_result;qualified=anchor.get('transport',{}).get('qualification',{})
            if (not source_read or anchor.get('ok') is not True or anchor.get('identityOnly') is not True
                    or anchor.get('executionEnabled') is not True or anchor.get('vehicleDataCollected') is not False
                    or anchor.get('vehicleGeneration')!=contract['generation']
                    or not re.fullmatch('[a-f0-9]{64}',anchor.get('vehicleIdentitySha256',''))
                    or anchor.get('readContract',{}).get('ecuId')!=9
                    or qualified.get('ecuId')!=9 or qualified.get('sourceCatalogId')!=contract.get('sourceCatalogId')
                    or qualified.get('freshRead') is not True or qualified.get('observedProfileMatch') is not True
                    or qualified.get('variantLink',{}).get('profile_id')!=anchor.get('identifiedProfileId')
                    or anchor.get('transport',{}).get('closed') is not True or anchor['transport'].get('closeErrors')!=[]):
                raise ElmError('manufacturer-vehicle-anchor-unqualified')
            anchor_hash=anchor['vehicleIdentitySha256']
            out['vehicleAnchor']=dict(ecuId=9,profileId=anchor['identifiedProfileId'],sourceCatalogId=qualified['sourceCatalogId'],
                vehicleGeneration=anchor['vehicleGeneration'],vehicleIdentitySha256=anchor_hash,freshRead=True,
                observedProfileMatch=True,closed=True)
        authorized = frozenset(g['requestHex'] for g in contract['groups'])
        named = profile_by_id(catalog, contract['catalogProfileId']) if contract['catalogProfileId'] in SESSION_BY_PROFILE else None
        def guard():
            if cancel.is_set(): raise ElmError('cancelled')
            if time.monotonic() >= deadline: raise ElmError('deadline-expired')
        def close():
            if client is None: return
            result = client.close_restore()
            errors = result.get('errors', [])
            out['transport']['closeErrors'].extend(errors)
            if not getattr(client, '_prepared_log_saved', False):
                out['transport']['rawLog'].extend(client.raw_log)
                client._prepared_log_saved = True
                if not streaming and len(json.dumps(out['transport']['rawLog']).encode()) > 1024 * 1024:
                    raise ElmError('manufacturer-transport-log-limit')
            if (any(str(e).startswith('close:') for e in errors)
                    or (transport_kind == 'd-pdu-iso-tp' and errors)):
                raise ElmError('recovery-close-failed')
            out['transport']['closed'] = True
            if any(not str(e).startswith('poisoned:') for e in errors):
                raise ElmError('manufacturer-close-failed')
        def open_qualified(expected=None):
            nonlocal client, connection_epoch
            guard()
            client = client_factory(cancel)
            # Actual RX CAN/PDU recording has its independent explicit sink.
            # Do not accumulate a duplicate raw byte log for ordinary live values.
            if streaming and physical and on_cycle is not None:
                client.raw_log = deque(maxlen=0)
            out['executionEnabled'] = physical
            out['transport']['closed'] = False
            if source == 'synthetic-ecu' and getattr(client.port, 'trafficKind', None) != 'synthetic-session':
                # Close without AT/ECU traffic when a test supplied the wrong port.
                client._poisoned = True
                raise ElmError('synthetic-port-required')
            adapter = client.validate_adapter(min(10, max(0, deadline - time.monotonic())))
            out['transport']['adapter'] = adapter
            if transport_kind == 'd-pdu-iso-tp' or contract.get('transportAddressing',{}).get('kind')=='porsche-tp20':
                client.configure_manufacturer(profile, selected, deadline, identity_only=identifying)
            else:
                client.configure_pair(f"{contract['txId']:X}", f"{contract['rxId']:X}", deadline)
            if named is None:
                if contract.get('sourceIdentityKind') == 'piwis-led-identity':
                    from .headlamp_source import qualify_client
                elif not contract.get('versionSelectors') and contract.get('applicationProtocol') != 'uds':
                    from .pdk_read import qualify_client
                else:
                    from .uds_source_read import qualify_client
                def resolve_version(value):
                    nonlocal profile, contract
                    key = 'dsnHex' if contract['ecuId'] in (2,68,70,75,65) else 'dsnText'
                    from .uds_source_read import version_matches
                    matches = [(p,c) for p,c in candidates if (version_matches(c,value) if c.get('versionSelectors') else c['identityConstraints'].get(key) == value)]
                    if len(matches) != 1:
                        raise ElmError('identity-mismatch')
                    profile, contract = matches[0]
                    if transport_kind == 'd-pdu-iso-tp':
                        client.bind_manufacturer_identity(profile, deadline)
                    out['readContract'] = contract
                    return contract, profile['profileId']
                def resolve_source_responses(responses):
                    nonlocal profile, contract
                    from .uds_source_read import resolve_version_responses
                    resolved=resolve_version_responses(candidates,responses)
                    if resolved is None:return None
                    profile,contract=resolved
                    if transport_kind == 'd-pdu-iso-tp':
                        client.bind_manufacturer_identity(profile,deadline)
                    out['readContract']=contract
                    return contract,profile['profileId']
                options = ({'resolve_source_responses': resolve_source_responses}
                    if contract.get('versionSelectors') else {'resolve_version': resolve_version}) if identifying else {}
                identity, qualified = qualify_client(client, contract, profile['profileId'], deadline, guard, **options)
                if not vehicle_probe:
                    vin = _identity_vin(contract, identity)
                    if vin is not None and vehicle_generation_from_vin(vin) != contract['generation']:
                        raise ElmError('vehicle-identity-mismatch')
                if expected is not None and identity != expected:
                    raise ElmError('identity-mismatch')
                out['transport']['qualification'] = qualified
                connection_epoch += 1
                return identity
            hx, positive = SESSION_BY_PROFILE[contract['catalogProfileId']]
            reply = client.request(hx, min(deadline, time.monotonic() + 15))
            if not reply.get('ok') or not (reply.get('payload_hex') or '').startswith(positive):
                raise ElmError(reply.get('error') or 'session-subfunction-mismatch')
            identity = {}
            for field, oid in IDENTITY_FIELDS[contract['catalogProfileId']]:
                guard()
                op = operation(named, oid)
                response = client.request(op['requestHex'], min(deadline, time.monotonic() + 15))
                if not response.get('ok') or not (response.get('payload_hex') or '').startswith(op['positivePrefixHex']):
                    raise ElmError(response.get('error') or 'identity-request-failed')
                decoded = decode_payload(response['payload_hex'], op['decode'])
                if not decoded.get('ok'): raise ElmError('identity-decode-failed')
                identity[field] = decoded.get('text')
            qualified = qualify_identity({'generation': '981', 'ecuId': contract['ecuId'], 'identity': identity}, catalog,
                                         expected_generation='981')
            if (not qualified.get('observedProfileMatch')
                    or (qualified.get('variantLink') or {}).get('profile_id') != profile['profileId']
                    or (expected is not None and identity != expected)):
                raise ElmError('identity-mismatch')
            out['transport']['qualification'] = qualified
            connection_epoch += 1
            return identity
        identity = open_qualified()
        if identifying:
            out['transport']['qualification'] = {**out['transport']['qualification'], 'freshRead': True}
            if source_read:
                vin = _identity_vin(contract, identity)
                vin_supported=any(item['requestHex'] in ('1A90','22F190') for item in contract['identityRequests'])
                if vin_supported:
                    generation = vehicle_generation_from_vin(vin)
                    if generation != contract['generation']:
                        raise ElmError('vehicle-identity-mismatch')
                    vehicle_hash = hashlib.sha256(vin.encode('ascii')).hexdigest()
                    if anchor_hash is not None and vehicle_hash!=anchor_hash:raise ElmError('vehicle-identity-mismatch')
                else:
                    if anchor_hash is None:raise ElmError('manufacturer-vehicle-anchor-required')
                    generation=contract['generation'];vehicle_hash=anchor_hash
                if expected_vehicle_identity_sha256 is not None and expected_vehicle_identity_sha256 != vehicle_hash:
                    raise ElmError('vehicle-identity-mismatch')
                out.update(vehicleGeneration=generation,vehicleIdentitySha256=vehicle_hash,
                    vehicleIdentitySource='unit-vin' if vin_supported else 'gateway-vin')
                out['identityInfo']=identity_details(contract, identity)
                out.update(identityOnly=False,identifiedProfileId=profile['profileId'])
                if read_coding:
                    from .manufacturer_coding import read_source_coding
                    out['codingOnly']=True
                    # Identity is already freshly qualified within its own
                    # budget. Complete originals use the remaining batch time;
                    # read_source_coding bounds every individual request.
                    deadline = session_deadline
                    result=read_source_coding(client,profile,contract,out['transport']['qualification'],deadline,guard,
                        require_complete_scope=require_complete_coding)
                    out['codingRead']=result
                    out.update(ok=result['ok'],error=result['error'],vehicleDataCollected=bool(result['blocks']))
                    if result.get('completeOriginalRead'):
                        from .manufacturer_coding_fields import coding_display_fields
                        from .pdk_read import _source_profile
                        source = _source_profile(profile['profileId'], contract['ecuId'], generation=generation)
                        result['fields'] = coding_display_fields(source, result['blocks'], guard)
                else:
                    from .manufacturer_dtc import read_source_dtcs
                    out['dtcOnly']=True
                    out['dtcRead']=read_source_dtcs(client,profile,contract,out['transport']['qualification'],deadline,guard)
                    out.update(ok=True,error=None,vehicleDataCollected=True)
                return out
            out.update(ok=True, error=None, identityOnly=True, identifiedProfileId=profile['profileId'])
            if contract['ecuId'] == 9:
                out['identityInfo'] = identity_details(contract, identity)
                # Decode the fresh source-bound VIN, never the garage archive or
                # an engine part number. Keep the full VIN out of worker output.
                vin = _identity_vin(contract, identity)
                out['vehicleGeneration'] = vehicle_generation_from_vin(vin)
                if out['vehicleGeneration']:
                    out['vehicleIdentitySha256'] = hashlib.sha256(vin.encode('ascii')).hexdigest()
            return out
        def fault(error):
            from .tcp_port import TcpCleanupError
            if isinstance(error, TcpCleanupError): return False
            return isinstance(error, (ConnectionError, OSError)) or str(error).startswith(
                ('port-io:', 'short-write', 'disconnect', 'prompt-timeout', 'nrc78-timeout', 'pending-timeout',
                 'mode30-peer-closed', 'mode30-response-timeout', 'mode30-request-ack-timeout'))
        def reason(error):
            from .tcp_port import TcpCleanupError
            if isinstance(error, TcpCleanupError): return 'recovery-close-failed'
            code = str(error)
            if fault(error): return 'transport-disconnected'
            if code in ('cancelled', 'deadline-expired', 'identity-mismatch', 'vehicle-identity-mismatch', 'recovery-close-failed',
                        'manufacturer-close-failed', 'recovery-exhausted', 'identity-decode-failed',
                        'identity-request-failed', 'session-subfunction-mismatch', 'manual-retry-required'):
                return code
            if code.startswith(('negative-response', 'nrc')): return 'response-rejected'
            return 'recovery-failed'
        def notify(episode, phase, attempt, error=None):
            event = {'sequence': len(out['connectionEvents']) + 1, 'episode': episode,
                     'phase': phase, 'attempt': attempt, 'connectionEpoch': connection_epoch,
                     'elapsedMs': (time.monotonic() - acquisition_started) * 1000,
                     'observedUtc': datetime.now(timezone.utc).isoformat(),
                     'reason': reason(error) if error is not None else None,
                     'sourceKind': source, 'simulation': not physical,
                     'executionEnabled': physical, 'liveVerified': False, 'writePayload': None}
            if phase == 'awaiting-retry': event['linkReleased'] = True
            out['connectionEvents'].append(event)
            out['connectionEventCount'] += 1
            if on_connection is not None: on_connection(dict(event))
        pending_idle_error = None
        def ask(hx):
            nonlocal pending_idle_error, deadline
            # Qualification has its own bounded budget. Once qualified, a
            # healthy stream is bounded by each request and the original batch,
            # not the sum of all selected requests in a polling cycle.
            if streaming:
                deadline = session_deadline
            guard()
            if hx not in authorized: raise ElmError('manufacturer-request-unqualified')
            if pending_idle_error is not None:
                error, pending_idle_error = pending_idle_error, None
                raise error
            return client.request(hx, min(deadline, time.monotonic() + min(15, float(budget_s))))
        def receive(hx):
            nonlocal deadline
            episode = None
            attempt = 0
            try:
                try:
                    response = ask(hx)
                except (ElmError, OSError) as first:
                    if (streaming and time.monotonic() >= session_deadline
                            and (str(first) in ('deadline-expired', 'port-io:write-deadline')
                                 or str(first).startswith(('prompt-timeout', 'nrc78-timeout', 'pending-timeout',
                                     'mode30-response-timeout', 'mode30-request-ack-timeout')))):
                        # The batch ended while awaiting this request. Do not
                        # present normal expiry as a connection interruption.
                        raise ElmError('deadline-expired') from first
                    if not fault(first): raise
                    episode = {'unfinishedRequestHex': hx, 'attempts': [], 'recovered': False}
                    out['transport']['recovery'].append(episode)
                    number = len(out['transport']['recovery'])
                    notify(number, 'interrupted', 0, first)
                    close()
                    manual = physical or wait_retry is not None
                    while manual or attempt < 3:
                        if manual:
                            if wait_retry is None: raise ElmError('manual-retry-required')
                            if not wait_retry(number, attempt, session_deadline,
                                    lambda: notify(number, 'awaiting-retry', attempt)):
                                if cancel.is_set(): raise ElmError('cancelled')
                                if time.monotonic() >= session_deadline:
                                    out['stopReason'] = 'time-limit'
                                    raise ElmError('deadline-expired')
                                raise ElmError('manual-retry-required')
                        # Requalification is bounded on every retry; the
                        # original batch deadline never moves.
                        deadline = min(session_deadline, time.monotonic() + float(budget_s))
                        guard()
                        attempt += 1
                        entry = {'attempt': attempt, 'ok': False}
                        episode['attempts'].append(entry)
                        notify(number, 'retrying', attempt)
                        try:
                            open_qualified(identity)
                            notify(number, 'qualified', attempt)
                            response = ask(hx)
                            break
                        except (ElmError, OSError) as error:
                            entry['error'] = str(error)
                            if not fault(error): raise
                            notify(number, 'retry-failed', attempt, error)
                            close()
                            if not manual and attempt == 3: raise ElmError('recovery-exhausted') from error
                payload = response.get('payload_hex') or ''
                unsupported = (response.get('error') == 'negative-response'
                    and parameter_unavailable_pdu(bytes.fromhex(hx), bytes.fromhex(payload)))
                if not response.get('ok') and not unsupported:
                    raise ElmError(response.get('error') or 'manufacturer-no-data')
                guard()
                pdu = bytes.fromhex(response['payload_hex'])
                if episode is not None:
                    episode['attempts'][-1]['ok'] = True
                    episode['recovered'] = True
                    notify(number, 'recovered', attempt)
                return pdu
            except (ElmError, OSError, ImportError, ValueError) as error:
                if episode is not None:
                    if episode['attempts']: episode['attempts'][-1].setdefault('error', str(error))
                    notify(number, 'failed', attempt, error)
                raise
        def bounded_receive(hx):
            try: return receive(hx)
            except (ElmError, OSError, ImportError) as error: raise ValueError(str(error)) from error
        acquisition_started = time.monotonic()
        unavailable_ids = set()
        invalid_parameters = {}
        for cycle in (itertools.count() if cycles is None else range(cycles)):
            if streaming:
                if time.monotonic() >= session_deadline:
                    out['stopReason'] = 'time-limit'
                    break
                deadline = session_deadline
            if cycle and interval_s:
                if callable(getattr(client,'idle_until',None)):
                    try:client.idle_until(min(time.monotonic()+interval_s,deadline),deadline)
                    except (ElmError,OSError) as error:pending_idle_error=error
                else:cancel.wait(min(interval_s, max(0, deadline - time.monotonic())))
            if streaming and not cancel.is_set() and time.monotonic() >= session_deadline:
                out['stopReason'] = 'time-limit'
                break
            guard()
            active_ids = [key for key in selected if key not in unavailable_ids]
            result = _decode_cycle(profile, active_ids, bounded_receive, cancelled=cancel.is_set,
                                   source=source, synthetic=not physical,
                                   elapsed_origin=acquisition_started,
                                   connection_epoch=lambda: connection_epoch)
            for sample in result['samples']:
                sample.update(cycle=cycle + 1, sourceKind=source)
                invalid_parameters.pop(sample['parameterId'], None)
            for transaction in result['transactions']:
                transaction.update(cycle=cycle + 1, sourceKind=source)
            for parameter in result['unavailableParameters']:
                parameter.update(cycle=cycle + 1, sourceKind=source)
                unavailable_ids.add(parameter['parameterId'])
                invalid_parameters.pop(parameter['parameterId'], None)
            for parameter in result['invalidParameters']:
                parameter.update(cycle=cycle + 1, sourceKind=source)
                invalid_parameters[parameter['parameterId']] = parameter
            out['invalidParameters'] = list(invalid_parameters.values())
            out['unavailableParameters'].extend(result['unavailableParameters'])
            out['samples'].extend(result['samples'])
            out['transactions'].extend(result['transactions'])
            out['completedCycles'] += result['completedCycles']
            out['vehicleDataCollected'] |= result['vehicleDataCollected']
            out['requestCount'] = len(out['transactions'])
            out['ok'], out['error'] = result['ok'], result['error']
            if on_cycle is not None:
                on_cycle({**result, 'cycle': cycle + 1, 'sourceKind': source})
            if not result['ok']:
                batch_expired = (streaming and result['error'] == 'deadline-expired'
                    and time.monotonic() >= session_deadline)
                break
            if len(unavailable_ids) == len(selected):
                out['stopReason'] = 'all-parameters-unavailable'
                break
    except (ValueError, KeyError, TypeError, ElmError, OSError) as error:
        out.update(ok=False, error=str(error))
        batch_expired = (streaming and str(error) == 'deadline-expired'
            and time.monotonic() >= session_deadline)
    finally:
        if client is not None:
            try: close()
            except (ElmError, OSError) as error:
                out['ok'] = False
                out['error'] = str(error)
            # Configuration restoration must succeed on a healthy connection.
            if out['ok'] and any(not str(e).startswith('poisoned:') for e in out['transport']['closeErrors']):
                out['ok'] = False
                out['error'] = 'manufacturer-close-failed'
    # Preserve partial-cycle evidence and the low-level timeout. Only an expiry
    # observed during acquisition is a normal batch stop; a later cleanup
    # failure must remain a failure, even if cleanup crosses the batch deadline.
    if batch_expired and out.get('error') == 'deadline-expired':
        out['stopReason'] = 'time-limit'
    return out
