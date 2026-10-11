"""Original bounded text/bytefield inverse calculations, never a vehicle editor.

Raw caller storage bytes and source formulas are explicit. Encoding, Android UI,
complete coding blocks and actual libc bindings remain unqualified. Source
differences are reported rather than copied into desktop preview behavior.
"""
from pathlib import Path
from .arm_write_monitor import attach_arm_write_monitor, remove_arm_write_monitor
import argparse,collections,hashlib,json,struct,time
from . import x431_input_reference as previous
from . import x431_cpu_reference as base
from .x431_hex_sprintf_reference import CpuHexSprintfReference,FORMAT
from .x431_callback_source import audit_callback_sources
from .coding_input_source import _pic_address,REGIONS
from .x431_formula import parse_formula_text

FORMAT_SITES=((0x2f638,0x2f640,0x2f644),(0x2f790,0x2f79a,0x2f79c),
              (0x2fb14,0x2fb1c,0x2fb1e),(0x2fc90,0x2fc98,0x2fc9a))
GUARDS={**previous.GUARDS,'textInverseInstructionsOnly':True,
    'rawCallerStorageBytes':True,'actualAndroidLibcSprintfExecuted':False,
    'isolatedOriginalHexSprintfSubstitution':True,'completeBlockLengthQualified':False,
    'actualInputInfoReturnQualified':False}


def source_audit(calc,std,deviceid):
    audit=audit_callback_sources(calc,std,deviceid);elf=base._Elf(calc);sites=[]
    for load,add,call in FORMAT_SITES:
        address=_pic_address(elf,load,add)
        base._check(address==0xf61f8 and elf.cstring(address).encode()==FORMAT,
            'text-field-format-source-unreviewed')
        sites.append(dict(loadVa=load,addVa=add,callVa=call,returnVa=call+4,
            formatVa=address,formatBytesHex=FORMAT.hex().upper()))
    name,start,size,sha=REGIONS['calc'][1]
    base._check(hashlib.sha256(elf.raw(start,size)).hexdigest()==sha,'text-field-caller-source-unreviewed')
    return dict(callbackSources=audit,hexFormatSites=sites,
        inverseCaller=dict(name=name,startVa=start,bytes=size,sha256=sha,inputInfoCallVa=0x247e2,
            inputInfoResultToR5Va=0x247e6,inverseR3FromR5Va=0x2483c,inverseCallVa=0x2483e,
            r3IsInputInfoResult=True,r3IsOriginalBufferLength=False))


def validate_field_bytes(formula,data,original,byte,bit):
    if type(formula) is not str or not 1<=len(formula)<=1000 or not formula.isascii() or '\0' in formula:
        raise ValueError('text-field-formula-invalid')
    parsed=parse_formula_text(formula)
    if not parsed.get('ok') or parsed.get('kind')!='IDENTICAL' or parsed.get('baseDataType') not in ('A_ASCIISTRING','A_BYTEFIELD'):
        raise ValueError('text-field-family-unreviewed')
    if type(data) is not bytes or len(data)>8192:
        raise ValueError('text-field-caller-storage-unbounded')
    if parsed['baseDataType']=='A_BYTEFIELD' and (not data.isascii() or any(c not in b'0123456789abcdefABCDEF' for c in data) or len(data)%2):
        raise ValueError('text-field-complete-hex-pairs-required')
    count=len(data)//2 if parsed['baseDataType']=='A_BYTEFIELD' else len(data)
    if count>4092:
        raise ValueError('text-field-four-kib-temporary-boundary')
    if (type(original) is not bytes or not 512<=len(original)<=4096 or type(byte) is not int
            or not 0<=byte<=len(original)-512 or type(bit) is not int or not 0<=bit<=7):
        raise ValueError('text-field-source-window-unreviewed')
    width=parsed.get('bitLength')
    if width is not None and (type(width) is not int or not 1<=width<=32736 or width%8
            or byte+(width+bit+7)//8>len(original) or count>width//8):
        raise ValueError('text-field-fixed-layout-unreviewed')
    if byte+count+bool(bit)>len(original):
        raise ValueError('text-field-target-window-unreviewed')
    if width is None and (parsed.get('lengthInfo')!='MinMax' or parsed.get('termination') not in ('ZERO','END-OF-PDU')):
        raise ValueError('text-field-variable-layout-unreviewed')
    return parsed


class CpuTextFieldReference(previous.CpuInputReference):
    def __init__(self,calc,std,deviceid):
        self.source_audit=source_audit(calc,std,deviceid)
        super().__init__(calc)
        try:self.hex=CpuHexSprintfReference(calc,std,deviceid)
        except Exception:
            super().close();raise
        self.hex_events=[];self.write_hook=None;self.active_bytes=0
        try:self.write_hook=attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close();raise

    def cstr(self,at):
        out=bytearray()
        for offset in range(16384):
            value=self.u.mem_read(at+offset,1)[0]
            if not value:return bytes(out)
            out.append(value)
        raise ValueError('text-field-cstring-unbounded')

    def write(self,u,access,address,size,value,userdata):
        allowed=(base.STACK<=address and address+size<=base.STACK+0x100000
            or base.DATA<=address and address+size<=base.DATA+self.active_bytes
            or base.DATA+0x20000<=address and address+size<=base.DATA+0x20304
            or any(a<=address and address+size<=a+n for a,n in self.allocations.items()))
        base._check(allowed,'text-field-write-range-unreviewed')

    def code(self,u,address,size,userdata):
        name=self.hooks.get(address)
        if name in ('memcpy','memmove','__aeabi_memcpy','__aeabi_memcpy4','__aeabi_memcpy8','memset'):
            a,c=[u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R2)]
            self.write(u,None,a,c,None,None)
        if name in ('__aeabi_memclr','__aeabi_memclr4','__aeabi_memclr8'):
            a,b=[u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1)]
            self.write(u,None,a,b,None,None)
        if name=='sprintf':
            a,b,c=[u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2)]
            lr=u.reg_read(base.UC_ARM_REG_LR);sp=u.reg_read(base.UC_ARM_REG_SP)
            base._check(b==0xf61f8 and lr&~1 in {r[2]+4 for r in FORMAT_SITES}
                and sp<=a and a+50<=sp+0x1150 and len(self.hex_events)<8192,
                'text-field-hex-callsite-or-destination-unreviewed')
            result=self.hex.format_byte(self.cstr(b),c,u.reg_read(base.UC_ARM_REG_FPSCR))
            output=bytes.fromhex(result['outputBytesHex']);self.write(u,None,a,len(output)+1,None,None)
            u.mem_write(a,output+b'\0')
            self.hex_events.append(dict(returnVa=lr&~1,argumentByte=c,outputBytesHex=result['outputBytesHex'],trace=result['trace']))
            for reg,value in zip((base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2,base.UC_ARM_REG_R3),result['returnedRegisters']):u.reg_write(reg,value)
            u.reg_write(base.UC_ARM_REG_APSR,result['returnedApsr']);u.reg_write(base.UC_ARM_REG_FPSCR,result['returnedFpscr']);u.reg_write(base.UC_ARM_REG_PC,lr)
            self.trace['isolatedOriginalHexSprintf']+=1;return
        return super().code(u,address,size,userdata)

    def field_bytes(self,formula,data,original,byte=0,bit=0,raw_r3=None):
        parsed=validate_field_bytes(formula,data,original,byte,bit)
        if raw_r3 is None and parsed.get('bitLength') is None:
            raise ValueError('text-field-explicit-unqualified-input-info-result-required')
        raw_r3=0 if raw_r3 is None else raw_r3
        base._check(type(raw_r3) is int and 0<=raw_r3<=65535,'text-field-input-info-result-unbounded')
        base._check(not self.closed and not self.poisoned,'text-field-prior-outcome-unknown-or-closed')
        self.trace.clear();self.last.clear();self.allocations.clear();self.next_alloc=base.HEAP
        self.hex_events.clear();self.field_events=[];self.active_bytes=len(original)
        u=self.u;u.mem_write(base.DATA,original);u.mem_write(base.DATA+len(original),b'\xa7'*32)
        objects=(base.DATA+0x20000,base.DATA+0x20010,base.DATA+0x20020)
        for obj,value in zip(objects,(formula.encode(),data,b'')):self.put_string(obj,0,value)
        sp=base.STACK+0x80000;u.mem_write(sp,struct.pack('<III',byte,bit,objects[2]))
        for reg,value in ((base.UC_ARM_REG_R0,objects[0]),(base.UC_ARM_REG_R1,objects[1]),(base.UC_ARM_REG_R2,base.DATA),(base.UC_ARM_REG_R3,raw_r3),(base.UC_ARM_REG_SP,sp),(base.UC_ARM_REG_LR,base.STOP|1)):u.reg_write(reg,value)
        # Full 4,092-byte probes include per-instruction IT-shadow observation.
        # Keep the existing instruction bound; allow the measured Windows cost.
        self.deadline=time.monotonic()+60
        try:
            u.emu_start(0x2ef45,base.STOP,count=50_000_000)
            base._check(u.reg_read(base.UC_ARM_REG_PC)==base.STOP and u.reg_read(base.UC_ARM_REG_SP)==sp,'text-field-return-or-stack-unreviewed')
            ret=u.reg_read(base.UC_ARM_REG_R0);after=bytes(u.mem_read(base.DATA,len(original)))
            ptr=self.uint(objects[2]);length=self.uint(ptr-12);base._check(length<=16384,'text-field-output-unbounded')
            output=bytes(u.mem_read(ptr,length))
            base._check(bytes(u.mem_read(base.DATA+len(original),32))==b'\xa7'*32,'text-field-buffer-sentinel-changed')
            for obj in objects:
                u.reg_write(base.UC_ARM_REG_R0,self.uint(obj)-12);u.reg_write(base.UC_ARM_REG_R1,base.DATA+0x20300);u.reg_write(base.UC_ARM_REG_LR,base.STOP|1)
                u.emu_start(0xc6309,base.STOP,count=300_000)
                base._check(u.reg_read(base.UC_ARM_REG_PC)==base.STOP and u.reg_read(base.UC_ARM_REG_SP)==sp,'text-field-dispose-unreviewed')
            base._check(not self.allocations,'text-field-allocations-not-released')
            return dict(returnValue=ret,rawInputInfoResultR3=raw_r3,afterHex=after.hex().upper(),outputBytesHex=output.hex().upper(),
                trace=dict(self.trace),hexCallouts=self.hex_events.copy(),allocationsRemaining=0,**GUARDS)
        except Exception as e:
            self.poisoned=True
            raise RuntimeError(dict(error=str(e),pc=u.reg_read(base.UC_ARM_REG_PC),recent=list(self.last),trace=dict(self.trace))) from e

    def close(self):
        errors=[]
        if hasattr(self,'hex'):
            try:self.hex.close()
            except Exception as e:errors.append(e)
        if self.write_hook is not None and not self.closed:
            try:remove_arm_write_monitor(self.u, self.write_hook);self.write_hook=None
            except Exception as e:errors.append(e)
        try:super().close()
        except Exception as e:errors.append(e)
        if errors:raise ValueError('text-field-engine-cleanup-failed') from errors[0]


def audit_definitions(calc,std,deviceid,census,output):
    source=source_audit(calc,std,deviceid)
    from .offline_match import ANCHORS
    base._check(census['sourceSha256']==ANCHORS['variants'],'text-field-definition-source-unreviewed')
    seen=set()
    for definition in census['definitions']:
        parsed={k:v for k,v in definition['parsed'].items() if k not in ('id_hex','text')}
        base._check(json.dumps(parsed,sort_keys=True)==json.dumps(parse_formula_text(definition['formula']),sort_keys=True) and definition['formula'] not in seen,
            'text-field-census-formula-mismatch')
        seen.add(definition['formula'])
    output.mkdir();rows=[];counts=collections.Counter();started=time.monotonic()
    for definition in census['definitions']:
        parsed=definition['parsed']
        if parsed.get('kind')!='IDENTICAL':
            counts['otherFormulaDefinitions']+=1;continue
        length=parsed['bitLength']//8 if parsed.get('bitLength') else max(parsed.get('minLength') or 0,6)
        target=bytes(65+i%26 for i in range(length))
        data=target.hex().upper().encode() if parsed['baseDataType']=='A_BYTEFIELD' else target
        original=b'\xa5'*4096;byte=4;probe=CpuTextFieldReference(calc,std,deviceid)
        raw_r3=0 if parsed.get('bitLength') else 4096
        try:result=probe.field_bytes(definition['formula'],data,original,byte,raw_r3=raw_r3)
        finally:probe.close()
        after=bytes.fromhex(result['afterHex']);expected=original[:byte]+target+original[byte+len(target):]
        matched=after==expected
        counts['originalCalculationsReturned']+=1;counts['expectedBytePatchMatched' if matched else 'expectedBytePatchDifferent']+=1
        counts['originalHexCallouts']+=len(result['hexCallouts'])
        if parsed.get('bitLength'):counts['fixedDefinitions']+=1
        else:counts['variableUnqualifiedRawR3Probes']+=1
        rows.append(dict(formula=definition['formula'],sourceRecords=definition['records'],sourceOffsets=definition['sourceOffsets'],
            targetStorageHex=data.hex().upper(),targetBytes=length,byteOffset=byte,originalSha256=hashlib.sha256(original).hexdigest(),
            independentBytePatchMatched=matched,changedOffsets=[i for i,(a,b) in enumerate(zip(original,after)) if a!=b],
            actualTargetPrefixMatchedBytes=next((i for i,(a,b) in enumerate(zip(after[byte:],target)) if a!=b),len(target)),
            bothEnginesDisposed=probe.closed and probe.hex.closed,result=result))
    with (output/'cases.jsonl').open('x',encoding='utf8',newline='\n') as f:
        for row in rows:f.write(json.dumps(row,separators=(',',':'))+'\n')
    names=('x431_text_field_reference.py','x431_hex_sprintf_reference.py','x431_input_reference.py','x431_field_reference.py','x431_cpu_reference.py','x431_sprintf_reference.py','x431_integer_sprintf_reference.py','x431_callback_source.py','x431_language_source.py','coding_input_source.py','x431_formula.py','x431_values.py','x431_native_binding.py')
    report=dict(kind='x431-text-bytefield-inverse-reference',schemaVersion=1,sourceAudit=source,
        definitionSourceSha256=census['sourceSha256'],definitionCensusSha256=hashlib.sha256(json.dumps(census,sort_keys=True,ensure_ascii=True).encode()).hexdigest(),
        counts=dict(counts),elapsedSeconds=round(time.monotonic()-started,5),casesSha256=hashlib.sha256((output/'cases.jsonl').read_bytes()).hexdigest(),
        code=[dict(name=n,sha256=hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()) for n in names],**GUARDS)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n');return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','deviceid','census','output-dir'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();r=audit_definitions(a.calc.read_bytes(),a.std.read_bytes(),a.deviceid.read_bytes(),json.loads(a.census.read_text(encoding='utf8')),a.output_dir)
    print(json.dumps(dict(counts=r['counts'],casesSha256=r['casesSha256'],**GUARDS)))


if __name__=='__main__':main()
