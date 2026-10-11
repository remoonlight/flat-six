"""Private-fixture streaming worker; typed IDs only, no physical transport."""
import json
import sys
import threading

from .realtime_preparation import FLAGS, handle_ready
from .workbench import _validate
from .decode import redact_vin


def _redact(value):
    if isinstance(value, str):
        return redact_vin(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


def run(source, sink, root=None):
    cancel = threading.Event()
    def emit(event, result):
        sink.write(json.dumps({'event': event, 'result': _redact(result)}, ensure_ascii=False) + '\n')
        sink.flush()
    try:
        line = source.readline(65537)
        if len(line.encode('utf8')) > 65536 or not line.endswith('\n'):
            raise ValueError('stream-request-limit')
        request = json.loads(line)
        invalid = _validate(request)
        if invalid:
            emit('finished', invalid)
            return
        if request.get('action') != 'ready-acquire':
            raise ValueError('stream-action-invalid')
        def controls():
            while not cancel.is_set():
                line = source.readline(256)
                # EOF/invalid control also stops; no arbitrary new requests.
                try:
                    stop = not line or json.loads(line) == {'action': 'stop'}
                except (ValueError, TypeError):
                    stop = True
                if stop:
                    cancel.set()
                    return
                cancel.set()
        threading.Thread(target=controls, daemon=True).start()
        result = handle_ready(request, root=root, cancel_event=cancel, streaming=True,
                              on_cycle=lambda value: emit('cycle', value),
                              on_connection=lambda value: emit('connection-state', value))
        # Each processed group/sample was already emitted, including partial cycles.
        result.pop('samples', None)
        result.pop('transactions', None)
        result.pop('connectionEvents', None)
        emit('finished', result)
    except (ValueError, TypeError, KeyError, OSError) as error:
        emit('finished', {'ok': False, 'error': str(error), **FLAGS})


if __name__ == '__main__':
    run(sys.stdin, sys.stdout)
