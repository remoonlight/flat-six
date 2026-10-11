"""Validate the entire prepared bundle and its provenance without a device or database."""
from __future__ import annotations
import argparse
from pathlib import Path, PurePosixPath, PureWindowsPath
import json
from .realtime_preparation import DEFAULT_OUTPUT, ROOT, load, verify_source, plan_for, FLAGS, POINT_LIMIT


def runtime_path(root, reference):
    """Resolve portable manifest paths without accepting drives or escaping roots."""
    if not isinstance(reference, str) or not reference or '\\' in reference or '\x00' in reference:
        raise ValueError('invalid-runtime-manifest-path')
    relative = PurePosixPath(reference)
    if (relative.is_absolute() or PureWindowsPath(reference).drive
            or any(part in ('', '.', '..') for part in reference.split('/'))):
        raise ValueError('invalid-runtime-manifest-path')
    root = Path(root).resolve()
    result = root.joinpath(*relative.parts).resolve()
    if not result.is_relative_to(root):
        raise ValueError('runtime-manifest-path-outside-root')
    return result


def validate_bundle(root):
    root = Path(root)
    manifest = load(root / 'manifest.json')
    portable = manifest.get('schemaVersion') == 2
    if portable:
        if manifest.get('inputsAreProvenanceOnlyAtRuntime') is not True:
            raise ValueError('runtime-provenance-contract-missing')
        for item in manifest['code']:
            if not item['path'].startswith('scripts/'):
                raise ValueError('runtime-code-outside-scripts')
            verify_source(runtime_path(ROOT, item['path']), item['sha256'])
    else:
        for item in manifest['inputs'] + manifest['code']:
            verify_source(Path(item['path']), item['sha256'])
    outputs = {}
    for item in manifest['outputs']:
        original = Path(item['path'])
        relative = item['path'] if portable else ('profiles/' + original.name if original.parent.name == 'profiles' else original.name)
        if relative in outputs:
            raise ValueError('duplicate-runtime-output')
        outputs[relative] = item['sha256']
        verify_source(runtime_path(root, relative), item['sha256'])
    if portable and 'index.json' not in outputs:
        raise ValueError('runtime-index-not-bound')
    index = load(root / 'index.json')
    counts = {'parameters': 0, 'identity': 0, 'plans': 0, 'requestGroups': 0, 'sourceLabelsMissing': 0}
    for row in index['profiles']:
        profile_path = runtime_path(root, row['file'])
        if portable and outputs.get(row['file']) != row['sha256']:
            raise ValueError('runtime-profile-not-bound')
        verify_source(profile_path, row['sha256'])
        profile = load(profile_path)
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
