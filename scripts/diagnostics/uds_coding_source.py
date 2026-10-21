"""Complete readable coding scopes for reviewed source versions.

PIWIS dataset membership and exact lengths were checked against the original
DLL. The original filename is retained for the shared UDS/KWP catalog.
This module neither sends requests nor authorizes writes.
"""
import json
import hashlib
import re
from pathlib import Path

from scripts.x431_re.request_fields import derived_read_request

SCOPES = Path(__file__).with_name('uds-coding-scopes.json')


def coding_field_read_bindings(source, row):
    """Bind reviewed factory reads to each immutable original field request.

    Some old head units mix local/common identifiers within a coding table.
    Its first-record plan is retained; this separate binding requires every
    original field in the group to agree with the factory request.
    """
    bindings = row.get('fieldReadBindings')
    if bindings is None:
        return {}
    coding = source.get('coding') or {}
    groups = coding.get('requestGroups') or []
    if (not isinstance(bindings, list) or not bindings or len(bindings) != len(groups)
            or coding.get('definitionSha256') != row['definitionSha256']
            or hashlib.sha256(json.dumps(bindings, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
                != row.get('fieldReadBindingsSha256')):
        raise ValueError('coding-complete-scope-conflict')
    mapping = {}; factory = set()
    for group, bound in zip(groups, bindings):
        request = bound.get('factoryReadRequestHex')
        fields = group.get('fields') or []
        if (bound.get('sourcePlanRequestHex') != group.get('requestHex')
                or group['requestHex'] in mapping or request in factory
                or not isinstance(request, str) or not re.fullmatch(r'(?:21[0-9A-F]{2}|22[0-9A-F]{4})', request)
                or not fields or bound.get('fields') != [{k: f.get(k) for k in
                    ('sourceOffset', 'originalPrefixHex', 'originalCandidateHex', 'sourceRecordIndex')} for f in fields]):
            raise ValueError('coding-complete-scope-conflict')
        for field in fields:
            try:
                original = derived_read_request(bytes.fromhex(field['originalPrefixHex']))
            except (KeyError, TypeError, ValueError):
                raise ValueError('coding-complete-scope-conflict') from None
            if (original.get('status') != 'ok' or original.get('request_hex') != request
                    or field.get('originalCandidateHex') != request
                    or field.get('originalRecordReadSID') != int(request[:2], 16)):
                raise ValueError('coding-complete-scope-conflict')
        mapping[group['requestHex']] = request; factory.add(request)
    return mapping


def complete_uds_scope(profile, source, contract, groups):
    document = json.loads(SCOPES.read_text(encoding='utf8'))
    if document.get('schemaVersion') != 1 or document.get('kind') != 'reviewed-source-coding-read-scopes':
        raise ValueError('coding-complete-scope-unqualified')
    rows = [v for v in document['profiles'] if v['profileId'] == profile['profileId']]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('coding-complete-scope-unqualified')
    shared = rows[0]
    row = shared
    if 'contexts' in shared:
        contexts = shared['contexts']
        if (not isinstance(contexts, list) or [r.get('ecuId') for r in contexts] != shared.get('ecuIds')
                or len(set(shared['ecuIds'])) != len(contexts)):
            raise ValueError('coding-complete-scope-unqualified')
        selected = [r for r in contexts if r.get('ecuId') == contract.get('ecuId')]
        if len(selected) != 1:
            raise ValueError('coding-complete-scope-unqualified')
        row = {**shared, **selected[0]}
    if 'sourceLinks' in row:
        selected_link = profile.get('selectedSourceLink', 0)
        links = row['sourceLinks']
        if (type(selected_link) is not int or not isinstance(links, list)
                or [link.get('linkIndex') for link in links] != [0, 1]
                or selected_link not in (0, 1)):
            raise ValueError('coding-complete-scope-unqualified')
        row = {**row, **links[selected_link]}
    if (source.get('module') != row['module'] or source.get('ecuIds') != shared.get('ecuIds', [row['ecuId']])
            or contract.get('ecuId') != row['ecuId'] or contract.get('generation') not in row['generations']
            or any(contract.get(k) != row.get(k) for k in ('sourceQualification', 'applicationProtocol',
                'txId', 'rxId', 'identityConstraints', 'sourceCommands', 'session'))
            or row.get('identityLayoutResolvedByDsn') is not None
                and contract.get('identityLayoutResolvedByDsn') != row['identityLayoutResolvedByDsn']
            or row.get('transportAddressing') is not None
                and contract.get('transportAddressing') != row['transportAddressing']
            or (contract.get('identityRequests') != row['identityLayout'] if 'identityLayout' in row else
                [(r.get('requestHex'), r.get('record', {}).get('at')) for r in contract.get('identityRequests', [])]
                != [(r['requestHex'], r['sourceOffset']) for r in row['identityFields']])
            or source.get('coding', {}).get('definitionSha256') != row['definitionSha256']
            or row.get('versionSelectors') is not None and contract.get('versionSelectors') != row['versionSelectors']
            or source.get('coding', {}).get('definitionFieldCount') != row['definitionFieldCount']):
        raise ValueError('coding-complete-scope-unqualified')
    scope = row['completeScopeSource']
    bindings = coding_field_read_bindings(source, row)
    if bindings:
        if ({g['requestHex'] for g in groups} != set(bindings)
                or any(g.get('identifierKind') != ('LID' if g['requestHex'].startswith('21') else 'DID')
                       or g.get('identifierHex') != g['requestHex'][2:] for g in groups)):
            raise ValueError('coding-complete-scope-conflict')
        groups = [{**g, 'requestHex': bindings[g['requestHex']],
                   'identifierHex': bindings[g['requestHex']][2:],
                   'identifierKind': 'LID' if bindings[g['requestHex']].startswith('21') else 'DID'} for g in groups]
    lengths = {v['requestHex']: v['bytes'] for v in scope['requestLengths']}
    extras = row.get('factoryOnlyReadRequests', [])
    extra_keys = {v['requestHex'] for v in extras}
    if (len(extra_keys) != len(extras) or any(v.get('kind') != 'piwis-config-read-primitive-default'
            or v.get('readSID') != 0x22 or type(v.get('identifier')) is not int
            or not 0 <= v['identifier'] <= 65535 or v['requestHex'] != f"22{v['identifier']:04X}"
            or v['requestHex'] not in lengths or not 1 <= lengths[v['requestHex']] <= 4092 for v in extras)
            or {g['requestHex'] for g in groups} != set(lengths) - extra_keys
            or any(g.get('identifierKind') != ('LID' if g['requestHex'].startswith('21') else 'DID')
                   or g.get('identifierHex') != g['requestHex'][2:] for g in groups)
            or any(g['minimumKnownBytes'] > lengths[g['requestHex']]
                   or g['expectedTotalBytes'] not in (None, lengths[g['requestHex']]) for g in groups)):
        raise ValueError('coding-complete-scope-conflict')
    # Unnamed factory bytes/groups remain part of the original. Additional
    # reads come only from the reviewed factory primitive/default bindings.
    known = {g['requestHex']: g for g in groups}
    return scope, [{**known[h], 'expectedTotalBytes': n} if h in known else
        dict(requestHex=h, identifierKind='DID', identifierHex=h[2:],
             minimumKnownBytes=n, expectedTotalBytes=n) for h, n in sorted(lengths.items())]
