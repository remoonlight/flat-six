"""Validate the entire prepared bundle and its provenance without a device or database."""
from __future__ import annotations
import argparse
from pathlib import Path
import json
from .realtime_preparation import DEFAULT_OUTPUT, load, verify_source, plan_for, FLAGS, POINT_LIMIT


def validate_bundle(root):
    root = Path(root)
    index, manifest = load(root / 'index.json'), load(root / 'manifest.json')
    for item in manifest['inputs'] + manifest['code']:
        verify_source(Path(item['path']), item['sha256'])
    for item in manifest['outputs']:
        original = Path(item['path'])
        relative = 'profiles/' + original.name if original.parent.name == 'profiles' else original.name
        verify_source(root / relative, item['sha256'])
    counts = {'parameters': 0, 'identity': 0, 'plans': 0, 'requestGroups': 0, 'sourceLabelsMissing': 0}
    for row in index['profiles']:
        profile = load(root / row['file'])
        verify_source(root / row['file'], row['sha256'])
        keys = [p['id'] for p in profile['parameters']]
        assert len(keys) == len(set(keys)), 'duplicate logical parameter'
        for p in profile['parameters']:
            assert all(p[k] == value for k, value in FLAGS.items()), 'vehicle flag enabled'
            assert sum(s['pointCount'] for s in p['replay']) == p['decodedSampleCount'], 'response coverage changed'
            categories = p.get('categories', [])
            assert categories and len({c['id'] for c in categories}) == len(categories), 'category membership missing/duplicated'
            assert all(c['sourceNamespace'] == 'DSTREAM_CN.GAG' for c in categories), 'category source namespace changed'
            for series in p['replay']:
                points = series['points']
                assert len(points) == series['displayPointCount'] <= POINT_LIMIT
                assert all(points[i]['elapsedMs'] <= points[i + 1]['elapsedMs'] for i in range(len(points) - 1))
                assert all(point['pduSha256'] and point['responseFrameSha256'] for point in points)
        for offset in range(0, len(keys), 12):
            plan = plan_for(profile, keys[offset:offset + 12])
            assert not plan['blocked'], 'current static decoder unresolved'
            counts['plans'] += 1; counts['requestGroups'] += plan['requestCountPerCycle']
        counts['parameters'] += len(keys)
        counts['identity'] += len(profile['identity'])
        counts['sourceLabelsMissing'] += sum(not p['nameResolved'] for p in profile['parameters'])
    assert counts['parameters'] == index['counts']['parameters']
    assert counts['identity'] == index['counts']['identityRecords']
    assert len(index['units']) == index['counts']['units']
    return {'ok': True, 'counts': counts, 'manifestSha256': verify_source(root / 'manifest.json')['sha256'], **FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = validate_bundle(args.bundle)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__': main()
