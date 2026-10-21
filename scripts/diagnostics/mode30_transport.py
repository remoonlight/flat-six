"""TP2.0 channel over an owned raw-CAN transport, for source-bound reads.

The transport supplies configure_pair(tx, rx, deadline), send(bytes, deadline)
and receive(deadline) -> (CAN ID, bytes) or None. It owns hardware cleanup;
this layer separately requires the A8 channel-close acknowledgement.

Protocol reference (MIT): Open Vehicles / Michael Balzer, OVMS VWTP poller:
https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3/blob/
76c4637ede9985ee6c1d14268c5a36b7e6ed9883/vehicle/OVMS.V3/components/poller/src/vehicle_poller_vwtp.cpp
Source C0 offers are supplied unchanged by the version-bound caller. The host
advertises its own finite ACK/separation parameters. No X431 smart-box packet
is sent to a raw-CAN API. A channel is not an ECU identity or a live approval.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
import time

from .allowlist import blocked_reason
from .elm import ElmError
from .pair import response_matches

MAX_PDU_BYTES = 4095
# Independent host capability advertisement, not an X431 timing observation.
HOST_PARAMETERS = bytes.fromhex('A00F8AFF0AFF')


def _can_id(value):
    return type(value) is int and 0 < value <= 0x7FF


def timing_seconds(value):
    if type(value) is not int or not 0 <= value <= 255:
        raise ElmError('mode30-timing-invalid')
    return (value & 63) * (0.0001, 0.001, 0.01, 0.1)[value >> 6]


@dataclass(frozen=True)
class ChannelOffer:
    tx_id: int
    rx_id: int
    payload: bytes

    def __post_init__(self):
        p = self.payload
        if (not _can_id(self.tx_id) or not _can_id(self.rx_id)
                or self.tx_id != 0x200 or type(p) is not bytes or len(p) != 7
                or p[1:4] != bytes.fromhex('C00010') or p[6] != 1
                or self.rx_id != self.tx_id + p[0]
                or not _can_id(int.from_bytes(p[4:6], 'little'))):
            raise ElmError('mode30-channel-offer-unqualified')


SOURCE_CHANNEL_OFFERS = frozenset(ChannelOffer(0x200, 0x200+p[0], p) for p in (
    bytes.fromhex('26C00010020301'), bytes.fromhex('35C00010030301'),
    bytes.fromhex('22C00010040301'), bytes.fromhex('23C00010050301')))


class ReplyAssembler:
    """One TP PDU, with sequence and block state carried across messages."""
    def __init__(self, sequence, block_size):
        if (type(sequence) is not int or not 0 <= sequence <= 15
                or type(block_size) is not int or not 1 <= block_size <= 255):
            raise ElmError('mode30-assembler-state-invalid')
        self.next_sequence = sequence
        self.block_size = block_size
        self.block_frames = 0
        self.expected = None
        self.data = bytearray()
        self.ack_needed = self.complete = False
        self.length_flags = 0

    def acknowledge(self):
        if not self.ack_needed:
            raise ElmError('mode30-unexpected-acknowledgement')
        self.ack_needed = False
        self.block_frames = 0

    def feed(self, frame):
        if (type(frame) is not bytes or not 2 <= len(frame) <= 8
                or frame[0] >> 4 not in (0, 1, 2, 3)):
            raise ElmError('mode30-data-frame-invalid')
        if self.complete or self.ack_needed:
            raise ElmError('mode30-acknowledgement-required' if self.ack_needed else 'mode30-extra-data-frame')
        if frame[0] & 15 != self.next_sequence:
            raise ElmError('mode30-sequence-mismatch')
        kind = frame[0] >> 4
        if self.expected is None:
            if len(frame) < 4 or frame[1] & 0x70:
                raise ElmError('mode30-first-frame-invalid')
            self.length_flags = frame[1] & 0xF0
            self.expected = ((frame[1] & 15) << 8) | frame[2]
            if not 1 <= self.expected <= MAX_PDU_BYTES:
                raise ElmError('mode30-response-length-invalid')
            body = frame[3:]
        else:
            body = frame[1:]
        remaining = self.expected - len(self.data)
        last = kind in (1, 3)
        if (not body or len(body) > remaining or last != (len(body) == remaining)
                or not last and len(frame) != 8):
            raise ElmError('mode30-response-length-mismatch')
        self.data.extend(body)
        self.next_sequence = (self.next_sequence + 1) & 15
        self.block_frames += 1
        self.ack_needed = kind in (0, 1)
        if self.block_frames > self.block_size or self.block_frames == self.block_size and not (self.ack_needed or last):
            raise ElmError('mode30-response-block-overrun')
        self.complete = last
        return bytes(self.data) if self.complete else None


def _read_allowlist(values):
    if not isinstance(values, (set, frozenset)) or not values:
        raise ElmError('mode30-read-allowlist-invalid')
    allowed = frozenset(values)
    for hx in allowed:
        if not isinstance(hx, str) or not re.fullmatch(r'(?:[0-9A-F]{2}){2,5}', hx):
            raise ElmError('mode30-read-allowlist-invalid')
        p = bytes.fromhex(hx)
        if (blocked_reason(p, allowed) or not (
                p in (b'\x10\x03', b'\x10\x89', b'\x3E\0')
                or p[0] in (0x1A, 0x21) and len(p) == 2
                or p[0] == 0x22 and len(p) == 3
                or p[0] == 0x18 and len(p) == 4
                or p[0] == 0x19 and p[1] == 2 and len(p) == 3)):
            raise ElmError('mode30-read-allowlist-invalid')
    return allowed


class Mode30Channel:
    """No port discovery, automatic reconnect, arbitrary service or write API.

    The source-bound caller owns identity/version checks and the batch deadline.
    A malformed/late response poisons this channel; opening a new channel belongs
    to the user's existing retry flow. Diagnostic requests are never replayed.
    """
    def __init__(self, io, offer, allowed, *, clock=time.monotonic, sleeper=time.sleep,
                 cancel_event=None, pdu_sink=None, phase_timeout_s=2.0, receive_block_size=15):
        if not isinstance(offer, ChannelOffer):
            raise ElmError('mode30-channel-offer-unqualified')
        if type(phase_timeout_s) not in (float, int) or not math.isfinite(phase_timeout_s) or not 0 < phase_timeout_s <= 10:
            raise ElmError('mode30-timeout-invalid')
        if type(receive_block_size) is not int or not 1 <= receive_block_size <= HOST_PARAMETERS[1]:
            raise ElmError('mode30-receive-block-size-invalid')
        self.host_parameters = HOST_PARAMETERS[:1] + bytes((receive_block_size,)) + HOST_PARAMETERS[2:]
        self.io, self.offer = io, offer
        self.allowed = _read_allowlist(allowed)
        self.clock, self.sleep, self.cancel = clock, sleeper, cancel_event
        self.pdu_sink, self.phase_timeout_s = pdu_sink, phase_timeout_s
        self.state = 'new'
        self.tx_id, self.rx_id = offer.tx_id, offer.rx_id
        self.tx_sequence = self.rx_sequence = 0
        self.peer_parameters = None
        self.channel_close_verified = False
        self._active_pair = None
        self._channel_assigned = False
        self._setup_attempted = False
        self._last_data_sent = None
        self._last_observation = None
        self._lock = threading.Lock()

    def _guard(self, deadline, *, cleanup=False):
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ElmError('mode30-deadline-invalid')
        if not cleanup and self.cancel is not None and self.cancel.is_set():
            raise ElmError('cancelled')
        if self.clock() >= deadline:
            raise ElmError('deadline-expired')

    def _send(self, frame, deadline, *, cleanup=False):
        self._guard(deadline, cleanup=cleanup)
        self.io.send(frame, deadline)
        self._guard(deadline, cleanup=cleanup)

    def _receive(self, deadline, *, cleanup=False, control=True):
        end = min(deadline, self.clock() + self.phase_timeout_s)
        for _ in range(4096):
            self._guard(deadline, cleanup=cleanup)
            if self.clock() >= end:raise ElmError('mode30-response-timeout')
            value = self.io.receive(end)
            self._guard(deadline, cleanup=cleanup)
            if self.clock() >= end:raise ElmError('mode30-response-timeout')
            if value is None:
                continue
            if not isinstance(value, tuple) or len(value) != 2 or not _can_id(value[0]):
                raise ElmError('mode30-raw-frame-invalid')
            identifier, frame = value
            if identifier != self.rx_id:
                continue
            if type(frame) is not bytes or not 1 <= len(frame) <= 8:
                raise ElmError('mode30-raw-frame-invalid')
            if control and frame == b'\xA3':
                self._send(b'\xA1' + self.host_parameters[1:], end, cleanup=cleanup)
                continue
            if control and frame == b'\xA8':
                self._send(b'\xA8', end, cleanup=cleanup)
                self.state = 'closed'
                self.channel_close_verified = True
                raise ElmError('mode30-peer-closed')
            self._last_observation = getattr(self.io, 'last_observation', None)
            return frame
        raise ElmError('mode30-receive-limit')

    def open(self, deadline):
        if not self._lock.acquire(blocking=False):
            raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            if self.state != 'new':
                raise ElmError('mode30-channel-already-started')
            self.state = 'setup'
            self.io.configure_pair(self.tx_id, self.rx_id, deadline)
            self._active_pair = (self.tx_id, self.rx_id)
            self._guard(deadline)
            self._setup_attempted = True
            self._send(self.offer.payload, deadline)
            reply = self._receive(deadline, control=False)
            if len(reply) != 7 or reply[1] != 0xD0 or reply[6] != 1:
                raise ElmError('mode30-channel-setup-failed')
            rx, tx = int.from_bytes(reply[2:4], 'little'), int.from_bytes(reply[4:6], 'little')
            if not _can_id(tx) or not _can_id(rx) or tx == rx:
                raise ElmError('mode30-negotiated-address-invalid')
            self.tx_id, self.rx_id = tx, rx
            self._channel_assigned = True
            self.state = 'parameters'
            self.io.configure_pair(tx, rx, deadline)
            self._active_pair = (tx, rx)
            self._send(self.host_parameters, deadline)
            reply = self._receive(deadline)
            if len(reply) != 6 or reply[0] != 0xA1 or not reply[1] or reply[3] != 255 or reply[5] != 255:
                raise ElmError('mode30-channel-parameters-invalid')
            self.peer_parameters = dict(blockSize=reply[1], ackTimeS=timing_seconds(reply[2]), separationTimeS=timing_seconds(reply[4]))
            self.state = 'idle'
        except Exception:
            if self.state != 'closed':
                self.state = 'failed'
            raise
        finally:
            self._lock.release()

    def bind_reads(self, allowed):
        validated = _read_allowlist(allowed)
        if not self._lock.acquire(blocking=False):
            raise ElmError('request-in-flight')
        try:
            if self.state != 'idle':
                raise ElmError('mode30-channel-not-ready')
            self.allowed = validated
        finally:
            self._lock.release()

    def idle_until(self, until, deadline):
        """Service peer channel control between samples without reading values.

        The caller's sample time and batch deadline remain fixed. A peer close
        or unsolicited PDU is not an automatic reconnect or a new observation.
        """
        if type(until) not in (int, float) or not math.isfinite(until):
            raise ElmError('mode30-deadline-invalid')
        if not self._lock.acquire(blocking=False):
            raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            if self.state != 'idle':
                raise ElmError('mode30-channel-not-ready')
            end = min(until, deadline)
            for _ in range(65536):
                if self.clock() >= end:
                    return
                self._guard(deadline)
                value = self.io.receive(end)
                self._guard(deadline)
                if value is None:
                    continue
                if not isinstance(value, tuple) or len(value) != 2 or not _can_id(value[0]):
                    raise ElmError('mode30-raw-frame-invalid')
                if value[0] != self.rx_id:
                    continue
                frame = value[1]
                if frame == b'\xA3':
                    self._send(b'\xA1' + self.host_parameters[1:], deadline)
                elif frame == b'\xA8':
                    self._send(b'\xA8', deadline)
                    self.channel_close_verified = True
                    self.state = 'closed'
                    raise ElmError('mode30-peer-closed')
                else:
                    raise ElmError('mode30-unsolicited-frame')
            raise ElmError('mode30-receive-limit')
        except Exception:
            if self.state != 'closed':
                self.state = 'failed'
            raise
        finally:
            self._lock.release()

    def request(self, request_hex, deadline):
        # Validate before touching IO, even if a caller accidentally supplies a
        # writable SID in a later binding. Read requests fit a single TP frame.
        if not isinstance(request_hex, str) or request_hex not in self.allowed:
            raise ElmError('unknown-request')
        request = bytes.fromhex(request_hex)
        if blocked_reason(request, self.allowed):
            raise ElmError('mode30-request-unqualified')
        if not self._lock.acquire(blocking=False):
            raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            if self.state != 'idle':
                raise ElmError('mode30-channel-not-ready')
            self._transmit_request(request, deadline)
            self.state = 'response'
            pending = []
            for _ in range(257):
                assembler = ReplyAssembler(self.rx_sequence, self.host_parameters[1])
                started_observation = None
                for _ in range(1024):
                    frame = self._receive(deadline)
                    if started_observation is None:
                        started_observation = self._last_observation
                    payload = assembler.feed(frame)
                    self.rx_sequence = assembler.next_sequence
                    if assembler.ack_needed:
                        self._send(bytes((0xB0 | self.rx_sequence,)), deadline)
                        assembler.acknowledge()
                    if payload is not None:
                        break
                else:
                    raise ElmError('mode30-response-frame-limit')
                relation = response_matches(request, payload)
                if (relation is None or request == b'\x3E\0' and payload[0] != 0x7F and payload != b'\x7E\0'
                        or assembler.length_flags and relation != 'pending-7Fxx78'):
                    raise ElmError('wrong-sid-or-did-echo')
                if self.pdu_sink:
                    self.pdu_sink(dict(requestHex=request_hex, pduHex=payload.hex().upper(),
                        observation=self._last_observation, startedObservation=started_observation))
                if relation == 'pending-7Fxx78':
                    pending.append(payload.hex().upper())
                    if len(pending) > 256:
                        raise ElmError('mode30-pending-limit')
                    continue
                self.state = 'idle'
                return dict(ok=relation != 'negative', error='negative-response' if relation == 'negative' else None,
                            payload_hex=payload.hex().upper(), pending=pending)
            raise ElmError('mode30-pending-limit')
        except Exception:
            if self.state != 'closed':
                self.state = 'failed'
            raise
        finally:
            self._lock.release()

    def _transmit_request(self, request, deadline):
        """Shared TP sender; caller owns the channel lock and PDU authorization.

        Read request validation remains in request(). This internal primitive
        does not grant a write or access service. Long operation clients must
        independently bind their source recipe before using it.
        """
        if type(request) is not bytes or not 1 <= len(request) <= MAX_PDU_BYTES:
            raise ElmError('mode30-request-length-invalid')
        if self.state != 'idle' or self.peer_parameters is None:
            raise ElmError('mode30-channel-not-ready')
        offset = block_frames = 0
        try:
            while offset < len(request):
                self._guard(deadline)
                if self._last_data_sent is not None:
                    earliest = self._last_data_sent + self.peer_parameters['separationTimeS']
                    while True:
                        self._guard(deadline)
                        remaining = earliest - self.clock()
                        if remaining <= 0:
                            break
                        self.sleep(min(0.01, remaining, max(0, deadline - self.clock())))
                first = offset == 0
                count = min(5 if first else 7, len(request) - offset)
                block_frames += 1
                last = offset + count == len(request)
                ack_needed = last or block_frames == self.peer_parameters['blockSize']
                opcode = 0x10 if last else 0 if ack_needed else 0x20
                frame = bytes((opcode | self.tx_sequence,))
                if first:
                    frame += len(request).to_bytes(2, 'big')
                frame += request[offset:offset + count]
                self.state = 'request-data'
                self._send(frame, deadline)
                self._last_data_sent = self.clock()
                self.tx_sequence = (self.tx_sequence + 1) & 15
                offset += count
                if ack_needed:
                    self.state = 'request-ack'
                    ack_time = self.peer_parameters['ackTimeS']
                    ack_deadline = min(deadline, self.clock() + ack_time) if ack_time else deadline
                    try:
                        ack = self._receive(ack_deadline)
                    except ElmError as error:
                        if str(error) == 'deadline-expired' and self.clock() < deadline:
                            raise ElmError('mode30-request-ack-timeout') from error
                        raise
                    if ack != bytes((0xB0 | self.tx_sequence,)):
                        raise ElmError('mode30-request-ack-mismatch')
                    block_frames = 0
            self.state = 'response'
        except Exception:
            if self.state != 'closed':
                self.state = 'failed'
            raise

    def close_channel(self, deadline):
        """Cancellation does not skip a bounded, independently verified close."""
        if not self._lock.acquire(blocking=False):
            raise ElmError('request-in-flight')
        try:
            if self.channel_close_verified:
                return dict(channelClosed=True)
            if not self._setup_attempted:
                self.state = 'closed'
                self.channel_close_verified = True
                return dict(channelClosed=True, channelNeverOpened=True)
            if not self._channel_assigned or self._active_pair != (self.tx_id, self.rx_id):
                raise ElmError('mode30-channel-close-address-unresolved')
            self.state = 'closing'
            self._send(b'\xA8', deadline, cleanup=True)
            # Already buffered request ACKs/data do not prove closure and do
            # not become samples. Only the peer's A8 confirms this channel.
            for _ in range(4096):
                reply = self._receive(deadline, cleanup=True, control=False)
                if reply == b'\xA8':
                    self.state = 'closed'
                    self.channel_close_verified = True
                    return dict(channelClosed=True)
            raise ElmError('mode30-channel-close-unconfirmed')
        except Exception:
            self.state = 'failed'
            raise
        finally:
            self._lock.release()
