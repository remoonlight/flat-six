from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.diagnostics import manufacturer_read as read
from scripts.diagnostics.manufacturer_transport import CATALOG_PROFILE
from scripts.diagnostics.realtime_preparation import dump, sha
from scripts.diagnostics.session_simulator import SessionSimPort, ath1_prompt, isotp_ath1_lines
from scripts.diagnostics.tests.test_manufacturer_acquisition import profile


def request():
    return {'action': 'manufacturer-read', 'generation': '981', 'ecuId': 1,
        'profileId': read.DME_PROFILE, 'parameterIds': ['a' * 64, 'b' * 64],
        'deviceId': 'bt:001122334455', 'confirmedReadOnly': True, 'x431Inactive': True}


def bundle(root, p=None):
    (root / 'profiles').mkdir()
    file = 'profiles/' + '0' * 64 + '.json'
    p = p or profile()
    dump(root / file, p)
    dump(root / 'index.json', {'profiles': [{'profileId': p['profileId'], 'ecuIds': p['ecuIds'],
        'file': file, 'sha256': sha(root / file)}]})
    manifest = {'schemaVersion': 2, 'manufacturerRead': {'schemaVersion': 1,
        'catalogPath': read.CATALOG_FILE, 'catalogSha256': sha(read.ROOT / read.CATALOG_FILE)},
        'code': [{'path': name, 'sha256': sha(read.ROOT / name)} for name in read.READ_CODE],
        'outputs': [{'path': 'index.json', 'sha256': sha(root / 'index.json')}]}
    dump(root / 'manifest.json', manifest)
    return manifest


class ReadTests(unittest.TestCase):
    def test_malformed_source_shapes_finish_before_device_open(self):
        cases = [('manifest', []), ('manifest', None), ('manufacturerRead', [1]),
                 ('code', None), ('code', [None]), ('outputs', [None]),
                 ('index', []), ('profiles', [None]), ('profile', []),
                 ('parameters', [None]), ('identity', [None])]
        for location, value in cases:
            with self.subTest(location=location, value=value), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); manifest = bundle(root)
                profile_file = root / 'profiles' / ('0' * 64 + '.json')
                index = json.loads((root / 'index.json').read_text())
                if location == 'manifest':
                    manifest = value
                elif location in ('manufacturerRead', 'code', 'outputs'):
                    manifest[location] = value
                elif location in ('index', 'profiles'):
                    index = value if location == 'index' else {'profiles': value}
                    dump(root / 'index.json', index)
                    manifest['outputs'][0]['sha256'] = sha(root / 'index.json')
                else:
                    damaged = value if location == 'profile' else {**profile(), location: value}
                    dump(profile_file, damaged)
                    index['profiles'][0]['sha256'] = sha(profile_file)
                    dump(root / 'index.json', index)
                    manifest['outputs'][0]['sha256'] = sha(root / 'index.json')
                dump(root / 'manifest.json', manifest)
                with patch('scripts.diagnostics.connection.open_selected_port') as opened, \
                        patch('scripts.diagnostics.vnci.DpuClient') as native:
                    out = read.read_manufacturer(request(), root=root)
                opened.assert_not_called(); native.assert_not_called()
                self.assertFalse(out['ok'])
                self.assertEqual(out['error'], 'manufacturer-read-bundle-required')
                self.assertTrue(out['sourceErrorDetail'])
                self.assertIs(out['transport']['openAttempted'], False)
                self.assertFalse(out['transport']['closed'])
                self.assertEqual(out['requestCount'], 0)
                self.assertFalse(out['executionEnabled']); self.assertFalse(out['vehicleDataCollected'])

    def test_bad_json_and_encoding_retain_source_error_evidence(self):
        for damaged, cause in [(b'{', 'JSONDecodeError'), (b'\xff', 'UnicodeDecodeError')]:
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); (root / 'manifest.json').write_bytes(damaged)
                with patch('scripts.diagnostics.connection.open_selected_port') as opened:
                    out = read.read_manufacturer(request(), root=root)
                opened.assert_not_called()
                self.assertEqual(out['error'], 'manufacturer-read-bundle-required')
                self.assertTrue(out['sourceErrorDetail'].startswith(cause + ':'))
                self.assertIs(out['transport']['openAttempted'], False)

    def test_malformed_source_cli_emits_finished_without_false_open_claim(self):
        for check_only in (False, True):
            with self.subTest(check_only=check_only), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); dump(root / 'manifest.json', [])
                sink = io.StringIO()
                with patch.dict('os.environ', {'PORSCHE981_REALTIME_PREPARATION': str(root)}), \
                        patch('scripts.diagnostics.connection.open_selected_port') as opened:
                    code = read.run(io.StringIO(json.dumps(request()) + '\n'), sink, check_only=check_only)
                opened.assert_not_called(); self.assertEqual(code, 1)
                events = [json.loads(line) for line in sink.getvalue().splitlines()]
                self.assertEqual([e['event'] for e in events], ['finished'])
                out = events[0]['result']
                self.assertEqual(out['error'], 'manufacturer-read-bundle-required')
                self.assertIn('AttributeError', out['sourceErrorDetail'])
                self.assertIs(out['transport']['openAttempted'], False)
                self.assertEqual(out['requestCount'], 0)

    def test_preopen_validation_failure_reports_no_open_attempt_without_claiming_close(self):
        with patch.object(read, 'verified_profile', side_effect=ValueError('SYNTHETIC source drift')), \
                patch('scripts.diagnostics.connection.open_selected_port') as opened:
            for req in (request(), {**request(), 'generation': '982'}):
                out = read.read_manufacturer(req)
                self.assertFalse(out['ok'])
                self.assertIs(out['transport']['openAttempted'], False)
                self.assertFalse(out['transport']['closed'])
                self.assertEqual(out['requestCount'], 0)
                self.assertFalse(out['executionEnabled'])
                self.assertFalse(out['vehicleDataCollected'])
            opened.assert_not_called()

    def test_failed_port_or_native_constructor_is_an_attempt_not_a_safe_preopen_failure(self):
        for device in ('bt:001122334455', 'vnci:123'):
            with patch.object(read, 'verified_profile', return_value=profile()), \
                    patch('scripts.diagnostics.connection.open_selected_port', side_effect=OSError('SYNTHETIC open failure')) as opened, \
                    patch('scripts.diagnostics.vnci.DpuClient', side_effect=OSError('SYNTHETIC native failure')) as native:
                out = read.read_manufacturer({**request(), 'deviceId': device})
                self.assertFalse(out['ok'])
                self.assertIs(out['transport']['openAttempted'], True)
                self.assertFalse(out['transport']['closed'])
                self.assertEqual(opened.call_count + native.call_count, 1)

    def test_check_only_and_stream_validation_report_no_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            out = read.read_manufacturer(request(), root=root, check_only=True)
            self.assertTrue(out['ok'])
            self.assertIs(out['transport']['openAttempted'], False)
        for line in ('{bad}\n', json.dumps({**request(), 'generation': '982'}) + '\n'):
            sink = io.StringIO()
            self.assertEqual(read.run(io.StringIO(line), sink), 1)
            out = json.loads(sink.getvalue())['result']
            self.assertIs(out['transport']['openAttempted'], False)
            self.assertEqual(out['requestCount'], 0)

    def test_successful_mocked_port_reports_attempt_and_actual_close(self):
        out, ports = self.run_session()
        self.assertTrue(out['ok'], out)
        self.assertIs(out['transport']['openAttempted'], True)
        self.assertTrue(out['transport']['closed'])
        self.assertTrue(ports[0].closed)

    def test_stream_exception_after_dispatch_never_claims_no_open_attempt(self):
        for error in (OSError('SYNTHETIC dispatch failure'), TypeError('SYNTHETIC output failure')):
            sink = io.StringIO()
            with patch.object(read, 'read_manufacturer', side_effect=error):
                self.assertEqual(read.run(io.StringIO(json.dumps(request()) + '\n'), sink), 1)
            out = json.loads(sink.getvalue())['result']
            self.assertIs(out['transport']['openAttempted'], True)
            self.assertFalse(out['transport']['closed'])

    def test_preflight_needs_no_fixture_and_never_opens_device(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            with patch('scripts.diagnostics.connection.open_selected_port', side_effect=AssertionError('opened')):
                out = read.read_manufacturer(request(), root=root, check_only=True)
            self.assertTrue(out['ok'], out)
            self.assertTrue(out['checkOnly'])
            self.assertFalse(out['vehicleDataCollected'])
            self.assertFalse(out['executionEnabled'])
            self.assertFalse(out['liveVerified'])

    def test_forbidden_fields_and_missing_flags_fail_before_definitions_or_device(self):
        cases = [{**request(), key: value} for key, value in (
            ('requestHex', '2EF100'), ('txId', 0x7E0), ('mode', 'live'), ('sourceKind', 'physical'),
            ('root', 'somewhere'), ('formula', {}), ('confirmedReadOnly', 1), ('x431Inactive', False))]
        cases += [None, [], {**request(), 'ecuId': True}, {**request(), 'generation': '982'},
                  {**request(), 'profileId': 'other'}, {**request(), 'deviceId': None}]
        with patch.object(read, 'verified_profile', side_effect=AssertionError('definitions loaded')):
            for req in cases:
                with self.subTest(req=req):
                    out = read.read_manufacturer(req)
                    self.assertFalse(out['ok']); self.assertFalse(out['executionEnabled'])

    def test_selection_budget_and_cycles_fail_before_loader(self):
        cases = [{**request(), 'parameterIds': ids} for ids in ([], ['a' * 64] * 2,
            ['a' * 64] * 13, ['abc'], ['A' * 64], [1])]
        cases += [{**request(), 'budgetSeconds': n} for n in (0, -1, 61, True, '30', float('nan'), float('inf'))]
        cases += [{**request(), 'cycleCount': n} for n in (0, 1001, True, 1.5)]
        with patch.object(read, 'verified_profile', side_effect=AssertionError('definitions loaded')):
            for req in cases:
                with self.subTest(req=req): self.assertFalse(read.read_manufacturer(req)['ok'])

    def test_pt3g_remains_explicitly_unqualified(self):
        out = read.read_manufacturer({**request(), 'deviceId': 'pt3g:123'})
        self.assertEqual(out['error'], 'pt3g-vehicle-transport-not-qualified')

    def test_stale_or_incomplete_code_catalog_and_index_fail_before_device(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = bundle(root)
            cases = []
            bad = deepcopy(manifest); bad.pop('manufacturerRead'); cases.append(bad)
            bad = deepcopy(manifest); bad['code'].pop(); cases.append(bad)
            bad = deepcopy(manifest); bad['code'][0]['sha256'] = '0' * 64; cases.append(bad)
            bad = deepcopy(manifest); bad['code'][0].pop('sha256'); cases.append(bad)
            bad = deepcopy(manifest); bad['code'][0]['path'] = 'scripts/../evil.py'; cases.append(bad)
            bad = deepcopy(manifest); bad['code'].append(bad['code'][0]); cases.append(bad)
            bad = deepcopy(manifest); bad['manufacturerRead']['catalogSha256'] = '0' * 64; cases.append(bad)
            bad = deepcopy(manifest); bad['manufacturerRead']['catalogPath'] = 'other.json'; cases.append(bad)
            bad = deepcopy(manifest); bad['outputs'][0]['sha256'] = '0' * 64; cases.append(bad)
            bad = deepcopy(manifest); bad['outputs'].append(bad['outputs'][0]); cases.append(bad)
            with patch('scripts.diagnostics.connection.open_selected_port', side_effect=AssertionError('opened')):
                for bad in cases:
                    dump(root / 'manifest.json', bad)
                    self.assertFalse(read.read_manufacturer(request(), root=root)['ok'])

    def test_profile_binding_and_embedded_request_cannot_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); manifest = bundle(root)
            file = root / 'profiles' / ('0' * 64 + '.json')
            p = profile(); p['parameters'][0]['requestHex'] = '2EF100'
            dump(file, p)
            self.assertIn('source-hash-mismatch', read.read_manufacturer(request(), root=root)['error'])
            variants = [dict(profile(), **change) for change in ({'profileId': 'other'}, {'ecuIds': [9]})]
            variants.append(p)
            for p in variants:
                dump(file, p)
                index = json.loads((root / 'index.json').read_text())
                index['profiles'][0]['sha256'] = sha(file); dump(root / 'index.json', index)
                manifest['outputs'][0]['sha256'] = sha(root / 'index.json'); dump(root / 'manifest.json', manifest)
                with patch('scripts.diagnostics.connection.open_selected_port', side_effect=AssertionError('opened')):
                    self.assertFalse(read.read_manufacturer(request(), root=root)['ok'])

    def run_session(self, *, modify=lambda port: None, req=None, **kwargs):
        # A mocked OS serial open exercises the real ELM parser, not hardware.
        ports = []
        # Explicit synthetic user decisions: grant up to three choices unless a
        # test supplies its own controller. Production never chooses for users.
        def synthetic_choice(episode, attempt, deadline, notify):
            notify()
            return attempt < 3
        kwargs.setdefault('wait_retry', synthetic_choice)
        def opened(device, *, adapter_model=None, serial_baud=None):
            self.assertEqual(device, (req or request())['deviceId'])
            self.assertEqual(adapter_model, (req or request()).get('adapterModel'))
            self.assertEqual(serial_baud, (req or request()).get('serialBaud'))
            port = SessionSimPort(CATALOG_PROFILE)
            port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('62F1002A07')))
            modify(port); ports.append(port)
            return port, 'mocked-os-port'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            with patch('scripts.diagnostics.connection.open_selected_port', side_effect=opened):
                out = read.read_manufacturer(req or request(), root=root, **kwargs)
        return out, ports

    def test_usb_new_elm_family_settings_reach_real_read_only_client_and_identity_checks(self):
        for model, ati in (('ELM327', b'ELM327 v1.5\r>'), ('OBDKey', b'OBDKey 1.4\r>'), ('OBDLink', b'STN1170 v4.0\r>')):
            def modify(port):
                original = port.write
                def write(data):
                    result = original(data)
                    if data == b'ATI\r':
                        port._rx[:] = ati
                    return result
                port.write = write
            req = {**request(), 'deviceId': 'usb:' + 'a' * 64, 'adapterModel': model}
            out, ports = self.run_session(req=req, modify=modify)
            self.assertTrue(out['ok'], out.get('error'))
            self.assertEqual(out['transport']['kind'], 'elm-iso-tp')
            self.assertEqual([value['value'] for value in out['samples']], [42, 7])
            self.assertTrue(ports[0].closed)
            self.assertFalse(any(hx.startswith(('14', '2E', '31', '34', '36')) for hx in ports[0].ecu_payloads))

    def test_invalid_adapter_model_is_rejected_before_definition_or_port_access(self):
        with patch.object(read, 'verified_profile', side_effect=AssertionError('loaded')):
            for model in ([], {}, None, 'CANtieCAR'):
                out = read.read_manufacturer({**request(), 'adapterModel': model})
                self.assertEqual(out['error'], 'adapter-model-invalid')

    def test_shared_physical_branch_fresh_identity_and_group_once_with_mocked_os(self):
        out, ports = self.run_session()
        self.assertTrue(out['ok'], out)
        self.assertEqual([s['value'] for s in out['samples']], [42, 7])
        self.assertEqual(ports[0].ecu_payloads.count('22F100'), 1)
        self.assertEqual(ports[0].ecu_payloads[0], '1089')
        self.assertTrue(out['vehicleDataCollected']); self.assertFalse(out['simulation'])
        self.assertTrue(all(not s['synthetic'] for s in out['samples']))
        self.assertFalse(out['liveVerified']); self.assertFalse(out['independentLiveVerified'])
        self.assertTrue(out['transport']['closed']); self.assertTrue(ports[0].closed)

    def test_mismatching_fresh_identity_never_sends_measurements(self):
        def mismatch(port):
            broken = SessionSimPort(CATALOG_PROFILE, scenario='identity-mismatch')
            port.ecu_map.update(broken.ecu_map)
        out, ports = self.run_session(modify=mismatch)
        self.assertEqual(out['error'], 'identity-mismatch')
        self.assertNotIn('22F100', ports[0].ecu_payloads)
        self.assertFalse(out['vehicleDataCollected']); self.assertTrue(ports[0].closed)

    def test_received_frames_are_adapter_lines_with_host_arrival_not_invented_tx(self):
        frames, pdus, cycles = [], [], []
        out, _ = self.run_session(on_frame=frames.append, on_pdu=pdus.append, on_cycle=cycles.append)
        self.assertTrue(out['ok'], out)
        self.assertGreater(len(frames), 0)
        self.assertTrue(all(frame['canId'] == 0x7E8 and frame['extended'] is False for frame in frames))
        self.assertTrue(all(frame['timestampSource'] == 'host-chunk-arrival' and frame['timestampUs'] > 0 for frame in frames))
        self.assertTrue(all(0 < len(bytes.fromhex(frame['dataHex'])) <= 8 for frame in frames))
        self.assertTrue(pdus); self.assertEqual(len(cycles), 1)
        self.assertEqual(out['transport']['rawLog'], [], "explicit sinks work with ordinary byte logging disabled")

    def test_wrong_echo_or_partial_group_never_produces_samples(self):
        for pdu in ('62F1012A07', '62F1002A', '7F2233'):
            out, ports = self.run_session(modify=lambda port: port.ecu_map.update(
                {'22F100': ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex(pdu)))}))
            self.assertFalse(out['ok']); self.assertEqual(out['samples'], [])
            self.assertTrue(ports[0].closed)

    def test_verified_physical_entry_isolates_explicit_unavailable_on_mocked_os_port(self):
        from scripts.diagnostics.tests.test_realtime_preparation import field
        p = profile(); p['parameters'][1] = field('b', '22F101')
        port = SessionSimPort(CATALOG_PROFILE)
        port.ecu_map.update({'22F100': ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('7F2231'))),
                            '22F101': ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('62F10107')))})
        events = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root, p)
            with patch('scripts.diagnostics.connection.open_selected_port', return_value=(port, 'mocked-os-port')):
                out = read.read_manufacturer({**request(), 'cycleCount': 3}, root=root, on_cycle=events.append)
        self.assertTrue(out['ok'], out)
        self.assertEqual(port.ecu_payloads.count('22F100'), 1)
        self.assertEqual(port.ecu_payloads.count('22F101'), 3)
        self.assertEqual([len(e['unavailableParameters']) for e in events], [1, 0, 0])
        self.assertEqual([s['value'] for s in out['samples']], [7, 7, 7])
        self.assertEqual(out['unavailableParameters'][0]['connectionEpoch'], 1)
        self.assertTrue(out['transport']['closed']); self.assertTrue(port.closed)
        self.assertFalse(out['liveVerified'])

    def test_cancel_before_open_and_after_cycle_preserves_complete_samples(self):
        event = threading.Event(); event.set()
        out, ports = self.run_session(cancel_event=event)
        self.assertEqual(out['error'], 'cancelled'); self.assertEqual(ports, [])
        event.clear(); events = []
        def stop(value): events.append(value); event.set()
        out, ports = self.run_session(req={**request(), 'cycleCount': None}, cancel_event=event, on_cycle=stop)
        self.assertEqual(out['error'], 'cancelled'); self.assertEqual(out['completedCycles'], 1)
        self.assertEqual(out['samples'], events[0]['samples'])
        self.assertFalse(events[0]['simulation']); self.assertTrue(events[0]['vehicleDataCollected'])
        self.assertTrue(ports[0].closed)
        self.assertEqual(out['transport']['rawLog'], [], "ordinary streamed live values do not duplicate raw bytes")

    def test_explicit_three_choices_requalify_same_device_then_stop_without_more_choices(self):
        for fail_count in (1, 4):
            number = 0
            def disconnect(port):
                nonlocal number
                number += 1
                if number <= fail_count:
                    write = port.write
                    def interrupted(data):
                        if data.startswith(b'03 22 F1 00'): raise ConnectionError('disconnect')
                        return write(data)
                    port.write = interrupted
            out, ports = self.run_session(modify=disconnect)
            self.assertEqual(len(ports), 2 if fail_count == 1 else 4)
            self.assertTrue(all(port.closed for port in ports))
            self.assertTrue(all(port.ecu_payloads[0] == '1089' for port in ports))
            if fail_count == 1:
                self.assertTrue(out['ok'], out)
                self.assertTrue(out['transport']['recovery'][0]['recovered'])
            else:
                self.assertEqual(out['error'], 'manual-retry-required')
                self.assertEqual(len(out['transport']['recovery'][0]['attempts']), 3)

    def test_connection_epoch_changes_only_after_requalified_recovery_and_is_saved_in_events(self):
        opened = 0
        events = []
        def modify(port):
            nonlocal opened
            opened += 1
            if opened == 1:
                write = port.write
                reads = 0
                def interrupted(data):
                    nonlocal reads
                    if data.startswith(b'03 22 F1 00'):
                        reads += 1
                        if reads == 2:
                            raise ConnectionError('disconnect')
                    return write(data)
                port.write = interrupted
        # One complete response precedes an interrupted request in the next cycle.
        with patch('threading.Event.wait', return_value=False):
            out, ports = self.run_session(modify=modify, req={**request(), 'cycleCount': 3}, on_cycle=events.append)
        self.assertTrue(out['ok'], out)
        self.assertEqual(len(ports), 2)
        self.assertEqual([s['connectionEpoch'] for s in out['samples']], [1, 1, 2, 2, 2, 2])
        self.assertEqual(out['samples'], [s for event in events for s in event['samples']])
        self.assertTrue(all(event['samples'][0]['elapsedMs'] == event['samples'][1]['elapsedMs'] for event in events))
        self.assertTrue(all(port.closed for port in ports))

    def test_recovery_notifications_arrive_before_reopen_and_response(self):
        opened = 0
        events = []
        def modify(port):
            nonlocal opened
            opened += 1
            if opened == 1:
                write = port.write
                def disconnected(data):
                    if data.startswith(b'03 22 F1 00'):
                        raise ConnectionError('disconnect')
                    return write(data)
                port.write = disconnected
        def notified(event):
            events.append((dict(event), opened))
        out, ports = self.run_session(modify=modify, on_connection=notified)
        self.assertTrue(out['ok'], out)
        self.assertEqual([e['phase'] for e, _ in events], ['interrupted', 'awaiting-retry', 'retrying', 'qualified', 'recovered'])
        self.assertEqual([n for _, n in events], [1, 1, 1, 2, 2])
        self.assertEqual([e['connectionEpoch'] for e, _ in events], [1, 1, 1, 2, 2])
        self.assertEqual([e['sequence'] for e, _ in events], [1, 2, 3, 4, 5])
        self.assertEqual(out['connectionEvents'], [e for e, _ in events])
        self.assertEqual(out['connectionEventCount'], 5)
        self.assertTrue(all(port.closed for port in ports))

    def test_security_negative_recovery_response_never_reports_recovered_or_replays(self):
        opened = 0
        events = []
        def modify(port):
            nonlocal opened
            opened += 1
            if opened == 1:
                write = port.write
                def disconnected(data):
                    if data.startswith(b'03 22 F1 00'):
                        raise ConnectionError('disconnect')
                    return write(data)
                port.write = disconnected
            else:
                port.ecu_map['22F100'] = ath1_prompt(isotp_ath1_lines(0x7E8, bytes.fromhex('7F2233')))
        out, ports = self.run_session(modify=modify, on_connection=events.append)
        self.assertFalse(out['ok'])
        self.assertEqual(len(ports), 2)
        self.assertEqual([e['phase'] for e in events], ['interrupted', 'awaiting-retry', 'retrying', 'qualified', 'failed'])
        self.assertFalse(out['transport']['recovery'][0]['recovered'])
        self.assertFalse(out['transport']['recovery'][0]['attempts'][0]['ok'])
        self.assertEqual(out['samples'], [])

    def test_cancel_during_retry_notification_does_not_open_again(self):
        cancel = threading.Event()
        events = []
        def modify(port):
            write = port.write
            def disconnected(data):
                if data.startswith(b'03 22 F1 00'):
                    raise ConnectionError('disconnect')
                return write(data)
            port.write = disconnected
        def notified(event):
            events.append(dict(event))
            if event['phase'] == 'retrying': cancel.set()
        out, ports = self.run_session(modify=modify, cancel_event=cancel, on_connection=notified)
        self.assertEqual(out['error'], 'cancelled')
        self.assertEqual(len(ports), 1)
        self.assertEqual([e['phase'] for e in events], ['interrupted', 'awaiting-retry', 'retrying', 'failed'])
        self.assertEqual(events[-1]['reason'], 'cancelled')

    def test_declining_after_three_manual_choices_notifies_one_terminal_failure(self):
        events = []
        def modify(port):
            write = port.write
            def disconnected(data):
                if data.startswith(b'03 22 F1 00'):
                    raise ConnectionError('disconnect')
                return write(data)
            port.write = disconnected
        out, ports = self.run_session(modify=modify, on_connection=events.append)
        self.assertEqual(out['error'], 'manual-retry-required')
        self.assertEqual(len(ports), 4)
        self.assertEqual([e['attempt'] for e in events if e['phase'] == 'retrying'], [1, 2, 3])
        self.assertEqual([e['phase'] for e in events].count('failed'), 1)
        self.assertEqual(events[-1]['reason'], 'manual-retry-required')
        self.assertFalse(out['transport']['recovery'][0]['recovered'])
        self.assertTrue(all(port.closed for port in ports))

    def test_no_controller_never_reopens_physical_transport(self):
        def modify(port):
            write = port.write
            def fail(data):
                if data.startswith(b'03 22 F1 00'): raise ConnectionError('disconnect')
                return write(data)
            port.write = fail
        out, ports = self.run_session(modify=modify, wait_retry=None)
        self.assertEqual(out['error'], 'manual-retry-required')
        self.assertEqual(len(ports), 1)
        self.assertTrue(ports[0].closed)
        self.assertEqual([e['phase'] for e in out['connectionEvents']], ['interrupted', 'failed'])

    def test_manual_choices_can_exceed_three_and_keep_original_deadline(self):
        opened = 0
        deadlines = []
        def modify(port):
            nonlocal opened
            opened += 1
            if opened <= 4:
                write = port.write
                def fail(data):
                    if data.startswith(b'03 22 F1 00'): raise ConnectionError('disconnect')
                    return write(data)
                port.write = fail
        def choose(episode, attempt, deadline, notify):
            deadlines.append(deadline)
            notify()
            return True
        out, ports = self.run_session(modify=modify, wait_retry=choose)
        self.assertTrue(out['ok'], out)
        self.assertEqual(len(ports), 5)
        self.assertEqual(len(set(deadlines)), 1)
        waiting = [e for e in out['connectionEvents'] if e['phase'] == 'awaiting-retry']
        self.assertEqual([e['attempt'] for e in waiting], [0, 1, 2, 3])
        self.assertTrue(all(e['linkReleased'] is True for e in waiting))
        self.assertTrue(all(p.closed for p in ports))

    def test_close_failure_never_offers_retry_or_reopens(self):
        def modify(port):
            def fail(data): raise ConnectionError('disconnect')
            original = port.write
            port.write = lambda data: fail(data) if data.startswith(b'03 22 F1 00') else original(data)
            port.close = lambda: (_ for _ in ()).throw(OSError('cannot-close'))
        chosen = []
        out, ports = self.run_session(modify=modify, wait_retry=lambda *args: chosen.append(args))
        self.assertEqual(len(ports), 1)
        self.assertEqual(chosen, [])
        self.assertFalse(out['transport']['closed'])
        self.assertNotIn('awaiting-retry', [e['phase'] for e in out['connectionEvents']])

    def test_worker_flushes_connection_event_before_final_without_duplicate_array(self):
        sink = io.StringIO()
        event = {'phase': 'interrupted', 'executionEnabled': True, 'liveVerified': False, 'writePayload': None}
        def handled(req, **options):
            options['on_connection'](event)
            self.assertEqual(len(sink.getvalue().splitlines()), 1)
            return {'ok': True, 'samples': [], 'transactions': [], 'connectionEvents': [event], 'connectionEventCount': 1}
        with patch.object(read, 'read_manufacturer', side_effect=handled):
            code = read.run(io.StringIO(json.dumps(request()) + '\n{"action":"stop"}\n'), sink)
        frames = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([frame['event'] for frame in frames], ['connection-state', 'finished'])
        self.assertNotIn('connectionEvents', frames[-1]['result'])
        self.assertEqual(frames[-1]['result']['connectionEventCount'], 1)

    def test_cleanup_failure_cannot_be_reported_as_success(self):
        def fail(port):
            def close(): raise OSError('cannot-close')
            port.close = close
        out, _ = self.run_session(modify=fail)
        self.assertFalse(out['ok']); self.assertFalse(out['transport']['closed'])
        self.assertEqual(len(out['samples']), 2)

    def test_control_eof_cancels_before_first_open(self):
        sink = io.StringIO()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle(root)
            with patch.dict('os.environ', {'PORSCHE981_REALTIME_PREPARATION': str(root)}), \
                    patch('scripts.diagnostics.connection.open_selected_port', side_effect=AssertionError('opened')):
                code = read.run(io.StringIO(json.dumps(request()) + '\n'), sink)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(sink.getvalue())['result']['error'], 'cancelled')

    def test_stream_invalid_or_eof_controls_never_open(self):
        for line in ('{}\n', 'x\n', '[]\n', 'x' * 65537):
            sink = io.StringIO()
            with patch.object(read, 'read_manufacturer', side_effect=AssertionError('called')):
                self.assertEqual(read.run(io.StringIO(line), sink), 1)
            self.assertEqual(json.loads(sink.getvalue())['event'], 'finished')

    def test_isolated_standalone_help(self):
        script = read.ROOT / 'scripts/run-manufacturer-read.py'
        result = subprocess.run([sys.executable, '-I', '-B', str(script), '--help'],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--check-only', result.stdout)


if __name__ == '__main__': unittest.main()


class ManualRetryTests(unittest.TestCase):
    def test_only_exact_pending_choice_grants_once_and_nothing_is_queued(self):
        cancel = threading.Event()
        control = read.ManualReadRetry(cancel)
        self.assertFalse(control.retry(1, 0))
        armed = threading.Event()
        result = []
        worker = threading.Thread(target=lambda: result.append(control.wait(1, 0, time.monotonic() + 2, armed.set)))
        worker.start()
        self.assertTrue(armed.wait(1))
        self.assertTrue(worker.is_alive())
        for pair in ((2, 0), (1, 1), (True, 0), (1, False)):
            self.assertFalse(control.retry(*pair))
        self.assertTrue(control.retry(1, 0))
        self.assertFalse(control.retry(1, 0))
        worker.join(1)
        self.assertEqual(result, [True])
        self.assertFalse(control.retry(1, 0))
        self.assertFalse(control.wait(1, 1, time.monotonic() + .02, lambda: None))

    def test_cancel_and_deadline_end_wait_without_grant(self):
        cancel = threading.Event()
        control = read.ManualReadRetry(cancel)
        self.assertFalse(control.wait(1, 0, time.monotonic() - 1, lambda: None))
        self.assertFalse(control.wait(1, 0, time.monotonic() + 1, cancel.set))
        self.assertIsNone(control.checkpoint)

    def test_fast_choice_inside_wait_notification_is_not_lost(self):
        control = read.ManualReadRetry(threading.Event())
        self.assertTrue(control.wait(1, 0, time.monotonic() + 1, lambda: self.assertTrue(control.retry(1, 0))))
