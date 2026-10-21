"""Resolve pinned X431 coding access descriptors without opening a connection.

RESETSPEFUNMENU's family byte-count is not a record count: the original reader
walks to 0xff. Kind 0 matches TOKEN+0x40a2c8 (module), kind 1 matches
TOKEN+0x40a264 (selected variant), with the first exact match taking precedence.
The returned descriptor is input to access-client implementation, not permission
to write. It supplies no previous ECU state or implicit 'no access required'.
"""
from __future__ import annotations

import hashlib
import struct

from .x431_access_math import (transform_bde_cod_w06, transform_gateway,
    transform_psm, transform_pdk, transform_kombi_9x1, transform_bcm_v_h,
    transform_swsg, transform_rdk)

RAW_SHA256 = '1f647bebfe521c6662128682897442cf91450210082f66c2b7c44423159d9160'
LOGICAL_SHA256 = '9ec90fe46a3e1fede439bc0e3b92883c253754dd2c429120ec9fd6933814a49f'
FILE_SHA256 = '80a4e5692adefa90a6bbf210bd6b9d5715eeb6410f3ee34f3bc25994e148bbab'
READWRITE_SHA256 = '8f5aef6bf660b0b834dd85ab55e704760a21f7dde3e9d7e46ab96c6478583c7a'

# Exact parameter strings in the pinned family-13 source table. Native name
# lookup, seed packing and the actual selected algorithm bodies were executed
# together; no algorithm is chosen from a translated ECU or marketing name.
_KEY_ALGORITHMS = {
    'ASAM_seedkey_BDE_COD_W06': (4, transform_bde_cod_w06),
    'ASAM_seedkey_Gateway': (4, transform_gateway),
    'ASAM_seedkey_PSM': (2, transform_psm),
    'ASAM_seedkey_PDK': (2, transform_pdk),
    'kombi_9x1_codierung': (4, transform_kombi_9x1),
    'SeedKey_BCM_V': (4, transform_bcm_v_h),
    'SeedKey_BCM_H': (4, transform_bcm_v_h),
    'SWSG_seedkey': (4, transform_swsg),
    'ASAM_seedkey_RDK': (4, transform_rdk),
}


def _read_string(blob, at):
    if at < 0 or at + 2 > len(blob):
        raise ValueError('coding-access-string-truncated')
    size = struct.unpack_from('<H', blob, at)[0]
    end = at + 2 + size
    if not 1 <= size <= 200 or end > len(blob):
        raise ValueError('coding-access-string-size-invalid')
    raw = blob[at + 2:end]
    if raw[-1:] != b'\0' or b'\0' in raw[:-1] or not raw[:-1].isascii():
        raise ValueError('coding-access-string-invalid')
    return raw[:-1].decode('ascii'), end


def _family_records(blob, family):
    """Bounded structural reader; caller must qualify the complete input hash."""
    if len(blob) < 0x19 or type(family) is not int or not 0 <= family <= 255:
        raise ValueError('coding-access-family-invalid')
    table = struct.unpack_from('<I', blob, 0x15)[0]
    if table < 0x19 or table >= len(blob):
        raise ValueError('coding-access-family-table-invalid')
    count = blob[table]
    if table + 1 + count * 5 > len(blob):
        raise ValueError('coding-access-family-table-truncated')
    sections = [ptr for key, ptr in
        (struct.unpack_from('<BI', blob, table + 1 + i * 5) for i in range(count)) if key == family]
    if len(sections) != 1 or not sections[0]:
        raise ValueError('coding-access-family-missing-or-ambiguous')
    start = sections[0]
    if start >= len(blob):
        raise ValueError('coding-access-section-invalid')
    at = start + 1
    rows = []
    while at < len(blob) and len(rows) < 4096:
        if blob[at] == 255:
            return tuple(rows)
        if at + 6 > len(blob):
            raise ValueError('coding-access-record-truncated')
        offset = at
        kind, flag, mode = struct.unpack_from('<BBI', blob, at)
        selector, at = _read_string(blob, at + 6)
        parameter, at = _read_string(blob, at)
        rows.append((offset, kind, flag, mode, selector, parameter))
    raise ValueError('coding-access-record-sentinel-missing')


def _match(rows, module, variant):
    for offset, kind, flag, mode, selector, parameter in rows:
        if (kind == 0 and selector == module) or (kind == 1 and selector == variant):
            return dict(sourceOffset=offset, selectorKind=kind, selector=selector,
                flagByte=flag, modeWord=mode, parameter=parameter)
    return None


class CodingAccessSource:
    """Immutable, exact-version source selector; no runtime token is accepted."""
    def __init__(self, logical):
        if (type(logical) is not bytes or len(logical) != 186584
                or hashlib.sha256(logical).hexdigest() != LOGICAL_SHA256):
            raise ValueError('coding-access-source-unreviewed')
        self._rows = _family_records(logical, 13)
        if len(self._rows) != 57:
            raise ValueError('coding-access-source-record-count-mismatch')

    def resolve(self, profile_id):
        if (not isinstance(profile_id, str) or len(profile_id.split(':')) != 3
                or profile_id.split(':')[0] != '9x1'
                or any(not part or not part.isascii() or '\0' in part
                    or len(part) >= 100 for part in profile_id.split(':')[1:])):
            raise ValueError('coding-access-profile-invalid')
        _, module, variant = profile_id.split(':')
        found = _match(self._rows, module, variant)
        return dict(kind='x431-coding-access-source', profileId=profile_id,
            familyId=13, sourceFile='RESETSPEFUNMENU.BIN', sourceVersion='V24.58',
            rawSha256=RAW_SHA256, logicalSha256=LOGICAL_SHA256, fileSha256=FILE_SHA256,
            readwriteSha256=READWRITE_SHA256, match=found,
            accessDefinitionMatched=found is not None,
            # Missing descriptors must be resolved from other source definitions.
            # A matched name alone does not qualify algorithm inputs or a client.
            securityAccessEnabled=False, codingWriteQualified=False,
            codingRestoreQualified=False, executionEnabled=False)

    def key_for_seed(self, profile_id, seed):
        """Pure calculation for a source-matched profile and exact seed bytes.

        The caller must separately establish a fresh positive response, its
        subfunction and ownership. This method opens no session, accepts no
        transport, and never turns the result into a vehicle request.
        """
        found = self.resolve(profile_id)['match']
        if found is None:
            raise ValueError('coding-access-definition-missing')
        algorithm = _KEY_ALGORITHMS.get(found['parameter'])
        if algorithm is None or algorithm[0] != found['flagByte']:
            raise ValueError('coding-access-algorithm-unqualified')
        width, calculate = algorithm
        if type(seed) is not bytes or len(seed) != width:
            raise ValueError('coding-access-seed-width-invalid')
        return calculate(int.from_bytes(seed, 'big')).to_bytes(width, 'big')
