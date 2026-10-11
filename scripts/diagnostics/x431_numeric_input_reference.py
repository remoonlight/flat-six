"""Original X431 input caller with explicit C numeric foundation models.

Other original instruction/callback references remain unchanged. This models
C-locale values, prefix consumption and integer ERANGE; Android libc, errno
TLS, floating flags and the actual input UI are not qualified.
"""
from pathlib import Path
import argparse,collections,hashlib,json,time
from . import x431_input_reference as original
from . import x431_prompt_input_reference as previous
from . import x431_numeric_foundation as numeric
from .x431_language_source import audit_language_sources
from .x431_callback_source import audit_callback_sources

GUARDS={**previous.GUARDS,'cNumericFoundationModeled':True,
    'retainedAndroidLibcExecuted':False,'actualLibcVersionMatched':False,
    'androidErrnoTlsModified':False,'floatingExceptionFlagsModeled':False}


class CpuNumericInputReference(previous.CpuPromptInputReference):
    def __init__(self,calc,std,deviceid,language):
        self.numeric_events=[]
        super().__init__(calc,std,deviceid,language)

    def numeric_conversion_budget(self):
        return 256

    def code(self,u,address,size,userdata):
        name=self.hooks.get(address);base=original.base
        if name in ('strtod','strtol','strtoul','strtoll','strtoull'):
            a,b,c=[u.reg_read(reg) for reg in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2)]
            base._check(b==0,'numeric-input-endptr-unreviewed')
            budget=self.numeric_conversion_budget()
            base._check(type(budget) is int and 1<=budget<=8192,'numeric-input-conversion-budget-unreviewed')
            base._check(len(self.numeric_events)<budget,'numeric-input-conversion-budget-exhausted')
            data=self.cstr(a)
            try:
                if name=='strtod':
                    result=numeric.decimal_prefix(data,u.reg_read(base.UC_ARM_REG_FPSCR));words=result['returnedWords']
                else:
                    bits=64 if name.endswith('ll') else 32
                    result=numeric.integer_prefix(data,c,name in ('strtol','strtoll'),bits)
                    words=[result['valueBits']&0xffffffff]
                    if bits==64:words.append(result['valueBits']>>32)
            except ValueError as error:
                raise original.FoundationBoundary(dict(name=name,reason=str(error),inputBytesHex=data.hex().upper(),
                    stoppedBeforeConversion=True,conversionExecuted=False)) from error
            self.numeric_events.append(dict(name=name,sourceReturnVa=u.reg_read(base.UC_ARM_REG_LR)&~1,
                inputBytesHex=data.hex().upper(),result=result,foundationSubstitution=True))
            self.trace[name]+=1
            for reg,value in zip((base.UC_ARM_REG_R0,base.UC_ARM_REG_R1),words):u.reg_write(reg,value)
            u.reg_write(base.UC_ARM_REG_PC,u.reg_read(base.UC_ARM_REG_LR));return
        return super().code(u,address,size,userdata)

    def validate(self,formula,input_bytes,flag=0):
        self.numeric_events.clear();result=super().validate(formula,input_bytes,flag)
        return dict(result,numericConversions=self.numeric_events.copy(),**{k:v for k,v in GUARDS.items() if k not in result})


def audit_matrix(calc,std,deviceid,language,output):
    audit=audit_language_sources(calc,std,language);audit_callback_sources(calc,std,deviceid)
    output.mkdir();counts=collections.Counter();started=time.monotonic()
    with (output/'cases.jsonl').open('x',encoding='utf8',newline='\n') as out:
        for case in original.matrix_cases():
            p=CpuNumericInputReference(calc,std,deviceid,language)
            try:
                result=p.validate(case['formula'],bytes.fromhex(case['inputBytesHex']),case['flag'])
                counts['cases']+=1;counts['completed' if result['completed'] else result['boundaryKind']]+=1
                if result['completed']:counts['returnedZero' if result['returnValue']==0 else 'returnedOther']+=1
                else:counts[result['boundary']['name']]+=1
                for name,key in (('isolatedFloatSprintfCallouts','sprintfCallouts'),('isolatedLanguageCallouts','languageCallouts'),
                    ('isolatedIntegerSprintfCallouts','integerSprintfCallouts'),('modeledNumericConversions','numericConversions')):
                    counts[name]+=len(result[key])
                counts['modeledIntegerRangeErrors']+=sum(r['result'].get('overflow',False) for r in result['numericConversions'])
            finally:p.close()
            base=original.base;base._check(p.closed and all(getattr(p,k).closed for k in ('language','integer','sprintf')),
                'numeric-input-engines-not-disposed')
            result['allFourEnginesDisposed']=True;out.write(json.dumps(dict(case,result=result),separators=(',',':'))+'\n')
    names=('x431_numeric_input_reference.py','x431_numeric_foundation.py','x431_prompt_input_reference.py',
        'x431_language_reference.py','x431_integer_sprintf_reference.py','x431_language_source.py',
        'x431_input_callbacks_reference.py','x431_sprintf_reference.py','x431_callback_source.py',
        'x431_input_reference.py','x431_field_reference.py','x431_cpu_reference.py','x431_formula.py',
        'x431_values.py','x431_native_binding.py','coding_input_source.py')
    report=dict(kind='x431-input-numeric-foundation-reference',schemaVersion=1,sourceAudit=audit,
        upstreamNumericModelSources=numeric.UPSTREAM,counts=dict(counts),elapsedSeconds=round(time.monotonic()-started,5),
        code=[dict(name=n,sha256=hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()) for n in names],
        casesSha256=hashlib.sha256((output/'cases.jsonl').read_bytes()).hexdigest(),**GUARDS)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('calc','std','deviceid','language','output-dir'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();r=audit_matrix(a.calc.read_bytes(),a.std.read_bytes(),a.deviceid.read_bytes(),a.language.read_bytes(),a.output_dir)
    print(json.dumps(dict(counts=r['counts'],casesSha256=r['casesSha256'],**GUARDS)))


if __name__=='__main__':main()
