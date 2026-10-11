"""Original input caller with explicit original prompt/decimal callouts.

Four pinned language + formatting pairs, virtual language-file context, and
original STD/DEVICEID instructions. Actual Android callback slots stay zero.
"""
from pathlib import Path
import argparse,collections,hashlib,json,struct,time
from . import x431_input_callbacks_reference as previous
from .x431_language_reference import CpuLanguageReference
from .x431_integer_sprintf_reference import CpuIntegerSprintfReference
from .x431_language_source import PROMPTS, audit_language_sources
from .x431_callback_source import audit_callback_sources

PAIRS={0x342c8:0x342da,0x3454c:0x3455e,0x34854:0x34866,0x34b16:0x34b28}
IDS={r[0]:r[1] for r in PROMPTS}
GUARDS={**previous.GUARDS,'isolatedLanguagePromptCallouts':True,
    'languageContextSetupModeled':True,'actualLanguageFileStateQualified':False}


class CpuPromptInputReference(previous.CpuInputCallbacksReference):
    def __init__(self,calc,std,deviceid,language):
        self.language_audit=audit_language_sources(calc,std,language)
        audit_callback_sources(calc,std,deviceid)
        self.language_events=[];self.integer_events=[];self.pending_prompt=None
        super().__init__(calc,std,deviceid)
        try:
            self.language=CpuLanguageReference(calc,std,language)
            self.integer=CpuIntegerSprintfReference(calc,std,deviceid)
            for a,b in PAIRS.items():
                previous.original.base._check(self.callback_sites[a]['name']=='GetTextFromTextLib'
                    and self.callback_sites[b]['name']=='SPRINTF','prompt-input-call-pair-unreviewed')
        except Exception:
            self.close();raise

    def _return(self,u,address,result):
        base=previous.original.base
        for reg,value in zip((base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2,base.UC_ARM_REG_R3),
                             result['returnedRegisters']):u.reg_write(reg,value)
        u.reg_write(base.UC_ARM_REG_APSR,result['returnedApsr'])
        u.reg_write(base.UC_ARM_REG_FPSCR,result['returnedFpscr'])
        u.reg_write(base.UC_ARM_REG_LR,(address+2)|1);u.reg_write(base.UC_ARM_REG_PC,(address+2)|1)

    def code(self,u,address,size,userdata):
        row=self.callback_sites.get(address);base=previous.original.base
        if row and address in (*PAIRS,*PAIRS.values()):
            base._check(self.uint(row['objectVa'])==0,'prompt-input-runtime-slot-not-unbound')
            a,b,c,d=[u.reg_read(reg) for reg in (base.UC_ARM_REG_R0,base.UC_ARM_REG_R1,base.UC_ARM_REG_R2,base.UC_ARM_REG_R3)]
            sp=u.reg_read(base.UC_ARM_REG_SP)
            if address in PAIRS:
                base._check(row['name']=='GetTextFromTextLib' and a==IDS[address] and b==sp+0x108
                    and c==200 and base.STACK<=b and b+200<=base.STACK+0x100000
                    and self.pending_prompt is None,'prompt-input-language-arguments-unreviewed')
                result=self.language.lookup(a,c);data=bytes.fromhex(result['payloadHex'])
                u.mem_write(b,data);self.pending_prompt=(PAIRS[address],b,data[:-1])
                self.language_events.append(dict(callVa=address,result=result,runtimeSlotValue=0,
                    isolatedCalloutSubstitution=True,actualRuntimeBinding=False))
            else:
                base._check(row['name']=='SPRINTF' and self.pending_prompt is not None
                    and self.pending_prompt[0]==address and self.pending_prompt[1]==b==sp+0x108
                    and a==sp+0xa0 and base.STACK<=a and a+50<=base.STACK+0x100000,
                    'prompt-input-format-pair-or-destination-unreviewed')
                fmt=self.cstr(b)
                base._check(fmt==self.pending_prompt[2],'prompt-input-language-format-changed')
                word=struct.pack('<I',c);fpscr=u.reg_read(base.UC_ARM_REG_FPSCR)
                result=self.integer.format_integer(fmt,word,fpscr);data=bytes.fromhex(result['outputBytesHex'])
                base._check(len(data)<50,'prompt-input-formatted-output-unbounded')
                u.mem_write(a,data+b'\0');self.pending_prompt=None
                self.integer_events.append(dict(callVa=address,formatBytesHex=fmt.hex().upper(),
                    argumentWordHex=word.hex().upper(),result=result,runtimeSlotValue=0,
                    isolatedCalloutSubstitution=True,actualRuntimeBinding=False))
            self._return(u,address,result);return
        return super().code(u,address,size,userdata)

    def validate(self,formula,input_bytes,flag=0):
        self.language_events.clear();self.integer_events.clear();self.pending_prompt=None
        result=super().validate(formula,input_bytes,flag)
        previous.original.base._check(not result['completed'] or self.pending_prompt is None,
            'prompt-input-pending-format-at-return')
        return dict(result,languageCallouts=self.language_events.copy(),
            integerSprintfCallouts=self.integer_events.copy(),**{k:v for k,v in GUARDS.items() if k not in result})

    def close(self):
        errors=[]
        for key in ('integer','language'):
            if hasattr(self,key):
                try:getattr(self,key).close()
                except Exception as e:errors.append(e)
        try:super().close()
        except Exception as e:errors.append(e)
        if errors:raise ValueError('prompt-input-engine-cleanup-failed') from errors[0]


def audit_matrix(calc,std,deviceid,language,output):
    audit=audit_language_sources(calc,std,language);audit_callback_sources(calc,std,deviceid)
    output.mkdir();counts=collections.Counter();started=time.monotonic()
    with (output/'cases.jsonl').open('x',encoding='utf8',newline='\n') as out:
        for case in previous.original.matrix_cases():
            p=CpuPromptInputReference(calc,std,deviceid,language)
            try:
                result=p.validate(case['formula'],bytes.fromhex(case['inputBytesHex']),case['flag'])
                counts['cases']+=1;counts['completed' if result['completed'] else result['boundaryKind']]+=1
                if result['completed']:counts['returnedZero' if result['returnValue']==0 else 'returnedOther']+=1
                else:counts[result['boundary']['name']]+=1
                for name,key in (('isolatedFloatSprintfCallouts','sprintfCallouts'),
                    ('isolatedLanguageCallouts','languageCallouts'),('isolatedIntegerSprintfCallouts','integerSprintfCallouts')):
                    counts[name]+=len(result[key])
            finally:p.close()
            previous.original.base._check(all(getattr(p,k).closed for k in ('language','integer','sprintf'))
                and p.closed,'prompt-input-engines-not-disposed')
            result['allFourEnginesDisposed']=True
            out.write(json.dumps(dict(case,result=result),separators=(',',':'))+'\n')
    names=('x431_prompt_input_reference.py','x431_language_reference.py','x431_integer_sprintf_reference.py',
        'x431_language_source.py','x431_input_callbacks_reference.py','x431_sprintf_reference.py',
        'x431_callback_source.py','x431_input_reference.py','x431_field_reference.py','x431_cpu_reference.py',
        'x431_formula.py','x431_values.py','x431_native_binding.py','coding_input_source.py')
    report=dict(kind='x431-input-original-prompt-reference',schemaVersion=1,sourceAudit=audit,
        counts=dict(counts),elapsedSeconds=round(time.monotonic()-started,5),
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
