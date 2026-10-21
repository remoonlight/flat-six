"""Static PIWIS Java archive inspection without Java, application launch or devices.

ClassFile/Code layout: Java Virtual Machine Specification chapters 4 and 6.
Instruction references describe symbolic targets, not runtime dispatch or execution.
Original classes remain local; output is research evidence, not executable definitions.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import struct
import zipfile

from scripts.archive_piwis_workshop import safe_relative
from scripts.diagnostics.piwis_research import xml


class Reader:
    def __init__(self, raw, base=0):
        self.raw, self.pos, self.base = raw, 0, base

    def take(self, size):
        if size < 0 or self.pos + size > len(self.raw):
            raise ValueError('truncated-class-structure')
        value = self.raw[self.pos:self.pos + size]
        self.pos += size
        return value

    def uint(self, size):
        return int.from_bytes(self.take(size), 'big')

    def end(self):
        if self.pos != len(self.raw):
            raise ValueError('unconsumed-class-structure')


def modified_utf8(raw):
    text = raw.replace(b'\xc0\x80', b'\0').decode('utf8', 'surrogatepass')
    return text.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'surrogatepass')


def constant_pool(reader):
    size = reader.uint(2)
    if size == 0:
        raise ValueError('invalid-constant-pool-size')
    pool = [None] * size
    i = 1
    while i < size:
        tag = reader.uint(1)
        if tag == 1:
            raw = reader.take(reader.uint(2))
            value = modified_utf8(raw)
        elif tag in (3, 4, 5, 6):
            value = struct.unpack({3: '>i', 4: '>f', 5: '>q', 6: '>d'}[tag], reader.take(8 if tag in (5, 6) else 4))[0]
            if isinstance(value, float) and not math.isfinite(value):
                value = 'NaN' if math.isnan(value) else 'Infinity' if value > 0 else '-Infinity'
        elif tag in (7, 8, 16, 19, 20):
            value = reader.uint(2)
        elif tag in (9, 10, 11, 12, 17, 18):
            value = (reader.uint(2), reader.uint(2))
        elif tag == 15:
            value = (reader.uint(1), reader.uint(2))
        else:
            raise ValueError('unsupported-constant-pool-tag: ' + str(tag))
        pool[i] = (tag, value)
        i += 2 if tag in (5, 6) else 1
        if i > size:
            raise ValueError('invalid-wide-constant-slot')
    return pool


def cp(pool, index, tags):
    if index < 1 or index >= len(pool) or pool[index] is None or pool[index][0] not in tags:
        raise ValueError('invalid-constant-pool-reference')
    return pool[index][1]


def utf(pool, index):
    return cp(pool, index, (1,))


def class_name(pool, index):
    return utf(pool, cp(pool, index, (7,)))


def symbol(pool, index, tags):
    owner, name_type = cp(pool, index, tags)
    name, descriptor = cp(pool, name_type, (12,))
    return {'owner': class_name(pool, owner), 'name': utf(pool, name), 'descriptor': utf(pool, descriptor)}


def attributes(reader, pool):
    result = []
    for _ in range(reader.uint(2)):
        name = utf(pool, reader.uint(2))
        size = reader.uint(4)
        start = reader.base + reader.pos
        result.append((name, reader.take(size), start))
    return result


# Operand sizes from JVMS 6.5. Reserved opcodes are never interpreted as one-byte instructions.
SIZES = {op: 1 for op in range(202)}
SIZES.update({op: 2 for op in (16, 18, *range(21, 26), *range(54, 59), 169, 188)})
SIZES.update({op: 3 for op in (17, 19, 20, 132, *range(153, 169), *range(178, 185), 187, 189, 192, 193, 198, 199)})
SIZES.update({185: 5, 186: 5, 197: 4, 200: 5, 201: 5})
OP_NAMES = {16: 'bipush', 17: 'sipush', 18: 'ldc', 19: 'ldc_w', 20: 'ldc2_w', 170: 'tableswitch',
    171: 'lookupswitch', 178: 'getstatic', 179: 'putstatic', 180: 'getfield', 181: 'putfield',
    182: 'invokevirtual', 183: 'invokespecial', 184: 'invokestatic', 185: 'invokeinterface', 186: 'invokedynamic',
    187: 'new', 189: 'anewarray', 192: 'checkcast', 193: 'instanceof', 196: 'wide', 197: 'multianewarray',
    172: 'ireturn', 173: 'lreturn', 174: 'freturn', 175: 'dreturn', 176: 'areturn', 177: 'return'}
OP_NAMES.update({0: 'nop', 1: 'aconst_null', 2: 'iconst_m1', 9: 'lconst_0', 10: 'lconst_1',
    14: 'dconst_0', 15: 'dconst_1', 87: 'pop', 89: 'dup', 96: 'iadd', 100: 'isub',
    104: 'imul', 108: 'idiv', 112: 'irem', 120: 'ishl', 122: 'ishr', 124: 'iushr',
    126: 'iand', 128: 'ior', 130: 'ixor', 132: 'iinc', 135: 'i2d', 167: 'goto',
    198: 'ifnull', 199: 'ifnonnull', 200: 'goto_w'})
OP_NAMES.update({op: f'iconst_{op - 3}' for op in range(3, 9)})
for start, names in ((21, ('iload', 'lload', 'fload', 'dload', 'aload')),
                     (54, ('istore', 'lstore', 'fstore', 'dstore', 'astore'))):
    OP_NAMES.update({start + n: name for n, name in enumerate(names)})
for start, name in ((26, 'iload'), (30, 'lload'), (34, 'fload'), (38, 'dload'), (42, 'aload'),
                    (59, 'istore'), (63, 'lstore'), (67, 'fstore'), (71, 'dstore'), (75, 'astore')):
    OP_NAMES.update({start + n: f'{name}_{n}' for n in range(4)})


def instructions(code, pool, base=0):
    r, result, targets = Reader(code), [], []
    while r.pos < len(code):
        start = r.pos
        op = r.uint(1)
        if op not in SIZES:
            raise ValueError('reserved-or-unknown-bytecode')
        item = {'pc': start, 'classOffset': base + start, 'opcode': op, 'mnemonic': OP_NAMES.get(op, f'op_{op:02x}')}
        if op in (170, 171):
            padding = r.take((-r.pos) % 4)
            if any(padding):
                raise ValueError('nonzero-switch-padding')
            default = int.from_bytes(r.take(4), 'big', signed=True)
            jumps = [start + default]
            if op == 170:
                low = int.from_bytes(r.take(4), 'big', signed=True)
                high = int.from_bytes(r.take(4), 'big', signed=True)
                if high < low or high - low + 1 > len(code) // 4:
                    raise ValueError('invalid-table-switch')
                pairs = [(key, int.from_bytes(r.take(4), 'big', signed=True)) for key in range(low, high + 1)]
            else:
                count = int.from_bytes(r.take(4), 'big', signed=True)
                if count < 0 or count > len(code) // 8:
                    raise ValueError('invalid-lookup-switch')
                pairs = [(int.from_bytes(r.take(4), 'big', signed=True), int.from_bytes(r.take(4), 'big', signed=True)) for _ in range(count)]
                if [key for key, _ in pairs] != sorted({key for key, _ in pairs}):
                    raise ValueError('invalid-lookup-switch-keys')
            jumps.extend(start + offset for _, offset in pairs)
            targets.extend(jumps)
            item['switch'] = {'defaultTarget': jumps[0], 'cases': [{'key': key, 'target': start + offset} for key, offset in pairs]}
        elif op == 196:
            widened = r.uint(1)
            if widened not in (*range(21, 26), *range(54, 59), 132, 169):
                raise ValueError('invalid-wide-bytecode')
            item['widenedOpcode'] = widened
            item['operandsHex'] = r.take(4 if widened == 132 else 2).hex()
        else:
            raw = r.take(SIZES[op] - 1)
            if raw:
                item['operandsHex'] = raw.hex()
            if op in (*range(153, 169), 198, 199, 200, 201):
                item['branchTarget'] = start + int.from_bytes(raw, 'big', signed=True)
                targets.append(item['branchTarget'])
            if op in (178, 179, 180, 181):
                item['field'] = symbol(pool, int.from_bytes(raw, 'big'), (9,))
            elif op in (182, 183, 184, 185):
                index = int.from_bytes(raw[:2], 'big')
                item['call'] = symbol(pool, index, (11,) if op == 185 else (10,) if op == 182 else (10, 11))
                if op == 185 and (raw[2] == 0 or raw[3] != 0):
                    raise ValueError('invalid-invokeinterface-operands')
            elif op == 186:
                bootstrap, nt = cp(pool, int.from_bytes(raw[:2], 'big'), (18,))
                name, descriptor = cp(pool, nt, (12,))
                if raw[2:] != b'\0\0':
                    raise ValueError('invalid-invokedynamic-operands')
                item['dynamicCall'] = {'bootstrapIndex': bootstrap, 'name': utf(pool, name), 'descriptor': utf(pool, descriptor)}
            elif op in (187, 189, 192, 193, 197):
                item['classReference'] = class_name(pool, int.from_bytes(raw[:2], 'big'))
            elif op in (18, 19, 20):
                index = int.from_bytes(raw, 'big')
                cp(pool, index, (5, 6, 17) if op == 20 else (3, 4, 7, 8, 15, 16, 17))
                tag, value = pool[index]
                if tag == 8:
                    item['string'] = utf(pool, value)
                elif tag == 7:
                    item['classReference'] = class_name(pool, index)
                elif tag in (3, 4, 5, 6):
                    item['number'] = value
            elif op in (16, 17):
                item['number'] = int.from_bytes(raw, 'big', signed=True)
        item['length'] = r.pos - start
        result.append(item)
    pcs = {i['pc'] for i in result}
    if any(target not in pcs for target in targets):
        raise ValueError('branch-target-not-an-instruction')
    return result


def inspect_class(raw):
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('class-size-limit')
    r = Reader(raw)
    if r.take(4) != b'\xca\xfe\xba\xbe':
        raise ValueError('invalid-class-magic')
    minor, major = r.uint(2), r.uint(2)
    pool = constant_pool(r)
    access, this, parent = r.uint(2), r.uint(2), r.uint(2)
    result = {'name': class_name(pool, this), 'sha256': hashlib.sha256(raw).hexdigest(), 'minor': minor, 'major': major,
              'access': access, 'super': class_name(pool, parent) if parent else None,
              'interfaces': [class_name(pool, r.uint(2)) for _ in range(r.uint(2))], 'fields': [], 'methods': []}
    for field in (True, False):
        for _ in range(r.uint(2)):
            flags, name, descriptor = r.uint(2), utf(pool, r.uint(2)), utf(pool, r.uint(2))
            member = {'name': name, 'descriptor': descriptor, 'access': flags}
            attrs = attributes(r, pool)
            codes = [a for a in attrs if a[0] == 'Code']
            if len(codes) > 1 or (field and codes):
                raise ValueError('duplicate-or-invalid-code-attribute')
            for attr, payload, start in attrs:
                if attr == 'ConstantValue':
                    a = Reader(payload); index = a.uint(2); a.end()
                    value = cp(pool, index, (3, 4, 5, 6, 8))
                    member['constant'] = utf(pool, value) if pool[index][0] == 8 else value
                elif attr == 'Code':
                    a = Reader(payload, start)
                    member['maxStack'], member['maxLocals'] = a.uint(2), a.uint(2)
                    size = a.uint(4)
                    if size < 1 or size > 65535:
                        raise ValueError('code-length-invalid')
                    code_start = start + a.pos
                    code = a.take(size)
                    member['codeOffset'], member['codeSha256'] = code_start, hashlib.sha256(code).hexdigest()
                    member['instructions'] = instructions(code, pool, code_start)
                    handlers = []
                    for _ in range(a.uint(2)):
                        begin, end, handler, catch = a.uint(2), a.uint(2), a.uint(2), a.uint(2)
                        pcs = {i['pc'] for i in member['instructions']}
                        if begin not in pcs or end not in pcs | {size} or handler not in pcs or begin >= end:
                            raise ValueError('invalid-exception-handler')
                        handlers.append({'start': begin, 'end': end, 'handler': handler, 'catch': class_name(pool, catch) if catch else None})
                    member['exceptionHandlers'] = handlers
                    attributes(a, pool)
                    a.end()
            result['fields' if field else 'methods'].append(member)
    attributes(r, pool)
    r.end()
    result['referencedClasses'] = sorted({class_name(pool, i) for i in range(1, len(pool)) if pool[i] and pool[i][0] == 7})
    return result


def manifest_fields(raw):
    # Some retained manifests use CRCRLF. Fold continuation lines without assuming platform newlines.
    lines = raw.decode('utf8').replace('\r', '').split('\n')
    fields, key = {}, None
    for line in lines:
        if not line:
            break  # Main bundle section only; named member sections have independent headers.
        if line.startswith(' ') and key:
            fields[key] += line[1:]
        elif ': ' in line:
            key, value = line.split(': ', 1)
            if key in fields:
                raise ValueError('duplicate-manifest-header')
            fields[key] = value
    return fields


def inspect_jar(path):
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('jar-size-limit')
    raw = path.read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('jar-size-limit')
    classes = []
    with zipfile.ZipFile(BytesIO(raw)) as jar:
        names = jar.namelist()
        if len(names) > 10000 or len(set(names)) != len(names):
            raise ValueError('duplicate-or-excessive-jar-members')
        for name in names:
            safe_relative(name.rstrip('/'))
        if sum(info.file_size for info in jar.infolist()) > 128 * 1024 * 1024:
            raise ValueError('jar-expanded-size-limit')
        for info in jar.infolist():
            if info.file_size > (16 * 1024 * 1024 if info.filename.endswith('.class') else 1024 * 1024):
                raise ValueError('jar-member-size-limit')
        if jar.testzip():
            raise ValueError('jar-crc-failure')
        # The JAR specification permits archives without META-INF/manifest.
        # https://docs.oracle.com/en/java/javase/17/docs/specs/jar/jar.html
        manifest_present = 'META-INF/MANIFEST.MF' in names
        manifest = manifest_fields(jar.read('META-INF/MANIFEST.MF')) if manifest_present else {}
        for name in names:
            if name.endswith('.class'):
                if jar.getinfo(name).file_size > 16 * 1024 * 1024:
                    raise ValueError('class-size-limit')
                cls = inspect_class(jar.read(name))
                if cls['name'] + '.class' != name:
                    raise ValueError('class-member-name-mismatch')
                classes.append({'member': name, **cls})
        extensions = []
        if 'plugin.xml' in names:
            root = xml(jar.read('plugin.xml'))
            for ext in root.findall('extension'):
                extensions.append({'point': ext.get('point'), 'providers': [node.get('class') for node in ext.findall('provider')]})
    if path.read_bytes() != raw:
        raise ValueError('jar-changed-during-inspection')
    owners = {c['name'] for c in classes}
    calls = [{**i['call'], 'fromClass': c['name'], 'fromMethod': m['name'], 'fromDescriptor': m['descriptor'],
              'member': c['member'], 'classSha256': c['sha256'], 'classOffset': i['classOffset'], 'pc': i['pc'],
              'dispatch': i['mnemonic'], 'ownerPresent': i['call']['owner'] in owners}
             for c in classes for m in c['methods'] for i in m.get('instructions', []) if 'call' in i]
    declared = {(c['name'], m['name'], m['descriptor']): 'instructions' in m
                for c in classes for m in c['methods']}
    for call in calls:
        key = (call['owner'], call['name'], call['descriptor'])
        call['exactMethodPresent'] = key in declared
        call['exactMethodHasCode'] = declared.get(key, False)
    return {'schemaVersion': 1, 'kind': 'piwis-java-static-research', 'scope': 'provided-jar-only',
        'executionEnabled': False, 'liveVerified': False, 'noDeviceIO': True, 'targetExecuted': False,
        'source': {'file': path.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
        'manifest': manifest, 'manifestPresent': manifest_present, 'extensions': extensions,
        'summary': {'classes': len(classes), 'methods': sum(len(c['methods']) for c in classes),
            'methodsWithCode': sum('instructions' in m for c in classes for m in c['methods']),
            'symbolicCalls': len(calls), 'ownersOutsideProvidedJar': len({c['owner'] for c in calls if not c['ownerPresent']}),
            'classVersions': sorted({c['major'] for c in classes})},
        'classes': classes, 'symbolicCalls': calls,
        'limitations': ['symbolic call targets do not establish virtual dispatch, runtime paths or ECU applicability',
            'unavailable dependency bodies remain unresolved', 'not a JVM verifier, decompiler, diagnostic definition or vehicle operation']}


def program_probes(index):
    """Project-relevant source locations; no execution or guessed protocol definitions."""
    classes = {c['name']: c for c in index['classes']}
    vendor_missing = sorted({name for c in classes.values() for name in c['referencedClasses']
                             if name.startswith('de/dsa/') and name not in classes})
    dependencies = [{'class': name, 'status': 'body-not-in-provided-jar',
                     'callSites': [c for c in index['symbolicCalls'] if c['owner'] == name]}
                    for name in vendor_missing]
    entrypoints = []
    for extension in index['extensions']:
        for provider in extension['providers']:
            owner = (provider or '').replace('.', '/')
            cls = classes.get(owner)
            entrypoints.append({'extensionPoint': extension['point'], 'provider': provider,
                'bodyPresent': cls is not None, 'member': cls['member'] if cls else None,
                'classSha256': cls['sha256'] if cls else None,
                'calls': [c for c in index['symbolicCalls'] if c['fromClass'] == owner]})
    scalings = []
    for cls in classes.values():
        if not cls['name'].startswith('de/dsa/pidt/obd/scaling/ScalingDefinition'):
            continue
        row = {'class': cls['name'], 'member': cls['member'], 'classSha256': cls['sha256'],
               'identifierKind': 'scaling-id-not-pid', 'ecuBindingVerified': False, 'methods': []}
        for method in cls['methods']:
            if method['name'] in ('getScalingId', 'getUnit', 'getDisplayUnit', 'translateValue'):
                row['methods'].append(method)
        scalings.append(row)
    return {'schemaVersion': 1, 'kind': 'piwis-program-source-probes', 'source': index['source'],
        'scope': index['scope'], 'executionEnabled': False, 'liveVerified': False, 'targetExecuted': False,
        'summary': index['summary'], 'requiredBundles': index['manifest'].get('Require-Bundle', ''),
        'entrypoints': entrypoints, 'vendorDependencies': dependencies, 'scalingDefinitions': scalings,
        'scalingUseMethods': [{'class': cls['name'], 'member': cls['member'], 'classSha256': cls['sha256'],
                              'method': method} for cls in classes.values() for method in cls['methods']
                             if '/scaling/' not in cls['name'] and any(
                                 i.get('call', {}).get('owner') in ('de/dsa/pidt/obd/scaling/ScalingManager',
                                                                   'de/dsa/pidt/obd/scaling/IScalingDefinition')
                                 for i in method.get('instructions', []))],
        'serviceCallSites': [c for c in index['symbolicCalls'] if c['fromClass'].endswith('/OBDServiceHandling')
                             and c['owner'].startswith('de/dsa/')],
        'gaps': ['external service and transport bodies', 'diagnostic database requests/responses and decoding definitions',
                 'CTS objects and script loading outside this plugin', 'ECU identity/version and live qualification'],
        'limitations': index['limitations']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / 'java-index.json'
    probes = args.output_dir / 'program-probes.json'
    if output.exists() or probes.exists():
        raise ValueError('java-research-output-already-exists')
    result = inspect_jar(args.jar)
    for path, value in ((output, result), (probes, program_probes(result))):
        with path.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps({'summary': result['summary'], 'source': result['source'], 'targetExecuted': False}))


if __name__ == '__main__':
    main()
