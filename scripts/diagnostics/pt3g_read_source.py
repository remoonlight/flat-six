"""Pinned PT3G diagnostic resources and read-only native object lookup.

No module connection, logical link, parameter application or ECU primitive.
Declared object IDs are compared with runtime IDs, never used as native handles.
"""
from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .elm import ElmError
from .pt3g import NativeAdapter, SUPPORT_SHA256, vendor_root
from .vnci import U32, UNDEF, ID_UNDEF, _check, _serialized_native

GUARDS = dict(moduleConnected=False, logicalLinkCreated=False, parametersApplied=False,
              ecuRequestsSent=0, firmwareQualified=False, vehicleTransportQualified=False,
              physicalSilenceVerified=False, executionEnabled=False)
RESOURCES = (
    ('dme', 'PT3G_VCI_KWP2000_CAN1_HS_OBD6_14', 'ISO_14230_3_on_ISO_15765_2', 0x7E0, 0x7E8),
    ('gateway', 'PT3G_VCI_UDS_CAN1_HS_OBD6_14', 'ISO_15765_3_on_ISO_15765_2', 0x710, 0x77A),
    ('standard-engine', 'PT3G_VCI_OBD_CAN1_HS_OBD6_14', 'ISO_OBD_on_ISO_15765_4', 0x7DF, 0x7E8),
)
COMMON = dict(CP_Baudrate=500000, CP_TesterPresentHandling=0, CP_RC21Handling=0,
              CP_RC23Handling=0, CP_RC78Handling=1, CP_RC78CompletionTimeout=8000000,
              CP_RepeatReqCountApp=0, CP_RepeatReqCountTrans=0,
              CP_CanFuncReqId=0x7DF, CP_CanFuncReqFormat=5)
UNIQUE = dict(CP_CanPhysReqFormat=5, CP_CanRespUSDTFormat=5, CP_CanRespUUDTId=UNDEF)


def _text(element, name):
    value = element.findtext(name)
    if value is None or not value.strip():
        raise ValueError('pt3g-read-source-field-missing:' + name)
    return value.strip()


def _uint(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= UNDEF:
        raise ValueError('pt3g-read-source-integer-invalid')
    return int(value)


def _parameters(element, objects):
    out = {}
    for ref in element.findall('COMPARAM_REF'):
        links = ref.findall('COMPARAM')
        if len(links) != 1 or links[0].get('IDREF') not in objects:
            raise ValueError('pt3g-read-source-parameter-reference-missing')
        target = objects[links[0].get('IDREF')]
        if target.tag != 'COMPARAM':
            raise ValueError('pt3g-read-source-parameter-reference-kind')
        name = _text(target, 'SHORT_NAME')
        if name in out:
            raise ValueError('pt3g-read-source-parameter-ambiguous')
        out[name] = dict(name=name, sourceId=_uint(_text(target, 'ID')),
            dataType=_text(target, 'DATA_TYPE'), parameterClass=_text(target, 'CLASS'),
            layer=_text(target, 'LAYER'), defaultValue=ref.findtext('DEFAULT_VALUE', target.findtext('DEFAULT_VALUE')),
            minimum=ref.findtext('MIN_VALUE'), maximum=ref.findtext('MAX_VALUE'))
    return out


def read_source_contract(path):
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != SUPPORT_SHA256['MDF_VCI.xml']:
        raise ValueError('pt3g-read-MDF-source-unreviewed')
    if len(raw) > 4*1024*1024 or b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
        raise ValueError('pt3g-read-MDF-unsafe')
    doc = ET.fromstring(raw); objects = {}
    for element in doc.iter():
        eid = element.get('EID')
        if eid:
            if eid in objects:
                raise ValueError('pt3g-read-source-object-ambiguous')
            objects[eid] = element
    modules = [m for m in doc.findall('MODULETYPE') if m.findtext('ID') == '11']
    if len(modules) != 1 or _text(modules[0], 'SHORT_NAME') != 'PT3G_VCI':
        raise ValueError('pt3g-read-source-module-mismatch')
    # Conflict-group declarations alone are not the module's available resources.
    available = {r.get('IDREF') for r in modules[0].findall('RESOURCE')}
    routes, queries = [], {}
    def query(kind, element):
        name = _text(element, 'SHORT_NAME'); key = (kind, name)
        row = dict(objectType=kind, name=name, sourceId=_uint(_text(element, 'ID')))
        if key in queries and queries[key] != row:
            raise ValueError('pt3g-read-source-object-id-conflict')
        queries[key] = row
        return row
    for purpose, resource_name, protocol_name, tx, rx in RESOURCES:
        candidates = [r for r in doc.findall('RESOURCE') if r.findtext('SHORT_NAME') == resource_name]
        if len(candidates) != 1 or candidates[0].get('EID') not in available:
            raise ValueError('pt3g-read-source-resource-not-in-module:' + resource_name)
        resource = candidates[0]
        query(0x8026, resource)
        resolved = {}
        for tag in ('BUSTYPE', 'PROTOCOL'):
            links = resource.findall(tag)
            if len(links) != 1 or links[0].get('IDREF') not in objects:
                raise ValueError('pt3g-read-source-resource-reference-missing')
            resolved[tag] = objects[links[0].get('IDREF')]
            if resolved[tag].tag != tag:
                raise ValueError('pt3g-read-source-resource-reference-kind')
        bus, protocol = resolved['BUSTYPE'], resolved['PROTOCOL']
        if _text(bus, 'SHORT_NAME') != 'ISO_11898_2_DWCAN' or _text(protocol, 'SHORT_NAME') != protocol_name:
            raise ValueError('pt3g-read-source-resource-protocol-mismatch')
        pins = []
        for pin in resource.findall('PIN_ON_MODULE'):
            links = pin.findall('PINTYPE')
            if len(links) != 1 or links[0].get('IDREF') not in objects:
                raise ValueError('pt3g-read-source-pin-reference-missing')
            target = objects[links[0].get('IDREF')]
            if target.tag != 'PINTYPE':
                raise ValueError('pt3g-read-source-pin-reference-kind')
            pins.append(dict(pin=_uint(_text(pin, 'PIN_ON_MODULE')), **query(0x8025, target)))
        if [(p['pin'], p['name']) for p in pins] != [(6, 'HI'), (14, 'LOW')]:
            raise ValueError('pt3g-read-source-pin-mismatch')
        query(0x8022, bus); query(0x8021, protocol)
        physical, transport = _parameters(bus, objects), _parameters(protocol, objects)
        if set(physical) & set(transport):
            raise ValueError('pt3g-read-source-parameter-layer-conflict')
        params = physical | transport
        values = COMMON | dict(CP_RequestAddrMode=2 if tx==0x7DF else 1) | UNIQUE | dict(
            CP_CanPhysReqId=0x7E0 if tx==0x7DF else tx, CP_CanRespUSDTId=rx)
        settings = []
        for name, value in values.items():
            if name not in params:
                raise ValueError('pt3g-read-source-required-parameter-missing:' + name)
            param = params[name]
            expected_class = 'UNIQUE_ID' if name in UNIQUE or name in ('CP_CanPhysReqId','CP_CanRespUSDTId') else None
            if (param['dataType'] != 'PDU_PT_UNUM32'
                    or (expected_class is not None and param['parameterClass'] != expected_class)
                    or (expected_class is None and param['parameterClass'] == 'UNIQUE_ID')
                    or not _uint(param['minimum']) <= value <= _uint(param['maximum'])):
                raise ValueError('pt3g-read-source-parameter-contract-invalid:' + name)
            key=(0x8024,name); row=dict(objectType=key[0],name=name,sourceId=param['sourceId'])
            if key in queries and queries[key]!=row:
                raise ValueError('pt3g-read-source-object-id-conflict')
            queries[key]=row; settings.append(param | dict(candidateValue=value, applied=False))
        routes.append(dict(purpose=purpose, sourceResource=resource_name,
            sourceResourceId=_uint(_text(resource, 'ID')), bus=_text(bus,'SHORT_NAME'),
            protocol=protocol_name, pins=pins, txId=f'{tx:03X}',rxId=f'{rx:03X}',parameters=settings, **GUARDS))
    return dict(kind='pt3g-diagnostic-read-source-contract', mdfSha256=sha, moduleType=11,
                routes=routes, objectQueries=list(queries.values()), **GUARDS)


class _ReadObjectProbe(NativeAdapter):
    def __init__(self, root):
        self.contract = read_source_contract(Path(root)/'MDF_VCI.xml')
        self._read_objects = {(r['objectType'],r['name'].encode('ascii')) for r in self.contract['objectQueries']}
        super().__init__(root)

    @_serialized_native
    def call(self, name, *args):
        self._require_open()
        if name=='PDUGetObjectId':
            if len(args)!=3 or type(args[0]) is not int or type(args[1]) is not bytes or (args[0],args[1]) not in self._read_objects:
                raise ElmError('pt3g-read-object-not-in-source-contract')
            _check(self.dll.PDUGetObjectId(*args), name)
        elif name in ('PDUConstruct','PDUDestruct'):
            super().call(name,*args)
        else:
            raise ElmError('pt3g-read-object-probe-only-api')


def _query_objects(native, contract):
    rows=[]
    for row in contract['objectQueries']:
        value=U32(ID_UNDEF); error=None
        try:
            native.call('PDUGetObjectId',row['objectType'],row['name'].encode('ascii'),c.byref(value))
            if value.value in (UNDEF,ID_UNDEF):
                raise ElmError('pt3g-read-runtime-object-undefined')
        except ElmError as problem:
            error=str(problem)
        rows.append(row | dict(runtimeId=value.value,error=error,
                               sourceIdMatchesRuntime=error is None and value.value==row['sourceId']))
    return rows


def probe_read_objects(root=None):
    root=Path(root if root is not None else vendor_root())
    native=_ReadObjectProbe(root)
    try:
        rows=_query_objects(native,native.contract)
    finally:
        cleanup=native.close()
    matched=bool(rows) and all(r['sourceIdMatchesRuntime'] for r in rows)
    return dict(ok=matched and not cleanup['errors'],kind='pt3g-native-read-object-probe',
                sourceContract=native.contract,objects=rows,allSourceObjectIdsMatched=matched,
                configuration=native.configuration,cleanup=cleanup,driverLoaded=True,**GUARDS)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',type=Path,required=True)
    parser.add_argument('--probe-objects',action='store_true')
    args=parser.parse_args()
    report=probe_read_objects(args.driver_root) if args.probe_objects else read_source_contract(args.driver_root/'MDF_VCI.xml')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0 if report.get('ok',True) else 2


if __name__=='__main__':
    raise SystemExit(main())
