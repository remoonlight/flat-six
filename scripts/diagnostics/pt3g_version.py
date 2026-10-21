"""PT3G driver-level version metadata; never qualifies a physical head.

ABI facts follow the local ISO22900.II PDU_VERSION_DATA reference. The pinned
wrapper has fixed VAS6154A metadata and an unchecked delegated return path.
Only PDUGetVersion(UNDEF) is exposed; no module, logical link or primitive.
"""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct

from .elm import ElmError
from .pt3g import NativeAdapter, SUPPORT_SHA256, vendor_root
from .vnci import U32, UNDEF, _check, _serialized_native

GUARDS=dict(moduleConnected=False,logicalLinkCreated=False,ecuRequestsSent=0,
            hardwareIdentityQualified=False,firmwareQualified=False,
            vehicleTransportQualified=False,executionEnabled=False)
TEXT_FIELDS=('hardwareName','firmwareName','vendorName','apiName')


class VersionData(c.Structure):
    _fields_=[('part1Version',U32),('part2Version',U32),('hardwareSerial',U32),
        ('hardwareName',c.c_ubyte*64),('hardwareVersion',U32),('hardwareDate',U32),
        ('hardwareInterface',U32),('firmwareName',c.c_ubyte*64),('firmwareVersion',U32),
        ('firmwareDate',U32),('vendorName',c.c_ubyte*64),('apiName',c.c_ubyte*64),
        ('apiVersion',U32),('apiDate',U32)]


def parse_api_version(raw):
    if not isinstance(raw,bytes) or len(raw)!=0x128 or c.sizeof(VersionData)!=0x128:
        raise ValueError('pt3g-version-buffer-size-invalid')
    item=VersionData.from_buffer_copy(raw); out={}
    for name,_ in VersionData._fields_:
        value=getattr(item,name)
        if name in TEXT_FIELDS:
            block=bytes(value)
            if b'\0' not in block:
                raise ValueError('pt3g-version-text-not-terminated')
            prefix=block.split(b'\0',1)[0]
            if any(v<32 or v>126 for v in prefix):
                raise ValueError('pt3g-version-text-invalid')
            out[name]=prefix.decode('ascii')
        else:
            out[name]=dict(raw=value,hex=f'{value:08X}')
            if name.endswith('Version'):
                out[name]['display']='.'.join(str((value>>shift)&255) for shift in (24,16,8,0))
    return dict(kind='pt3g-driver-api-version-metadata',moduleHandle=UNDEF,
                scope='driver-api-undefined-module',apiIdentityPresent=bool(out['apiName'] and out['apiVersion']['raw']),
                values=out,rawHex=raw.hex().upper(),**GUARDS)


def _pe_raw(blob,rva,size):
    if not isinstance(blob,bytes) or len(blob)<64 or blob[:2]!=b'MZ':
        raise ValueError('pt3g-version-source-PE-invalid')
    pe=struct.unpack_from('<I',blob,0x3c)[0]
    if pe+24>len(blob) or blob[pe:pe+4]!=b'PE\0\0':
        raise ValueError('pt3g-version-source-PE-invalid')
    machine,count=struct.unpack_from('<HH',blob,pe+4);opt_size=struct.unpack_from('<H',blob,pe+20)[0]
    opt=pe+24;section=opt+opt_size
    if (machine!=0x8664 or not 1<=count<=96 or opt_size<112 or section+40*count>len(blob)
            or struct.unpack_from('<H',blob,opt)[0]!=0x20b
            or type(rva) is not int or type(size) is not int or rva<0 or size<1):
        raise ValueError('pt3g-version-source-PE-invalid')
    found=[]
    for i in range(count):
        _,va,raw_size,offset=struct.unpack_from('<IIII',blob,section+40*i+8)
        if va<=rva and rva+size<=va+raw_size:
            at=offset+rva-va
            if offset+raw_size>len(blob) or at+size>len(blob):
                raise ValueError('pt3g-version-source-PE-range-invalid')
            found.append(blob[at:at+size])
    if len(found)!=1:
        raise ValueError('pt3g-version-source-PE-range-invalid')
    return found[0]


def audit_version_source(blob):
    if not isinstance(blob,bytes) or hashlib.sha256(blob).hexdigest()!=SUPPORT_SHA256['PDU_VCI.dll']:
        raise ValueError('pt3g-version-source-unreviewed')
    region=_pe_raw(blob,0x36b50,0x1b4)
    if hashlib.sha256(region).hexdigest()!='ea5c162a90429bccfda2aaa5f55d44a95bbe56ba663848b8234896ddf9ac642c':
        raise ValueError('pt3g-version-function-drift')
    anchors={0x36ba6:'41b828010000',0x36bcf:'ff5038',0x36bd2:'33dbe9f8000000',
        0x36bd9:'83feff',0x36c24:'c7470400000202',0x36c4a:'c7474c00000001',
        0x36c58:'c7475000010126',0x36c64:'c7475401000000',
        0x36c77:'c7879800000000000118',0x36c88:'c7879c00000000160933',
        0x36cb6:'c7872001000000000118',0x36cc0:'c7872401000000160933'}
    for rva,hx in anchors.items():
        if _pe_raw(blob,rva,len(bytes.fromhex(hx)))!=bytes.fromhex(hx):
            raise ValueError('pt3g-version-instruction-drift')
    strings={}
    for rva in (0x83e00,0x83e10,0x83e20):
        raw=_pe_raw(blob,rva,64)
        if b'\0' not in raw:
            raise ValueError('pt3g-version-source-string-invalid')
        strings[rva]=raw.split(b'\0',1)[0].decode('ascii')
    fallback=dict(part2Version=0x02020000,hardwareVersion=0x01000000,hardwareDate=0x26010100,
        hardwareInterface=1,firmwareVersion=0x18010000,firmwareDate=0x33091600,
        apiVersion=0x18010000,apiDate=0x33091600,hardwareName=strings[0x83e00],
        firmwareName=strings[0x83e10],vendorName=strings[0x83e00],apiName=strings[0x83e20])
    return dict(kind='pt3g-version-wrapper-source-audit',sourceSha256=hashlib.sha256(blob).hexdigest(),
        functionRva=0x36b50,functionBytes=len(region),functionSha256=hashlib.sha256(region).hexdigest(),
        instructionAnchors=[dict(rva=rva,hex=hx.upper()) for rva,hx in anchors.items()],
        outputZeroingBytes=0x128,delegateCallRva=0x36bcf,delegateStatusIgnored=True,
        delegateRuntimeBindingVerified=False,fallbackRuntimeBranchObserved=False,
        fixedFallbackFields=fallback,staticAuditOnly=True,**GUARDS)


class _MetadataProbe(NativeAdapter):
    def __init__(self,root):
        super().__init__(root)
        try:
            fn=self.dll.PDUGetVersion;fn.argtypes=[U32,c.POINTER(VersionData)];fn.restype=U32
        except Exception:
            if self.close()['errors']:
                raise ElmError('pt3g-api-version-probe-init-cleanup-failed') from None
            raise

    @_serialized_native
    def call(self,name,*args):
        self._require_open()
        if name=='PDUGetVersion':
            if len(args)!=2 or type(args[0]) is not int or args[0]!=UNDEF:
                raise ElmError('pt3g-api-version-only-undefined-module')
            _check(self.dll.PDUGetVersion(*args),name)
        elif name in ('PDUConstruct','PDUDestruct'):
            super().call(name,*args)
        else:
            raise ElmError('pt3g-api-version-probe-only-api')


def probe_driver_version(root=None):
    root=Path(root if root is not None else vendor_root())
    source=audit_version_source((root/'PDU_VCI.dll').read_bytes())
    native=_MetadataProbe(root);data=VersionData();metadata=None;error=None;api_succeeded=False
    try:
        try:
            native.call('PDUGetVersion',UNDEF,c.byref(data));api_succeeded=True
            metadata=parse_api_version(c.string_at(c.byref(data),c.sizeof(data)))
            if not metadata['apiIdentityPresent']:
                error='pt3g-api-version-metadata-incomplete'
        except (ElmError,ValueError) as problem:
            error=str(problem)
    finally:
        cleanup=native.close()
    comparisons=[]
    if metadata is not None:
        for name,value in source['fixedFallbackFields'].items():
            reported=metadata['values'][name]
            comparisons.append(dict(name=name,sourceValue=value,reportedValue=reported if name in TEXT_FIELDS else reported['raw'],
                equal=(reported if name in TEXT_FIELDS else reported['raw'])==value))
    return dict(ok=api_succeeded and metadata is not None and metadata['apiIdentityPresent'] and not cleanup['errors'],
        kind='pt3g-driver-api-version-probe',probeCompleted=True,apiSucceeded=api_succeeded,error=error,
        metadata=metadata,rawBufferHex=c.string_at(c.byref(data),c.sizeof(data)).hex().upper(),
        pinnedFallbackFieldComparisons=comparisons,
        matchesPinnedFallbackFields=bool(comparisons) and all(row['equal'] for row in comparisons),
        fallbackRuntimeBranchObserved=False,sourceAudit=source,configuration=native.configuration,
        cleanup=cleanup,driverLoaded=True,**GUARDS)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',type=Path,required=True)
    parser.add_argument('--probe-api-version',action='store_true')
    args=parser.parse_args()
    report=probe_driver_version(args.driver_root) if args.probe_api_version else audit_version_source((args.driver_root/'PDU_VCI.dll').read_bytes())
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0 if report.get('ok',True) else 2


if __name__=='__main__':raise SystemExit(main())
