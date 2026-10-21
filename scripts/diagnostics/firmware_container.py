"""Bounded, read-only PDX/ODX structure inspection. Never extract or execute."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zipfile
import zlib

MAX_FILE = 512 * 1024 * 1024
MAX_XML = 64 * 1024 * 1024
MAX_REPORT = 2 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_NODES = 200_000
FLAGS = dict(executionEnabled=False, liveVerified=False, writePayload=None,
             vehicleApplicabilityQualified=False, addressSemanticsQualified=False,
             encryptionCompressionInterpreted=False, binaryPayloadsExtracted=False)


def _xml(raw):
    if len(raw) > MAX_XML:
        raise ValueError('firmware-container-xml-limit')
    parser = expat.ParserCreate()
    nodes, depth = 0, 0

    def reject(*_):
        raise ValueError('firmware-container-xml-declaration-forbidden')

    def start(*_):
        nonlocal nodes, depth
        nodes += 1
        depth += 1
        if nodes > MAX_NODES or depth > 128:
            raise ValueError('firmware-container-xml-limit')

    def end(*_):
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(raw, True)
        return ET.fromstring(raw)
    except (expat.ExpatError, ET.ParseError) as error:
        raise ValueError('firmware-container-xml-invalid') from error


def _member(name):
    if (not name or '\\' in name or ':' in name or '\0' in name or name.startswith('/')
            or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))):
        raise ValueError('firmware-container-member-invalid')
    return name.rstrip('/')


def _hash(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _document(raw, name, payloads, issues):
    doc = _xml(raw)
    if doc.tag != 'ODX':
        raise ValueError('firmware-container-odx-invalid')
    version = doc.get('MODEL-VERSION')
    if version not in ('2.0.1', '2.1.0'):
        issues.append(dict(member=name, reason='odx-model-version-unqualified', version=version))
    ids = {}
    for node in doc.iter():
        key = node.get('ID')
        if key:
            if key in ids:
                raise ValueError('firmware-container-id-duplicate')
            ids[key] = node

    def reference(ref, expected, owner):
        target = ids.get(ref.get('ID-REF')) if ref is not None else None
        valid = ref is not None and not ref.get('DOCREF') and target is not None and target.tag == expected
        if not valid:
            issues.append(dict(member=name, reason='odx-reference-unresolved', owner=owner,
                               expected=expected, attributes=dict(ref.attrib) if ref is not None else None))
        return dict(attributes=dict(ref.attrib) if ref is not None else None, resolved=valid)

    def single(node, tag, reason, required=False):
        candidates = node.findall(tag)
        if len(candidates) > 1 or required and not candidates:
            issues.append(dict(member=name, reason=reason, owner=node.get('ID'),
                expected=tag, candidates=[dict(c.attrib) for c in candidates]))
        return candidates[0] if len(candidates) == 1 else None

    parents = {child: parent for parent in doc.iter() for child in parent}

    def ancestor(node, tags):
        while node is not None and node.tag not in tags:
            node = parents.get(node)
        return node

    def memory_id(node):
        node = ancestor(node, ('ECU-MEM',))
        return node.get('ID') if node is not None else None

    memory_sessions, memory_identifiers = {}, {}
    for node in doc.iter():
        if node.tag not in ('SESSION', 'EXPECTED-IDENT', 'OWN-IDENT'):
            continue
        index = memory_sessions if node.tag == 'SESSION' else memory_identifiers
        memory = ancestor(node, ('ECU-MEM',))
        index.setdefault(memory, {}).setdefault(node.findtext('SHORT-NAME'), []).append(node)

    def identifier(node):
        # A_ASCIISTRING padding and blank declarations are source data. Do not
        # trim, decode or reinterpret these as current/target ECU versions.
        if any(list(v) for v in node.findall('IDENT-VALUES/IDENT-VALUE')):
            issues.append(dict(member=name, reason='odx-identifier-value-unqualified', owner=node.get('ID')))
        return dict(id=node.get('ID'), name=node.findtext('SHORT-NAME'),
            longName=node.findtext('LONG-NAME'), values=[dict(attributes=dict(v.attrib),
                text=v.text or '', hasChildren=bool(list(v))) for v in node.findall('IDENT-VALUES/IDENT-VALUE')])

    sessions = [dict(id=n.get('ID'), name=n.findtext('SHORT-NAME'), memoryId=memory_id(n),
        datablocks=[reference(r, 'DATABLOCK', n.get('ID')) for r in n.iter('DATABLOCK-REF')],
        expectedIdentifiers=[identifier(i) for i in n.findall('EXPECTED-IDENTS/EXPECTED-IDENT')],
        expectedIdentifierCount=len(list(n.iter('EXPECTED-IDENT'))),
        securityEntryCount=len(list(n.iter('SECURITY')))) for n in doc.iter('SESSION')]
    blocks = [dict(id=n.get('ID'), name=n.findtext('SHORT-NAME'), type=n.get('TYPE'), memoryId=memory_id(n),
        ownIdentifiers=[identifier(i) for i in n.findall('OWN-IDENTS/OWN-IDENT')],
        flashdata=reference(single(n, 'FLASHDATA-REF', 'odx-reference-ambiguous'), 'FLASHDATA', n.get('ID')),
        segments=[dict(attributes=dict(s.attrib), fields=[dict(name=c.tag, text=(c.text or '').strip(),
            attributes=dict(c.attrib)) for c in s if not list(c)]) for s in n.iter('SEGMENT')]) for n in doc.iter('DATABLOCK')]
    flashdata = []
    for n in doc.iter('FLASHDATA'):
        format_node = single(n, 'DATAFORMAT', 'flashdata-dataformat-ambiguous-or-missing', required=True)
        method = single(n, 'ENCRYPT-COMPRESS-METHOD', 'flashdata-encrypt-compress-method-ambiguous')
        files, values = n.findall('DATAFILE'), n.findall('DATA')
        file = files[0] if len(files) == 1 else None
        data = values[0] if len(values) == 1 else None
        selection = format_node.get('SELECTION') if format_node is not None else None
        record = dict(id=n.get('ID'), name=n.findtext('SHORT-NAME'), attributes=dict(n.attrib),
            format=dict(format_node.attrib) if format_node is not None else None,
            encryptCompressMethod=dict(attributes=dict(method.attrib), text=(method.text or '').strip()) if method is not None else None,
            payload=None)
        if selection != 'BINARY':
            issues.append(dict(member=name, reason='flashdata-format-unqualified', id=n.get('ID'), format=selection))
        if len(files) + len(values) != 1:
            issues.append(dict(member=name, reason='flashdata-payload-ambiguous-or-missing', id=n.get('ID'),
                datafileCount=len(files), dataCount=len(values)))
        elif file is not None:
            text = (file.text or '').strip()
            try:
                _member(text)
                candidate = (PurePosixPath(name).parent / text).as_posix()
                _member(candidate)
            except ValueError:
                candidate = None
            found = payloads.get(candidate)
            record['payload'] = dict(kind='external-member', declaredName=text, attributes=dict(file.attrib),
                resolved=found is not None, **(dict(member=candidate, **found) if found is not None else {}))
            if found is None:
                issues.append(dict(member=name, reason='flashdata-member-unresolved', id=n.get('ID'), declaredName=text))
        else:
            text = data.text or ''
            compact = re.sub(r'[\t\r\n ]', '', text)
            if selection == 'BINARY' and not list(data) and len(compact) % 2 == 0 and re.fullmatch('[0-9a-fA-F]*', compact):
                value = bytes.fromhex(compact)
                record['payload'] = dict(kind='inline-hex', resolved=True, bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
            else:
                record['payload'] = dict(kind='inline-unqualified', resolved=False)
                issues.append(dict(member=name, reason='flashdata-inline-unqualified', id=n.get('ID')))
        flashdata.append(record)
    connectors = []
    for n in doc.iter('ECU-MEM-CONNECTOR'):
        refs = n.findall('ECU-MEM-REF')
        mem_ref = reference(refs[0] if len(refs) == 1 else None, 'ECU-MEM', n.get('ID'))
        memory = ids[refs[0].get('ID-REF')] if mem_ref['resolved'] else None
        descriptions = []
        for s in n.findall('SESSION-DESCS/SESSION-DESC'):
            refs = s.findall('SESSION-SNREF')
            ref = refs[0] if len(refs) == 1 else None
            targets = (memory_sessions.get(memory, {}).get(ref.get('SHORT-NAME'), [])
                if memory is not None and ref is not None and ref.get('SHORT-NAME') and not ref.get('DOCREF') else [])
            resolved = len(targets) == 1
            if not resolved:
                issues.append(dict(member=name, reason='odx-session-short-name-unresolved', owner=n.get('ID'),
                    attributes=dict(ref.attrib) if ref is not None else None, candidates=len(targets)))
            descriptions.append(dict(name=s.findtext('SHORT-NAME'), direction=s.get('DIRECTION'),
                partNumber=s.findtext('PARTNUMBER'), sessionReference=dict(
                    attributes=dict(ref.attrib) if ref is not None else None,
                    resolved=resolved, targetId=targets[0].get('ID') if resolved else None),
                diagnosticReferences=[dict(r.attrib) for r in s.findall('DIAG-COMM-SNREF')]))
        identities = []
        for i in n.findall('IDENT-DESCS/IDENT-DESC'):
            refs = i.findall('IDENT-IF-SNREF')
            ref = refs[0] if len(refs) == 1 else None
            # One interface name can occur in several blocks. Preserve every
            # source candidate; this is not a resolved diagnostic read binding.
            candidates = []
            if memory is not None and ref is not None and ref.get('SHORT-NAME') and not ref.get('DOCREF'):
                for t in memory_identifiers.get(memory, {}).get(ref.get('SHORT-NAME'), []):
                    owner = ancestor(t, ('SESSION', 'DATABLOCK', 'ECU-MEM'))
                    candidates.append(dict(id=t.get('ID'), kind=t.tag, ownerId=owner.get('ID'), ownerKind=owner.tag))
            identities.append(dict(identifierReference=dict(ref.attrib) if ref is not None else None,
                localCandidates=candidates, diagnosticReferences=[dict(r.attrib) for r in i.findall('DIAG-COMM-SNREF')],
                outputParameterReferences=[dict(r.attrib) for r in i.findall('OUT-PARAM-IF-SNREF')]))
        connectors.append(dict(id=n.get('ID'), name=n.findtext('SHORT-NAME'), memoryReference=mem_ref,
            sessionDescriptions=descriptions, identityDescriptions=identities,
            layerReferences=[dict(r.attrib) for r in n.findall('LAYER-REFS/LAYER-REF')]))
    if not sessions or not blocks or not flashdata:
        issues.append(dict(member=name, reason='odx-flash-structure-incomplete'))
    return dict(member=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), modelVersion=version,
        partNumbers=sorted({n.text.strip() for n in doc.iter('PARTNUMBER') if n.text and n.text.strip()}),
        sessions=sessions, blocks=blocks, flashdata=flashdata, memoryConnectors=connectors,
        identityMatchQualified=False)


def inspect_container(file):
    path = Path(file)
    issues, documents, members = [], [], []
    with path.open('rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_FILE:
            raise ValueError('firmware-size-invalid')
        digest = _hash(source)
        source.seek(0)
        suffix = path.suffix.lower()
        recognized = suffix in ('.pdx', '.odx', '.odx-f')
        if suffix == '.pdx':
            try:
                with zipfile.ZipFile(source) as package:
                    infos = package.infolist()
                    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_FILE:
                        raise ValueError('firmware-container-expanded-limit')
                    # Windows ZipInfo normalizes backslashes and strips NUL suffixes.
                    # Validate original archive names before accepting normalized ones.
                    canonical = [_member(i.orig_filename).casefold() for i in infos]
                    if len(set(canonical)) != len(canonical):
                        raise ValueError('firmware-container-member-duplicate')
                    payloads = {}
                    for i in infos:
                        if i.is_dir():
                            continue
                        if stat.S_ISLNK(i.external_attr >> 16):
                            raise ValueError('firmware-container-member-invalid')
                        if i.flag_bits & 1:
                            raise ValueError('firmware-container-zip-encrypted')
                        with package.open(i) as stream:
                            payloads[i.filename] = dict(bytes=i.file_size, sha256=_hash(stream))
                        members.append(dict(name=i.filename, **payloads[i.filename]))
                    if 'index.xml' in payloads:
                        if payloads['index.xml']['bytes'] > MAX_XML:
                            raise ValueError('firmware-container-xml-limit')
                        catalog = _xml(package.read('index.xml'))
                        for n in catalog.iter('FILE'):
                            declared = (n.text or '').strip()
                            if declared not in payloads:
                                issues.append(dict(member='index.xml', reason='catalog-member-unresolved', declaredName=declared))
                    else:
                        issues.append(dict(reason='pdx-catalog-missing'))
                    for i in infos:
                        if i.filename.lower().endswith(('.odx-f', '.odx')) and not i.is_dir():
                            if i.file_size > MAX_XML:
                                raise ValueError('firmware-container-xml-limit')
                            documents.append(_document(package.read(i), i.filename, payloads, issues))
                    if not documents:
                        issues.append(dict(reason='pdx-odx-missing'))
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as error:
                raise ValueError('firmware-container-zip-invalid') from error
        elif recognized:
            if before.st_size > MAX_XML:
                raise ValueError('firmware-container-xml-limit')
            documents.append(_document(source.read(), path.name, {}, issues))
        else:
            issues.append(dict(reason='firmware-container-format-unrecognized'))
        source.seek(0)
        after_digest = _hash(source)
        after = os.fstat(source.fileno())
        if digest != after_digest or any(getattr(before, k) != getattr(after, k) for k in ('st_size', 'st_mtime_ns', 'st_ino', 'st_dev')):
            raise ValueError('firmware-container-file-changed')
    payloads = [f['payload'] for d in documents for f in d['flashdata'] if f['payload']]
    report = dict(schemaVersion=1, kind='ecu-firmware-container-inspection',
        file=dict(name=path.name, bytes=before.st_size, sha256=digest), recognized=recognized,
        complete=recognized and bool(documents) and not issues, members=members, documents=documents, issues=issues,
        counts=dict(documents=len(documents), sessions=sum(len(d['sessions']) for d in documents),
            blocks=sum(len(d['blocks']) for d in documents), flashdata=sum(len(d['flashdata']) for d in documents),
            externalPayloads=sum(p['kind'] == 'external-member' for p in payloads),
            inlinePayloads=sum(p['kind'] == 'inline-hex' for p in payloads), issues=len(issues)), **FLAGS)
    if len(json.dumps(report, ensure_ascii=False).encode('utf8')) > MAX_REPORT - 1024:
        raise ValueError('firmware-container-report-limit')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(dict(ok=True, container=inspect_container(args.file), **FLAGS), ensure_ascii=False))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps(dict(ok=False, error=str(error) if isinstance(error, ValueError) else 'firmware-container-file-unavailable', **FLAGS)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
