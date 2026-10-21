"""Bounded source-mode50 CAN data framing for the central headlamp unit.

The original 0x11320 constructor and 0x11c30 initializer supply address 0x55,
six request bytes per single frame, and flow control 55 30 10 05. The original
receive wrapper does not compare the extra receive address. Preserve it and
require it to stay consistent, rather than assuming a tester address.
The byte port is supplied by the existing selected-adapter factory. Software
framing and adapter-local configuration do not establish vehicle qualification.
"""
from __future__ import annotations

from .elm import ElmError, ElmClient, parse_can_line
from .capture_clock import CAPTURE_CLOCK
from .pair import response_matches
import re

TX_ID = 0x754
RX_ID = 0x7BE
ADDRESS = 0x55
BLOCK_SIZE = 0x10
FLOW_CONTROL = bytes.fromhex('55301005AAAAAAAA')
MAX_PDU_BYTES = 4095


def request_frame(payload: bytes) -> bytes:
    """Reject the source's seven-byte truncation case instead of copying it."""
    if type(payload) is not bytes or not 1 <= len(payload) <= 6:
        raise ElmError('mode50-request-size-unqualified')
    return bytes((ADDRESS, len(payload))) + payload + b'\xAA' * (6-len(payload))


class ReplyAssembler:
    """One complete PDU; callers own the deadline, CAN-ID filter and FC writes."""
    def __init__(self, receive_address=None):
        if receive_address is not None and (type(receive_address) is not int or not 0 <= receive_address <= 255):
            raise ElmError('mode50-receive-address-invalid')
        self.receive_address = receive_address
        self.expected = None
        self.data = bytearray()
        self.next_sequence = 1
        self.block_frames = 0
        self.flow_control_needed = False
        self.complete = False

    def flow_control_sent(self):
        if not self.flow_control_needed or self.complete:
            raise ElmError('mode50-unexpected-flow-control')
        self.flow_control_needed = False
        self.block_frames = 0

    def feed(self, frame: bytes):
        if type(frame) is not bytes or not 3 <= len(frame) <= 8:
            raise ElmError('mode50-frame-size-invalid')
        if self.complete:
            raise ElmError('mode50-extra-response-frame')
        address, pci = frame[:2]
        if self.receive_address is not None and address != self.receive_address:
            raise ElmError('identity-mismatch')
        kind = pci >> 4
        if self.expected is None:
            if kind == 0:
                size = pci & 15
                if not 1 <= size <= 6 or len(frame) < 2+size:
                    raise ElmError('mode50-single-frame-invalid')
                self.expected = size
                self.data.extend(frame[2:2+size])
                self.complete = True
            elif kind == 1:
                size = ((pci & 15) << 8) | frame[2]
                if len(frame) != 8 or not 7 <= size <= MAX_PDU_BYTES:
                    raise ElmError('mode50-first-frame-invalid')
                self.expected = size
                self.data.extend(frame[3:])
                self.flow_control_needed = True
            else:
                raise ElmError('mode50-first-frame-required')
            self.receive_address = address
        else:
            if kind != 2 or pci & 15 != self.next_sequence:
                raise ElmError('mode50-sequence-mismatch')
            if self.flow_control_needed:
                raise ElmError('mode50-flow-control-required')
            remaining = self.expected-len(self.data)
            take = min(6, remaining)
            if len(frame) < 2+take:
                raise ElmError('mode50-consecutive-frame-short')
            self.data.extend(frame[2:2+take])
            self.next_sequence = (self.next_sequence+1) & 15
            self.block_frames += 1
            self.complete = len(self.data) == self.expected
            if not self.complete and self.block_frames == BLOCK_SIZE:
                self.flow_control_needed = True
        return bytes(self.data) if self.complete else None


class _Reply:
    def __init__(self, client, request, deadline):
        self.client, self.request, self.deadline = client, request, deadline
        self.assembler = ReplyAssembler(client.receive_address)
        self.final = None
        self.pending = []
        self.lines = self.frames = 0
        self.started = self.started_mono = None

    @property
    def stop_needed(self):
        return self.final is not None or self.assembler.flow_control_needed

    def line(self, line, stamp, started, mono=None, started_mono=None):
        if line is None:
            raise ElmError('mode50-line-too-long')
        text = line.decode('latin-1').strip().upper()
        self.lines += 1
        if self.lines > 4096:
            raise ElmError('mode50-response-limit')
        if any(token in text for token in ('ERROR','UNABLE','BUFFER FULL','BUS BUSY','?')):
            raise ElmError('mode50-adapter-error')
        if text == 'STOPPED' and not self.client._monitor_stop_sent:
            raise ElmError('mode50-monitor-stopped')
        parsed = parse_can_line(text)
        if parsed is None or parsed[0] != RX_ID:
            return
        if self.started is None:
            self.started, self.started_mono = started, started_mono
        self.frames += 1
        if self.frames > 2048:
            raise ElmError('mode50-response-limit')
        payload = self.assembler.feed(parsed[1])
        if payload is not None:
            if self.client._pdu_sink:
                self.client._pdu_sink(dict(requestHex=self.request.hex().upper(),pduHex=payload.hex().upper(),
                    timestampUs=stamp,startedTimestampUs=self.started,monotonicClock=CAPTURE_CLOCK,
                    monotonicTimestampUs=mono,startedMonotonicTimestampUs=self.started_mono,
                    timestampSource='host-pdu-observed',transportKind='elm-iso-tp',
                    observation='reassembled-complete-pdu',duringCleanup=False))
            relation = response_matches(self.request,payload)
            if relation is None or self.request==b'\x3E\0' and payload[0]!=0x7F and payload!=b'\x7E\0':
                raise ElmError('wrong-sid-or-did-echo')
            self.client.receive_address = self.assembler.receive_address
            if relation == 'pending-7Fxx78':
                self.pending.append(payload.hex().upper())
                if len(self.pending)>256:raise ElmError('mode50-pending-limit')
                self.assembler = ReplyAssembler(self.client.receive_address)
                self.started = self.started_mono = None
            else:
                self.final = dict(ok=relation!='negative',error='negative-response' if relation=='negative' else None,
                    payload_hex=payload.hex().upper(),pending=self.pending)
        if self.client._monitoring and self.stop_needed and not self.client._monitor_stop_sent:
            self.client._write_all(b'\x1B',self.deadline)
            self.client._monitor_stop_sent = True


class Mode50ElmClient(ElmClient):
    """Source-owned raw frames and FC, with bounded monitor-only receives.

    CAF0/CFC0 keep address and PCI ownership here. ATMA receives late replies
    without replaying a vehicle request; an acknowledged ESC ends every monitor.
    No CAN receive address byte is guessed or supplied by a renderer.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.receive_address = None
        self._monitoring = self._monitor_stop_sent = self._monitor_cleaning = False
        self._mode50_cleanup_errors = []

    def configure_pair(self,tx_hex,rx_hex,deadline):
        self._enter_io()
        try:
            self._configured=False;self.receive_address=None
            if (tx_hex,rx_hex)!=('754','7BE'):raise ElmError('mode50-address-unqualified')
            for cmd in ('ATE0','ATL0','ATS1','ATH1','ATD0','ATCEA','ATCAF0','ATCFC0',
                        'ATAT0','ATST19','ATTP6','ATSH 754','ATCRA 7BE','ATCSM0'):
                self._send_at_unlocked(cmd,deadline,require_ok=True)
            self._tx_id,self._rx_id=TX_ID,RX_ID;self._configured=True
        finally:self._io_lock.release()

    def _stop_monitor(self):
        if not self._monitoring:return
        cancel=self._cancel;self._cancel=None;self._monitor_cleaning=True
        try:
            deadline=self._now()+1
            if not self._monitor_stop_sent:
                self._write_all(b'\x1B',deadline);self._monitor_stop_sent=True
            self._read_until_prompt(deadline,occupy=True)
            self._monitoring=False
        except Exception:
            if 'mode50:monitor-stop-unconfirmed' not in self._mode50_cleanup_errors:
                self._mode50_cleanup_errors.append('mode50:monitor-stop-unconfirmed')
            raise
        finally:self._cancel=cancel;self._monitor_cleaning=False

    def _poison(self,reason):
        if self._monitoring and not self._monitor_cleaning and not self._poisoned:
            try:self._stop_monitor()
            except Exception:pass  # Retained in close evidence; never claim a clean monitor.
        super()._poison(reason)

    def request(self,req_hex,deadline):
        if not isinstance(req_hex,str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}){2,3}',req_hex):
            raise ElmError('mode50-request-unqualified')
        request=bytes.fromhex(req_hex)
        if not (request in (b'\x10\x03',b'\x3E\0') or request[0] in (0x1A,0x21) and len(request)==2
                or request[0]==0x22 and len(request)==3
                or request in (b'\x19\x02\x08',b'\x19\x02\x0c',b'\x19\x02\x2c')):raise ElmError('mode50-request-unqualified')
        if not self._configured:raise ElmError('not-configured')
        self._enter_io()
        reply=_Reply(self,request,deadline)
        try:
            if self._check_idle_rx(deadline):
                self._poison('desynchronized-rx');raise ElmError('desynchronized-rx')
            packet=request_frame(request)
            while True:
                self._raise_if_blocked(deadline)
                self._monitor_stop_sent=False
                if packet is None:
                    data=b'ATMA\r';self._monitoring=True
                else:data=packet.hex().upper().encode('ascii')+b'\r'
                self.raw_log.append(dict(dir='tx',hex=data.hex().upper(),req=req_hex.upper(),
                    mode50=True,monitorOnly=packet is None))
                self._write_all(data,deadline)
                if packet==FLOW_CONTROL:reply.assembler.flow_control_sent()
                self._read_until_prompt(deadline,occupy=True,observe_line=reply.line)
                if self._monitoring and not self._monitor_stop_sent:
                    self._monitoring=False;raise ElmError('mode50-monitor-ended')
                self._monitoring=False
                if reply.final is not None:return reply.final
                packet=FLOW_CONTROL if reply.assembler.flow_control_needed else None
        except ElmError as error:
            if not self._poisoned:self._poison(str(error))
            if str(error).startswith('prompt-timeout') and reply.assembler.expected is None and not reply.pending:
                raise ElmError('NO DATA') from error
            raise
        finally:self._io_lock.release()

    def close_restore(self):
        report=super().close_restore()
        return {**report,'errors':list(dict.fromkeys(report['errors']+self._mode50_cleanup_errors))}
