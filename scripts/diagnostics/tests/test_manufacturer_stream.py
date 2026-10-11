import io
import json
import unittest
from unittest.mock import patch

from scripts.diagnostics.manufacturer_stream import run
from scripts.diagnostics.realtime_preparation import FLAGS


class StreamWorkerTests(unittest.TestCase):
    def test_forbidden_transport_and_invalid_actions_never_enter_acquisition(self):
        for request in [{'action': 'ready-acquire', 'serialPort': 'COM1'},
                        {'action': 'ready-plan'}, {'action': 'ready-acquire', 'cycleCount': 999}, []]:
            sink = io.StringIO()
            with patch('scripts.diagnostics.manufacturer_stream.handle_ready') as handler:
                run(io.StringIO(json.dumps(request) + '\n'), sink)
            handler.assert_not_called()
            result = json.loads(sink.getvalue())['result']
            self.assertFalse(result['ok']); self.assertFalse(result['executionEnabled'])

    def test_control_eof_stops_without_opening_a_physical_path(self):
        sink = io.StringIO()
        def handler(req, **options):
            self.assertTrue(options['cancel_event'].wait(1))
            return {'ok': False, 'error': 'cancelled', **FLAGS}
        with patch('scripts.diagnostics.manufacturer_stream.handle_ready', side_effect=handler):
            run(io.StringIO('{"action":"ready-acquire"}\n'), sink)
        self.assertEqual(json.loads(sink.getvalue())['result']['error'], 'cancelled')

    def test_emitted_identity_text_uses_existing_vin_redaction(self):
        sink = io.StringIO()
        def handler(req, **options):
            return {'ok': True, 'identity': 'WP0ZZZ98ZES000000', **FLAGS}
        with patch('scripts.diagnostics.manufacturer_stream.handle_ready', side_effect=handler):
            run(io.StringIO('{"action":"ready-acquire"}\n'), sink)
        self.assertNotIn('WP0ZZZ98ZES000000', sink.getvalue())
        self.assertTrue(json.loads(sink.getvalue())['result']['ok'])

    def test_cycles_flushed_and_final_summary_does_not_duplicate_values(self):
        sink = io.StringIO()
        def handler(req, **options):
            self.assertTrue(options['streaming'])
            options['on_cycle']({'samples': [1], **FLAGS})
            self.assertEqual(len(sink.getvalue().splitlines()), 1)
            return {'ok': True, 'samples': [1], 'transactions': [2], 'completedCycles': 1, **FLAGS}
        with patch('scripts.diagnostics.manufacturer_stream.handle_ready', side_effect=handler):
            run(io.StringIO('{"action":"ready-acquire"}\n{"action":"stop"}\n'), sink)
        frames = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([frame['event'] for frame in frames], ['cycle', 'finished'])
        self.assertNotIn('samples', frames[-1]['result'])
        self.assertNotIn('transactions', frames[-1]['result'])


if __name__ == '__main__': unittest.main()
