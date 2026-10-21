"""Complete readable Gateway coding records; OEM job inputs are not ECU values.

Source/method/length proof is retained in the task-local Gateway source audit.
The additional A7_1 Filterstatus record has no qualified factory length and is
intentionally absent. This module neither sends requests nor authorizes writes.
"""
import json
from pathlib import Path

SCOPES = Path(__file__).with_name('gateway-coding-scopes.json')


def complete_gateway_scope(profile, source, contract, groups):
    document = json.loads(SCOPES.read_text(encoding='utf8'))
    if document.get('schemaVersion') != 1 or document.get('kind') != 'reviewed-gateway-coding-read-scopes':
        raise ValueError('coding-complete-scope-unqualified')
    rows = [v for v in document['profiles'] if v['profileId'] == profile['profileId']]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('coding-complete-scope-unqualified')
    row = rows[0]
    if (source.get('module') != 'CAN_CAN_Gateway' or source.get('ecuIds') != [9]
            or contract.get('ecuId') != 9 or contract.get('generation') not in ('981', '982')
            or contract.get('sourceQualification') != 'uds-source-version-and-identity'
            or contract.get('applicationProtocol') != 'uds'
            or (contract.get('txId'), contract.get('rxId')) != (0x710, 0x77A)
            or contract.get('identityConstraints') != row['identityConstraints']
            or contract.get('sourceCommands') != ['3E00', '22F1A2', '22F19E', '22F1DF', '1003']
            or contract.get('session') != {'requestHex':'1003', 'positivePrefixHex':'5003'}
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
