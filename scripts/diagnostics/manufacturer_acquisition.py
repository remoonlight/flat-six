"""Bounded manufacturer sampling/decoding. No serial import or device ownership.

The same full application PDU is decoded once per selected request group. Real
transport qualification is deliberately absent; historical responses are only
fixtures, never evidence that an independent adapter can perform this task.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import time

from .realtime_preparation import FLAGS, plan_for, record_from_parameter
from .response_values import decode_application_response

DME_PROFILE = '9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5'
GATEWAY_PROFILE = '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1'
# Explicit software routes, not a general inference from an ECU number/name.
# The shared 9x1 Gateway definition has no generation tag. Only its observed
# 981 identity/catalog binding is accepted; the caller must still select 981.
READ_PROFILES = {
    DME_PROFILE: (1, 'porsche-981-2014-dme', '981'),
    GATEWAY_PROFILE: (9, 'porsche-981-2014-gateway', None),
}


def matched_read_profile(profile):
    route = READ_PROFILES.get(profile.get('profileId'))
    if (route is None or profile.get('status') != 'identity-matched'
            or profile.get('ecuIds') != [route[0]] or profile.get('generation') != route[2]):
        raise ValueError('manufacturer-profile-unqualified')
    return route


def acquire_cycle(profile, selected, receive, *, mode='simulation', cancelled=lambda: False):
    """receive is a test-owned full-PDU provider; live fails before it is called."""
    if mode != 'simulation':
        out = _cycle_result('historical-fixture-rehearsal', synthetic=True)
        out['error'] = 'manufacturer-transport-unqualified'
        return out
    return _decode_cycle(profile, selected, receive, cancelled=cancelled,
                         source='historical-fixture-rehearsal', synthetic=True)


def _cycle_result(source, *, synthetic):
    out = {'ok': False, 'error': None, 'sourceKind': 'historical-fixture-rehearsal',
           'vehicleDataCollected': False, 'simulation': True, 'liveVerified': False,
           'samples': [], 'transactions': [], 'completedCycles': 0, **FLAGS}
    out.update(sourceKind=source, simulation=synthetic, executionEnabled=not synthetic)
    return out


def _decode_cycle(profile, selected, receive, *, cancelled, source, synthetic,
                  elapsed_origin=None, connection_epoch=None):
    """Private common decoder; transport owns fresh identity and provenance gates."""
    out = _cycle_result(source, synthetic=synthetic)
    try:
        matched_read_profile(profile)
        plan = plan_for(profile, selected)
        if plan['blocked']:
            raise ValueError('manufacturer-definition-incomplete')
        params = {p['id']: p for p in profile['parameters']}
        started = time.monotonic() if elapsed_origin is None else elapsed_origin
        for group in plan['groups']:
            if cancelled():
                raise ValueError('cancelled')
            hx = group['requestHex']
            request = bytes.fromhex(hx)
            if request[0] not in (0x21, 0x22) or not 2 <= len(request) <= 7:
                raise ValueError('manufacturer-request-unqualified')
            response = receive(hx)
            if not isinstance(response, bytes) or not response:
                raise ValueError('manufacturer-no-data')
            if len(response) > 4095:
                raise ValueError('manufacturer-response-too-large')
            out['vehicleDataCollected'] = not synthetic
            # All fields in this response share one host observation, before
            # decoding. This is not an ECU acquisition/hardware timestamp.
            elapsed_ms = (time.monotonic() - started) * 1000
            stamp = datetime.now(timezone.utc).isoformat()
            epoch = connection_epoch() if connection_epoch is not None else None
            transaction = {'requestHex': hx, 'pduHex': response.hex().upper(),
                           'pduSha256': hashlib.sha256(response).hexdigest(),
                           'processedUtc': stamp, 'elapsedMs': elapsed_ms, 'ok': False,
                           'timestampSource': 'host-response-observed',
                           **({'connectionEpoch': epoch} if epoch is not None else {})}
            out['transactions'].append(transaction)
            if cancelled():
                transaction['error'] = 'cancelled'
                raise ValueError('cancelled')
            decoded = []
            # A truncated group never produces a mixture of fresh and stale fields.
            for key in group['parameterIds']:
                p = params[key]
                value = decode_application_response(p.get('record') or record_from_parameter(p),
                    response, mode='pdu', source=source)
                if not value.get('ok'):
                    transaction['error'] = value.get('reason')
                    raise ValueError(value.get('reason') or 'manufacturer-decode-failed')
                decoded.append({'parameterId': key, 'name': p['name'], 'unit': p.get('unit'),
                    'requestHex': hx, 'value': value.get('phys', value.get('value')),
                    'display': value.get('display', value.get('text', value.get('value'))),
                    'numeric': value.get('numeric', False), 'rawValue': value.get('raw'),
                    'processedUtc': stamp, 'elapsedMs': elapsed_ms,
                    'timestampSource': 'host-response-observed',
                    **({'connectionEpoch': epoch} if epoch is not None else {}),
                    'synthetic': synthetic, 'pduSha256': transaction['pduSha256']})
            transaction['ok'] = True
            out['samples'].extend(decoded)
        out.update(ok=True, completedCycles=1, requestCount=len(out['transactions']))
    except (ValueError, OSError, TimeoutError, KeyError, TypeError) as e:
        out['error'] = str(e) or type(e).__name__
    return out


def rehearsal_cycle(profile, selected, groups):
    """Only use exact referenced historical PDUs on the matched address pair."""
    fixtures = historical_fixtures(profile, selected, groups)
    return acquire_cycle(profile, selected, fixtures.get)


def historical_fixtures(profile, selected, groups):
    """Resolve exact evidence references; never substitute a different capture."""
    from .catalog import load_catalog, profile_by_id
    _ecu, catalog_id, _generation = matched_read_profile(profile)
    named = profile_by_id(load_catalog(), catalog_id)
    pair = (int(named['txId'], 16), int(named['rxId'], 16))
    params = {p['id']: p for p in profile['parameters']}
    fixtures = {}
    for key in selected or []:
        p = params.get(key)
        if not p:
            raise ValueError('unknown-parameter')
        if not p.get('decodedSampleCount') or not p.get('examples'):
            raise ValueError('manufacturer-historical-response-missing')
        refs = {(s['groupId'], s['responseFrameId'], s['pduSha256']) for s in p['examples']}
        match = None
        for g in groups:
            if (g['requestHex'] != p['requestHex'] or (g['txId'], g['rxId']) != pair
                    or g['adapterStream'] != [2, 2] or g['capturePhase'] != 'before-coding'):
                continue
            for s in g['samples']:
                raw = bytes.fromhex(s['pduHex'])
                if (g['groupId'], s['responseFrameId'], hashlib.sha256(raw).hexdigest()) in refs:
                    match = raw
                    break
            if match is not None:
                break
        if match is None:
            raise ValueError('manufacturer-response-reference-mismatch')
        prior = fixtures.setdefault(p['requestHex'], match)
        if prior != match:
            raise ValueError('manufacturer-shared-response-conflict')
    return fixtures
