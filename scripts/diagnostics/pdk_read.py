"""Source-bound 981 KWP reads for DME, PDK, selector and media units.

The source's complete COMMAND descriptors are decoded as data, not passed to
its heap/string loader. Version comparison uses the reviewed two-byte DSN
or source-defined DME ASCII rule. No native map or write service is reused.
Physical adapter/car acceptance is separate from this software contract.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import time

from .elm import ElmError
from .response_values import decode_application_response
from .source_catalog import _catalog, _document
from .x431_command_origin import _lookup_command

SOURCE_CATALOG = '31bf93245fffc375b757fc19ba03491e101ccabc838c9ee742d70a88cb7805ba'
MODULE = '9x1:Getriebesteuerung_PDK:'
VERSIONS = {
    MODULE + 'PDK': '4000',
    MODULE + 'PDK_ohne_Quersperre': '4010',
    MODULE + 'PDK_A2': '4100',
    MODULE + 'PDK_A2_ohne_Quersperre': '4110',
    MODULE + 'PDK_A3': '4200',
    MODULE + 'PDK_A3_ohne_Quersperre': '4210',
    MODULE + 'PDK_A4': '4400',
    MODULE + 'PDK_A5_CS': '4500',
}
SELECTOR_PROFILE = '9x1:Waehlhebel:WH'
DME_VERSIONS = {'9x1:DME_BDE_Continental:SDI9_1_981_2_7L_E4E': 'O300', '9x1:DME_BDE_Continental:SDI9_1_981_2_7L_EU4': 'O400', '9x1:DME_BDE_Continental:SDI9_1_981_2_7L_EU5': 'O200', '9x1:DME_BDE_Continental:SDI9_1_981_2_7L_ULEV': 'O100', '9x1:DME_BDE_Continental:SDI9_1_981_3_4L_E4E': 'P300', '9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU4': 'P400', '9x1:DME_BDE_Continental:SDI9_1_981_3_4L_ULEV': 'P100', '9x1:DME_BDE_Continental:SDI9_1_981_3_8L_E4E': 'W300', '9x1:DME_BDE_Continental:SDI9_1_981_3_8L_EU4': 'W400', '9x1:DME_BDE_Continental:SDI9_1_981_3_8L_EU6': 'W200', '9x1:DME_BDE_Continental:SDI9_1_981_3_8L_ULEV': 'W100', '9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5': 'P200'}
SHARED_DME_FACTORY_PROFILE = '9x1:DME_BDE_Continental:SDI9_1_Werksdatensatz'
SHARED_DME_UNPROGRAMMED_PROFILE = '9x1:DME_BDE_Continental:SDI_9_1_unprogrammiert'
DME_VERSIONS[SHARED_DME_FACTORY_PROFILE] = 'C000'
DME_VERSIONS[SHARED_DME_UNPROGRAMMED_PROFILE] = '0000'
READ_VERSIONS = {**DME_VERSIONS, **VERSIONS, SELECTOR_PROFILE: '0100',
    **{'9x1:Head_Unit:'+name:dsn for name,dsn in [('CDR31','2000'),('PCM31_A3','8000'),('PCM31_A3_2','0900'),('PCM31_A3_3','0901'),('PCM31_G1_und_9x1','7000')]},
    '9x1:TVTuner:TVTuner_A2':'5000','9x1:TVTuner:TVTuner_Delphi_A2':'6000',
    '9x1:Verstaerker:ASK':'1000','9x1:Verstaerker:BOSE':'7000'}
FAULT_ONLY_VERSIONS = {
    MODULE+'PDK_unprogrammiert':'FFFF',
    '9x1:Waehlhebel:WH_unprogrammiert':'FFFF',
    '9x1:Head_Unit:HeadUnit_unprogrammiert':'FFFF',
    '9x1:TVTuner:TVTuner_unprogrammiert':'FFFF',
    '9x1:Verstaerker:ASK_Verstaerker_unprogrammiert':'FFFE',
    '9x1:Verstaerker:BOSE_Verstaerker_unprogrammiert':'FFFF',
}
SOURCE_VERSIONS = {**READ_VERSIONS, **FAULT_ONLY_VERSIONS}
ECUS = {**{pid:1 for pid in DME_VERSIONS}, **{pid:2 for pid in VERSIONS},SELECTOR_PROFILE:68,
    **{pid:70 for pid in READ_VERSIONS if pid.startswith('9x1:Head_Unit:')},
    **{pid:75 for pid in READ_VERSIONS if pid.startswith('9x1:TVTuner:')},
    **{pid:65 for pid in READ_VERSIONS if pid.startswith('9x1:Verstaerker:')}}
ECUS.update({pid:2 if pid.startswith(MODULE) else 68 if ':Waehlhebel:' in pid else
    70 if ':Head_Unit:' in pid else 75 if ':TVTuner:' in pid else 65 for pid in FAULT_ONLY_VERSIONS})
SOURCE_GENERATIONS = {pid:'981' if pid in DME_VERSIONS else '9x1' if pid=='9x1:Head_Unit:PCM31_G1_und_9x1' else None for pid in READ_VERSIONS}
SOURCE_GENERATIONS[SHARED_DME_FACTORY_PROFILE] = None
SOURCE_GENERATIONS[SHARED_DME_UNPROGRAMMED_PROFILE] = None
SOURCE_GENERATIONS.update({pid:None for pid in FAULT_ONLY_VERSIONS})
PAIRS = {1:(0x7E0,0x7E8),70:(0x773,0x7DD),75:(0x76D,0x7D7),65:(0x76F,0x7D9),2: (0x71E,0x788), 68: (0x753,0x7BD)}
IDENTITY_REQUESTS = ('1A9F', '1A95', '1A9A', '1A87', '1A91', '1A90')
COMMAND_HASHES = {
    '1A9F': 'b903c2ab4ec976d519220f116971b440e15ef370b130b5249d3cb1c4f51df403',
    '1089': '83f735b9ada1cca9803c8c194fd993e1c38090c4a16e760df3e24da76b17cbe0',
}


def _source_profile(profile_id, ecu_id=2, *, generation='981'):
    if generation not in ('981','982'):
        raise ValueError('manufacturer-profile-unqualified')
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    root, manifest = _catalog(library, SOURCE_CATALOG)
    index = _document(root, manifest, 'all-units.json')
    rows = [v for u in index['units'] if u['generation'] == generation and u['ecuId'] == ecu_id
            for v in u['variants'] if v['profileId'] == profile_id]
    if len(rows) != 1:
        raise ValueError('pdk-source-profile-missing-or-ambiguous')
    return _document(root, manifest, rows[0]['file'])


def source_contract(profile, selected):
    pid = profile.get('profileId')
    ecu = ECUS.get(pid)
    if pid not in SOURCE_VERSIONS or profile.get('ecuIds') != [ecu] or profile.get('generation') != SOURCE_GENERATIONS[pid]:
        raise ValueError('manufacturer-profile-unqualified')
    requested_generation=profile.get('requestedGeneration','981')
    source = _source_profile(pid,ecu,generation=requested_generation)
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    language = (library / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP').read_bytes()
    for hx, cid in [('1089', 0x200007), ('1A9F', 0x200006)]:
        command = _lookup_command(language, cid)
        if (command.get('recordFound') is not True or command.get('decodedPayloadHex', '').upper() != '0102' + hx
                or command.get('decodedPayloadSha256') != COMMAND_HASHES[hx]):
            raise ValueError('pdk-source-command-unresolved')
    if (source.get('profileId') != pid or source.get('ecuIds') != [ecu]
            or requested_generation not in source.get('generations', [])):
        raise ValueError('pdk-source-profile-mismatch')
    definitions = source.get('versionRoutes', [])
    if len(definitions) != 1 or definitions[0].get('bindingStatus') != 'exact-source-version-rule-match':
        raise ValueError('pdk-source-version-unresolved')
    routes = definitions[0].get('routes', [])
    if len(routes) != 1:
        raise ValueError('pdk-source-version-unresolved')
    route = routes[0]
    selectors, matches = route.get('selectors', []), route.get('versionMatches', [])
    dme = pid in DME_VERSIONS
    expected_rule = SOURCE_VERSIONS[pid] if dme else '0x' + SOURCE_VERSIONS[pid][:2] + ' ' + SOURCE_VERSIONS[pid][2:]
    if (route.get('flagsWord') != 0 or len(selectors) != 1 or len(matches) != 1
            or matches[0].get('name') != pid.rsplit(':',1)[1]
            or matches[0].get('rules') != [expected_rule]
            or any(selectors[0].get(key) != value for key, value in {
                'leftWordSigned': 0x1A, 'rightWordSigned': 0x9F, 'byteOffsetSigned': 1,
                'bitOffsetByte': 0, 'formulaId': 0x80000007 if dme else 0x8000056E}.items())):
        raise ValueError('pdk-source-version-unresolved')
    diagnostics = source.get('diagnosticSources', [])
    if len(diagnostics) != 1 or diagnostics[0].get('bindingStatus') != 'exact-source-name-match':
        raise ValueError('pdk-source-route-unresolved')
    links = diagnostics[0].get('links', [])
    if len(links) != 1:
        raise ValueError('pdk-source-route-unresolved')
    comm = links[0]['communication']
    pairs = comm.get('pairs', [])
    tx,rx = PAIRS[ecu]
    if (comm.get('class') != 'static-source-confirmed' or comm.get('modeByte') != 0x10
            or len(pairs) != 1 or pairs[0].get('subtypeByte') != 3
            or pairs[0].get('storedTx') != tx<<5 or pairs[0].get('storedRx') != rx<<5
            or pairs[0].get('parameterIds') != [0x200007, 0x200006, 0x200007]):
        raise ValueError('pdk-source-route-unresolved')
    addresses = profile.get('addressCandidates', [])
    if (ecu == 2 and not addresses) or any(a.get('txId') != tx or a.get('rxId') != rx for a in addresses):
        raise ValueError('pdk-source-address-unresolved')
    plans = source.get('versionSelectorReadSources', [])
    occurrences = [o for plan in plans for r in plan.get('routes', [])
                   for group in r.get('requestGroups', []) if group.get('requestCandidateHex') == '1A9F'
                   for o in group.get('sourceOccurrences', [])]
    if (len(occurrences) != 1
            or occurrences[0]['sourceCommand'].get('decodedDescriptorSha256') != COMMAND_HASHES['1A9F']
            or occurrences[0]['sourceCommand'].get('rawDescriptorTrailingBytes') != 0
            or occurrences[0]['sourceCommand'].get('rawDescriptorComplete') is not True):
        raise ValueError('pdk-source-command-unresolved')
    identity_ids={1:IDENTITY_REQUESTS+('1A9E','1A92'),2:IDENTITY_REQUESTS,68:('1A9F','1A91','1A90'),70:('1A9F','1A95','1A91','1A90'),75:('1A9F','1A91','1A90'),65:('1A9F','1A87','1A91','1A92','1A90')}[ecu]
    if pid == SHARED_DME_UNPROGRAMMED_PROFILE:
        # This source state has no 1A95/1A9A records. Bind its own complete
        # supported identity fields; never request those of a normal dataset.
        identity_ids = ('1A9F','1A87','1A91','1A90','1A9E','1A92')
    if pid=='9x1:Head_Unit:HeadUnit_unprogrammiert':
        identity_ids=('1A9F','1A91','1A90')
    identity = source_records(profile, source, selected, identity_ids)
    result = {'sourceCatalogId': SOURCE_CATALOG, 'sourceQualification': 'pdk-dsn-and-full-identity' if ecu in (2,68) else 'kwp-source-version-and-identity',
            'identityRequests': identity, 'identityConstraints': {'dsnHex': SOURCE_VERSIONS[pid]},
            'identityLayoutResolvedByDsn': not dme,
            'txId': tx, 'rxId': rx, 'session': {'requestHex': '1089', 'positivePrefixHex': '5089'},
            'catalogProfileId': 'porsche-981-pdk-source' if ecu == 2 else 'porsche-981-selector-source' if ecu==68 else 'porsche-981-kwp-source', 'ecuId': ecu}
    if dme:
        from .x431_variant_selection import _load_version_language
        from scripts.x431_re.gag_lib import compact_express
        reader = _load_version_language(library / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP')
        result.update(applicationProtocol='kwp', sourceCommands=['1089','1A9F','1089'],
            versionSelectors=[dict(requestHex='1A9F', selector=selectors[0],
                formula=compact_express(reader.lookup_id('EXPRESS.GAG', selectors[0]['formulaId'])))],
            versionIdentityRequests=[], identityConstraints={'versionRules':[expected_rule]})
    return result


def source_records(profile, source, selected, identity_requests, prefix='pdk'):
    """Bind measurement and identity records to one immutable source profile."""
    fields = {field['sourceOffset']: (group['requestHex'], field['decodingDefinition'])
              for group in source['measurementGroups'] for field in group['fields']}
    for parameter in profile['parameters']:
        if parameter['id'] not in selected:
            continue
        if parameter.get('nameResolved') is not True:
            raise ValueError('manufacturer-parameter-name-unresolved')
        original = fields.get(parameter.get('definitionOffset'))
        record = parameter.get('record', {})
        if (not original or original[0] != parameter.get('requestHex')
                or any(original[1].get(key) != record.get(key) for key in ('byteOffset', 'bitOffset', 'enumText'))
                or original[1].get('formula', {}).get('text') != record.get('formula', {}).get('text')):
            raise ValueError(prefix+'-source-parameter-mismatch')
    identity = []
    for request in identity_requests:
        records = [r for r in profile['identity'] if r.get('requestHex') == request and r.get('decoderReady')]
        source_groups = [g for g in source['identityGroups'] if g['requestHex'] == request]
        if len(source_groups) != 1 or not records or len(records) != len(source_groups[0]['fields']):
            raise ValueError(prefix+'-source-identity-unresolved')
        for record, field in zip(records, source_groups[0]['fields']):
            definition = field['decodingDefinition']
            if (any(record.get(k) != definition.get(k) for k in ('byteOffset', 'bitOffset', 'enumText'))
                    or record.get('formula', {}).get('text') != definition.get('formula', {}).get('text')):
                raise ValueError(prefix+'-source-identity-mismatch')
        identity.append({'field': request, 'requestHex': request,
                         **({'record': records[0]} if len(records)==1 else {'records': records})})
    return identity


def qualify_client(client, contract, profile_id, deadline, guard, *, resolve_version=None):
    """Fresh source order: 1089, DSN read, 1089, then remaining identity reads."""
    identity = {}

    def request(hx):
        guard()
        result = client.request(hx, min(deadline, time.monotonic() + 15))
        payload = result.get('payload_hex', '')
        if (not result.get('ok') or not isinstance(payload, str)
                or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){2,513}', payload)):
            raise ElmError(result.get('error') or 'identity-request-failed')
        return payload.upper()

    for hx in ('1089', '1A9F', '1089'):
        pdu = request(hx)
        if hx == '1089':
            if not pdu.startswith('5089'):
                raise ElmError('session-subfunction-mismatch')
        else:
            if resolve_version is not None and pdu.startswith('5A9F'):
                contract, profile_id = resolve_version(pdu[4:8])
            if not pdu.startswith('5A9F') or pdu[4:8] != contract['identityConstraints']['dsnHex']:
                raise ElmError('identity-mismatch')
            identity[hx] = pdu
    for item in contract['identityRequests']:
        hx = item['requestHex']
        pdu = identity.get(hx) or request(hx)
        decoded = decode_application_response(item['record'], bytes.fromhex(pdu), mode='pdu')
        if not decoded.get('ok'):
            raise ElmError('identity-decode-failed')
        body = bytes.fromhex(pdu)[2:]
        # FF FF is the exact original version rule of these named states,
        # not a substitute for blank hardware/serial/VIN identity fields.
        declared_state_version=(hx=='1A9F' and len(body)==2
            and profile_id in FAULT_ONLY_VERSIONS
            and body.hex().upper()==FAULT_ONLY_VERSIONS[profile_id]
            and contract['identityConstraints'].get('dsnHex')==FAULT_ONLY_VERSIONS[profile_id])
        if not body or all(b == 0 for b in body) or all(b == 255 for b in body) and not declared_state_version:
            raise ElmError('identity-decode-failed')
        if hx == '1A90' and not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', str(decoded.get('text', ''))):
            raise ElmError('identity-decode-failed')
        identity[hx] = pdu
    return identity, {'observedProfileMatch': True, 'generation': contract.get('generation','981'), 'ecuId': contract['ecuId'],
                      'variantLink': {'profile_id': profile_id}, 'sourceCatalogId': SOURCE_CATALOG,
                      'freshRead': True, 'liveVerified': False, 'writePayload': None}
