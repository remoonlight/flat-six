"""Inspect a retained PIWIS archive offline; export provenance, never firmware or requests.

The archive is a bounded source snapshot, not the complete PIWIS installation.
CTS names and ODX-F descriptors never qualify a vehicle operation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

from scripts.archive_piwis_workshop import safe_relative

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = ROOT / '.local/piwis-archive/981-982/2026-10-01'
XML_LIMIT = 64 * 1024 * 1024
CONFIG_NS = 'ctsconfig.xml.pidt.dsa.de'
WORKFLOWS = (
    ('psm-roller', 'PSM', 'Rollenmodus', 'cts-ecu.xml', 'psm_rollenmodus_config.xml'),
    ('vehicle-roller', None, 'Rollenmodus', 'cts-platform.xml', 'PAG_Gesamtfahrzeug_Rollenmodus.xml'),
    ('pdk-oil-fill', 'GETRIEBE', 'Oelbefuellung', 'cts-ecu.xml', 'PAG_9x1_PDK_Oelbefuellung.xml'),
    ('pdk-calibration', 'GETRIEBE', 'Kalibrieren (Gesamtablauf)', 'cts-ecu.xml', 'PAG_9x1_PDKit_Calibration_Prepare.xml'),
)
DIAGNOSTIC_TAGS = ('DIAG-SERVICE', 'REQUEST', 'POS-RESPONSE', 'NEG-RESPONSE',
                   'DATA-OBJECT-PROP', 'COMPARAM-SPEC', 'ECU-VARIANT')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def local_tag(tag):
    return tag.split('}')[-1] if isinstance(tag, str) else '#comment'


def xml(raw):
    # Reject entities even in UTF-16 inputs; preserve comments for explicit source units.
    if len(raw) > XML_LIMIT or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw.replace(b'\x00', b''), re.I):
        raise ValueError('unsupported-or-oversized-xml')
    return ET.fromstring(raw, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))


def located(root):
    def visit(node, path):
        yield node, path
        counts = Counter()
        for child in node:
            tag = local_tag(child.tag)
            if tag == '#comment':
                continue
            counts[tag] += 1
            yield from visit(child, f'{path}/{tag}[{counts[tag]}]')
    return list(visit(root, '/' + local_tag(root.tag) + '[1]'))


def ref(source, locator):
    return {'file': source['file'], 'sha256': source['sha256'], 'locator': locator}


def parameters(root, source):
    if root.tag != f'{{{CONFIG_NS}}}cts':
        raise ValueError('unsupported-config-namespace')
    result = []
    names = set()
    for node, locator in located(root):
        if local_tag(node.tag) != 'param':
            continue
        values = []
        comments = []
        fields = {}
        for child in node:
            tag = local_tag(child.tag)
            if tag == '#comment':
                comments.append((child.text or '').strip())
            elif child.tag != f'{{{CONFIG_NS}}}{tag}' or child.attrib or list(child):
                raise ValueError('unsupported-config-field')
            elif tag == 'value':
                values.append(child.text or '')
            elif tag in ('short-name', 'type') and tag not in fields:
                fields[tag] = child.text or ''
            else:
                raise ValueError('unsupported-config-field')
        name = fields.get('short-name', '')
        if not name or name in names or not fields.get('type'):
            raise ValueError('duplicate-or-incomplete-config-parameter')
        names.add(name)
        result.append({'name': name, 'type': fields['type'], 'values': values,
                       'comments': comments, 'source': ref(source, locator)})
    return result


def menus(root, source, logical_link, label):
    result = []
    for series, series_path in located(root):
        if series.tag != 'series' or series.get('name') != '9x1':
            continue
        contexts = [(series, series_path)] if logical_link is None else [
            (node, series_path + locator.removeprefix('/series[1]'))
            for node, locator in located(series)
            if node.tag == 'ECU' and node.get('logicallinkname') == logical_link]
        for context, context_path in contexts:
            ordinal = 0
            for node in context:
                if node.tag != 'CTS':
                    continue
                ordinal += 1
                if node.get('default_label') != label:
                    continue
                result.append({'attributes': dict(node.attrib), 'context': dict(context.attrib),
                    'source': ref(source, f'{context_path}/CTS[{ordinal}]')})
    return result


def resolved_path(archive, relative):
    base = archive / 'files'
    path = base / safe_relative(relative)
    if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('archive-path-outside-files')
    return path


def inspect_archive(archive):
    """Verify every declared source, inspect XML only, and retain member identities.

    BIN contents are checked by the archive's CRC but are never exported or executed.
    No directory outside this archive is searched.
    """
    archive = Path(archive)
    manifest_raw = (archive / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest.get('schemaVersion') != 1 or manifest.get('executionEnabled') is not False:
        raise ValueError('unsupported-archive-manifest')
    rows = manifest.get('files', [])
    if not rows or len(rows) > 5000:
        raise ValueError('archive-file-count-invalid')
    sources, roots, counts, members = [], {}, Counter(), Counter()
    seen = set()
    for row in rows:
        rel = row['relativePath']
        if rel in seen:
            raise ValueError('duplicate-archive-source')
        seen.add(rel)
        path = resolved_path(archive, rel)
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != row['bytes'] or sha != row['sha256']:
            raise ValueError('archive-source-integrity: ' + rel)
        source = {'file': rel, 'bytes': row['bytes'], 'sha256': sha}
        source['diagnosticTags'] = {tag: 0 for tag in DIAGNOSTIC_TAGS}
        suffix = path.suffix.lower()
        counts[suffix] += 1
        source['kind'] = 'other'
        documents = []
        if suffix in ('.xml', '.odx'):
            root = xml(path.read_bytes())
            roots[rel] = root
            documents.append((rel, root))
            source['kind'] = 'flash-description' if local_tag(root.tag) == 'ODX' else 'plaintext-config'
        elif suffix == '.pdx':
            source['kind'] = 'flash-container'
            with zipfile.ZipFile(path) as package:
                entries = package.infolist()
                names = [entry.filename for entry in entries]
                if len(names) != len(set(names)) or len(names) > 10000:
                    raise ValueError('duplicate-or-excessive-pdx-members')
                for name in names:
                    safe_relative(name)
                if package.testzip():
                    raise ValueError('pdx-crc-failure')
                observed = [{'name': e.filename, 'bytes': e.file_size, 'crc32': f'{e.CRC:08x}'} for e in entries]
                if observed != row['metadata'].get('members'):
                    raise ValueError('pdx-member-metadata-mismatch')
                source['members'] = observed
                for entry in entries:
                    ext = PurePosixPath(entry.filename).suffix.lower()
                    members[ext] += 1
                    if ext in ('.xml', '.odx', '.odx-f', '.odx-d', '.odx-c', '.odx-v'):
                        if entry.file_size > XML_LIMIT:
                            raise ValueError('oversized-pdx-xml')
                        documents.append((entry.filename, xml(package.read(entry))))
        elif suffix == '.asl':
            source['kind'] = 'access-reference-text'
            source['referenceLines'] = path.read_text(encoding='utf8').splitlines()
        for member, root in documents:
            tag_counts = Counter(local_tag(node.tag) for node in root.iter())
            for tag in DIAGNOSTIC_TAGS:
                source['diagnosticTags'][tag] += tag_counts[tag]
            source.setdefault('documents', []).append({'name': member, 'root': local_tag(root.tag),
                'modelVersion': root.get('MODEL-VERSION'), 'flashDescriptions': tag_counts['FLASH']})
        # Detect a changing source between fingerprinting and XML/container inspection.
        with path.open('rb') as stream:
            final_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != row['bytes'] or final_sha != sha:
            raise ValueError('archive-source-changed-during-inspection: ' + rel)
        sources.append(source)
    # Detect omissions without recursively searching any other research directory.
    actual = {p.relative_to(archive / 'files').as_posix() for p in (archive / 'files').rglob('*') if p.is_file()}
    if actual != seen:
        raise ValueError('archive-file-set-mismatch')
    source_map = {s['file']: s for s in sources}
    workflows = []
    for function_id, link, label, menu_file, config_file in WORKFLOWS:
        config = 'cts-config/' + config_file
        menu_source, config_source = source_map.get(menu_file), source_map.get(config)
        menu_rows = menus(roots[menu_file], menu_source, link, label) if menu_source else []
        params = parameters(roots[config], config_source) if config_source else []
        explicit = any(m['attributes'].get('filename') == config_file for m in menu_rows)
        dependencies = []
        for menu in menu_rows:
            for key in ('filename', 'errFileName'):
                value = menu['attributes'].get(key)
                if value:
                    relative = value.replace('\\', '/')
                    if '/' not in relative:
                        relative = 'cts-config/' + relative
                    safe_relative(relative)
                    dependencies.append({'name': relative, 'kind': key, 'present': relative in source_map,
                                         'source': menu['source']})
        for param in params:
            if param['name'] in ('RuleFileName', 'ruleFileName', 'actionFileName'):
                for value in param['values']:
                    relative = 'flash-data/regeln/' + str(safe_relative(value))
                    dependencies.append({'name': relative, 'kind': param['name'], 'present': relative in source_map,
                                         'source': param['source']})
        gaps = ['exact-ecu-version/applicability', 'cts-executable-body-and-resource-loader',
                'exact-request/response-and-service-binding', 'session/access/timing',
                'exit/recovery-and-independent-validation']
        if not explicit:
            gaps.append('config-to-cts-binding-unverified')
        if not menu_rows:
            gaps.append('menu-reference-not-retained')
        if not params:
            gaps.append('configuration-not-retained')
        if any(not d['present'] for d in dependencies):
            gaps.append('referenced-file-not-retained')
        workflows.append({'functionId': function_id, 'logicalLink': link,
            'scope': 'shared-9x1-candidate', 'configBinding': 'explicit-menu-reference' if explicit else 'catalog-reference-unverified',
            'menus': menu_rows, 'parameters': params, 'dependencies': dependencies, 'gaps': gaps,
            'executionEnabled': False, 'liveVerified': False})
    diagnostic_counts = {tag: sum(s['diagnosticTags'][tag] for s in sources) for tag in DIAGNOSTIC_TAGS}
    summary = {'files': len(sources), 'fileTypes': dict(sorted(counts.items())),
        'containerMemberTypes': dict(sorted(members.items())), 'diagnosticTags': diagnostic_counts,
        'workflowConfigurations': sum(bool(w['parameters']) for w in workflows),
        'workflowParameters': sum(len(w['parameters']) for w in workflows),
        'nativeProgramFiles': sum(counts[ext] for ext in ('.exe', '.dll', '.jar', '.class'))}
    index = {'schemaVersion': 1, 'kind': 'piwis-offline-research', 'scope': 'retained-archive-only',
        'executionEnabled': False, 'liveVerified': False, 'noDeviceIO': True,
        'manifestSha256': digest(manifest_raw), 'versions': manifest.get('versions', {}),
        'summary': summary, 'sources': sources, 'workflows': workflows}
    index['nextProbes'] = [
        {'priority': 1, 'target': 'PIWIS diagnostic/coding data and resource loader',
         'evidenceNeeded': 'exact 981/982 ECU-version definitions, service bytes, decoding and full coding scope',
         'availableInRetainedArchive': diagnostic_counts['DIAG-SERVICE'] > 0},
        {'priority': 2, 'target': 'CTS objects: ' + ', '.join(sorted({m['attributes']['oid'] for w in workflows for m in w['menus']})),
         'evidenceNeeded': 'object-to-code/resource binding, ordered calls, prerequisites, failure and exit paths',
         'availableInRetainedArchive': False},
        {'priority': 3, 'target': 'access references: ' + ', '.join(sorted({line for s in sources for line in s.get('referenceLines', [])})),
         'evidenceNeeded': 'reference resolution and version-specific access dependencies; text names are not algorithms',
         'availableInRetainedArchive': False},
    ]
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    # Refuse overwrites and keep research outputs out of the original archive.
    if args.output_dir.resolve().is_relative_to(args.archive.resolve()):
        raise ValueError('research-output-inside-source-archive')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / 'research-index.json'
    if output.exists():
        raise ValueError('research-output-already-exists')
    index = inspect_archive(args.archive)
    with output.open('x', encoding='utf8') as stream:
        json.dump(index, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'summary': index['summary'], 'executionEnabled': False, 'noDeviceIO': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
