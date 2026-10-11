"""Offline audit of a retained X431 internal response, never an ECU decoder.

Source: libCOMM_ABSTRACT_LAYER.so cc96cd14… at 0x2b8d8 / 0x2ce8c.
The direct helper strips one status byte before copying into its caller's buffer.
That copied buffer remains an internal Porsche envelope, not a qualified ECU PDU.
Inputs must describe one explicitly supplied offline observation, not a device.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

SOURCE_SHA256 = 'cc96cd1443f373c96853cd797be6a9bf84579cdf6e7031c35dc1df423efb91da'
PORSCHE_COMM_SHA256 = 'e1fd611820be63bb2c234eadeb5d56a3aeae8c31def2b8dbc56d020c280654fc'
GUARDS = {'offlineOnly': True, 'executionEnabled': False, 'vendorCodeExecuted': False,
          'vehicleIO': False, 'deviceWrites': False, 'actualTransportSelected': False,
          'actualNativeRuntimeQualified': False, 'actualResponseLengthContractQualified': False,
          'positiveEcuResponseQualified': False, 'actualVehicleFreshnessQualified': False,
          'originalFullCodingQualified': False}


def audit_internal_response(observation: dict) -> dict:
    """Keep internal status, return count and actual copied extent separate.

    No transport callback is accepted. No request is constructed or sent.
    Incomplete, wrapped, retry and status-only observations yield no data candidate.
    """
    if not isinstance(observation, dict):
        raise ValueError('internal-observation-object-required')
    allowed = {'path', 'capacity', 'transportReturn', 'frameHex'}
    if set(observation) - allowed:
        raise ValueError('internal-observation-unknown-fields')
    path = observation.get('path')
    if path not in ('direct', 'cached'):
        raise ValueError('internal-response-path-required')
    capacity = observation.get('capacity')
    if type(capacity) is not int or not 1 <= capacity <= 32767:
        raise ValueError('internal-capacity-signed16-positive-required')
    out = {'sourceSha256': SOURCE_SHA256, 'sourceKind': 'offline-supplied-internal-observation',
           'path': path, 'capacity': capacity,
           'status': None, 'nativeReturnCandidate': None, 'nativeCopyBytesCandidate': 0,
           'internalBufferHexCandidate': None, 'internalBufferComplete': False,
           'ecuPayloadHex': None, 'retryPerformed': False, 'nativeSourceRetryLimit': 3,
           **GUARDS}
    if path == 'cached':
        if 'transportReturn' in observation or 'frameHex' in observation:
            raise ValueError('cached-path-has-no-direct-frame')
        out.update(status='cached-destination-recorded-no-response', nativeReturnCandidate=0)
        return out
    count = observation.get('transportReturn')
    if type(count) is not int or not -(2**31) <= count < 2**31:
        raise ValueError('internal-transport-return-int32-required')
    hx = observation.get('frameHex')
    if not isinstance(hx, str) or len(hx) > 65536 or len(hx) % 2 or not re.fullmatch(r'[0-9a-fA-F]*', hx):
        raise ValueError('internal-frame-hex-invalid')
    frame = bytes.fromhex(hx)
    signed_count = (count & 65535) - (65536 if count & 32768 else 0)
    out.update(transportReturn=count, transportSigned16=signed_count, observedFrameBytes=len(frame))
    if count != signed_count:
        out['status'] = 'transport-return-wrapped-unqualified'
        return out
    if count == -14:
        out.update(status='internal-retry-indicated', nativeReturnCandidate=-14)
        return out
    if count < 2:
        out.update(status='no-complete-internal-frame', nativeReturnCandidate=-60)
        return out
    # An allocated native buffer can have stale/unspecified bytes after the
    # reported extent. Require the exact observed extent; never use that tail.
    if len(frame) != count:
        out['status'] = 'observed-frame-length-mismatch'
        return out
    out['internalStatusByte'] = frame[0]
    if frame[0] == 0:
        data_count = count - 1
        copied = min(data_count, capacity)
        out.update(nativeReturnCandidate=data_count, nativeCopyBytesCandidate=copied)
        if data_count > capacity:
            out['status'] = 'internal-buffer-truncated'
            return out
        out.update(status='complete-internal-buffer-candidate', internalBufferComplete=True,
                   internalBufferHexCandidate=frame[1:].hex())
        return out
    if frame[0] == 255:
        code = frame[1]
        out['internalStatusCode'] = code
        result, label = {0: (0, 'internal-no-data-status'), 1: (-14, 'internal-retry-indicated'),
                         2: (-70, 'internal-failure-status'),
                         128: (128, 'positive-internal-status-only'),
                         129: (129, 'positive-internal-status-only')}.get(code, (-60, 'unknown-internal-status'))
        out.update(status=label, nativeReturnCandidate=result)
        return out
    out.update(status='unknown-internal-status', nativeReturnCandidate=-60)
    return out


def audit_porsche_mode30_wrapper(observation: dict) -> dict:
    """Inspect only the retained non-subtype-four wrapper at 0x11208.

    Native code reads buffer[0] and buffer[5], then copies buffer[8:8+n].
    The whole 1024-byte scratch is initially zero, so declared lengths must be
    checked against fresh copied bytes, not the scratch allocation or zero tail.
    This does not select that wrapper for a vehicle or identify an ECU PDU.
    """
    if not isinstance(observation, dict) or set(observation) != {'wrapper', 'internal'}:
        raise ValueError('porsche-wrapper-observation-invalid')
    if observation['wrapper'] != 'mode30-non-subtype4-0x11208':
        raise ValueError('porsche-wrapper-source-route-unqualified')
    internal = observation['internal']
    if not isinstance(internal, dict) or internal.get('capacity') != 1024:
        raise ValueError('porsche-wrapper-capacity-mismatch')
    lower = audit_internal_response(internal)
    out = {'commSourceSha256': PORSCHE_COMM_SHA256, 'wrapper': observation['wrapper'],
           'lowerResponse': lower, 'status': 'lower-response-unqualified',
           'declaredBodyHexCandidate': None, 'declaredBodyComplete': False,
           'ecuPayloadHex': None, **GUARDS}
    if not lower['internalBufferComplete']:
        return out
    buffer = bytes.fromhex(lower['internalBufferHexCandidate'])
    out['freshInternalBufferBytes'] = len(buffer)
    if len(buffer) < 8:
        out['status'] = 'wrapper-header-not-fresh-complete'
        return out
    declared = buffer[5]
    out.update(wrapperStatusByte=buffer[0], declaredBodyBytes=declared, bodyOffset=8)
    if 8 + declared > len(buffer):
        out['status'] = 'wrapper-body-exceeds-fresh-extent'
        return out
    out.update(status='complete-wrapper-body-candidate', declaredBodyComplete=True,
               declaredBodyHexCandidate=buffer[8:8+declared].hex(),
               extraFreshBytes=len(buffer)-8-declared)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation-json', type=Path, required=True,
                        help='Offline {path, capacity, transportReturn, frameHex}; no vehicle connection')
    parser.add_argument('--porsche-mode30-wrapper', action='store_true',
                        help='Audit explicitly supplied retained wrapper context; never selects a vehicle route')
    args = parser.parse_args(argv)
    try:
        observation = json.loads(args.observation_json.read_text(encoding='utf-8-sig'))
        report = (audit_porsche_mode30_wrapper(observation) if args.porsche_mode30_wrapper
                  else audit_internal_response(observation))
    except (OSError, ValueError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
