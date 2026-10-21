"""Read the verified PIWIS CompilationResults layout as data, never deserialize Java objects.

Supported stream grammar: https://docs.oracle.com/en/java/javase/17/docs/specs/serialization/protocol.html
Only the retained CompilationResults -> HashMap<String, byte[]> layout is accepted.
No constructors, readObject callbacks, class loading or target program execution.
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

from .piwis_java import Reader, inspect_class, modified_utf8

LIMIT = 4 * 1024 * 1024
RESULT_CLASS = 'de.dsa.rcp.plugins.scripting.core.util.CompilationResults'
RESULT_UID = -6040620178969725369
FIELDS = [('L', 'compilationMap', 'Ljava/util/Map;'), ('L', 'datatypeDescription', 'Ljava/lang/Object;'),
          ('L', 'entityOID', 'Ljava/lang/String;'), ('L', 'fullyQualifiedClassName', 'Ljava/lang/String;'),
          ('L', 'sourceString', 'Ljava/lang/String;')]
ENTITY_KINDS = {'CTS': 'CTc', 'Script': 'Scc', 'BuildingBlock': 'BBC', 'Screen': 'Scr'}


class CompilationReader(Reader):
    def __init__(self, raw):
        super().__init__(raw)
        self.handles = []

    def token(self, value):
        if self.uint(1) != value:
            raise ValueError('unsupported-compilation-stream-token')

    def handle(self, kind, value):
        self.handles.append((kind, value))

    def reference(self, kind):
        index = self.uint(4) - 0x7e0000
        if not 0 <= index < len(self.handles) or self.handles[index][0] != kind:
            raise ValueError('invalid-compilation-stream-reference')
        return self.handles[index][1]

    def text(self, nullable=False):
        token = self.uint(1)
        if token == 0x70 and nullable:
            return None
        if token == 0x71:
            return self.reference('string')
        if token not in (0x74, 0x7c):
            raise ValueError('unsupported-compilation-string')
        length = self.uint(2 if token == 0x74 else 8)
        if length > LIMIT:
            raise ValueError('compilation-string-size-limit')
        value = modified_utf8(self.take(length))
        self.handle('string', value)
        return value

    def utf(self):
        return modified_utf8(self.take(self.uint(2)))

    def descriptor(self):
        token = self.uint(1)
        if token == 0x71:
            return self.reference('descriptor')
        if token != 0x72:
            raise ValueError('unsupported-compilation-class-descriptor')
        result = {'name': self.utf(), 'uid': int.from_bytes(self.take(8), 'big', signed=True)}
        self.handle('descriptor', result)
        result['flags'] = self.uint(1)
        count = self.uint(2)
        if count > 16:
            raise ValueError('compilation-field-count-limit')
        fields = []
        for _ in range(count):
            code, name = chr(self.uint(1)), self.utf()
            fields.append((code, name, self.text() if code in ('L', '[') else None))
        result['fields'] = fields
        self.token(0x78)  # No class annotations supported.
        self.token(0x70)  # No serializable superclass supported.
        return result

    def object(self, name, fields, flags, uid):
        self.token(0x73)
        desc = self.descriptor()
        if (desc['name'] != name or desc['fields'] != fields or desc['flags'] != flags or desc['uid'] != uid):
            raise ValueError('unsupported-compilation-object-layout')
        self.handle('object', name)
        return desc


def inspect_compilation(raw, entity_kind='CTS'):
    if entity_kind not in ENTITY_KINDS:
        raise ValueError('unsupported-compilation-entity-kind')
    if len(raw) > LIMIT:
        raise ValueError('compilation-object-size-limit')
    reader = CompilationReader(raw)
    if reader.take(4) != b'\xac\xed\x00\x05':
        raise ValueError('invalid-compilation-stream-magic')
    reader.object(RESULT_CLASS, FIELDS, 2, RESULT_UID)
    reader.object('java.util.HashMap', [('F', 'loadFactor', None), ('I', 'threshold', None)], 3, 362498820763181265)
    load_factor = struct.unpack('>f', reader.take(4))[0]
    threshold = reader.uint(4)
    reader.token(0x77)
    if reader.uint(1) != 8:
        raise ValueError('unsupported-compilation-map-block')
    capacity, count = reader.uint(4), reader.uint(4)
    if not 0 < load_factor <= 1 or threshold > 1024 * 1024 or capacity > 1024 * 1024 or not 1 <= count <= 32:
        raise ValueError('unsupported-compilation-map-size')
    classes, keys = [], set()
    for _ in range(count):
        key = reader.text()
        if key in keys:
            raise ValueError('duplicate-compilation-class-key')
        keys.add(key)
        reader.token(0x75)
        desc = reader.descriptor()
        if desc != {'name': '[B', 'uid': -5984413125824719648, 'flags': 2, 'fields': []}:
            raise ValueError('unsupported-compilation-byte-array')
        reader.handle('byte-array', None)
        length = reader.uint(4)
        if not 0 < length <= LIMIT:
            raise ValueError('compilation-byte-array-size-limit')
        offset = reader.pos
        cls = inspect_class(reader.take(length))
        if cls['name'].replace('/', '.') != key:
            raise ValueError('compilation-class-key-mismatch')
        classes.append({'mapKey': key, 'classOffsetInObject': offset, 'classBytes': length, 'class': cls})
    reader.token(0x78)
    reader.token(0x70)  # datatypeDescription is null in the retained layout; arbitrary objects are rejected.
    oid_offset = reader.pos
    # Retained visual classes have no serialized entityOID. Keep it absent;
    # never promote their filename/class-name convention to an object identity.
    oid = reader.text(nullable=entity_kind == 'Screen')
    class_offset = reader.pos
    class_name = reader.text()
    source_offset = reader.pos
    source = reader.text(nullable=True)
    reader.end()
    if (oid is not None and not re.fullmatch(ENTITY_KINDS[entity_kind] + r'_[A-Za-z0-9_]+', oid)) or class_name not in keys:
        raise ValueError('compilation-entity-or-main-class-mismatch')
    return {'schemaVersion': 1, 'kind': 'piwis-' + entity_kind.lower() + '-compilation-static-research',
        'entityKind': entity_kind, 'scope': 'provided-object-only',
        'executionEnabled': False, 'liveVerified': False, 'targetExecuted': False, 'noDeviceIO': True,
        'sourceSha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'serialVersionUID': RESULT_UID,
        'entityOID': oid, 'entityIdentityPresent': oid is not None,
        'entityOIDTokenOffset': oid_offset, 'fullyQualifiedClassName': class_name,
        'classNameTokenOffset': class_offset, 'sourceString': source, 'sourceStringTokenOffset': source_offset,
        'classes': classes, 'summary': {'classes': count, 'methods': sum(len(c['class']['methods']) for c in classes)},
        'limitations': ['exact retained serialization layout only; other objects and custom callbacks rejected',
                       'object identity and class data are static evidence, not ECU service/vehicle qualification']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--object', type=Path, required=True)
    parser.add_argument('--entity-kind', choices=tuple(ENTITY_KINDS), default='CTS')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.object.stat().st_size > LIMIT:
        raise ValueError('compilation-object-size-limit')
    raw = args.object.read_bytes()
    result = inspect_compilation(raw, args.entity_kind)
    if args.object.read_bytes() != raw:
        raise ValueError('compilation-object-changed-during-inspection')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / 'compilation-index.json'
    with output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'sourceSha256': result['sourceSha256'], 'entityOID': result['entityOID'],
                      'class': result['fullyQualifiedClassName'], 'summary': result['summary'], 'targetExecuted': False}))


if __name__ == '__main__':
    main()
