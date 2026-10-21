"""Write deadlines and host timeouts, using only explicit fake channels."""
import unittest
from scripts.diagnostics.elm import ElmClient, ElmError


class Clock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value


class Port:
    def __init__(self, clock):
        self.clock = clock
        self.write_timeout = None
        self.writes, self.timeouts = [], []
        self.close_count = 0
        self.advance = 0
        self.error = None
    def write(self, data):
        self.writes.append(data); self.timeouts.append(self.write_timeout)
        self.clock.value += self.advance
        if self.error: raise self.error
        return len(data)
    def read(self, size=1): return b''
    def close(self): self.close_count += 1


class WriteDeadlineTests(unittest.TestCase):
    def test_unbounded_channel_gets_one_second_limit_and_remaining_deadline(self):
        clock=Clock();port=Port(clock);client=ElmClient(port,clock=clock)
        client._write_all(b'ATI\r',10)
        client._write_all(b'ATI\r',.025)
        client._write_all(b'ATI\r',10)
        self.assertEqual(port.timeouts,[1,.025,1])
        self.assertEqual(port.close_count,0)

    def test_stricter_caller_limit_is_preserved(self):
        clock=Clock();port=Port(clock);port.write_timeout=.1;client=ElmClient(port,clock=clock)
        client._write_all(b'ATI\r',10)
        self.assertEqual(port.timeouts,[.1])

    def test_expired_deadline_writes_nothing(self):
        clock=Clock();port=Port(clock);client=ElmClient(port,clock=clock)
        with self.assertRaisesRegex(ElmError,'deadline-expired'):client._write_all(b'ATI\r',0)
        self.assertEqual(port.writes,[])

    def test_write_that_exhausts_deadline_poisoned_and_never_replayed(self):
        clock=Clock();port=Port(clock);port.advance=.2;client=ElmClient(port,clock=clock)
        with self.assertRaisesRegex(ElmError,'port-io:write-deadline'):client._write_all(b'14FF00\r',.1)
        with self.assertRaisesRegex(ElmError,'client-poisoned'):client._write_all(b'14FF00\r',10)
        self.assertEqual(port.writes,[b'14FF00\r']);self.assertEqual(port.close_count,1)

    def test_os_write_timeout_preserves_error_closes_once_and_no_replay(self):
        clock=Clock();port=Port(clock);port.error=TimeoutError('synthetic write timeout');client=ElmClient(port,clock=clock)
        with self.assertRaisesRegex(ElmError,'port-io:synthetic write timeout'):client._write_all(b'ATI\r',10)
        with self.assertRaisesRegex(ElmError,'client-poisoned'):client._write_all(b'ATI\r',10)
        self.assertEqual(port.writes,[b'ATI\r']);self.assertEqual(port.close_count,1)

    def test_os_timeout_configuration_failure_cannot_send(self):
        clock=Clock()
        class Failure(Port):
            @property
            def write_timeout(self):return 1
            @write_timeout.setter
            def write_timeout(self,value):
                if hasattr(self,'writes'):raise OSError('synthetic SetCommTimeouts failure')
        port=Failure(clock);client=ElmClient(port,clock=clock)
        with self.assertRaisesRegex(ElmError,'port-io:synthetic SetCommTimeouts'):client._write_all(b'ATI\r',10)
        self.assertEqual(port.writes,[]);self.assertEqual(port.close_count,1)
