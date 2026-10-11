"""Original decimal integer SPRINTF, including its retained ARM division code.

Only %d/%u and four complete retained language templates are admitted. The
original formatter emits bytes; Python formatting never supplies its output.
"""
from pathlib import Path
import argparse, hashlib, json, struct, time
from . import x431_sprintf_reference as floating
from .x431_language_source import PROMPTS, audit_language_sources
from .x431_callback_source import audit_callback_sources
from .x431_native_binding import _Elf

GUARDS = {**floating.GUARDS, 'isolatedIntegerSprintfReference': True,
    'divisionFoundationModeled': False, 'originalIntegerDivisionSupported': True}
INTEGER_REGIONS = (
    ('__udivsi3',0xc644,168,'d31a72fca065ea825bd3fbe4dd37ba846522b5642d0504b96ab5a630252d4370'),
    ('__aeabi_uidivmod',0xc6ec,32,'e645e895869f8bb3e584efee5ad127ea3914df0c377972e9a786f28d4167e1ac'),
)


def validate_integer_format(fmt, argument_bytes, fpscr=0):
    if (type(fmt) is not bytes or b'\0' in fmt
            or not (fmt in (b'%d',b'%u') or hashlib.sha256(fmt+b'\0').hexdigest() in {r[3] for r in PROMPTS})):
        raise ValueError('integer-sprintf-format-unreviewed')
    if type(argument_bytes) is not bytes or len(argument_bytes)!=4:
        raise ValueError('integer-sprintf-word-bytes-required')
    if type(fpscr) is not int or not 0<=fpscr<=0xffffffff or fpscr & 0x00c00000:
        raise ValueError('integer-sprintf-rounding-mode-unreviewed')
    return struct.unpack('<I',argument_bytes)[0]


class CpuIntegerSprintfReference(floating.CpuSprintfReference):
    def __init__(self,calc,std,deviceid):
        audit_callback_sources(calc,std,deviceid)
        elf=_Elf(deviceid)
        for name,start,size,sha in INTEGER_REGIONS:
            symbols=[s for s in elf.symbols if s['name']==name and s['type']==2 and s['section']]
            floating._check(len(symbols)==1 and symbols[0]['va']==start and symbols[0]['bytes']==size
                and hashlib.sha256(elf.raw(start,size)).hexdigest()==sha,'integer-sprintf-source-region-unreviewed')
        super().__init__(calc,std,deviceid)
        self.allowed.extend((s,s+n) for _,s,n,_ in INTEGER_REGIONS)

    def code(self,u,address,size,userdata):
        if address==0xc6ec:
            r=self.r; b=u.reg_read(r.UC_ARM_REG_R1)
            floating._check(b==10,'integer-sprintf-divisor-unreviewed')
            self.trace['originalIntegerDivisions']+=1
        return super().code(u,address,size,userdata)

    def validate_format(self,fmt,argument_bytes,fpscr=0):
        return validate_integer_format(fmt,argument_bytes,fpscr)

    def format_integer(self,fmt,argument_bytes,fpscr=0):
        value=self.validate_format(fmt,argument_bytes,fpscr)
        floating._check(not self.closed and not self.poisoned,'integer-sprintf-prior-outcome-unknown-or-closed')
        u=self.u;r=self.r;sp=floating.STACK+0x80000;before=bytes([0xa5])*1024
        u.mem_write(floating.DATA,before);u.mem_write(floating.DATA+0x10000,fmt+b'\0')
        self.trace.clear();self.sink.clear()
        for reg,v in ((r.UC_ARM_REG_R0,floating.DATA),(r.UC_ARM_REG_R1,floating.DATA+0x10000),
                      (r.UC_ARM_REG_R2,value),(r.UC_ARM_REG_R3,0),
                      (r.UC_ARM_REG_SP,sp),(r.UC_ARM_REG_LR,floating.STOP|1),(r.UC_ARM_REG_FPSCR,fpscr)):
            u.reg_write(reg,v)
        self.deadline=time.monotonic()+1
        try:
            u.emu_start(0x7dc9,floating.STOP,count=100_000)
            floating._check(u.reg_read(r.UC_ARM_REG_PC)==floating.STOP and u.reg_read(r.UC_ARM_REG_SP)==sp,
                'integer-sprintf-not-returned-or-stack-not-restored')
            raw=bytes(u.mem_read(floating.DATA,1024));out=raw.split(b'\0',1)[0]
            regs=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)]
            floating._check(len(out)<50 and regs[0]==len(out) and self.sink==out
                and raw[len(out)+1:]==before[len(out)+1:],'integer-sprintf-output-invariant-failed')
            return dict(outputBytesHex=out.hex().upper(),virtualStdioHex=self.sink.hex().upper(),
                returnValue=regs[0],returnedRegisters=regs,returnedApsr=u.reg_read(r.UC_ARM_REG_APSR),
                returnedFpscr=u.reg_read(r.UC_ARM_REG_FPSCR),trace=dict(self.trace),
                originalIntegerDivisionExecuted=self.trace['originalIntegerDivisions']>0,**GUARDS)
        except Exception:
            self.poisoned=True;raise


def audit_matrix(calc,std,deviceid,language,output):
    audit=audit_language_sources(calc,std,language)
    output.mkdir();probe=CpuIntegerSprintfReference(calc,std,deviceid);rows=[]
    formats=[b'%d',b'%u']+[bytes.fromhex(r['payloadHex'])[:-1] for r in audit['prompts']]
    try:
        for fmt in formats:
            for value in (0,1,2,9,10,99,100,255,65535,0x7fffffff,0x80000000,0xffffffff):
                word=struct.pack('<I',value);result=probe.format_integer(fmt,word)
                numeric=value-(1<<32) if b'%d' in fmt and value>>31 else value
                comparison=fmt.replace(b'%d',str(numeric).encode()).replace(b'%u',str(numeric).encode())
                rows.append(dict(formatBytesHex=fmt.hex().upper(),argumentWordHex=word.hex().upper(),
                    result=result,decimalComparisonHex=comparison.hex().upper(),
                    comparisonDifferent=result['outputBytesHex']!=comparison.hex().upper()))
    finally:probe.close()
    report=dict(kind='x431-original-integer-sprintf-reference',schemaVersion=1,sourceAudit=audit,
        integerDivisionRegions=[dict(symbol=n,startVa=s,bytes=z,sha256=h) for n,s,z,h in INTEGER_REGIONS],
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),cases=rows,caseCount=len(rows),
        comparisonDifferent=sum(r['comparisonDifferent'] for r in rows),engineDisposed=True,**GUARDS)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','deviceid','language','output-dir'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();r=audit_matrix(a.calc.read_bytes(),a.std.read_bytes(),a.deviceid.read_bytes(),a.language.read_bytes(),a.output_dir)
    print(json.dumps(dict(cases=r['caseCount'],comparisonDifferent=r['comparisonDifferent'],**GUARDS)))


if __name__=='__main__':main()
