"""Host observation clock shared with libuv; never substitute it for wall time."""
import sys
import time

CAPTURE_CLOCK = ('windows-qpc' if sys.platform == 'win32' else
                 'darwin-continuous' if sys.platform == 'darwin' else 'posix-monotonic')


def capture_now_us():
    # CPython <=3.12 monotonic() on Windows uses GetTickCount64, not QPC.
    if sys.platform == 'win32':
        return time.perf_counter_ns() // 1000
    if sys.platform == 'darwin':
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW) // 1000
    return time.monotonic_ns() // 1000


def capture_timestamp():
    return {'timestampUs': time.time_ns() // 1000,
            'monotonicClock': CAPTURE_CLOCK, 'monotonicTimestampUs': capture_now_us()}


def valid_capture_clock(value):
    keys = ('monotonicClock', 'monotonicTimestampUs', 'startedMonotonicTimestampUs')
    if not any(key in value for key in keys):
        return True
    def positive(n):
        return type(n) is int and 0 < n <= 9007199254740991
    stamp = value.get('monotonicTimestampUs')
    start = value.get('startedMonotonicTimestampUs', stamp)
    return (value.get('monotonicClock') in ('windows-qpc', 'darwin-continuous', 'posix-monotonic')
            and positive(stamp) and positive(start) and start <= stamp)
