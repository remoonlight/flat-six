"""Explicit offline ARM reference for the pinned X431 bit codec.

Runs original bit/reading/C++ instructions inside Unicorn virtual memory. Only
bounded libc memory/format operations are substituted. Unknown callouts stop.
No host ELF loading, Android/JNI workflow, transport or vehicle qualification.
Unicorn 2.1.4 is an optional research dependency, never an application driver.
"""
from pathlib import Path
import argparse, struct, hashlib, json, collections, time

from .x431_native_binding import _Elf

SOURCE_BYTES = 1087328
REGIONS = (
    ('_Z23GetBitChangAnsDataExBitdPhiii', 0x26b1c, 496, '86953f9b5a30dcd024c814acbab90de35f8adf52e8f813c6ba4f66e22cac1472'),
    ('_Z18GetBitChangAnsDatadPhiiiSs', 0x26d0c, 808, '4a0e3739d01d5aa1d76bdc1d9237876e5346f150220a3986dd836d7514bcfbc9'),
    ('_Z11GetX_ModifySsiiiPh', 0x37a28, 776, '6f4bb4f06653feb8999b7afa8e3cb95fbb75b05736a4e9624fd932add5f4c785'),
)
GUARDS = dict(cpuEmulationOnly=True, hostElfLoaded=False, actualDynamicBindingQualified=False,
    nativeEditingBehaviorVerified=False, vehicleVerified=False, codingWriteQualified=False,
    codingRestoreQualified=False, executionEnabled=False, writePayload=None)


def _check(condition, detail):
    if not condition:
        raise ValueError(detail)


def source_audit(blob):
    if type(blob) is not bytes or len(blob) != SOURCE_BYTES or hashlib.sha256(blob).hexdigest() != SHA:
        raise ValueError('cpu-reference-source-unreviewed')
    elf = _Elf(blob)
    rows = []
    for name, start, size, sha in REGIONS:
        symbols = [x for x in elf.symbols if x['name'] == name and x['type'] == 2 and x['section']]
        if (len(symbols) != 1 or symbols[0]['va'] != (start|1) or symbols[0]['bytes'] != size
                or hashlib.sha256(elf.raw(start, size)).hexdigest() != sha):
            raise ValueError('cpu-reference-function-unreviewed')
        rows.append(dict(name=name, startVa=start, bytes=size, sha256=sha))
    return elf, rows


def validate_case(original, bit, width, value=None, hl=1, ex=False):
    if type(original) is not bytes or not 1 <= len(original) <= 1024:
        raise ValueError('cpu-reference-buffer-invalid')
    if type(bit) is not int or not 0 <= bit <= 7 or type(width) is not int or not 1 <= width <= 32:
        raise ValueError('cpu-reference-layout-invalid')
    if type(hl) is not int or hl not in (0,1) or type(ex) is not bool:
        raise ValueError('cpu-reference-byte-order-invalid')
    if len(original) < (bit+width+7)//8 or ex and bit+width > 8:
        raise ValueError('cpu-reference-truncated-or-not-single-byte')
    if value is not None and (type(value) is not int or not 0 <= value < 1<<width):
        raise ValueError('cpu-reference-value-invalid')


SHA = '40a4bf4ebff5da4f5d3093e469287881a1aee876b483416ef0bdc8d9e8edc543'
STACK, HEAP, DATA, IMPORTS, STOP = 0x2000000, 0x2100000, 0x2500000, 0x3000000, 0x3100000


class CpuReference:
    def __init__(self, blob):
        # Verify the exact original before importing the optional CPU engine.
        elf, self.function_regions = source_audit(blob)
        try:
            import unicorn as engine
            from unicorn import arm_const
        except ImportError as error:
            raise ValueError('cpu-reference-private-unicorn-2.1.4-required') from error
        if engine.__version__ != '2.1.4':
            raise ValueError('cpu-reference-engine-version-unreviewed')
        # These constants are needed only during explicit research execution.
        for module in (engine, arm_const):
            globals().update({k:v for k,v in vars(module).items() if k.startswith('UC_')})
        self.elf = elf
        self.engine_version = engine.__version__
        u = self.u = engine.Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        for p in elf.ph:
            if p[0] == 1:
                start = p[2] & ~4095
                end = (p[2] + p[5] + 4095) & ~4095
                perms = (UC_PROT_READ if p[6] & 4 else 0) | (UC_PROT_WRITE if p[6] & 2 else 0) | (UC_PROT_EXEC if p[6] & 1 else 0)
                u.mem_map(start, end-start, perms)
                u.mem_write(p[2], blob[p[1]:p[1]+p[4]])
        u.mem_map(STACK, 0x100000, UC_PROT_READ | UC_PROT_WRITE)
        u.mem_map(HEAP, 0x400000, UC_PROT_READ | UC_PROT_WRITE)
        u.mem_map(DATA, 0x100000, UC_PROT_READ | UC_PROT_WRITE)
        u.mem_map(IMPORTS, 0x100000, UC_PROT_READ | UC_PROT_EXEC)
        u.mem_map(STOP, 4096, UC_PROT_READ | UC_PROT_EXEC)
        self.hooks = {}
        imports = {}
        for s in elf.symbols:
            if not s['section'] and s['name']:
                at = IMPORTS + len(imports)*16
                imports[s['name']] = at | 1 if s['type'] == 2 else DATA + 0x1000 + len(imports)*16
                if s['type'] == 2:
                    self.hooks[at] = s['name']
                    u.mem_write(at, bytes.fromhex('7047'))
        self.imports = imports
        for at,r in elf.relocations.items():
            typ, sym = r['type'], r['symbol']
            old = self.uint(at)
            addr = sym['va'] if sym['section'] else imports.get(sym['name'], 0)
            if typ == 23:
                value = old
            elif typ in (21,22):
                value = addr
            elif typ == 2:
                value = old + addr
            else:
                raise ValueError(('relocation-unreviewed', typ))
            self.word(at, value)
        self.word(imports['__stack_chk_guard'], 0x59712486)
        u.reg_write(UC_ARM_REG_C1_C0_2, 0x00f00000)
        u.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        self.next_alloc = HEAP
        self.allocations = {}
        self.trace = collections.Counter()
        self.last = collections.deque(maxlen=24)
        u.hook_add(UC_HOOK_CODE, self.code)

    def uint(self, at):
        return struct.unpack('<I', self.u.mem_read(at,4))[0]

    def word(self, at, value):
        self.u.mem_write(at, struct.pack('<I', value & 0xffffffff))

    def alloc(self, size):
        _check(0 <= size <= 0x10000, 'cpu-reference-invariant-failed')
        at = self.next_alloc
        self.next_alloc += (size+31)&~15
        _check(self.next_alloc < HEAP + 0x400000, 'cpu-reference-invariant-failed')
        self.allocations[at] = size
        self.u.mem_write(at, bytes(max(1,size)))
        return at

    def cstr(self, at):
        data = bytearray()
        for i in range(1024):
            b = self.u.mem_read(at+i, 1)[0]
            if not b:
                return bytes(data)
            data.append(b)
        raise ValueError('cstring-unbounded')

    def code(self, u, address, size, _):
        self.last.append(address)
        name = self.hooks.get(address)
        if name is None:
            # Original bit functions, local PLT and embedded C++ runtime only.
            if not (0x26b1c <= address < 0x27000 or 0x37a28 <= address < 0x37d10 or 0x1df00 <= address < 0x20700 or 0xac000 <= address < 0xd6000):
                raise ValueError(('code-range-unreviewed', hex(address)))
            self.trace['originalInstructions'] += 1
            if self.trace['originalInstructions'] % 1024 == 0:
                _check(time.monotonic() < self.deadline, 'cpu-reference-wall-budget-exhausted')
            return
        a,b,c,d = (u.reg_read(reg) for reg in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3))
        self.trace[name] += 1
        result = 0
        if name == 'malloc':
            result = self.alloc(a)
        elif name == 'free':
            if a:
                _check(a in self.allocations, ('free-unknown',hex(a)))
                del self.allocations[a]
        elif name in ('memcpy','memmove','__aeabi_memcpy','__aeabi_memcpy4','__aeabi_memcpy8'):
            _check(c <= 0x10000, 'cpu-reference-invariant-failed')
            if c:
                u.mem_write(a, bytes(u.mem_read(b,c)))
            result = a
        elif name == 'memset':
            _check(c <= 0x10000, 'cpu-reference-invariant-failed')
            if c:
                u.mem_write(a, bytes([b&255])*c)
            result = a
        elif name in ('__aeabi_memclr','__aeabi_memclr4','__aeabi_memclr8'):
            _check(b <= 0x10000, 'cpu-reference-invariant-failed')
            if b:
                u.mem_write(a, bytes(b))
            result = a
        elif name == 'strtoul':
            _check(c == 2 and b == 0, 'cpu-reference-invariant-failed')
            text = self.cstr(a)
            _check(text and set(text) <= {48,49}, 'cpu-reference-invariant-failed')
            result = int(text,2)
            _check(result <= 0xffffffff, 'cpu-reference-invariant-failed')
        elif name == 'memcmp':
            _check(c <= 0x10000, 'cpu-reference-invariant-failed')
            x,y = bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c))
            result = (x>y)-(x<y)
        elif name == 'strlen':
            result = len(self.cstr(a))
        elif name == 'sprintf':
            _check(self.cstr(b) == b'%02X' and c <= 255, 'cpu-reference-invariant-failed')
            text = f'{c:02X}'.encode()
            u.mem_write(a, text+b'\0')
            result = len(text)
        else:
            raise ValueError(('import-call-unreviewed', name, [hex(x) for x in (a,b,c,d)]))
        u.reg_write(UC_ARM_REG_R0, result & 0xffffffff)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def run(self, original, bit, width, value, hl=1, ex=False):
        validate_case(original, bit, width, value, hl, ex)
        if value is None:
            raise ValueError('cpu-reference-value-required')
        u = self.u
        self.trace.clear(); self.last.clear()
        self.next_alloc = HEAP; self.allocations.clear()
        u.mem_write(DATA, original)
        # Old GNU string representation: length, capacity, refcount, chars.
        rep = DATA + 0x20000
        u.mem_write(rep, struct.pack('<III',1,1,0) + str(hl).encode() + b'\0')
        obj = DATA + 0x20100
        self.word(obj, rep+12)
        sp = STACK+0x80000
        u.mem_write(sp, struct.pack('<III',bit,width,obj))
        lo,hi = struct.unpack('<II',struct.pack('<d',float(value)))
        for reg, v in ((UC_ARM_REG_R0,lo),(UC_ARM_REG_R1,hi),(UC_ARM_REG_R2,DATA),(UC_ARM_REG_R3,0),(UC_ARM_REG_SP,sp),(UC_ARM_REG_LR,STOP|1)):
            u.reg_write(reg,v)
        entry = 0x26b1d if ex else 0x26d0d
        self.deadline = time.monotonic()+1
        try:
            u.emu_start(entry, STOP, count=300_000)
        except Exception as error:
            raise RuntimeError({'error':str(error),'pc':hex(u.reg_read(UC_ARM_REG_PC)),'recent':[hex(x) for x in self.last], 'trace':dict(self.trace)}) from error
        _check(u.reg_read(UC_ARM_REG_PC) == STOP, 'source-did-not-return')
        _check(u.reg_read(UC_ARM_REG_SP) == sp, 'stack-not-restored')
        return {'afterHex':bytes(u.mem_read(DATA,len(original))).hex().upper(),'return':u.reg_read(UC_ARM_REG_R0),'trace':dict(self.trace),'allocationsRemaining':len(self.allocations)}

    def read(self, original, bit, width, hl=1):
        validate_case(original, bit, width, hl=hl)
        u=self.u
        self.trace.clear();self.last.clear();self.next_alloc=HEAP;self.allocations.clear()
        u.mem_write(DATA,original)
        rep=DATA+0x20000;obj=DATA+0x20100;out=DATA+0x20200
        u.mem_write(rep,struct.pack('<III',1,1,0)+str(hl).encode()+b'\0');self.word(obj,rep+12)
        self.word(out,0x10ce10+12)
        sp=STACK+0x80000
        u.mem_write(sp,struct.pack('<II',bit,DATA))
        for reg,v in ((UC_ARM_REG_R0,out),(UC_ARM_REG_R1,obj),(UC_ARM_REG_R2,width),(UC_ARM_REG_R3,0),(UC_ARM_REG_SP,sp),(UC_ARM_REG_LR,STOP|1)):
            u.reg_write(reg,v)
        self.deadline = time.monotonic()+1
        try:
            u.emu_start(0x37a29,STOP,count=300_000)
        except Exception as error:
            raise RuntimeError({'error':str(error),'pc':hex(u.reg_read(UC_ARM_REG_PC)),'recent':[hex(x) for x in self.last],'trace':dict(self.trace)}) from error
        _check(u.reg_read(UC_ARM_REG_PC)==STOP and u.reg_read(UC_ARM_REG_SP)==sp, 'cpu-reference-invariant-failed')
        ptr=self.uint(out);text=self.cstr(ptr)
        _check(len(text)<=10 and text and set(text)<=set(b'0123456789ABCDEF'), 'cpu-reference-invariant-failed')
        result={'raw':int(text,16),'rawText':text.decode(),'inputAfterHex':bytes(u.mem_read(DATA,len(original))).hex().upper()}
        u.reg_write(UC_ARM_REG_R0,ptr-12);u.reg_write(UC_ARM_REG_R1,DATA+0x20300)
        u.reg_write(UC_ARM_REG_LR,STOP|1)
        self.deadline = time.monotonic()+1
        u.emu_start(0xc6309,STOP,count=300_000)
        _check(u.reg_read(UC_ARM_REG_PC)==STOP and u.reg_read(UC_ARM_REG_SP)==sp, 'cpu-reference-invariant-failed')
        result.update(trace=dict(self.trace),allocationsRemaining=len(self.allocations))
        return result


def untouched_bits_preserved(before, after, bit, width, hl):
    """Independent byte/bit positions, without the production integer patch."""
    used = (bit+width+7)//8
    masks = [0]*len(before)
    for k in range(bit, bit+width):
        byte = used-1-k//8 if hl or used==1 else k//8
        masks[byte] |= 1 << (k%8)
    return len(before)==len(after) and all(not ((a^b) & (~m & 255)) for a,b,m in zip(before,after,masks,strict=True))


def audit_matrix(blob, output):
    """Run the complete bounded layout matrix, not Android or vehicle writes."""
    from .x431_values import insert_raw, extract_raw
    output = Path(output)
    if output.exists():
        raise ValueError('cpu-reference-fresh-output-required')
    probe = CpuReference(blob)
    output.mkdir(parents=True)
    counts, trace = collections.Counter(), collections.Counter()
    layout_counts = collections.Counter()
    examples = []
    start = time.monotonic()
    with (output/'cases.jsonl').open('w',encoding='utf8',newline='\n') as stream:
        for width in range(1,33):
            for bit in range(8):
                for hl in (0,1):
                    mask = (1<<width)-1
                    values = sorted({0,1,mask,mask//2,0xa5a5a5a5 & mask})
                    for original in (bytes.fromhex('A5BCD1E2F387965A'),bytes.fromhex('123456789ABCDEF0')):
                        for value in values:
                            source = probe.run(original,bit,width,value,hl)
                            trace.update(source['trace'])
                            parsed = dict(bitLength=width,highLow=hl,bitMask=0)
                            project = insert_raw(parsed,original,0,bit,value)
                            _check(project['ok'],'cpu-reference-project-patch-failed')
                            native = bytes.fromhex(source['afterHex'])
                            matched = native == project['after']
                            _check(source['allocationsRemaining']==0,'cpu-reference-source-allocation-leak')
                            _check(source['return']==(width+bit+7)//8,'cpu-reference-source-return-mismatch')
                            preserved = untouched_bits_preserved(original,native,bit,width,hl)
                            _check(untouched_bits_preserved(original,project['after'],bit,width,hl),'cpu-reference-project-collateral-change')
                            reads = {}
                            for name,data in (('before',original),('sourceAfter',native),('projectAfter',project['after'])):
                                read = probe.read(data,bit,width,hl)
                                trace.update(read['trace'])
                                _check(read['allocationsRemaining']==0,'cpu-reference-read-allocation-leak')
                                decoded = extract_raw(parsed,data,0,bit)
                                _check(decoded['ok'] and decoded['raw']==read['raw'],'cpu-reference-source-extraction-mismatch')
                                reads[name] = read['raw']
                                counts['originalReadCalls'] += 1
                            source_target = reads['sourceAfter']==value
                            _check(reads['projectAfter']==value,'cpu-reference-project-native-readback-mismatch')
                            counts['cases'] += 1
                            counts['sourcePatchMatchesProject' if matched else 'sourcePatchDiffersProject'] += 1
                            counts['sourceTargetMatched' if source_target else 'sourceTargetDifferent'] += 1
                            counts['sourceUntouchedBitsPreserved' if preserved else 'sourceCollateralBitsChanged'] += 1
                            counts['projectNativeReadbackMatched'] += 1
                            counts['projectUntouchedBitsPreserved'] += 1
                            if not matched:
                                layout_counts[(width,bit,hl)] += 1
                            row = dict(width=width,bitOffset=bit,highLow=hl,raw=value,
                                beforeHex=original.hex().upper(),sourceAfterHex=source['afterHex'],
                                projectAfterHex=project['after'].hex().upper(),sourcePatchMatched=matched,
                                sourceTargetMatched=source_target,sourceUntouchedBitsPreserved=preserved,
                                originalReadValues=reads,sourceReturnBytes=source['return'])
                            stream.write(json.dumps(row,separators=(',',':'))+'\n')
                            if not source_target and len(examples)<3:
                                examples.append(row)
    # ABI objects must also stay outside larger original blocks, not just the
    # eight-byte layout fixtures. Exercise all documented input length bounds.
    large_cases = []
    for size in (256,512,1024):
        original = bytes((i*37+19)&255 for i in range(size))
        for bit,width,hl,value in ((2,12,1,0xABC),(0,32,0,0x81234567),(7,9,1,0x101)):
            source = probe.run(original,bit,width,value,hl)
            native = bytes.fromhex(source['afterHex'])
            _check(native[5:]==original[5:],'cpu-reference-abi-overlaps-original-buffer')
            _check(source['allocationsRemaining']==0,'cpu-reference-large-allocation-leak')
            parsed = dict(bitLength=width,highLow=hl,bitMask=0)
            project = insert_raw(parsed,original,0,bit,value)
            read = probe.read(project['after'],bit,width,hl)
            _check(read['raw']==value and read['allocationsRemaining']==0,'cpu-reference-large-readback-mismatch')
            large_cases.append(dict(originalBytes=size,bitOffset=bit,bitLength=width,highLow=hl,
                raw=value,sourceTrailingBytesUnchanged=True,projectNativeReadbackMatched=True))
    result = dict(kind='x431-original-bit-codec-cpu-audit',schemaVersion=1,
        sourceSha256=SHA,functionRegions=probe.function_regions,engineVersion=probe.engine_version,
        layouts=512,widthRange=[1,32],bitOffsetRange=[0,7],highLowValues=[0,1],
        rawInputs='unsigned exact integers; mask=0; two synthetic full buffers',
        counts=dict(counts),foundationCalls=dict(trace),elapsedSeconds=time.monotonic()-start,
        differingLayouts=[dict(width=w,bitOffset=b,highLow=h,cases=n) for (w,b,h),n in sorted(layout_counts.items())],
        targetDifferenceExamples=examples,originalInstructionsExecutedInEmulator=True,
        largeOriginalBufferChecks=large_cases,
        dynamicUiOrCallerWorkflowExecuted=False,**GUARDS)
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calc-source',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args(argv)
    if args.calc_source.stat().st_size != SOURCE_BYTES:
        raise ValueError('cpu-reference-source-length-unreviewed')
    paths = [Path(__file__).resolve(),Path(__file__).with_name('x431_values.py'),
             Path(__file__).with_name('x431_formula.py'),Path(__file__).with_name('x431_native_binding.py')]
    code = [dict(name=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
    result = audit_matrix(args.calc_source.read_bytes(),args.output_dir)
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=r['sha256'] for p,r in zip(paths,code,strict=True)):
        raise ValueError('cpu-reference-code-changed')
    rows = [dict(name=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(args.output_dir.iterdir())]
    manifest = dict(sourceSha256=SHA,code=code,outputs=rows,**GUARDS)
    (args.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(ok=True,counts=result['counts'],elapsedSeconds=result['elapsedSeconds'],**GUARDS)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
