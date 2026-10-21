"""Read-only BOSE 002 bridge from its own PIWIS layer and X431 fields.

The retained PIWIS variant explicitly references BV_SoundSysteUDS. That
layer declares its own CAN_D_Gx normal 11-bit pair, independently of the
older X431 SYS_DATA entry. No fault or write recipe is supplied here.
"""
from pathlib import Path
import os

from .pdk_read import SOURCE_CATALOG, _source_profile, source_records
from .x431_command_origin import _lookup_command

PROFILE = '9x1:BV_SoundSysteUDS:EV_AMPMst16C4Gen2BOSE_002'
IDENTITY_REQUESTS = ('22F189', '22F191', '22F190', '22F187', '22F1A3', '22F18C', '22F179')
SELECTOR_KEYS = ('leftWordSigned', 'rightWordSigned', 'byteOffsetSigned', 'bitOffsetByte', 'formulaId')
SELECTORS = ((0x22, -3682, 2, 0, 0x8000021D), (0x22, -3678, 2, 0, 0x8000000A))
RULE = 'EV_AMPMst16C4Gen2BOSE#002'
PIWIS_SOURCES = {
    '0.0.0@BV_SoundSysteUDS.bv.db': '1e3b6fb7c4fb24067fe81ae53c7dbf08ae32038df2102495c8a7a4357fb08ebc',
    'Diagnose_CAN_981.odx-v': 'a414ae79127246604a78cbefdf1799f88466b2f9da1a60d4282a58a452bf8f8f',
    '0.0.0@FunctionalGroupUDS.fg.db': '85aab0c95470cc4e54424570e8c28d1b35d2b0c9fa2b08543b3b22ae2d7279b6',
}


def source_contract(profile, selected):
    generation = profile.get('requestedGeneration', '981')
    if (profile.get('profileId') != PROFILE or profile.get('ecuIds') != [65]
            or type(profile.get('selectedEcuId', 65)) is not int or profile.get('selectedEcuId', 65) != 65
            or profile.get('generation') is not None or generation not in ('981', '982')):
        raise ValueError('manufacturer-profile-unqualified')
    source = _source_profile(PROFILE, 65, generation=generation)
    if (source.get('profileId') != PROFILE or source.get('module') != 'BV_SoundSysteUDS'
            or source.get('sourceGeneration') is not None or source.get('ecuIds') != [65]
            or generation not in source.get('generations', [])):
        raise ValueError('bose-source-profile-mismatch')
    refs = source.get('versionRoutes', [])
    if (len(refs) != 1 or refs[0].get('bindingStatus') != 'exact-source-version-rule-match'
            or refs[0].get('sourceSha256') != '55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f'
            or refs[0].get('requestedEcuId') != 65 or len(refs[0].get('routes', [])) != 1):
        raise ValueError('bose-source-version-unresolved')
    route = refs[0]['routes'][0]
    selectors = route.get('selectors', [])
    matches = route.get('versionMatches', [])
    if (route.get('routeId') != 65 or route.get('routeIndexOffset') != 105663
            or route.get('flagsWord') != 1 or route.get('module') != 'BV_SoundSysteUDS'
            or [tuple(s.get(k) for k in SELECTOR_KEYS) for s in selectors] != list(SELECTORS)
            or len(matches) != 1 or matches[0].get('name') != PROFILE.rsplit(':', 1)[1]
            or matches[0].get('versionIndex') != 2 or matches[0].get('ruleOffset') != 118740
            or matches[0].get('rules') != [RULE]):
        raise ValueError('bose-source-version-unresolved')
    refs = source.get('diagnosticSources', [])
    if (len(refs) != 1 or refs[0].get('bindingStatus') != 'source-name-missing'
            or refs[0].get('requestedEcuId') != 65 or refs[0].get('links') != []
            or refs[0].get('sourceSha256') != '18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'):
        raise ValueError('bose-source-route-mismatch')
    identity = source_records(profile, source, selected, IDENTITY_REQUESTS, prefix='bose')
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    path = library / '.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
    language = path.read_bytes()
    commands = ((0x20000A, '00021003', '90a95828ebc1916d89ff0ef9249c8ab6542bc065c81255004ee21c7fc074592c'),
                (0x200002, '010322F19E', 'af7e9fd8f0e0461f31027ab5baa5f8f2e9bf19be5b62d91575fc2d34117d828e'),
                (0x200001, '010322F1A2', 'd0d052a6db998f9edc31a725b5f6bbded719920a02b94348936cd81eb60c774c'))
    for cid, encoded, digest in commands:
        record = _lookup_command(language, cid)
        if (record.get('recordFound') is not True or record.get('decodedPayloadHex', '').upper() != encoded
                or record.get('decodedPayloadSha256') != digest):
            raise ValueError('bose-source-command-unresolved')
    from .x431_variant_selection import _load_version_language
    from scripts.x431_re.gag_lib import compact_express
    reader = _load_version_language(path)
    fields = [dict(requestHex=f'{s["leftWordSigned"]:02X}{s["rightWordSigned"] & 0xFFFF:04X}',
                   selector=s, formula=compact_express(reader.lookup_id('EXPRESS.GAG', s['formulaId'])))
              for s in selectors]
    if any(not f['formula'] or not f['formula'].get('text') for f in fields):
        raise ValueError('bose-source-selector-unresolved')
    return dict(sourceCatalogId=SOURCE_CATALOG, sourceQualification='uds-source-version-and-identity',
        catalogProfileId='porsche-981-uds-source', ecuId=65, txId=0x76F, rxId=0x7D9, applicationProtocol='uds',
        identityRequests=identity, identityConstraints={'versionRules': [RULE]}, versionSelectors=fields,
        versionIdentityRequests=['22F19E', '22F1A2'], sourceCommands=['1003', '22F19E', '22F1A2'],
        session={'requestHex': '1003', 'positivePrefixHex': '5003'},
        addressEvidence='pinned-piwis-bose002-normal-11bit-table',
        sourceBridge={'project': '9x1', 'version': '2830.1.0', 'sources': dict(PIWIS_SOURCES)})
