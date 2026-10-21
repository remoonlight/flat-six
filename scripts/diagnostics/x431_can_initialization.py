"""Offline reference for fixed Porsche COMM initialization; no transport or DLL load.

The callbacks below are observations of the internal X431 interface, not CANtieCAR
commands or ECU PDUs. Matching the original's return does not qualify hardware:
the original ignores foundation and final link-filter failures.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from functools import reduce
from operator import xor

from .x431_native_binding import _Elf
from .x431_response_contract import PORSCHE_COMM_SHA256
from .x431_version_response import SYS_DATA_LOGICAL_SHA256
from scripts.x431_re.sysdata import parse_payload

GUARDS = dict(offlineOnly=True, executionEnabled=False, writePayload=None,
              liveVerified=False, actualHeadConfigurationQualified=False,
              actualRuntimeBindingQualified=False, lowerCallbacksModeled=True)
CB = 0x1000000  # Explicit virtual load address used by the retained CPU matrix.
REGIONS = ((0x11c30, 1324, 'd3d92a2becffb770545d510b02fba9f8397118c8d5e501eb3c218744e6c7f341'),
           (0x1215c, 220, '467625c1c916712799e4826ae781b609d6ab23438747cc5c7d16d64a7c95b33d'),
           (0x12238, 16, '93c05624dacf71d303dfb1b0c4147432075281d1191b00fa5909aee46ad69977'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def word(value):
    if type(value) is not int or not -0x80000000 <= value <= 0xffffffff:
        raise ValueError('initialization-word-invalid')
    return value & 0xffffffff


def finish_frame(frame):
    frame[-1] = reduce(xor, frame[2:-1], 0)
    return frame.hex()


class InitializationReference:
    def __init__(self, comm_blob, logical_blob):
        if type(comm_blob) is not bytes or digest(comm_blob) != PORSCHE_COMM_SHA256:
            raise ValueError('initialization-comm-source-unreviewed')
        if type(logical_blob) is not bytes or digest(logical_blob) != SYS_DATA_LOGICAL_SHA256:
            raise ValueError('initialization-sys-source-unreviewed')
        elf = _Elf(comm_blob)
        for start, size, expected in REGIONS:
            if digest(elf.raw(start, size)) != expected:
                raise ValueError('initialization-region-unreviewed')
        self.enter_template = elf.raw(189448, 30)
        self.link_template = elf.raw(172456, 20)
        self.records = {(0xd00 | ecu, link['index']): link['comm']
                        for ecu, system in parse_payload(logical_blob)['byEcuId'].items()
                        for link in system['links']}

    def predict(self, case):
        if not isinstance(case, dict) or type(case.get('family')) is not int or case['family'] != 13:
            raise ValueError('initialization-family-unreviewed')
        key = (case.get('systemKey'), case.get('linkIndex'))
        if any(type(x) is not int for x in key) or key not in self.records:
            raise ValueError('initialization-source-record-mismatch')
        # JSON preserves bool/int/float distinctions that Python equality loses.
        try:
            source_record = json.dumps(self.records[key], sort_keys=True, separators=(',', ':'))
            case_record = json.dumps(case.get('comm'), sort_keys=True, separators=(',', ':'))
        except (TypeError, ValueError) as exc:
            raise ValueError('initialization-source-record-mismatch') from exc
        if source_record != case_record:
            raise ValueError('initialization-source-record-mismatch')
        c = case['comm']
        if c['protocolKind'] not in (0x10, 0x30, 0x50):
            raise ValueError('initialization-protocol-unreviewed')
        subtype = case.get('enterSubtype')
        if type(subtype) is not int or subtype not in (1, 3, 4, 5, 6):
            raise ValueError('initialization-subtype-unreviewed')
        if type(case.get('differentSourcePointer')) is not bool:
            raise ValueError('initialization-pointer-scenario-invalid')
        # The original reads the first byte of its supplied configuration
        # (TOKEN+0x63c), rather than using a constant channel. Older retained
        # standalone observations initialized that byte to one explicitly.
        channel = case.get('channelByte', 1)
        if type(channel) is not int or not 0 <= channel <= 255:
            raise ValueError('initialization-channel-byte-invalid')
        values = {k: word(case.get(k)) for k in ('carType', 'pin1Return', 'pin2Return',
                  'parameterReturn', 'enterFrameReturn', 'linkReturn', 'foundationReturn', 'priorFilter')}
        events = []
        filter_word = values['priorFilter']
        def event(name, site, result, **fields):
            events.append(dict(name=name, returnAddress=CB + site, returnedWord=result, **fields))
        def result(ret):
            return dict(nativeReturnWord=ret & 0xffffffff, callbackEvents=events,
                        globalFilterAfter=filter_word, sourceInputsBound=True,
                        channelByte=channel,
                        channelSource='explicit-observation' if 'channelByte' in case else 'legacy-fixture-one',
                        initializerChecksLinkFilterReturn=False, **GUARDS)
        event('SelectPIN', 0x11c94, values['pin1Return'], args=[channel, 14, 0, 0, 0])
        if values['pin1Return']:
            return result(-1)
        low = values['carType'] & 65535
        event('GetDBSCarType', 0x11ca8, values['carType'], returnLow16=low)
        if low != 2:
            event('GetDBSCarType', 0x11cba, values['carType'], returnLow16=low)
        speed = c['token']['TOKEN+0x641']['value']
        event('SelectPIN', 0x11d14, values['pin2Return'],
              args=[channel, 0x21 if low in (2, 10) else 3, 0, 3 if speed == 5 else 6, 11 if speed == 5 else 14])
        if values['pin2Return']:
            return result(-2)
        for name, site, args in [('BeginCombination', 0x11d28, None),
                ('SetCommunicationMaximumWaitTime', 0x11d38, [18000]),
                ('SetEcuCommunicationVoltageLogic', 0x11d48, [0, 255]),
                ('SetEcuBaudRate', 0x11d5a, [0x47610000]),
                ('SetCommunicationParameter', 0x11d6e, [1, 8, 1, 0]),
                ('SetEcuCommTimeInterval', 0x11dc0, [0, 50, 1600]),
                ('EndCombination', 0x11dca, None)]:
            event(name, site, values['foundationReturn'], **({'args': args} if args is not None else {}))
        parameter = bytearray(8) + bytes([speed])
        if c['protocolKind'] == 0x30:
            parameter += b'\xff\x00\x00'
        else:
            pair = c['pairs'][-1]
            filter_word = ((pair['storedTx'] << 16) | (pair['storedRx'] & 65535)) & 0xffffffff
            parameter += b'\x01' + (filter_word & 65535).to_bytes(2, 'big')
        event('CanSetParameterNormal', 0x11e8c, values['parameterReturn'], channel=channel, parameterHex=parameter.hex())
        if values['parameterReturn']:
            return result(-3)
        if subtype in (3, 5, 6):
            frame = bytearray(self.enter_template[:19 if subtype == 6 else 17])
            if subtype == 6:
                frame[2], frame[5] = 15, 0x88
                frame[6:10] = filter_word.to_bytes(4, 'big')
                frame[10:13] = bytes([0x30, 0, 1])
                frame[13:18] = b'\xaa' * 5
                site, error = 0x11f6e, -6
            elif subtype == 5:
                frame[6:8] = (filter_word >> 16).to_bytes(2, 'big')
                extra = word(case.get('mode5Extra', c['pairs'][0].get('mode5Extra') or 0))
                frame[8:12] = bytes([extra & 255, 0x30, 0x10, 5])
                frame[12:16] = b'\xaa' * 4
                site, error = 0x11fdc, -5
            else:
                high = filter_word >> 16
                frame[6:8] = b'\xfc\x00' if high == 0xfbe0 else high.to_bytes(2, 'big')
                frame[8] = 0x30
                # Current source is family 13; the >=0x11 family branch is not a 981/982 input.
                low_key = case['systemKey'] & 255
                special = 0xb00 <= (case['systemKey'] & ~255) <= 0xf00 and low_key in (0x41, 0x44, 0x46, 0x4b)
                frame[9:11] = bytes([8, 10]) if special else bytes([0x10, 5])
                frame[11:16] = bytes(5) if special else b'\xaa' * 5
                site, error = 0x120ac, -4
            event('SetEnterFrame', site, values['enterFrameReturn'], frameBytes=len(frame), frameHex=finish_frame(frame))
            if values['enterFrameReturn'] & 65535:
                return result(error)
        count = self.link_template[0]
        if count != 2:
            raise ValueError('initialization-link-template-unreviewed')
        frame = bytearray([0x55, 0xaa, 4 + count * 10, 0x60, 0x19, 0, count])
        for i in range(count):
            frame += b'\0\0' + self.link_template[1 + 8*i:9 + 8*i]
        frame += b'\0'
        event('OneToOne_SendDataToEcuGetAnswer', 0x1220c, values['linkReturn'],
              args=[255, 80, 1], stackExtraExceptBuffer=[len(frame), 0, 2, 4], frameHex=finish_frame(frame))
        return result(1)

    def compare(self, observation):
        if not isinstance(observation, dict):
            raise ValueError('initialization-observation-invalid')
        predicted = self.predict(observation.get('case'))
        for field in ('nativeReturnWord', 'callbackEvents', 'globalFilterAfter'):
            if observation.get(field) != predicted[field]:
                raise ValueError('initialization-reference-mismatch:' + field)
        return predicted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comm', type=Path, required=True)
    parser.add_argument('--sys-data', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.observations.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('initialization-observations-too-large')
    reference = InitializationReference(args.comm.read_bytes(), args.sys_data.read_bytes())
    counts, matched = Counter(), 0
    with args.observations.open(encoding='utf-8') as f:
        for line in f:
            if matched >= 10000 or len(line) > 65536:
                raise ValueError('initialization-observation-limit')
            row = reference.compare(json.loads(line))
            counts[str(row['nativeReturnWord'])] += 1
            matched += 1
    if not matched:
        raise ValueError('initialization-observations-empty')
    report = dict(kind='x431-offline-initialization-reference', matched=matched,
                  returnCounts=dict(counts), sourceCommRecords=len(reference.records),
                  commSha256=PORSCHE_COMM_SHA256, logicalSha256=SYS_DATA_LOGICAL_SHA256,
                  observationsSha256=digest(args.observations.read_bytes()), **GUARDS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
