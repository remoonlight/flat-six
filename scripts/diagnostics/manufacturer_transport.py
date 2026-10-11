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
from collections import deque
from datetime import datetime, timezone

from .catalog import load_catalog, operation, profile_by_id
from .decode import decode_payload
from .elm import ElmClient, ElmError
from .manufacturer_acquisition import DME_PROFILE, matched_read_profile, _decode_cycle, historical_fixtures
from .qualification import qualify_identity
from .realtime_preparation import FLAGS, plan_for, record_from_parameter
from .response_values import request_from_record, data_span
from .session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from .sessions import IDENTITY_FIELDS, SESSION_BY_PROFILE

CATALOG_PROFILE = 'porsche-981-2014-dme'


def prepared_read_contract(profile, selected, catalog=None, *, transport_kind='elm-iso-tp'):
    """Server-owned request/identity contract; does not grant hardware access."""
    if transport_kind not in ('elm-iso-tp', 'd-pdu-iso-tp'):
        raise ValueError('manufacturer-transport-unqualified')
    ecu_id, catalog_id, _generation = matched_read_profile(profile)
    plan = plan_for(profile, selected)
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
    named = profile_by_id(catalog or load_catalog(), catalog_id)
    hx, positive = SESSION_BY_PROFILE[catalog_id]
    identity = [dict(field=field, **operation(named, oid))
                for field, oid in IDENTITY_FIELDS[catalog_id]]
    contract = {'schemaVersion': 1, 'kind': 'prepared-manufacturer-read',
        'profileId': profile['profileId'], 'catalogProfileId': catalog_id,
        'generation': '981', 'ecuId': ecu_id, 'txId': int(named['txId'], 16), 'rxId': int(named['rxId'], 16),
        'session': {'requestHex': hx, 'positivePrefixHex': positive},
        'identityRequests': identity, 'identityConstraints': named['identityConstraints'],
        'groups': plan['groups'], 'selectionLimit': 12, 'intervalClaim': None,
        'transportKind': transport_kind, 'rawCanFramesAvailable': transport_kind == 'elm-iso-tp',
        'reconnectAttemptsPerInterruption': 3,
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
           'samples': [], 'transactions': [], 'completedCycles': 0,
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
                   interval_s=0, on_cycle=None, on_connection=None, streaming=False, transport_kind='elm-iso-tp'):
    """Private engine. Physical entry must verify definitions and select its own factory."""
    out = _empty_result(source)
    out['simulation'] = not physical
    if physical:
        out['transport']['kind'] = transport_kind
    cancel = cancel_event or threading.Event()
    client = None
    connection_epoch = 0
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
        catalog = load_catalog()
        contract = prepared_read_contract(profile, selected, catalog, transport_kind=transport_kind)
        out['readContract'] = contract
        authorized = frozenset(g['requestHex'] for g in contract['groups'])
        named = profile_by_id(catalog, contract['catalogProfileId'])
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
            if transport_kind == 'd-pdu-iso-tp':
                client.configure_manufacturer(profile, selected, deadline)
            else:
                client.configure_pair(named['txId'], named['rxId'], deadline)
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
        def fault(error):
            return isinstance(error, (ConnectionError, OSError)) or str(error).startswith(
                ('port-io:', 'short-write', 'disconnect'))
        def reason(error):
            code = str(error)
            if fault(error): return 'transport-disconnected'
            if code in ('cancelled', 'deadline-expired', 'identity-mismatch', 'recovery-close-failed',
                        'manufacturer-close-failed', 'recovery-exhausted', 'identity-decode-failed',
                        'identity-request-failed', 'session-subfunction-mismatch'):
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
            out['connectionEvents'].append(event)
            out['connectionEventCount'] += 1
            if on_connection is not None: on_connection(dict(event))
        def ask(hx):
            guard()
            if hx not in authorized: raise ElmError('manufacturer-request-unqualified')
            return client.request(hx, min(deadline, time.monotonic() + 15))
        def receive(hx):
            episode = None
            attempt = 0
            try:
                try:
                    response = ask(hx)
                except (ElmError, OSError) as first:
                    if not fault(first): raise
                    episode = {'unfinishedRequestHex': hx, 'attempts': [], 'recovered': False}
                    out['transport']['recovery'].append(episode)
                    number = len(out['transport']['recovery'])
                    notify(number, 'interrupted', 0, first)
                    close()
                    for attempt in range(1, 4):
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
                            if attempt == 3: raise ElmError('recovery-exhausted') from error
                if not response.get('ok'):
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
        for cycle in (itertools.count() if cycles is None else range(cycles)):
            if streaming:
                if time.monotonic() >= session_deadline:
                    out['stopReason'] = 'time-limit'
                    break
                deadline = min(session_deadline, time.monotonic() + float(budget_s))
            if cycle and interval_s:
                cancel.wait(min(interval_s, max(0, deadline - time.monotonic())))
            if streaming and not cancel.is_set() and time.monotonic() >= session_deadline:
                out['stopReason'] = 'time-limit'
                break
            guard()
            result = _decode_cycle(profile, selected, bounded_receive, cancelled=cancel.is_set,
                                   source=source, synthetic=not physical,
                                   elapsed_origin=acquisition_started,
                                   connection_epoch=lambda: connection_epoch)
            for sample in result['samples']:
                sample.update(cycle=cycle + 1, sourceKind=source)
            for transaction in result['transactions']:
                transaction.update(cycle=cycle + 1, sourceKind=source)
            out['samples'].extend(result['samples'])
            out['transactions'].extend(result['transactions'])
            out['completedCycles'] += result['completedCycles']
            out['vehicleDataCollected'] |= result['vehicleDataCollected']
            out['requestCount'] = len(out['transactions'])
            out['ok'], out['error'] = result['ok'], result['error']
            if on_cycle is not None:
                on_cycle({**result, 'cycle': cycle + 1, 'sourceKind': source})
            if not result['ok']:
                break
    except (ValueError, KeyError, TypeError, ElmError, OSError) as error:
        out.update(ok=False, error=str(error))
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
