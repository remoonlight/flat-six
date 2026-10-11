"""Explicit isolated original SPRINTF reference for three finite float formats.

Original DEVICEID instructions, bounded foundation math/memory, and a virtual
stdio sink. No host ELF or stdio calls; no Android or vehicle qualification.
"""
from pathlib import Path
from .arm_write_monitor import attach_arm_write_monitor, remove_arm_write_monitor
import argparse, collections, hashlib, json, math, struct, time, weakref
from .x431_callback_source import audit_callback_sources, REGIONS
from .x431_native_binding import _Elf

STACK, DATA, IMPORTS, STOP = 0x2000000, 0x2500000, 0x3000000, 0x3100000
FORMATS = (b'%.0f', b'%.1f', b'%.3f')
GUARDS = dict(cpuEmulationOnly=True, hostElfLoaded=False, hostStdioCalled=False,
    virtualStdioCapture=True, actualDynamicBindingQualified=False,
    runtimeCallbacksBound=False, androidInputWorkflowVerified=False,
    nativeEditingBehaviorVerified=False, inputCharacterEncodingQualified=False,
    legalInputDomainQualified=False, vehicleVerified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)


def validate_format(fmt, double_bytes, fpscr=0):
    if type(fmt) is not bytes or fmt not in FORMATS:
        raise ValueError('sprintf-reference-format-unreviewed')
    if type(double_bytes) is not bytes or len(double_bytes) != 8:
        raise ValueError('sprintf-reference-double-bytes-invalid')
    value = struct.unpack('<d', double_bytes)[0]
    if not math.isfinite(value) or abs(value) > 1_000_000:
        raise ValueError('sprintf-reference-finite-domain-unreviewed')
    if type(fpscr) is not int or not 0 <= fpscr <= 0xffffffff or fpscr & 0x00c00000:
        raise ValueError('sprintf-reference-rounding-mode-unreviewed')
    return value


def _check(condition, reason):
    if not condition:
        raise ValueError(reason)


class CpuSprintfReference:
    def __init__(self, calc, std, deviceid):
        # Three-file registration/implementation chain before optional engine import.
        self.source_audit = audit_callback_sources(calc, std, deviceid)
        try:
            import unicorn as engine
            from unicorn import arm_const
        except ImportError as error:
            raise ValueError('sprintf-reference-private-unicorn-2.1.4-required') from error
        _check(engine.__version__ == '2.1.4', 'sprintf-reference-engine-unreviewed')
        self.engine, self.r = engine, arm_const
        self.u = engine.Uc(engine.UC_ARCH_ARM, engine.UC_MODE_THUMB)
        self.closed = False; self.poisoned = False; self.code_hook = self.write_hook = None
        self.allowed = [(s,s+n) for lib,_,s,n,_ in REGIONS if lib == 'deviceid']
        self.trace = collections.Counter(); self.sink = bytearray()
        try:
            self._map(deviceid)
            self.code_hook = self.u.hook_add(engine.UC_HOOK_CODE, self.code)
            self.write_hook = attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close()
            raise

    def _map(self, blob):
        e = _Elf(blob); u = self.u; uc = self.engine; r = self.r
        u.ctl_set_cpu_model(r.UC_CPU_ARM_CORTEX_A9)
        for p in e.ph:
            if p[0] == 1:
                start=p[2]&~4095; end=(p[2]+p[5]+4095)&~4095
                perms=(uc.UC_PROT_READ if p[6]&4 else 0)|(uc.UC_PROT_WRITE if p[6]&2 else 0)|(uc.UC_PROT_EXEC if p[6]&1 else 0)
                u.mem_map(start,end-start,perms); u.mem_write(p[2],blob[p[1]:p[1]+p[4]])
        for start,size,perms in ((STACK,0x100000,3),(DATA,0x100000,3),(IMPORTS,0x100000,5),(STOP,4096,5)):
            u.mem_map(start,size,perms)
        self.imports = {}; self.hooks = {}
        for s in e.symbols:
            if not s['section'] and s['name']:
                at=IMPORTS+len(self.imports)*16
                self.imports[s['name']]=at|1 if s['type']==2 else DATA+0x1000+len(self.imports)*256
                if s['type']==2:
                    self.hooks[at]=s['name']; u.mem_write(at,bytes.fromhex('7047'))
        for at,rel in e.relocations.items():
            sym=rel['symbol']; old=struct.unpack('<I',u.mem_read(at,4))[0]
            addr=sym['va'] if sym['section'] else self.imports.get(sym['name'],0)
            typ=rel['type']; value=old if typ==23 else addr if typ in (21,22) else old+addr if typ==2 else None
            _check(value is not None, 'sprintf-reference-relocation-unreviewed')
            u.mem_write(at,struct.pack('<I',value&0xffffffff))
        u.mem_write(self.imports['__stack_chk_guard'],struct.pack('<I',0x59712486))
        u.reg_write(r.UC_ARM_REG_C1_C0_2,0x00f00000); u.reg_write(r.UC_ARM_REG_FPEXC,0x40000000)

    def write(self, u, access, address, size, value, userdata):
        _check(STACK <= address and address+size <= STACK+0x100000
               or DATA <= address and address+size <= DATA+50,
               'sprintf-reference-write-range-unreviewed')

    def code(self, u, address, size, userdata):
        if address == STOP:
            return
        name=self.hooks.get(address); r=self.r
        if name is None:
            _check(any(a <= address < b for a,b in self.allowed) or 0x5700 <= address < 0x5b00,
                   'sprintf-reference-code-range-unreviewed')
            self.trace['originalInstructions'] += 1
            if self.trace['originalInstructions'] % 1024 == 0:
                _check(time.monotonic() < self.deadline, 'sprintf-reference-wall-budget-exhausted')
            return
        self.trace[name] += 1
        a,b,c,d=[u.reg_read(x) for x in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)]
        result=0
        if name == 'putc':
            _check(b == self.imports['__sF']+84 and len(self.sink) < 49, 'sprintf-reference-stdio-unreviewed')
            self.sink.append(a&255); result=a&255
        elif name in ('__aeabi_memclr8','__aeabi_memset'):
            _check(b <= 1024 and STACK <= a and a+b <= STACK+0x100000, 'sprintf-reference-memory-unreviewed')
            u.mem_write(a,bytes([c&255 if name=='__aeabi_memset' else 0])*b)
        elif name == 'floor':
            value=struct.unpack('<d',struct.pack('<II',a,b))[0]
            _check(math.isfinite(value) and abs(value) < 1e9, 'sprintf-reference-floor-unreviewed')
            result,hi=struct.unpack('<II',struct.pack('<d',float(math.floor(value))))
            u.reg_write(r.UC_ARM_REG_R1,hi)
        else:
            raise ValueError('sprintf-reference-import-unreviewed:' + name)
        u.reg_write(r.UC_ARM_REG_R0,result); u.reg_write(r.UC_ARM_REG_PC,u.reg_read(r.UC_ARM_REG_LR))

    def format(self, fmt, double_bytes, fpscr=0):
        validate_format(fmt, double_bytes, fpscr)
        _check(not self.closed and not self.poisoned, 'sprintf-reference-prior-outcome-unknown-or-closed')
        u=self.u; r=self.r; before=bytes([0xa5])*1024; sp=STACK+0x80000
        u.mem_write(DATA,before); u.mem_write(DATA+0x10000,fmt+b'\0')
        lo,hi=struct.unpack('<II',double_bytes); self.trace.clear(); self.sink.clear()
        for reg,value in ((r.UC_ARM_REG_R0,DATA),(r.UC_ARM_REG_R1,DATA+0x10000),
                          (r.UC_ARM_REG_R2,lo),(r.UC_ARM_REG_R3,hi),
                          (r.UC_ARM_REG_SP,sp),(r.UC_ARM_REG_LR,STOP|1),(r.UC_ARM_REG_FPSCR,fpscr)):
            u.reg_write(reg,value)
        self.deadline=time.monotonic()+1
        try:
            u.emu_start(0x7dc9,STOP,count=100_000)
            _check(u.reg_read(r.UC_ARM_REG_PC)==STOP and u.reg_read(r.UC_ARM_REG_SP)==sp,
                   'sprintf-reference-not-returned-or-stack-not-restored')
            raw=bytes(u.mem_read(DATA,1024)); out=raw.split(b'\0',1)[0]
            regs=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)]
            _check(regs[0]==len(out) and self.sink==out and raw[len(out)+1:]==before[len(out)+1:],
                   'sprintf-reference-output-invariant-failed')
            return dict(outputBytesHex=out.hex().upper(), virtualStdioHex=self.sink.hex().upper(),
                returnValue=regs[0], returnedRegisters=regs, returnedApsr=u.reg_read(r.UC_ARM_REG_APSR),
                returnedFpscr=u.reg_read(r.UC_ARM_REG_FPSCR), trace=dict(self.trace), **GUARDS)
        except Exception:
            self.poisoned=True
            raise

    def close(self):
        if self.closed:
            return
        u=self.u
        errors=[]
        for hook in (self.code_hook,self.write_hook):
            if hook is not None:
                try:
                    remove_arm_write_monitor(u, hook)
                except Exception as error:
                    errors.append(error)
        finalizer=getattr(u,'_Uc__finalizer',None)
        _check(isinstance(finalizer,weakref.finalize) and finalizer.alive, 'sprintf-reference-finalizer-unreviewed')
        finalizer(); _check(not finalizer.alive, 'sprintf-reference-engine-not-disposed')
        u._uch=None; self.u=None; self.closed=True; self.poisoned=True
        if errors:
            raise ValueError('sprintf-reference-hook-cleanup-failed') from errors[0]


def audit_matrix(calc, std, deviceid, output):
    source=audit_callback_sources(calc,std,deviceid)
    output.mkdir()
    values=(-99.9995,-12.5,-9.995,-1.999,-1.25,-.125,-.001,-0.,0.,.001,.005,.125,.5,.995,1.,1.25,1.999,9.995,12.5,99.9995)
    rows=[]; probe=CpuSprintfReference(calc,std,deviceid)
    try:
        for fmt in FORMATS:
            for value in values:
                data=struct.pack('<d',value); result=probe.format(fmt,data)
                comparison=f'{value:.{int(chr(fmt[2]))}f}'.encode()
                rows.append(dict(formatBytesHex=fmt.hex().upper(),inputDoubleHex=data.hex().upper(),
                    result=result,pythonComparisonHex=comparison.hex().upper(),
                    comparisonDifferent=result['outputBytesHex']!=comparison.hex().upper()))
    finally:
        probe.close()
    report=dict(kind='x431-original-sprintf-reference',schemaVersion=1,sourceAudit=source,
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        cases=rows,caseCount=len(rows),comparisonDifferent=sum(r['comparisonDifferent'] for r in rows),
        engineVersion='2.1.4',engineDisposed=True, **GUARDS)
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','deviceid','output-dir'):
        parser.add_argument('--'+key,required=True,type=Path)
    a=parser.parse_args()
    report=audit_matrix(a.calc.read_bytes(),a.std.read_bytes(),a.deviceid.read_bytes(),a.output_dir)
    print(json.dumps(dict(cases=report['caseCount'],comparisonDifferent=report['comparisonDifferent'],**GUARDS)))


if __name__ == '__main__':
    main()
