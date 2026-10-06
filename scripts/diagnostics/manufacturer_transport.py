"""Prepared manufacturer read transport, exercised with a BytePort, never hardware.

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

from .catalog import load_catalog, operation, profile_by_id
from .decode import decode_payload
from .elm import ElmClient, ElmError
from .manufacturer_acquisition import DME_PROFILE, acquire_cycle, historical_fixtures
from .qualification import qualify_identity
from .realtime_preparation import FLAGS, plan_for, record_from_parameter
from .response_values import request_from_record, data_span
from .session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from .sessions import IDENTITY_FIELDS, SESSION_BY_PROFILE

CATALOG_PROFILE = 'porsche-981-2014-dme'


def prepared_read_contract(profile, selected, catalog=None):
    """Server-owned request/identity contract; does not grant hardware access."""
    if (profile.get('profileId') != DME_PROFILE or profile.get('generation') != '981'
            or profile.get('ecuIds') != [1] or profile.get('status') != 'identity-matched'):
        raise ValueError('manufacturer-profile-unqualified')
    plan = plan_for(profile, selected)
    if plan['blocked']:
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
    named = profile_by_id(catalog or load_catalog(), CATALOG_PROFILE)
    hx, positive = SESSION_BY_PROFILE[CATALOG_PROFILE]
    identity = [dict(field=field, **operation(named, oid))
                for field, oid in IDENTITY_FIELDS[CATALOG_PROFILE]]
    contract = {'schemaVersion': 1, 'kind': 'prepared-manufacturer-read',
        'profileId': profile['profileId'], 'catalogProfileId': CATALOG_PROFILE,
        'generation': '981', 'ecuId': 1, 'txId': int(named['txId'], 16), 'rxId': int(named['rxId'], 16),
        'session': {'requestHex': hx, 'positivePrefixHex': positive},
        'identityRequests': identity, 'identityConstraints': named['identityConstraints'],
        'groups': plan['groups'], 'selectionLimit': 12, 'intervalClaim': None,
        'transportKind': 'elm-iso-tp', 'reconnectAttemptsPerInterruption': 3,
        'qualificationRequired': ['independent-adapter/session', 'fresh-complete-identity',
                                  'request-response-and-field-agreement', 'observed-cadence'],
        'executionEnabled': False, 'liveVerified': False, 'writePayload': None}
    contract['sha256'] = hashlib.sha256(json.dumps(contract, sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
    return contract


def transport_rehearsal(profile, selected, groups, *, mode='simulation',
                        port_factory=None, cancel_event=None, budget_s=30):
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
        port = SessionSimPort(CATALOG_PROFILE)
        for hx, pdu in fixtures.items():
            port.ecu_map[hx] = ath1_prompt(isotp_ath1_lines(0x7E8, pdu))
        return port
    return _transport_cycle(profile, selected, port_factory or factory,
                            cancel_event=cancel_event, budget_s=budget_s)


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
           'samples': [], 'transactions': [], 'completedCycles': 0,
           'transport': {'kind': 'injected-byte-port', 'qualification': None,
                         'recovery': [], 'closed': False, 'closeErrors': [], 'rawLog': []}}
    out['sourceKind'] = source
    return out


def _transport_cycle(profile, selected, factory, *, mode='simulation', cycles=1,
                     source='historical-fixture-rehearsal', cancel_event=None, budget_s=30):
    out = _empty_result(source)
    if mode != 'simulation':
        out['error'] = 'manufacturer-transport-unqualified'
        return out
    cancel = cancel_event or threading.Event()
    client = None
    try:
        if isinstance(budget_s, bool) or not math.isfinite(float(budget_s)) or not 0 < float(budget_s) <= 60:
            raise ValueError('manufacturer-budget-invalid')
        if type(cycles) is not int or not 1 <= cycles <= 1000:
            raise ValueError('manufacturer-cycles-invalid')
        deadline = time.monotonic() + float(budget_s)
        catalog = load_catalog()
        contract = prepared_read_contract(profile, selected, catalog)
        out['readContract'] = contract
        authorized = frozenset(g['requestHex'] for g in contract['groups'])
        named = profile_by_id(catalog, CATALOG_PROFILE)
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
                if len(json.dumps(out['transport']['rawLog']).encode()) > 1024 * 1024:
                    raise ElmError('manufacturer-transport-log-limit')
            if any(str(e).startswith('close:') for e in errors):
                raise ElmError('recovery-close-failed')
            out['transport']['closed'] = True
            if any(not str(e).startswith('poisoned:') for e in errors):
                raise ElmError('manufacturer-close-failed')
        def open_qualified(expected=None):
            nonlocal client
            guard()
            client = ElmClient(factory(), cancel_event=cancel)
            out['transport']['closed'] = False
            if source == 'synthetic-ecu' and getattr(client.port, 'trafficKind', None) != 'synthetic-session':
                # Close without AT/ECU traffic when a test supplied the wrong port.
                client._poisoned = True
                raise ElmError('synthetic-port-required')
            client.validate_adapter(min(10, max(0, deadline - time.monotonic())))
            client.configure_pair(named['txId'], named['rxId'], deadline)
            hx, positive = SESSION_BY_PROFILE[CATALOG_PROFILE]
            reply = client.request(hx, min(deadline, time.monotonic() + 15))
            if not reply.get('ok') or not (reply.get('payload_hex') or '').startswith(positive):
                raise ElmError(reply.get('error') or 'session-subfunction-mismatch')
            identity = {}
            for field, oid in IDENTITY_FIELDS[CATALOG_PROFILE]:
                guard()
                op = operation(named, oid)
                response = client.request(op['requestHex'], min(deadline, time.monotonic() + 15))
                if not response.get('ok') or not (response.get('payload_hex') or '').startswith(op['positivePrefixHex']):
                    raise ElmError(response.get('error') or 'identity-request-failed')
                decoded = decode_payload(response['payload_hex'], op['decode'])
                if not decoded.get('ok'): raise ElmError('identity-decode-failed')
                identity[field] = decoded.get('text')
            qualified = qualify_identity({'generation': '981', 'ecuId': 1, 'identity': identity}, catalog,
                                         expected_generation='981')
            if not qualified.get('observedProfileMatch') or (expected is not None and identity != expected):
                raise ElmError('identity-mismatch')
            out['transport']['qualification'] = qualified
            return identity
        identity = open_qualified()
        def fault(error):
            return isinstance(error, (ConnectionError, OSError)) or str(error).startswith(
                ('port-io:', 'short-write', 'disconnect'))
        def ask(hx):
            guard()
            if hx not in authorized: raise ElmError('manufacturer-request-unqualified')
            return client.request(hx, min(deadline, time.monotonic() + 15))
        def receive(hx):
            try:
                response = ask(hx)
            except (ElmError, OSError) as first:
                if not fault(first): raise
                close()
                episode = {'unfinishedRequestHex': hx, 'attempts': [], 'recovered': False}
                out['transport']['recovery'].append(episode)
                for attempt in range(1, 4):
                    entry = {'attempt': attempt, 'ok': False}
                    episode['attempts'].append(entry)
                    try:
                        open_qualified(identity)
                        response = ask(hx)
                        entry['ok'] = True
                        episode['recovered'] = True
                        break
                    except (ElmError, OSError) as error:
                        entry['error'] = str(error)
                        close()
                        if not fault(error): raise
                        if attempt == 3: raise ElmError('recovery-exhausted') from error
            if not response.get('ok'):
                raise ElmError(response.get('error') or 'manufacturer-no-data')
            guard()
            return bytes.fromhex(response['payload_hex'])
        def bounded_receive(hx):
            try: return receive(hx)
            except (ElmError, OSError) as error: raise ValueError(str(error)) from error
        acquisition_started = time.monotonic()
        for cycle in range(cycles):
            elapsed_offset = (time.monotonic() - acquisition_started) * 1000
            result = acquire_cycle(profile, selected, bounded_receive, cancelled=cancel.is_set)
            for sample in result['samples']:
                sample.update(cycle=cycle + 1, sourceKind=source)
                sample['elapsedMs'] += elapsed_offset
            for transaction in result['transactions']:
                transaction.update(cycle=cycle + 1, sourceKind=source)
            out['samples'].extend(result['samples'])
            out['transactions'].extend(result['transactions'])
            out['completedCycles'] += result['completedCycles']
            out['requestCount'] = len(out['transactions'])
            out['ok'], out['error'] = result['ok'], result['error']
            if not result['ok']:
                break
    except (ValueError, KeyError, TypeError, ElmError, OSError) as error:
        out['error'] = str(error)
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
    return out
