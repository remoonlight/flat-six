"""Audit repeated rear-body coding labels against existing private captures.

Source record order is a hypothesis about screenshot row order, never native
menu/version qualification. No write payload or vehicle transport is produced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .offline_match import ROOT, DEFAULT_VARIANTS, ANCHORS, DSN_PATH, FLAGS, selector_hits, verify_source
from .realtime_preparation import load
from .x431_capture import ADDRESSES, matches_display
from .x431_values import decode_record

DEFAULT_CAPTURE = ROOT / '.local/vehicle-analysis/981-precar-20261001/x431-long-returns'


def reserved_candidates(variants, comparisons, read_pairs):
    observations = [r for r in comparisons if r['module'] == 'BCM_hinten' and '保留（第' in r['field']]
    # Neighboring, already-compared fields bind these to the same coding-read
    # phase. Do not silently borrow pre-coding responses or another ECU's block.
    responses = {e['responseFrameId'] for r in comparisons if r['module'] == 'BCM_hinten'
                 for e in r.get('evaluations', [])}
    rows = [r for r in read_pairs if r.get('responseFrameId') in responses
            and r.get('txId') == ADDRESSES['BCM_hinten'] and r.get('status') == 'positive-candidate']
    candidates = []
    for variant in variants:
        if variant['module'] != 'BCM_hinten' or variant.get('generation') == '982':
            continue
        records = sorted((r for r in variant['pool_records']['coding']['records']
            if [t.get('text') for t in r.get('labels', [])][-2:] == ['系列', '保留']), key=lambda r: r['at'])
        if len(records) != len(observations) or len(records) != 2:
            continue
        slots = [(r.get('read_request_candidate_hex'), r.get('byteOffset'), r.get('bitOffset')) for r in records]
        if len(set(slots)) != 2:
            continue
        for ordinal, rec in enumerate(records, 1):
            obs = next((r for r in observations if f'第{ordinal}个' in r['field']), None)
            if obs is None:
                continue
            evaluations = []
            for pair in rows:
                if pair['requestHex'] != rec.get('read_request_candidate_hex', '').upper():
                    continue
                decoded = decode_record(rec, bytes.fromhex(pair['dataHex']))
                evaluations.append({'requestFrameId': pair['requestFrameId'],
                    'responseFrameId': pair['responseFrameId'], 'decodedText': decoded.get('text'),
                    'matchesVisibleValue': matches_display(decoded, obs['displayedValue'], rec.get('unit'))})
            candidates.append({'observationId': obs['observationId'], 'visibleOrdinal': ordinal,
                'profileId': variant['profile_id'], 'definitionOffset': rec['at'],
                'requestHex': rec.get('read_request_candidate_hex'),
                'byteOffset': rec.get('byteOffset'), 'bitOffset': rec.get('bitOffset'),
                'displayedValue': obs['displayedValue'], 'source': rec.get('source'),
                'evaluations': evaluations, 'layoutStatus': 'source-order-candidate',
                'nativeRowOrderProven': False, 'fullVehicleVersionQualified': False,
                'freshReadProven': False, **FLAGS})
    return candidates


def audit(output):
    output = Path(output)
    if output.exists():
        raise ValueError('output-already-exists')
    inputs = [verify_source(DEFAULT_VARIANTS, ANCHORS['variants']), verify_source(DSN_PATH, ANCHORS['dsn'])]
    manifest_path = DEFAULT_CAPTURE / 'manifest.json'
    inputs.append(verify_source(manifest_path))
    manifest = load(manifest_path)
    files = {}
    for name in ('original-comparisons.json', 'read-pairs.json'):
        expected = next(r['sha256'] for r in manifest if r['file'] == name)
        inputs.append(verify_source(DEFAULT_CAPTURE / name, expected))
        files[name] = load(DEFAULT_CAPTURE / name)
    with DEFAULT_VARIANTS.open(encoding='utf-8') as stream:
        variants = [json.loads(line) for line in stream]
    candidates = reserved_candidates(variants, files['original-comparisons.json'], files['read-pairs.json'])
    selectors = selector_hits(DSN_PATH.read_bytes(), 'EV_MUHig6C3Gen2AW7_001')
    result = {'reservedCandidates': candidates,
        'layoutSlots': sorted({(r['visibleOrdinal'], r['requestHex'], r['byteOffset'], r['bitOffset']) for r in candidates}),
        'pcmSelectors': selectors, 'pcmSuffixSemanticsProven': False,
        'remainingEvidence': ['rear-body full identity/version and selected native coding menu',
            'continuous ordered coding pages with neighboring fields and raw read buffers',
            'fresh read timestamp, not only a backup-viewer screenshot',
            'PCM complete identity, version-selection path and original coding block',
            'native selector predicate semantics; more snapshots alone do not prove #001 comparison'], **FLAGS}
    output.mkdir(parents=True)
    target = output / 'coding-readiness.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {'ok': True, 'candidateRows': len(candidates), 'candidateProfiles': len({r['profileId'] for r in candidates}),
        'layoutSlots': result['layoutSlots'], 'valueAgreementRows': sum(bool(r['evaluations']) and all(e['matchesVisibleValue'] for e in r['evaluations']) for r in candidates),
        'pcmSelectorOccurrences': len(selectors), 'vehicleVerified': False, **FLAGS}
    (output / 'manifest.json').write_text(json.dumps({'inputs': inputs,
        'code': [verify_source(Path(__file__)), verify_source(ROOT / 'scripts/diagnostics/x431_values.py')],
        'outputs': [verify_source(target)], **FLAGS}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.output_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
