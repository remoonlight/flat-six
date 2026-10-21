"""Source-bound CTS dependency/configuration index from retained SSH snapshots.

Configured source precedence is a candidate selection, not runtime verification.
Embedded-call declarations are dependencies, not an executable repair procedure.
"""
import argparse
from collections import defaultdict, deque
import json
from pathlib import Path

from scripts.archive_piwis_workshop import safe_relative
from .piwis_compilation import inspect_compilation, ENTITY_KINDS
from .piwis_research import digest

WORKFLOW_OIDS = {
    'vehicle-roller': 'CTc_X33Q8RTYHHZMMRWEB', 'psm-roller': 'CTc_X9Q40DFJ41TL5C81D8BB',
    'pdk-calibration': 'CTc_X39LJ2GB41QRC0E52B1F', 'pdk-oil-fill': 'CTc_XAE7WVL6GI6266BF7A0'}
MODULE_OWNER = 'de/dsa/rcp/plugins/scripting/core/util/ExternalModuleID'


def properties(raw):
    result = {}
    for line in raw.decode('utf8').splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '!')):
            continue
        if '=' not in line or line.endswith('\\'):
            raise ValueError('unsupported-data-service-properties')
        key, value = (x.strip() for x in line.split('=', 1))
        if key in result:
            raise ValueError('duplicate-data-service-property')
        result[key] = value
    return result


def canonical(path):
    path = safe_relative(path).as_posix()
    return path[5:] if path.startswith('Pidt/') else path


def read_snapshots(paths):
    sources = {}
    for manifest_path in paths:
        manifest_path = Path(manifest_path)
        if manifest_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('flow-manifest-size-limit')
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        for item in manifest['files']:
            if not item['present']:
                continue
            relative = safe_relative(item['relativePath']).as_posix()
            name = canonical(relative)
            if not (name.endswith('.obj') or name in ('workspace/dataservice.properties',
                    'workspace/vehicle-platform/9x1/dataservice.properties')):
                continue
            if item['bytes'] > 4 * 1024 * 1024 or len(sources) >= 512:
                raise ValueError('flow-source-size-or-count-limit')
            path = manifest_path.parent / 'sources' / relative
            if path.stat().st_size != item['bytes']:
                raise ValueError('flow-source-size-or-hash-mismatch')
            raw = path.read_bytes()
            if len(raw) != item['bytes'] or digest(raw) != item['sha256']:
                raise ValueError('flow-source-size-or-hash-mismatch')
            source = {'file': name, 'bytes': len(raw), 'sha256': digest(raw)}
            if name in sources and sources[name]['source'] != source:
                raise ValueError('conflicting-flow-source-snapshots')
            sources[name] = {'source': source, 'raw': raw, 'localPath': path}
    return sources


def description(data):
    config, edges, names, services = [], [], [], []
    for entry in data['classes']:
        cls = entry['class']
        methods = {(m['name'], m.get('descriptor')): m for m in cls['methods']}
        for method in cls['methods']:
            code = method.get('instructions', [])
            for n, ins in enumerate(code):
                location = {'class': cls['name'], 'method': method['name'], 'pc': ins['pc'],
                    'classOffset': ins['classOffset'], 'objectOffset': entry['classOffsetInObject'] + ins['classOffset']}
                if isinstance(ins.get('string'), str) and ins['string'].endswith('.xml'):
                    config.append({**location, 'literal': ins['string']})
                if method['name'].startswith('apidescription') and isinstance(ins.get('string'), str):
                    if not ins['string'].startswith(tuple(p + '_' for p in ENTITY_KINDS.values()) + ('UKN_',)):
                        names.append(ins['string'])
                call = ins.get('call')
                if call and '/mcd/' in call['owner']:
                    services.append({**location, 'call': call, 'dynamicDispatchVerified': False})
                if not (method['name'].startswith('embeddedCallDescriptions') and call
                        and call['owner'] == MODULE_OWNER and call['name'].startswith('new')
                        and call['name'].endswith('OID')):
                    continue
                preceding = code[n - 1] if n else {}
                target = preceding.get('string')
                if target is None or call['descriptor'] != '(Ljava/lang/String;)L' + MODULE_OWNER + ';':
                    edges.append({**location, 'kind': call['name'], 'targetOID': None,
                                  'bindingVerified': False, 'missing': 'nonliteral-module-identity'})
                    continue
                parameter_method = parameter_descriptor = None
                for following in code[n + 1:n + 4]:
                    candidate = following.get('call', {})
                    if candidate.get('owner') == cls['name'] and 'MActualParameterDescription' in candidate.get('descriptor', ''):
                        parameter_method = candidate['name']
                        parameter_descriptor = candidate['descriptor']
                        break
                node = next((i['string'] for i in reversed(code[max(0, n - 4):n - 1])
                             if isinstance(i.get('string'), str) and i['string'].startswith('UKN_')), None)
                literals = [{'pc': i['pc'], 'classOffset': i['classOffset'], 'literal': i['string']}
                    for i in methods.get((parameter_method, parameter_descriptor), {}).get('instructions', []) if 'string' in i]
                edges.append({**location, 'nodeOID': node, 'kind': call['name'], 'targetOID': target,
                    'actualParameterMethod': parameter_method, 'actualParameterDescriptor': parameter_descriptor,
                    'parameterDescriptionLiterals': literals,
                    'bindingVerified': True, 'executionOrderVerified': False})
    return {'entityOID': data['entityOID'], 'mainClass': data['fullyQualifiedClassName'],
        'apiNames': sorted(set(names)), 'summary': data['summary'], 'configLiterals': config,
        'serviceSymbolCalls': services, 'dependencies': edges}


def configured_sources(sources):
    global_name, platform_name = 'workspace/dataservice.properties', 'workspace/vehicle-platform/9x1/dataservice.properties'
    if global_name not in sources or platform_name not in sources:
        raise ValueError('missing-global-or-platform-data-service-properties')
    global_props = properties(sources[global_name]['raw'])
    platform_props = properties(sources[platform_name]['raw'])
    roots = {}
    for prefix, props in (('g', global_props), ('', platform_props)):
        for key, value in props.items():
            if key.startswith('deliverable_'):
                token = prefix + key.removeprefix('deliverable_')
                relative = safe_relative(value.removeprefix('./')).as_posix()
                roots[token] = 'workspace/' + relative + '/code/'
    sequence = platform_props.get('CodeServiceSequence', '').split(',')
    if not sequence or any(not token or token not in roots for token in sequence) or len(set(sequence)) != len(sequence):
        raise ValueError('unknown-or-duplicate-code-service-sequence')
    return sequence, roots, {'global': sources[global_name]['source'], 'platform': sources[platform_name]['source']}


def build_index(sources, workflow_oids=WORKFLOW_OIDS):
    sequence, roots, configuration_sources = configured_sources(sources)
    objects, by_oid = [], defaultdict(list)
    for name, item in sorted(sources.items()):
        if not name.endswith('.obj'):
            continue
        scope = [token for token, prefix in roots.items() if name.startswith(prefix)]
        if len(scope) != 1:
            raise ValueError('unknown-or-ambiguous-compilation-deliverable')
        folder = name[len(roots[scope[0]]):].split('/')[0]
        if folder not in ENTITY_KINDS:
            raise ValueError('unsupported-compilation-deliverable-kind')
        data = inspect_compilation(item['raw'], folder)
        obj = {'source': item['source'], 'deliverable': scope[0], 'entityKind': data['entityKind'], **description(data)}
        objects.append(obj)
        if obj['entityOID'] is not None:
            by_oid[obj['entityOID']].append(obj)

    def resolve(oid):
        for token in sequence:
            found = [row for row in by_oid.get(oid, []) if row['deliverable'] == token]
            if found:
                if len(found) > 1:
                    return None, 'variant-selection-unresolved'
                return found[0], None
        # Filenames can identify a missing metadata binding, never resolve it.
        if any(o['entityOID'] is None and Path(o['source']['file']).name.split('@')[0] == oid.lower() for o in objects):
            return None, 'serialized-entity-identity-absent'
        return None, 'object-not-retained-in-configured-sources'

    workflows = []
    for function, root_oid in workflow_oids.items():
        queue, visited, selected, missing = deque([root_oid]), set(), [], []
        while queue:
            oid = queue.popleft()
            if oid in visited:
                continue
            visited.add(oid)
            obj, failure = resolve(oid)
            if failure:
                missing.append({'entityOID': oid, 'reason': failure})
                continue
            selected.append({'entityOID': oid, 'source': obj['source'], 'deliverable': obj['deliverable'],
                'mainClass': obj['mainClass'], 'apiNames': obj['apiNames'], 'runtimeVerified': False,
                'otherRetainedCandidates': [{'source': row['source'], 'deliverable': row['deliverable']}
                    for row in by_oid[oid] if row is not obj]})
            for edge in obj['dependencies']:
                if edge['targetOID'] and edge['kind'] in ('newCTSOID', 'newScriptOID', 'newBuildingBlockOID', 'newScreenOID'):
                    queue.append(edge['targetOID'])
                else:
                    missing.append({'entityOID': edge['targetOID'], 'kind': edge['kind'],
                        'reason': 'unsupported-or-nonliteral-module-not-inspected', 'sourceObjectOID': oid})
        workflows.append({'functionId': function, 'rootOID': root_oid, 'configuredCandidates': selected,
                          'missingDependencies': missing, 'executable': False, 'completeProcedureVerified': False})
    return {'schemaVersion': 1, 'kind': 'piwis-cts-dependency-static-research', 'scope': 'provided-sources-only',
        'executionEnabled': False, 'targetExecuted': False, 'noDeviceIO': True, 'liveVerified': False,
        'configurationSources': configuration_sources, 'configuredCodeServiceSequence': sequence,
        'objects': objects, 'workflows': workflows,
        'summary': {'objects': len(objects), 'dependencyDeclarations': sum(len(o['dependencies']) for o in objects)},
        'limitations': ['configured source precedence and literal declarations only; runtime selection unverified',
            'unretained modules, variant metadata, branching, parameter values, exit/recovery and ECU requests remain unqualified']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    sources = read_snapshots(args.snapshot)
    result = build_index(sources)
    for item in sources.values():
        if digest(item['localPath'].read_bytes()) != item['source']['sha256']:
            raise ValueError('flow-source-changed-during-inspection')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'flow-index.json').write_text(json.dumps(result, ensure_ascii=True, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'summary': result['summary'], 'workflows': [
        {'functionId': w['functionId'], 'retainedCandidates': len(w['configuredCandidates']),
         'missingDependencies': len(w['missingDependencies'])} for w in result['workflows']], 'targetExecuted': False}))


if __name__ == '__main__':
    main()
