"""X431 routine source headers. Unknown bodies never become vehicle recipes."""
from __future__ import annotations

import hashlib
import re
from .routine_body import routine_body_layout

LEAF_KIND = 'u32count_u32leafptrs_id_leaves'
NAMESPACE = 'DSTREAM_CN.GAG'
MAX_LEAF_BYTES = 65536


def routine_definition(record):
    """Read the single-ID header observed in retained leaves, not a command."""
    result = {'status': 'unresolved', 'name': None, 'nameStatus': 'unresolved',
              'nameId': None, 'labelNamespace': NAMESPACE, 'sourceOffset': record.get('at'),
              'source': record.get('source'), 'headerBytes': None, 'bodyBytes': None,
              'headerSha256': None, 'bodySha256': None, 'nativeEntryTag': None,
              'nativeHeaderSemanticsQualified': False, 'functionClass': 'unclassified',
              'recipeQualified': False, 'executionEnabled': False, 'liveVerified': False,
              'requestHex': None, 'writePayload': None, 'bodyLayout': None}
    if record.get('payload_kind') != LEAF_KIND:
        return {**result, 'reason': 'routine-leaf-layout-unqualified'}
    raw_hex = record.get('raw_hex')
    if (not isinstance(raw_hex, str) or len(raw_hex) > MAX_LEAF_BYTES * 2
            or not re.fullmatch(r'(?:[0-9a-fA-F]{2})+', raw_hex)):
        return {**result, 'reason': 'routine-leaf-hex-invalid'}
    raw = bytes.fromhex(raw_hex)
    if len(raw) < 3:
        return {**result, 'reason': 'routine-header-truncated'}
    size = int.from_bytes(raw[1:3], 'little')
    if size != 11:
        return {**result, 'reason': 'routine-name-chain-layout-unqualified'}
    end = 3 + size
    if len(raw) < end:
        return {**result, 'reason': 'routine-header-truncated'}
    token = raw[3:end]
    if not re.fullmatch(rb'0x[0-9a-fA-F]{8}\x00', token):
        return {**result, 'reason': 'routine-name-token-invalid'}
    name_id = token[2:-1].decode('ascii').upper()
    result.update(status='source-header-only', nameId=name_id, nativeEntryTag=raw[0],
                  headerBytes=end, bodyBytes=len(raw) - end,
                  headerSha256=hashlib.sha256(raw[:end]).hexdigest(),
                  bodySha256=hashlib.sha256(raw[end:]).hexdigest(),
                  bodyLayout=routine_body_layout(raw[end:]))
    # Match only the header's ID in its recorded namespace. Body labels are not names.
    labels = record.get('labels')
    matches = [label.get('text') for label in labels if isinstance(label, dict)
               and label.get('id_hex') == name_id and label.get('namespace') == NAMESPACE] if isinstance(labels, list) else []
    if not matches:
        result.update(nameStatus='source-entry-missing', reason='routine-name-source-missing')
    elif any(not isinstance(text, str) for text in matches) or len(set(matches)) != 1:
        result.update(nameStatus='source-conflict', reason='routine-name-source-conflict')
    elif matches[0] == '':
        result.update(nameStatus='source-empty', reason='routine-name-source-empty')
    elif matches[0].startswith('SubIndexNum='):
        result.update(nameStatus='dynamic-name-unresolved', reason='routine-dynamic-name-unresolved')
    else:
        result.update(name=matches[0], nameStatus='source-label', reason='routine-recipe-and-recovery-required')
    return result
