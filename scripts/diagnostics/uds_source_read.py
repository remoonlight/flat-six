"""Named 981 UDS read recipes from the immutable source catalog.

Only source-defined versions, CAN pairs, descriptors and identity fields are
accepted. Source filter words use the same left-five-bit standard-ID encoding
as the named capture readers. This is software binding, not head/car acceptance.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import time

from .elm import ElmError
from .pdk_read import SOURCE_CATALOG, _source_profile, source_records
from .response_values import decode_application_response
from .x431_command_origin import _lookup_command
from .swsg_source import VERSIONS as SWSG_VERSIONS, TARGETS as SWSG_TARGETS
from .headlamp_source import VERSION_RULES as LED_VERSIONS
from .bose_source import PROFILE as BOSE_PROFILE

GATEWAY_VERSIONS = ('9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A3', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A4', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A4_1', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A4_2', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5_1', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5_2', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5_3', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_1', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_2', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_3', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_4', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_0', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_2_China', '9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1')
MODE50_VERSIONS = {f'9x1:SCHEINWERFER:SCHEINWERFER_{i}':(88,None) for i in range(1,5)}
MEDIA_VERSIONS = {
    BOSE_PROFILE: 65,
    '9x1:BV_InforContrUnit1UDSDesay:EV_MU_ChinaPCM_DESAY_001':70,
    **{'9x1:BV_InforContrUnit1UDS:'+name:70 for name in (
        'EV_MUHig6C3Gen2AW7_001','EV_MUHig6C3Gen2HBAS_001','EV_MUStd6C3DELP_001')},
    **{'9x1:BV_SoundSysteUDS:'+name:65 for name in (
        'EV_AMPMst16C1Gen2ASK_001','EV_AMPMst16C4Gen2BOSE_001')},
    '9x1:BV_TVTunerUDS:EV_TVMELC_001':75,
}
DME_UDS_VERSIONS = {'9x1:BV_ECM_UDS:'+name:(1,None) for name in ('Boxster_B4T_2_0L_EU6AG_mOPF', 'Boxster_B4T_2_0L_RDW', 'Boxster_B4T_2_0L_USA', 'Boxster_B4T_2_5L_EU6AG_mOPF', 'Boxster_B4T_2_5L_GTS_EU6AG_mOPF', 'Boxster_B4T_2_5L_GTS_RDW', 'Boxster_B4T_2_5L_GTS_USA', 'Boxster_B4T_2_5L_RDW', 'Boxster_B4T_2_5L_USA')}
DME_ASAM_VERSIONS = {'9x1:BV_EnginContrModul1UDS:'+name:(1,None) for name in (
    'EV_ECM40FSI011982906033AM_001','EV_ECM40TFS011982906033G_002',
    'EV_ECM40TFS011982906033J_001','EV_ECM40TFS011982906033L_001',
    'EV_ECM40TFS011982906034AC_001','EV_ECM40TFS011982906034G_001',
    'EV_ECM40TFS011982906034J_002','EV_ECM40TFS011982906034L_001')}
DME_GT4_RS_PROFILE = '9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033A_001'
# Target rules are linked to original PIWIS982 labels and Porsche SY193/24.
# Other declarations in these shared datasets remain outside this read scope.
DME_TARGET_RULES = {
    '9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033AL_001': (
        'EV_ECM40FSI0119GT906033AM#001',
        'EV_ECM40FSI9GT906033AM#A01',
    ),
    '9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033A_001': (
        'EV_ECM40FSI0119GT906033L#001',
        'EV_ECM40FSI0119GT906033R#001',
        'EV_ECM40FSI9GT906033R#A01',
        'EV_ECM40FSI0119GT906033AH#001',
        'EV_ECM40FSI9GT906033AH#A01',
        'EV_ECM40FSI0119GT906033AR#001',
        'EV_ECM40FSI9GT906033AR#A01',
    ),
    '9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033_001': (
        'EV_ECM40FSI0119GT906033N#001',
        'EV_ECM40FSI9GT906033N#A01',
        'EV_ECM40FSI0119GT906033K#001',
        'EV_ECM40FSI9GT906033K#A01',
        'EV_ECM40FSI0119GT906033M#001',
        'EV_ECM40FSI9GT906033M#A01',
        'EV_ECM40FSI0119GT906033P#001',
        'EV_ECM40FSI9GT906033P#A01',
        'EV_ECM40FSI0119GT906033AA#001',
        'EV_ECM40FSI0119GT906033Q#001',
        'EV_ECM40FSI0119GT906033S#001',
        'EV_ECM40FSI0119GT906033T#001',
        'EV_ECM40FSI9GT906033AA#A01',
        'EV_ECM40FSI9GT906033Q#A01',
        'EV_ECM40FSI9GT906033T#A01',
        'EV_ECM40FSI0119GT906033AG#001',
        'EV_ECM40FSI0119GT906033AK#001',
        'EV_ECM40FSI0119GT906033AJ#001',
        'EV_ECM40FSI9GT906033AG#A01',
        'EV_ECM40FSI9GT906033AJ#A01',
        'EV_ECM40FSI9GT906033AK#A01',
    ),
}
DME_ASAM_VERSIONS.update({pid:(1,None) for pid in DME_TARGET_RULES})
DME_982_VERSIONS = frozenset((*DME_UDS_VERSIONS,*DME_ASAM_VERSIONS))
DME_SHARED_ASAM_PROFILE = '9x1:BV_EnginContrModul1UDS:EV_Unprogrammiertes_SG'
DME_SOURCE_UDS_VERSIONS = DME_982_VERSIONS | {DME_SHARED_ASAM_PROFILE}
VERSIONS = {
    **DME_UDS_VERSIONS,
    **DME_ASAM_VERSIONS,
    DME_SHARED_ASAM_PROFILE: (1,None),
    **MODE50_VERSIONS,
    **{pid:(ecu,None) for pid,ecu in MEDIA_VERSIONS.items()},
    **{pid:(9,None) for pid in GATEWAY_VERSIONS},
    **{'9x1:PSM:'+name: (5, dsn) for name,dsn in [
        ('PSM_a1','001000'), ('PSM_a2','002000'), ('PSM_a2_1','003000'), ('PSM_a2_2','004000'),
        ('PSM_a2_3','005000'), ('PSM_a2_5','007000'), ('PSM_a2_6','008000'), ('PSM_a2_7_CS','009000')]},
    **{'9x1:BCM_vorne:BCM_vorne_9x1_'+name: (32, dsn) for name,dsn in [
        ('Basis_GT4','050018'), ('Basis_GT5','050031'), ('Basis_VR10','050010'), ('Basis_VR12','050012'),
        ('Max_GT4','055018'), ('Max_GT5','055031'), ('Max_VR10','055010'), ('Max_VR12','055012')]},
    **{'9x1:BCM_hinten:BCM_hinten_9x1_'+name: (35, dsn) for name,dsn in [
        ('GT4','050018'), ('GT5','050031'), ('VR10','050010'), ('VR12','050012')]},
    **{'9x1:Lenkstockschalter:Kombilenkstockmodul_'+name: (54,dsn) for name,dsn in [('A2_4','000033'),('A2_5','000034'),('A2_6','000035')]},
    **{'9x1:PDC:PDC_'+name: (18,dsn) for name,dsn in [('4_Kanal_A2_4','040003'),('4_Kanal_A2_5','040005'),('4_Kanal_A2_7','040007'),('8_Kanal_A2_4','080003'),('8_Kanal_A2_5','080005'),('8_Kanal_A2_7','080007')]},
    '9x1:EPB:Parkbremse_a7': (50,'000006'),
    '9x1:RDK:Reifendruckkontrolle_A2_4': (55,'200001'),
    '9x1:RDK:RDK_unprogrammiertes_SG': (55,None),
    **{'9x1:RDK3:'+name:(55,None) for name in ('RDK3_004','RDK3_005','RDK3_006','RDK3_007','RDK3_CS','RDK3_GT')},
    **{'9x1:Kombiinstrument_MM:Kombiinstrument_'+name: (8,dsn) for name,dsn in [
        ('A2_6','000010'),('A2_7','000012'),('A2_8','000013'),('A2_9','000015'),
        ('A3_1','000030'),('A3_2','000031'),('A3_3','000032'),('A3_4','000033'),('A3_5','000034'),('A3_6','000035')]},
    **{'9x1:Airbag:'+name:(4,None) for name in ['Airbag_A2_4_9x1','Airbag_A2_5_9x1','Airbag_A2_6_9x1','Airbag_A2_7','Airbag_A2_8']},
    **{'9x1:BKE:'+name:(7,None) for name in ['BKE_1_Zone_A2_1','BKE_1_Zone_A2_2','BKE_1_Zone_A2_3','BKE_1_Zone_A2_4','BKE_2_Zonen_A2_1','BKE_2_Zonen_A2_2','BKE_2_Zonen_A2_3','BKE_2_Zonen_A2_4','BKE_2_Zonen_A2_5','Heizung_1_Zone_A2_3','Heizung_1_Zone_A2_4','Klima_2_Zonen_A2_10']},
    **{'9x1:PASM_PADM:'+name:(19,None) for name in ['PADM_9x1_a2_1','PADM_9x1_a2_2','PADM_9x1_a2_2_GT','PASM_9x1_a2_1','PASM_9x1_a2_2','PASM_9x1_a2_2_GT','PASM_PADM_9x1_a2_1','PASM_PADM_9x1_a2_2','PASM_PADM_9x1_a2_2_GT']},
    **{'9x1:Steering_Control_Unit:'+name:(69,None) for name in ['EPS_A2_2','EPS_A2_3','EPS_A2_4','EPS_A2_5','EPS_A2_GT']},
    **{'9x1:Zusatzinstrument:'+name:(29,None) for name in ['Analoge_Uhr_A2','Analoge_Uhr_A2_1','Analoge_Uhr_A2_2','Analoge_Uhr_A2_3','Zusatzinstrument_A2_0','Zusatzinstrument_A2_1','Zusatzinstrument_A2_2','Zusatzinstrument_A2_3']},
    **{'9x1:Kombiinstrument_MM:Kombiinstrument_'+name:(8,None) for name in ['A2_2','A2_3','A2_4','A2_5']},
    **{'9x1:ACC3:ACC3_'+name:(64,None) for name in ['A2','A3','A4','A5','A6','A7','A8']},
    **{'9x1:Frontkamera:'+name:(74,None) for name in ['Frontkamera_0004','Frontkamera_0005','Frontkamera_0006','Frontkamera_1','Frontkamera_2','Frontkamera_P005','Frontkamera_P006','Frontkamera_PP06']},
    **{'9x1:Rueckfahrkamera_II:'+name:(81,None) for name in ['RFK_Compact_A1_1','RFK_Compact_A2_1','RFK_Mid_A1_1']},
    '9x1:ERA_GLONASS:EV_ERAGLONAPeike_001':(165,None),
    '9x1:ERA_GLONASS_VFA:EV_ERAGLONAVFA_001':(165,None),
    '9x1:Verdeck_Synchronisation:Verdeck_Synchronisations_SG_A1_0':(39,None),
    **{'9x1:BV_ContrUnitLaneChangAssis2UDS:'+name:(95,None) for name in (
        'EV_RearRadarS1BoschPO416_001','EV_RearRadarS1BoschPO67x_001',
        'EV_RearRadarS2BoschPO416_001','EV_RearRadarS2BoschPO67x_001')},
    **{'9x1:BV_ActuaForStrucBorneSoundUDS:'+name:(87,None) for name in (
        'Actuator_For_Exterior_Noise_UDS','EV_SoundActuaGen2AU64X_003')},
    '9x1:BCM_vorne:BCM_vorne_unprogrammiertes_SG': (32, '900000'),
    '9x1:BCM_hinten:BCM_hinten_uncodiertes_SG': (35, '000000'),
}
VERSIONS.update({pid: (SWSG_TARGETS[0], dsn) for pid, dsn in SWSG_VERSIONS.items()})
SHARED_TARGETS = {pid:(95,96) for pid in VERSIONS if pid.startswith('9x1:BV_ContrUnitLaneChangAssis2UDS:')}
SHARED_TARGETS.update({pid: SWSG_TARGETS for pid in SWSG_VERSIONS})
PAIRS = {9:(0x710,0x77A),64:(0x757,0x7C1),74:(0x74F,0x7B9),81:(0x769,0x7D3),165:(0x767,0x7D1),39:(0x72D,0x797),4:(0x715,0x77F),7:(0x746,0x7B0),19:(0x755,0x7BF),69:(0x712,0x77C),29:(0x729,0x793),8: (0x714,0x77E), 5: (0x713,0x77D), 32: (0x70E,0x778), 35: (0x70D,0x777), 54: (0x70C,0x776), 18: (0x70A,0x774), 50: (0x752,0x7BC), 55: (0x70B,0x775)}
PAIRS[1]=(0x7E0,0x7E8)
PAIRS[88]=(0x754,0x7BE)
PAIRS.update({70:(0x773,0x7DD),65:(0x76F,0x7D9),75:(0x76D,0x7D7)})
COMMANDS = (
    (0x1003A7, '00023E00', '2742166ff923120848916368b1abbea5a04b39ffa0dbc344ab6439c38afa3fc6'),
    (0x200001, '010322F1A2', 'd0d052a6db998f9edc31a725b5f6bbded719920a02b94348936cd81eb60c774c'),
    (0x200003, '010322F1DF', '12fe2247842cd932cf0ecee288bc03c8b5dc64cb4a42094f81dfd02c4da5cf69'),
    (0x20000A, '00021003', '90a95828ebc1916d89ff0ef9249c8ab6542bc065c81255004ee21c7fc074592c'),
    (0x200002, '010322F19E', 'af7e9fd8f0e0461f31027ab5baa5f8f2e9bf19be5b62d91575fc2d34117d828e'),
)

FAULT_ONLY_VERSIONS = {
    **{pid: (47, None) for pid in LED_VERSIONS},
    '9x1:CAN_CAN_Gateway:Werksdatensatz_Gateway': (9,None),
    '9x1:CAN_CAN_Gateway:unprogrammiertes_Gateway': (9,None),
    '9x1:Steering_Control_Unit:unprogrammierte_EPS': (69,None),
    '9x1:ACC3:ACC3_unprogrammiert': (64,None),
    '9x1:Airbag:Unprogrammiertes_Airbag_SG': (4,None),
    '9x1:BCM_hinten:BCM_hinten_unprogrammiertes_SG': (35,None),
    '9x1:BCM_vorne:BCM_vorne_uncodiertes_Basis_SG': (32,None),
    '9x1:BCM_vorne:BCM_vorne_uncodiertes_Max_SG': (32,None),
    '9x1:BKE:unprogrammierte_BKE': (7,None),
    '9x1:EPB:unprogrammierte_Parkbremse': (50,None),
    '9x1:Kombiinstrument_MM:unprogrammiertes_Kombiinstrument_MM': (8,None),
    '9x1:Lenkstockschalter:Unprogrammiertes_Kombilenkstockmodul_SG': (54,None),
    '9x1:PASM_PADM:unprogrammiertes_PASM': (19,None),
    '9x1:PDC:unprogrammiertes_SG_PDC': (18,None),
}

SOURCE_GENERATIONS = {pid: '9x1' if spec[0] in (32,35,19) or pid in (
    '9x1:Airbag:Airbag_A2_4_9x1','9x1:Airbag:Airbag_A2_5_9x1','9x1:Airbag:Airbag_A2_6_9x1') else None
    for pid,spec in VERSIONS.items()}
for pid in ('9x1:BCM_vorne:BCM_vorne_unprogrammiertes_SG', '9x1:BCM_hinten:BCM_hinten_uncodiertes_SG'):
    SOURCE_GENERATIONS[pid] = None  # Preserve each original declaration's scope.
SOURCE_GENERATIONS.update({pid:None for pid in FAULT_ONLY_VERSIONS})
MULTIFIELD_UNITS = frozenset((4,7,19,69,29,64,74,81,165,39))


def version_matches(contract, values):
    from .x431_variant_selection import substitute_version_rule
    selectors=contract['versionSelectors']
    if not isinstance(values,list) or len(values)!=len(selectors):
        return False
    def matches(rules):
        return any((expanded:=substitute_version_rule(rule,values)) and values[:len(expanded)]==expanded
                   for rule in rules)
    constraints=contract['identityConstraints']
    return matches(constraints['versionRules']) and not matches(constraints.get('conflictingVersionRules',[]))


def decode_version_values(contract, responses):
    from .x431_variant_selection import _decode_version_map_field
    values=[]
    for item in contract['versionSelectors']:
        pdu=responses.get(item['requestHex'])
        if pdu is None:
            return None
        # Source map vectors exclude the response SID but retain the DID.
        decoded=_decode_version_map_field(item['selector'],item['formula'],bytes.fromhex(pdu)[1:])
        value=decoded.get('text') if decoded.get('formulaKind')=='TEXTTABLE' else decoded.get('value')
        if not decoded.get('ok') or not isinstance(value,str) or not value.isascii() or '\0' in value or len(value)>=256:
            raise ElmError('identity-decode-failed')
        values.append(value)
    return values


def resolve_version_responses(candidates, responses):
    """Compare each declared version format against the same fresh replies.

    Wait for the union of source selectors before deciding. A nominal profile's
    field length must not reject a different supported version in advance.
    """
    required={s['requestHex'] for _,c in candidates for s in c['versionSelectors']}
    if not required.issubset(responses):
        return None
    matches=[]
    for profile,contract in candidates:
        try:
            values=decode_version_values(contract,responses)
        except ElmError:
            continue
        if values is not None and version_matches(contract,values):
            matches.append((profile,contract))
    if len(matches)!=1:
        raise ElmError('identity-mismatch')
    return matches[0]


def source_contract(profile, selected, *, identity_only=False):
    if profile.get('profileId') in LED_VERSIONS:
        from .headlamp_source import source_contract as headlamp_contract
        return headlamp_contract(profile, selected)
    if profile.get('profileId') == BOSE_PROFILE:
        from .bose_source import source_contract as bose_contract
        return bose_contract(profile, selected)
    if profile.get('profileId') in SWSG_VERSIONS:
        from .swsg_source import source_contract as swsg_contract
        return swsg_contract(profile, selected)
    pid=profile.get('profileId'); route_spec=VERSIONS.get(pid) or FAULT_ONLY_VERSIONS.get(pid)
    targets=SHARED_TARGETS.get(pid,(route_spec[0],) if route_spec else ())
    ecu=profile.get('selectedEcuId',targets[0] if len(targets)==1 else None)
    if (route_spec is None or type(ecu) is not int or ecu not in targets or profile.get('ecuIds') != list(targets)
            or profile.get('generation') != SOURCE_GENERATIONS[pid]):
        raise ValueError('manufacturer-profile-unqualified')
    requested_generation=profile.get('requestedGeneration','981')
    if requested_generation not in ('981','982') or pid in DME_982_VERSIONS and requested_generation!='982':
        raise ValueError('manufacturer-profile-unqualified')
    dsn=route_spec[1]; source=_source_profile(pid,ecu,generation=requested_generation)
    if source.get('profileId') != pid or source.get('ecuIds') != list(targets) or requested_generation not in source.get('generations',[]):
        raise ValueError('uds-source-profile-mismatch')
    definitions=source.get('versionRoutes',[])
    if len(targets)>1:definitions=[d for d in definitions if d.get('requestedEcuId')==ecu]
    expected_version_binding='source-route-or-version-ambiguous' if ecu==87 else 'exact-source-version-rule-match'
    if len(definitions)!=1 or definitions[0].get('bindingStatus')!=expected_version_binding:
        raise ValueError('uds-source-version-unresolved')
    routes=definitions[0].get('routes',[])
    if ecu==87:
        # Both original declarations have identical version semantics. Do not
        # infer a communication link from their flags or table order.
        def version_semantics(r):
            return ([tuple(s.get(k) for k in ('leftWordSigned','rightWordSigned','byteOffsetSigned','bitOffsetByte','formulaId')) for s in r.get('selectors',[])],
                [(m.get('name'),m.get('rules')) for m in r.get('versionMatches',[])])
        if (len(routes)!=2 or [r.get('routeIndexOffset') for r in routes]!=[105454,105493]
                or [r.get('flagsWord') for r in routes]!=[0,1]
                or version_semantics(routes[0])!=version_semantics(routes[1])):
            raise ValueError('uds-source-version-unresolved')
        routes=routes[:1]
    if len(routes)!=1:
        raise ValueError('uds-source-version-unresolved')
    route=routes[0];selectors=route.get('selectors',[]);matches=route.get('versionMatches',[])
    expected_selectors=[(0x22,-3678,2,0,0x80000002)]
    if ecu == 8:
        expected_selectors += [(0x22,-3682,2,0,0x8000002A)]
    if ecu in (54,18,50,8):
        # Preserve the declared selectors. Each source rule determines which
        # prefix fields it compares; earlier instrument versions also use
        # the name field, while later ones compare only the DSN.
        expected_selectors += [(0x22,-3617,2,2,0xA0000001),(0x22,-3617,2,6,0xA0000001)]
    if ecu == 55:
        expected_selectors += [(0x22,-3617,2,2,0xA0000001)]
    if ecu in MULTIFIELD_UNITS:
        dsn_selector=(0x22,-3678,2,0,0x80000002)
        name_selector=(0x22,-3682,2,0,0x8000002A)
        flag2=(0x22,-3617,2,2,0xA0000001);flag6=(0x22,-3617,2,6,0xA0000001)
        expected_selectors={4:[dsn_selector,name_selector,flag2,flag6],7:[name_selector,dsn_selector,flag2],
            19:[dsn_selector,(0x22,-3682,2,0,0x80000819),flag2,flag6],
            69:[(0x22,-3682,2,0,0x80000001),dsn_selector,flag2],
            29:[dsn_selector,name_selector,(0x22,-3703,2,0,0x80000007)]}.get(ecu,[])
    simple_name=(0x22,-3682,2,0,0x8000021D);short_dsn=(0x22,-3678,2,0,0x8000000A)
    if ecu==64:expected_selectors=[(0x22,-3682,2,0,0x80000001),dsn_selector,flag2,flag6]
    if ecu==74:expected_selectors=[flag2,(0x22,-3703,2,0,0x80000007),simple_name,short_dsn]
    if ecu==81:expected_selectors=[simple_name,short_dsn]
    if ecu==165:expected_selectors=([(0x22,-3682,2,0,0x400001E4),(0x22,-3678,2,0,0x40000008)]
        if pid.startswith('9x1:ERA_GLONASS_VFA:') else [simple_name,short_dsn])
    if ecu in (39,88):expected_selectors=[(0x22,-3682,2,0,0x8000002A),short_dsn]
    if ecu in (87,95,96):expected_selectors=[simple_name,short_dsn]
    if pid in MEDIA_VERSIONS:expected_selectors=[simple_name,short_dsn]
    if pid.startswith('9x1:RDK3:'):
        expected_selectors=[(0x22,-3682,2,0,0x8000002A),short_dsn,(0x22,-3617,2,2,0xA0000001)]
    if ecu==1:expected_selectors=[(0x22,-3678,2,0,0x80000007)]
    if pid in DME_ASAM_VERSIONS or pid==DME_SHARED_ASAM_PROFILE:expected_selectors=[(0x22,-3682,2,0,0xF000000D),(0x22,-3678,2,0,0xF0000009)]
    if ecu==9:
        expected_selectors=[(0x22,-3678,2,0,0x80000002),(0x22,-3682,2,0,0x80000423),
            (0x22,-3617,2,2,0xA0000001),(0x22,-3617,2,6,0xA0000001)]
    selector_keys=('leftWordSigned','rightWordSigned','byteOffsetSigned','bitOffsetByte','formulaId')
    if (route.get('flagsWord')!=(1 if pid in MEDIA_VERSIONS or pid in DME_SOURCE_UDS_VERSIONS else 0) or [tuple(s.get(k) for k in selector_keys) for s in selectors]!=expected_selectors or len(matches)!=1
            or matches[0].get('name')!=pid.rsplit(':',1)[1] or (matches[0].get('rules')!=[dsn] if dsn is not None else not matches[0].get('rules'))
            ):
        raise ValueError('uds-source-version-unresolved')
    diagnostics=source.get('diagnosticSources',[])
    if len(targets)>1:diagnostics=[d for d in diagnostics if d.get('requestedEcuId')==ecu]
    expected_binding='source-link-ambiguous' if ecu==87 else 'exact-source-name-match'
    if len(diagnostics)!=1 or diagnostics[0].get('bindingStatus')!=expected_binding:
        raise ValueError('uds-source-route-unresolved')
    links=diagnostics[0].get('links',[])
    if ecu==87:
        selected_link=profile.get('selectedSourceLink',0)
        if type(selected_link) is not int or selected_link not in (0,1) or [l.get('linkIndex') for l in links]!=[0,1]:
            raise ValueError('uds-source-route-unresolved')
        links=[links[selected_link]]
    if len(links)!=1:
        raise ValueError('uds-source-route-unresolved')
    comm=links[0]['communication'];pairs=comm.get('pairs',[])
    tx,rx=([(0x71C,0x786),(0x764,0x7CE)][selected_link] if ecu==87 else
        {95:(0x74E,0x7B8),96:(0x4D2,0x232)}[ecu] if ecu in (95,96) else PAIRS[ecu])
    commands=((COMMANDS[0],COMMANDS[1],COMMANDS[4],COMMANDS[2],COMMANDS[3]) if ecu==8 else
              (COMMANDS[0],COMMANDS[2],COMMANDS[1],COMMANDS[4],COMMANDS[3]) if ecu==55 else
              COMMANDS[:2]+COMMANDS[3:4] if ecu==35 else COMMANDS[:4])
    if ecu in (4,69):commands=(COMMANDS[0],COMMANDS[1],COMMANDS[4],COMMANDS[2],COMMANDS[3])
    if ecu==7:commands=(COMMANDS[0],COMMANDS[1],COMMANDS[2],COMMANDS[4],COMMANDS[3])
    if ecu==19:commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[2],COMMANDS[3])
    if ecu==29:commands=(COMMANDS[0],COMMANDS[1],COMMANDS[4],
        (0x200008,'010322F189','b568ca0678d7a22d339e23694ce27bb896821e0eb436979c57b216930f8cebbd'),COMMANDS[2],COMMANDS[3])
    if ecu in (64,81,165):commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[2],COMMANDS[3])
    if ecu==74:commands=(COMMANDS[0],COMMANDS[4],COMMANDS[2],
        (0x200008,'010322F189','b568ca0678d7a22d339e23694ce27bb896821e0eb436979c57b216930f8cebbd'),COMMANDS[1],COMMANDS[3])
    if ecu==165:commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[3])
    if ecu==39:commands=(COMMANDS[0],COMMANDS[1],COMMANDS[4],COMMANDS[2],
        (0x1829,'000322F187','58dda22e2be23f0c2d9705c07cf565e8c0c1fdcc4ad8a65fa970c10bc17ef351'),COMMANDS[3])
    if ecu in (87,95,96):commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[3])
    if pid in MEDIA_VERSIONS:commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[3])
    if ecu==1:commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[3])
    if ecu==88:commands=(COMMANDS[0],COMMANDS[4],COMMANDS[1],COMMANDS[2],COMMANDS[3])
    if ecu==9:commands=(COMMANDS[0],COMMANDS[1],COMMANDS[4],COMMANDS[2],COMMANDS[3])
    if (comm.get('class')!='static-source-confirmed' or comm.get('modeByte')!=(0x50 if ecu==88 else 0x10) or len(pairs)!=1
            or pairs[0].get('subtypeByte')!=(5 if ecu==88 else 3) or pairs[0].get('storedTx')!=tx<<5 or pairs[0].get('storedRx')!=rx<<5
            or pairs[0].get('parameterIds')!=[x[0] for x in commands]):
        raise ValueError('uds-source-route-unresolved')
    if ecu==88 and (diagnostics[0].get('sourceSha256')!='18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'
            or links[0].get('communicationSourceOffset')!=73802):
        raise ValueError('uds-source-route-unresolved')
    plans=source.get('versionSelectorReadSources',[])
    if len(targets)>1:plans=[p for p in plans if p.get('requestedEcuId')==ecu]
    occurrences=[o for p in plans for r in p.get('routes',[]) for g in r.get('requestGroups',[])
                 if g.get('requestCandidateHex')=='22F1A2' for o in g.get('sourceOccurrences',[])]
    if (len(occurrences)!=(4 if ecu==87 else 1)
            or ecu==87 and sorted((o.get('linkIndex'),o.get('pairIndex')) for o in occurrences)!=[(0,0),(0,0),(1,0),(1,0)]
            or any(o['sourceCommand'].get('decodedDescriptorSha256')!=COMMANDS[1][2] for o in occurrences)):
        raise ValueError('uds-source-command-unresolved')
    library=Path(os.environ.get('PORSCHE981_DEFINITION_ROOT') or Path(__file__).resolve().parents[2])
    language=(library/'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP').read_bytes()
    for cid,hx,digest in commands:
        command=_lookup_command(language,cid)
        if (command.get('recordFound') is not True or command.get('decodedPayloadHex','').upper()!=hx
                or command.get('decodedPayloadSha256')!=digest):
            raise ValueError('uds-source-command-unresolved')
    ids=['22F1A2','22F189','22F1A3','22F187','22F190']
    if ecu in (5,32,18,50,55,8) or ecu in MULTIFIELD_UNITS: ids.insert(3,'22F191')
    if ecu==74:ids=['22F189','22F1A3','22F191','22F187','22F190']
    if ecu==81:ids=['22F189','22F1A3','22F191','22F187']
    if ecu==165:ids=['22F189','22F191','22F1A3','22F187']
    if ecu==1:ids=['22F187','22F190','22F191','22F197','22F1A2','22F1E3','22F15B']
    if pid in DME_ASAM_VERSIONS:ids=['22F189','22F190','22F1A3','22F191','22F187','22F197','22F15B','22F17E','22F1AD']
    if pid in (DME_SHARED_ASAM_PROFILE,DME_GT4_RS_PROFILE):
        # These source profiles have no F1A3 identity record. Keep their own
        # supported fields and grouped records; do not borrow a normal layout.
        ids=['22F189','22F190','22F191','22F187','22F197','22F15B','22F17E','22F1AD']
    if ecu==88:ids=['22F189','22F182','22F191','22F1A3','22F187','22F190']
    if ecu==39:ids=['22F189','22F1A3','22F187','22F190']
    if ecu in (95,96):ids=['22F189','22F191','22F187']
    if ecu==87:ids=['22F18C','22F191','22F187','22F18B','22F15B']
    if pid.startswith('9x1:RDK3:'):ids=['22F189','22F191','22F187','22F190']
    if ecu==9:
        ids=['22F1A2','22F1A3','22F187','22F18C','22F190','22F191','22F182']
        if any(g['requestHex']=='22F189' for g in source['identityGroups']):ids.append('22F189')
        if pid in FAULT_ONLY_VERSIONS:
            # Both original state definitions omit F182. Keep their own
            # complete DSN/software/hardware/serial/VIN fields instead.
            ids.remove('22F182')
    if pid=='9x1:Steering_Control_Unit:unprogrammierte_EPS':
        ids=['22F1A2','22F191','22F1A3','22F187','22F18C','22F190']
    if pid in MEDIA_VERSIONS:
        ids=['22F189','22F191','22F190']
        ids+=['22F187'] if ecu in (65,75) else []
        if pid=='9x1:BV_InforContrUnit1UDSDesay:EV_MU_ChinaPCM_DESAY_001':ids.append('22F187')
        ids+=['22F1A3','22F18C'] if ecu in (65,70) else []
        if any(g['requestHex']=='22F179' for g in source['identityGroups']):ids.append('22F179')
    identity=source_records(profile,source,selected,ids,prefix='uds')
    if not pid.startswith('9x1:RDK3:') and pid not in MEDIA_VERSIONS and ecu not in (1,74,81,165,39,87,88,95,96) and identity[0]['record']['formula'].get('text')!='IDENTICAL:Standar,BitMask=0,BaseDataType=A_ASCIISTRING,BitLength=48,Encoding=ISO-8859-2,HighLow=1,Radix=10,DataType=A_UNICODE2STRING,Precision=null;':
        raise ValueError('uds-source-version-formula-mismatch')
    result={'sourceCatalogId':SOURCE_CATALOG,'sourceQualification':'uds-source-version-and-identity',
            'catalogProfileId':'porsche-981-uds-source','ecuId':ecu,'txId':tx,'rxId':rx,'applicationProtocol':'uds',
            'identityRequests':identity,'identityConstraints':{'dsnText':dsn},
            'sourceCommands':[bytes.fromhex(hx)[2:].hex().upper() for _,hx,_ in commands],
            'session':{'requestHex':'1003','positivePrefixHex':'5003'},'addressEvidence':'pinned-source-stored-11bit-words'}

    if ecu==88:
        result.update(transportAddressing={'kind':'porsche-mode50','transmitAddress':0x55,
            'flowControlHex':'55301005AAAAAAAA','receiveAddress':'observed-and-bound'},
            addressEvidence='pinned-mode50-source-data-window')
    if dsn is None or ecu in (8,55) or identity_only:
        from .x431_variant_selection import _load_version_language
        from scripts.x431_re.gag_lib import compact_express
        language_reader=_load_version_language(library/'.local/x431-re/2026-09-27-protocol/package/PORSCHE_CN.GGP')
        version_fields=[]
        for selector in selectors:
            hx=f"{selector['leftWordSigned']:02X}{selector['rightWordSigned']&0xFFFF:04X}"
            if hx not in result['sourceCommands']:
                raise ValueError('uds-source-selector-request-missing')
            formula=compact_express(language_reader.lookup_id('EXPRESS.GAG',selector['formulaId']))
            version_fields.append(dict(requestHex=hx,selector=selector,formula=formula))
        result['versionSelectors']=version_fields
        # The exact selector is authoritative for the actual version. Some
        # separate display records describe a different slice of the same DID;
        # do not require or fabricate that slice to qualify a read-only client.
        result['versionIdentityRequests']=list(dict.fromkeys(v['requestHex'] for v in version_fields if v['requestHex'] not in ids))
        result['identityConstraints']={'versionRules':matches[0]['rules']}
        if pid in DME_TARGET_RULES:
            qualified = DME_TARGET_RULES[pid]
            if any(rule not in matches[0]['rules'] for rule in qualified):
                raise ValueError('uds-source-target-rules-unresolved')
            result['identityConstraints']={'versionRules':list(qualified)}
        if pid in ('9x1:RDK:Reifendruckkontrolle_A2_4','9x1:RDK:RDK_unprogrammiertes_SG'):
            # Original SYSP GetInfoFromBvAdd stops at the first matching DSN
            # variant. This exact two-entry table places #ja before 200001:
            # inconsistent software selects the unprogrammed source profile,
            # even when its DSN is 200001. Never prefer an enabled candidate.
            unprogrammed=pid.endswith(':RDK_unprogrammiertes_SG')
            other_name='Reifendruckkontrolle_A2_4' if unprogrammed else 'RDK_unprogrammiertes_SG'
            other=_source_profile('9x1:RDK:'+other_name,55,generation=requested_generation)
            definitions=other.get('versionRoutes',[])
            if (other.get('ecuIds')!=[55] or requested_generation not in other.get('generations',[])
                    or len(definitions)!=1 or definitions[0].get('bindingStatus')!='exact-source-version-rule-match'
                    or definitions[0].get('sourceSha256')!=source['versionRoutes'][0].get('sourceSha256')):
                raise ValueError('uds-source-version-unresolved')
            other_routes=definitions[0].get('routes',[])
            if len(other_routes)!=1:
                raise ValueError('uds-source-version-unresolved')
            other_route=other_routes[0];other_matches=other_route.get('versionMatches',[])
            if (other_route.get('routeIndexOffset')!=route.get('routeIndexOffset')
                    or other_route.get('flagsWord')!=route.get('flagsWord')
                    or [tuple(s.get(k) for k in selector_keys) for s in other_route.get('selectors',[])]!=expected_selectors
                    or route.get('definitionOffset')!=134861 or route.get('definitionIndexEnd')!=135002
                    or matches[0].get('versionIndex')!=(0 if unprogrammed else 1)
                    or matches[0].get('ruleOffset')!=(135002 if unprogrammed else 135057)
                    or other_route.get('definitionOffset')!=route.get('definitionOffset')
                    or other_route.get('definitionIndexEnd')!=route.get('definitionIndexEnd')
                    or len(other_matches)!=1 or other_matches[0].get('name')!=other_name
                    or other_matches[0].get('versionIndex')!=(1 if unprogrammed else 0)
                    or other_matches[0].get('ruleOffset')!=(135057 if unprogrammed else 135002)
                    or other_matches[0].get('rules')!=(['200001'] if unprogrammed else ['#ja'])
                    or matches[0].get('rules')!=(['#ja'] if unprogrammed else ['200001'])):
                raise ValueError('uds-source-version-unresolved')
            if not unprogrammed:
                result['identityConstraints']['conflictingVersionRules']=other_matches[0]['rules']
    return result


def qualify_client(client,contract,profile_id,deadline,guard,*,resolve_version=None,resolve_source_responses=None):
    identity={};version_responses={};records={x['requestHex']:x['record'] for x in contract['identityRequests'] if 'record' in x}

    def ask(hx):
        guard();result=client.request(hx,min(deadline,time.monotonic()+15));pdu=result.get('payload_hex','')
        if not result.get('ok') or not isinstance(pdu,str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){2,4095}',pdu):
            raise ElmError(result.get('error') or 'identity-request-failed')
        wire=bytes.fromhex(hx);positive=bytes([wire[0]+0x40])+wire[1:]
        if not bytes.fromhex(pdu).startswith(positive):
            raise ElmError('wrong-sid-or-did-echo')
        return pdu.upper()

    version_checked=False
    for hx in contract['sourceCommands']:
        pdu=ask(hx)
        if contract.get('versionSelectors'):
            if resolve_source_responses is not None:
                version_responses[hx]=pdu
                if not version_checked and (resolved:=resolve_source_responses(version_responses)) is not None:
                    contract,profile_id=resolved
                    records={x['requestHex']:x['record'] for x in contract['identityRequests'] if 'record' in x}
                    values=decode_version_values(contract,version_responses)
                    if values is None or not version_matches(contract,values):raise ElmError('identity-mismatch')
                    version_checked=True
            elif not version_checked:
                if hx in {item['requestHex'] for item in contract['versionSelectors']}:version_responses[hx]=pdu
                values=decode_version_values(contract,version_responses)
                if values is None:continue
                if resolve_version is not None:
                    contract,profile_id=resolve_version(values)
                    records={x['requestHex']:x['record'] for x in contract['identityRequests'] if 'record' in x}
                if not version_matches(contract,values):raise ElmError('identity-mismatch')
                version_checked=True
        elif hx=='22F1A2':
            decoded=decode_application_response(records[hx],bytes.fromhex(pdu),mode='pdu')
            if resolve_version is not None and decoded.get('ok'):
                contract,profile_id=resolve_version(decoded.get('text'))
                records={x['requestHex']:x['record'] for x in contract['identityRequests'] if 'record' in x}
            if not decoded.get('ok') or decoded.get('text')!=contract['identityConstraints']['dsnText']:
                raise ElmError('identity-mismatch')
            identity[hx]=pdu
    if contract.get('versionSelectors') and not version_checked:raise ElmError('identity-mismatch')
    for hx in contract.get('versionIdentityRequests',[]):
        identity[hx]=version_responses[hx]
    for item in contract['identityRequests']:
        hx=item['requestHex'];pdu=identity.get(hx) or version_responses.get(hx) or ask(hx)
        decoded_fields=[decode_application_response(record,bytes.fromhex(pdu),mode='pdu')
            for record in (item.get('records') or [item['record']])]
        decoded=decoded_fields[0];body=bytes.fromhex(pdu)[len(bytes.fromhex(hx)):]
        if not all(d.get('ok') for d in decoded_fields) or not body or all(x==0 for x in body) or all(x==255 for x in body):
            raise ElmError('identity-decode-failed')
        if hx in ('1A90','22F190') and not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}',str(decoded.get('text',''))):
            raise ElmError('identity-decode-failed')
        identity[hx]=pdu
    if contract.get('transportAddressing',{}).get('kind')=='porsche-mode50':
        address=getattr(client,'receive_address',None)
        if type(address) is not int or not 0<=address<=255:raise ElmError('identity-mismatch')
        identity['_mode50ReceiveAddress']=address
    return identity,{'observedProfileMatch':True,'generation':contract.get('generation','981'),'ecuId':contract['ecuId'],
        'variantLink':{'profile_id':profile_id},'sourceCatalogId':SOURCE_CATALOG,'freshRead':True,'liveVerified':False,'writePayload':None}
