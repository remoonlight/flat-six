"""Pinned Android reference-library binding audits, without loading native code.

These ELF files are X431 research sources, not Windows diagnostic-head drivers.
Explicit loader events describe offline observations; they never qualify the
actual process library, function pointers, heap, device or vehicle.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct

from .x431_version_response import FILE_SHA256, VERSION_GUARDS

STD_SHA256 = 'f92baf83d6b83aed87c0066a2b114c4c3864ec24d27aeb17b9c8896e1a840c20'
SYMBOL_NAMES = ('FromIdGetBinaryString', 'SearchIdOpenFile', 'SearchIdCloseFile',
    'GetTextFromTextLib', 'ggp_Open', 'GetPrivateProFileInt', 'IdSearch',
    'SetFixTextIdBuffer', 'DelIdBuffer', 'ggp_Close', 'GetGAGOffsetFromGGP',
    'GetGAGSizeFromGGP', 'fseekGGP', 'GetPrivateProFileString',
    'SetPrivateProFileString', 'SetPrivateProFileInt')
NAME_LOAD_SITES = (0x258ce, 0x258e6, 0x258fe, 0x25916, 0x2592e, 0x25946,
    0x2595e, 0x25974, 0x2598a, 0x259a0, 0x259b6, 0x259cc, 0x259e2, 0x259f8, 0x25a0e, 0x25a24)
REGIONS = (
    ('OpenStdDll', 0x25838, 0x2587c, '6026f0feb420379a144917660f2d0d28dd05d6990a8c3c2ada0c666a1643f254'),
    ('CloseStdDll', 0x2587c, 0x258a8, '6557b028ae7907baa8643618da0be98fda9abf19a651b789bda749f13eabc772'),
    ('OpenSearchIDDLL', 0x258a8, 0x25b48, '3b57394dd89be2e9b95109486c8fcd4ba309586e25d20cd59eb9c277c1cf6cfb'))


def loader_source_metadata():
    return {'fileSourceSha256': FILE_SHA256, 'stdSourceSha256': STD_SHA256,
        'regions': [dict(name=n, startVa=a, endVa=z, sha256=h) for n,a,z,h in REGIONS],
        'requiredSymbolNames': list(SYMBOL_NAMES), 'requiredSymbolCount': len(SYMBOL_NAMES),
        'stdLibraryNameCandidates': ['libSTD.so', './libSTD.so'], 'sourceDlopenFlags': 1,
        'searchHandleStoreBeforeSymbolsVa': 0x258c8, 'cachedSearchHandleBranchVa': 0x258b6,
        'cachedSearchHandleBypassReturnCandidate': 0,
        'cachedSearchHandleBypassRechecksSymbols': False,
        'sourceHandleClearedOnSymbolFailure': False,
        'sourceCloseChecksDlcloseReturn': False,
        'sourceCloseClearsStdHandle': True, 'sourceCloseClearsSearchHandle': False,
        'sourceCloseClearsFunctionSlots': False, 'lookupCallerEntry': 0x17748,
        'callerChecksLoaderReturn': False,
        'runtimeLibraryFingerprintCheckedBySource': False,
        'actualDynamicLookupBindingQualified': False, 'nativeCodeExecuted': False}


class _Elf:
    """Small reader used only after exact retained-file hashes are checked."""
    def __init__(self, blob):
        self.b = blob
        if blob[:7] != b'\x7fELF\x01\x01\x01':
            raise ValueError('native-binding-elf-format-unreviewed')
        po = struct.unpack_from('<I', blob, 28)[0]
        ps,pn = struct.unpack_from('<HH', blob, 42)
        self.ph = [struct.unpack_from('<8I', blob, po+i*ps) for i in range(pn)]
        so = struct.unpack_from('<I', blob, 32)[0]
        ss,sn,names = struct.unpack_from('<HHH', blob, 46)
        sh = [struct.unpack_from('<10I', blob, so+i*ss) for i in range(sn)]
        n = sh[names]; strings = blob[n[4]:n[4]+n[5]]
        sections = {strings[s[0]:].split(b'\0',1)[0].decode():s for s in sh}
        ds = sections['.dynsym']; n = sh[ds[6]]; strings = blob[n[4]:n[4]+n[5]]
        self.symbols = []
        for at in range(ds[4], ds[4]+ds[5], ds[9]):
            no,va,size,info,other,section = struct.unpack_from('<IIIBBH', blob, at)
            self.symbols.append(dict(name=strings[no:].split(b'\0',1)[0].decode(),
                va=va, bytes=size, type=info&15, section=section))
        self.relocations = {}
        for s in sh:
            if s[1] != 9:
                continue
            if s[6] != sh.index(ds):
                raise ValueError('native-binding-relocation-table-unreviewed')
            for at in range(s[4], s[4]+s[5], s[9]):
                va,info = struct.unpack_from('<II', blob, at)
                self.relocations[va] = dict(type=info&255, symbol=self.symbols[info>>8])

    def raw(self, va, count):
        segments = [p for p in self.ph if p[0]==1 and p[2]<=va and va+count<=p[2]+p[4]]
        if len(segments)!=1:
            raise ValueError('native-binding-virtual-range-unreviewed')
        p = segments[0]; at = p[1]+va-p[2]
        return self.b[at:at+count]

    def pic_address(self, load, add):
        instruction = struct.unpack('<H', self.raw(load,2))[0]
        register = (instruction>>8)&7
        if instruction>>11 != 9 or self.raw(add,2) != struct.pack('<H',0x4478+register):
            raise ValueError('native-binding-pic-instruction-unreviewed')
        literal = ((load+4)&~3)+(instruction&255)*4
        return (struct.unpack('<I',self.raw(literal,4))[0]+add+4)&0xffffffff

    def cstring(self, address):
        return self.raw(address,100).split(b'\0',1)[0].decode('ascii')


def audit_std_loader_sources(file_blob, std_blob):
    for label,blob,sha in (('file',file_blob,FILE_SHA256),('std',std_blob,STD_SHA256)):
        if not isinstance(blob,bytes) or hashlib.sha256(blob).hexdigest()!=sha:
            raise ValueError('native-binding-'+label+'-source-unreviewed')
    file,std = _Elf(file_blob),_Elf(std_blob)
    for name,start,end,sha in REGIONS:
        syms = [s for s in file.symbols if s['name']==name and s['type']==2]
        if len(syms)!=1 or syms[0]['va']&~1!=start or syms[0]['bytes']!=end-start or hashlib.sha256(file.raw(start,end-start)).hexdigest()!=sha:
            raise ValueError('native-binding-loader-region-unreviewed')
    rows = []
    for i,(name,load) in enumerate(zip(SYMBOL_NAMES,NAME_LOAD_SITES)):
        name_va = file.pic_address(load,load+4)
        got = file.pic_address(load+10,load+14)
        relocation = file.relocations.get(got)
        symbol = (relocation or {}).get('symbol',{})
        exports = [s for s in std.symbols if s['name']==name and s['type']==2 and s['va'] and s['section']]
        if (file.cstring(name_va)!=name or not relocation or relocation['type']!=21
                or symbol.get('name')!=name or symbol.get('type')!=1 or symbol.get('bytes')!=4
                or file.raw(load+16,4).hex()!='09680860' or len(exports)!=1):
            raise ValueError('native-binding-symbol-source-unreviewed')
        export = exports[0]; entry = export['va']&~1
        rows.append(dict(index=i,name=name,nameVa=name_va,dlsymCallSite=load+6,
            gotVa=got,objectVa=symbol['va'],slotStoreVa=load+18,
            missingSymbolReturnCandidate=-(i+3),stdExportEntry=entry,stdExportBytes=export['bytes'],
            stdExportRegionSha256=hashlib.sha256(std.raw(entry,export['bytes'])).hexdigest()))
    paths = [file.cstring(file.pic_address(load,add)) for load,add in ((0x25846,0x2584a),(0x25852,0x25856))]
    if paths!=['libSTD.so','./libSTD.so']:
        raise ValueError('native-binding-library-names-unreviewed')
    return {'kind':'x431-offline-std-loader-source-audit','loaderSourceAudit':loader_source_metadata(),
        'symbolBindings':rows,'staticLookupExportChainReviewed':True,
        'actualProcessLibraryFingerprintQualified':False, **VERSION_GUARDS}


def _return_observation(value, expected):
    if value is not None and (type(value) is not int or not -0x80000000<=value<=0x7fffffff or value!=expected):
        raise ValueError('native-loader-return-source-mismatch')


def audit_loader_sequence(observation):
    fields = {'pStdDllPresent','pStdSearchIDHandlePresent','symbolSlots','events'}
    if not isinstance(observation,dict) or set(observation)!=fields:
        raise ValueError('native-loader-sequence-fields-invalid')
    std,search = observation['pStdDllPresent'],observation['pStdSearchIDHandlePresent']
    slots = observation['symbolSlots']; events = observation['events']
    if (type(std) is not bool or type(search) is not bool or not isinstance(slots,dict)
            or set(slots)!=set(SYMBOL_NAMES) or any(v is not None and type(v) is not bool for v in slots.values())
            or not isinstance(events,list) or not 1<=len(events)<=256):
        raise ValueError('native-loader-sequence-state-invalid')
    slots = dict(slots); fresh = False; lifetime = False; steps = []
    for event in events:
        if not isinstance(event,dict):
            raise ValueError('native-loader-event-required')
        op = event.get('operation'); resolved = []
        if op=='open-search':
            if set(event)!={'operation','firstDlopenSucceeded','fallbackDlopenSucceeded','symbolLookupResults','observedReturn'}:
                raise ValueError('native-loader-open-fields-invalid')
            first,fallback,results = event['firstDlopenSucceeded'],event['fallbackDlopenSucceeded'],event['symbolLookupResults']
            if not isinstance(results,list) or any(type(v) is not bool for v in results):
                raise ValueError('native-loader-symbol-results-invalid')
            if search:
                if first is not None or fallback is not None or results:
                    raise ValueError('native-loader-cached-search-does-not-resolve')
                ret = 0; status = 'cached-search-handle-bypasses-symbol-checks'
            else:
                if std:
                    if first is not None or fallback is not None:
                        raise ValueError('native-loader-cached-std-does-not-dlopen')
                else:
                    if type(first) is not bool or (first and fallback is not None) or (not first and type(fallback) is not bool):
                        raise ValueError('native-loader-dlopen-results-invalid')
                    std = first or fallback is True
                if not std:
                    if results:
                        raise ValueError('native-loader-null-library-does-not-resolve')
                    ret = -1; status = 'std-library-open-failed'
                else:
                    if not results or len(results)>len(SYMBOL_NAMES) or (False in results and results.index(False)!=len(results)-1) or (all(results) and len(results)!=len(SYMBOL_NAMES)):
                        raise ValueError('native-loader-symbol-sequence-incomplete-or-after-failure')
                    search = True; fresh = False; lifetime = False
                    for name,value in zip(SYMBOL_NAMES,results):
                        slots[name] = value; resolved.append(name)
                    if results[-1]:
                        ret = 0; fresh = True; lifetime = True; status = 'all-symbols-resolved-source-candidate'
                    else:
                        ret = -(len(results)+2); status = 'symbol-failure-leaves-cached-search-handle'
        elif op=='close-std':
            if set(event)!={'operation','observedDlcloseReturn','observedReturn'}:
                raise ValueError('native-loader-close-fields-invalid')
            closed = event['observedDlcloseReturn']
            if closed is not None and (type(closed) is not int or not -0x80000000<=closed<=0x7fffffff):
                raise ValueError('native-loader-dlclose-return-invalid')
            if not std and closed is not None:
                raise ValueError('native-loader-null-std-does-not-dlclose')
            if std:
                lifetime = False; fresh = False
            std = False; ret = 0; status = 'std-handle-cleared-search-handle-and-slots-retained'
        else:
            raise ValueError('native-loader-operation-unreviewed')
        _return_observation(event['observedReturn'],ret)
        all_slots = all(v is True for v in slots.values())
        steps.append(dict(operation=op,status=status,sourceReturnCandidate=ret,
            sourceResolvedSymbols=resolved,pStdDllPresentCandidate=std,
            pStdSearchIDHandlePresentCandidate=search,symbolSlotsCandidate=dict(slots),
            allSlotsObservedNonNullCandidate=all_slots,
            completeResolutionInSequenceCandidate=fresh,slotLifetimeCandidate=lifetime,
            loaderReturnMasksIncompleteBindingsCandidate=ret==0 and not (all_slots and std and search and fresh and lifetime),
            completeLoaderChainCandidate=all_slots and std and search and fresh and lifetime))
    return {'kind':'x431-offline-native-loader-sequence-audit','loaderSourceAudit':loader_source_metadata(),
        'steps':steps,'completeLoaderChainCandidate':steps[-1]['completeLoaderChainCandidate'],
        'actualProcessLibraryFingerprintQualified':False,'actualDynamicLookupBindingQualified':False,
        'observationsProveActualRuntimeOrigin':False, **VERSION_GUARDS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file-library',type=Path,required=True)
    parser.add_argument('--std-library',type=Path,required=True)
    parser.add_argument('--observation-json',type=Path)
    args = parser.parse_args(argv)
    try:
        report = audit_std_loader_sources(args.file_library.read_bytes(),args.std_library.read_bytes())
        if args.observation_json is not None:
            report['loaderSequenceAudit'] = audit_loader_sequence(json.loads(args.observation_json.read_text(encoding='utf-8-sig')))
    except (OSError,ValueError) as error:
        parser.exit(2,f'{error}\n')
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
