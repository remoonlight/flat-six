"""Retained PIWIS project catalog declarations and exact source gaps.

This is a supplied-snapshot inventory. File names and update declarations do
not prove installed file presence, ECU identity, model coverage or execution.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from scripts.archive_piwis_workshop import safe_relative

CATALOG_LIMIT = 4 * 1024 * 1024
SOURCE_LIMIT = 16 * 1024 * 1024


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inspect_catalog(raw, *, project, catalog_kind):
    safe_relative(project)
    if '/' in project or catalog_kind not in ('source', 'runtime'):
        raise ValueError('catalog-project-or-kind-invalid')
    if len(raw) > CATALOG_LIMIT:
        raise ValueError('catalog-size-limit')
    if b'\x00' in raw:
        raise ValueError('catalog-text-encoding-unqualified')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('catalog-external-or-entity-declaration-blocked')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError('catalog-xml-invalid') from exc
    if root.tag != 'CATALOG' or root.findtext('SHORT-NAME') != project:
        raise ValueError('catalog-project-identity-mismatch')
    blocks = root.findall('./ABLOCKS/ABLOCK')
    if not blocks or len(blocks) > 256:
        raise ValueError('catalog-block-count-unqualified')
    declarations = []
    for block_index, block in enumerate(blocks):
        name, category, update = block.findtext('SHORT-NAME'), block.findtext('CATEGORY'), block.get('UPD')
        if not name or not category or update not in (None, 'NEW', 'DELETED'):
            raise ValueError('catalog-block-layout-unqualified')
        for index, file in enumerate(block.findall('./FILES/FILE')):
            if list(file) or file.text is None or file.text != file.text.strip():
                raise ValueError('catalog-file-layout-unqualified')
            path = safe_relative(file.text).as_posix()
            if path != file.text or not Path(path).suffix or len(declarations) >= 16384:
                raise ValueError('catalog-file-path-or-count-unqualified')
            declarations.append({'catalogKind': catalog_kind, 'blockIndex': block_index,
                'fileIndex': index, 'blockName': name, 'category': category,
                'declaredUpdate': update, 'relativePath': path,
                'runtimeActivationVerified': False, 'vehicleApplicabilityVerified': False})
    if len(root.findall('.//FILE')) != len(declarations):
        raise ValueError('catalog-file-container-unqualified')
    return {'project': project, 'catalogKind': catalog_kind, 'blockCount': len(blocks),
            'declarationCount': len(declarations), 'declarations': declarations}


def _snapshot_sources(manifests, requested):
    sources = {}
    for name in manifests:
        path = Path(name)
        if path.stat().st_size > CATALOG_LIMIT:
            raise ValueError('catalog-snapshot-size-limit')
        snapshot = json.loads(path.read_text(encoding='utf8'))
        for item in snapshot['files']:
            relative = safe_relative(item['relativePath']).as_posix()
            if relative not in requested or item['present'] is not True:
                continue
            size, expected = item.get('bytes'), item.get('sha256')
            if (type(size) is not int or not 0 < size <= SOURCE_LIMIT
                    or not isinstance(expected, str) or len(expected) != 64
                    or any(c not in '0123456789abcdef' for c in expected)):
                raise ValueError('catalog-retained-source-identity-invalid')
            base = (path.parent / 'sources').resolve()
            target = (base / relative).resolve()
            if not target.is_relative_to(base):
                raise ValueError('catalog-retained-source-path-outside-root')
            if target.stat().st_size != size:
                raise ValueError('catalog-retained-source-hash-mismatch')
            raw = target.read_bytes()
            if len(raw) != size or _digest(raw) != expected:
                raise ValueError('catalog-retained-source-hash-mismatch')
            source = {'file': str(target), 'relativePath': relative, 'bytes': size, 'sha256': expected}
            if relative in sources:
                previous = sources[relative]['source']
                if (previous['bytes'], previous['sha256']) != (size, expected):
                    raise ValueError('catalog-conflicting-retained-sources')
                sources[relative]['manifests'].append(str(path.resolve()))
            else:
                sources[relative] = {'source': source, 'raw': raw, 'manifests': [str(path.resolve())]}
    return sources


def audit_catalog(manifests, project, output):
    safe_relative(project)
    if '/' in project:
        raise ValueError('catalog-project-or-kind-invalid')
    manifests = list(manifests)
    prefix = 'Projects-V/' + project + '/'
    paths = {prefix + 'index.xml': 'source', prefix + 'rt_index.xml': 'runtime'}
    catalogs = _snapshot_sources(manifests, set(paths))
    if set(catalogs) != set(paths):
        raise ValueError('catalog-source-and-runtime-originals-required')
    declarations, catalog_evidence = [], []
    for relative, kind in paths.items():
        retained = catalogs[relative]
        parsed = inspect_catalog(retained['raw'], project=project, catalog_kind=kind)
        catalog_evidence.append({'source': retained['source'], 'manifests': retained['manifests'],
            'catalogKind': kind, 'blockCount': parsed['blockCount'], 'declarationCount': parsed['declarationCount']})
        for row in parsed['declarations']:
            declarations.append(dict(row, catalogSource=retained['source']))
    wanted = {prefix + row['relativePath'] for row in declarations}
    retained_files = _snapshot_sources(manifests, wanted)
    grouped = defaultdict(list)
    for row in declarations:
        grouped[row['relativePath']].append(row)
    counts, files = Counter(), []
    for relative, rows in sorted(grouped.items()):
        retained = retained_files.get(prefix + relative)
        deleted = any(row['declaredUpdate'] == 'DELETED' for row in rows)
        listed = any(row['declaredUpdate'] != 'DELETED' for row in rows)
        conflict = deleted and listed
        counts['uniqueDeclaredFiles'] += 1
        counts['retainedFiles' if retained else 'notRetainedFiles'] += 1
        counts['ambiguousUpdateFiles'] += conflict
        files.append({'relativePath': relative, 'declarations': rows,
            'retainedSource': retained['source'] if retained else None,
            'retainedHashVerified': retained is not None, 'notRetained': retained is None,
            'listedAndDeletedConflict': conflict, 'declaredDeleted': deleted,
            'installedPresenceVerified': False, 'runtimeActivationVerified': False,
            'vehicleApplicabilityVerified': False, 'executionEnabled': False})
    runtime = {row['relativePath'] for row in declarations if row['catalogKind'] == 'runtime'}
    pairs = []
    for database in sorted(p for p in runtime if p.endswith('.db')):
        key = database[:-3] + '.key'
        db_retained, key_retained = retained_files.get(prefix + database), retained_files.get(prefix + key)
        declared_key = key in runtime
        conflict = any(row['declaredUpdate'] == 'DELETED' for name in (database, key) for row in grouped.get(name, []))
        counts['runtimeDeclaredDatabases'] += 1
        counts['runtimeDeclaredKeyPairs' if declared_key else 'runtimeMissingKeyDeclarations'] += 1
        counts['retainedCompleteDbKeyPairs' if db_retained and key_retained else 'notRetainedCompleteDbKeyPairs'] += 1
        pairs.append({'databasePath': database, 'keyPath': key, 'keyDeclared': declared_key,
            'databaseSource': db_retained['source'] if db_retained else None,
            'keySource': key_retained['source'] if key_retained else None,
            'deletionDeclarationPresent': conflict, 'runtimeActivationVerified': False,
            'ecuIdentityVerified': False, 'vehicleApplicabilityVerified': False, 'executionEnabled': False})
    counts.update({kind + 'Declarations': sum(row['catalogKind'] == kind for row in declarations)
                   for kind in ('source', 'runtime')})
    result = {'schemaVersion': 1, 'kind': 'piwis-retained-project-catalog', 'project': project,
        'scope': 'supplied-catalog-declarations-only', 'catalogSources': catalog_evidence,
        'counts': dict(counts), 'databaseKeyPairs': pairs, 'installedPresenceVerified': False,
        'runtimeActivationVerified': False, 'complete981982InventoryVerified': False,
        'ecuIdentityVerified': False, 'vehicleApplicabilityVerified': False, 'executionEnabled': False,
        'noDeviceIO': True, 'targetExecuted': False,
        'limitations': ['catalog project name is not a vehicle applicability decision',
            'listed and deleted declarations are preserved without guessing update order or runtime activation',
            'not retained means missing from supplied snapshots, not absent from the remote installation']}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'catalog-file-declarations.jsonl').open('x', encoding='utf8') as stream:
        for row in files:
            stream.write(json.dumps(row, ensure_ascii=True) + '\n')
    (output / 'catalog-index.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, action='append', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = audit_catalog(args.snapshot, args.project, args.output_dir)
    print(json.dumps({'project': result['project'], 'counts': result['counts'], 'targetExecuted': False}))


if __name__ == '__main__':
    main()
