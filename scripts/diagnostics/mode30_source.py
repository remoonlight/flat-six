"""Seat/door read definitions bound to the immutable X431 source catalog.

The source's later static pair is a declaration, not the active TP address.
Only the C0 offer is configured here; D0 negotiates the actual channel at run
time. Unknown/unprogrammed overlaps remain unavailable for normal reads.
"""
import os
from pathlib import Path

from .pdk_read import SOURCE_CATALOG, _source_profile, source_records
from .x431_command_origin import _lookup_command
from .x431_variant_selection import _load_version_language
from scripts.x431_re.gag_lib import compact_express

SEAT_VERSIONS = tuple('9x1:Sitzsteuergeraet:'+n for n in ('Sitz_A2','Sitz_A2_1_9x1','Sitz_A2_9x1'))
DOOR_VERSIONS = tuple('9x1:TUER_VORNE:'+n for n in ('Tuer_Vorne_A2_1_9x1','Tuer_Vorne_A2_2'))
TARGETS = {**{p:(76,78) for p in SEAT_VERSIONS}, **{p:(30,31) for p in DOOR_VERSIONS}}
FAULT_ONLY_TARGETS = {
    '9x1:Sitzsteuergeraet:Unprogrammiertes_Sitz_SG':(76,78),
    '9x1:TUER_VORNE:Unprogrammiertes_Tuer_Vorne_SG':(30,31),
}
SOURCE_GENERATIONS = {p:('9x1' if p.endswith('_9x1') else None) for p in TARGETS}
SOURCE_GENERATIONS.update({p:None for p in FAULT_ONLY_TARGETS})
OFFERS = {
    76:(0x226, '26C00010020301', 0x3001B2, 0x519, 0x302, 77343, 106248),
    78:(0x235, '35C00010030301', 0x3001B3, 0x4EF, 0x303, 77632, 106274),
    30:(0x222, '22C00010040301', 0x3001B4, 0x4C2, 0x304, 69732, 106373),
    31:(0x223, '23C00010050301', 0x3001B5, 0x4C3, 0x304, 69993, 106393),
}
SELECTOR_KEYS = ('leftWordSigned','rightWordSigned','byteOffsetSigned','bitOffsetByte','formulaId')


def _route(source, ecu, pid):
    rows = [r for r in source.get('versionRoutes', []) if r.get('requestedEcuId') == ecu]
    if (len(rows) != 1 or rows[0].get('sourceSha256') != '55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f'
            or rows[0].get('bindingStatus') != 'exact-source-version-rule-match'
            or len(rows[0].get('routes', [])) != 1):
        raise ValueError('mode30-source-version-unresolved')
    route = rows[0]['routes'][0]
    selectors = [(0x22,-3678,2,0,0x80000002)]
    if ecu in (76,78):selectors.append((0x22,-3703,2,0,0x80000007))
    selectors.append((0x22,-3617,2,0,0xA0000001))
    matches = route.get('versionMatches', [])
    if (route.get('routeIndexOffset') != OFFERS[ecu][6] or route.get('flagsWord') != 0
            or [tuple(s.get(k) for k in SELECTOR_KEYS) for s in route.get('selectors', [])] != selectors
            or len(matches) != 1 or matches[0].get('name') != pid.rsplit(':',1)[1]
            or not matches[0].get('rules')):
        raise ValueError('mode30-source-version-unresolved')
    return route


def source_contract(profile, selected):
    pid = profile.get('profileId'); targets = TARGETS.get(pid, FAULT_ONLY_TARGETS.get(pid, ()))
    ecu = profile.get('selectedEcuId'); generation = profile.get('requestedGeneration','981')
    if (type(ecu) is not int or ecu not in targets or profile.get('ecuIds') != list(targets)
            or profile.get('generation') != SOURCE_GENERATIONS.get(pid) or generation not in ('981','982')):
        raise ValueError('manufacturer-profile-unqualified')
    source = _source_profile(pid, ecu, generation=generation)
    if source.get('profileId') != pid or source.get('ecuIds') != list(targets) or generation not in source.get('generations', []):
        raise ValueError('mode30-source-profile-mismatch')
    route = _route(source, ecu, pid)
    rx, offer, cid, declared_tx, declared_rx, offset, _ = OFFERS[ecu]
    rows = [d for d in source.get('diagnosticSources', []) if d.get('requestedEcuId') == ecu]
    if (len(rows) != 1 or rows[0].get('sourceSha256') != '18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'
            or rows[0].get('bindingStatus') != 'exact-source-name-match' or len(rows[0].get('links', [])) != 1):
        raise ValueError('mode30-source-route-unresolved')
    link = rows[0]['links'][0]; comm = link.get('communication', {})
    seat = ecu in (76,78)
    pairs = [(0x200,rx,1,[cid]),(declared_tx,declared_rx,1,[0x1A82 if seat else 0x1A71]),
        (declared_tx,declared_rx,4,[0x1A72,0x1A73,0x1A74,0x1A79] if seat else [0x1A72,0x1A74,0x1A79])]
    if (link.get('communicationSourceOffset') != offset or comm.get('class') != 'static-source-confirmed'
            or comm.get('modeByte') != 0x30 or len(comm.get('pairs', [])) != 3
            or any((p.get('storedTx'),p.get('storedRx'),p.get('subtypeByte'),p.get('parameterIds')) != (tx<<5,rr<<5,sub,ids)
                for p,(tx,rr,sub,ids) in zip(comm['pairs'],pairs))):
        raise ValueError('mode30-source-route-unresolved')
    commands = [(cid,'0107'+offer),(0x1A82 if seat else 0x1A71,'0106A00F00FF00FF'),
        (0x1A72,'010322F1A2'),*([(0x1A73,'010322F189')] if seat else []),
        (0x1A74,'010322F1DF'),(0x1A79,'01021089')]
    library = Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    path = library/'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP'
    reader = _load_version_language(path)
    language = reader.raw
    for command_id,hx in commands:
        decoded = _lookup_command(language, command_id)
        if decoded.get('recordFound') is not True or decoded.get('decodedPayloadHex','').upper() != hx:
            raise ValueError('mode30-source-command-unresolved')
    identity_ids = ['22F187','22F1A2','22F189','22F1A3','22F18C']
    if pid in FAULT_ONLY_TARGETS:
        identity_ids = (['22F1A2','22F187','22F189','22F1A3','22F18C'] if seat else
            ['22F1A2','22F189','22F1A3','22F18C','22F187'])
    identity = source_records(profile, source, selected, identity_ids, prefix='mode30')
    selectors = []
    for s in route['selectors']:
        selectors.append(dict(requestHex=f"{s['leftWordSigned']:02X}{s['rightWordSigned']&65535:04X}",
            selector=s, formula=compact_express(reader.lookup_id('EXPRESS.GAG', s['formulaId']))))
    other_pid = ('9x1:Sitzsteuergeraet:Unprogrammiertes_Sitz_SG' if seat else '9x1:TUER_VORNE:Unprogrammiertes_Tuer_Vorne_SG')
    other = _source_profile(other_pid, ecu, generation=generation)
    other_route = _route(other, ecu, other_pid)
    conflict = ['##ja' if seat else '#ja']
    if other_route['versionMatches'][0]['rules'] != conflict:
        raise ValueError('mode30-source-version-unresolved')
    if pid in FAULT_ONLY_TARGETS:
        if route['versionMatches'][0]['rules'] != conflict:
            raise ValueError('mode30-source-version-unresolved')
        conflict = []
    return dict(sourceCatalogId=SOURCE_CATALOG,sourceQualification='uds-source-version-and-identity',
        catalogProfileId='porsche-981-tp20-source',ecuId=ecu,txId=0x200,rxId=rx,applicationProtocol='kwp',
        identityRequests=identity,identityConstraints=dict(versionRules=route['versionMatches'][0]['rules'],conflictingVersionRules=conflict),
        versionSelectors=selectors,versionIdentityRequests=['22F1DF'],
        sourceCommands=[hx[4:] for _,hx in commands[2:]],session=dict(requestHex='1089',positivePrefixHex='5089'),
        transportAddressing=dict(kind='porsche-tp20',offerHex=offer),addressEvidence='source-C0-offer-and-fresh-D0-channel')
