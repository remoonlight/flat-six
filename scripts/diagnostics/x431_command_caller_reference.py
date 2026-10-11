"""Isolated FILE caller plus an explicit separate-CPU STD lookup bridge.

Pinned original instructions execute in two CPUs. Loader, file context,
allocation and cross-CPU pointer translation are declared models; this is not
an Android process binding, actual heap observation or vehicle transport.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import struct
import time
import zipfile

from .x431_loader_reference import CpuLoaderReference, HANDLE, GUARDS as LOADER_GUARDS
from .x431_native_binding import _Elf, SYMBOL_NAMES, FILE_SHA256
from .x431_command_reference import CpuCommandReference, CTX, SEC, FILE_TOKEN, HEAP
from .x431_command_origin import decode_command_catalog
from .x431_language_source import audit_language_sources
from .x431_sprintf_reference import STACK, DATA, STOP, _check

CALLER = (0x17748, 176, 'a16978d9471f4ddd35b935983cffd366f481123e3d45cb6fe04a26be76d0e988')
SCRATCH = DATA + 0x50000
CONTEXT_TOKEN = 0x71534354
PROFILES = ('complete', 'cached-known', 'library-failed', 'failed-first-symbol',
            'failed-open-symbol', 'failed-lookup-symbol', 'failed-after-lookup', 'cached-stale')
GUARDS = {**LOADER_GUARDS, 'separateCpuLookupBridgeModeled': True,
    'nativeCommandCallerInstructionsExecuted': True, 'actualHeapBufferQualified': False,
    'actualCommandLookupRuntimeQualified': False, 'singleAndroidProcessBindingQualified': False,
    'nativeFileOpenExecuted': False, 'nativeFileCloseExecuted': False}


def scratch_bytes(tail):
    _check(tail in ('zero', 'sentinel-nul', 'sentinel'), 'file-caller-tail-unreviewed')
    return bytes(514) if tail == 'zero' else b'\xa5' * 513 + (b'\0' if tail == 'sentinel-nul' else b'\xa5')


def selector_scope(portable, language):
    """Read and hash every source payload without extracting another catalog."""
    from .source_catalog import _code_status
    records = {r['commandIdHex']: r for r in decode_command_catalog(language)['records']}
    counts = collections.Counter()
    groups = missing = profiles = 0
    with zipfile.ZipFile(portable) as z:
        names = [n for n in z.namelist() if n.startswith('.local/diagnostics/x431-source-references/')
                 and n.endswith('/manifest.json')]
        _check(len(names) == 1, 'file-caller-source-snapshot-not-unique')
        prefix = names[0].removesuffix('manifest.json')
        raw = z.read(names[0])
        manifest = json.loads(raw)
        code = _code_status(manifest)
        _check(code['generatorCodeMatchesCurrent'] and not code['generatorCodeDrift'],
               'file-caller-source-generator-drift')
        for row in manifest['files']:
            name = row['name']
            _check(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name,
                   'file-caller-source-path-invalid')
            data = z.read(prefix + name)
            _check(len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'],
                   'file-caller-source-payload-drift')
            if not name.startswith('profiles/'):
                continue
            profiles += 1
            profile = json.loads(data)
            for source in profile['versionSelectorReadSources']:
                for route in source['routes']:
                    for group in route['requestGroups']:
                        groups += 1
                        missing += not group['sourceOccurrences']
                        for occurrence in group['sourceOccurrences']:
                            command = occurrence['sourceCommand']
                            record = records.get(command['commandIdHex'])
                            _check(record is not None and hashlib.sha256(bytes.fromhex(
                                record['sourceDescriptorHex'])).hexdigest() == command['decodedDescriptorSha256'],
                                'file-caller-selector-descriptor-drift')
                            counts[command['commandIdHex']] += 1
    _check(profiles > 0 and counts, 'file-caller-selector-scope-empty')
    return dict(sourceSnapshotId=prefix.rstrip('/').split('/')[-1],
        sourceManifestSha256=hashlib.sha256(raw).hexdigest(), sourceProfiles=profiles,
        sourceGroups=groups, groupsWithoutOccurrences=missing, sourceOccurrences=sum(counts.values()),
        commands=[dict(commandIdHex=k, occurrences=v) for k, v in sorted(counts.items())])


class CpuDirectCommandReference(CpuCommandReference):
    def search(self, command_id, before):
        _check(type(command_id) is int and 0 <= command_id <= 0xffffffff and type(before) is bytes
               and len(before) == 514, 'file-caller-direct-input-invalid')
        _check(not self.closed and not self.poisoned and not self.opened and not self.allocations,
               'file-caller-direct-prior-state')
        u, r = self.u, self.r
        sp = STACK + 0x80000
        context = bytearray(self.state['ctx'])
        struct.pack_into('<IIf', context, 0, FILE_TOKEN, self.state['index_off'], self.state['ver'])
        struct.pack_into('<IIII', context, 0x110, self.state['nsec'], SEC,
                         self.section['offset'], self.section['bytes'])
        u.mem_write(CTX, bytes(context))
        u.mem_write(SEC, self.state['sec'])
        u.mem_write(DATA, before + b'\x6e' * 510)
        u.mem_write(DATA + 0x11000, command_id.to_bytes(4, 'big'))
        self.opened = True  # Declared context supplied by FILE's open model.
        self.cursor, self.next_alloc = self.section['offset'], HEAP
        self.trace.clear()
        self.reads.clear()
        self.seen.clear()
        self.strlen_observations.clear()
        saved = [u.reg_read(getattr(r, 'UC_ARM_REG_R' + str(n))) for n in range(4, 12)]
        for reg, value in ((r.UC_ARM_REG_R0, DATA + 0x11000), (r.UC_ARM_REG_R1, DATA),
                           (r.UC_ARM_REG_R2, 512), (r.UC_ARM_REG_R3, CTX),
                           (r.UC_ARM_REG_SP, sp), (r.UC_ARM_REG_LR, STOP | 1), (r.UC_ARM_REG_FPSCR, 0)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 1
        error = None
        returned = False
        try:
            u.emu_start(0x251c1, STOP, count=200_000)
            _check(u.reg_read(r.UC_ARM_REG_PC) == STOP and u.reg_read(r.UC_ARM_REG_SP) == sp,
                   'file-caller-direct-not-returned')
            _check(saved == [u.reg_read(getattr(r, 'UC_ARM_REG_R' + str(n))) for n in range(4, 12)],
                   'file-caller-direct-saved-registers')
            _check(not self.allocations, 'file-caller-direct-allocation-leak')
            returned = True
        except Exception as failure:
            self.poisoned = True
            error = str(failure)
        _check(bytes(u.mem_read(DATA + 514, 510)) == b'\x6e' * 510,
               'file-caller-direct-output-guard-changed')
        return dict(error=error, returned=returned, observedR0=u.reg_read(r.UC_ARM_REG_R0),
            bufferHex=bytes(u.mem_read(DATA, 514)).hex(), trace=dict(self.trace),
            originalFunctionEntries=self.seen.copy(), strlen=self.strlen_observations.copy(),
            allocationsRemaining=len(self.allocations), enginePoisoned=self.poisoned)


class CpuFileCallerReference(CpuLoaderReference):
    def __init__(self, file_blob, std_blob, calc, language):
        # Reject changed inputs before either CPU is created.
        _check(type(file_blob) is bytes and hashlib.sha256(file_blob).hexdigest() == FILE_SHA256,
               'file-caller-file-source-unreviewed')
        audit_language_sources(calc, std_blob, language)
        elf = _Elf(file_blob)
        _check(hashlib.sha256(elf.raw(CALLER[0], CALLER[1])).hexdigest() == CALLER[2],
               'file-caller-region-unreviewed')
        symbols = [s for s in elf.symbols if s['name'] == 'FromCmdIdToCmdBuff' and s['section']]
        _check(len(symbols) == 1 and symbols[0]['va'] == CALLER[0] | 1 and symbols[0]['bytes'] == CALLER[1],
               'file-caller-symbol-unreviewed')
        command_name = elf.pic_address(0x17778, 0x1777c)
        _check(elf.cstring(command_name) == 'COMMAND.GAG', 'file-caller-command-name-unreviewed')
        super().__init__(file_blob, std_blob)
        self.command_name = command_name
        self.std_blob, self.calc, self.language = std_blob, calc, language
        self.std_reference = None
        self.allowed.append((CALLER[0], CALLER[0] + CALLER[1]))
        self.allocation_live = False
        self.context_open = False
        try:
            for page in (0x51000000, 0x53000000):
                self.u.mem_map(page, 4096, 5)
                self.u.mem_write(page, b'\x70\x47' * 2048)
            for index, name in ((1, 'source-open-model'), (6, 'source-lookup-bridge'), (2, 'source-close-model')):
                self.hooks[0x51000000 + index * 32] = name
        except Exception:
            self.close()
            raise

    def _writable(self, address, size):
        return super()._writable(address, size) or type(size) is int and 0 < size <= 514 and (
            DATA <= address and address + size <= DATA + 512
            or self.allocation_live and SCRATCH <= address and address + size <= SCRATCH + 514)

    def code(self, u, address, size, userdata):
        if address == 0:
            raise ValueError('file-caller-null-function-slot')
        if 0x53000000 <= address < 0x53001000:
            raise ValueError('file-caller-stale-function-slot-unreviewed')
        if address == 0x17772:
            raw = u.reg_read(self.r.UC_ARM_REG_R0)
            self.loader_return = raw if raw < 0x80000000 else raw - 0x100000000
        name = self.hooks.get(address)
        if name not in ('malloc', 'free', '__aeabi_memcpy', 'source-open-model',
                        'source-lookup-bridge', 'source-close-model'):
            return super().code(u, address, size, userdata)
        _check(time.monotonic() < self.deadline, 'file-caller-wall-budget-exhausted')
        r = self.r
        a, b, c, d = [u.reg_read(reg) for reg in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1,
                                                r.UC_ARM_REG_R2, r.UC_ARM_REG_R3)]
        self.caller_events.append(dict(name=name, args=[a, b, c, d]))
        result = 0
        if name == 'malloc':
            _check(a == 514 and not self.allocation_live, 'file-caller-allocation-unreviewed')
            self.allocation_live = True
            u.mem_write(SCRATCH, self.before)
            result = SCRATCH
        elif name == 'free':
            _check(a == SCRATCH and self.allocation_live, 'file-caller-free-unreviewed')
            self.allocation_live = False
        elif name == '__aeabi_memcpy':
            _check((a, b, c) == (DATA, SCRATCH, 512) and self.allocation_live,
                   'file-caller-copy-unreviewed')
            u.mem_write(DATA, bytes(u.mem_read(SCRATCH, 512)))
        elif name == 'source-open-model':
            _check(a == self.command_name and not self.context_open, 'file-caller-context-open-unreviewed')
            self.context_open = True
            result = CONTEXT_TOKEN
        elif name == 'source-close-model':
            _check(a == CONTEXT_TOKEN and self.context_open, 'file-caller-context-close-unreviewed')
            self.context_open = False
            if self.std_reference is not None:
                self.std_reference.opened = False  # Explicit modeled close, no native STD close.
        else:
            _check((b, c, d) == (SCRATCH, 512, CONTEXT_TOKEN) and self.allocation_live
                   and self.context_open and self.std_reference is None
                   and STACK <= a and a + 4 <= STACK + 0x100000,
                   'file-caller-lookup-arguments-unreviewed')
            command_id = int.from_bytes(bytes(u.mem_read(a, 4)), 'big')
            _check(command_id == self.command_id, 'file-caller-command-id-mismatch')
            self.std_reference = CpuDirectCommandReference(self.calc, self.std_blob, self.language)
            self.lookup = self.std_reference.search(command_id, bytes(u.mem_read(SCRATCH, 514)))
            u.mem_write(SCRATCH, bytes.fromhex(self.lookup['bufferHex']))
            _check(self.lookup['error'] is None, 'file-caller-std-bridge-stopped:' + str(self.lookup['error']))
            result = self.lookup['observedR0']
        u.reg_write(r.UC_ARM_REG_R0, result)
        u.reg_write(r.UC_ARM_REG_PC, u.reg_read(r.UC_ARM_REG_LR))

    def run(self, command_id, tail, profile):
        _check(type(command_id) is int and 0 <= command_id <= 0xffffffff and profile in PROFILES,
               'file-caller-input-invalid')
        before = scratch_bytes(tail)
        _check(not self.closed and not self.poisoned and self.std_reference is None
               and not self.allocation_live and not self.context_open, 'file-caller-prior-state')
        self.command_id, self.before = command_id, before
        u, r = self.u, self.r
        for name, address in self.slots.items():
            value = 0
            if profile in ('cached-known', 'cached-stale'):
                if name in SYMBOL_NAMES:
                    page = 0x51000001 if profile == 'cached-known' else 0x53000001
                    value = page + SYMBOL_NAMES.index(name) * 32
                elif name == 'pStdSearchIDHandle' or profile == 'cached-known':
                    value = HANDLE
            u.mem_write(address, struct.pack('<I', value))
        failures = {'failed-first-symbol': 0, 'failed-open-symbol': 1,
                    'failed-lookup-symbol': 6, 'failed-after-lookup': 7}
        results = [True] * 16
        if profile in failures:
            results = [True] * failures[profile] + [False]
        self.event = dict(firstDlopenSucceeded=profile != 'library-failed',
            fallbackDlopenSucceeded=False if profile == 'library-failed' else None,
            symbolLookupResults=[] if profile == 'library-failed' else results)
        self.logger_return = 0
        self.events, self.writes, self.caller_events = [], [], []
        self.lookup, self.loader_return = None, None
        self.trace = collections.Counter()
        self.instruction_hash = hashlib.sha256()
        u.mem_write(DATA, b'\x3c' * 512 + b'\x6e' * 512)
        u.mem_write(SCRATCH, b'\x6e' * 1024)
        for n in range(12):
            u.reg_write(getattr(r, 'UC_ARM_REG_R' + str(n)), 0x6a000000 + n * 16)
        saved = [u.reg_read(getattr(r, 'UC_ARM_REG_R' + str(n))) for n in range(4, 12)]
        sp = STACK + 0x80000
        for reg, value in ((r.UC_ARM_REG_R0, DATA), (r.UC_ARM_REG_R1, 512),
                           (r.UC_ARM_REG_R2, command_id), (r.UC_ARM_REG_SP, sp), (r.UC_ARM_REG_LR, STOP | 1)):
            u.reg_write(reg, value)
        self.deadline = time.monotonic() + 10
        error = None
        returned = False
        try:
            u.emu_start(CALLER[0] | 1, STOP, count=200_000)
            _check(u.reg_read(r.UC_ARM_REG_PC) == STOP and u.reg_read(r.UC_ARM_REG_SP) == sp,
                   'file-caller-not-returned')
            _check(saved == [u.reg_read(getattr(r, 'UC_ARM_REG_R' + str(n))) for n in range(4, 12)],
                   'file-caller-saved-registers')
            _check(not self.allocation_live and not self.context_open, 'file-caller-cleanup-incomplete')
            returned = True
        except Exception as failure:
            self.poisoned = True
            error = str(failure)
        _check(bytes(u.mem_read(DATA + 512, 512)) == b'\x6e' * 512
               and bytes(u.mem_read(SCRATCH + 514, 510)) == b'\x6e' * 510,
               'file-caller-buffer-guard-changed')
        return dict(commandIdHex=f'{command_id:08X}', tail=tail, profile=profile,
            error=error, returned=returned, observedR0=u.reg_read(r.UC_ARM_REG_R0), loaderReturn=self.loader_return,
            outputHex=bytes(u.mem_read(DATA, 512)).hex(), scratchHex=bytes(u.mem_read(SCRATCH, 514)).hex(),
            allocationLive=self.allocation_live, contextModelOpen=self.context_open,
            lookup=self.lookup, callerEvents=self.caller_events.copy(), loaderEvents=self.events.copy(),
            nativeObjectWrites=self.writes.copy(), instructionTraceSha256=self.instruction_hash.hexdigest(),
            trace=dict(self.trace), savedRegistersPreserved=True if returned else None, stackRestored=True if returned else None,
            nativeStdLookupInstructionsExecuted=self.lookup is not None, **GUARDS)

    def close(self):
        errors = []
        std = getattr(self, 'std_reference', None)
        if std is not None and not std.closed:
            try:
                std.close()
            except Exception as error:
                errors.append(error)
        try:
            super().close()
        except Exception as error:
            errors.append(error)
        if errors:
            raise ValueError('file-caller-engine-cleanup-failed:' + str(len(errors))) from errors[0]


def audit_matrix(file_blob, std_blob, calc, language, portable, output, *, budget_s=300):
    _check(type(budget_s) in (int, float) and 1 <= budget_s <= 900,
           'file-caller-total-budget-invalid')
    output = Path(output)
    _check(not output.exists(), 'file-caller-fresh-output-required')
    _check(type(file_blob) is bytes and hashlib.sha256(file_blob).hexdigest() == FILE_SHA256,
           'file-caller-file-source-unreviewed')
    audit_language_sources(calc, std_blob, language)
    scope = selector_scope(portable, language)
    records = {row['commandId']: row for row in decode_command_catalog(language)['records']}
    selection = [int(row['commandIdHex'], 16) for row in scope['commands']]
    _check(0xffffffff not in records, 'file-caller-missing-record-probe-no-longer-missing')
    cases = [(command_id, tail, profile) for command_id in selection
             for tail in ('zero', 'sentinel-nul', 'sentinel') for profile in PROFILES]
    cases += [(0xffffffff, tail, 'complete') for tail in ('zero', 'sentinel-nul', 'sentinel')]
    output.mkdir(parents=True)
    started = time.monotonic()
    counts = collections.Counter()
    disposed = 0
    case_count = 0
    case_file = output / 'cases.jsonl'
    with case_file.open('x', encoding='utf8', newline='\n') as stream:
        for command_id, tail, profile in cases:
            _check(time.monotonic() - started < budget_s, 'file-caller-total-budget-exhausted')
            reference = CpuFileCallerReference(file_blob, std_blob, calc, language)
            try:
                row = reference.run(command_id, tail, profile)
            finally:
                reference.close()
                disposed += 1 + (reference.std_reference is not None)
            row['enginesDisposedThisCase'] = 1 + (reference.std_reference is not None)
            body = bytes.fromhex(records[command_id]['sourceDescriptorHex']) if command_id in records else b''
            before = scratch_bytes(tail)
            if profile == 'cached-stale':
                expected_error = 'file-caller-stale-function-slot-unreviewed'
            elif profile in ('library-failed', 'failed-first-symbol', 'failed-open-symbol', 'failed-lookup-symbol'):
                expected_error = 'file-caller-null-function-slot'
            elif body and tail == 'sentinel' and b'\0' not in body:
                expected_error = 'file-caller-std-bridge-stopped:command-strlen-outside-observed-buffer'
            else:
                expected_error = None
            _check(row['error'] == expected_error, 'file-caller-unexpected-outcome:' + str(row['error']))
            _check(row['returned'] == (expected_error is None), 'file-caller-return-state-mismatch')
            loader_expected = {'library-failed': -1, 'failed-first-symbol': -3,
                               'failed-open-symbol': -4, 'failed-lookup-symbol': -9, 'failed-after-lookup': -10}
            _check(row['loaderReturn'] == loader_expected.get(profile, 0), 'file-caller-loader-return-mismatch')
            if row['lookup'] is not None:
                expected_scratch = body + before[len(body):] if body else before
                _check(row['scratchHex'] == expected_scratch.hex(), 'file-caller-independent-scratch-mismatch')
            else:
                _check(row['scratchHex'] == before.hex(), 'file-caller-unmodified-scratch-mismatch')
            if expected_error is None:
                expected_output = body + before[len(body):512] if body else before[:512]
                _check(row['observedR0'] == 0 and row['lookup']['observedR0'] == len(body)
                       and row['outputHex'] == expected_output.hex()
                       and not row['allocationLive'] and not row['contextModelOpen'],
                       'file-caller-return-or-full-copy-mismatch')
                expected_calls = ['malloc', 'source-open-model', 'source-lookup-bridge',
                                  '__aeabi_memcpy', 'free', 'source-close-model']
            else:
                _check(row['outputHex'] == (bytes(2) + b'\x3c' * 510).hex()
                       and row['allocationLive'], 'file-caller-stop-prefix-or-lifetime-mismatch')
                expected_calls = ['malloc']
                if profile == 'failed-lookup-symbol' or row['lookup'] is not None:
                    expected_calls.append('source-open-model')
                if row['lookup'] is not None:
                    expected_calls.append('source-lookup-bridge')
            _check([e['name'] for e in row['callerEvents']] == expected_calls,
                   'file-caller-cleanup-call-order-mismatch')
            counts['returned' if row['returned'] else row['error']] += 1
            case_count += 1
            stream.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            if case_count % 25 == 0:
                stream.flush()
                (output / 'progress.json').write_text(json.dumps(dict(completedCases=case_count,
                    enginesDisposed=disposed), indent=2) + '\n', encoding='utf8', newline='\n')
    with case_file.open('rb') as stream:
        cases_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = dict(kind='x431-original-file-command-caller-reference', schemaVersion=1,
        caseCount=case_count, counts=dict(counts), enginesDisposed=disposed, engineVersion='2.1.4',
        selectorScope=scope, missingCommandProbe='FFFFFFFF', sourceDescriptorRecords=len(records),
        casesSha256=cases_sha, codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        sourceHashes=dict(file=hashlib.sha256(file_blob).hexdigest(), std=hashlib.sha256(std_blob).hexdigest(),
                          calc=hashlib.sha256(calc).hexdigest(), language=hashlib.sha256(language).hexdigest()),
        qualificationScope='pinned FILE caller and separate original STD lookup; explicit virtual loader/file context/allocation and pointer-translation bridge; not actual Android heap or process binding',
        fullCapacityCopyChecked=True, callerReturnDoesNotPropagateLookupResult=True,
        originalBodyMatchesIndependentDescriptorBytes=True, **GUARDS)
    (output / 'result.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n',
                                      encoding='utf8', newline='\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('file-library', 'std-library', 'calc-library', 'language', 'source-catalog-zip', 'output-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    report = audit_matrix(args.file_library.read_bytes(), args.std_library.read_bytes(),
        args.calc_library.read_bytes(), args.language.read_bytes(), args.source_catalog_zip, args.output_dir)
    print(json.dumps({key: report[key] for key in ('caseCount', 'counts', 'enginesDisposed', 'casesSha256')}))


if __name__ == '__main__':
    main()
