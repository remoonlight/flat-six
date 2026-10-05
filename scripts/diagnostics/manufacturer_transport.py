"""Prepared manufacturer read transport, exercised with a BytePort, never hardware.

Uses the production ELM framing/parser and catalog session/identity qualification.
The public entry rejects live before creating a port. Historical PDUs are private
test fixtures; neither simulated identities nor this rehearsal qualify live reads.
"""
from __future__ import annotations

import threading
import time
import json

from .catalog import load_catalog, operation, profile_by_id
from .decode import decode_payload
from .elm import ElmClient, ElmError
from .manufacturer_acquisition import DME_PROFILE, acquire_cycle, historical_fixtures
from .qualification import qualify_identity
from .realtime_preparation import plan_for
from .session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from .sessions import IDENTITY_FIELDS, SESSION_BY_PROFILE

CATALOG_PROFILE = 'porsche-981-2014-dme'


def transport_rehearsal(profile, selected, groups, *, mode='simulation',
                        port_factory=None, cancel_event=None, budget_s=30):
    out = {'ok': False, 'error': None, 'sourceKind': 'historical-fixture-rehearsal',
           'simulation': True, 'vehicleDataCollected': False, 'liveVerified': False,
           'samples': [], 'transactions': [], 'completedCycles': 0,
           'transport': {'kind': 'injected-byte-port', 'qualification': None,
                         'recovery': [], 'closed': False, 'closeErrors': [], 'rawLog': []}}
    if mode != 'simulation':
        out['error'] = 'manufacturer-transport-unqualified'
        return out
    cancel = cancel_event or threading.Event()
    client = None
    deadline = time.monotonic() + min(max(float(budget_s), 0.01), 60)
    try:
        if (profile.get('profileId') != DME_PROFILE or profile.get('generation') != '981'
                or profile.get('ecuIds') != [1] or profile.get('status') != 'identity-matched'):
            raise ValueError('manufacturer-profile-unqualified')
        plan = plan_for(profile, selected)
        if plan['blocked']:
            raise ValueError('manufacturer-definition-incomplete')
        authorized = frozenset(g['requestHex'] for g in plan['groups'])
        if any(not 2 <= len(bytes.fromhex(hx)) <= 7 or bytes.fromhex(hx)[0] not in (0x21, 0x22)
               for hx in authorized):
            raise ValueError('manufacturer-request-unqualified')
        fixtures = historical_fixtures(profile, selected, groups)
        catalog = load_catalog()
        named = profile_by_id(catalog, CATALOG_PROFILE)
        def factory():
            port = SessionSimPort(CATALOG_PROFILE)
            for hx, pdu in fixtures.items():
                port.ecu_map[hx] = ath1_prompt(isotp_ath1_lines(0x7E8, pdu))
            return port
        factory = port_factory or factory
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
        def open_qualified(expected=None):
            nonlocal client
            guard()
            client = ElmClient(factory(), cancel_event=cancel)
            out['transport']['closed'] = False
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
        result = acquire_cycle(profile, selected, bounded_receive, cancelled=cancel.is_set)
        out.update(result)
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
