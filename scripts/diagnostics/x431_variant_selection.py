"""Inspect X431 version rules and explicit native vectors offline; never qualify an ECU."""
import argparse
import hashlib
import json
import re
from pathlib import Path


REVIEWED_DSN_SHA256 = '55e87f5aee37ac5e6a2566434c3e78b3cd3ce1fda86ce70fe9fda981d802291f'
REVIEWED_LANGUAGE_SHA256 = '7caf1c27e475511e4a75db8fe92008684f5632eeb9fbb4484cbc16fa0eec7de3'
_REVIEWED_VERSION_FORMULAS = frozenset({
    0x80000001, 0x80000002, 0xA0000001, 0x8000002A, 0x8000000A, 0x8000021D,
    0x80000007, 0x80000423, 0x8000056E, 0x80000819, 0xF000000D, 0xF0000009,
    0x400001E4, 0x40000008,
})
_GUARDS = {
    'ecuIdentityQualified': False, 'ecuVersionQualified': False,
    'freshReadProven': False, 'completeVehicleCodingScopeQualified': False,
    'executionEnabled': False, 'writePayload': None,
}

# Retained 0x1b654 tail consumes TOKEN+0x644, not automatically the DSN
# selector's formula ID. These source branch destinations are provenance only.
_LEGACY_TAIL_FAMILY_TARGETS = {
    1: 0x1bafe, 2: 0x1bafe, 3: 0x1b924, 4: 0x1baa4, 5: 0x1b862,
    6: 0x1baa4, 7: 0x1b9f8, 8: 0x1b9f8, 9: 0x1b9f8, 10: 0x1bc08,
    11: 0x1b964, 12: 0x1bbaa, 13: 0x1b8d2, 14: 0x1bc68, 15: 0x1ba4e,
}


def classify_version_formula_route(formula_id: int):
    """Expose formula provenance without equating distinct native ID fields.

    GetCalcFormSidPid's special paths are separate from reviewed EXPRESS.GAG
    formulas. Legacy TOKEN tail branch presence is never parser coverage.
    """
    if type(formula_id) is not int or not 0<=formula_id<=0xffffffff:
        raise ValueError('version-formula-id-uint32-required')
    family=formula_id>>24
    reviewed=formula_id in _REVIEWED_VERSION_FORMULAS
    if family==0x7f:
        route='special-dsn-unqualified'
    elif family==0x8f:
        route='special-1a87-dsn-unqualified'
    elif formula_id==0xff010001:
        route='legacy-zero-or-ff-text-unqualified'
    elif formula_id==0xff010002:
        route='legacy-three-byte-text-unqualified'
    else:
        route='reviewed-expression' if reviewed else 'unreviewed-expression'
    return {'formulaId':f'{formula_id:08X}','nativeSelectorHelperEntry':0x2b4b0,
            'sourceRoute':route,'offlineCalculatorImplemented':reviewed,
            'reviewedLanguageSha256':REVIEWED_LANGUAGE_SHA256 if reviewed else None,
            'legacyTokenTailFamilyTargetCandidate':_LEGACY_TAIL_FAMILY_TARGETS.get(family),
            'legacyTokenIdEqualsSelectorIdProven':False,
            'nativeFormulaRuntimeQualified':False,**_GUARDS}


def parse_version_definition(data: bytes, offset: int):
    """Read one DSN BV block and its bounded mode-rule tables.

    GetInfoFromBvAdd reads an unsigned byte count, string plus 11 raw bytes for
    each selector, then an unsigned byte count and string/offset for each variant.
    GetInfoFromModeAdd reads a signed 16-bit count and length-prefixed strings.
    Unknown formula bodies and actual response-vector origins stay unqualified.
    """
    if not isinstance(data, bytes) or type(offset) is not int:
        raise ValueError('version-definition-input-invalid')

    def take(pos, size):
        if pos < 0 or size < 0 or pos + size > len(data):
            raise ValueError('version-definition-truncated')
        return data[pos:pos + size]

    def text(pos):
        length = int.from_bytes(take(pos, 2), 'little')
        if not 1 <= length <= 512:
            raise ValueError('version-definition-string-length-invalid')
        raw = take(pos + 2, length)
        # ReadStrFromFile advances by the full stored length. Its callers build
        # a C string, so zero padding after the first terminator is not text.
        terminator = raw.find(b'\0')
        if terminator < 0 or any(raw[terminator:]):
            raise ValueError('version-definition-string-termination-invalid')
        try:
            value = raw[:terminator].decode('ascii')
        except UnicodeDecodeError as error:
            raise ValueError('version-definition-string-encoding-invalid') from error
        return value, pos + 2 + length

    count = take(offset, 1)[0]
    pos, selectors = offset + 1, []
    for _ in range(count):
        label, pos = text(pos)
        fields = take(pos, 11)
        left = int.from_bytes(fields[:2], 'little', signed=True)
        right = int.from_bytes(fields[2:4], 'little', signed=True)
        selectors.append({
            'label': label, 'sourceOffset': pos,
            'leftWordSigned': left, 'rightWordSigned': right,
            'nativeResponseMapKey': (left + right) & 65535,
            'byteOffsetSigned': int.from_bytes(fields[4:6], 'little', signed=True),
            'bitOffsetByte': fields[6], 'formulaId': int.from_bytes(fields[7:], 'little'),
            'responseOriginAndFormulaQualified': False,
        })
        pos += 11
    # Native code returns before reading a variant count when selectors are empty.
    variant_count = take(pos, 1)[0] if count else 0
    if count:
        pos += 1
    variants = []
    for _ in range(variant_count):
        name, pos = text(pos)
        target = int.from_bytes(take(pos, 4), 'little')
        pos += 4
        rule_count = int.from_bytes(take(target, 2), 'little', signed=True)
        if rule_count < 0:
            raise ValueError('version-definition-rule-count-invalid')
        cursor, rules = target + 2, []
        for _ in range(rule_count):
            rule, cursor = text(cursor)
            rules.append(rule)
        variants.append({'name': name, 'ruleOffset': target, 'rules': rules,
                         'sourceDeclaredRuleLoopBounded': True,
                         'nativeRuntimeTerminationQualified': False})
    digest = hashlib.sha256(data).hexdigest()
    return {
        'kind': 'x431-offline-version-definition', 'sourceSha256': digest,
        'matchesReviewedSource': digest == REVIEWED_DSN_SHA256,
        'definitionOffset': offset, 'indexEnd': pos,
        'selectors': selectors, 'variants': variants, **_GUARDS,
    }


def substitute_version_rule(pattern: str, values: list[str]):
    """MinStr '#' segments: empty segments reuse the corresponding input.

    The native comparison checks a nonempty rule vector against the input prefix;
    it may leave later input fields unchecked. Empty input values are preserved,
    but cannot be used to establish ECU identity or a complete version response.
    """
    if not isinstance(pattern, str) or not isinstance(values, list):
        raise ValueError('version-rule-input-invalid')
    for value in [pattern, *values]:
        if not isinstance(value, str) or '\0' in value or not value.isascii():
            raise ValueError('version-rule-string-invalid')
    if not values or not pattern:
        return []
    segments = pattern.split('#')
    if pattern.endswith('#'):
        segments.pop()  # Native trailing empty buffer is not emitted twice.
    result = []
    for index, segment in enumerate(segments):
        if len(segment) >= 256:
            raise ValueError('version-rule-segment-too-long')
        if not segment:
            if index >= len(values):
                raise ValueError('version-rule-placeholder-out-of-range')
            result.append(values[index])
        else:
            result.append(segment)
    return result


def match_version_rules(definition: dict, values: list[str]):
    """Compare explicit, already-calculated offline values with every variant.

    Never choose a unique live version, persist a baseline, infer missing response
    fields, or execute native code. Preserve all matching
    candidates, including prefix-only and empty-value matches.
    """
    if not isinstance(definition, dict) or not isinstance(values, list):
        raise ValueError('version-selection-input-invalid')
    selectors, variants = definition.get('selectors'), definition.get('variants')
    if not isinstance(selectors, list) or not isinstance(variants, list):
        raise ValueError('version-selection-definition-invalid')
    if len(values) != len(selectors):
        raise ValueError('version-selection-field-count-mismatch')
    if any(not isinstance(v, str) or not v.isascii() or '\0' in v or len(v) >= 256 for v in values):
        raise ValueError('version-selection-values-invalid')
    candidates, errors = [], []
    for variant in variants:
        matches = []
        for index, pattern in enumerate(variant['rules']):
            try:
                expanded = substitute_version_rule(pattern, values)
            except ValueError as error:
                errors.append({'variant': variant['name'], 'ruleIndex': index, 'error': str(error)})
                continue
            if expanded and len(expanded) <= len(values) and values[:len(expanded)] == expanded:
                matches.append({'ruleIndex': index, 'comparedFieldCount': len(expanded),
                                'allFieldsCompared': len(expanded) == len(values)})
        if matches:
            candidates.append({'variant': variant['name'], 'ruleOffset': variant['ruleOffset'],
                               'matchingRules': matches})
    return {
        'kind': 'x431-offline-version-rule-match',
        'sourceSha256': definition.get('sourceSha256'),
        'definitionOffset': definition.get('definitionOffset'),
        'matchesReviewedSource': definition.get('matchesReviewedSource', False),
        'inputSource': 'explicit-offline-calculated-values',
        'inputFieldCount': len(values), 'hasEmptyInputFields': any(not v for v in values),
        'valueFingerprints': [hashlib.sha256(v.encode('ascii')).hexdigest() for v in values],
        'matchingCandidates': candidates, 'matchingCandidateCount': len(candidates),
        'sourceFirstMatchingCandidate': candidates[0]['variant'] if candidates else None,
        'selectedVehicleVariant': None, 'errors': errors,
        'sourceDeclaredRuleLoopBounded': True,
        'nativeNonmatchLoopQualified': False, **_GUARDS,
    }


def audit_version_vectors(definition: dict, vectors: list[dict]):
    """Audit explicit native map vectors, whose identifier bytes are retained.

    This input is neither a complete application PDU nor stripped coding data.
    Never infer freshness from the shared map, calculate unknown formulas, or
    promote vector presence into an ECU version or complete original backup.
    """
    if not isinstance(definition, dict) or not isinstance(definition.get('selectors'), list):
        raise ValueError('version-vector-definition-invalid')
    if not isinstance(vectors, list) or len(vectors) > 256:
        raise ValueError('version-vectors-input-invalid')
    groups, unresolved = {}, []
    for index, selector in enumerate(definition['selectors']):
        left, right = selector['leftWordSigned'] & 65535, selector['rightWordSigned'] & 65535
        if left == 0x22:
            request, identifier = bytes([left]) + right.to_bytes(2, 'big'), right.to_bytes(2, 'big')
        elif left in (0x21, 0x1a) and right <= 255:
            request, identifier = bytes([left, right]), bytes([right])
        else:
            unresolved.append({'selectorIndex': index, 'reason': 'version-selector-read-pair-unqualified'})
            continue
        hx = request.hex().upper()
        group = groups.setdefault(hx, {'requestCandidateHex': hx, 'nativeResponseMapKey': (left + right) & 65535,
                                      'selectorIndices': [], 'identifierPrefixHex': identifier.hex().upper(),
                                      'vectorPresent': False, 'identifierPrefixMatches': False})
        group['selectorIndices'].append(index)
    by_key = {}
    for group in groups.values():
        by_key.setdefault(group['nativeResponseMapKey'], []).append(group['requestCandidateHex'])
    collisions = [dict(nativeResponseMapKey=key, requests=items) for key, items in by_key.items() if len(items) > 1]
    seen, errors = set(), []
    for item in vectors:
        if not isinstance(item, dict) or set(item) != {'requestHex', 'vectorHex'}:
            raise ValueError('version-vector-row-invalid')
        hx, value = item['requestHex'], item['vectorHex']
        if (not isinstance(hx, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){2,3}', hx)
                or not isinstance(value, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){1,512}', value)):
            raise ValueError('version-vector-hex-invalid')
        hx = hx.upper()
        if hx in seen:
            raise ValueError('version-vector-duplicate-request')
        seen.add(hx)
        if hx not in groups:
            errors.append({'requestHex': hx, 'reason': 'version-vector-outside-definition'})
            continue
        group, raw = groups[hx], bytes.fromhex(value)
        group.update(vectorPresent=True, vectorBytes=len(raw), vectorSha256=hashlib.sha256(raw).hexdigest(),
                     identifierPrefixMatches=raw.startswith(bytes.fromhex(group['identifierPrefixHex'])))
        if not group['identifierPrefixMatches']:
            errors.append({'requestHex': hx, 'reason': 'version-vector-identifier-prefix-mismatch'})
        for index in group['selectorIndices']:
            offset = definition['selectors'][index]['byteOffsetSigned']
            if offset < 0 or offset >= len(raw):
                errors.append({'requestHex': hx, 'selectorIndex': index, 'reason': 'version-vector-offset-outside-buffer'})
    return {'kind': 'x431-offline-version-vector-audit', 'sourceSha256': definition.get('sourceSha256'),
            'definitionOffset': definition.get('definitionOffset'),
            'matchesReviewedSource': definition.get('matchesReviewedSource', False),
            'inputSource': 'explicit-native-map-value-vectors', 'requestGroups': list(groups.values()),
            'missingRequests': [hx for hx, group in groups.items() if not group['vectorPresent']],
            'mapKeyCollisions': collisions, 'unresolvedSelectors': unresolved, 'errors': errors,
            'formulaRoutes': [dict(selectorIndex=index,**classify_version_formula_route(selector['formulaId']))
                              for index,selector in enumerate(definition['selectors'])],
            'responseOriginAndFormulaQualified': False, 'selectedVehicleVariant': None,
            'fieldValuesCalculated': False, **_GUARDS}


def _load_version_language(language_path: Path):
    from scripts.x431_re.gag_lib import GgpLanguage

    if hashlib.sha256(Path(language_path).read_bytes()).hexdigest() != REVIEWED_LANGUAGE_SHA256:
        raise ValueError('version-calculation-language-source-unreviewed')
    language = GgpLanguage(Path(language_path))
    # The existing reader hashes the path separately. Check its parsed raw buffer
    # as well, so a file change between those reads cannot qualify different bytes.
    if hashlib.sha256(language.raw).hexdigest() != REVIEWED_LANGUAGE_SHA256:
        raise ValueError('version-calculation-language-source-changed')
    return language


_VERSION_MINMAX_SOURCE = {
    0x400001E4: (3, 25, 'c513a4e10678b4faad0b2f878d59703139d5de2cd80606da5ba646054b830635'),
    0x8000002A: (6, 26, 'b85cd5dc43d3e941dabfd8877fc7214588c8cac9690562a96f0fc83ef68ab3a9'),
    0x8000021D: (3, 25, 'c513a4e10678b4faad0b2f878d59703139d5de2cd80606da5ba646054b830635'),
    0x80000423: (6, 31, 'a1c302a2168e9a4ccf192ccebc49fdd325b7aa961fbade8b933449398b51b48a'),
    0x80000819: (5, 26, '99d710d81b846bd6f4bbb59d5000839248339fc2cb30fd2218b1ff63b2dcc046'),
    0xF000000D: (3, 25, 'c513a4e10678b4faad0b2f878d59703139d5de2cd80606da5ba646054b830635'),
}


def _decode_version_map_field(selector: dict, formula: dict | None, vector: bytes) -> dict:
    """Pinned GetCalc MinMax read semantics, separate from live value buffers.

    GetCalc clears 512 input bytes, copies the map vector, and calls CALC with
    the vector's original length. MinLength checks remaining raw bytes, whereas
    the returned text stops at ZERO or MaxLength. An initialized terminator is
    an offline source candidate, never proof of fresh vehicle bytes.
    """
    from .x431_formula import formula_from_record
    from .x431_values import decode_record

    record = {'formula': formula, 'byteOffset': selector['byteOffsetSigned'],
              'bitOffset': selector['bitOffsetByte']}
    spec = _VERSION_MINMAX_SOURCE.get(selector['formulaId'])
    if spec is None:
        return decode_record(record, vector)
    text, _ = formula_from_record(record)
    if not isinstance(text, str) or hashlib.sha256(text.encode('utf-8')).hexdigest() != spec[2]:
        return decode_record(record, vector)
    minimum, maximum, _ = spec
    offset = selector['byteOffsetSigned']
    if (not isinstance(vector, bytes) or not 1 <= len(vector) <= 512
            or type(offset) is not int or offset < 0 or offset + maximum > 512
            or selector['bitOffsetByte'] != 0):
        return {'ok': False, 'reason': 'version-minmax-source-buffer-bounds-unqualified', **_GUARDS}
    available = max(0, len(vector) - offset)
    window = vector[offset:offset + maximum]
    zero = window.find(b'\0')
    if available < minimum:
        payload = b''
        termination = 'minimum-raw-extent-empty-result'
    elif zero >= 0:
        payload = window[:zero]
        termination = 'explicit-zero'
    elif available < maximum:
        payload = window
        termination = 'initialized-zero-after-vector'
    else:
        payload = window
        termination = 'max-length'
    if any(byte >= 128 for byte in payload):
        return {'ok': False, 'reason': 'version-minmax-nonascii-source-output-unreviewed', **_GUARDS}
    return {'ok': True, 'reason': None, 'formulaKind': 'IDENTICAL', 'value': payload.decode('ascii'),
            'sourceVersionMinMaxAudit': {
                'getCalcSourceEntry': 0x2B4B0, 'inputScratchZeroBytes': 512,
                'sourceFormulaTextSha256': spec[2], 'minimumRawBytes': minimum,
                'maximumTextBytes': maximum, 'availableRawBytes': available,
                'terminationCandidate': termination,
                'initializedTerminatorUsedCandidate': termination == 'initialized-zero-after-vector',
                'actualRuntimeScratchQualified': False, 'freshReadProven': False}, **_GUARDS}


def calculate_version_vectors(definition: dict, vectors: list[dict], language_path: Path):
    """Calculate reviewed ordinary version formulas from explicit offline vectors.

    The native wrapper's special 7F/8F/FF and legacy formula routes are excluded.
    Bytes are checked before parsing; no language file is silently discovered.
    A complete calculation still cannot prove the shared map's origin/freshness.
    """
    from scripts.x431_re.gag_lib import compact_express

    if definition.get('sourceSha256') != REVIEWED_DSN_SHA256:
        raise ValueError('version-calculation-definition-source-unreviewed')
    language = _load_version_language(language_path)
    audit = audit_version_vectors(definition, vectors)
    result = {**audit, 'kind': 'x431-offline-version-formula-calculation',
              'formulaSourceSha256': REVIEWED_LANGUAGE_SHA256,
              'formulaSourceMatchesReviewedBytes': True, 'calculatedFields': [],
              'calculationComplete': False, 'versionRuleMatch': None}
    if (audit['errors'] or audit['missingRequests'] or audit['mapKeyCollisions']
            or audit['unresolvedSelectors']):
        return result
    buffers = {item['requestHex'].upper(): bytes.fromhex(item['vectorHex']) for item in vectors}
    by_index = {index: group['requestCandidateHex'] for group in audit['requestGroups']
                for index in group['selectorIndices']}
    values = []
    for index, selector in enumerate(definition['selectors']):
        fid = selector['formulaId']
        field = {'selectorIndex': index, 'formulaId': f'{fid:08X}', 'calculated': False}
        result['calculatedFields'].append(field)
        if fid not in _REVIEWED_VERSION_FORMULAS:
            field['reason'] = 'version-formula-route-unreviewed'
            continue
        row = language.lookup_id('EXPRESS.GAG', fid)
        formula = compact_express(row) if row else None
        decoded = _decode_version_map_field(selector, formula, buffers[by_index[index]])
        if 'sourceVersionMinMaxAudit' in decoded:
            field['sourceVersionMinMaxAudit'] = decoded['sourceVersionMinMaxAudit']
        value = decoded.get('text') if decoded.get('formulaKind') == 'TEXTTABLE' else decoded.get('value')
        if fid == 0x8000056E:
            # The pinned CALC returns bytefield version text as "0x42 10".
            # DSN rules compare that presentation literally; ordinary field
            # values retain the decoder's compact hex representation.
            raw_hex = decoded.get('rawHex')
            value = ('0x' + raw_hex[:2] + ' ' + raw_hex[2:]
                     if isinstance(raw_hex, str) and re.fullmatch(r'[0-9A-F]{4}', raw_hex)
                     else None)
        if (not decoded.get('ok') or not isinstance(value, str) or not value.isascii()
                or '\0' in value or len(value) >= 256):
            field['reason'] = decoded.get('reason') or 'version-calculated-text-unqualified'
            continue
        field.update(calculated=True, textBytes=len(value),
                     textSha256=hashlib.sha256(value.encode('ascii')).hexdigest())
        values.append(value)
    complete = len(values) == len(definition['selectors'])
    result.update(calculationComplete=complete, fieldValuesCalculated=complete)
    if complete:
        result['versionRuleMatch'] = match_version_rules(definition, values)
    return result


def audit_version_observations(definition: dict, observations: list[dict], language_path: Path | None = None):
    """Check response origin before exposing bytes to the version formula path.

    The retained wrapper's body is not silently re-labelled as its native map
    value. Missing copied bytes, cached state and unmatched identifiers remain
    missing inputs; a source audit never chooses a vehicle version.
    """
    from .x431_version_response import audit_version_map_origin

    if not isinstance(observations, list) or len(observations) > 256:
        raise ValueError('version-response-observations-invalid')
    empty = audit_version_vectors(definition, [])
    allowed = {group['requestCandidateHex'] for group in empty['requestGroups']}
    reports, vectors, seen = [], [], set()
    for observation in observations:
        report = audit_version_map_origin(observation)
        hx = report['requestHex']
        if hx in seen:
            raise ValueError('version-response-duplicate-request')
        if hx not in allowed:
            raise ValueError('version-response-outside-definition')
        seen.add(hx)
        reports.append(report)
        if report['nativeMapVectorHexCandidate'] is not None:
            vectors.append({'requestHex': hx, 'vectorHex': report['nativeMapVectorHexCandidate']})
    audit = audit_version_vectors(definition, vectors)
    blocked = bool(audit['errors'] or audit['missingRequests'] or audit['mapKeyCollisions'] or audit['unresolvedSelectors'])
    result = (calculate_version_vectors(definition, vectors, language_path)
              if language_path is not None and not blocked else
              {**audit, 'calculationComplete': False, 'versionRuleMatch': None})
    result.update(kind='x431-offline-version-response-origin-audit', nativeOriginAudits=reports,
                  inputSource='explicit-internal-response-observations',
                  originInputsComplete=not blocked, calculationRequested=language_path is not None)
    return result


def audit_application_version_observations(definition: dict, observations: list[dict],
                                         language_path: Path | None = None):
    """Compare explicit offline application responses with DSN selector formulas.

    A positive SID and identifier echo establish an application-layout candidate,
    not the contents/origin of X431's native response map. Keep identifier bytes
    for DSN offsets, using the same ordinary read-response checks as field decode.
    Caller-selected DSN blocks and historical labels do not qualify a fitted ECU.
    """
    from .response_values import normalize_response

    if definition.get('sourceSha256') != REVIEWED_DSN_SHA256:
        raise ValueError('version-application-definition-source-unreviewed')
    if not isinstance(observations, list) or len(observations) > 256:
        raise ValueError('version-application-observations-invalid')
    empty = audit_version_vectors(definition, [])
    allowed = {group['requestCandidateHex'] for group in empty['requestGroups']}
    reports, vectors, seen = [], [], set()
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {'requestHex', 'responseHex', 'sourceKind'}:
            raise ValueError('version-application-observation-fields-invalid')
        if observation['sourceKind'] not in ('historical-capture', 'simulation', 'imported-unqualified'):
            raise ValueError('version-application-offline-source-required')
        request_hex, response_hex = observation['requestHex'], observation['responseHex']
        if (not isinstance(request_hex, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){2,3}', request_hex)
                or not isinstance(response_hex, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){1,513}', response_hex)):
            raise ValueError('version-application-hex-invalid')
        request_hex = request_hex.upper()
        if request_hex in seen:
            raise ValueError('version-application-duplicate-request')
        if request_hex not in allowed:
            raise ValueError('version-application-outside-definition')
        seen.add(request_hex)
        request, response = bytes.fromhex(request_hex), bytes.fromhex(response_hex)
        record = {'readSID': request[0], 'pid': int.from_bytes(request[1:], 'big'),
                  'read_request_candidate_hex': request_hex}
        normalized = normalize_response(response, record, 'pdu', source=observation['sourceKind'])
        report = {'requestHex': request_hex, 'responseSha256': hashlib.sha256(response).hexdigest(),
                  'responseBytes': len(response), 'sourceKind': observation['sourceKind'],
                  'applicationHeaderMatches': normalized['ok'], 'reason': normalized.get('reason'),
                  'nrc': normalized.get('nrc'), 'nativeResponseMapOriginProven': False}
        reports.append(report)
        if normalized['ok']:
            # Remove only the positive SID. Identifier bytes belong to the
            # explicit candidate buffer, unlike ordinary field-data decoding.
            vectors.append({'requestHex': request_hex, 'vectorHex': response[1:].hex().upper()})
    audit = audit_version_vectors(definition, vectors)
    blocked = bool(not definition['selectors'] or audit['errors'] or audit['missingRequests']
                   or audit['mapKeyCollisions'] or audit['unresolvedSelectors']
                   or any(not report['applicationHeaderMatches'] for report in reports))
    result = (calculate_version_vectors(definition, vectors, language_path)
              if language_path is not None and not blocked else
              {**audit, 'calculationComplete': False, 'versionRuleMatch': None})
    result.update(kind='x431-offline-application-version-audit',
                  inputSource='explicit-offline-application-responses', applicationResponseAudits=reports,
                  applicationInputsComplete=not blocked, calculationRequested=language_path is not None,
                  emptySelectorDefinition=not definition['selectors'],
                  nativeResponseMapOriginProven=False, actualDsnRouteSelectionQualified=False,
                  applicationVectorEqualsNativeMapProven=False, freshReadProven=False,
                  selectedVehicleVariant=None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decoded-dsn', type=Path, required=True)
    parser.add_argument('--definition-offset', type=lambda value: int(value, 0), required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--values-json', type=Path,
                        help='JSON array of explicitly supplied, already-calculated offline strings')
    inputs.add_argument('--native-vectors-json', type=Path,
                        help='JSON rows {requestHex, vectorHex}; identifier bytes retained, no positive SID')
    inputs.add_argument('--response-observations-json', type=Path,
                        help='Explicit retained wrapper/producer observations; source bounds checked before formula input')
    inputs.add_argument('--application-responses-json', type=Path,
                        help='Offline rows {requestHex,responseHex,sourceKind}; no native-map origin claim')
    inputs.add_argument('--command-response-observations-json', type=Path,
                        help='Pinned command-source selections plus explicit retained response observations')
    parser.add_argument('--logical-sys-data',type=Path,
                        help='Required reviewed SYS_DATA for command-source observations only')
    parser.add_argument('--dsn-route-context-json', type=Path,
                        help='Optional explicit SYSP route context; no current global byte is inferred')
    parser.add_argument('--language-ggp', type=Path,
                        help='Optional reviewed PORSCHE_CN.GGP for offline native-vector formula calculation')
    parser.add_argument('--decoded-alldata', type=Path,
                        help='Optional reviewed ALLDATA for exact offline candidate definition bindings')
    parser.add_argument('--system-name', help='Exact source route name for ALLDATA binding')
    args = parser.parse_args()
    native_input = (args.native_vectors_json or args.response_observations_json
                    or args.command_response_observations_json or args.application_responses_json)
    if args.command_response_observations_json and not (args.logical_sys_data and args.language_ggp):
        parser.error('--command-response-observations-json requires --logical-sys-data and --language-ggp')
    if args.logical_sys_data and not args.command_response_observations_json:
        parser.error('--logical-sys-data requires --command-response-observations-json')
    if args.dsn_route_context_json and not args.command_response_observations_json:
        parser.error('--dsn-route-context-json requires --command-response-observations-json')
    if args.language_ggp and not native_input:
        parser.error('--language-ggp requires a vector or response input')
    if args.decoded_alldata or args.system_name:
        if not (args.decoded_alldata and args.system_name and args.language_ggp and native_input):
            parser.error('--decoded-alldata and --system-name require --language-ggp and a native input')
    try:
        definition = parse_version_definition(args.decoded_dsn.read_bytes(), args.definition_offset)
        path = args.values_json or native_input
        values = json.loads(path.read_text(encoding='utf-8-sig'))
        if args.command_response_observations_json:
            from .x431_command_origin import audit_source_command_version_observations
            from .x431_definition_binding import audit_source_system_route, bind_source_service_candidates
            if not isinstance(values, list) or len(values) > 256:
                raise ValueError('source-command-version-observations-invalid')
            logical = args.logical_sys_data.read_bytes()
            dsn = args.decoded_dsn.read_bytes()
            context = None
            if args.dsn_route_context_json:
                if not values:
                    raise ValueError('source-route-context-requires-observations')
                context = json.loads(args.dsn_route_context_json.read_text(encoding='utf-8-sig'))
                if context is None:
                    raise ValueError('system-route-context-fields-invalid')
            routes = {}
            for row in values:
                selection = row.get('commandSelection') if isinstance(row, dict) else None
                if not isinstance(selection, dict) or not {'familyId', 'systemKey', 'linkIndex'} <= set(selection):
                    raise ValueError('source-command-selection-fields-invalid')
                source = {key: selection[key] for key in ('familyId', 'systemKey', 'linkIndex')}
                if any(type(value) is not int for value in source.values()):
                    raise ValueError('source-command-selection-integers-required')
                key = tuple(source.values())
                if key not in routes:
                    routes[key] = audit_source_system_route(dsn, logical, args.definition_offset,
                                                            args.system_name, source, context)
                    if context is not None and not any(
                            predicate['sourceRouteEligibleCandidate'] for predicate in routes[key]['sourcePredicateAudits']):
                        raise ValueError('source-command-version-route-context-rejected')
            result=audit_source_command_version_observations(definition,values,logical,args.language_ggp)
            result.update(systemRouteAudits=list(routes.values()), actualDsnRouteSelectionQualified=False,
                          sourceRouteContextQualified=False)
            result.update(sourceServiceCandidateBindings=None, sourceServiceBindingsCompleteCandidate=False)
            if result['calculationComplete']:
                names = [row['variant'] for row in result['versionRuleMatch']['matchingCandidates']]
                bindings = [bind_source_service_candidates(route, names) for route in routes.values()]
                result.update(sourceServiceCandidateBindings=bindings,
                    sourceServiceBindingsCompleteCandidate=bool(bindings) and
                        all(binding['sourceBindingsCompleteCandidate'] for binding in bindings))
        else:
            if args.values_json:
                result = match_version_rules(definition, values)
            elif args.application_responses_json:
                result = audit_application_version_observations(definition, values, args.language_ggp)
            elif args.response_observations_json:
                result = audit_version_observations(definition, values, args.language_ggp)
            elif args.language_ggp:
                result = calculate_version_vectors(definition, values, args.language_ggp)
            else:
                result = audit_version_vectors(definition, values)
        if args.decoded_alldata:
            from .x431_definition_binding import audit_source_binding
            result['definitionBindings'] = None
            if result['calculationComplete']:
                names = [candidate['variant'] for candidate in result['versionRuleMatch']['matchingCandidates']]
                result['definitionBindings'] = audit_source_binding(
                    args.decoded_dsn.read_bytes(), args.definition_offset, args.system_name,
                    args.decoded_alldata.read_bytes(), args.language_ggp, names)
    except (ValueError, OSError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
