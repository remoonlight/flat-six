"""Bounded ELM raw CAN I/O for the source-bound TP2.0 read channel.

No port discovery, ECU writes or application retries. The caller must select
the port and qualify its identity, then use Mode30Channel with receive block
size one. ATMA is stopped and its prompt consumed before another command.
ATV1 is required: CAF0 alone may still pad short ISO15765 CAN frames.

Adapter command semantics: ELM Electronics ELM327 v2.3 datasheet, pp.17,
22-23, 28-29, 46, 51 (ELM327DSL, May 2020). Software support is not proof
that a particular clone implements these commands or has passed car testing.
https://www.elmelectronics.com/wp-content/uploads/2020/05/ELM327DSL.pdf
"""
from collections import deque
from contextlib import contextmanager
import math
import threading
import time

from .capture_clock import capture_timestamp
from .elm import ElmClient, ElmError, parse_can_line
from .mode30_transport import ChannelOffer, HOST_PARAMETERS, SOURCE_CHANNEL_OFFERS, _read_allowlist

ELM_HOST_PARAMETERS = HOST_PARAMETERS[:1] + b'\x01' + HOST_PARAMETERS[2:]


class Mode30ElmIo:
    def __init__(self, port, offer, *, clock=time.monotonic, sleeper=time.sleep,
                 expected_model=None, elm_client=None):
        if not isinstance(offer, ChannelOffer) or offer not in SOURCE_CHANNEL_OFFERS:
            raise ElmError('mode30-channel-offer-unqualified')
        # Cancellation belongs to Mode30Channel. Adapter-local monitor stop
        # and restoration must remain possible after acquisition is cancelled.
        if elm_client is not None and (not isinstance(elm_client, ElmClient) or elm_client.port is not port):
            raise ElmError('mode30-elm-client-unqualified')
        self.elm = elm_client or ElmClient(port, clock=clock, sleeper=sleeper, expected_model=expected_model)
        self.offer, self.clock, self.sleep = offer, clock, sleeper
        self.raw_log = self.elm.raw_log
        self._lock = threading.Lock()
        self._pair = self._negotiated_pair = None
        self._setup = True
        self._setup_transmitted = self._setup_delivered = False
        self._monitoring = self._failed = self._closed = False
        self._settings_touched = False
        self._frames = deque()
        self._close_report = None
        self.last_observation = None

    def _guard(self, deadline, *, cleanup=False):
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ElmError('mode30-deadline-invalid')
        if self._closed or self._failed and not cleanup:
            raise ElmError('mode30-elm-client-poisoned')
        self.elm._raise_if_blocked(deadline)

    @contextmanager
    def _owned(self, deadline):
        if not self._lock.acquire(blocking=False):
            raise ElmError('io-in-flight')
        try:
            self._guard(deadline)
            yield
        except Exception:
            self._failed = True
            raise
        finally:
            self._lock.release()

    def configure_pair(self, tx, rx, deadline):
        with self._owned(deadline):
            deadline = min(deadline, self.clock()+3)
            first = self._pair is None
            if (type(tx) is not int or type(rx) is not int or not 0 < tx <= 0x7FF
                    or not 0 < rx <= 0x7FF or tx == rx
                    or first and (tx, rx) != (self.offer.tx_id, self.offer.rx_id)
                    or not first and (not self._setup or not self._setup_transmitted
                        or not self._setup_delivered or (tx, rx) != self._negotiated_pair)):
                raise ElmError('mode30-elm-address-unqualified')
            if self._frames or self._monitoring:
                raise ElmError('desynchronized-rx')
            if first:
                self._settings_touched = True
                for cmd in ('ATE0', 'ATL0', 'ATS1', 'ATH1', 'ATD0', 'ATM0', 'ATCEA',
                            'ATCAF0', 'ATCFC0', 'ATAT0', 'ATCTM1', 'ATST01',
                            'ATTP6', 'ATV1', 'ATR1', 'ATCSM0'):
                    self.elm.send_at(cmd, deadline, require_ok=True)
            else:
                self.elm.send_at('ATPC', deadline, require_ok=True)
            for cmd in (f'ATSH{tx:03X}', f'ATCRA{rx:03X}'):
                self.elm.send_at(cmd, deadline, require_ok=True)
            self._pair, self._setup = (tx, rx), first

    def _allowed_frame(self, frame):
        if type(frame) is not bytes or not 1 <= len(frame) <= 8:
            return False
        if self._setup:
            return frame == self.offer.payload and not self._setup_transmitted
        if frame in (ELM_HOST_PARAMETERS, b'\xA1' + ELM_HOST_PARAMETERS[1:], b'\xA8'):
            return True
        if len(frame) == 1 and frame[0] >> 4 == 0xB:
            return True
        if not (5 <= len(frame) <= 8 and frame[0] >> 4 == 1
                and frame[1] == 0 and frame[2] == len(frame)-3):
            return False
        try:
            _read_allowlist({frame[3:].hex().upper()})
            return True
        except ElmError:
            return False

    def _line(self, line, observation, started, *, monitor=False, stopping=False):
        text = line.decode('ascii', errors='replace').strip()
        if not text or stopping and text == 'STOPPED' or not monitor and text == 'NO DATA':
            return
        parsed = parse_can_line(text)
        if parsed is None or not 1 <= len(parsed[1]) <= 8:
            raise ElmError('mode30-elm-response-invalid:' + text[:96])
        if self._pair is None or parsed[0] != self._pair[1]:
            raise ElmError('mode30-elm-unmatched-can-id')
        if len(self._frames) >= 1024:
            raise ElmError('mode30-raw-response-limit')
        self._frames.append((*parsed, dict(**observation,
            startedTimestampUs=started['timestampUs'],
            startedMonotonicTimestampUs=started['monotonicTimestampUs'],
            timestampSource='host-chunk-arrival')))
        if self.elm._frame_sink:
            self.elm._frame_sink(dict(canId=parsed[0], extended=False, dataHex=parsed[1].hex().upper(),
                **observation, startedTimestampUs=started['timestampUs'],
                startedMonotonicTimestampUs=started['monotonicTimestampUs'], timestampSource='host-chunk-arrival'))

    def _read_byte(self, deadline):
        # Serial defaults used by other transports can exceed a TP ACK budget.
        # Temporarily bound this read only; do not change baud rate or leave a
        # shorter timeout installed for the next diagnostics client.
        port = self.elm.port
        name = 'timeout' if hasattr(port, 'timeout') else 'read_timeout' if hasattr(port, 'read_timeout') else None
        old = getattr(port, name) if name else None
        try:
            if name:
                remaining = max(.0001, min(.005, deadline-self.clock()))
                setattr(port, name, min(old, remaining) if type(old) in (int, float) and old >= 0 else remaining)
            chunk = port.read(1)
        except (ConnectionError, OSError, TimeoutError) as error:
            self.elm._poison(type(error).__name__)
            raise ElmError(f'port-io:{error}') from error
        finally:
            if name:
                setattr(port, name, old)
        if not chunk:
            self.sleep(min(.001, max(0, deadline-self.clock())))
            return chunk, None
        if type(chunk) is not bytes or len(chunk) != 1:
            raise ElmError('mode30-elm-byte-port-invalid')
        stamp = capture_timestamp()
        self.raw_log.append(dict(dir='rx', hex=chunk.hex().upper(), **stamp))
        return chunk, stamp

    def _exchange(self, payload, deadline, *, monitor=False):
        """A prompt is mandatory, including after a bounded quiet monitor.

        In monitor mode the one stop character is discarded by the adapter;
        it is never concatenated with the next CAN frame or AT command.
        """
        self.elm._enter_io()
        line = bytearray()
        started = None
        stop_sent = False
        end = deadline
        initial_frames = len(self._frames)
        error = None
        try:
            self.raw_log.append(dict(dir='tx', hex=payload.hex().upper()))
            self.elm._write_all(payload, deadline)
            self._monitoring = monitor
            for _ in range(32768):
                if monitor and not stop_sent and (self.clock() >= deadline or len(self._frames) > initial_frames or error):
                    # Cleanup has its own finite budget after the observation
                    # deadline. The channel rechecks its unchanged deadline
                    # before accepting any resulting application response.
                    end = self.clock()+.25
                    self.raw_log.append(dict(dir='tx', hex='1B', note='stop-monitor'))
                    self.elm._write_all(b'\x1B', end)
                    stop_sent = True
                if self.clock() >= end:
                    raise ElmError('mode30-elm-monitor-stop-unconfirmed' if monitor else 'prompt-timeout')
                chunk, stamp = self._read_byte(end)
                if not chunk:
                    continue
                byte = chunk[0]
                if byte in (10, 13, 62):
                    if line:
                        try:
                            self._line(bytes(line), stamp, started, monitor=monitor, stopping=stop_sent)
                        except ElmError as cause:
                            error = error or cause
                        line.clear()
                        started = None
                    if byte == 62:
                        self._monitoring = False
                        if monitor and not stop_sent:
                            error = error or ElmError('mode30-elm-monitor-ended')
                        if error:
                            raise error
                        return
                else:
                    if started is None:
                        started = stamp
                    if len(line) >= 256:
                        raise ElmError('mode30-elm-line-limit')
                    line.append(byte)
            raise ElmError('mode30-elm-response-limit')
        except Exception as cause:
            # A known prompt leaves the adapter available for local defaults
            # restoration. An unacknowledged command/monitor cannot be reused.
            if self._monitoring or not isinstance(cause, ElmError) or str(cause) in (
                    'prompt-timeout', 'mode30-elm-line-limit', 'mode30-elm-response-limit'):
                self.elm._poison(str(cause))
            raise
        finally:
            self.elm._io_lock.release()

    def send(self, frame, deadline):
        with self._owned(deadline):
            if self._pair is None or not self._allowed_frame(frame):
                raise ElmError('mode30-elm-frame-unqualified')
            self._exchange(frame.hex().upper().encode('ascii')+b'\r', min(deadline, self.clock()+1))
            if self._setup:
                self._setup_transmitted = True

    def receive(self, deadline):
        with self._owned(deadline):
            if self._pair is None:
                raise ElmError('mode30-elm-not-configured')
            if not self._frames:
                self._exchange(b'ATMA\r', min(deadline, self.clock()+.02), monitor=True)
            if not self._frames:
                return None
            identifier, frame, self.last_observation = self._frames.popleft()
            if self._setup and self._setup_transmitted and len(frame) == 7 and frame[1] == 0xD0 and frame[6] == 1:
                self._negotiated_pair = (int.from_bytes(frame[4:6], 'little'), int.from_bytes(frame[2:4], 'little'))
                self._setup_delivered = True
            return identifier, frame

    def close_restore(self):
        """Restore adapter defaults; TP channel A8 confirmation is separate."""
        if not self._lock.acquire(blocking=False):
            return dict(errors=['close-in-progress'])
        try:
            if self._close_report is not None:
                return dict(errors=list(self._close_report['errors']))
            errors = []
            if self._settings_touched and not self.elm._poisoned:
                end = self.clock()+3
                # ST00 retrieves the adapter's configured default (PP03), not
                # an assumed factory timer. None of these commands sends a
                # diagnostic request, changes UART speed or writes a PP.
                for cmd in ('ATPC', 'ATV0', 'ATST00', 'ATAT1', 'ATCTM1',
                            'ATCSM1', 'ATCAF1', 'ATCFC1', 'ATM1'):
                    try:
                        self.elm.send_at(cmd, end, require_ok=True)
                    except Exception as cause:
                        errors.append(f'{cmd}:{cause}')
                        break
            if self.elm._poisoned:
                errors.append('poisoned:' + str(self.elm._poison_reason))
            errors.extend(self.elm._close_port_once())
            self.elm._closed = self._closed = True
            self._frames.clear()
            self._close_report = dict(errors=errors)
            return dict(errors=list(errors))
        finally:
            self._lock.release()
