"""Read the source coding groups on a freshly identified, owned connection.

The complete returned PDUs are retained, including bytes without named fields.
Source group coverage is not a qualified complete ECU baseline: expected block
lengths, write recipes and restoration remain independent requirements. This
reader never creates a backup, emits a write, or replays a failed request.

Reviewed source versions additionally bind their complete PIWIS configuration
record sets and exact lengths. A complete read is still not a saved baseline
or permission to write; other source groups retain their prior status.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import time

from .elm import ElmError
from .pdk_read import SOURCE_CATALOG, _source_profile
from .response_values import normalize_response
from scripts.x431_re.request_fields import CODING_DATA_SHA256, CODING_READWRITE_SHA256
from .swsg_source import VERSIONS as SWSG_VERSIONS, PAIRS as SWSG_PAIRS, PIWIS_SOURCES as SWSG_SOURCES

SWSG_CODING_LENGTHS = {
    '2204A1': 137, '2204A2': 27, '2204A3': 57, '2204A4': 29,
    '2204A5': 17, '2204A6': 42, '2204A7': 11, '2204A8': 54,
    '2204A9': 33, '2204AA': 26, '2204AB': 61, '2204B0': 30,
    '2204FE': 3, '220600': 3,
}

_SWSG_DATASET_SHA256 = {
    'SWSG_Master_mit_AFS_ab_1310': '366bc3b78025cd1db8e64249e0b2325ef986a4a2a602a0f2d2c207fa866e2fa0',
    'SWSG_Master_mit_AFS_ab_VR06': '3285ce10e0bf3ad6fa6c2121c3177984dd9fb877a99bbf2d59365804e72f1a5b',
    'SWSG_Master_mit_AFS_ab_VR15': '911f0b41bdddfe7950be9ed540fe0d01c9ec26e7c58c07b9f3630119cbb11ee9',
    'SWSG_Master_ohne_AFS_ab_1310': 'ffdf7edac2dc6e3e86fd8c7d10537b5d35a10d9439f9d0d71a3e5502f2ec1543',
    'SWSG_Master_ohne_AFS_ab_VR06': '049b6756aa9dedbb1400c2811793fb695106bfa0f909db8499f7a754e0c2d08b',
    'SWSG_Master_ohne_AFS_ab_VR15': '245f33c4fedb7fa192965605e4eb3a3ee698a2db314e1f6e8cdcd9aeb2cc7b1f',
    'SWSG_Slave_mit_AFS_ab_1310': 'f8225563bda22240bb9d0444404a2f637aaf13f2af932cdc4a2547a4cb207bb4',
    'SWSG_Slave_mit_AFS_ab_VR06': '637c0f21029cd1bbe2b733076750b706794898d8fce733d1c99861de158b8e26',
    'SWSG_Slave_mit_AFS_ab_VR15': '00b983e392b78d9a3ec4179a3aea0bda5a1953a7ee050632a74d8ec06784ff27',
    'SWSG_Slave_ohne_AFS_ab_1310': '067e322af9f49f7cea5a6252f193a16af85eaa468e7c924c057db8680923eeff',
    'SWSG_Slave_ohne_AFS_ab_VR06': '4100c02bf91100f5a9d8c1f23429ccc74761f0e71e888c9235b43287e185616d',
    'SWSG_Slave_ohne_AFS_ab_VR15': 'e81767f6599276fa64852ddce07dba9d13924fa00758b4b9afefcf34d4d18b4e',
}

_SWSG_VR06 = frozenset((
    'SWSG_Master_mit_AFS_ab_VR06',
    'SWSG_Master_ohne_AFS_ab_VR06',
    'SWSG_Slave_mit_AFS_ab_VR06',
    'SWSG_Slave_ohne_AFS_ab_VR06',
))

_SWSG_SLAVES = frozenset((
    'SWSG_Slave_mit_AFS_ab_1310',
    'SWSG_Slave_mit_AFS_ab_VR06',
    'SWSG_Slave_mit_AFS_ab_VR15',
    'SWSG_Slave_ohne_AFS_ab_1310',
    'SWSG_Slave_ohne_AFS_ab_VR06',
    'SWSG_Slave_ohne_AFS_ab_VR15',
))


def _complete_swsg_scope(profile, source, contract, groups):
    """Only the 12 reviewed PIWIS configuration datasets, never name guessing.

    Original dataset membership, exclusions, selected request/table rows and
    native byte-length load/getter evidence are retained in
    .local/obd-piwis-coding-source-20261018/. These per-version omissions were
    checked against all 158 factory records and the complete X431 group sets.
    """
    pid = profile['profileId']; ecu = contract['ecuId']
    if (contract.get('sourceQualification') != 'uds-source-version-and-identity'
            or type(ecu) is not int or ecu not in SWSG_PAIRS
            or (contract.get('txId'), contract.get('rxId')) != SWSG_PAIRS[ecu]
            or contract.get('applicationProtocol') != 'uds'
            or contract.get('identityConstraints') != {'dsnText': SWSG_VERSIONS[pid]}
            or contract.get('sourceCommands') != ['1003', '22F1A2']
            or contract.get('session') != {'requestHex': '1003', 'positivePrefixHex': '5003'}
            or contract.get('sourceBridge') != dict(project='9x1', version='2830.1.0', sources=SWSG_SOURCES)
            or source.get('module') != 'SWSG' or source.get('ecuIds') != [47, 48]):
        raise ValueError('coding-complete-scope-unqualified')
    variant = pid.rsplit(':', 1)[1]
    omitted = set()
    if variant in _SWSG_VR06: omitted.add('2204AB')
    if variant in _SWSG_SLAVES: omitted.add('2204B0')
    expected = {hx: size for hx, size in SWSG_CODING_LENGTHS.items() if hx not in omitted}
    if ({g['requestHex'] for g in groups} != set(expected)
            or any(g['minimumKnownBytes'] != expected[g['requestHex']]
                   or g['expectedTotalBytes'] not in (None, expected[g['requestHex']]) for g in groups)):
        raise ValueError('coding-complete-scope-conflict')
    scope = dict(kind='piwis-config-data-complete-record-set', project='9x1', version='2830.1.0',
        sourceFile='0.0.0@EcuConfig_SWSG.ec.db',
        sourceSha256='bf4ca54cc74157f03c852161f5374a7c345ede883ec0a775d333c32761f39b8c',
        indexSha256='91be931fb91514c7b21b86422c540c32722c0197211481d3f2e994cc89f352f7',
        datasetId='#RtGen_CD_EcuConfig_SWSG_ConfigData_' + variant,
        datasetSha256=_SWSG_DATASET_SHA256[variant],
        requestLengths=[dict(requestHex=hx, bytes=expected[hx]) for hx in sorted(expected)])
    scope['sha256'] = hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return scope, [{**g, 'expectedTotalBytes': expected[g['requestHex']]} for g in groups]


def source_coding_plan(profile, contract):
    """Accept only the immutable catalog's entire version-specific coding table."""
    pid = profile['profileId']; ecu = contract['ecuId']; generation = contract['generation']
    if (contract.get('profileId') != pid or contract.get('sourceCatalogId') != SOURCE_CATALOG
            or contract.get('sourceQualification') not in ('pdk-dsn-and-full-identity',
                'kwp-source-version-and-identity', 'uds-source-version-and-identity')
            or profile.get('selectedEcuId', ecu) != ecu
            or profile.get('requestedGeneration', generation) != generation):
        raise ValueError('manufacturer-coding-source-unqualified')
    source = _source_profile(pid, ecu, generation=generation)
    if source.get('profileId') != pid or generation not in source.get('generations', []):
        raise ValueError('manufacturer-coding-source-unqualified')
    plan = source.get('coding') or {}; canonical = dict(plan)
    # all_unit_collection attaches namedFields after the plan hash is made.
    # The outer immutable source document binds these annotations separately.
    if isinstance(canonical.get('requestGroups'), list):
        canonical['requestGroups'] = [{k: v for k, v in group.items() if k != 'namedFields'}
            for group in canonical['requestGroups']]
    digest = canonical.pop('definitionSha256', None)
    if (plan.get('kind') != 'ecu-coding-read-plan' or plan.get('schemaVersion') != 1
            or plan.get('profileId') != pid or plan.get('module') != source.get('module')
            or plan.get('variant') != source.get('variant')
            or hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest() != digest
            or plan.get('definitionCoverageComplete') is not True or plan.get('missing') != []):
        raise ValueError('manufacturer-coding-definition-incomplete')
    groups = plan.get('requestGroups'); count = plan.get('definitionFieldCount')
    if (not isinstance(groups, list) or not 1 <= len(groups) <= 256
            or type(count) is not int or not 1 <= count <= 10000):
        raise ValueError('manufacturer-coding-definition-incomplete')
    origin = None; indexes = []; requests = []; result = []
    for group in groups:
        hx = group.get('requestHex'); minimum = group.get('minimumKnownBytes')
        expected = group.get('expectedTotalBytes'); fields = group.get('fields')
        current = group.get('readOrigin') or {}
        if (not isinstance(hx, str) or not re.fullmatch(r'(?:21[0-9A-F]{2}|22[0-9A-F]{4})', hx)
                or hx in requests or group.get('identifierHex') != hx[2:]
                or group.get('identifierKind') != ('LID' if hx.startswith('21') else 'DID')
                or type(minimum) is not int or not 0 <= minimum <= 4095-len(hx)//2
                or expected is not None and (type(expected) is not int or not max(1, minimum) <= expected <= 4095-len(hx)//2)
                or not isinstance(fields, list) or not fields
                or current.get('kind') != 'x431-coding-profile-first-record'
                or current.get('sourceSha256') != CODING_DATA_SHA256
                or current.get('readwriteSha256') != CODING_READWRITE_SHA256
                or current.get('module') != plan['module'] or current.get('variant') != plan['variant']
                or current.get('recordCount') != count or current.get('readSID') != int(hx[:2], 16)
                or current.get('method') != 'first-record-service-all-record-pid-map'
                or current.get('executionEnabled') is not False or current.get('writePayload') is not None):
            raise ValueError('manufacturer-coding-group-unqualified')
        if origin is not None and origin != current:
            raise ValueError('manufacturer-coding-group-unqualified')
        origin = current
        for field in fields:
            index = field.get('sourceRecordIndex'); span = field.get('span') or {}
            if (type(index) is not int or not 0 <= index < count
                    or type(span.get('dataMin')) is not int or not 0 <= span['dataMin'] <= minimum
                    or span['dataMin'] == 0 and (span.get('kind') != 'lowerBound'
                        or span.get('unknownRemaining') is not True)):
                raise ValueError('manufacturer-coding-group-unqualified')
            indexes.append(index)
        requests.append(hx)
        result.append({key: group[key] for key in ('requestHex', 'identifierHex', 'identifierKind',
            'minimumKnownBytes', 'expectedTotalBytes')})
    if sorted(indexes) != list(range(count)) or requests != sorted(requests):
        raise ValueError('manufacturer-coding-definition-incomplete')
    output = dict(profileId=pid, ecuId=ecu, generation=generation, sourceCatalogId=SOURCE_CATALOG,
        definitionSha256=digest, definitionFieldCount=count, requestGroups=result)
    if pid in SWSG_VERSIONS:
        scope, groups = _complete_swsg_scope(profile, source, contract, result)
        output.update(completeScopeSource=scope, requestGroups=groups)
    elif source.get('module') == 'CAN_CAN_Gateway':
        from .gateway_coding_source import complete_gateway_scope
        complete = complete_gateway_scope(profile, source, contract, result)
        if complete is not None:
            scope, groups = complete
            output.update(completeScopeSource=scope, requestGroups=groups)
    elif source.get('module') == 'DME_BDE_Continental':
        from .dme_coding_source import complete_dme_scope
        complete = complete_dme_scope(profile, source, contract, result)
        if complete is not None:
            scope, groups = complete
            output.update(completeScopeSource=scope, requestGroups=groups)
    elif source.get('module') == 'Getriebesteuerung_PDK':
        from .pdk_coding_source import complete_pdk_scope
        complete = complete_pdk_scope(profile, source, contract, result)
        if complete is not None:
            scope, groups = complete
            output.update(completeScopeSource=scope, requestGroups=groups)
    elif source.get('module') in ('BCM_vorne', 'BCM_hinten', 'PSM', 'EPB', 'PASM_PADM',
            'PDC', 'Kombiinstrument_MM', 'Airbag', 'BKE', 'Steering_Control_Unit', 'Zusatzinstrument',
            'Lenkstockschalter', 'RDK', 'RDK3', 'BV_InforContrUnit1UDS',
            'BV_InforContrUnit1UDSDesay', 'BV_SoundSysteUDS', 'TVTuner', 'Verstaerker', 'Head_Unit',
            'ACC3', 'Frontkamera', 'Rueckfahrkamera_II', 'ERA_GLONASS',
            'BV_TVTunerUDS', 'Verdeck_Synchronisation', 'BV_ECM_UDS', 'BV_EnginContrModul1UDS',
            'Sitzsteuergeraet', 'TUER_VORNE', 'BV_ActuaForStrucBorneSoundUDS', 'BV_ContrUnitLaneChangAssis2UDS'):
        from .uds_coding_source import complete_uds_scope
        complete = complete_uds_scope(profile, source, contract, result)
        if complete is not None:
            scope, groups = complete
            output.update(completeScopeSource=scope, requestGroups=groups)
    return output


def read_source_coding(client, profile, contract, qualification, deadline, guard, *, require_complete_scope=False):
    """Read once, in native map order. Failures retain explicit partial coverage."""
    if (qualification.get('freshRead') is not True or qualification.get('observedProfileMatch') is not True
            or qualification.get('sourceCatalogId') != SOURCE_CATALOG
            or qualification.get('generation') != contract['generation']
            or qualification.get('ecuId') != contract['ecuId']
            or (qualification.get('variantLink') or {}).get('profile_id') != profile['profileId']):
        raise ElmError('manufacturer-coding-identity-unqualified')
    guard(); plan = source_coding_plan(profile, contract)
    if require_complete_scope and 'completeScopeSource' not in plan:
        raise ElmError('coding-complete-scope-unqualified')
    output = dict(ok=False, error=None, **{k: v for k, v in plan.items() if k != 'requestGroups'},
        blocks=[], missing=[g['requestHex'] for g in plan['requestGroups']],
        allDefinedGroupsReceived=False, freshReadProven=True, completeOriginalBackup=False,
        completeVehicleCodingScopeQualified=False, baselineCreated=False,
        completeOriginalRead=False,
        codingWriteQualified=False, codingRestoreQualified=False, liveVerified=False, writePayload=None)
    previous_allowed = set(client.allowed) if hasattr(client, 'allowed') else None
    try:
        if previous_allowed is not None:
            client.allowed = previous_allowed | {g['requestHex'] for g in plan['requestGroups']}
        for group in plan['requestGroups']:
            guard(); hx = group['requestHex']
            response = client.request(hx, min(deadline, time.monotonic()+15)); guard()
            if not response.get('ok'):
                raise ElmError(response.get('error') or 'manufacturer-coding-read-failed')
            pdu_hex = response.get('payload_hex')
            if not isinstance(pdu_hex, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){1,4095}', pdu_hex):
                raise ElmError('manufacturer-coding-response-invalid')
            raw = bytes.fromhex(hx)
            decoded = normalize_response(bytes.fromhex(pdu_hex),
                dict(readSID=raw[0], pid=int(hx[2:], 16), read_request_candidate_hex=hx), 'pdu',
                source='independent-manufacturer-coding-read')
            if not decoded.get('ok'):
                raise ElmError(decoded.get('reason') or 'manufacturer-coding-response-invalid')
            data = decoded['payload']
            # A zero source lower bound describes an unknown remaining extent,
            # not permission to publish an empty reply as original coding.
            if not data:
                raise ElmError('coding-read-empty-block')
            if len(data) < group['minimumKnownBytes']:
                raise ElmError('coding-read-short-block')
            if group['expectedTotalBytes'] is not None and len(data) != group['expectedTotalBytes']:
                raise ElmError('coding-read-block-length-mismatch')
            output['blocks'].append(dict(requestHex=hx, identifierKind=group['identifierKind'],
                identifierHex=group['identifierHex'], pduHex=pdu_hex.upper(), dataHex=data.hex().upper(),
                sha256=hashlib.sha256(data).hexdigest(), receivedUtc=datetime.now(timezone.utc).isoformat(),
                completeLengthQualified='completeScopeSource' in plan))
        output.update(ok=True, allDefinedGroupsReceived=True)
        if 'completeScopeSource' in plan:
            output.update(completeOriginalRead=True, completeVehicleCodingScopeQualified=True)
    except (ElmError, OSError) as error:
        output['error'] = str(error)
    finally:
        if previous_allowed is not None:
            client.allowed = previous_allowed
    completed = {b['requestHex'] for b in output['blocks']}
    output['missing'] = [g['requestHex'] for g in plan['requestGroups'] if g['requestHex'] not in completed]
    return output
