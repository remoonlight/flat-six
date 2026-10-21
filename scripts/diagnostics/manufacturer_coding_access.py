"""Source-bound coding access exchange on an already owned, identified client.

Not exposed by the desktop or a CLI: coding edit/write/restore recipes and their
backup gates remain separate. This helper never writes a coding record, opens a
device, retries a request, or treats a native callback return as an ECU reply.
Its client contract is the project's normalized full-PDU request interface.
"""
from __future__ import annotations

import math
import re
import time

from .elm import ElmError
from .manufacturer_coding import source_coding_plan, SOURCE_CATALOG
from .x431_coding_access import CodingAccessSource


def _operations(mode):
    phase = (mode >> 8) & 255
    level = mode >> 16
    # The original new-key dispatcher and security parser were executed for
    # these exact session paths/subfunctions. Flash access is a separate job.
    if level not in (0x0102, 0x0304, 0x090a, 0x1112, 0x2122, 0x2526):
        raise ValueError('coding-access-level-unqualified')
    sessions = {1: ('1003', '104F'), 2: ('1089', '1086'),
        3: ('1003',), 4: ('1089',)}.get(phase)
    if sessions is None:
        raise ValueError('coding-access-session-path-unqualified')
    return [(session, level >> 8, level & 255) for session in sessions]


def run_coding_access(client, source, profile, contract, qualification, deadline, guard,
        *, clock=time.monotonic, sleeper=time.sleep):
    """Exchange only the selected source's sessions and seed/key requests.

    Fresh vehicle/ECU identity and a qualified complete original-read definition
    are prerequisites. Successful access is not permission to modify coding.
    A partial failure requires the owner to discard/re-identify this session.
    """
    pid = profile.get('profileId')
    if (type(source) is not CodingAccessSource or not callable(guard)
            or type(deadline) not in (float, int) or not math.isfinite(deadline)
            or qualification.get('freshRead') is not True
            or qualification.get('observedProfileMatch') is not True
            or qualification.get('sourceCatalogId') != SOURCE_CATALOG
            or qualification.get('generation') != contract.get('generation')
            or qualification.get('ecuId') != contract.get('ecuId')
            or (qualification.get('variantLink') or {}).get('profile_id') != pid):
        raise ElmError('coding-access-identity-unqualified')
    guard()
    plan = source_coding_plan(profile, contract)
    if 'completeScopeSource' not in plan:
        raise ElmError('coding-access-complete-scope-unqualified')
    definition = source.resolve(pid)['match']
    if definition is None:
        raise ElmError('coding-access-definition-missing')
    # Check algorithm support and exact source width before sending anything.
    width = definition['flagByte']
    if width not in (2, 4):
        raise ElmError('coding-access-algorithm-unqualified')
    try:
        source.key_for_seed(pid, bytes(width))
        operations = _operations(definition['modeWord'])
    except ValueError as error:
        raise ElmError(str(error)) from error
    result = dict(ok=False, error=None, profileId=pid, completed=[],
        accessGranted=False, requiresFreshSession=False, codingWriteQualified=False,
        codingRestoreQualified=False, vehicleVerified=False)
    prior_allowed = set(client.allowed) if hasattr(client, 'allowed') else None

    def check():
        guard()
        if clock() >= deadline:
            raise ElmError('coding-access-deadline')

    def pause():
        end = min(deadline, clock() + 0.5)
        while clock() < end:
            check()
            remaining = end - clock()
            if remaining <= 0:
                break
            sleeper(min(0.05, remaining))
        check()

    def request(hx, *, seed_width=None):
        check()
        if prior_allowed is not None:
            client.allowed = prior_allowed | {hx}
        response = client.request(hx, min(deadline, clock() + 15))
        check()
        if not isinstance(response, dict):
            raise ElmError('coding-access-response-invalid')
        if response.get('ok') is not True:
            raise ElmError(response.get('error') or 'coding-access-request-failed')
        value = response.get('payload_hex')
        if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,4095}', value):
            raise ElmError('coding-access-response-invalid')
        pdu, sent = bytes.fromhex(value), bytes.fromhex(hx)
        if pdu[0] == 0x7f:
            raise ElmError('coding-access-negative-response')
        if len(pdu) < 2 or pdu[:2] != bytes((sent[0] + 0x40, sent[1])):
            raise ElmError('coding-access-response-mismatch')
        if sent[0] == 0x27 and len(pdu) != 2 + (seed_width or 0):
            raise ElmError('coding-access-response-length-mismatch')
        return pdu

    try:
        for session, seed_sub, key_sub in operations:
            request(session)
            result['completed'].append(dict(kind='session', session=session))
            pause()
            reply = request('27%02X' % seed_sub, seed_width=width)
            seed = reply[2:]
            if any(seed):
                key = source.key_for_seed(pid, seed)
                pause()
                request('27%02X%s' % (key_sub, key.hex().upper()))
            # A fresh, correctly echoed zero seed means this access level is
            # already granted. It never causes a key or an extra retry.
            result['completed'].append(dict(kind='access', subfunction=seed_sub,
                alreadyGranted=not any(seed)))
        result.update(ok=True, accessGranted=True)
    except (ElmError, OSError, ValueError) as error:
        result.update(error=str(error), requiresFreshSession=True)
    finally:
        if prior_allowed is not None:
            client.allowed = prior_allowed
    return result
