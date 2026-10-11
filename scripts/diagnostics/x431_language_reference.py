"""Execute original language lookup against an explicit retained-file model.

The original ID lookup, decryption and indirect-text checks execute in ARM.
Opening/closing and context setup are bounded models, not Android cache state.
"""
from pathlib import Path
from .arm_write_monitor import attach_arm_write_monitor
import argparse, collections, hashlib, json, struct, time
from . import x431_sprintf_reference as cpu
from .x431_language_source import audit_language_sources, REGIONS
from .x431_native_binding import _Elf
from scripts.x431_re.gag_codec import parse_text_gag

CTX=cpu.DATA+0x20000
SEC=cpu.DATA+0x21000
FILE_TOKEN=0x71534944
HEAP=cpu.DATA+0x40000
GUARDS={**cpu.GUARDS,'isolatedLanguageReference':True,'virtualFileOnly':True,
    'sourceOpenExecuted':False,'sourceCloseExecuted':False,
    'languageContextSetupModeled':True,'actualLanguageFileStateQualified':False}
DIVISION_REGIONS=(
    ('__divsi3',0x8cd0c,220,'63ac3909169d308c5fe4c63d7c5b5f054a309f6ee433d958de4df53436387c48'),
    ('__aeabi_idivmod',0x8cde8,32,'242f34e093c7bee677e98c14bd99bc2787f73cfa3f9238c3a9574e2fce43212d'),
)


class CpuLanguageReference(cpu.CpuSprintfReference):
    def __init__(self,calc,std,language):
        self.source_audit=audit_language_sources(calc,std,language)
        elf=_Elf(std)
        for name,start,size,sha in DIVISION_REGIONS:
            symbols=[s for s in elf.symbols if s['name']==name and s['type']==2 and s['section']]
            cpu._check(len(symbols)==1 and symbols[0]['va']==start and symbols[0]['bytes']==size
                and hashlib.sha256(elf.raw(start,size)).hexdigest()==sha,'language-reference-division-source-unreviewed')
        self.prompts={int(r['idHex'],16):r for r in self.source_audit['prompts']}
        self.file_bytes=language
        section=self.source_audit['textSection'];self.section=section
        self.state=parse_text_gag(language[section['offset']:section['offset']+section['bytes']])
        try:
            import unicorn as engine
            from unicorn import arm_const
        except ImportError as error:raise ValueError('language-reference-private-unicorn-2.1.4-required') from error
        cpu._check(engine.__version__=='2.1.4','language-reference-engine-unreviewed')
        self.engine,self.r=engine,arm_const;self.u=engine.Uc(engine.UC_ARCH_ARM,engine.UC_MODE_THUMB)
        self.closed=False;self.poisoned=False;self.code_hook=self.write_hook=None
        self.allowed=[(s,s+n) for name,s,n,_ in REGIONS if name not in ('OpenFileIssueSearchID','CloseFileIssueSearchID')]
        self.allowed.extend((s,s+n) for _,s,n,_ in DIVISION_REGIONS)
        self.trace=collections.Counter();self.sink=bytearray();self.allocations={};self.next_alloc=HEAP
        self.opened=False;self.reads=[];self.cursor=section['offset'];self.seen=[]
        try:
            self._map(std)
            self.hooks[0x32680]='retained-language-open-model'
            self.hooks[0x32b98]='retained-language-close-model'
            self.code_hook=self.u.hook_add(engine.UC_HOOK_CODE,self.code)
            self.write_hook=attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close();raise

    def _writable(self,address,size):
        return type(size) is int and 0<=size<=1024 and (
            cpu.STACK<=address and address+size<=cpu.STACK+0x100000
            or cpu.DATA<=address and address+size<=cpu.DATA+200
            or any(a<=address and address+size<=a+n for a,n in self.allocations.items()))

    def write(self,u,access,address,size,value,userdata):
        cpu._check(self._writable(address,size),'language-reference-write-range-unreviewed')

    def _put(self,address,data):
        cpu._check(self._writable(address,len(data)),'language-reference-foundation-write-unreviewed')
        self.u.mem_write(address,data)

    def _cstring(self,address):
        raw=bytes(self.u.mem_read(address,200));at=raw.find(b'\0')
        cpu._check(at>=0,'language-reference-string-unterminated')
        return raw[:at]

    def code(self,u,address,size,userdata):
        if address==cpu.STOP:return
        name=self.hooks.get(address);r=self.r
        if name is None:
            cpu._check(any(a<=address<b for a,b in self.allowed) or 0x1e000<=address<0x1ec00,
                'language-reference-code-range-unreviewed')
            if address==0x8cde8:
                a,b=u.reg_read(r.UC_ARM_REG_R0),u.reg_read(r.UC_ARM_REG_R1)
                cpu._check(0<=a<200 and b==self.state['nsec'] and b>0,
                    'language-reference-positive-modulo-unreviewed')
                self.trace['originalSecretTableModulo']+=1
            if address in (0x255ac,0x25574,0x251c0,0x25614,0x32bc8,0x32e00):self.seen.append(address)
            self.trace['originalInstructions']+=1
            if self.trace['originalInstructions']%1024==0:
                cpu._check(time.monotonic()<self.deadline,'language-reference-wall-budget-exhausted')
            return
        a,b,c,d=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)]
        result=0;self.trace[name]+=1
        if name=='retained-language-open-model':
            cpu._check(a==974346 and not self.opened,'language-reference-open-state-unreviewed')
            self.opened=True;result=CTX
        elif name=='retained-language-close-model':
            cpu._check(a==CTX and self.opened and not self.allocations,'language-reference-close-state-unreviewed')
            self.opened=False
        elif name=='fseek':
            cpu._check(self.opened and a==FILE_TOKEN and c==0
                and self.section['offset']<=b<=self.section['offset']+self.section['bytes'],
                'language-reference-seek-unreviewed')
            self.cursor=b
        elif name=='fread':
            n=b*c
            cpu._check(self.opened and d==FILE_TOKEN and 0<b<=200 and 0<c<=200 and n<=200
                and self.section['offset']<=self.cursor
                and self.cursor+n<=self.section['offset']+self.section['bytes'],
                'language-reference-read-unreviewed')
            self._put(a,self.file_bytes[self.cursor:self.cursor+n])
            self.reads.append(dict(offset=self.cursor,bytes=n));self.cursor+=n;result=c
        elif name=='malloc':
            cpu._check(a==4 and len(self.allocations)<2,'language-reference-allocation-unreviewed')
            result=self.next_alloc;self.next_alloc+=16
            cpu._check(self.next_alloc<=HEAP+4096,'language-reference-allocation-budget-exhausted')
            self.allocations[result]=a;self._put(result,bytes(a))
        elif name=='free':
            cpu._check(a==0 or a in self.allocations,'language-reference-free-unreviewed')
            if a:self.allocations.pop(a)
        elif name in ('__aeabi_memclr','__aeabi_memclr8'):
            self._put(a,bytes(b)) if b<=1024 else cpu._check(False,'language-reference-memory-unreviewed')
        elif name=='__aeabi_memcpy':
            cpu._check(c<=200,'language-reference-copy-unreviewed')
            self._put(a,bytes(u.mem_read(b,c)))
        elif name=='memcmp':
            cpu._check(c in (4,12),'language-reference-compare-unreviewed')
            left,right=bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c))
            result=(left>right)-(left<right)
        elif name=='strlen':result=len(self._cstring(a))
        else:raise ValueError('language-reference-import-unreviewed:'+name)
        u.reg_write(r.UC_ARM_REG_R0,result&0xffffffff)
        u.reg_write(r.UC_ARM_REG_PC,u.reg_read(r.UC_ARM_REG_LR))

    def lookup(self,id_u32,capacity=200):
        cpu._check(type(id_u32) is int and id_u32 in self.prompts and type(capacity) is int and capacity==200,
            'language-reference-prompt-or-capacity-unreviewed')
        cpu._check(not self.closed and not self.poisoned,'language-reference-prior-outcome-unknown-or-closed')
        u=self.u;r=self.r;sp=cpu.STACK+0x80000;before=bytes([0xa5])*1024
        cpu._check(not self.opened and not self.allocations,'language-reference-prior-file-or-allocation-live')
        context=bytearray(self.state['ctx'])
        struct.pack_into('<IIf',context,0,FILE_TOKEN,self.state['index_off'],self.state['ver'])
        struct.pack_into('<IIII',context,0x110,self.state['nsec'],SEC,self.section['offset'],self.section['bytes'])
        u.mem_write(CTX,bytes(context));u.mem_write(SEC,self.state['sec']);u.mem_write(cpu.DATA,before)
        self.cursor=self.section['offset'];self.next_alloc=HEAP;self.trace.clear();self.reads.clear();self.seen.clear()
        saved=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R4,r.UC_ARM_REG_R5,r.UC_ARM_REG_R6,r.UC_ARM_REG_R7,
            r.UC_ARM_REG_R8,r.UC_ARM_REG_R9,r.UC_ARM_REG_R10,r.UC_ARM_REG_R11)]
        for reg,v in ((r.UC_ARM_REG_R0,id_u32),(r.UC_ARM_REG_R1,cpu.DATA),(r.UC_ARM_REG_R2,capacity),
            (r.UC_ARM_REG_R3,0),(r.UC_ARM_REG_SP,sp),(r.UC_ARM_REG_LR,cpu.STOP|1),(r.UC_ARM_REG_FPSCR,0)):
            u.reg_write(reg,v)
        self.deadline=time.monotonic()+1
        try:
            u.emu_start(0x255ad,cpu.STOP,count=200_000)
            cpu._check(u.reg_read(r.UC_ARM_REG_PC)==cpu.STOP and u.reg_read(r.UC_ARM_REG_SP)==sp,
                'language-reference-not-returned-or-stack-not-restored')
            actual_saved=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R4,r.UC_ARM_REG_R5,r.UC_ARM_REG_R6,r.UC_ARM_REG_R7,
                r.UC_ARM_REG_R8,r.UC_ARM_REG_R9,r.UC_ARM_REG_R10,r.UC_ARM_REG_R11)]
            expected=bytes.fromhex(self.prompts[id_u32]['payloadHex']);raw=bytes(u.mem_read(cpu.DATA,1024))
            regs=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)]
            cpu._check(saved==actual_saved and regs[0]==len(expected) and raw[:len(expected)]==expected
                and raw[len(expected):]==before[len(expected):] and not self.opened and not self.allocations,
                'language-reference-output-or-lifetime-invariant-failed')
            cpu._check(all(at in self.seen for at in (0x255ac,0x25574,0x251c0,0x25614,0x32bc8,0x32e00)),
                'language-reference-original-chain-not-observed')
            return dict(idHex=f'{id_u32:08X}',payloadHex=expected.hex(),returnValue=regs[0],returnedRegisters=regs,
                returnedApsr=u.reg_read(r.UC_ARM_REG_APSR),returnedFpscr=u.reg_read(r.UC_ARM_REG_FPSCR),
                trace=dict(self.trace),fileReads=self.reads.copy(),originalFunctionEntries=self.seen.copy(),
                virtualFileClosed=True,virtualAllocationsRemaining=0,**GUARDS)
        except Exception:self.poisoned=True;raise


def audit_matrix(calc,std,language,output):
    output.mkdir();p=CpuLanguageReference(calc,std,language);rows=[]
    try:
        for id_u32 in p.prompts:rows.append(p.lookup(id_u32))
        audit=p.source_audit
    finally:p.close()
    report=dict(kind='x431-original-language-prompt-reference',schemaVersion=1,sourceAudit=audit,
        divisionRegions=[dict(symbol=n,startVa=s,bytes=z,sha256=h) for n,s,z,h in DIVISION_REGIONS],
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),cases=rows,
        caseCount=len(rows),engineDisposed=True,**GUARDS)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','language','output-dir'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();r=audit_matrix(a.calc.read_bytes(),a.std.read_bytes(),a.language.read_bytes(),a.output_dir)
    print(json.dumps(dict(cases=r['caseCount'],**GUARDS)))


if __name__=='__main__':main()
