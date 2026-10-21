"""Source-bound TP2.0 clients; identity, sampling and cleanup share one channel."""
from copy import deepcopy
import threading

from .capture_clock import valid_capture_clock
from .elm import ElmClient, ElmError
from .mode30_transport import ChannelOffer, Mode30Channel
from .mode30_elm import Mode30ElmIo
from .mode30_vnci import Mode30RawIo
from .vnci import DpuClient


def _reads(contract):
    return {r['requestHex'] for r in contract['groups']} | {r['requestHex'] for r in contract['identityRequests']} | set(contract['sourceCommands'])


def _offer(contract):
    if contract.get('transportAddressing', {}).get('kind') != 'porsche-tp20':
        raise ElmError('manufacturer-transport-unqualified')
    return ChannelOffer(contract['txId'], contract['rxId'], bytes.fromhex(contract['transportAddressing']['offerHex']))


def _pdu(client, event, kind):
    if client._pdu_sink is None:
        return
    last, first = event['observation'], event['startedObservation']
    if (not isinstance(last, dict) or not isinstance(first, dict) or not valid_capture_clock(last)
            or not valid_capture_clock(first) or type(last.get('timestampUs')) is not int
            or type(first.get('timestampUs')) is not int):
        raise ElmError('mode30-observation-unqualified')
    client._pdu_sink({**last, 'requestHex':event['requestHex'], 'pduHex':event['pduHex'],
        'startedTimestampUs':first['timestampUs'], 'startedMonotonicTimestampUs':first['monotonicTimestampUs'],
        'transportKind':kind, 'observation':'reassembled-complete-pdu',
        'timestampSource':'host-pdu-observed', 'duringCleanup':False})


class Mode30ElmClient(ElmClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mode30_cancel = self._cancel
        self._mode30_lock = threading.Lock()
        self._channel = self._raw_io = None
        self._mode30_close_report = None
        self.allowed = set()

    def configure_manufacturer(self, profile, selected, deadline, *, identity_only=False):
        from .manufacturer_transport import prepared_read_contract
        contract = prepared_read_contract(profile, selected, identity_only=identity_only)
        offer = _offer(contract)
        if not self._mode30_lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            self._raise_if_blocked(deadline)
            if self._channel is not None:raise ElmError('mode30-channel-already-started')
            self.allowed = _reads(contract)
            self._raw_io = Mode30ElmIo(self.port, offer, clock=self._now, sleeper=self._sleeper, elm_client=self)
            self._channel = Mode30Channel(self._raw_io, offer, self.allowed, clock=self._now,
                sleeper=self._sleeper, cancel_event=self._mode30_cancel, receive_block_size=1,
                pdu_sink=lambda event:_pdu(self,event,'elm-mode30'))
            self._cancel = None  # Channel owns acquisition cancellation; local cleanup remains possible.
            self._channel.open(deadline)
            self._configured = True
        finally:self._mode30_lock.release()

    def request(self, req_hex, deadline):
        if not self._mode30_lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            self._raise_if_blocked(deadline)
            if not self._configured or self._channel is None:raise ElmError('not-configured')
            self._channel.bind_reads(self.allowed)
            return self._channel.request(req_hex, deadline)
        finally:self._mode30_lock.release()

    def idle_until(self, until, deadline):
        if not self._mode30_lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            if not self._configured or self._channel is None:raise ElmError('not-configured')
            self._channel.idle_until(until, deadline)
        finally:self._mode30_lock.release()

    def close_restore(self):
        if not self._mode30_lock.acquire(blocking=False):return dict(errors=['close-in-progress'])
        try:
            if self._mode30_close_report is not None:return deepcopy(self._mode30_close_report)
            self._configured = False
            errors = []
            if self._channel is not None:
                try:self._channel.close_channel(self._now()+1)
                except Exception as error:errors.append(str(error))
            report = self._raw_io.close_restore() if self._raw_io is not None else super().close_restore()
            report['errors'] = list(dict.fromkeys(errors+report['errors']))
            report['channelClosed'] = self._channel is None or self._channel.channel_close_verified
            self._mode30_close_report = deepcopy(report)
            return deepcopy(report)
        finally:self._mode30_lock.release()


class Mode30DpuClient(DpuClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._channel = self._raw_io = None
        self._mode30_close_report = None

    def configure_manufacturer(self, profile, selected, deadline, *, identity_only=False):
        from .manufacturer_transport import prepared_read_contract
        contract = prepared_read_contract(profile, selected, transport_kind='d-pdu-iso-tp', identity_only=identity_only)
        offer = _offer(contract)
        if not self._lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            if self._device_brand != 'vnci' or self._channel is not None:raise ElmError('manufacturer-transport-unqualified')
            self.allowed = _reads(contract)
            self._raw_io = Mode30RawIo(self.native, offer, clock=self._clock, sleeper=self._sleep, raw_log=self.raw_log)
            self._channel = Mode30Channel(self._raw_io, offer, self.allowed, clock=self._clock,
                sleeper=self._sleep, cancel_event=self.cancel_event, pdu_sink=lambda event:_pdu(self,event,'d-pdu-mode30'))
            self._channel.open(deadline)
            self._manufacturer_contract = deepcopy(contract) if identity_only else None
            self._configured = True
        finally:self._lock.release()

    def request(self, req_hex, deadline):
        if not self._lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            if not self._configured or self._channel is None:raise ElmError('not-configured')
            self._channel.bind_reads(self.allowed)
            return self._channel.request(req_hex, deadline)
        finally:self._lock.release()

    def idle_until(self, until, deadline):
        if not self._lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            if not self._configured or self._channel is None:raise ElmError('not-configured')
            self._channel.idle_until(until, deadline)
        finally:self._lock.release()

    def close_restore(self):
        if not self._lock.acquire(blocking=False):return dict(errors=['d-pdu:close-during-request'])
        try:
            if self._mode30_close_report is not None:return deepcopy(self._mode30_close_report)
            self._configured = False
            if self._channel is not None:
                try:self._channel.close_channel(self._clock()+1)
                except Exception as error:self._cleanup_errors.append(str(error))
            if self._raw_io is not None:
                try:self._raw_io.close_primitives(self._clock()+1)
                except Exception as error:self._cleanup_errors.append(str(error))
        finally:self._lock.release()
        report = super().close_restore()
        report['channelClosed'] = self._channel is None or self._channel.channel_close_verified
        self._mode30_close_report = deepcopy(report)
        return deepcopy(report)
