"""Observe pinned PT3G callback bindings in this process; no vehicle I/O.

The sole delegated call is PDUGetVersion(UNDEF), after checking the loaded
image, export RVA, mapped instruction bytes and stable wrapper state. No
module, CAN link, IOCTL or communication primitive is called.
"""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct

from .elm import ElmError
from .pt3g import SUPPORT_SHA256
from .pt3g_version import GUARDS, VersionData, _MetadataProbe, _pe_raw, audit_version_source, parse_api_version
from .vnci import PTR, U32, UNDEF

CONTEXT_RVA = 0x92370
TABLE_OFFSET = 0x218
HANDLE_OFFSET = 0x240
TABLE_BYTES = 0xf0
VERSION_SLOT = 0x38
SLOT_NAMES = (
    'PDUConstruct', 'PDUDestruct', 'PDUModuleConnect', 'PDUModuleDisconnect',
    'PDUGetTimestamp', 'PDUIoCtl', 'PDUGetVersion', 'PDUGetStatus',
    'PDUGetLastError', 'PDUGetResourceStatus', 'PDUCreateComLogicalLink',
    'PDUDestroyComLogicalLink', 'PDUConnect', 'PDUDisconnect', 'PDULockResource',
    'PDUUnlockResource', 'PDUGetComParam', 'PDUSetComParam', 'PDUStartComPrimitive',
    'PDUCancelComPrimitive', 'PDUGetEventItem', 'PDUDestroyItem',
    'PDURegisterEventCallback', 'PDUGetObjectId', 'PDUGetModuleIds',
    'PDUGetResourceIds', 'PDUGetConflictingResources', 'PDUGetUniqueRespIdTable',
    'PDUSetUniqueRespIdTable',
)
STRING_RVAS = (0x82fa0,0x82fb0,0x82fc0,0x82fd8,0x82ff0,0x83000,0x83010,
    0x83020,0x83030,0x83040,0x83058,0x83070,0x83090,0x830a0,0x830b0,
    0x830c0,0x830d8,0x830e8,0x830f8,0x83110,0x83128,0x83138,0x83148,
    0x83168,0x83178,0x83188,0x831a0,0x831c0,0x831d8)
REGIONS = (
    ('wrapperLoader','PDU_VCI.dll',0x2e480,0x335,'17642106652c2ec61d3dc4e798681c6fd227e301dddc077b89ce9dc5c8c7a7cd'),
    ('delegateVersion','pdu2.dll',0x121000,0x161,'dae99188a7f4725af15b4af7a0a86024d8c88d320c4589c621994b43021fb06a'),
    ('delegateVersionInner','pdu2.dll',0x94b10,0x137,'31a8cec43bbd09e2b749b0b7280dfeecd7cb37781abbce99dbb91abc49eae40b'),
)


def _exports(blob):
    # The caller pins the whole file first. Still bound every PE-derived read.
    _pe_raw(blob,0x121000,1)
    pe=struct.unpack_from('<I',blob,0x3c)[0];opt=pe+24
    if struct.unpack_from('<H',blob,pe+20)[0]<120:
        raise ValueError('pt3g-binding-export-header-invalid')
    rva,size=struct.unpack_from('<II',blob,opt+112)
    fields=struct.unpack('<IIHHIIIIIII',_pe_raw(blob,rva,40))
    count,names,functions,name_array,ordinals=fields[6:11]
    if not 1<=count<=128 or names!=count or size<40:
        raise ValueError('pt3g-binding-export-count-invalid')
    addresses=struct.unpack('<'+'I'*count,_pe_raw(blob,functions,4*count))
    strings=struct.unpack('<'+'I'*names,_pe_raw(blob,name_array,4*names))
    indexes=struct.unpack('<'+'H'*names,_pe_raw(blob,ordinals,2*names))
    out={}
    for name_rva,index in zip(strings,indexes):
        raw=_pe_raw(blob,name_rva,64)
        if b'\0' not in raw or index>=count:
            raise ValueError('pt3g-binding-export-invalid')
        name=raw.split(b'\0',1)[0].decode('ascii');target=addresses[index]
        if not name or name in out or target==0 or rva<=target<rva+size:
            raise ValueError('pt3g-binding-export-invalid')
        _pe_raw(blob,target,1);out[name]=target
    return out


def audit_binding_source(wrapper,delegate):
    files={'PDU_VCI.dll':wrapper,'pdu2.dll':delegate}
    for name,blob in files.items():
        if not isinstance(blob,bytes) or hashlib.sha256(blob).hexdigest()!=SUPPORT_SHA256[name]:
            raise ValueError('pt3g-binding-source-unreviewed:'+name)
    version=audit_version_source(wrapper);exports=_exports(delegate)
    if set(exports)!=set(SLOT_NAMES) or exports['PDUGetVersion']!=0x121000:
        raise ValueError('pt3g-binding-exports-drift')
    regions=[]
    for label,file,rva,size,expected in REGIONS:
        actual=hashlib.sha256(_pe_raw(files[file],rva,size)).hexdigest()
        if actual!=expected:raise ValueError('pt3g-binding-function-drift:'+label)
        regions.append(dict(name=label,file=file,rva=rva,bytes=size,sha256=actual))
    bindings=[]
    for i,(name,rva) in enumerate(zip(SLOT_NAMES,STRING_RVAS),1):
        if _pe_raw(wrapper,rva,len(name)+1)!=name.encode('ascii')+b'\0':
            raise ValueError('pt3g-binding-name-drift')
        bindings.append(dict(name=name,slot=i*8,stringRva=rva,exportRva=exports[name]))
    anchors={
        0x36b87:'488b1de2b70500', # wrapper context global
        0x36bb4:'8b8b40020000', # cached module handle
        0x36bbe:'488b8318020000', # external table
        0x36bcf:'ff5038', # GetVersion callback
        0x2f805:'b9f0000000', # table allocation size
        0x2f8fd:'49898618020000', # initialized table retained in context
    }
    for rva,hx in anchors.items():
        if _pe_raw(wrapper,rva,len(bytes.fromhex(hx)))!=bytes.fromhex(hx):
            raise ValueError('pt3g-binding-context-drift')
    return dict(kind='pt3g-driver-binding-source',files={n:SUPPORT_SHA256[n] for n in files},
        regions=regions,bindings=bindings,contextRva=CONTEXT_RVA,tableOffset=TABLE_OFFSET,
        cachedModuleHandleOffset=HANDLE_OFFSET,tableBytes=TABLE_BYTES,
        versionSlot=VERSION_SLOT,wrapperVersion=version,
        delegateVersionErrorBranches=[dict(code=0x20,name='PDU_ERR_PDUAPI_NOT_CONSTRUCTED'),
            dict(code=0x50,name='PDU_ERR_INVALID_PARAMETERS'),dict(code=0x60,name='PDU_ERR_INVALID_HANDLE')],
        delegateRuntimeBindingVerified=False,staticAuditOnly=True,**GUARDS)


class _MemoryInfo(c.Structure):
    _fields_=[('base',PTR),('allocation',PTR),('allocationProtect',U32),
        ('partition',c.c_uint16),('size',c.c_size_t),('state',U32),('protect',U32),('type',U32)]


def _readable_region(address,size,info,*,image=None,executable=False):
    if (type(address) is not int or type(size) is not int or not 0<address<2**64
            or not 1<=size<=4096 or address+size>2**64 or not info.base or not info.allocation
            or info.state!=0x1000 or info.protect & (0x100|0x200)
            or info.protect & 0xff not in (2,4,8,0x20,0x40,0x80)
            or not info.base<=address<address+size<=info.base+info.size
            or image is not None and (info.type!=0x1000000 or info.allocation!=image)
            or executable and info.protect & 0xff not in (0x20,0x40,0x80)):
        raise ElmError('pt3g-binding-memory-range-not-qualified')


class _OwnedMemory:
    """ReadProcessMemory only on the current process pseudo handle."""
    def __init__(self):
        self.kernel=c.WinDLL('kernel32',use_last_error=True)
        api=self.kernel
        api.VirtualQuery.argtypes=[PTR,c.POINTER(_MemoryInfo),c.c_size_t];api.VirtualQuery.restype=c.c_size_t
        api.ReadProcessMemory.argtypes=[PTR,PTR,PTR,c.c_size_t,c.POINTER(c.c_size_t)];api.ReadProcessMemory.restype=c.c_int
        api.GetCurrentProcess.argtypes=[];api.GetCurrentProcess.restype=PTR
        api.GetModuleFileNameW.argtypes=[PTR,c.c_wchar_p,U32];api.GetModuleFileNameW.restype=U32
        self.process=api.GetCurrentProcess()

    def info(self,address):
        item=_MemoryInfo()
        if self.kernel.VirtualQuery(address,c.byref(item),c.sizeof(item))!=c.sizeof(item):
            raise ElmError('pt3g-binding-memory-query-failed')
        return item

    def read(self,address,size,**constraints):
        _readable_region(address,size,self.info(address),**constraints)
        target=c.create_string_buffer(size);count=c.c_size_t()
        if (not self.kernel.ReadProcessMemory(self.process,address,target,size,c.byref(count))
                or count.value!=size):
            raise ElmError('pt3g-binding-memory-read-failed')
        return target.raw

    def verify_image(self,base,path):
        item=self.info(base)
        _readable_region(base,2,item,image=base)
        text=c.create_unicode_buffer(32768);count=self.kernel.GetModuleFileNameW(base,text,len(text))
        if not 0<count<len(text) or Path(text.value).resolve()!=path.resolve():
            raise ElmError('pt3g-binding-loaded-image-path-mismatch')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=SUPPORT_SHA256[path.name]:
            raise ElmError('pt3g-binding-loaded-image-source-mismatch')


def _observe(native,memory,root,source):
    base=native.dll._handle;memory.verify_image(base,root/'PDU_VCI.dll')
    wrapper=(root/'PDU_VCI.dll').read_bytes()
    if memory.read(base+0x36b50,0x1b4,image=base,executable=True)!=_pe_raw(wrapper,0x36b50,0x1b4):
        raise ElmError('pt3g-binding-wrapper-memory-drift')
    context=struct.unpack('<Q',memory.read(base+CONTEXT_RVA,8,image=base))[0]
    if not context:raise ElmError('pt3g-binding-context-missing')
    table=struct.unpack('<Q',memory.read(context+TABLE_OFFSET,8))[0]
    handle=struct.unpack('<I',memory.read(context+HANDLE_OFFSET,4))[0]
    if not table:raise ElmError('pt3g-binding-table-missing')
    raw=memory.read(table,TABLE_BYTES);values=struct.unpack('<30Q',raw);owner=values[0]
    memory.verify_image(owner,root/'pdu2.dll')
    rows=[]
    for row,address in zip(source['bindings'],values[1:]):
        if address!=owner+row['exportRva']:
            raise ElmError('pt3g-binding-callback-mismatch:'+row['name'])
        _readable_region(address,1,memory.info(address),image=owner,executable=True)
        rows.append(row|dict(loadedExportMatches=True))
    delegate=(root/'pdu2.dll').read_bytes()
    for label,file,rva,size,_ in REGIONS:
        if file=='pdu2.dll' and memory.read(owner+rva,size,image=owner,executable=True)!=_pe_raw(delegate,rva,size):
            raise ElmError('pt3g-binding-delegate-memory-drift:'+label)
    # Reject changes while following the chain, before any delegated API call.
    if (memory.read(base+CONTEXT_RVA,8,image=base)!=struct.pack('<Q',context)
            or memory.read(context+TABLE_OFFSET,8)!=struct.pack('<Q',table)
            or memory.read(context+HANDLE_OFFSET,4)!=struct.pack('<I',handle)
            or memory.read(table,TABLE_BYTES)!=raw):
        raise ElmError('pt3g-binding-state-changed')
    return dict(cachedModuleHandle=handle,versionQueryHandle=UNDEF,
        versionDelegateConditionSatisfied=handle!=UNDEF,bindings=rows,delegateFile='pdu2.dll',
        delegateSha256=SUPPORT_SHA256['pdu2.dll'],delegateVersionExportRva=0x121000,
        delegateRuntimeBindingVerified=True,loadedInstructionBytesVerified=True),values[VERSION_SLOT//8]


def probe_driver_binding(root):
    root=Path(root).resolve()
    source=audit_binding_source((root/'PDU_VCI.dll').read_bytes(),(root/'pdu2.dll').read_bytes())
    native=_MetadataProbe(root);observed=None;wrapper_result=None;delegate_result=None;error=None
    try:
        try:
            memory=_OwnedMemory();observed,target=_observe(native,memory,root,source)
            data=VersionData();native.call('PDUGetVersion',UNDEF,c.byref(data))
            wrapper_result=dict(status=0,metadata=parse_api_version(bytes(data)))
            again,second_target=_observe(native,memory,root,source)
            if again!=observed or target!=second_target:raise ElmError('pt3g-binding-state-changed')
            # One explicitly named driver metadata call. Never invoke other slots.
            fn=c.WINFUNCTYPE(U32,U32,c.POINTER(VersionData))(target)
            data=VersionData();status=int(fn(UNDEF,c.byref(data)))
            delegate_result=dict(status=status,metadata=parse_api_version(bytes(data)))
            after,third_target=_observe(native,memory,root,source)
            if after!=observed or target!=third_target:raise ElmError('pt3g-binding-state-changed')
        except (ElmError,ValueError,OSError) as problem:
            error=str(problem)
    finally:
        cleanup=native.close()
    return dict(ok=error is None and not cleanup['errors'],kind='pt3g-owned-process-binding-probe',
        scope='driver-api-undefined-module',sourceAudit=source,observation=observed,
        wrapperVersion=wrapper_result,delegateVersion=delegate_result,error=error,
        sequentialVersionStatusesDiffer=bool(wrapper_result is not None and delegate_result is not None
            and wrapper_result['status']==0 and delegate_result['status']!=0),
        delegateReturnInsideWrapperObserved=False,
        versionMetadataQualified=False,configuration=native.configuration,cleanup=cleanup,**GUARDS)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',type=Path,required=True)
    parser.add_argument('--probe-bindings',action='store_true')
    args=parser.parse_args();root=args.driver_root
    report=probe_driver_binding(root) if args.probe_bindings else audit_binding_source(
        (root/'PDU_VCI.dll').read_bytes(),(root/'pdu2.dll').read_bytes())
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0 if report.get('ok',True) else 2


if __name__=='__main__':raise SystemExit(main())
