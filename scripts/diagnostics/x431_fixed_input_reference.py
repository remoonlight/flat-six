"""Exact retained fixed text/bytefield inverse callers, without vehicle writes.

The entire pinned census is required. Full-input storage boundaries stay in
the catalog; cancellation and source callback models do not qualify editing.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import struct
import sys
from . import x431_cpu_reference as base
from .x431_negate_caller_reference import CpuNegateCallerReference, audit_sources as audit_parent
from .x431_calc_input_info_reference import validate_census
from .x431_text_field_reference import validate_field_bytes
from .x431_callback_source import _slot
from . import x431_numeric_foundation as numeric

PAIRS = {'A_ASCIISTRING':(0x32ff8, 0x3300a, 0x32fe2, 0x32fe6, 0x32ffa, 0x32ffe),
    'A_BYTEFIELD':(0x33258, 0x3326c, 0x33242, 0x33246, 0x3325c, 0x33260)}
GUARDS = dict(originalMinMaxCallerChainExecuted=False, fullFixedCatalogInputCoverage=False,
    actualAndroidUiExecuted=False, inputCharacterEncodingQualified=False,
    legalInputDomainQualified=False, completeCodingBlockQualified=False,
    actualDynamicBindingQualified=False, vehicleVerified=False,
    codingWriteQualified=False, codingRestoreQualified=False,
    sourceDifferencesAreProductRules=False, executionEnabled=False, writePayload=None)


def fixed_definitions(census):
    validate_census(census)
    rows=[r for r in census['definitions'] if r['parsed'].get('kind')=='IDENTICAL'
        and r['parsed'].get('bitLength') is not None]
    base._check(len(rows)==42, 'fixed-census-count-different')
    return rows


def audit_sources(calc, std, deviceid, language, readwrite):
    parent=audit_parent(calc, std, deviceid, language, readwrite)
    elf=base._Elf(calc); pairs=[]
    for kind,(language_call,format_call,load,add,format_load,format_add) in PAIRS.items():
        base._check(all(elf.raw(a,2).hex()=='9847' for a in (language_call,format_call)),
            'fixed-prompt-instruction-unreviewed')
        pairs.append(dict(baseDataType=kind, languageCallVa=language_call, formatterCallVa=format_call,
            languageSlot=_slot(elf,load,add,'GetTextFromTextLib',0x10b770),
            formatterSlot=_slot(elf,format_load,format_add,'SPRINTF',0x10ba5c),
            idHex='FF30001E',languageStackOffset=0xb8,formatterStackOffset=0x50,
            languageCapacity=200,formatterCapacity=50))
    base._check(elf.raw(0x32efa,4).hex()=='ebf7fce9','fixed-mask-conversion-call-unreviewed')
    base._check(elf.raw(0x32f6e,4).hex()=='ebf7d2e8','fixed-upper-mask-conversion-call-unreviewed')
    return dict(parent=parent,pairs=pairs,maskConversions=[dict(importName='strtol',
        callVa=0x32efa,returnVa=0x32efe,radix=16,signed=True,bits=32),
        dict(importName='strtoul',callVa=0x32f6e,returnVa=0x32f72,radix=16,signed=False,bits=32)],sourceInputBufferBytes=4096,
        maximumQualifiedNonNulStorageBytes=4095,fullCatalogRetained=True)


def validate_fixed(formula,replies,original,byte,bit,title,census):
    rows=fixed_definitions(census)
    base._check(type(formula) is str and formula in {r['formula'] for r in rows},
        'fixed-exact-retained-formula-required')
    parsed=validate_field_bytes(formula,b'',original,byte,bit)
    base._check(type(byte) is int and byte>=2 and type(bit) is int and bit==0,
        'fixed-header-or-bit-offset-unreviewed')
    base._check(type(title) is bytes and len(title)<=64 and b'\0' not in title
        and b'0x' not in title and b'0X' not in title,'fixed-title-unreviewed')
    base._check(type(replies) is list and 1<=len(replies)<=3,'fixed-reply-budget-required')
    for reply in replies:
        base._check(type(reply) is dict and set(reply)=={'data','accepted'}
            and type(reply['data']) is bytes and len(reply['data'])<=4095
            and b'\0' not in reply['data'] and type(reply['accepted']) is bool,
            'fixed-input-storage-or-reply-unreviewed')
        if parsed['baseDataType']=='A_BYTEFIELD':
            base._check(all(c in b'0123456789abcdefABCDEF' for c in reply['data']),
                'fixed-hex-character-unreviewed')
    return parsed


class CpuFixedCallerReference(CpuNegateCallerReference):
    def __init__(self,calc,std,deviceid,language,readwrite,census):
        fixed_definitions(census)
        self.fixed_audit=audit_sources(calc,std,deviceid,language,readwrite)
        self.census=census;self.fixed_prompts=[];self.pending_fixed_prompt=None
        super().__init__(calc,std,deviceid,language,readwrite)
        try:
            for row in self.info_audit['languageSource']['prompts']:
                self.language.prompts[int(row['idHex'],16)]=row
        except Exception:
            self.close();raise

    def validate_arguments(self,formula,replies,original,byte,bit,title):
        parsed=validate_fixed(formula,replies,original,byte,bit,title,self.census)
        self.fixed_kind=parsed['baseDataType'];self.fixed_width=parsed['bitLength']//8
        return parsed

    def numeric_conversion_budget(self):
        return min(8192,self.fixed_width*2+32)

    def execution_limits(self):
        return 60, 8_000_000

    def code(self,u,address,size,userdata):
        name=self.hooks.get(address);lr=u.reg_read(base.UC_ARM_REG_LR)
        if (name,lr) in (('strtol',0x32eff),('strtoul',0x32f73)):
            a,b,c=(u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2))
            length=self.fixed_width*2
            base._check(b==0 and c==16 and self.uint(a-12)==length and length<=8192
                and self.allocations.get(a-12,0)>=12+length+1,'fixed-mask-storage-unreviewed')
            data=bytes(u.mem_read(a,length+1))
            expected=b'F'*length if name=='strtol' else b'7'+b'F'*(length-1)
            base._check(data==expected+b'\0','fixed-mask-bytes-unreviewed')
            result=numeric.integer_prefix(data[:-1],16,name=='strtol',32,maximum_bytes=8192)
            self.numeric_events.append(dict(name=name,sourceReturnVa=lr&~1,
                inputBytesHex=data[:-1].hex().upper(),result=result,foundationSubstitution=True,
                originalFixedMaskStorage=True,maximumStorageBytes=8192))
            self.trace[name]+=1
            u.reg_write(base.UC_ARM_REG_R0,result['valueBits']);u.reg_write(base.UC_ARM_REG_PC,lr)
            return
        language_call,format_call,*_=PAIRS[self.fixed_kind]
        if address in (language_call,format_call):
            a,b,c=(u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2))
            sp=u.reg_read(base.UC_ARM_REG_SP)
            if address==language_call:
                base._check(self.uint(0x10b770)==0 and a==0xff30001e and b==sp+0xb8
                    and c==200 and self.pending_fixed_prompt is None,'fixed-language-arguments-unreviewed')
                result=dict(self.language.lookup(a,c),isolatedMinMaxPromptReference=False,
                    isolatedFixedPromptReference=True)
                data=bytes.fromhex(result['payloadHex']);self.pending_fixed_prompt=data[:-1]
            else:
                base._check(self.uint(0x10ba5c)==0 and a==sp+0x50 and b==sp+0xb8
                    and self.pending_fixed_prompt is not None and self.cstr(b)==self.pending_fixed_prompt
                    and c==self.fixed_width,'fixed-format-arguments-unreviewed')
                result=self.integer.format_integer(self.pending_fixed_prompt,struct.pack('<I',c))
                data=bytes.fromhex(result['outputBytesHex'])+b'\0'
                base._check(len(data)<=50,'fixed-format-capacity-unreviewed')
                self.pending_fixed_prompt=None
            target=b if address==language_call else a
            self.write(u,None,target,len(data),None,None);u.mem_write(target,data)
            self.fixed_prompts.append(dict(callVa=address,argumentWord=c,result=result))
            self._return(u,address,result);return
        return super().code(u,address,size,userdata)

    def call(self,formula,replies,original,byte=4,bit=0,title=b'field'):
        result=super().call(formula,replies,original,byte,bit,title)
        base._check(self.pending_fixed_prompt is None,'fixed-prompt-not-completed')
        return dict(result,**{**GUARDS,'originalFixedCallerChainExecuted':result['completed'],
            'fixedPrompts':self.fixed_prompts.copy()})


def matrix_cases(census):
    for definition in sorted(fixed_definitions(census),key=lambda r:(-r['parsed']['bitLength'],r['formula'])):
        parsed=definition['parsed'];width=parsed['bitLength']//8
        original=bytes(range(256))*16
        byte=min(3584,len(original)-width)
        target=(bytes((i*37+7)%256 for i in range(width)).hex().upper().encode()
            if parsed['baseDataType']=='A_BYTEFIELD' else bytes(65+i%26 for i in range(width)))
        if len(target)<=4095:
            yield definition,'full-input',original,byte,[dict(data=target,accepted=True)]
            short=target[:-2] if parsed['baseDataType']=='A_BYTEFIELD' else target[:-1]
            if not short:
                short=target+b'00'
            yield definition,'overflow-retry' if width==1 else 'short-retry',b'\xa5'*4096,byte,[dict(data=short,accepted=True),dict(data=target,accepted=True)]
            if width==1:
                yield definition,'empty-accepted-no-change',original,byte,[dict(data=b'',accepted=True)]
        yield definition,'cancel',original,byte,[dict(data=b'',accepted=False)]


def compare_case(definition,original,byte,replies,result):
    if not result['completed']:
        return dict(independentComparisonCompleted=False,sourceBoundaryRecorded=True)
    info=next(e for e in result['chainEvents'] if e['va']==0x247e6)
    inverse=next(e for e in result['chainEvents'] if e['va']==0x2483e)
    returned=next(e['inverseReturn'] for e in result['chainEvents'] if e['va']==0x24842)
    base._check(info['inputInfoReturn']==inverse['inverseInputInfoResultR3'],
        'fixed-input-return-not-propagated')
    canceled=not replies[-1]['accepted'];empty=not canceled and replies[-1]['data']==b''
    expected=bytearray(original)
    if empty:
        base._check(info['inputInfoReturn']==0 and returned==1,'fixed-empty-response-return-different')
    elif not canceled:
        data=replies[-1]['data'];parsed=definition['parsed'];width=parsed['bitLength']//8
        payload=bytes.fromhex(data.decode()) if parsed['baseDataType']=='A_BYTEFIELD' else data
        base._check(len(payload)==width and info['inputInfoReturn']==len(data)
            and returned==width,'fixed-input-length-or-inverse-return-different')
        expected[byte:byte+width]=payload
    else:
        base._check(info['inputInfoReturn']==-1 and returned==1,'fixed-cancel-return-different')
    base._check(result['afterHex']==expected.hex().upper() and result['repliesConsumed']==len(replies),
        'fixed-independent-buffer-or-retry-comparison-different')
    return dict(independentComparisonCompleted=True,independentBufferComparisonDifferent=False,
        canceled=canceled,fullReplacementCompared=not canceled and not empty,outerReturnIsNotInputAcceptance=True,
        **({'emptyAcceptedWithoutModification':True} if empty else {}))


def write_report(sources,census,output,counts):
    output=Path(output);definitions=fixed_definitions(census);boundaries=[]
    for row in definitions:
        width=row['parsed']['bitLength']//8
        required=width*(2 if row['parsed']['baseDataType']=='A_BYTEFIELD' else 1)
        if required>4095:
            boundaries.append(dict(formula=row['formula'],requiredInputBytes=required,
                availableNonNulBytes=4095,fullTargetExecuted=False,catalogDefinitionRetained=True))
    directory=Path(__file__).resolve().parents[1]
    files=sorted({Path(m.__file__).resolve() for m in tuple(sys.modules.values())
        if getattr(m,'__file__',None) and Path(m.__file__).resolve().is_relative_to(directory)})
    report=dict(kind='x431-original-fixed-input-caller-reference',schemaVersion=1,sourceAudit=sources,
        censusCanonicalSha256=hashlib.sha256(json.dumps(census,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        definitionCount=len(definitions),fullInputStorageFitDefinitions=len(definitions)-len(boundaries),
        storageBoundaries=boundaries,counts=dict(counts),
        casesSha256=hashlib.sha256((output/'cases.jsonl').read_bytes()).hexdigest(),
        code=[dict(path=f.relative_to(directory).as_posix(),sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in files],
        **GUARDS)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return report


def audit_matrix(calc,std,deviceid,language,readwrite,census,output):
    sources=audit_sources(calc,std,deviceid,language,readwrite);fixed_definitions(census)
    output=Path(output);output.mkdir();counts=collections.Counter()
    with (output/'cases.jsonl').open('x',encoding='utf8',newline='\n') as out:
        for definition,name,original,byte,replies in matrix_cases(census):
            probe=CpuFixedCallerReference(calc,std,deviceid,language,readwrite,census)
            try:
                result=probe.call(definition['formula'],replies,original,byte)
                comparison=compare_case(definition,original,byte,replies,result)
            finally:probe.close()
            base._check(probe.closed and all(getattr(probe,k).closed for k in
                ('hex','pair','string','integer','language','sprintf')),'fixed-engines-not-disposed')
            counts['cases']+=1;counts['enginesDisposed']+=17
            counts['completed']+=int(result['completed']);counts['rejectedVirtualWrite']+=int(not result['completed'])
            counts[name]+=1
            out.write(json.dumps(dict(formula=definition['formula'],name=name,byteOffset=byte,
                originalBytesHex=original.hex().upper(),replies=[dict(dataHex=r['data'].hex().upper(),
                accepted=r['accepted']) for r in replies],allEnginesDisposed=True,
                comparison=comparison,result=result),separators=(',',':'))+'\n');out.flush()
    return write_report(sources,census,output,counts)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','deviceid','language','readwrite','census','output-dir'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args()
    report=audit_matrix(*(getattr(a,k).read_bytes() for k in ('calc','std','deviceid','language','readwrite')),
        json.loads(a.census.read_text(encoding='utf8')),a.output_dir)
    print(json.dumps(dict(counts=report['counts'],storageBoundaries=len(report['storageBoundaries']),**GUARDS)))


if __name__=='__main__':main()
