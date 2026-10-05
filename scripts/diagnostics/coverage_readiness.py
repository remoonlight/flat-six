"""Produce a bounded per-version 981/982 definition/readiness register, no device I/O."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .coding_read_plan import coding_read_plan
from .offline_match import ROOT, DEFAULT_VARIANTS, verify_source, ANCHORS
from .workbench import load_or_build_index, _matches_generation
from .offline import DEFAULT_COVERAGE
from .response_values import request_from_record


def build(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / '.local') or output == ROOT / '.local' or output.exists():
        raise ValueError('new-private-output-required')
    source = verify_source(DEFAULT_VARIANTS, ANCHORS['variants'])
    index = load_or_build_index(DEFAULT_VARIANTS, json.loads(DEFAULT_COVERAGE.read_text(encoding='utf8')))
    metadata = {row['profileId']: row for row in index['rows']}
    rows = []
    with DEFAULT_VARIANTS.open(encoding='utf8') as stream:
        for line in stream:
            variant = json.loads(line)
            meta = metadata.get(variant['profile_id'])
            if meta is None: continue
            pools = variant.get('pool_records', {})
            coding = coding_read_plan(variant)
            measurements = pools.get('measurement', {}).get('records', [])
            requests = {r['request'].hex().upper() for record in measurements if (r := request_from_record(record)).get('ok')}
            for generation in ('981', '982'):
                if not _matches_generation(meta, generation): continue
                rows.append({'generation': generation, 'profileId': variant['profile_id'], 'module': variant['module'],
                    'variant': variant['name'], 'ecuIds': meta.get('ecuIds', []),
                    'identityDefinitions': len(pools.get('identity', {}).get('records', [])),
                    'measurementDefinitions': len(measurements), 'measurementReadRequests': len(requests),
                    'codingFields': coding['definitionFieldCount'], 'codingReadRequests': len(coding['requestGroups']),
                    'codingDefinitionGaps': len(coding['missing']), 'codingFullLengthQualified': False,
                    'dtcDefinitions': len(pools.get('dtc', {}).get('records', [])),
                    'routineDefinitions': len(pools.get('routine', {}).get('records', [])),
                    'manufacturerLiveQualified': False, 'codingWriteQualified': False, 'firmwareQualified': False,
                    'qualificationGaps': ['fresh-vehicle-identity/version', 'exact-session/address', 'adapter-independent-read',
                        'full-coding-block-scope', 'feature-write/readback/recovery', 'OEM-firmware-authenticity/recipe/recovery'],
                    'sourceOffset': variant.get('off')})
    verify_source(DEFAULT_VARIANTS, source['sha256'])
    output.mkdir(parents=True)
    result = {'schemaVersion': 1, 'kind': 'diagnostic-definition-readiness', 'noDeviceIO': True,
        'vehicleVerified': False, 'source': source,
        'uniqueVariantCounts': {generation: sum(r['generation'] == generation for r in rows) for generation in ('981', '982')},
        'ecuVariantBindingCounts': {generation: sum(len(r['ecuIds']) for r in rows if r['generation'] == generation)
                                  for generation in ('981', '982')},
        'countBasis': 'unique source versions and separate ECU/version bindings; neither is installed ECUs or working functions', 'rows': rows}
    (output / 'coverage-readiness.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return {k: v for k, v in result.items() if k not in ('rows', 'source')}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output_dir), ensure_ascii=False))

if __name__ == '__main__': main()
