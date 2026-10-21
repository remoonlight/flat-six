"""Decode the six LED identity replies from their own PIWIS definitions.

The four version rules are from the pinned X431 source catalogue. Field
positions and types are from PIWIS 9x1 2830.1.0 SCHEINWERFER_LED, independently
checked against the original database readers. These are not X431 identity
records: that catalogue contains none for these versions.

This decoder performs no I/O and does not establish freshness, transport or
vehicle applicability. The shared-address discovery client must bind those
before exposing a freshly recognized unit. No fault/write recipe is supplied.
"""
import os
from functools import lru_cache
from pathlib import Path
import re
import time

from .elm import ElmError
from .pdk_read import _source_profile
from .x431_command_origin import _lookup_command


SOURCE_CATALOG = '31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba'
PIWIS_DATABASE_SHA256 = '07dee0d7e404ea175ab4c6695ef76b3db5fe8d9102184731b6e4f0230efa1fb5'
# The hash is checked against the retained original before client registration.
VERSION_RULES = {
    '9x1:SCHEINWERFER_LED:Scheinwerfer_LED_DK2F_001': (
        'EV_LEDModRightMELCOVW26X#001', 'EV_LEDModLeftMELCOVW26X#001'),
    '9x1:SCHEINWERFER_LED:Scheinwerfer_LED_DK3_001': (
        'EV_LEDModLeftMELCOAU736#001', 'EV_LEDModRightMELCOAU736#001'),
    '9x1:SCHEINWERFER_LED:Scheinwerfer_LED_Links_001': (
        'EV_LEDModLeftHELLA#001#nein', 'EV_LEDModLeftHELLAAU37X#001#nein'),
    '9x1:SCHEINWERFER_LED:Scheinwerfer_LED_Rechts_001': (
        'EV_LEDModRightHELLA#001#nein', 'EV_LEDModRightHELLAAU37X#001#nein'),
}
IDENTITY_REQUESTS = ('22F19E', '22F1A2', '22F1DF', '22F187', '22F189', '22F191')
_FIXED_LENGTHS = {'22F1A2': 6, '22F1DF': 1, '22F187': 11, '22F189': 4, '22F191': 11}
IDENTITY_KIND = 'piwis-led-identity'
SELECTOR_KEYS = ('leftWordSigned', 'rightWordSigned', 'byteOffsetSigned', 'bitOffsetByte', 'formulaId')
SELECTORS = ((0x22, -3682, 2, 0, 0x8000021D), (0x22, -3678, 2, 0, 0x8000000A),
             (0x22, -3617, 2, 2, 0xA0000001))
COMMANDS = (
    (0x20000A, '00021003', '90a95828ebc1916d89ff0ef9249c8ab6542bc065c81255004ee21c7fc074592c'),
    (0x200002, '010322F19E', 'af7e9fd8f0e0461f31027ab5baa5f8f2e9bf19be5b62d91575fc2d34117d828e'),
    (0x200001, '010322F1A2', 'd0d052a6db998f9edc31a725b5f6bbded719920a02b94348936cd81eb60c774c'),
    (0x200003, '010322F1DF', '12fe2247842cd932cf0ecee288bc03c8b5dc64cb4a42094f81dfd02c4da5cf69'),
)


@lru_cache(maxsize=1)
def _validate_commands(language):
    """Cache only a successful validation of these exact immutable file bytes.

    Four variants and repeated contract construction use the same language
    file. A path/mtime cache could hide changes; bytes are the cache key instead.
    There is no cached ECU reply, version decision or mutable decoder context.
    """
    for cid, encoded, digest in COMMANDS:
        command = _lookup_command(language, cid)
        if (command.get('recordFound') is not True or command.get('decodedPayloadHex', '').upper() != encoded
                or command.get('decodedPayloadSha256') != digest):
            raise ValueError('headlamp-source-command-unresolved')


def source_contract(profile, selected):
    """Only the four literal source versions on their declared ECU 47 route.

    This is a PIWIS identity-layout bridge plus the exact X431 communication
    binding. It neither borrows normal SWSG identity fields nor infers an ECU
    address from the Links/Rechts names. There are no measurement definitions.
    """
    pid = profile.get('profileId')
    ecu = profile.get('selectedEcuId')
    generation = profile.get('requestedGeneration', '981')
    if (not isinstance(pid, str) or pid not in VERSION_RULES or type(ecu) is not int or ecu != 47
            or profile.get('ecuIds') != [47] or profile.get('generation') is not None
            or generation not in ('981', '982') or selected != []
            or profile.get('identity') != [] or profile.get('parameters') != []):
        raise ValueError('manufacturer-profile-unqualified')
    source = _source_profile(pid, ecu, generation=generation)
    if (source.get('profileId') != pid or source.get('module') != 'SCHEINWERFER_LED'
            or source.get('sourceGeneration') is not None or source.get('ecuIds') != [47]
            or generation not in source.get('generations', [])
            or source.get('identityGroups') != [] or source.get('measurementGroups') != []):
        raise ValueError('headlamp-source-profile-mismatch')
    refs = source.get('versionRoutes', [])
    if (len(refs) != 1 or refs[0].get('requestedEcuId') != ecu
            or refs[0].get('sourceSha256') != '55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f'
            or refs[0].get('bindingStatus') != 'exact-source-version-rule-match'
            or len(refs[0].get('routes', [])) != 1):
        raise ValueError('headlamp-source-version-unresolved')
    route = refs[0]['routes'][0]
    matches = route.get('versionMatches', [])
    selectors = route.get('selectors', [])
    if (route.get('routeId') != ecu or route.get('routeIndexOffset') != 106194
            or route.get('flagsWord') != 0 or route.get('module') != 'SCHEINWERFER_LED'
            or [tuple(s.get(k) for k in SELECTOR_KEYS) for s in selectors] != list(SELECTORS)
            or len(matches) != 1 or matches[0].get('name') != pid.rsplit(':', 1)[1]
            or matches[0].get('rules') != list(VERSION_RULES[pid])):
        raise ValueError('headlamp-source-version-unresolved')
    refs = source.get('diagnosticSources', [])
    if (len(refs) != 1 or refs[0].get('requestedEcuId') != ecu
            or refs[0].get('sourceSha256') != '18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'
            or refs[0].get('bindingStatus') != 'exact-source-name-match'
            or refs[0].get('variantName') != pid.rsplit(':', 1)[1]
            or len(refs[0].get('links', [])) != 1):
        raise ValueError('headlamp-source-route-mismatch')
    link = refs[0]['links'][0]
    communication = link.get('communication') or {}
    pairs = communication.get('pairs', [])
    if (link.get('linkIndex') != 0 or communication.get('class') != 'static-source-confirmed'
            or communication.get('modeByte') != 0x10 or len(pairs) != 1
            or pairs[0].get('storedTx') != 0x725 << 5 or pairs[0].get('storedRx') != 0x78F << 5
            or pairs[0].get('subtypeByte') != 3
            or pairs[0].get('parameterIds') != [1049511, 2097153, 2097155, 2097162]
            or 'selectedSourceLink' in profile):
        raise ValueError('headlamp-source-route-mismatch')
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    path = library / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
    language = path.read_bytes()
    _validate_commands(language)
    from .x431_variant_selection import _load_version_language
    from scripts.x431_re.gag_lib import compact_express
    reader = _load_version_language(path)
    fields = [dict(requestHex=f'{s["leftWordSigned"]:02X}{s["rightWordSigned"] & 0xFFFF:04X}',
                   selector=s, formula=compact_express(reader.lookup_id('EXPRESS.GAG', s['formulaId'])))
              for s in selectors]
    if any(not item['formula'] or not item['formula'].get('text') for item in fields):
        raise ValueError('headlamp-source-selector-unresolved')
    return dict(sourceCatalogId=SOURCE_CATALOG, sourceQualification='uds-source-version-and-identity',
        catalogProfileId='porsche-981-uds-source', ecuId=ecu, txId=0x725, rxId=0x78F,
        applicationProtocol='uds', sourceIdentityKind=IDENTITY_KIND,
        identityRequests=[dict(field=hx, requestHex=hx) for hx in IDENTITY_REQUESTS],
        identityConstraints={'versionRules': list(VERSION_RULES[pid])}, versionSelectors=fields,
        sourceCommands=['1003', '22F19E', '22F1A2', '22F1DF'],
        session=dict(requestHex='1003', positivePrefixHex='5003'),
        sourceBridge=dict(project='9x1', version='2830.1.0',
            sources={'0.0.0@SCHEINWERFER_LED.bv.db': PIWIS_DATABASE_SHA256}))


def decode_identity(profile_id, responses):
    """Validate complete positive PDUs, then compare the literal source rule.

    The six bytes of F1A2 include both the diagnostic major and minor versions;
    three bytes sufficient to compare the X431 rule are not a complete reply.
    A zero F1DF byte is a valid state, not a blank identity. Complete original
    replies are retained for the caller's subsequent identity comparison.
    """
    if not isinstance(profile_id, str) or profile_id not in VERSION_RULES:
        raise ValueError('headlamp-source-profile-unqualified')
    if not isinstance(responses, dict):
        raise ValueError('headlamp-source-identity-incomplete')
    bodies = {}
    original = {}
    for request in IDENTITY_REQUESTS:
        text = responses.get(request)
        if not isinstance(text, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){4,29}', text):
            raise ValueError('headlamp-source-identity-incomplete')
        pdu = bytes.fromhex(text)
        if pdu[:3] != b'\x62' + bytes.fromhex(request)[1:]:
            raise ValueError('headlamp-source-identity-response-mismatch')
        body = pdu[3:]
        if request in _FIXED_LENGTHS and len(body) != _FIXED_LENGTHS[request]:
            raise ValueError('headlamp-source-identity-length-mismatch')
        # The ASAM name row explicitly requires an ECU-supplied zero terminator.
        # Do not manufacture one from the zero-initialized X431 work buffer.
        if request == '22F19E':
            if not body.endswith(b'\0') or not 3 <= len(body) - 1 <= 25 or b'\0' in body[:-1]:
                raise ValueError('headlamp-source-identity-termination-mismatch')
        elif request != '22F1DF' and (not body.strip(b'\0 ') or all(v == 255 for v in body)):
            raise ValueError('headlamp-source-identity-blank')
        bodies[request] = body
        original[request] = text.upper()

    family = bodies['22F19E'][:-1].decode('iso-8859-1')
    major = bodies['22F1A2'][:3].decode('iso-8859-1')
    minor = bodies['22F1A2'][3:].decode('iso-8859-1')
    status = bodies['22F1DF'][0]
    inconsistent = bool(status & 0x04)
    values = (family, major, 'ja' if inconsistent else 'nein')
    if not any(tuple(rule.split('#')) == values[:len(rule.split('#'))]
               for rule in VERSION_RULES[profile_id]):
        raise ValueError('headlamp-source-identity-mismatch')
    return {
        'profileId': profile_id,
        'asamIdentifier': family,
        'diagnosticMajorVersion': major,
        'diagnosticMinorVersion': minor,
        'softwareInconsistent': inconsistent,
        'programmable': bool(status & 0x40),
        'sparePartNumber': bodies['22F187'].decode('iso-8859-1'),
        'softwareVersion': bodies['22F189'].decode('iso-8859-1'),
        'hardwareNumber': bodies['22F191'].decode('iso-8859-1'),
        'responses': original,
    }


def qualify_client(client, contract, profile_id, deadline, guard, *, resolve_source_responses=None):
    """Read the source selectors and complete identity on the owned connection."""
    if (contract.get('sourceIdentityKind') != IDENTITY_KIND or profile_id not in VERSION_RULES
            or contract.get('ecuId') != 47 or contract.get('generation') not in ('981', '982')
            or contract.get('sourceCommands') != ['1003', '22F19E', '22F1A2', '22F1DF']
            or [r.get('requestHex') for r in contract.get('identityRequests', [])] != list(IDENTITY_REQUESTS)):
        raise ElmError('identity-mismatch')
    responses = {}

    def ask(request):
        guard()
        result = client.request(request, min(deadline, time.monotonic() + 15))
        guard()
        pdu = result.get('payload_hex')
        if (not result.get('ok') or not isinstance(pdu, str)
                or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){2,513}', pdu)):
            raise ElmError(result.get('error') or 'identity-request-failed')
        wire = bytes.fromhex(request)
        if not bytes.fromhex(pdu).startswith(bytes([wire[0] + 0x40]) + wire[1:]):
            raise ElmError('wrong-sid-or-did-echo')
        return pdu.upper()

    for request in contract['sourceCommands']:
        pdu = ask(request)
        if request != '1003':
            responses[request] = pdu
    if resolve_source_responses is not None:
        resolved = resolve_source_responses(responses)
        if resolved is None:
            raise ElmError('identity-mismatch')
        actual, profile_id = resolved
        if (actual.get('sourceIdentityKind') != IDENTITY_KIND
                or any(actual.get(key) != contract.get(key) for key in
                       ('ecuId', 'generation', 'txId', 'rxId', 'sourceCommands', 'identityRequests', 'sourceBridge'))):
            raise ElmError('identity-mismatch')
        contract = actual
    for request in IDENTITY_REQUESTS:
        if request not in responses:
            responses[request] = ask(request)
    try:
        decode_identity(profile_id, responses)
    except ValueError as error:
        raise ElmError('identity-mismatch' if str(error).endswith('identity-mismatch')
                       else 'identity-decode-failed') from error
    return responses, dict(observedProfileMatch=True, generation=contract['generation'], ecuId=47,
        variantLink={'profile_id': profile_id}, sourceCatalogId=SOURCE_CATALOG,
        freshRead=True, liveVerified=False, writePayload=None)
