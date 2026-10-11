"""Original input caller with an explicit isolated finite SPRINTF callout.

The actual Android function-pointer slot remains unbound. Only two pinned
LINEAR float sites are substituted with isolated original DEVICEID execution.
Language, other formats and unknown behavior retain the original stop points.
"""
from pathlib import Path
import argparse, collections, hashlib, json, struct, time
from . import x431_input_reference as original
from .x431_callback_source import audit_callback_sources
from .x431_sprintf_reference import CpuSprintfReference, FORMATS

FLOAT_SITES = {0x31946: 0xb8, 0x3196c: 0x80}
FLOAT_ROUNDTRIPS = {0x31946: [0x31938,0x3193c], 0x3196c: [0x3195e,0x31962]}
GUARDS = {**original.GUARDS, 'isolatedFiniteSprintfReference': True,
    'actualDynamicBindingQualified': False, 'runtimeCallbacksBound': False}


class CpuInputCallbacksReference(original.CpuInputReference):
    def __init__(self, calc, std, deviceid):
        self.callback_audit = audit_callback_sources(calc,std,deviceid)
        self.sprintf_events = []
        self.float_roundtrip_seen = []
        super().__init__(calc)
        try:
            self.sprintf = CpuSprintfReference(calc,std,deviceid)
        except Exception:
            super().close()
            raise

    def code(self, u, address, size, userdata):
        if address in (0x31938,0x3193c,0x3195e,0x31962):
            self.float_roundtrip_seen.append(address)
        row=self.callback_sites.get(address)
        if row and row['name']=='SPRINTF' and address in FLOAT_SITES:
            base=original.base
            base._check(self.uint(row['objectVa'])==0, 'input-reference-runtime-slot-not-unbound')
            r0,r1,r2,r3=[u.reg_read(r) for r in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,
                                               base.UC_ARM_REG_R2,base.UC_ARM_REG_R3)]
            fmt=self.cstr(r1)
            if fmt not in FORMATS:
                return super().code(u,address,size,userdata)
            sp=u.reg_read(base.UC_ARM_REG_SP)
            base._check(r0==sp+FLOAT_SITES[address] and base.STACK<=r0
                       and r0+50<=base.STACK+0x100000, 'input-callback-destination-unreviewed')
            base._check(self.float_roundtrip_seen==FLOAT_ROUNDTRIPS[address],
                       'input-callback-float-conversions-not-observed')
            self.float_roundtrip_seen.clear()
            fpscr=u.reg_read(base.UC_ARM_REG_FPSCR)
            result=self.sprintf.format(fmt,struct.pack('<II',r2,r3),fpscr)
            data=bytes.fromhex(result['outputBytesHex'])
            base._check(len(data)<50, 'input-callback-output-unbounded')
            u.mem_write(r0,data+b'\0')
            self.sprintf_events.append(dict(callVa=address, formatBytesHex=fmt.hex().upper(),
                inputDoubleHex=struct.pack('<II',r2,r3).hex().upper(), result=result,
                callerFloat32RoundTripExecuted=True, callerFloat32InstructionSites=FLOAT_ROUNDTRIPS[address],
                runtimeSlotValue=0,
                isolatedCalloutSubstitution=True, actualRuntimeBinding=False))
            # The BLX itself is replaced. Reproduce its LR/continuation and the
            # observed original callee's volatile registers/flags, not a host ABI.
            for reg,value in zip((base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2,base.UC_ARM_REG_R3),
                                 result['returnedRegisters']):
                u.reg_write(reg,value)
            u.reg_write(base.UC_ARM_REG_APSR,result['returnedApsr'])
            u.reg_write(base.UC_ARM_REG_FPSCR,result['returnedFpscr'])
            u.reg_write(base.UC_ARM_REG_LR,(address+2)|1)
            u.reg_write(base.UC_ARM_REG_PC,(address+2)|1)
            return
        return super().code(u,address,size,userdata)

    def validate(self, formula, input_bytes, flag=0):
        self.sprintf_events.clear()
        self.float_roundtrip_seen.clear()
        result=super().validate(formula,input_bytes,flag)
        return dict(result, sprintfCallouts=self.sprintf_events.copy(), **{
            k:v for k,v in GUARDS.items() if k not in result})

    def close(self):
        try:
            if hasattr(self,'sprintf'):
                self.sprintf.close()
        finally:
            super().close()


def audit_matrix(calc, std, deviceid, output):
    audit=audit_callback_sources(calc,std,deviceid)
    output.mkdir(); counts=collections.Counter(); started=time.monotonic()
    with (output/'cases.jsonl').open('w',encoding='utf8',newline='\n') as out:
        for case in original.matrix_cases():
            probe=CpuInputCallbacksReference(calc,std,deviceid)
            try:
                result=probe.validate(case['formula'],bytes.fromhex(case['inputBytesHex']),case['flag'])
                counts['cases']+=1
                counts['completed' if result['completed'] else result['boundaryKind']]+=1
                if result['completed']:
                    counts['returnedZero' if result['returnValue']==0 else 'returnedOther']+=1
                else:
                    counts[result['boundary']['name']]+=1
                counts['isolatedSprintfCallouts']+=len(result['sprintfCallouts'])
            finally:
                probe.close()
            result['inputEngineDisposed']=True; result['sprintfEngineDisposed']=True
            out.write(json.dumps(dict(case,result=result),separators=(',',':'))+'\n')
    names=('x431_input_callbacks_reference.py','x431_sprintf_reference.py','x431_callback_source.py',
           'x431_input_reference.py','x431_field_reference.py','x431_cpu_reference.py',
           'x431_formula.py','x431_values.py','x431_native_binding.py','coding_input_source.py')
    report=dict(kind='x431-input-isolated-callback-reference',schemaVersion=1,
        counts=dict(counts),sourceAudit=audit,elapsedSeconds=round(time.monotonic()-started,5),
        code=[dict(name=n,sha256=hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()) for n in names],
        casesSha256=hashlib.sha256((output/'cases.jsonl').read_bytes()).hexdigest(), **GUARDS)
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','deviceid','output-dir'):
        parser.add_argument('--'+key,required=True,type=Path)
    a=parser.parse_args()
    report=audit_matrix(a.calc.read_bytes(),a.std.read_bytes(),a.deviceid.read_bytes(),a.output_dir)
    print(json.dumps(dict(counts=report['counts'],casesSha256=report['casesSha256'],**GUARDS)))


if __name__ == '__main__':
    main()
