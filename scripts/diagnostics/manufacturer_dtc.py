"""Source-bound fault reads after fresh manufacturer identity; never clears faults.

GetDtcData maps table byte 0 to the record stride, metadata bytes 0..5 to
TOKEN+0x6861..0x6866. The last byte selects PorscheReadDtc, not the stride.
The source routes use reader kinds 1/9/11 and complete KWP/UDS PDUs.
Native count evidence is separate from mandatory runtime length validation.
"""
from __future__ import annotations
import hashlib
import json
import re
import time

from .decode import decode_payload, sae_from_two_byte
from .elm import ElmError
from .pdk_read import _source_profile, SOURCE_CATALOG
from .swsg_source import VERSIONS as SWSG_VERSIONS, PAIRS as SWSG_PAIRS, PIWIS_SOURCES as SWSG_SOURCES
from .bose_source import PROFILE as BOSE_PROFILE, PIWIS_SOURCES as BOSE_SOURCES, RULE as BOSE_RULE

NATIVE_DTC_LIBRARY_SHA256 = '7e5f383231eee9c8402b3fd1347c8e342dc873b3496d3c55e7a22006fc0e7a89'
NATIVE_COUNT_SHA256 = 'f3bf8fe50c497868678c753bbc8882d8e3497de36699a81d4a70866e8e3f1c9c'
LAYOUTS = {
    '03000102FF0001': ('kwp-dtc-18', ('dtcRead',)),
    '03000102FF0009': ('kwp-dtc-18', ('sessionPrecondition', 'dtcRead')),
    '04FF0203FF0009': ('uds-dtc-19-02', ('sessionPrecondition', 'dtcRead')),
    '03FF0102FF000B': ('kwp-dtc-18-length', ('dtcRead',)),
}

# PIWIS 9x1 2830.1.0 explicitly declares 19/02 and a one-byte status mask.
# Its eight source defaults encode 08: bits 0/4 are zero, bit 3 is one,
# and reserved bits are zero. The response is 59/02, availability byte, then
# zero or more big-endian 24-bit DTC + one-byte status records.
# SWSG's 12 normal variants inherit this service without excluding it; its
# unprogrammed variant explicitly excludes it. BOSE 002 declares its own
# service/request/response and explicitly shares its 001-named data types.
# Bounded source fields and original DLL instruction checks are retained in
# .local/obd-piwis-dtc-source-20261018/. No X431 descriptor is fabricated.
PIWIS_DTC_SOURCES = {
    'SWSG': dict(project='9x1', version='2830.1.0',
        sourceFile='0.0.0@SWSG.bv.db', sourceSha256=SWSG_SOURCES['0.0.0@SWSG.bv.db'],
        requestSha256='b86940f0b1b9b17870a6598337bdcc47ca05416df0a078b325570086e06fa4e7',
        responseSha256='361cde30cc89a6fd49ad5f1c1c165929a50eeafc3059971e336f3193fb4c7132'),
    'BOSE002': dict(project='9x1', version='2830.1.0',
        sourceFile='0.0.0@BV_SoundSysteUDS.bv.db', sourceSha256=BOSE_SOURCES['0.0.0@BV_SoundSysteUDS.bv.db'],
        requestSha256='3b98ac0365c01e87935ab76ee1ea4ef0ec34c6ef54a3aa3e5710a0cc6512163b',
        responseSha256='acbfd21824a07c1d470ce95d425200f0accfff0526102d8d5e045adf8df574ba'),
}


def _piwis_dtc_plan(profile, source, contract):
    pid = profile['profileId']; ecu = contract['ecuId']
    swsg = pid in SWSG_VERSIONS
    sources = SWSG_SOURCES if swsg else BOSE_SOURCES
    pair = SWSG_PAIRS.get(ecu) if swsg else (0x76F, 0x7D9) if ecu == 65 else None
    if (pair is None or contract.get('sourceQualification') != 'uds-source-version-and-identity'
            or (contract.get('txId'), contract.get('rxId')) != pair
            or contract.get('applicationProtocol') != 'uds'
            or contract.get('sourceBridge') != dict(project='9x1', version='2830.1.0', sources=sources)
            or contract.get('identityConstraints') != ({'dsnText': SWSG_VERSIONS[pid]} if swsg else {'versionRules': [BOSE_RULE]})
            or contract.get('sourceCommands') != (['1003', '22F1A2'] if swsg else ['1003', '22F19E', '22F1A2'])
            or contract.get('session') != dict(requestHex='1003', positivePrefixHex='5003')
            or source.get('module') != ('SWSG' if swsg else 'BV_SoundSysteUDS')
            or source.get('ecuIds') != ([47, 48] if swsg else [65])
            or 'selectedSourceLink' in profile):
        raise ValueError('manufacturer-dtc-piwis-source-unqualified')
    refs = source.get('diagnosticSources', [])
    refs = [r for r in refs if r.get('requestedEcuId') == ecu]
    if (len(refs) != 1 or refs[0].get('variantName') != source['variant']
            or refs[0].get('bindingStatus') != 'source-name-missing' or refs[0].get('links') != []
            or refs[0].get('sourceSha256') != '18dca0abdc181510beef8667146f9febac08857e05612a6bb87f8c474d7cc522'):
        raise ValueError('manufacturer-dtc-piwis-source-unqualified')
    return dict(descriptorHex=None, decodeType='uds-dtc-19-02',
        commands=[dict(role='sessionPrecondition', requestHex='1003'), dict(role='dtcRead', requestHex='190208')],
        piwisReadSource=dict(PIWIS_DTC_SOURCES['SWSG' if swsg else 'BOSE002']))


def decode_source_dtc(payload_hex, kind):
    """Reader11 body layout; other source readers retain the shared decoder.

    Descriptor 03 FF 01 02 FF 00 0B skips one body byte, then reads big-endian
    two-byte codes and one-byte statuses. Count derives from complete records,
    not the skipped byte. The original count routine's partial-tail rounding
    is deliberately rejected. The generator-bound generic decoder is unchanged.
    """
    if kind != 'kwp-dtc-18-length':
        return decode_payload(payload_hex, {'type':kind})
    def fail(reason):return dict(type=kind,ok=False,reason=reason,records=[])
    if not isinstance(payload_hex,str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){1,4095}',payload_hex):
        return fail('invalid-payload')
    payload=bytes.fromhex(payload_hex)
    if len(payload)<2:return fail('truncated-dtc')
    if payload[0]!=0x58:return fail('positive-prefix-mismatch')
    if (len(payload)-2)%3:return fail('count-inconsistent-dtc')
    records=[]
    for offset in range(2,len(payload),3):
        code=payload[offset:offset+2].hex().upper()
        records.append(dict(dtcHex=code,statusHex=f'{payload[offset+2]:02X}',displayCode=sae_from_two_byte(code)))
    return dict(type=kind,ok=True,records=records,rawHex=payload.hex().upper())


def source_dtc_plan(profile, contract):
    pid = profile['profileId']; ecu = contract['ecuId']; generation = contract['generation']
    if (contract.get('profileId') != pid or contract.get('sourceCatalogId') != SOURCE_CATALOG
            or contract.get('sourceQualification') not in ('pdk-dsn-and-full-identity',
                'kwp-source-version-and-identity', 'uds-source-version-and-identity')
            or profile.get('selectedEcuId', ecu) != ecu
            or profile.get('requestedGeneration', generation) != generation):
        raise ValueError('manufacturer-dtc-source-unqualified')
    source = _source_profile(pid, ecu, generation=generation)
    if source.get('profileId') != pid or generation not in source.get('generations', []):
        raise ValueError('manufacturer-dtc-source-unqualified')
    piwis = pid in SWSG_VERSIONS or pid == BOSE_PROFILE
    if piwis:
        return _validated_dictionary_plan(_piwis_dtc_plan(profile, source, contract), source, pid, ecu, generation)
    refs = [r for r in source.get('diagnosticSources', [])
            if r.get('requestedEcuId') == ecu and r.get('variantName') == source['variant']]
    if len(refs) != 1:raise ValueError('manufacturer-dtc-source-unqualified')
    links = refs[0].get('links', [])
    if 'selectedSourceLink' in profile:
        links = [r for r in links if r.get('linkIndex') == profile['selectedSourceLink']]
    plans = []
    for link in links:
        dtc = link.get('dtc') or {}; stride = dtc.get('styleByte'); meta = dtc.get('metadataHex')
        if type(stride) is not int or not 0 <= stride <= 255 or not isinstance(meta,str) or not re.fullmatch('[0-9A-F]{12}',meta):
            raise ValueError('manufacturer-dtc-layout-unqualified')
        descriptor = f'{stride:02X}' + meta
        spec = LAYOUTS.get(descriptor)
        if spec is None:raise ValueError('manufacturer-dtc-layout-unqualified')
        if (spec[0]=='kwp-dtc-18-length' and (ecu not in (30,31,76,78)
                or contract.get('transportAddressing',{}).get('kind')!='porsche-tp20')):
            raise ValueError('manufacturer-dtc-layout-unqualified')
        commands = dtc.get('readCommandSlots', [])
        if tuple(c.get('role') for c in commands) != spec[1]:
            raise ValueError('manufacturer-dtc-sequence-unqualified')
        selected = []
        for command in commands:
            hx = command.get('wireCandidateHex')
            if (not isinstance(hx,str) or command.get('commandFormat') != 'complete-source-command'
                    or command.get('nativeDescriptorHexCandidate') != '01'+f'{len(hx)//2:02X}'+hx):
                raise ValueError('manufacturer-dtc-command-unqualified')
            if command['role'] == 'sessionPrecondition':
                if hx != contract['session']['requestHex']:raise ValueError('manufacturer-dtc-session-conflict')
            elif not (spec[0]=='kwp-dtc-18' and hx=='1800FF00'
                    or spec[0]=='kwp-dtc-18-length' and hx=='1802FF00'
                    or spec[0]=='uds-dtc-19-02' and hx in ('190208','19020C','19022C')):
                raise ValueError('manufacturer-dtc-command-unqualified')
            selected.append(dict(role=command['role'],requestHex=hx))
        plans.append(dict(descriptorHex=descriptor,decodeType=spec[0],commands=selected))
    if not plans or any(p != plans[0] for p in plans[1:]):
        raise ValueError('manufacturer-dtc-source-route-ambiguous')
    return _validated_dictionary_plan(plans[0], source, pid, ecu, generation)


def _validated_dictionary_plan(plan, source, pid, ecu, generation):
    definition = source.get('dtcDefinition') or {}; canonical = dict(definition)
    digest = canonical.pop('definitionSha256', None)
    if (definition.get('profileId') != pid
            or hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=True).encode()).hexdigest() != digest):
        raise ValueError('manufacturer-dtc-dictionary-unqualified')
    return {**plan, 'profileId':pid, 'ecuId':ecu, 'generation':generation,
            'sourceCatalogId':SOURCE_CATALOG, 'definitionSha256':digest, 'dictionary':definition['records']}


def read_source_dtcs(client, profile, contract, qualification, deadline, guard):
    """A single source sequence on the already qualified and owned connection."""
    if (not qualification.get('freshRead') or not qualification.get('observedProfileMatch')
            or qualification.get('sourceCatalogId') != SOURCE_CATALOG
            or qualification.get('generation') != contract['generation']
            or qualification.get('ecuId') != contract['ecuId']
            or (qualification.get('variantLink') or {}).get('profile_id') != profile['profileId']):
        raise ElmError('manufacturer-dtc-identity-unqualified')
    guard();plan = source_dtc_plan(profile, contract)
    # The native request guard still requires membership in its read-only set.
    # These additions are taken only from the verified server-owned source plan.
    if hasattr(client,'allowed'):
        client.allowed = client.allowed | {c['requestHex'] for c in plan['commands']}
    responses=[];decoded=None
    for command in plan['commands']:
        guard();hx=command['requestHex'];reply=client.request(hx,min(deadline,time.monotonic()+15));guard()
        if not reply.get('ok'):raise ElmError(reply.get('error') or 'manufacturer-dtc-read-failed')
        pdu_hex=reply.get('payload_hex')
        if not isinstance(pdu_hex,str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){2,4095}',pdu_hex):
            raise ElmError('manufacturer-dtc-response-invalid')
        pdu=bytes.fromhex(pdu_hex);request=bytes.fromhex(hx)
        if command['role']=='sessionPrecondition':
            if not pdu.startswith(bytes([request[0]+0x40])+request[1:]):raise ElmError('session-subfunction-mismatch')
        else:
            decoded=decode_source_dtc(pdu_hex,plan['decodeType'])
            if not decoded.get('ok'):raise ElmError(decoded.get('reason') or 'manufacturer-dtc-response-invalid')
        responses.append(dict(requestHex=hx,pduHex=pdu_hex.upper(),role=command['role']))
    if decoded is None:raise ElmError('manufacturer-dtc-sequence-unqualified')
    for row in decoded['records']:
        matches=[r for r in plan['dictionary'] if r.get('numericKey')==int(row['dtcHex'],16)]
        ref=matches[0] if len(matches)==1 and matches[0].get('definitionIssue') is None else None
        text=ref.get('text') if ref else None
        # Launch's Chinese translation is not a Chinese factory-manual title.
        # The renderer separately resolves the manual; only original English
        # is a fallback. Dictionary statusKind is never a vehicle status byte.
        row['originalEnglish']=text if isinstance(text,str) and re.search('[A-Za-z]',text) and not re.search('[\u3400-\u9fff]',text) else None
        row['dictionaryMatch']=ref is not None
    provenance = {'piwisReadSource':plan['piwisReadSource']} if 'piwisReadSource' in plan else {}
    return dict(ok=True,profileId=plan['profileId'],ecuId=plan['ecuId'],generation=plan['generation'],
        decodeType=plan['decodeType'],descriptorHex=plan['descriptorHex'],decoded=decoded,
        responses=responses,sourceCatalogId=SOURCE_CATALOG,definitionSha256=plan['definitionSha256'],
        freshRead=True,liveVerified=False,writePayload=None,**provenance)
