"""Changing synthetic ECU at the byte boundary, with hand-calculated values."""
import threading
import unittest
from unittest.mock import patch

from scripts.diagnostics.manufacturer_transport import synthetic_transport_cycles, CATALOG_PROFILE
from scripts.diagnostics.elm import ElmClient
from scripts.diagnostics.session_simulator import SessionSimPort
from scripts.diagnostics.tests.test_manufacturer_acquisition import profile


class ChangingEcu(SessionSimPort):
    def __init__(self, *, fail_at=None, cancel=None, mismatch=False):
        super().__init__(CATALOG_PROFILE, 'identity-mismatch' if mismatch else 'success')
        self.measurements = 0
        self.fail_at = fail_at
        self.cancel = cancel

    def write(self, data):
        # Independent fixture values and ISO-TP framing; no historical PDU.
        if data.startswith(b'03 22 F1 00'):
            self.measurements += 1
            if self.measurements == self.fail_at:
                raise ConnectionError('disconnect')
            self.ecu_payloads.append('22F100')
            value = self.measurements * 3
            payload = bytes([0x62, 0xF1, 0, value, 0xFE]) + bytes(range(20))
            frames = [bytes([0x10, len(payload)]) + payload[:6]]
            for index, start in enumerate(range(6, len(payload), 7), 1):
                frames.append(bytes([0x20 | (index % 16)]) + payload[start:start + 7])
            self._rx.extend(('\r'.join('7E8 ' + frame.ljust(8, b'\xAA').hex(' ').upper()
                                      for frame in frames) + '\r>').encode())
            if self.cancel:
                self.cancel.set()
            return len(data)
        return super().write(data)


class SyntheticTransportTests(unittest.TestCase):
    def run_cycles(self, factory=ChangingEcu, **kwargs):
        p = profile()
        # No historical sample declarations: software must not depend on them.
        for param in p['parameters']:
            param.update(decodedSampleCount=0, examples=[])
        p['parameters'][1]['formula']['text'] = (
            'LINEAR:BaseDataType=A_INT32,BitLength=8,Encoding=2C,HighLow=1,'
            'Xa=0,Xb=2,Xc=1,DataType=A_FLOAT64,Precision=1;')
        ports = []
        def wrapped():
            port = factory()
            ports.append(port)
            return port
        result = synthetic_transport_cycles(p, ['a' * 64, 'b' * 64], port_factory=wrapped, **kwargs)
        return result, ports

    def test_changing_values_three_cycles_multiframe_one_read_per_group(self):
        out, ports = self.run_cycles(cycles=3)
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['value'] for s in out['samples'][::2]], [3, 6, 9])
        self.assertEqual([float(s['value']) for s in out['samples'][1::2]], [-1, -1, -1])
        self.assertEqual(out['completedCycles'], 3)
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 3)
        self.assertEqual(len(set(s['pduSha256'] for s in out['samples'])), 3)
        self.assertTrue(ports[0].closed)
        self.assertEqual(out['sourceKind'], 'synthetic-ecu')
        self.assertTrue(all(s['synthetic'] and s['sourceKind'] == 'synthetic-ecu' for s in out['samples']))
        self.assertFalse(out['executionEnabled']); self.assertFalse(out['vehicleDataCollected'])
        self.assertFalse(out['independentLiveVerified']); self.assertFalse(out['liveApproved'])
        self.assertFalse(out['fittedClaim']); self.assertIsNone(out['writePayload'])
        elapsed = [s['elapsedMs'] for s in out['samples']]
        self.assertEqual(elapsed, sorted(elapsed))
        self.assertEqual([t['cycle'] for t in out['transactions']], [1, 2, 3])

    def test_each_interruption_has_fresh_recovery_budget(self):
        out, ports = self.run_cycles(lambda: ChangingEcu(fail_at=2), cycles=3)
        # The fresh ECU repeatedly disconnects at its second read, so each
        # interruption has its own three-attempt budget. All ports close.
        self.assertTrue(out['ok'], out)
        self.assertEqual(len(out['transport']['recovery']), 2)
        self.assertTrue(all(p.closed for p in ports))

    def test_terminal_response_in_later_cycle_preserves_completed_group(self):
        class ShortReply(ChangingEcu):
            def write(self, data):
                if data.startswith(b'03 22 F1 00') and self.measurements == 1:
                    self.measurements += 1
                    self._rx.extend(b'7E8 04 62 F1 00 06 AA AA AA\r>')
                    return len(data)
                return super().write(data)
        out, ports = self.run_cycles(ShortReply, cycles=3)
        self.assertFalse(out['ok']); self.assertEqual(out['completedCycles'], 1)
        self.assertEqual(len(out['samples']), 2); self.assertEqual(len(out['transactions']), 2)
        self.assertFalse(out['transactions'][-1]['ok']); self.assertTrue(ports[0].closed)

    def test_identity_change_on_recovery_stops_before_measurement(self):
        count = 0
        def factory():
            nonlocal count
            count += 1
            return ChangingEcu(fail_at=2, mismatch=count > 1)
        out, ports = self.run_cycles(factory, cycles=3)
        self.assertEqual(out['error'], 'identity-mismatch')
        self.assertEqual(out['completedCycles'], 1)
        self.assertEqual(len(out['samples']), 2)
        self.assertEqual(ports[1].measurements, 0)
        self.assertTrue(all(p.closed for p in ports))

    def test_cleanup_failure_prevents_opening_replacement(self):
        close = ElmClient.close_restore
        def failed_restore(client):
            result = close(client)
            if client._poisoned:
                result['errors'].append('ATPC:synthetic-restore-failure')
            return result
        with patch.object(ElmClient, 'close_restore', failed_restore):
            out, ports = self.run_cycles(lambda: ChangingEcu(fail_at=2), cycles=3)
        self.assertEqual(out['error'], 'manufacturer-close-failed')
        self.assertEqual(len(ports), 1)
        self.assertTrue(ports[0].closed)

    def test_live_and_invalid_cycles_never_open(self):
        def forbidden():
            raise AssertionError('port opened')
        for kwargs in ({'mode': 'live'}, *({'cycles': c} for c in (0, -1, True, 1001, 1.5))):
            out, ports = self.run_cycles(forbidden, **kwargs)
            self.assertFalse(out['ok']); self.assertEqual(ports, [])

    def test_non_synthetic_port_closed_without_any_traffic(self):
        def factory():
            port = ChangingEcu(); port.trafficKind = 'physical'
            return port
        out, ports = self.run_cycles(factory)
        self.assertEqual(out['error'], 'synthetic-port-required')
        self.assertEqual(ports[0].writes, []); self.assertEqual(ports[0].measurements, 0)
        self.assertTrue(ports[0].closed)

    def test_cancel_during_read_closes_and_no_half_group(self):
        event = threading.Event()
        out, ports = self.run_cycles(lambda: ChangingEcu(cancel=event), cancel_event=event)
        self.assertEqual(out['error'], 'cancelled')
        self.assertEqual(out['samples'], []); self.assertTrue(ports[0].closed)


if __name__ == '__main__': unittest.main()
