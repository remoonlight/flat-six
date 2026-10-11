"""Prefix/suffix field parse. No send, no write payload."""
from __future__ import annotations

import struct

CODING_DATA_SHA256 = 'f32173d768ca9f8d2caf109bc7159fa43bde816f924fea5a91f1f202a2a76a2e'
CODING_READWRITE_SHA256 = '8f5aef6bf660b0b834dd85ab55e704760a21f7dde3e9d7e46ab96c6478583c7a'


def parse_prefix7(prefix: bytes) -> dict:
    if len(prefix) != 7:
        return {"ok": False, "error": f"prefix_len_{len(prefix)}"}
    sid = prefix[0]
    companion = struct.unpack_from("<H", prefix, 1)[0]
    pid = struct.unpack_from("<I", prefix, 3)[0]
    return {
        "ok": True,
        "readSID": sid,
        "companionSID": companion,
        "pid": pid,
        "error": None,
    }


def derived_read_request(prefix: bytes) -> dict:
    """Native SendUdsReadSidPidCmd: 1A/21 one PID byte; 22 two PID bytes high-first.

    Zero PID is valid metadata. Overflow / unsupported SID rejected (no silent truncate).
    """
    p = parse_prefix7(prefix)
    if not p["ok"]:
        return {**p, "request_hex": None, "status": "malformed_prefix"}
    sid, pid = p["readSID"], p["pid"]
    if sid in (0x1A, 0x21):
        if pid > 0xFF:
            return {**p, "request_hex": None, "status": "pid_overflow_u8"}
        req = bytes([sid, pid]).hex().upper()
        return {**p, "request_hex": req, "status": "ok"}
    if sid == 0x22:
        if pid > 0xFFFF:
            return {**p, "request_hex": None, "status": "pid_overflow_u16"}
        req = bytes([0x22, (pid >> 8) & 0xFF, pid & 0xFF]).hex().upper()
        return {**p, "request_hex": req, "status": "ok"}
    return {**p, "request_hex": None, "status": "unsupported_sid"}


def _coding_prefix(record):
    try:
        prefix = bytes.fromhex(record['request_prefix_hex'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('coding-read-origin-prefix-invalid') from error
    parsed = parse_prefix7(prefix)
    if not parsed['ok'] or any(type(record.get(k)) is not int or record[k] != parsed[k]
                               for k in ('readSID', 'companionSID', 'pid')):
        raise ValueError('coding-read-origin-prefix-mismatch')
    return prefix


def coding_profile_records(records, *, table_offset=None, record_count=None):
    """Bind the reviewed full coding table to its first record's read service.

    READWRITE PorscheReadSysCodeData -> GetOdxXmlMcrCodeInfoEx ->
    PorscheReadAndReturnCodeEx passes the first vector entry's SID for the
    entire PID map. Original per-record prefixes and derived candidates remain
    separately visible. This is offline source provenance, never live approval.
    """
    if not records:
        return records
    sources = [record.get('source') or {} for record in records]
    reviewed = [str(source.get('sha256', '')).lower() == CODING_DATA_SHA256 for source in sources]
    if not any(reviewed):
        if any('codingReadOrigin' in record for record in records):
            raise ValueError('coding-read-origin-source-unreviewed')
        return records
    first, source = records[0], sources[0]
    if (not all(reviewed) or not 1 <= len(records) <= 10000
            or source.get('file') != '9X1_ALLDATA.BIN.dec'
            or any(not isinstance(source.get(k), str) or not source[k] for k in ('module', 'variant'))
            or any(s != source for s in sources)
            or type(first.get('at')) is not int or first['at'] < 4):
        raise ValueError('coding-read-origin-profile-source-mismatch')
    prefix = _coding_prefix(first)
    if (type(table_offset) is not int or table_offset != first['at'] - 4
            or type(record_count) is not int or record_count != len(records)):
        raise ValueError('coding-read-origin-complete-table-required')
    if prefix[0] not in (0x21, 0x22):
        raise ValueError('coding-read-origin-first-service-unreviewed')
    common = {'kind': 'x431-coding-profile-first-record', 'schemaVersion': 1,
        'sourceSha256': CODING_DATA_SHA256, 'readwriteSha256': CODING_READWRITE_SHA256,
        'module': source['module'], 'variant': source['variant'],
        'tableOffset': first['at'] - 4, 'firstRecordOffset': first['at'],
        'firstPrefixHex': first['request_prefix_hex'], 'recordCount': len(records),
        'readSID': prefix[0], 'method': 'first-record-service-all-record-pid-map',
        'actualDynamicBindingQualified': False, 'executionEnabled': False, 'writePayload': None}
    output, previous = [], first['at'] - 1
    for index, record in enumerate(records):
        raw = _coding_prefix(record)
        if type(record.get('at')) is not int or record['at'] <= previous:
            raise ValueError('coding-read-origin-record-order-invalid')
        previous = record['at']
        origin = {**common, 'recordIndex': index}
        original = derived_read_request(raw)['request_hex']
        resolved = derived_read_request(bytes([prefix[0]]) + raw[1:])
        if resolved['status'] != 'ok':
            raise ValueError('coding-read-origin-' + resolved['status'])
        if 'codingReadOrigin' in record:
            if (record['codingReadOrigin'] != origin
                    or record.get('sourceReadRequestCandidateHex') != original
                    or record.get('read_request_candidate_hex') != resolved['request_hex']):
                raise ValueError('coding-read-origin-existing-binding-mismatch')
        elif record.get('read_request_candidate_hex') != original:
            raise ValueError('coding-read-origin-original-candidate-mismatch')
        output.append({**record, 'codingReadOrigin': origin,
            'sourceReadRequestCandidateHex': original,
            'read_request_candidate_hex': resolved['request_hex']})
    return output


def coding_profile_variant(variant):
    """Nonmutating preparation shared by retained-source readers and generators."""
    pools = variant.get('pool_records') or {}
    coding = pools.get('coding') or {}
    records = coding.get('records') or []
    if not records and coding.get('count'):
        raise ValueError('coding-read-origin-complete-table-required')
    bound = coding_profile_records(records, table_offset=coding.get('pointer'), record_count=coding.get('count'))
    if bound is records:
        return variant
    if any(record['codingReadOrigin']['module'] != variant.get('module')
           or record['codingReadOrigin']['variant'] != variant.get('name') for record in bound):
        raise ValueError('coding-read-origin-profile-identity-mismatch')
    return {**variant, 'pool_records': {**pools, 'coding': {**coding, 'records': bound}}}


def coding_profile_request(record):
    """Validate a prepared field's source context without inventing a SID alias."""
    origin = record.get('codingReadOrigin')
    if not isinstance(origin, dict):
        raise ValueError('coding-read-origin-invalid')
    source = record.get('source') or {}
    required = {'kind', 'schemaVersion', 'sourceSha256', 'readwriteSha256', 'module', 'variant',
        'tableOffset', 'firstRecordOffset', 'firstPrefixHex', 'recordCount', 'readSID', 'method',
        'actualDynamicBindingQualified', 'executionEnabled', 'writePayload', 'recordIndex'}
    if (set(origin) != required or origin['kind'] != 'x431-coding-profile-first-record'
            or type(origin['schemaVersion']) is not int or origin['schemaVersion'] != 1
            or origin['sourceSha256'] != CODING_DATA_SHA256
            or str(source.get('sha256', '')).lower() != CODING_DATA_SHA256
            or source.get('file') != '9X1_ALLDATA.BIN.dec'
            or origin['readwriteSha256'] != CODING_READWRITE_SHA256
            or any(not isinstance(origin[k], str) or not origin[k] or origin[k] != source.get(k)
                   for k in ('module', 'variant'))
            or origin['method'] != 'first-record-service-all-record-pid-map'
            or origin['actualDynamicBindingQualified'] is not False
            or origin['executionEnabled'] is not False or origin['writePayload'] is not None
            or any(type(origin[k]) is not int for k in ('tableOffset', 'firstRecordOffset', 'recordCount', 'recordIndex', 'readSID'))
            or not 1 <= origin['recordCount'] <= 10000
            or not 0 <= origin['recordIndex'] < origin['recordCount']
            or origin['tableOffset'] < 0 or origin['firstRecordOffset'] != origin['tableOffset'] + 4
            or origin['readSID'] not in (0x21, 0x22)
            or any(k in record for k in ('rawSID', 'wireSID', 'disabledreadrequestcandidate'))):
        raise ValueError('coding-read-origin-context-invalid')
    raw = _coding_prefix(record)
    try:
        first = bytes.fromhex(origin['firstPrefixHex'])
    except (TypeError, ValueError) as error:
        raise ValueError('coding-read-origin-first-prefix-invalid') from error
    if (len(first) != 7 or first[0] != origin['readSID']
            or type(record.get('at')) is not int
            or (origin['recordIndex'] == 0 and (record['at'] != origin['firstRecordOffset'] or raw != first))
            or (origin['recordIndex'] > 0 and record['at'] <= origin['firstRecordOffset'])):
        raise ValueError('coding-read-origin-record-context-mismatch')
    resolved = derived_read_request(bytes([first[0]]) + raw[1:])
    if (resolved['status'] != 'ok'
            or record.get('sourceReadRequestCandidateHex') != derived_read_request(raw)['request_hex']
            or record.get('read_request_candidate_hex') != resolved['request_hex']
            or record.get('requestHex', resolved['request_hex']) != resolved['request_hex']):
        raise ValueError('coding-read-origin-request-mismatch')
    return bytes.fromhex(resolved['request_hex'])


def parse_coding_suffix8(suf: bytes) -> dict:
    if len(suf) != 8:
        return {"ok": False, "error": f"suffix_len_{len(suf)}"}
    property_b = suf[0]
    byte_off = struct.unpack_from("<H", suf, 1)[0]
    bit_off = suf[3]
    formula_id = struct.unpack_from("<I", suf, 4)[0]
    return {
        "ok": True,
        "propertyByte": property_b,
        "property_status": "unresolved",
        "byteOffset": byte_off,
        "bitOffset": bit_off,
        "bit_semantics": "lsb_within_one_byte_proven",
        "formula_id": formula_id,
        "formula_id_hex": f"{formula_id:08X}",
    }


def parse_identity_suffix7(suf: bytes) -> dict:
    """VERF GetOdxXmlVesInfoEx stores u16/u8/u32 at +58/+5a/+5c.

    PorscheVerInfo 0x129c6..0x129f2 passes the first two as the
    byte/bit arguments to PorscheVerCalcOdx. Static proof, not live approval.
    """
    if len(suf) != 7:
        return {"ok": False, "error": f"suffix_len_{len(suf)}"}
    formula_id = struct.unpack_from("<I", suf, 3)[0]
    return {
        "ok": True,
        "head_hex": suf[:3].hex(),
        "head_status": "native_layout_confirmed",
        "byteOffset": struct.unpack_from("<H", suf, 0)[0],
        "bitOffset": suf[2],
        "formula_id": formula_id,
        "formula_id_hex": f"{formula_id:08X}",
    }
