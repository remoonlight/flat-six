from __future__ import annotations

import errno
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.diagnostics.elm import ElmClient, ElmError
from scripts.diagnostics.tcp_port import TcpBytePort, TcpEndpoint


class TestEndpoint(unittest.TestCase):
    def test_ip_endpoints_roundtrip(self):
        for value in ('tcp:192.168.0.10:35000', 'tcp:[2001:db8::1]:2000', 'tcp:127.0.0.1:65535'):
            self.assertEqual(TcpEndpoint.from_device_id(value).device_id, value)

    def test_noncanonical_and_unbounded_inputs_rejected(self):
        for value in (None, '', 'tcp:head.local:35000', 'tcp:192.168.000.10:35000',
                      'tcp:192.168.0.10:0', 'tcp:192.168.0.10:65536', 'tcp:127.0.0.1:035000',
                      'tcp:[2001:0db8::1]:2000', 'tcp:[fe80::1%en0]:35000', 'tcp:127.0.0.1:1\n',
                      'tcp://127.0.0.1:35000', 'tcp:127.0.0.1:1/path', 'tcp:1.2.3.4:True'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TcpEndpoint.from_device_id(value)

    def test_port_type_and_host_validation(self):
        for host, port in [('127.0.0.1', True), ('localhost', 2000), ('127.0.0.1', 3.5), ('127.0.0.1', '35000')]:
            with self.subTest(host=host, port=port), self.assertRaises(ValueError):
                TcpEndpoint(host, port)


class LoopbackServer:
    """An explicit localhost fake, never a diagnostic head or vehicle connection."""
    def __init__(self, handler):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.endpoint = TcpEndpoint('127.0.0.1', self.listener.getsockname()[1])
        self.handler = handler
        self.error = None
        self.client = None
        self.accepted = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def serve(self):
        try:
            self.client, _ = self.listener.accept()
            self.accepted.set()
            self.client.settimeout(2)
            with self.client:
                self.handler(self.client)
        except BaseException as exc:
            self.error = exc

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        # connect() can complete before the server thread has returned from
        # accept(). Keep the listener alive until that queued connection is owned.
        self.accepted.wait(2)
        self.listener.close()
        self.thread.join(2)
        if self.thread.is_alive() and self.client is not None:
            self.client.close()
            self.thread.join(2)
        assert not self.thread.is_alive(), 'loopback fixture did not stop'
        if self.error is not None and not args[0]:
            raise self.error


class TestTcpBytePort(unittest.TestCase):
    def test_explicit_endpoint_factory_checks_model_and_clear_before_open(self):
        from scripts.diagnostics.connection import open_selected_port, valid_device_id
        self.assertTrue(valid_device_id('tcp:127.0.0.1:35000'))
        self.assertTrue(valid_device_id('tcp:[::1]:2000'))
        self.assertFalse(valid_device_id('tcp:999.0.0.1:35000'))
        with mock.patch('scripts.diagnostics.connection.TcpBytePort') as opener:
            for model in (None, 'VNCI', 'PT3G'):
                with self.assertRaisesRegex(ElmError, 'device-model-required'):
                    open_selected_port('tcp:127.0.0.1:35000', adapter_model=model)
            with self.assertRaisesRegex(ElmError, 'adapter-clear-not-qualified'):
                open_selected_port('tcp:127.0.0.1:35000', adapter_model='vLinker', require_clear_qualified=True)
            opener.assert_not_called()
            actual, name = open_selected_port('tcp:127.0.0.1:35000', adapter_model='ELM327')
            self.assertIs(actual, opener.return_value); self.assertEqual(name, 'tcp:127.0.0.1:35000')
            opener.assert_called_once_with(TcpEndpoint('127.0.0.1', 35000))

    def simulator_server(self, simulator):
        def serve(peer):
            buf = bytearray()
            try:
                while True:
                    chunk = peer.recv(1)
                    if not chunk:
                        return
                    buf.extend(chunk)
                    if chunk != b'\r':
                        continue
                    simulator.write(bytes(buf)); buf.clear()
                    while True:
                        response = simulator.read(65536)
                        if not response:
                            break
                        peer.sendall(response)
            finally:
                simulator.close()
        return LoopbackServer(serve)

    def test_real_socket_factory_named_read_and_voltage_monitor(self):
        from scripts.diagnostics.session_simulator import SessionSimPort
        from scripts.diagnostics.connection import run_voltage_monitor
        from scripts.diagnostics.sessions import run_session
        profile = 'porsche-981-2014-dme'
        simulator = SessionSimPort(profile)
        with self.simulator_server(simulator) as fixture, tempfile.TemporaryDirectory() as temp:
            out = run_session(profile_id=profile, mode='live', device_id=fixture.endpoint.device_id,
                adapter_model='ELM327', confirmed_read_only=True, x431_inactive=True,
                artifact_root=Path(temp), retain_artifacts=False)
            self.assertTrue(out['ok'], out)
            self.assertEqual(list(Path(temp).iterdir()), [])
        self.assertTrue(simulator.closed); self.assertEqual(simulator.clear_tx_count, 0)
        simulator = SessionSimPort(profile); output = io.StringIO()
        with self.simulator_server(simulator) as fixture:
            code = run_voltage_monitor(fixture.endpoint.device_id, adapter_model='ELM327', max_samples=1, stdout=output)
            self.assertEqual(code, 0, output.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[0]['type'], 'handshake')
        self.assertEqual(events[0]['serialOpens'], 0); self.assertEqual(events[0]['tcpOpens'], 1)
        self.assertEqual(events[-1]['type'], 'stopped')
        self.assertEqual(simulator.ecu_payloads, [])

    def test_real_socket_factory_manufacturer_cycles(self):
        from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
        from scripts.diagnostics.tests.test_manufacturer_read import bundle, request, CATALOG_PROFILE
        from scripts.diagnostics.manufacturer_read import read_manufacturer
        simulator = SessionSimPort(CATALOG_PROFILE)
        simulator.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('62F1002A07')))
        cycles = []
        with self.simulator_server(simulator) as fixture, tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            req = {**request(), 'deviceId': fixture.endpoint.device_id, 'adapterModel': 'ELM327', 'cycleCount': 2}
            out = read_manufacturer(req, root=root, on_cycle=cycles.append)
            self.assertTrue(out['ok'], out)
        self.assertEqual(out['completedCycles'], 2); self.assertEqual(len(cycles), 2)
        self.assertEqual(simulator.ecu_payloads.count('22F100'), 2)
        self.assertTrue(simulator.closed); self.assertEqual(simulator.clear_tx_count, 0)

    def test_stale_noise_stops_after_one_byte_before_any_send(self):
        port = mock.Mock(); port.read.return_value = b'X'
        client = ElmClient(port); client._configured = True; client._rx_id = 0x7E8
        with self.assertRaisesRegex(ElmError, 'desynchronized-rx'):
            client.request('010C', time.monotonic() + 1)
        port.read.assert_called_once_with(1); port.write.assert_not_called(); port.close.assert_called_once()

    def test_manufacturer_recovery_stops_after_connect_cleanup_failure(self):
        from scripts.diagnostics.session_simulator import SessionSimPort, _sf_payload
        from scripts.diagnostics.tests.test_manufacturer_read import bundle, request, CATALOG_PROFILE
        from scripts.diagnostics.manufacturer_read import read_manufacturer
        from scripts.diagnostics.tcp_port import TcpCleanupError
        class Broken(SessionSimPort):
            def write(self, data):
                text = data.decode('ascii').strip().upper()
                if not text.startswith('AT') and _sf_payload(text) == '22F100':
                    raise ConnectionError('fake dropped connection')
                return super().write(data)
        first = Broken(CATALOG_PROFILE)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            with mock.patch('scripts.diagnostics.connection.TcpBytePort',
                    side_effect=[first, TcpCleanupError('tcp-connect:timeout;tcp-close:failed')]) as opened:
                out = read_manufacturer({**request(), 'deviceId': 'tcp:127.0.0.1:35000', 'adapterModel': 'ELM327'}, root=root,
                    wait_retry=lambda episode, attempt, deadline, notify: (notify(), True)[1])
            self.assertFalse(out['ok']); self.assertEqual(opened.call_count, 2)
            self.assertIn('tcp-close:failed', out['error'])
            self.assertEqual(out['connectionEvents'][-1]['reason'], 'recovery-close-failed')
        self.assertTrue(first.closed)

    def test_cancelled_or_expired_request_does_not_drain_or_send(self):
        for expired in (False, True):
            port = mock.Mock(); cancel = threading.Event()
            if not expired: cancel.set()
            client = ElmClient(port, cancel_event=cancel); client._configured = True; client._rx_id = 0x7E8
            with self.assertRaisesRegex(ElmError, 'deadline-expired' if expired else 'cancelled'):
                client.request('010C', time.monotonic() - 1 if expired else time.monotonic() + 1)
            port.read.assert_not_called(); port.write.assert_not_called()

    def test_connect_sends_no_bytes_and_timeout_is_empty(self):
        received = []
        def server(peer):
            received.append(peer.recv(32))  # only local close should produce EOF
        with LoopbackServer(server) as fixture:
            port = TcpBytePort(fixture.endpoint)
            try:
                self.assertEqual(port.read(32), b'')
            finally:
                port.close()
        self.assertEqual(received, [b''])

    def test_fragmented_at_exchange_and_bounded_cleanup_with_real_elm(self):
        commands = []
        def server(peer):
            buf = bytearray()
            while True:
                chunk = peer.recv(1)
                if not chunk:
                    return
                if chunk != b'\r':
                    buf.extend(chunk)
                    continue
                command = bytes(buf); buf.clear(); commands.append(command)
                answer = {b'ATI': b'ELM327 v1.5\r>', b'ATDPN': b'A6\r>', b'ATRV': b'12.6V\r>', b'ATPC': b'OK\r>'}[command]
                for part in (answer[:2], answer[2:-1], answer[-1:]):
                    peer.sendall(part)
        with LoopbackServer(server) as fixture:
            port = TcpBytePort(fixture.endpoint)
            client = ElmClient(port, expected_model='ELM327', timeout_s=1)
            try:
                ident = client.validate_adapter(2)
                self.assertEqual(ident['atrv'], '12.6V\r>')
            finally:
                self.assertEqual(client.close_restore(), {'errors': []})
        self.assertEqual(commands, [b'ATI', b'ATDPN', b'ATRV', b'ATPC'])

    def test_peer_eof_is_a_fault_and_no_command_is_replayed(self):
        commands = []
        def server(peer):
            commands.append(peer.recv(32))
        with LoopbackServer(server) as fixture:
            port = TcpBytePort(fixture.endpoint)
            client = ElmClient(port, timeout_s=1)
            with self.assertRaisesRegex(ElmError, 'port-io:tcp-peer-closed'):
                client.send_at('ATI', time.monotonic() + 1)
            with self.assertRaisesRegex(ElmError, 'client-poisoned'):
                client.send_at('ATI', time.monotonic() + 1)
            self.assertTrue(client.close_restore()['errors'])
        self.assertEqual(commands, [b'ATI\r'])

    def test_close_interrupts_waiting_reader(self):
        done = threading.Event()
        def server(peer):
            self.assertEqual(peer.recv(1), b'')
        with LoopbackServer(server) as fixture:
            port = TcpBytePort(fixture.endpoint, read_timeout=0.25)
            observed = []
            def reader():
                try:
                    port.read()
                except (ConnectionError, OSError) as exc:
                    observed.append(str(exc))
                finally:
                    done.set()
            worker = threading.Thread(target=reader)
            worker.start(); port.close()
            self.assertTrue(done.wait(1)); worker.join(1)
            self.assertEqual(observed, ['tcp-port-closed'])
            port.close()

    def fake_port(self):
        port = TcpBytePort.__new__(TcpBytePort)
        port._socket = mock.Mock()
        port._closed = False
        port._close_error = None
        port._close_lock = threading.Lock()
        port.read_timeout, port.write_timeout = 0.05, 1
        return port

    def test_partial_send_is_reported_once_for_elm_to_reject(self):
        port = self.fake_port(); port._socket.send.return_value = 2
        with mock.patch('scripts.diagnostics.tcp_port.select.select', return_value=([], [port._socket], [])):
            client = ElmClient(port)
            with self.assertRaisesRegex(ElmError, 'short-write'):
                client.send_at('ATI', time.monotonic() + 1)
        port._socket.send.assert_called_once_with(b'ATI\r')
        port._socket.close.assert_called_once()

    def test_write_timeout_is_not_retried(self):
        port = self.fake_port()
        with mock.patch('scripts.diagnostics.tcp_port.select.select', return_value=([], [], [])):
            with self.assertRaisesRegex(TimeoutError, 'tcp-write-timeout'):
                port.write(b'ATI\r')
        port._socket.send.assert_not_called()

    def test_release_failure_is_retained_and_close_is_once(self):
        port = self.fake_port()
        port._socket.shutdown.side_effect = OSError(errno.EIO, 'shutdown failure')
        port._socket.close.side_effect = OSError(errno.EIO, 'close failure')
        for _ in range(2):
            with self.assertRaisesRegex(OSError, 'tcp-shutdown:.*;tcp-close:'):
                port.close()
        port._socket.shutdown.assert_called_once(); port._socket.close.assert_called_once()

    def test_peer_disconnected_shutdown_is_harmless(self):
        port = self.fake_port(); port._socket.shutdown.side_effect = OSError(errno.ENOTCONN, 'not connected')
        port.close(); port.close(); port._socket.close.assert_called_once()

    def test_connect_failure_closes_socket_without_retry(self):
        sock = mock.Mock(); sock.connect.side_effect = TimeoutError('connect timeout')
        with mock.patch('scripts.diagnostics.tcp_port.socket.socket', return_value=sock):
            with self.assertRaisesRegex(TimeoutError, 'connect timeout'):
                TcpBytePort(TcpEndpoint('127.0.0.1', 35000))
        sock.connect.assert_called_once(); sock.close.assert_called_once(); sock.send.assert_not_called()

    def test_invalid_timeouts_rejected_before_socket_open(self):
        for kw in ({'connect_timeout': 0}, {'connect_timeout': 11}, {'read_timeout': float('nan')},
                   {'read_timeout': True}, {'read_timeout': 1}, {'write_timeout': float('inf')}):
            with self.subTest(kw=kw), mock.patch('scripts.diagnostics.tcp_port.socket.socket') as opener:
                with self.assertRaises(ValueError):
                    TcpBytePort(TcpEndpoint('127.0.0.1', 35000), **kw)
                opener.assert_not_called()

    def test_connect_and_cleanup_failures_both_survive(self):
        sock = mock.Mock()
        sock.connect.side_effect = TimeoutError('connect timeout')
        sock.close.side_effect = OSError('close failure')
        with mock.patch('scripts.diagnostics.tcp_port.socket.socket', return_value=sock):
            with self.assertRaisesRegex(OSError, 'tcp-connect:connect timeout;tcp-close:close failure'):
                TcpBytePort(TcpEndpoint('127.0.0.1', 35000))
        sock.connect.assert_called_once(); sock.close.assert_called_once()

    def test_read_write_after_close_and_invalid_sizes(self):
        port = self.fake_port()
        for size in (-1, True, 65537, '1'):
            with self.assertRaises(ValueError):
                port.read(size)
        with self.assertRaises(TypeError):
            port.write('ATI')
        self.assertEqual(port.read(0), b''); self.assertEqual(port.write(b''), 0)
        port.close()
        with self.assertRaises(ConnectionError): port.read()
        with self.assertRaises(ConnectionError): port.write(b'ATI\r')

    def test_concurrent_close_normalizes_windows_socket_error(self):
        for operation in ('read', 'write'):
            port = self.fake_port()
            def closed(*args):
                port._closed = True
                raise OSError(10038, 'operation on nonsocket')
            with mock.patch('scripts.diagnostics.tcp_port.select.select', side_effect=closed):
                with self.assertRaisesRegex(ConnectionError, 'tcp-port-closed'):
                    port.read() if operation == 'read' else port.write(b'ATI\r')


if __name__ == '__main__':
    unittest.main()
