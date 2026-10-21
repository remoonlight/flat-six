"""Source-bounded linear coding research. No editor, transport or write recipe.

Targets use exact rational physical values, not rounded display text or arbitrary
bytes. A source domain is still only a candidate for the native editing workflow.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from fractions import Fraction
import hashlib
import importlib
import json
from pathlib import Path
import re
import zipfile

from .offline_match import ANCHORS, FLAGS
from .response_values import data_span, request_from_record
from .x431_formula import formula_from_record
from .x431_values import decode_record, insert_raw
from scripts.x431_re.request_fields import coding_profile_variant

GUARDS = {**FLAGS, 'vehicleVerified': False, 'nativeEditingBehaviorVerified': False,
          'codingWriteQualified': False, 'codingRestoreQualified': False,
          'editorEnabled': False, 'completeOriginalBackup': False}
INTEGER_TYPES = frozenset({'A_UINT8', 'A_UINT16', 'A_UINT32', 'A_INT8', 'A_INT16', 'A_INT32'})
VARIANTS_BYTES = 290147216  # Exact retained source bound to ANCHORS['variants'].


def rational(value):
    number = Fraction(value)
    return {'numerator': str(number.numerator), 'denominator': str(number.denominator)}


def physical_target(text):
    """Exact decimal or fraction; no exponent, display rounding or nearest step."""
    if not isinstance(text, str) or not 1 <= len(text) <= 128:
        raise ValueError('numeric-target-invalid')
    value = text.strip()
    if not re.fullmatch(r'[+-]?[0-9]{1,64}(?:\.[0-9]{1,64}|/[1-9][0-9]{0,63})?', value):
        raise ValueError('numeric-target-invalid')
    return rational(Fraction(value))


def _constraints(record):
    request = request_from_record(record)
    if not request.get('ok') or request['sid'] not in (0x21, 0x22):
        raise ValueError('numeric-read-request-unresolved')
    _, parsed = formula_from_record(record)
    if not parsed.get('ok') or parsed.get('kind') not in ('LINEAR', 'SCALE-LINEAR'):
        raise ValueError('numeric-linear-definition-required')
    if parsed['kind'] == 'SCALE-LINEAR':
        segments = parsed.get('segments')
        # The retained PCM fields each declare one bounded affine segment.
        # Multiple segments may be discontinuous or have ambiguous inverses;
        # they are not reduced to one envelope or inferred from storage width.
        if not isinstance(segments, list) or len(segments) != 1:
            raise ValueError('numeric-single-source-segment-required')
        parsed = {**parsed, **{k: segments[0].get(k) for k in ('lower', 'upper', 'xa', 'xb', 'xc')}}
    if parsed.get('baseDataType') not in INTEGER_TYPES or (parsed.get('encoding') or '').upper() not in ('UNDEFINED', '2C'):
        raise ValueError('numeric-integer-encoding-unqualified')
    try:
        xa, xb, xc = (Fraction(parsed[k]) for k in ('xa', 'xb', 'xc'))
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        raise ValueError('numeric-coefficients-unresolved')
    if not xb or not xc:
        raise ValueError('numeric-inverse-not-unique')
    width = parsed.get('bitLength')
    if type(width) is not int or not 1 <= width <= 32 or data_span(record) is None:
        raise ValueError('numeric-field-layout-unresolved')
    if not decode_record(record, bytes(data_span(record)['dataMin'])).get('displayName'):
        raise ValueError('numeric-field-name-unresolved')
    lo, hi = parsed.get('lower'), parsed.get('upper')
    if type(lo) is not int or type(hi) is not int:
        raise ValueError('numeric-complete-source-bounds-required')
    signed = parsed['baseDataType'].startswith('A_INT') or parsed.get('encoding', '').upper() == '2C' or parsed.get('signedLiterals')
    floor, ceiling = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, (1 << width) - 1)
    if not floor <= lo <= hi <= ceiling:
        raise ValueError('numeric-source-bounds-unrepresentable')
    mask = parsed.get('bitMask') or 0
    if type(mask) is not int or not 0 <= mask < 1 << width:
        raise ValueError('numeric-mask-unrepresentable')
    return request, parsed, lo, hi, xa, xb, xc


def numeric_physical_value(record, coded):
    """Exact source conversion shared by a draft and its current-value display."""
    if type(coded) is not int:
        raise ValueError('numeric-coded-value-invalid')
    _, _, _, _, xa, xb, xc = _constraints(record)
    return (Fraction(coded) * xc - xa) / xb


def numeric_domain(record):
    try:
        request, parsed, lo, hi, xa, xb, xc = _constraints(record)
        endpoints = sorted(((lo * xc - xa) / xb, (hi * xc - xa) / xb))
        return {'ok': True, 'kind': 'source-bounded-linear-candidate',
                'name': decode_record(record, bytes(data_span(record)['dataMin']))['displayName'],
                'unit': record.get('unit'), 'requestHex': request['request'].hex().upper(),
                'sourceOffset': record.get('at'), 'source': record.get('source'),
                'definitionSha256': hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'codedLower': lo, 'codedUpper': hi, 'bitLength': parsed['bitLength'],
                'physicalEnvelope': [rational(v) for v in endpoints],
                'physicalStepMagnitude': rational(abs(xc / xb)),
                'reservedCodedRanges': [list(row[:2]) for row in parsed.get('ic') or []],
                'codedMaskHex': f"{parsed.get('bitMask') or 0:X}",
                'allEnvelopeValuesAllowed': False, 'targetBasis': 'exact-source-physical-value',
                'displayPrecision': parsed.get('precision'), 'displayMayMergeDistinctValues': True,
                'offlinePreviewReady': True, **GUARDS}
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return {'ok': False, 'reason': str(error), 'offlinePreviewReady': False, **GUARDS}


def preview_numeric_candidate(record, original, target):
    """Pure source calculation; neither a native edit nor vehicle original data."""
    try:
        _, parsed, lo, hi, xa, xb, xc = _constraints(record)
        if not isinstance(original, bytes) or not 1 <= len(original) <= 65535:
            raise ValueError('numeric-original-buffer-invalid')
        if (not isinstance(target, dict) or set(target) != {'numerator', 'denominator'}
                or not all(isinstance(target[k], str) and len(target[k]) <= 128 for k in target)
                or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)', target['numerator'])
                or not re.fullmatch(r'[1-9][0-9]*', target['denominator'])):
            raise ValueError('numeric-target-invalid')
        physical = Fraction(int(target['numerator']), int(target['denominator']))
        coded = (physical * xb + xa) / xc
        if coded.denominator != 1:
            raise ValueError('numeric-target-not-representable')
        coded = coded.numerator
        if not lo <= coded <= hi:
            raise ValueError('numeric-target-out-of-source-bounds')
        if any(a <= coded <= b for a, b, _ in parsed.get('ic') or []):
            raise ValueError('numeric-target-reserved')
        raw = coded % (1 << parsed['bitLength'])
        mask = parsed.get('bitMask') or 0
        if mask and raw & ~mask:
            raise ValueError('numeric-target-mask-unrepresentable')
        patched = insert_raw(parsed, original, record['byteOffset'], record['bitOffset'], raw)
        if not patched['ok']:
            raise ValueError(patched['reason'])
        after = patched['after']; decoded = decode_record(record, after)
        if not decoded.get('ok') or decoded.get('raw') != raw or decoded.get('value') != coded:
            raise ValueError('numeric-preview-decode-mismatch')
        return {'ok': True, 'kind': 'offline-numeric-coding-candidate',
                'beforeHex': original.hex().upper(), 'afterHex': after.hex().upper(),
                'changedBitMaskHex': bytes(a ^ b for a, b in zip(original, after, strict=True)).hex().upper(),
                'targetPhysical': rational(physical), 'codedValue': coded, 'rawValue': raw,
                'decodedCandidate': decoded, 'sourceDomain': numeric_domain(record),
                'originalProvenance': 'caller-buffer-unqualified', **GUARDS}
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return {'ok': False, 'reason': str(error), **GUARDS}


def audit_variants(stream, output):
    from .decoder_reference import reference_patch, reference_value, compare_reference
    output = Path(output)
    if output.exists():
        raise ValueError('numeric-audit-fresh-output-required')
    output.mkdir(parents=True)
    counts, cache, profiles = Counter(), {}, []
    with (output / 'fields.jsonl').open('w', encoding='utf8', newline='\n') as rows:
        for line in stream:
            variant = coding_profile_variant(json.loads(line))
            if not variant.get('on_target_menu') or variant.get('generation') not in (None, '9x1', '981', '982'):
                continue
            tally = Counter()
            for record in variant.get('pool_records', {}).get('coding', {}).get('records', []):
                _, parsed = formula_from_record(record)
                if parsed.get('kind') not in ('LINEAR', 'SCALE-LINEAR'):
                    continue
                domain = numeric_domain(record); tally['linearFields'] += 1
                key = json.dumps({k: record.get(k) for k in ('formula', 'byteOffset', 'bitOffset', 'unit', 'enumText')}, sort_keys=True)
                check = {'status': 'unverified', 'cases': 0, 'rejected': Counter(), 'mismatches': []}
                if domain['ok']:
                    if key not in cache:
                        lo, hi = domain['codedLower'], domain['codedUpper']
                        for coded in sorted({lo, hi, (lo + hi) // 2, lo - 1, hi + 1}):
                            physical = numeric_physical_value(record, coded)
                            for pattern in (0, 0xA5, 0xFF):
                                before = bytes([pattern]) * (data_span(record)['dataMin'] + 3)
                                candidate = preview_numeric_candidate(record, before, rational(physical))
                                check['cases'] += 1
                                raw = coded % (1 << parsed['bitLength'])
                                expected = reference_patch(record, before, raw)
                                reference = reference_value(record, expected)
                                expected_accept = (lo <= coded <= hi and reference.get('ok')
                                    and reference.get('raw') == raw and reference.get('value') == coded)
                                if bool(expected_accept) != candidate['ok']:
                                    check['mismatches'].append({'coded': coded, 'pattern': pattern,
                                        'expectedAccepted': bool(expected_accept), 'actualAccepted': candidate['ok']})
                                if not candidate['ok']:
                                    check['rejected'][candidate['reason']] += 1
                                    continue
                                after = bytes.fromhex(candidate['afterHex'])
                                differences = compare_reference(reference, candidate['decodedCandidate'])
                                if after != expected or differences:
                                    check['mismatches'].append({'coded': coded, 'pattern': pattern, 'keys': differences, 'bitsMatch': after == expected})
                        check['status'] = 'mismatch' if check['mismatches'] else 'matched'
                        cache[key] = check
                    check = cache[key]; tally['candidateFields'] += 1
                    tally['reference:' + check['status']] += 1
                else:
                    tally['blocked:' + domain['reason']] += 1
                rows.write(json.dumps({'profileId': variant['profile_id'], 'sourceOffset': record.get('at'),
                    'domain': domain, 'syntheticReference': check, **GUARDS}, ensure_ascii=False) + '\n')
            profiles.append({'profileId': variant['profile_id'], 'counts': dict(tally)})
            counts.update(tally)
    result = {'kind': 'all-source-numeric-coding-research', 'counts': dict(counts), 'profiles': profiles,
              'uniqueCandidatesChecked': len(cache), 'uniqueSyntheticCases': sum(v['cases'] for v in cache.values()),
              'uniqueRejectedCases': dict(sum((v['rejected'] for v in cache.values()), Counter())),
              'targetBoundsAreSourceEnvelopeOnly': True, 'actualNativeEditingNotTested': True, **GUARDS}
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8', newline='\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--variants', type=Path)
    source.add_argument('--definition-bundle', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    code = []
    for name in ('coding_numeric.py', 'decoder_reference.py', 'x431_formula.py', 'x431_values.py',
                 'response_values.py', 'offline_match.py', 'definition_bundle.py'):
        file = Path(importlib.import_module('scripts.diagnostics.' + Path(name).stem).__file__)
        code.append((file, {'name': name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}))
    file = Path(__file__).parents[1] / 'x431_re/request_fields.py'
    code.append((file, {'name': 'scripts/x431_re/request_fields.py', 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}))
    with ExitStack() as stack:
        if args.variants:
            if args.variants.stat().st_size != VARIANTS_BYTES:
                raise ValueError('numeric-variant-source-length-mismatch')
            binary = stack.enter_context(args.variants.open('rb'))
        else:
            from .definition_bundle import VARIANTS
            archive = stack.enter_context(zipfile.ZipFile(args.definition_bundle))
            manifest = json.loads(archive.read('manifest.json'))
            rows = [r for r in manifest['files'] if r['name'] == VARIANTS]
            if len(rows) != 1 or rows[0]['sha256'] != ANCHORS['variants']:
                raise ValueError('numeric-variant-source-unreviewed')
            if rows[0]['bytes'] != VARIANTS_BYTES or archive.getinfo(VARIANTS).file_size != VARIANTS_BYTES:
                raise ValueError('numeric-variant-source-length-mismatch')
            binary = stack.enter_context(archive.open(VARIANTS))
        if hashlib.file_digest(binary, 'sha256').hexdigest() != ANCHORS['variants']:
            raise ValueError('numeric-variant-source-unreviewed')
        binary.seek(0)
        # Hash the actual parsed bytes too; a source change between passes fails.
        actual = hashlib.sha256()
        def lines():
            for line in binary:
                actual.update(line)
                yield line.decode('utf8')
        result = audit_variants(lines(), args.output_dir)
        if actual.hexdigest() != ANCHORS['variants']:
            raise ValueError('numeric-variant-source-changed')
    for file, row in code:
        if hashlib.sha256(file.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('numeric-audit-code-changed')
    manifest = {'sourceSha256': ANCHORS['variants'], 'code': [row for _, row in code], 'outputs': [], **GUARDS}
    for file in sorted(args.output_dir.iterdir()):
        manifest['outputs'].append({'name': file.name, 'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8', newline='\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'profiles'}))
    return 1 if result['counts'].get('reference:mismatch') else 0


if __name__ == '__main__':
    raise SystemExit(main())
