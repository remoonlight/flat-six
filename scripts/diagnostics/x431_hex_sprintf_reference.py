"""Isolated original DEVICEID formatter for the retained ' %02X' byte format.

This is an explicit reference substitution, not the CALC Android libc sprintf
binding. Original instructions emit every byte into the virtual stdio sink.
"""
import hashlib
import time
from . import x431_sprintf_reference as core
from .x431_integer_sprintf_reference import INTEGER_REGIONS
from .x431_callback_source import audit_callback_sources
from .x431_native_binding import _Elf

FORMAT=b' %02X'
GUARDS={**core.GUARDS,'isolatedHexByteSprintfReference':True,
    'actualAndroidLibcSprintfExecuted':False}


def validate_byte(fmt,value,fpscr=0):
    if type(fmt) is not bytes or fmt!=FORMAT:
        raise ValueError('hex-sprintf-format-unreviewed')
    if type(value) is not int or not 0<=value<=255:
        raise ValueError('hex-sprintf-byte-required')
    if type(fpscr) is not int or not 0<=fpscr<=0xffffffff or fpscr&0x00c00000:
        raise ValueError('hex-sprintf-rounding-mode-unreviewed')


class CpuHexSprintfReference(core.CpuSprintfReference):
    def __init__(self,calc,std,deviceid):
        audit_callback_sources(calc,std,deviceid)
        elf=_Elf(deviceid)
        for name,start,size,sha in INTEGER_REGIONS:
            symbols=[s for s in elf.symbols if s['name']==name and s['type']==2 and s['section']]
            core._check(len(symbols)==1 and symbols[0]['va']==start and symbols[0]['bytes']==size
                and hashlib.sha256(elf.raw(start,size)).hexdigest()==sha,'hex-sprintf-division-region-unreviewed')
        super().__init__(calc,std,deviceid)
        self.allowed.extend((s,s+n) for _,s,n,_ in INTEGER_REGIONS)

    def code(self,u,address,size,userdata):
        if address==0xc6ec:
            core._check(u.reg_read(self.r.UC_ARM_REG_R1)==16,'hex-sprintf-divisor-unreviewed')
            self.trace['originalHexDivisions']+=1
        return super().code(u,address,size,userdata)

    def format_byte(self,fmt,value,fpscr=0):
        validate_byte(fmt,value,fpscr)
        core._check(not self.closed and not self.poisoned,'hex-sprintf-prior-outcome-unknown-or-closed')
        u=self.u;r=self.r;sp=core.STACK+0x80000;before=b'\xa5'*1024
        u.mem_write(core.DATA,before);u.mem_write(core.DATA+0x10000,fmt+b'\0')
        self.trace.clear();self.sink.clear()
        for reg,v in ((r.UC_ARM_REG_R0,core.DATA),(r.UC_ARM_REG_R1,core.DATA+0x10000),
                      (r.UC_ARM_REG_R2,value),(r.UC_ARM_REG_R3,0),(r.UC_ARM_REG_SP,sp),
                      (r.UC_ARM_REG_LR,core.STOP|1),(r.UC_ARM_REG_FPSCR,fpscr)):
            u.reg_write(reg,v)
        self.deadline=time.monotonic()+1
        try:
            u.emu_start(0x7dc9,core.STOP,count=100_000)
            core._check(u.reg_read(r.UC_ARM_REG_PC)==core.STOP and u.reg_read(r.UC_ARM_REG_SP)==sp,
                'hex-sprintf-return-or-stack-unreviewed')
            raw=bytes(u.mem_read(core.DATA,1024));out=raw.split(b'\0',1)[0]
            regs=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)]
            core._check(len(out)==3 and regs[0]==3 and self.sink==out and raw[4:]==before[4:],
                'hex-sprintf-output-invariant-failed')
            return dict(outputBytesHex=out.hex().upper(),returnValue=3,returnedRegisters=regs,
                returnedApsr=u.reg_read(r.UC_ARM_REG_APSR),returnedFpscr=u.reg_read(r.UC_ARM_REG_FPSCR),
                trace=dict(self.trace),**GUARDS)
        except Exception:
            self.poisoned=True;raise
