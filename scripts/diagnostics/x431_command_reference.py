"""Explicit isolated COMMAND lookup reference; no host files or vehicle I/O.

Execute the pinned STD wrapper, IdSearch, decrypt and postprocessor with a
virtual GAG file and explicitly initialized 514-byte output storage. Foundation
strlen is bounded to observed bytes; unknown heap tails are never padded. A
successful synthetic tail does not qualify FILE's dynamic IdSearch binding or
an actual caller/heap. An interrupted engine cannot be reused.
"""
from pathlib import Path
import argparse, collections, hashlib, json, struct, time
from .x431_language_reference import CpuLanguageReference, CTX, SEC, FILE_TOKEN, HEAP, cpu
from .x431_command_origin import decode_command_catalog, COMMAND_GAG_SHA256
from .x431_version_response import VERSION_GUARDS
from scripts.x431_re.ggp_index import parse_header
from scripts.x431_re.gag_codec import parse_text_gag

GUARDS = {**VERSION_GUARDS, 'vendorCodeExecuted': True, 'originalInstructionsExecutedInCpu': True,
    'cpuEmulationOnly': True, 'virtualFileOnly': True, 'vehicleVerified': False,
    'liveVerified': False, 'writePayload': None,
    'hostElfLoaded': False, 'headOpened': False, 'nativeFileOpenExecuted': False,
    'nativeFileCloseExecuted': False, 'actualHeapBufferQualified': False,
    'actualDynamicLookupBindingQualified': False, 'actualCommandLookupRuntimeQualified': False}

class CpuCommandReference(CpuLanguageReference):
 def __init__(self,calc,std,language):
  super().__init__(calc,std,language)
  sections=[s for s in parse_header(language)['entries'] if s['name_ascii']=='COMMAND.GAG']
  cpu._check(len(sections)==1,'command-section-not-unique');s=sections[0]
  raw=language[s['offset']:s['offset']+s['size']];cpu._check(hashlib.sha256(raw).hexdigest()==COMMAND_GAG_SHA256,'command-section-hash')
  self.section=dict(offset=s['offset'],bytes=s['size']);self.state=parse_text_gag(raw)
  self.records={r['commandId']:r for r in decode_command_catalog(language)['records']};self.strlen_observations=[]
 def _writable(self,address,size):
  return type(size) is int and 0<=size<=1024 and (cpu.STACK<=address and address+size<=cpu.STACK+0x100000 or cpu.DATA<=address and address+size<=cpu.DATA+514 or any(a<=address and address+size<=a+n for a,n in self.allocations.items()))
 def _cstring(self,address):
  cpu._check(cpu.DATA<=address<cpu.DATA+514,'command-strlen-address')
  raw=bytes(self.u.mem_read(address,cpu.DATA+514-address));at=raw.find(b'\0')
  self.strlen_observations.append(dict(offset=address-cpu.DATA,knownBytes=len(raw),terminatorOffset=at if at>=0 else None))
  cpu._check(at>=0,'command-strlen-outside-observed-buffer');return raw[:at]
 def code(self,u,address,size,userdata):
  name=self.hooks.get(address);r=self.r
  if name=='retained-language-open-model':
   cpu._check(u.reg_read(r.UC_ARM_REG_R0)==cpu.DATA+0x10000 and not self.opened,'command-file-open-model')
   self.opened=True;self.trace[name]+=1;u.reg_write(r.UC_ARM_REG_R0,CTX);u.reg_write(r.UC_ARM_REG_PC,u.reg_read(r.UC_ARM_REG_LR));return
  if name=='fread':
   a,b,c,d=[u.reg_read(reg) for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3)];n=b*c
   cpu._check(self.opened and d==FILE_TOKEN and 0<b<=512 and 0<c<=512 and n<=512 and self.section['offset']<=self.cursor and self.cursor+n<=self.section['offset']+self.section['bytes'],'command-file-read-range')
   self._put(a,self.file_bytes[self.cursor:self.cursor+n]);self.reads.append(dict(offset=self.cursor,bytes=n));self.cursor+=n;self.trace[name]+=1
   u.reg_write(r.UC_ARM_REG_R0,c);u.reg_write(r.UC_ARM_REG_PC,u.reg_read(r.UC_ARM_REG_LR));return
  return super().code(u,address,size,userdata)
 def command(self,command_id,tail):
  cpu._check(not self.closed and not self.poisoned and not self.opened and not self.allocations,'command-prior-state')
  cpu._check(command_id in self.records and tail in ('zero','sentinel-nul','sentinel'),'command-input')
  u=self.u;r=self.r;sp=cpu.STACK+0x80000;before=bytes(514) if tail=='zero' else b'\xa5'*513+(b'\0' if tail=='sentinel-nul' else b'\xa5')
  context=bytearray(self.state['ctx']);struct.pack_into('<IIf',context,0,FILE_TOKEN,self.state['index_off'],self.state['ver']);struct.pack_into('<IIII',context,0x110,self.state['nsec'],SEC,self.section['offset'],self.section['bytes'])
  u.mem_write(CTX,bytes(context));u.mem_write(SEC,self.state['sec']);u.mem_write(cpu.DATA,before+b'\x6e'*510)
  self.cursor=self.section['offset'];self.next_alloc=HEAP;self.trace.clear();self.reads.clear();self.seen.clear();self.strlen_observations.clear();u.mem_write(cpu.DATA+0x10000,b'COMMAND.GAG\0');u.mem_write(cpu.DATA+0x11000,command_id.to_bytes(4,'big'))
  saved=[u.reg_read(getattr(r,'UC_ARM_REG_R'+str(n))) for n in range(4,12)]
  for reg,v in ((r.UC_ARM_REG_R0,cpu.DATA+0x11000),(r.UC_ARM_REG_R1,cpu.DATA),(r.UC_ARM_REG_R2,512),(r.UC_ARM_REG_R3,cpu.DATA+0x10000),(r.UC_ARM_REG_SP,sp),(r.UC_ARM_REG_LR,cpu.STOP|1),(r.UC_ARM_REG_FPSCR,0)):u.reg_write(reg,v)
  self.deadline=time.monotonic()+1;error=None
  try:
   u.emu_start(0x25575,cpu.STOP,count=200_000)
   cpu._check(u.reg_read(r.UC_ARM_REG_PC)==cpu.STOP and u.reg_read(r.UC_ARM_REG_SP)==sp,'command-not-returned')
   expected=bytes.fromhex(self.records[command_id]['sourceDescriptorHex']);actual=bytes(u.mem_read(cpu.DATA,1024))
   cpu._check(u.reg_read(r.UC_ARM_REG_R0)==len(expected) and actual[:len(expected)]==expected and actual[len(expected):]==(before+b'\x6e'*510)[len(expected):] and not self.allocations and not self.opened,'command-output-or-cleanup')
   cpu._check(saved==[u.reg_read(getattr(r,'UC_ARM_REG_R'+str(n))) for n in range(4,12)],'command-saved-registers')
   cpu._check(all(a in self.seen for a in (0x25574,0x251c0,0x25614,0x32bc8,0x32e00)),'command-original-chain')
  except Exception as e:self.poisoned=True;error=str(e)
  return dict(commandIdHex=f'{command_id:08X}',tail=tail,error=error,returnValue=u.reg_read(r.UC_ARM_REG_R0),outputHex=bytes(u.mem_read(cpu.DATA,len(bytes.fromhex(self.records[command_id]['sourceDescriptorHex'])))).hex(),strlen=self.strlen_observations.copy(),trace=dict(self.trace),originalFunctionEntries=self.seen.copy(),allocationsRemaining=len(self.allocations),virtualCloseCallbackObserved=self.trace.get('retained-language-close-model',0)==1, enginePoisoned=self.poisoned, **GUARDS)


def audit_matrix(calc, std, language, output, *, budget_s=600):
    if type(budget_s) not in (int, float) or not 1 <= budget_s <= 1800:
        raise ValueError('command-reference-total-budget-invalid')
    output = Path(output)
    if output.exists():
        raise ValueError('command-reference-fresh-output-required')
    catalog = decode_command_catalog(language)
    output.mkdir(parents=True)
    started = time.monotonic(); counts = collections.Counter(); disposed = 0; reference = None
    files = output/'cases.jsonl'
    try:
        with files.open('x',encoding='utf8',newline='\n') as stream:
            for tail in ('zero','sentinel-nul','sentinel'):
                for row in catalog['records']:
                    cpu._check(time.monotonic()-started < budget_s,'command-reference-total-budget-exhausted')
                    if reference is None:
                        reference = CpuCommandReference(calc,std,language)
                    body = bytes.fromhex(row['sourceDescriptorHex'])
                    result = reference.command(row['commandId'],tail)
                    no_terminator = tail=='sentinel' and b'\0' not in body
                    expected_error = 'command-strlen-outside-observed-buffer' if no_terminator else None
                    cpu._check(result['error']==expected_error,'command-reference-unexpected-outcome:'+str(result['error']))
                    cpu._check(result['outputHex']==body.hex(),'command-reference-descriptor-mismatch')
                    cpu._check(result['allocationsRemaining']==0,'command-reference-allocation-leak')
                    cpu._check(result['enginePoisoned']==no_terminator and result['virtualCloseCallbackObserved']==(not no_terminator),
                        'command-reference-lifetime-outcome-mismatch')
                    stream.write(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':'))+'\n')
                    counts[(tail,'bounded-stop' if no_terminator else 'returned')]+=1
                    if reference.poisoned:
                        reference.close();reference=None;disposed+=1
                if reference is not None:
                    reference.close();reference=None;disposed+=1
                stream.flush()
                (output/'progress.json').write_text(json.dumps(dict(completedCases=sum(counts.values()),lastCompletedTail=tail,enginesDisposed=disposed),indent=2)+'\n',encoding='utf8',newline='\n')
    finally:
        if reference is not None:
            reference.close();disposed+=1
    with files.open('rb') as stream:
        cases_sha = hashlib.file_digest(stream,'sha256').hexdigest()
    report = dict(kind='x431-original-command-buffer-reference',schemaVersion=1,
        sourceRecords=catalog['recordCount'],caseCount=sum(counts.values()),
        counts={tail:{status:counts[tail,status] for status in ('returned','bounded-stop')} for tail in ('zero','sentinel-nul','sentinel')},
        casesSha256=cases_sha,enginesDisposed=disposed,engineVersion='2.1.4',
        sourceHashes=dict(calc=hashlib.sha256(calc).hexdigest(),std=hashlib.sha256(std).hexdigest(),language=hashlib.sha256(language).hexdigest(),commandGag=COMMAND_GAG_SHA256),
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        qualificationScope='pinned STD wrapper/IdSearch/decrypt/postprocessor; bounded virtual file/memory/strlen and explicit 514-byte synthetic tails; not FILE caller or actual heap',
        sourceDescriptorsAgreeWithProject=True,nativeFileCallerCopyQualified=False,
        sourceUnobservedHeapQualificationPromoted=False,**GUARDS)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=True,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('calc','std','language'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--budget-seconds',type=int,default=600)
    args=parser.parse_args()
    result=audit_matrix(args.calc.read_bytes(),args.std.read_bytes(),args.language.read_bytes(),args.output_dir,budget_s=args.budget_seconds)
    print(json.dumps(dict(caseCount=result['caseCount'],counts=result['counts'],enginesDisposed=result['enginesDisposed'],**GUARDS)))


if __name__=='__main__':
    main()
