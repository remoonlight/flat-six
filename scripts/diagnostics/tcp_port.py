"""Bounded TCP byte transport for ELM-style Wi-Fi heads, independent of ECU protocol.

Endpoints must be explicit IP literals. Opening this port sends no application
bytes; the owning ELM client performs identity/AT checks and controls requests.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import ipaddress
import math
import re
import select
import socket
import threading


class TcpCleanupError(OSError):
    """The failed connection's socket could not be released; never retry it."""


@dataclass(frozen=True)
class TcpEndpoint:
    host: str
    port: int

    def __post_init__(self):
        if not isinstance(self.host, str) or '%' in self.host:
            raise ValueError('tcp-host-invalid')
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise ValueError('tcp-host-invalid') from exc
        if str(address) != self.host:
            raise ValueError('tcp-host-not-canonical')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('tcp-port-invalid')

    @property
    def device_id(self) -> str:
        host = '[' + self.host + ']' if ':' in self.host else self.host
        return f'tcp:{host}:{self.port}'

    @classmethod
    def from_device_id(cls, value: str) -> 'TcpEndpoint':
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError('tcp-device-id-invalid')
        match = re.fullmatch(r'tcp:(\[[0-9a-f:]+\]|[0-9.]+):([1-9][0-9]{0,4})', value)
        if match is None:
            raise ValueError('tcp-device-id-invalid')
        endpoint = cls(match[1].strip('[]'), int(match[2]))
        if endpoint.device_id != value:
            raise ValueError('tcp-device-id-invalid')
        return endpoint


def _timeout(value, maximum, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(name)
    return float(value)


class TcpBytePort:
    """One TCP connection; one send per write, no reconnect or command replay.

    A read timeout returns empty bytes, matching the existing serial BytePort.
    EOF instead raises ConnectionError so an ELM client stops using the link.
    close() interrupts I/O and retains its first release failure.
    """

    def __init__(self, endpoint: TcpEndpoint, *, connect_timeout=3.0, read_timeout=0.05, write_timeout=1.0):
        if not isinstance(endpoint, TcpEndpoint):
            raise ValueError('tcp-endpoint-required')
        connect_timeout = _timeout(connect_timeout, 10, 'tcp-connect-timeout-invalid')
        self.read_timeout = _timeout(read_timeout, 0.25, 'tcp-read-timeout-invalid')
        self.write_timeout = _timeout(write_timeout, 2, 'tcp-write-timeout-invalid')
        self.endpoint = endpoint
        self._close_lock = threading.Lock()
        self._closed = False
        self._close_error = None
        address = ipaddress.ip_address(endpoint.host)
        sock = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(connect_timeout)
            sock.connect((endpoint.host, endpoint.port))
            sock.setblocking(False)
        except BaseException as exc:
            try:
                sock.close()
            except OSError as cleanup:
                raise TcpCleanupError(f'tcp-connect:{exc};tcp-close:{cleanup}') from exc
            raise
        self._socket = sock

    def _open(self):
        if self._closed:
            raise ConnectionError('tcp-port-closed')

    def read(self, size=1):
        if type(size) is not int or not 0 <= size <= 65536:
            raise ValueError('tcp-read-size-invalid')
        self._open()
        if size == 0:
            return b''
        try:
            readable, _, _ = select.select([self._socket], [], [], self.read_timeout)
            self._open()
            if not readable:
                return b''
            try:
                data = self._socket.recv(size)
            except BlockingIOError:
                return b''
            if not data:
                raise ConnectionError('tcp-peer-closed')
            return data
        except ValueError as exc:
            # select on a concurrently closed descriptor must be a transport fault.
            raise ConnectionError('tcp-port-closed') from exc
        except OSError as exc:
            if self._closed:
                raise ConnectionError('tcp-port-closed') from exc
            raise

    def write(self, data):
        if not isinstance(data, bytes):
            raise TypeError('tcp-write-bytes-required')
        self._open()
        if not data:
            return 0
        try:
            _, writable, _ = select.select([], [self._socket], [], self.write_timeout)
            self._open()
            if not writable:
                raise TimeoutError('tcp-write-timeout')
            # Do not sendall or retry a partially sent diagnostic request.
            return self._socket.send(data)
        except ValueError as exc:
            raise ConnectionError('tcp-port-closed') from exc
        except OSError as exc:
            if self._closed:
                raise ConnectionError('tcp-port-closed') from exc
            raise

    def close(self):
        with self._close_lock:
            if self._closed:
                if self._close_error is not None:
                    raise OSError(self._close_error)
                return
            self._closed = True
            errors = []
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                if exc.errno != errno.ENOTCONN:
                    errors.append(f'tcp-shutdown:{exc}')
            finally:
                try:
                    self._socket.close()
                except OSError as exc:
                    errors.append(f'tcp-close:{exc}')
            if errors:
                self._close_error = ';'.join(errors)
                raise OSError(self._close_error)
