"""Explicit offline CPU reference for original LINEAR/IDENTICAL calculations.

This executes field calculation and parsing instructions, including output
copying. It does not execute Android input validation, JNI, a head or a vehicle.
The optional CPU engine and exact ELF checks are shared with x431_cpu_reference.
"""
from pathlib import Path
import argparse,collections,hashlib,json,math,re,struct,time

from . import x431_cpu_reference as base
from .x431_formula import parse_formula_text
from .x431_values import insert_raw

FIELD_REGIONS = (
    ('_Z12GetLINEARAnsSsSsPhiiiRSs', 0x31cd0, 1552, '0bc7ba11cd20ae53ae6fa4714f544569246e0785ba83f6c2f8bd1b08ede8ef47'),
    ('_Z15GetIDENTICALAnsSsSsPhiiiRSs', 0x2ef44, 5008, '0eef53a4432d4f1a69c7bcc81c94363f1ec62b6bb33c226e8189b5dd23d0acfd'),
    ('_Z13GetLINEARDataSsRSsS_RdS0_S0_', 0x27e10, 872, '86d76fd06af48376547a040b6370175af5188302cc7cfeb9d1cf699e50bccc40'),
    ('_Z13GetCommonDataSsRiRSs', 0x37594, 596, '0174a9b2db6244a4b1f46d07663a1986f7208eef0a676d195e207f2e217d0d27'),
    ('_Z13GetStrBetweenSsSsSsii', 0x36ecc, 460, 'efc9e1f0c0e64a16fe40f05b9cb689efbd6127bfdfe31616d34e08501c45dc77'),
    ('_Z10GetStrLeftSsSsii', 0x37098, 328, '3d669d3260380259b7466f55b5de5fa708376165a59c388d5325235ad4a3e791'),
    ('_Z11GetStrRightSsSsii', 0x371e0, 348, '01bb2416a2ab808a24c2cb9fd5117931280b9643be69c009898c0edfd13a6cd6'),
)
GUARDS = {**base.GUARDS,'fieldCalculationOnly':True,'androidInputValidationExecuted':False,
          'nativeEditingBehaviorVerified':False,'legalInputDomainQualified':False}


def source_audit(blob):
    elf,rows = base.source_audit(blob)
    for name,start,size,sha in FIELD_REGIONS:
        symbols = [s for s in elf.symbols if s['name']==name and s['type']==2 and s['section']]
        if (len(symbols)!=1 or symbols[0]['va']!=(start|1) or symbols[0]['bytes']!=size
                or hashlib.sha256(elf.raw(start,size)).hexdigest()!=sha):
            raise ValueError('field-reference-function-unreviewed')
        rows.append(dict(name=name,startVa=start,bytes=size,sha256=sha))
    return rows


def validate_field(formula,text,original,byte,bit):
    for name,value,limit in (('formula',formula,1000),('target-text',text,128)):
        if (type(value) is not str or not 1<=len(value)<=limit or '\0' in value
                or any(ord(c)>127 for c in value)):
            raise ValueError('field-reference-'+name+'-invalid')
    parsed = parse_formula_text(formula)
    if not parsed.get('ok') or parsed.get('kind') not in ('LINEAR','IDENTICAL'):
        raise ValueError('field-reference-formula-family-unreviewed')
    if (type(original) is not bytes or not 512<=len(original)<=1024
            or type(byte) is not int or not 0<=byte<=len(original)-512):
        # The actual functions copy a 512-byte source window before patching.
        raise ValueError('field-reference-source-window-required')
    base.validate_case(original[byte:],bit,parsed.get('bitLength'),hl=parsed.get('highLow'))
    if parsed.get('baseDataType') not in ('A_UINT8','A_UINT16','A_UINT32','A_INT8','A_INT16','A_INT32'):
        raise ValueError('field-reference-integer-path-required')
    if (parsed.get('encoding') or '').upper() not in ('UNDEFINED','2C'):
        raise ValueError('field-reference-integer-encoding-unreviewed')
    if not re.fullmatch(r'[+-]?(?:0[xX])?[0-9A-Fa-f]+|[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',text):
        raise ValueError('field-reference-numeric-text-required')
    return parsed


def libc_integer(text,radix,signed):
    """Bounded exact conversions; reject partial/overflow input, not simulate UI."""
    if type(radix) is not int or radix not in (2,10,16) or type(signed) is not bool:
        raise ValueError('field-reference-integer-base-unreviewed')
    if not isinstance(text,str) or not re.fullmatch(r'[+-]?(?:0[xX])?[0-9A-Fa-f]+',text):
        raise ValueError('field-reference-integer-input-unreviewed')
    try:value = int(text,radix)
    except ValueError as error:
        raise ValueError('field-reference-integer-input-unreviewed') from error
    lo,hi = (-0x80000000,0x7fffffff) if signed else (-0xffffffff,0xffffffff)
    if not lo<=value<=hi:
        raise ValueError('field-reference-integer-overflow-unreviewed')
    return value&0xffffffff


def libc_double(text):
    if not isinstance(text,str) or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',text):
        raise ValueError('field-reference-double-input-unreviewed')
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('field-reference-double-nonfinite')
    return struct.unpack('<II',struct.pack('<d',value))


class CpuFieldReference(base.CpuReference):
    def __init__(self,blob):
        regions = source_audit(blob)  # All extra functions pinned before engine import.
        super().__init__(blob)
        self.function_regions = regions
        self.poisoned = False
        self.field_events = []

    def code(self,u,address,size,userdata):
        if address in (0x320bc,0x26d0c,0x320c0,0x320fa,0x32126):
            if len(self.field_events)>=64:
                raise ValueError('field-reference-observation-limit')
            sp = u.reg_read(base.UC_ARM_REG_SP)
            event = dict(va=address)
            if address==0x26d0c:
                obj = self.uint(sp+8)
                ptr = u.reg_read(base.UC_ARM_REG_R2)
                event.update(bitOffset=self.uint(sp),bitLength=self.uint(sp+4),
                    highLowText=self.cstr(self.uint(obj)).decode('ascii'),inputPrefixHex=bytes(u.mem_read(ptr,5)).hex().upper())
            if address==0x320c0:
                event['temporaryOutputPrefixHex'] = bytes(u.mem_read(sp+0x98,5)).hex().upper()
            self.field_events.append(event)
        if any(start<=address<start+size for _,start,size,_ in FIELD_REGIONS):
            self.last.append(address);self.trace['originalInstructions']+=1
            if self.trace['originalInstructions']%1024==0:
                base._check(time.monotonic()<self.deadline,'field-reference-wall-budget-exhausted')
            return
        name = self.hooks.get(address)
        if name in ('strtol','strtoul','strtod'):
            a,b,c = (u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2))
            text = self.cstr(a).decode('ascii')
            self.last.append(address);self.trace[name]+=1
            base._check(b==0,'field-reference-numeric-endptr-unreviewed')
            if name=='strtod':
                lo,hi = libc_double(text)
                u.reg_write(base.UC_ARM_REG_R0,lo);u.reg_write(base.UC_ARM_REG_R1,hi)
            else:
                u.reg_write(base.UC_ARM_REG_R0,libc_integer(text,c,name=='strtol'))
            u.reg_write(base.UC_ARM_REG_PC,u.reg_read(base.UC_ARM_REG_LR))
            return
        return super().code(u,address,size,userdata)

    def string(self,obj,rep,text):
        data = text.encode('ascii')
        self.u.mem_write(rep,struct.pack('<III',len(data),len(data),0)+data+b'\0')
        self.word(obj,rep+12)

    def field(self,formula,text,original,byte=0,bit=0):
        parsed = validate_field(formula,text,original,byte,bit)
        if self.poisoned:
            raise ValueError('field-reference-prior-outcome-unknown')
        self.trace.clear();self.last.clear();self.next_alloc=base.HEAP;self.allocations.clear();self.field_events=[]
        u = self.u;u.mem_write(base.DATA,original)
        self.string(base.DATA+0x20000,base.DATA+0x21000,formula)
        self.string(base.DATA+0x20010,base.DATA+0x22000,text)
        out_obj = base.DATA+0x20020
        self.word(out_obj,0x10ce10+12)
        sp = base.STACK+0x80000
        u.mem_write(sp,struct.pack('<III',byte,bit,out_obj))
        for reg,value in ((base.UC_ARM_REG_R0,base.DATA+0x20000),(base.UC_ARM_REG_R1,base.DATA+0x20010),
                          (base.UC_ARM_REG_R2,base.DATA),(base.UC_ARM_REG_R3,len(original)),
                          (base.UC_ARM_REG_SP,sp),(base.UC_ARM_REG_LR,base.STOP|1)):
            u.reg_write(reg,value)
        self.deadline = time.monotonic()+2
        entry = 0x31cd1 if parsed['kind']=='LINEAR' else 0x2ef45
        try:
            u.emu_start(entry,base.STOP,count=600000)
            base._check(u.reg_read(base.UC_ARM_REG_PC)==base.STOP and u.reg_read(base.UC_ARM_REG_SP)==sp,'field-reference-did-not-return')
            ptr = self.uint(out_obj)
            result = dict(afterHex=bytes(u.mem_read(base.DATA,len(original))).hex().upper(),
                returnValue=u.reg_read(base.UC_ARM_REG_R0),outputText=self.cstr(ptr).decode('ascii'),events=list(self.field_events))
            # Dispose the actual caller-owned returned string with original C++.
            u.reg_write(base.UC_ARM_REG_R0,ptr-12);u.reg_write(base.UC_ARM_REG_R1,base.DATA+0x20300)
            u.reg_write(base.UC_ARM_REG_LR,base.STOP|1);self.deadline=time.monotonic()+2
            u.emu_start(0xc6309,base.STOP,count=600000)
            base._check(u.reg_read(base.UC_ARM_REG_PC)==base.STOP and u.reg_read(base.UC_ARM_REG_SP)==sp,'field-reference-dispose-did-not-return')
            base._check(not self.allocations,'field-reference-allocation-leak')
        except Exception as error:
            self.poisoned = True
            raise RuntimeError(dict(error=str(error),pc=u.reg_read(base.UC_ARM_REG_PC),
                recent=list(self.last),trace=dict(self.trace))) from error
        return {**result,'trace':dict(self.trace),'allocationsRemaining':0,**GUARDS}


def integer_formula(kind,width,hl,signed=False):
    dtype = 'A_INT32' if signed else 'A_UINT32'
    encoding = '2C' if signed else 'Undefined'
    if kind=='LINEAR':
        lower,upper = (-(1<<(width-1)),(1<<(width-1))-1) if signed else (0,(1<<width)-1)
        return (f'LINEAR:Lower={lower},Upper={upper},BaseDataType={dtype},Xa=0,Xb=1,Xc=1,'
                f'BitLength={width},BitMask=0,HighLow={hl},Encoding={encoding},Radix=10,'
                'DataType=A_FLOAT64,Precision=0,Unit=ms;')
    if kind=='IDENTICAL':
        return (f'IDENTICAL:Standar,BitMask=0,BaseDataType={dtype},BitLength={width},'
                f'Encoding={encoding},HighLow={hl},Radix=10,DataType={dtype},Precision=0;')
    raise ValueError('field-reference-formula-family-unreviewed')


def audit_matrix(blob,output):
    """Execute complete pure calculations, keeping source differences as evidence."""
    source_audit(blob)
    output.mkdir()  # Never replace previous evidence.
    probe = CpuFieldReference(blob)
    start = time.monotonic()
    counts = collections.Counter();calls = collections.Counter();examples = []
    layouts = collections.Counter()

    def compare(formula,text,original,byte,bit,raw,metadata):
        parsed = validate_field(formula,text,original,byte,bit)
        width,hl = parsed['bitLength'],parsed['highLow']
        source = probe.field(formula,text,original,byte,bit)
        calls.update(source['trace'])
        native = bytes.fromhex(source['afterHex'])
        project = insert_raw(parsed,original,byte,bit,raw)
        base._check(project['ok'],'field-reference-project-patch-failed')
        expected = project['after']
        base._check(native[:byte]==original[:byte] and native[byte+5:]==original[byte+5:],
                    'field-reference-outside-window-modified')
        native_read = probe.read(native[byte:],bit,width,hl)
        project_read = probe.read(expected[byte:],bit,width,hl)
        calls.update(native_read['trace']);calls.update(project_read['trace'])
        base._check(project_read['raw']==raw,'field-reference-project-native-readback-mismatch')
        preserved = base.untouched_bits_preserved(original[byte:],native[byte:],bit,width,hl)
        base._check(base.untouched_bits_preserved(original[byte:],expected[byte:],bit,width,hl),
                    'field-reference-project-collateral-change')
        matched,target = native==expected,native_read['raw']==raw
        count = (width+bit+7)//8
        base._check(source['returnValue']==count,'field-reference-return-size-mismatch')
        for key in ('cases','projectNativeReadbackMatched','projectUntouchedBitsPreserved'):
            counts[key]+=1
        counts['originalReadCalls']+=2
        counts['sourcePatchMatchesProject' if matched else 'sourcePatchDiffersProject']+=1
        counts['sourceTargetMatched' if target else 'sourceTargetDifferent']+=1
        counts['sourceUntouchedBitsPreserved' if preserved else 'sourceCollateralBitsChanged']+=1
        if not matched:layouts[(parsed['kind'],width,bit,hl)]+=1
        row = dict(**metadata,formula=formula,targetText=text,raw=raw,originalBytes=len(original),
            byteOffset=byte,bitOffset=bit,bitLength=width,highLow=hl,
            beforeWindowHex=original[byte:byte+8].hex().upper(),
            sourceAfterWindowHex=native[byte:byte+8].hex().upper(),
            projectAfterWindowHex=expected[byte:byte+8].hex().upper(),
            beforeSha256=hashlib.sha256(original).hexdigest(),
            sourceAfterSha256=hashlib.sha256(native).hexdigest(),
            projectAfterSha256=hashlib.sha256(expected).hexdigest(),
            sourcePatchMatched=matched,sourceTargetMatched=target,sourceUntouchedBitsPreserved=preserved,
            originalSourceReadRaw=native_read['raw'],originalProjectReadRaw=project_read['raw'],
            returnBytes=count,outputText=source['outputText'],events=source['events'],allocationsRemaining=0)
        if not target and len(examples)<6:examples.append(row)
        return row

    with (output/'cases.jsonl').open('w',encoding='utf8',newline='\n') as stream:
        for kind in ('LINEAR','IDENTICAL'):
            for width in range(1,33):
                for bit in range(8):
                    for hl in (0,1):
                        maximum=(1<<width)-1
                        values=sorted({0,1,maximum//2,maximum,(1<<min(width-1,15))})
                        formula=integer_formula(kind,width,hl)
                        for seed,size in ((19,512),(83,1024)):
                            original=bytes((i*37+seed)&255 for i in range(size))
                            byte=0 if size==512 else (width+bit)%17
                            for raw in values:
                                row=compare(formula,str(raw),original,byte,bit,raw,dict(fixture='unsigned-layout',seed=seed))
                                stream.write(json.dumps(row,separators=(',',':'))+'\n')
        # A separate signed path probes conversion differences without claiming
        # Android input validation or vehicle legal-value qualification.
        for kind in ('LINEAR','IDENTICAL'):
            for width in (8,16,32):
                for hl in (0,1):
                    formula=integer_formula(kind,width,hl,True)
                    for target in (-(1<<(width-1)),-2,-1,0,(1<<(width-1))-1):
                        original=bytes((i*11+53)&255 for i in range(1024))
                        row=compare(formula,str(target),original,512,0,target&((1<<width)-1),
                                    dict(fixture='signed-boundary',seed=53))
                        stream.write(json.dumps(row,separators=(',',':'))+'\n')
    result=dict(kind='x431-original-full-field-calculation-cpu-audit',schemaVersion=1,
        sourceSha256=base.SHA,functionRegions=probe.function_regions,engineVersion=probe.engine_version,
        layoutsPerFamily=512,families=['LINEAR','IDENTICAL'],counts=dict(counts),foundationCalls=dict(calls),
        differingLayouts=[dict(family=k,width=w,bitOffset=b,highLow=h,cases=n)
                          for (k,w,b,h),n in sorted(layouts.items())],targetDifferenceExamples=examples,
        elapsedSeconds=time.monotonic()-start,sourceBufferPatterns=['(i*37+seed)&255; seed19/83','(i*11+53)&255'],
        masksTested=[0],sourceCasesExecuted=False,fieldCalculationAndOutputCopyExecuted=True,**GUARDS)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calc-source',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.calc_source.stat().st_size!=base.SOURCE_BYTES:
        raise ValueError('field-reference-source-length-unreviewed')
    paths=[Path(__file__).resolve()]+[Path(__file__).with_name(name) for name in
           ('x431_cpu_reference.py','x431_values.py','x431_formula.py','x431_native_binding.py')]
    code=[dict(name=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
    result=audit_matrix(args.calc_source.read_bytes(),args.output_dir)
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256'] for p,r in zip(paths,code,strict=True)):
        raise ValueError('field-reference-code-changed')
    rows=[dict(name=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
          for p in sorted(args.output_dir.iterdir())]
    (args.output_dir/'manifest.json').write_text(json.dumps(dict(sourceSha256=base.SHA,code=code,outputs=rows,**GUARDS),indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(ok=True,counts=result['counts'],elapsedSeconds=result['elapsedSeconds'],**GUARDS)))
    return 0


if __name__=='__main__':raise SystemExit(main())
