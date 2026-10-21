"""Owned VNCI raw-CAN I/O for the source-bound TP2.0 channel.

The receiver remains active through a channel, including ACK/control sends.
Both primitives and tags are owned and checked. A negotiated pair is accepted
only from the setup receiver's D0, after its setup transmit has completed.
No discovery, application retry or caller-supplied arbitrary CAN send API.
"""
from collections import deque
import math
import threading
import time

from .capture_clock import capture_timestamp
from .elm import ElmError
from .mode30_transport import ChannelOffer, SOURCE_CHANNEL_OFFERS
from .vnci import UNDEF, ID_UNDEF, _result_indication


class Mode30RawIo:
    def __init__(self, native, offer, *, clock=time.monotonic, sleeper=time.sleep, raw_log=None):
        if not isinstance(offer, ChannelOffer) or offer not in SOURCE_CHANNEL_OFFERS:
            raise ElmError('mode30-channel-offer-unqualified')
        self.native, self.offer = native, offer
        self.clock, self.sleep = clock, sleeper
        self.raw_log = raw_log if raw_log is not None else []
        self.active = {}
        self._outcomes = {}
        self._last_tag = 0
        self._frames = deque()
        self._pair = self._negotiated_pair = None
        self._setup_transmitted = False
        self._setup_response_delivered = False
        self._setup = True
        self._stopping = self._closed = self._failed = False
        self._rx_cop = None
        self._lock = threading.RLock()
        self.last_observation = None

    def _guard(self, deadline, *, cleanup=False, expired_ok=False):
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ElmError('mode30-deadline-invalid')
        if self._closed or self._failed and not cleanup:
            raise ElmError('mode30-raw-client-poisoned')
        if self.clock() >= deadline and not expired_ok:
            raise ElmError('deadline-expired')

    def _start(self, frame, role, deadline):
        self._guard(deadline)
        result = self.native.start_mode30_primitive(frame)
        if not isinstance(result, tuple) or len(result) != 2:
            raise ElmError('d-pdu:primitive-handle-missing')
        cop, tag = result
        if (type(cop) is not int or not 0 <= cop < ID_UNDEF or cop in self.active
                or type(tag) is not int or not self._last_tag < tag <= 0xFFFFFFFF):
            raise ElmError('d-pdu:primitive-handle-missing')
        self._last_tag = tag
        self.active[cop] = dict(tag=tag, role=role)
        return cop, tag

    def _event(self):
        event = self.native.event()
        if event is None:
            return False
        if not isinstance(event, dict):
            raise ElmError('d-pdu:malformed-event')
        cop, kind = event.get('cop'), event.get('type')
        if kind == 0x1301 and cop == UNDEF and event.get('value') in (0x8050, 0x8051):
            return True
        owned = self.active.get(cop) if type(cop) is int else None
        if not owned or type(event.get('tag')) is not int or event['tag'] != owned['tag']:
            raise ElmError('d-pdu:foreign-com-primitive')
        row = dict(layer='can', cop=cop, tag=owned['tag'], eventType=kind, duringCleanup=self._stopping)
        if 'timestamp' in event:
            row['nativeTimestampUsRaw'] = event['timestamp']
        if kind == 0x1300:
            data, flags = event.get('payload'), event.get('flags')
            if type(data) is not bytes or type(flags) is not bytes:
                raise ElmError('d-pdu:malformed-or-unmatched-response')
            row.update(hex=data.hex().upper(), flagsHex=flags.hex().upper())
            self.raw_log.append(row)
            if _result_indication(data, flags) is not None:
                if event.get('unique_id') not in (1, UNDEF, ID_UNDEF):
                    raise ElmError('d-pdu:malformed-or-unmatched-response')
                return True
            if (owned['role'] != 'rx' or not 5 <= len(data) <= 12 or flags != b'\0'*4
                    or event.get('unique_id') != 1 or event.get('acceptance_id') != 1
                    or self._pair is None or int.from_bytes(data[:4], 'big') != self._pair[1]
                    or event.get('extra_header') not in (None, b'', data[:4])
                    or event.get('extra_footer') not in (None, b'')):
                raise ElmError('d-pdu:malformed-or-unmatched-response')
            if self._stopping:
                return True  # Bytes observed during cancellation never become PDUs.
            if len(self._frames) >= 1024:
                raise ElmError('mode30-raw-response-limit')
            self._frames.append((self._pair[1], data[4:], dict(**capture_timestamp(), primitive=cop,
                expectedPrimitive=cop, uniqueId=1, flagsHex=flags.hex().upper())))
        elif kind == 0x1301:
            state = event.get('value')
            row['value'] = state
            self.raw_log.append(row)
            if state not in (0x8010, 0x8011, 0x8012, 0x8013):
                raise ElmError('d-pdu:unexpected-primitive-state')
            if state in (0x8012, 0x8013):
                self.active.pop(cop)
                self._outcomes[(cop, owned['tag'])] = state
                if owned['role'] == 'rx' and not self._stopping:
                    raise ElmError('d-pdu:mode30-receive-ended')
        elif kind == 0x1302:
            raise ElmError('d-pdu:event-error:'+str(event.get('value')))
        else:
            raise ElmError('d-pdu:unexpected-primitive-event')
        return True

    def _stop_cops(self, deadline):
        self._stopping = True
        try:
            for cop in list(self.active):
                self.native.cancel(cop)
            for _ in range(4096):
                if not self.active:
                    self._outcomes.clear()
                    return
                self._guard(deadline, cleanup=True)
                if not self._event():
                    self.sleep(min(.01, max(0, deadline-self.clock())))
            raise ElmError('d-pdu:cleanup-event-cap')
        finally:
            self._stopping = False

    def configure_pair(self, tx, rx, deadline):
        with self._lock:
            try:
                self._guard(deadline)
                first = self._pair is None
                if (type(tx) is not int or type(rx) is not int or not 0 < tx <= 0x7FF or not 0 < rx <= 0x7FF or tx == rx
                        or first and (tx, rx) != (self.offer.tx_id, self.offer.rx_id)
                        or not first and (not self._setup or not self._setup_transmitted
                            or not self._setup_response_delivered or (tx, rx) != self._negotiated_pair)):
                    raise ElmError('d-pdu:mode30-address-unqualified')
                if not first:
                    if self._frames:
                        raise ElmError('desynchronized-rx')
                    self._stop_cops(min(deadline, self.clock()+1))
                    released = self.native.release_link()
                    if (not isinstance(released, dict) or released.get('errors') != []
                            or released.get('moduleRetained') is not True):
                        raise ElmError('d-pdu:link-transition-failed')
                self.native.create_mode30_link(tx, rx, offer=self.offer, setup=first)
                self._pair, self._setup = (tx, rx), first
                # Connection status notifications contain no ECU payload.
                for _ in range(32):
                    event = self.native.event()
                    if event is None:
                        break
                    if (not isinstance(event, dict) or event.get('type') != 0x1301
                            or event.get('cop') != UNDEF or event.get('value') not in (0x8050, 0x8051)):
                        raise ElmError('desynchronized-rx')
                else:
                    raise ElmError('d-pdu:setup-event-cap')
                self._rx_cop, _ = self._start(None, 'rx', deadline)
            except Exception:
                self._failed = True
                raise

    def send(self, frame, deadline):
        with self._lock:
            try:
                self._guard(deadline)
                if self._pair is None or self._rx_cop not in self.active:
                    raise ElmError('d-pdu:mode30-receive-not-active')
                cop, tag = self._start(frame, 'tx', deadline)
                # A raw CAN transmission owns exactly one COP and is never
                # repeated; a missing terminal notification is not success.
                end = min(deadline, self.clock()+1)
                for _ in range(4096):
                    self._guard(end)
                    if cop not in self.active:
                        if self._outcomes.pop((cop, tag), None) != 0x8012:
                            raise ElmError('d-pdu:mode30-transmit-cancelled')
                        if self._setup and frame == self.offer.payload:
                            self._setup_transmitted = True
                        return
                    if not self._event():
                        self.sleep(min(.01, max(0, end-self.clock())))
                raise ElmError('d-pdu:request-event-cap')
            except Exception:
                self._failed = True
                raise

    def receive(self, deadline):
        with self._lock:
            try:
                for _ in range(4096):
                    self._guard(deadline, expired_ok=True)
                    if self.clock() >= deadline:
                        return None
                    if self._frames:
                        identifier, frame, observation = self._frames.popleft()
                        self.last_observation = observation
                        if self._setup and self._setup_transmitted and len(frame) == 7 and frame[1] == 0xD0 and frame[6] == 1:
                            self._negotiated_pair = (int.from_bytes(frame[4:6], 'little'), int.from_bytes(frame[2:4], 'little'))
                            self._setup_response_delivered = True
                        return identifier, frame
                    if not self._event():
                        self.sleep(min(.01, max(0, deadline-self.clock())))
                        return None
                raise ElmError('mode30-receive-limit')
            except Exception:
                self._failed = True
                raise

    def close_primitives(self, deadline):
        """Release owned COPs; native module and TP channel close are separate."""
        with self._lock:
            if self._closed:
                return dict(primitivesClosed=not self.active)
            try:
                self._stop_cops(deadline)
                self._frames.clear()
                return dict(primitivesClosed=True)
            finally:
                self._closed = True
