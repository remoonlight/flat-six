"""Execute pinned FILE loader routines in an explicit isolated CPU model.

No host dlopen/dlsym/dlclose, logging, diagnostic head or vehicle is used.
Virtual loader results and native object initial values are declared inputs;
matching the source state model never qualifies an actual Android binding.
"""
from pathlib import Path
import argparse
import collections
import hashlib
import json
import struct
import time

from .arm_write_monitor import attach_arm_write_monitor
from .x431_native_binding import (
    _Elf, REGIONS, SYMBOL_NAMES, audit_loader_sequence, audit_std_loader_sources)
from .x431_sprintf_reference import CpuSprintfReference, STACK, STOP, _check
from .x431_version_response import VERSION_GUARDS

PLT = (0x13e0c, 4232, '7d9b77143b969d594da6a15688f76a1b14e43fbaba0123a695ca879e75bcfd45')
LOGGER = (0x21fb8, 264, 'ae8fe5b3cb969a23c859373998def549e57679ed9cb8a1b1b54d7d5c24f1dca5')
HANDLE = 0x71534c44
GUARDS = {**VERSION_GUARDS, 'vendorCodeExecuted': True,
    'originalInstructionsExecutedInCpu': True, 'cpuEmulationOnly': True,
    'hostElfLoaded': False, 'hostDynamicLoaderCalled': False, 'nativeLoggingExecuted': False,
    'dynamicLoaderResultsModeled': True, 'nativeObjectsExplicitlyInitialized': True,
    'actualDynamicLookupBindingQualified': False, 'actualProcessLibraryFingerprintQualified': False,
    'headOpened': False, 'vehicleVerified': False, 'liveVerified': False, 'writePayload': None}


def validate_observation(observation):
    expected = audit_loader_sequence(observation)
    _check(all(type(value) is bool for value in observation['symbolSlots'].values()),
           'loader-reference-unknown-initial-slot')
    return expected


class CpuLoaderReference(CpuSprintfReference):
    def __init__(self, file_blob, std_blob):
        self.source_audit = audit_std_loader_sources(file_blob, std_blob)
        elf = _Elf(file_blob)
        for address, size, sha in (PLT, LOGGER):
            _check(hashlib.sha256(elf.raw(address, size)).hexdigest() == sha,
                   'loader-reference-additional-region-unreviewed')
        logger = [s for s in elf.symbols if s['name'] == 'wrDllLog' and s['section']]
        _check(len(logger) == 1 and logger[0]['va'] == LOGGER[0] | 1
               and logger[0]['bytes'] == LOGGER[1] and logger[0]['type'] == 2,
               'loader-reference-logger-unreviewed')
        self.slots = {}
        for name in (*SYMBOL_NAMES, 'pStdDll', 'pStdSearchIDHandle'):
            symbols = [s for s in elf.symbols if s['name'] == name and s['section']]
            _check(len(symbols) == 1 and symbols[0]['type'] == 1 and symbols[0]['bytes'] == 4,
                   'loader-reference-object-unreviewed')
            self.slots[name] = symbols[0]['va']
        self.library_paths = [elf.pic_address(a, b) for a, b in ((0x25846, 0x2584a), (0x25852, 0x25856))]
        try:
            import unicorn as engine
            from unicorn import arm_const
        except ImportError as error:
            raise ValueError('loader-reference-private-unicorn-2.1.4-required') from error
        _check(engine.__version__ == '2.1.4', 'loader-reference-engine-unreviewed')
        self.engine, self.r = engine, arm_const
        self.u = engine.Uc(engine.UC_ARCH_ARM, engine.UC_MODE_THUMB)
        self.closed = self.poisoned = False
        self.code_hook = self.write_hook = None
        self.allowed = [(a, b) for _, a, b, _ in REGIONS] + [(PLT[0], PLT[0] + PLT[1])]
        try:
            self._map(file_blob)
            self.hooks[LOGGER[0]] = 'retained-file-log-model'
            self.code_hook = self.u.hook_add(engine.UC_HOOK_CODE, self.code)
            self.write_hook = attach_arm_write_monitor(self.u, self.write)
        except Exception:
            self.close()
            raise

    def _writable(self, address, size):
        return type(size) is int and 0 < size <= 8 and (
            STACK <= address and address + size <= STACK + 0x100000
            or size == 4 and address in self.slots.values())

    def write(self, u, access, address, size, value, userdata):
        _check(self._writable(address, size), 'loader-reference-write-range-unreviewed')
        if address in self.slots.values():
            name = next(n for n, a in self.slots.items() if a == address)
            self.writes.append(dict(object=name, value=value))

    def code(self, u, address, size, userdata):
        if address == STOP:
            return
        _check(time.monotonic() < self.deadline, 'loader-reference-wall-budget-exhausted')
        name = self.hooks.get(address)
        r = self.r
        if name is None:
            _check(any(a <= address < b for a, b in self.allowed),
                   'loader-reference-code-range-unreviewed')
            self.trace['originalInstructions'] += 1
            self.instruction_hash.update(struct.pack('<II', address, size))
            return
        a, b, c, d = [u.reg_read(reg) for reg in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1,
                                                r.UC_ARM_REG_R2, r.UC_ARM_REG_R3)]
        if name == 'retained-file-log-model':
            # Suppress the native logger; arguments are recorded without reading
            # unknown pointers. Matrix compares two explicit return tokens.
            result = self.logger_return
            self.events.append(dict(name=name, args=[a, b, c, d], result=result))
        elif name == 'dlopen':
            index = sum(e['name'] == 'dlopen' for e in self.events)
            _check(index < 2 and a == self.library_paths[index] and b == 1,
                   'loader-reference-dlopen-arguments')
            key = 'firstDlopenSucceeded' if index == 0 else 'fallbackDlopenSucceeded'
            success = self.event[key]
            _check(type(success) is bool, 'loader-reference-dlopen-outcome-unobserved')
            result = HANDLE if success else 0
            self.events.append(dict(name=name, path=('libSTD.so', './libSTD.so')[index], result=result))
        elif name == 'dlsym':
            index = sum(e['name'] == 'dlsym' for e in self.events)
            _check(index < len(SYMBOL_NAMES) and index < len(self.event['symbolLookupResults']),
                   'loader-reference-symbol-count')
            binding = self.source_audit['symbolBindings'][index]
            _check(a == HANDLE and b == binding['nameVa'], 'loader-reference-dlsym-arguments')
            # Virtual tokens are intentionally unrelated to process pointers.
            result = 0x51000001 + index * 32 if self.event['symbolLookupResults'][index] else 0
            self.events.append(dict(name=name, symbol=binding['name'], result=result))
        elif name == 'dlclose':
            _check(a == HANDLE and not any(e['name'] == 'dlclose' for e in self.events),
                   'loader-reference-dlclose-arguments')
            result = self.event['observedDlcloseReturn']
            _check(type(result) is int, 'loader-reference-dlclose-outcome-unobserved')
            self.events.append(dict(name=name, result=result))
        else:
            raise ValueError('loader-reference-import-unreviewed:' + name)
        u.reg_write(r.UC_ARM_REG_R0, result & 0xffffffff)
        u.reg_write(r.UC_ARM_REG_PC, u.reg_read(r.UC_ARM_REG_LR))

    def sequence(self, observation, *, logger_return=0):
        expected = validate_observation(observation)
        _check(type(logger_return) is int and logger_return in (0, 0xa5a5a5a5),
               'loader-reference-log-return-unreviewed')
        _check(not self.closed and not self.poisoned, 'loader-reference-prior-state')
        u, r = self.u, self.r
        self.logger_return = logger_return
        initial = {name: 0x53000001 + i * 32 if observation['symbolSlots'][name] else 0
                   for i, name in enumerate(SYMBOL_NAMES)}
        initial.update(pStdDll=HANDLE if observation['pStdDllPresent'] else 0,
                       pStdSearchIDHandle=HANDLE if observation['pStdSearchIDHandlePresent'] else 0)
        for name, value in initial.items():
            u.mem_write(self.slots[name], struct.pack('<I', value))
        steps = []
        try:
            for event, target in zip(observation['events'], expected['steps']):
                self.event = event
                self.events, self.writes = [], []
                self.trace = collections.Counter()
                self.instruction_hash = hashlib.sha256()
                sp = STACK + 0x80000
                for n in range(12):
                    u.reg_write(getattr(r, 'UC_ARM_REG_R' + str(n)), 0x6a000000 + n * 16)
                saved = [u.reg_read(getattr(r, 'UC_ARM_REG_R' + str(n))) for n in range(4, 12)]
                u.reg_write(r.UC_ARM_REG_SP, sp)
                u.reg_write(r.UC_ARM_REG_LR, STOP | 1)
                self.deadline = time.monotonic() + 1
                entry = 0x258a9 if event['operation'] == 'open-search' else 0x2587d
                u.emu_start(entry, STOP, count=100_000)
                _check(u.reg_read(r.UC_ARM_REG_PC) == STOP and u.reg_read(r.UC_ARM_REG_SP) == sp,
                       'loader-reference-not-returned')
                _check(saved == [u.reg_read(getattr(r, 'UC_ARM_REG_R' + str(n))) for n in range(4, 12)],
                       'loader-reference-saved-registers')
                raw = u.reg_read(r.UC_ARM_REG_R0)
                ret = raw if raw < 0x80000000 else raw - 0x100000000
                slots = {name: struct.unpack('<I', u.mem_read(address, 4))[0]
                         for name, address in self.slots.items()}
                _check(ret == target['sourceReturnCandidate'], 'loader-reference-return-mismatch')
                _check(bool(slots['pStdDll']) == target['pStdDllPresentCandidate']
                       and bool(slots['pStdSearchIDHandle']) == target['pStdSearchIDHandlePresentCandidate'],
                       'loader-reference-handle-mismatch')
                _check({name: bool(slots[name]) for name in SYMBOL_NAMES} == target['symbolSlotsCandidate'],
                       'loader-reference-slot-mismatch')
                resolved = [e['symbol'] for e in self.events if e['name'] == 'dlsym']
                _check(resolved == target['sourceResolvedSymbols'], 'loader-reference-resolution-order')
                steps.append(dict(operation=event['operation'], returnValue=ret, slots=slots,
                    foundationEvents=self.events.copy(), nativeObjectWrites=self.writes.copy(),
                    trace=dict(self.trace), instructionTraceSha256=self.instruction_hash.hexdigest(),
                    savedRegistersPreserved=True, stackRestored=True,
                    sourceStatus=target['status'], sourceCompleteLoaderChainCandidate=target['completeLoaderChainCandidate']))
            return dict(initialObjects=initial, loggerModelReturn=logger_return, steps=steps, **GUARDS)
        except Exception:
            self.poisoned = True
            raise


def matrix_scenarios():
    def opened(first=True, fallback=None, results=None):
        return dict(operation='open-search', firstDlopenSucceeded=first,
                    fallbackDlopenSucceeded=fallback,
                    symbolLookupResults=[True] * 16 if results is None else results, observedReturn=None)
    cached = opened(None, None, [])
    scenarios = [('cold', False, False, [opened()]),
                 ('fallback', False, False, [opened(False, True)]),
                 ('dlopen-failed', False, False, [opened(False, False, [])])]
    for index in range(16):
        scenarios.append(('symbol-failed-' + str(index), False, False,
                          [opened(results=[True] * index + [False]), cached]))
    for ret in (0, -1):
        scenarios.append(('close-reopen-' + str(ret), False, False,
                          [opened(), dict(operation='close-std', observedDlcloseReturn=ret, observedReturn=None), cached]))
    scenarios.extend([
        ('cached-std', True, False, [opened(None, None)]),
        ('cached-search-only', False, True, [cached]),
        ('cached-both', True, True, [cached]),
        ('close-null', False, False, [dict(operation='close-std', observedDlcloseReturn=None, observedReturn=None)]),
        ('close-null-search', False, True,
         [dict(operation='close-std', observedDlcloseReturn=None, observedReturn=None), cached])])
    return scenarios


def audit_matrix(file_blob, std_blob, output, *, budget_s=60):
    _check(type(budget_s) in (int, float) and 1 <= budget_s <= 300,
           'loader-reference-total-budget-invalid')
    output = Path(output)
    _check(not output.exists(), 'loader-reference-fresh-output-required')
    source = audit_std_loader_sources(file_blob, std_blob)
    output.mkdir(parents=True)
    started = time.monotonic()
    rows = []
    for name, std, search, events in matrix_scenarios():
        for stale in (False, True):
            pair = []
            for logger_return in (0, 0xa5a5a5a5):
                _check(time.monotonic() - started < budget_s, 'loader-reference-total-budget-exhausted')
                observation = dict(pStdDllPresent=std, pStdSearchIDHandlePresent=search,
                                   symbolSlots={name: stale for name in SYMBOL_NAMES}, events=events)
                reference = CpuLoaderReference(file_blob, std_blob)
                try:
                    row = reference.sequence(observation, logger_return=logger_return)
                finally:
                    reference.close()
                row.update(case=name, initiallyStaleSlots=stale, engineDisposed=reference.closed)
                rows.append(row)
                pair.append([(s['returnValue'], s['slots'], s['instructionTraceSha256']) for s in row['steps']])
            _check(pair[0] == pair[1], 'loader-reference-logger-return-affects-state-or-trace')
    raw = b''.join((json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode() for row in rows)
    (output / 'cases.jsonl').write_bytes(raw)
    report = dict(kind='x431-original-file-loader-reference', schemaVersion=1,
        caseCount=len(rows), scenarioCount=len(matrix_scenarios()), enginesDisposed=len(rows),
        stepCount=sum(len(row['steps']) for row in rows), casesSha256=hashlib.sha256(raw).hexdigest(),
        codeSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), engineVersion='2.1.4',
        sourceHashes=dict(file=hashlib.sha256(file_blob).hexdigest(), std=hashlib.sha256(std_blob).hexdigest()),
        sourceStateModelAgreesWithOriginal=True, loggerReturnDoesNotAffectStateOrInstructionTrace=True,
        qualificationScope='pinned FILE loader routines; explicit virtual loader and logging returns, object initial values; not Android process binding or FILE command caller',
        symbolBindings=source['symbolBindings'], **GUARDS)
    (output / 'result.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf8', newline='\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file-library', type=Path, required=True)
    parser.add_argument('--std-library', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = audit_matrix(args.file_library.read_bytes(), args.std_library.read_bytes(), args.output_dir)
    print(json.dumps({key: result[key] for key in ('caseCount', 'scenarioCount', 'stepCount', 'enginesDisposed', 'casesSha256')}))


if __name__ == '__main__':
    main()
