"""Read-only SWSG bridge: PIWIS routes, immutable X431 fields and DSNs.

PIWIS 9x1 2830.1.0 declares both normal 11-bit ISO-TP pairs in the
SWSG base variant. Its normal variants have matching X431 names and unique
six-character DSN rules. No route is inferred from Master/Slave spelling.
The unprogrammed rule also needs status fields and is outside this bridge.

Original declarations and same-library Ghidra/x64 checks are retained in
.local/obd-piwis-live-source-20261018/. Hardware acceptance remains separate.
"""
from pathlib import Path
import os

from .pdk_read import SOURCE_CATALOG, _source_profile, source_records
from .x431_command_origin import _lookup_command


VERSIONS = {
    '9x1:SWSG:SWSG_' + role + '_' + equipment + '_ab_' + revision: prefix + suffix
    for role, role_prefix in (('Master', '1'), ('Slave', '0'))
    for equipment, prefix in (('mit_AFS', role_prefix + '00'), ('ohne_AFS', role_prefix + '05'))
    for revision, suffix in (('1310', '010'), ('VR06', '006'), ('VR15', '015'))
}
TARGETS = (47, 48)
PAIRS = {47: (0x725, 0x78F), 48: (0x754, 0x7BE)}
IDENTITY_REQUESTS = ('22F1A2', '22F190', '22F1A3', '22F187', '220906', '22F189')
PIWIS_SOURCES = {
    '0.0.0@SWSG.bv.db': 'a39358f6958af044d4585f0093dd1832c11d192e45bb8f9dddd6a8b4122cf8c7',
    'Diagnose_CAN_981.odx-v': 'a414ae79127246604a78cbefdf1799f88466b2f9da1a60d4282a58a452bf8f8f',
    '0.0.0@FunctionalGroupUDS.fg.db': '85aab0c95470cc4e54424570e8c28d1b35d2b0c9fa2b08543b3b22ae2d7279b6',
}
SELECTOR_KEYS = ('leftWordSigned', 'rightWordSigned', 'byteOffsetSigned', 'bitOffsetByte', 'formulaId')
SELECTORS = [(0x22, -3678, 2, 0, 0x80000002),
             (0x22, -3617, 2, 2, 0xA0000001), (0x22, -3617, 2, 6, 0xA0000001)]
DSN_FORMULA = ('IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=48,'
               'Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;')


def source_contract(profile, selected):
    pid = profile.get('profileId')
    ecu = profile.get('selectedEcuId')
    generation = profile.get('requestedGeneration', '981')
    if (pid not in VERSIONS or type(ecu) is not int or ecu not in TARGETS
            or profile.get('ecuIds') != list(TARGETS) or profile.get('generation') is not None
            or generation not in ('981', '982')):
        raise ValueError('manufacturer-profile-unqualified')
    source = _source_profile(pid, ecu, generation=generation)
    if (source.get('profileId') != pid or source.get('module') != 'SWSG'
            or source.get('ecuIds') != list(TARGETS) or source.get('sourceGeneration') is not None
            or generation not in source.get('generations', [])):
        raise ValueError('swsg-source-profile-mismatch')

    declarations = [r for r in source.get('versionRoutes', []) if r.get('requestedEcuId') == ecu]
    if (len(declarations) != 1
            or declarations[0].get('sourceSha256') != '55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f'
            or declarations[0].get('bindingStatus') != 'exact-source-version-rule-match'
            or len(declarations[0].get('routes', [])) != 1):
        raise ValueError('swsg-source-version-unresolved')
    route = declarations[0]['routes'][0]
    matches = route.get('versionMatches', [])
    if (route.get('routeId') != ecu or route.get('routeIndexOffset') != {47: 106234, 48: 106220}[ecu]
            or route.get('flagsWord') != 0 or route.get('module') != 'SWSG'
            or [tuple(s.get(k) for k in SELECTOR_KEYS) for s in route.get('selectors', [])] != SELECTORS
            or len(matches) != 1 or matches[0].get('name') != pid.rsplit(':', 1)[1]
            or matches[0].get('rules') != [VERSIONS[pid]]):
        raise ValueError('swsg-source-version-unresolved')

    # This is an independent PIWIS route bridge, not an X431 SYS_DATA link.
    # A changed source declaration requires review rather than silent override.
    declarations = [r for r in source.get('diagnosticSources', []) if r.get('requestedEcuId') == ecu]
    if (len(declarations) != 1
            or declarations[0].get('sourceSha256') != '18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'
            or declarations[0].get('bindingStatus') != 'source-name-missing'
            or declarations[0].get('links') != []):
        raise ValueError('swsg-source-route-mismatch')

    identity = source_records(profile, source, selected, IDENTITY_REQUESTS, prefix='swsg')
    if (len(identity[0].get('records', [identity[0].get('record')])) != 1
            or identity[0].get('record', {}).get('formula', {}).get('text') != DSN_FORMULA):
        raise ValueError('swsg-source-version-formula-mismatch')

    # PIWIS normal ExtendedSession selects key 3 from TAB_Session; SID is 0x10.
    # The source's normal DSN rules use only their first selector. Do not issue
    # the unresolved status request needed by its separate unprogrammed rule.
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    language = (library / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP').read_bytes()
    commands = ((0x20000A, '00021003', '90a95828ebc1916d89ff0ef9249c8ab6542bc065c81255004ee21c7fc074592c'),
                (0x200001, '010322F1A2', 'd0d052a6db998f9edc31a725b5f6bbded719920a02b94348936cd81eb60c774c'))
    for cid, encoded, digest in commands:
        command = _lookup_command(language, cid)
        if (command.get('recordFound') is not True or command.get('decodedPayloadHex', '').upper() != encoded
                or command.get('decodedPayloadSha256') != digest):
            raise ValueError('swsg-source-command-unresolved')

    tx, rx = PAIRS[ecu]
    return dict(sourceCatalogId=SOURCE_CATALOG, sourceQualification='uds-source-version-and-identity',
        catalogProfileId='porsche-981-uds-source', ecuId=ecu, txId=tx, rxId=rx, applicationProtocol='uds',
        identityRequests=identity, identityConstraints={'dsnText': VERSIONS[pid]},
        sourceCommands=['1003', '22F1A2'], session={'requestHex': '1003', 'positivePrefixHex': '5003'},
        addressEvidence='pinned-piwis-swsg-normal-11bit-tables',
        sourceBridge={'project': '9x1', 'version': '2830.1.0', 'sources': dict(PIWIS_SOURCES)})
