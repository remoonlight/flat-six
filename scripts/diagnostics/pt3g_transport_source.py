"""Pinned PT3G CAN dispatch and current-process metadata observation.

No physical module, logical link, parameter setting or primitive is invoked.
Loaded tables and static dispatch are evidence, never vehicle qualification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct

from .elm import ElmError
from .pt3g import NativeAdapter, SUPPORT_SHA256, vendor_root
from .pt3g_binding import _OwnedMemory, _readable_region
from .pt3g_read_source import read_source_contract
from .pt3g_version import _pe_raw

GUARDS = dict(moduleConnected=False, logicalLinkCreated=False, parametersApplied=False,
              ecuRequestsSent=0, hardwareIdentityQualified=False, firmwareQualified=False,
              vehicleTransportQualified=False, physicalSilenceVerified=False, executionEnabled=False)
CONTEXT_RVA=0x92370
PASSTHRU_OFFSET=0x238
PROTOCOL_OFFSET=0x1d0
TABLE_BYTES=0xc8
CAN_VTABLE_RVA=0x82860
BINDING_NAMES=('Construct', 'Destruct', 'GetDevice', 'Connect', 'Disconnect', 'SetReadMsgFilter', 'OffReadMsgFilter', 'AddCopTxData', 'CancelCopTxData', 'ReadMsg', 'SetConfig', 'SetCanfdConfig', 'SetIOCTL', 'SetVoltage', 'SetTesterPresent', 'OffTesterPresent', 'GetJ2534ReqTime', 'SetDoipPullup', 'GetDoipMod', 'ConnectDoip', 'DoipIsConnect', 'UpdateDoipUrid', 'DesCanKline', 'DesDoip')
STRING_RVAS=(537368, 537384, 537400, 537416, 537424, 537440, 537464, 537488, 537504, 537520, 537528, 537544, 537560, 537576, 537592, 537616, 537640, 537656, 537672, 537688, 537704, 537720, 537736, 537752)
REGIONS=(('passthruLoader', 192784, 674, '1e7fe7f383237059f9e157fd2b76e1f423469696390a1dcd1ca0566d408c3a4b'), ('protocolNames', 175008, 2656, '348849457e14dcd2b31c375d21d50f03e472df45b69783e230bda25cbb76cdd8'), ('protocolMap', 20016, 189, 'd918bbce0686d528067084d4a9c8a5bfe1b8f672254402c13380a44e806493f6'), ('protocolIndex', 177680, 785, 'ca1dcc10f1fc523bf264dc113440cf9f80f692df169a17d1b43b2370bde05d50'), ('factory', 201968, 1280, '071efcb517bef38bbc0cec88b428522e187bb0f5af7cb59a8c122ec40373833d'), ('create', 208032, 1376, 'd6d93afeeeae071ef7914cec63be907b70bfc25059941c0bec71bc391060bcc8'), ('connect', 209744, 621, '7207d12203a073c89745000ce239224ef5bb3759374c27f8c38130513fad355d'), ('disconnect', 210368, 769, '4da0661cf8291b52a70a73b2ddc26217d0bc3c28410eb04f7f2c1251bc98f3e1'), ('start', 215424, 1000, 'f7cab96bc214f52f31f896b950ae5256682715c8decd20b37b79a73462fb1a7a'), ('sendrecvDispatch', 216042, 158, 'a50da439cfbebb5c751d8c38d193c1c003786978eebc350405cbcd2ef1a25643'), ('primitiveJumpTable', 217964, 24, '29d19b2e308523e530ef39d624725106a6e932845f1ccdb0f51a20383a0841c9'), ('cancel', 227552, 463, 'ab144baf67b124775cf0d1e382821d1bda881f17a7ab7cf76a07fa3f536f2111'), ('event', 228016, 300, '289d7411a43f789e59b38afcdc521dcf00688f66c0ad0fb3c348b330dfee3c72'), ('canCtor', 100912, 80, 'a9e41cc7e54f1524627312be9898c5ef2ebb6fbcadef61ed8af198492c42417d'), ('canConnect', 115488, 228, '00801d3d7e320de09cf0c5b080ef087ce44fa09fbc17b4b6bf28d1fc791ea69f'), ('canDisconnect', 115728, 559, '825baa10ff3503f112996592e532f4a6d66eeea6074b4f22a0d06dc086d4cc14'), ('sendrecvBase', 10784, 803, '28fbab265dda9554aabf848f2eaaa40c86facd984ed32dc0b1a123e7956f9a43'), ('cancelBase', 73568, 555, '303d067ca6a5905253b58cf46ba9ecaf189c9f243e56c457f34fd03d377b2992'), ('contextPassthruAllocation', 199109, 56, '5413e13f814450c441b5f9d47a432d0d9917d7dfa5c16a7aa06497088c38b799'), ('protocolArraySetup', 199770, 28, '28b45c251d30b04d7d1eb1c3266c01680d12c0f32b619b1e18b18f31a4693491'))
PROTOCOL_STRINGS=(('DS2_ON_DS2_UART', 534992), ('FIAT_on_ISO_9141', 535008), ('HONDA_92HM_on_KW_UART', 535032), ('HONDA_HM99_AND_HM99B_ON_92HM_KW_UART', 535056), ('ISO_11898_RAW', 535096), ('ISO_14230_3_on_ISO_14230_2', 535112), ('ISO_14230_3_on_ISO_15765_2', 535144), ('ISO_15031_5_on_ISO_14230_4', 535176), ('ISO_15031_5_on_ISO_15765_4', 535208), ('ISO_15031_5_on_ISO_9141_2', 535240), ('ISO_15031_5_on_SAE_J1850_PWM', 535272), ('ISO_15031_5_on_SAE_J1850_VPW', 535304), ('ISO_15765_3_on_ISO_15765_2', 535336), ('ISO_OBD_on_ISO_15765_4', 535368), ('ISO_OBD_on_K_Line', 535392), ('ISO_OBD_on_SAE_J1850', 535416), ('ISO_OBD_on_SAE_J1939_73', 535440), ('KW1281_on_KW_UART', 535464), ('KW309_on_KW_UART', 535488), ('KW71_on_KW_UART', 535512), ('KW81_on_KW_UART', 535528), ('KW82_on_KW_UART', 535544), ('MSP_AGS_on_AGS_TP', 535560), ('MSP_ATC_on_ATC_TP', 535584), ('MSP_EST42_on_EST42_TP', 535608), ('MSP_JED677_on_JED677_TP', 535632), ('MSP_MT11_on_MT11_TP', 535656), ('SAE_J1587_on_SAE_J1708', 535680), ('SAE_J1939_73_on_SAE_J1939_21', 535704), ('SAE_J2190_on_ISO_14230_2', 535736), ('SAE_J2190_on_ISO_15765_2', 535768), ('SAE_J2190_on_ISO_9141', 535800), ('SAE_J2190_on_ISO_9141_2', 535824), ('SAE_J2190_on_SAE_J1850_PWM', 535848), ('SAE_J2190_on_SAE_J1850_VPW', 535880), ('SAE_J2610_on_SAE_J2610_SCI', 535912), ('SDL_on_SDL_UART', 535944), ('SUBARUSS_on_SUBARUSS_UART', 535960), ('UART_LOGGER_on_LOGGER_UART', 535992), ('XDE_5024_on_XDE_5024_UART', 536024), ('ISO_14229_5_on_ISO_13400_2', 536056), ('MONITOR', 536088), ('ISO_13400', 536096), ('BENZ_CYCLERX', 536112), ('BENZ_KWFB', 536128), ('BENZ_MBISO', 536144), ('MSP_VW2000LP_on_TP16', 536160), ('MSP_KW1281_on_TP16', 536184), ('MSP_VW2000LP_on_TP20', 536208), ('MSP_KW1281_on_ISO_9141_2', 536232), ('MSP_VW2000LP_on_ISO_14230_2', 536264), ('MSP_RN_DDL1_ON_DDL_UART', 536296), ('VW2000_on_TP20', 536320), ('MSP_VW2000LP_on_VW_14230_2', 536336), ('MSP_KW71_UART', 536368))
CAN_VTABLE_TARGETS=(100832, 9568, 120320, 122336, 116288, 116512, 9584, 115488, 115728, 4704, 73568, 10784, 123456, 127936, 139056)
CAN_ROUTES=(('dme', 6, 'ISO_14230_3_on_ISO_15765_2'), ('gateway', 12, 'ISO_15765_3_on_ISO_15765_2'), ('standard-engine', 13, 'ISO_OBD_on_ISO_15765_4'))

def _exports(blob):
    if not isinstance(blob,bytes) or len(blob)<64:
        raise ValueError('pt3g-transport-export-PE-invalid')
    pe=struct.unpack_from('<I',blob,0x3c)[0]
    if pe+24>len(blob) or blob[pe:pe+4]!=b'PE\0\0':
        raise ValueError('pt3g-transport-export-PE-invalid')
    opt=pe+24
    if struct.unpack_from('<H',blob,pe+20)[0]<120:
        raise ValueError('pt3g-transport-export-header-invalid')
    rva,size=struct.unpack_from('<II',blob,opt+112)
    fields=struct.unpack('<IIHHIIIIIII',_pe_raw(blob,rva,40))
    count,names,functions,name_array,ordinals=fields[6:11]
    if not 1<=count<=64 or names!=count or size<40:
        raise ValueError('pt3g-transport-export-count-invalid')
    addresses=struct.unpack('<'+'I'*count,_pe_raw(blob,functions,4*count))
    strings=struct.unpack('<'+'I'*names,_pe_raw(blob,name_array,4*names))
    indexes=struct.unpack('<'+'H'*names,_pe_raw(blob,ordinals,2*names))
    out={}
    for name_rva,index in zip(strings,indexes):
        raw=_pe_raw(blob,name_rva,64)
        if b'\0' not in raw or index>=count:
            raise ValueError('pt3g-transport-export-invalid')
        name=raw.split(b'\0',1)[0].decode('ascii');target=addresses[index]
        if not name or name in out or target==0 or rva<=target<rva+size:
            raise ValueError('pt3g-transport-export-invalid')
        _pe_raw(blob,target,32);out[name]=target
    return out


def audit_transport_source(root):
    root=Path(root);files={}
    for name in ('PDU_VCI.dll','PassthruManage.dll'):
        raw=(root/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=SUPPORT_SHA256[name]:
            raise ValueError('pt3g-transport-source-unreviewed:'+name)
        files[name]=raw
    w=files['PDU_VCI.dll'];contract=read_source_contract(root/'MDF_VCI.xml')
    regions=[]
    for name,rva,size,expected in REGIONS:
        sha=hashlib.sha256(_pe_raw(w,rva,size)).hexdigest()
        if sha!=expected:raise ValueError('pt3g-transport-instructions-drift:'+name)
        regions.append(dict(name=name,rva=rva,bytes=size,sha256=sha))
    exports=_exports(files['PassthruManage.dll'])
    if set(exports)!=set(BINDING_NAMES):raise ValueError('pt3g-transport-export-set-drift')
    bindings=[]
    for i,(name,rva) in enumerate(zip(BINDING_NAMES,STRING_RVAS),1):
        if _pe_raw(w,rva,len(name)+1)!=name.encode('ascii')+b'\0':
            raise ValueError('pt3g-transport-loader-name-drift')
        bindings.append(dict(name=name,slot=8*i,stringRva=rva,exportRva=exports[name],
            prologueBytes=32,prologueSha256=hashlib.sha256(_pe_raw(files['PassthruManage.dll'],exports[name],32)).hexdigest()))
    for name,rva in PROTOCOL_STRINGS:
        if _pe_raw(w,rva,len(name)+1)!=name.encode('ascii')+b'\0':
            raise ValueError('pt3g-transport-protocol-name-drift')
    targets=tuple(x-0x180000000 for x in struct.unpack('<15Q',_pe_raw(w,CAN_VTABLE_RVA,120)))
    if targets!=CAN_VTABLE_TARGETS:raise ValueError('pt3g-transport-CAN-vtable-drift')
    # Decoded original index comparisons 6, 12 and 13 each jump to return 5.
    # The factory switch for 5 selects 0x31680 and constructor 0x18a30.
    routes=[]
    for key,index,name in CAN_ROUTES:
        matches=[q for q in contract['objectQueries'] if q['objectType']==0x8021 and q['name']==name]
        if len(matches)!=1 or PROTOCOL_STRINGS[index][0]!=name:
            raise ValueError('pt3g-transport-protocol-MDF-mismatch')
        routes.append(dict(route=key,protocol=name,index=index,sourceId=matches[0]['sourceId'],
            factoryClass=5,constructorRva=0x18a30,vtableRva=CAN_VTABLE_RVA))
    primitive_targets=struct.unpack('<6I',_pe_raw(w,0x3536c,24))
    if primitive_targets[3]!=0x34bea or struct.unpack('<I',_pe_raw(w,0x319f0+3*4,4))[0]!=0x31680:
        raise ValueError('pt3g-transport-dispatch-drift')
    return dict(kind='pt3g-CAN-transport-source-audit',staticOnly=True,
        files={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},mdfSha256=contract['mdfSha256'],
        regions=regions,bindings=bindings,protocolCount=55,routes=routes,
        canVtableTargets=list(targets),sendrecvPrimitive=0x8004,sendrecvTargetRva=0x34bea,
        virtualSlots=dict(connect=0x38,disconnect=0x40,cancel=0x50,sendrecv=0x58),
        canDisconnectLowerStatusPropagated=False,cancelLowerStatusPropagated=False,
        nativeProtocolObjectObserved=False,**GUARDS)


def _snapshot(memory,base,root,source):
    memory.verify_image(base,root/'PDU_VCI.dll')
    context_raw=memory.read(base+CONTEXT_RVA,8,image=base)
    context=struct.unpack('<Q',context_raw)[0]
    if not context:raise ElmError('pt3g-transport-context-missing')
    table_pointer=memory.read(context+PASSTHRU_OFFSET,8)
    table=struct.unpack('<Q',table_pointer)[0]
    if not table:raise ElmError('pt3g-transport-passthru-table-missing')
    raw=memory.read(table,TABLE_BYTES);values=struct.unpack('<25Q',raw);owner=values[0]
    memory.verify_image(owner,root/'PassthruManage.dll')
    for row,address in zip(source['bindings'],values[1:]):
        if address!=owner+row['exportRva']:raise ElmError('pt3g-transport-binding-mismatch:'+row['name'])
        actual=memory.read(address,row['prologueBytes'],image=owner,executable=True)
        if hashlib.sha256(actual).hexdigest()!=row['prologueSha256']:
            raise ElmError('pt3g-transport-loaded-prologue-drift:'+row['name'])
    header=memory.read(context+PROTOCOL_OFFSET,24);begin,end,capacity=struct.unpack('<3Q',header)
    if (not begin or begin%4 or end-begin!=55*4 or not end<=capacity<=begin+128*4):
        raise ElmError('pt3g-transport-protocol-vector-invalid')
    protocols=memory.read(begin,55*4);ids=struct.unpack('<55I',protocols)
    for row in source['routes']:
        if ids[row['index']]!=row['sourceId']:raise ElmError('pt3g-transport-protocol-id-mismatch:'+row['route'])
    vtable_raw=memory.read(base+CAN_VTABLE_RVA,120,image=base)
    vtable=struct.unpack('<15Q',vtable_raw)
    if tuple(address-base for address in vtable)!=tuple(source['canVtableTargets']):
        raise ElmError('pt3g-transport-loaded-vtable-drift')
    for address in vtable:_readable_region(address,1,memory.info(address),image=base,executable=True)
    for row in source['regions']:
        # The primitive jump table is image data, all other intervals are code.
        actual=memory.read(base+row['rva'],row['bytes'],image=base,executable=row['name']!='primitiveJumpTable')
        if hashlib.sha256(actual).hexdigest()!=row['sha256']:
            raise ElmError('pt3g-transport-loaded-instructions-drift:'+row['name'])
    if (memory.read(base+CONTEXT_RVA,8,image=base)!=context_raw
            or memory.read(context+PASSTHRU_OFFSET,8)!=table_pointer
            or memory.read(table,TABLE_BYTES)!=raw
            or memory.read(context+PROTOCOL_OFFSET,24)!=header
            or memory.read(begin,55*4)!=protocols
            or memory.read(base+CAN_VTABLE_RVA,120,image=base)!=vtable_raw):
        raise ElmError('pt3g-transport-state-changed')
    return dict(bindingsVerified=24,protocolIdsVerified=3,loadedStaticCanVtableVerified=True,
        loadedInstructionsVerified=True,stateStable=True,nativeProtocolObjectObserved=False)


class _TransportMetadataProbe(NativeAdapter):
    def call(self,name,*args):
        if name not in ('PDUConstruct','PDUDestruct'):
            raise ElmError('pt3g-transport-metadata-only-api')
        super().call(name,*args)


def probe_transport_metadata(root=None):
    root=Path(root if root is not None else vendor_root());source=audit_transport_source(root)
    native=_TransportMetadataProbe(root);observed=None;error=None
    try:observed=_snapshot(_OwnedMemory(),native.dll._handle,root,source)
    except Exception as problem:error=str(problem) or type(problem).__name__
    finally:cleanup=native.close()
    return dict(ok=error is None and not cleanup['errors'],kind='pt3g-CAN-transport-metadata-probe',
        source=source,observation=observed,error=error,cleanup=cleanup,
        directPassthruCalls=0,**GUARDS)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver-root',type=Path,default=vendor_root())
    parser.add_argument('--probe-runtime',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.output and args.output.exists():raise ValueError('pt3g-transport-output-already-exists')
    report=probe_transport_metadata(args.driver_root) if args.probe_runtime else audit_transport_source(args.driver_root)
    content=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        with args.output.open('x',encoding='utf8') as out:out.write(content)
    else:print(content,end='')
    return 0 if report.get('ok',True) else 2


if __name__=='__main__':raise SystemExit(main())
