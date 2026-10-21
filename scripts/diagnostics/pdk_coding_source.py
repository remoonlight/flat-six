"""Complete readable coding scopes for eight normal PDK source versions.

PIWIS dataset membership and exact lengths were checked against the original
DLL. This module neither sends requests nor authorizes writes.
"""
import json
from pathlib import Path

SCOPES = Path(__file__).with_name('pdk-coding-scopes.json')


def complete_pdk_scope(profile, source, contract, groups):
    document = json.loads(SCOPES.read_text(encoding='utf8'))
    if document.get('schemaVersion') != 1 or document.get('kind') != 'reviewed-pdk-coding-read-scopes':
        raise ValueError('coding-complete-scope-unqualified')
    rows = [v for v in document['profiles'] if v['profileId'] == profile['profileId']]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('coding-complete-scope-unqualified')
    row = rows[0]
    if (source.get('module') != 'Getriebesteuerung_PDK' or source.get('ecuIds') != [2]
            or contract.get('ecuId') != 2 or contract.get('generation') not in ('981', '982')
            or any(contract.get(k) != row.get(k) for k in ('sourceQualification', 'applicationProtocol',
                'txId', 'rxId', 'identityConstraints', 'sourceCommands', 'session'))
            or [(r.get('requestHex'), r.get('record', {}).get('at')) for r in contract.get('identityRequests', [])]
                != [(r['requestHex'], r['sourceOffset']) for r in row['identityFields']]
            or source.get('coding', {}).get('definitionSha256') != row['definitionSha256']
            or source.get('coding', {}).get('definitionFieldCount') != row['definitionFieldCount']):
        raise ValueError('coding-complete-scope-unqualified')
    scope = row['completeScopeSource']
    lengths = {v['requestHex']: v['bytes'] for v in scope['requestLengths']}
    if ({g['requestHex'] for g in groups} != set(lengths)
            or any(g['minimumKnownBytes'] > lengths[g['requestHex']]
                   or g['expectedTotalBytes'] not in (None, lengths[g['requestHex']]) for g in groups)):
        raise ValueError('coding-complete-scope-conflict')
    # Factory bytes beyond the named X431 fields remain part of the original.
    return scope, [{**g, 'expectedTotalBytes':lengths[g['requestHex']]} for g in groups]
