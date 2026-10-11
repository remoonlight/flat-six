"""Take back an existing, stopped X431 Android HCI file. No vehicle transport.

Explicit serial/path only; no root, settings changes, app launch or device writes.
ADB may start its host server during collection. We never kill a shared server.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import time

from .hashutil import sha256_file

ROOT = Path(__file__).resolve().parents[2]
TOOLS_MANIFEST = ROOT / 'data/seed/diagnostics/adb-tools-windows.v1.json'
MAX_CAPTURE = 512 * 1024 * 1024


def _shell_command(arguments):
    """Explicit remote status for Android versions without shell exit-code transport."""
    marker = 'FLATSIX_EXIT_' + secrets.token_hex(16)
    command = shlex.join(arguments) + '; _flat_six_status=$?; printf "\\n' + marker + ':%s\\n" "$_flat_six_status"'
    return command, marker


def _shell_output(stdout, marker):
    # Only the exact, final nonce trailer belongs to this invocation. A host
    # zero exit or marker-looking bytes in the command output prove nothing.
    match = re.fullmatch(r'(.*)\n' + re.escape(marker) + r':([0-9]{1,3})\r?\n?', stdout, re.S)
    if not match or int(match[2]) > 255:
        raise ValueError('adb-remote-exit-status-unqualified')
    return match[1], int(match[2])


def verify_windows_tools(executable):
    executable = Path(executable)
    if executable.name.lower() != 'adb.exe':
        raise ValueError('adb-explicit-Windows-executable-name-invalid')
    root = executable.parent
    if root.is_symlink():
        raise ValueError('adb-tools-symlink')
    manifest = json.loads(TOOLS_MANIFEST.read_text(encoding='utf8'))
    for row in manifest['files']:
        path = root / row['path']
        if path.is_symlink() or not path.is_file():
            raise ValueError('adb-tool-file-missing-or-symlink:' + row['path'])
        if path.stat().st_size != row['bytes'] or sha256_file(path).lower() != row['sha256']:
            raise ValueError('adb-tool-file-hash-mismatch:' + row['path'])
    return {'version': manifest['version'], 'fileCount': len(manifest['files']),
            'manifestSha256': sha256_file(TOOLS_MANIFEST).lower(), 'filesVerified': True}


def check_runtime(*, env=None, runner=None):
    env = dict(os.environ if env is None else env)
    executable = env.get('PORSCHE981_ADB') or shutil.which('adb', path=env.get('PATH', ''))
    if not executable:
        raise ValueError('adb-runtime-missing')
    # An explicit supplied Windows bundle cannot silently fall back to PATH.
    tools = verify_windows_tools(executable) if env.get('PORSCHE981_ADB') and Path(executable).suffix.lower() == '.exe' else None
    result = (runner or subprocess.run)([executable, 'version'], check=True, capture_output=True,
        text=True, timeout=10, env=env)
    if not re.search(r'^Android Debug Bridge version \d+\.\d+\.\d+$', result.stdout, re.M):
        raise ValueError('adb-version-not-recognized')
    if tools and not re.search(r'^Version ' + re.escape(tools['version']) + r'-\d+', result.stdout, re.M):
        raise ValueError('adb-version-and-pinned-tools-mismatch')
    return {'executable': str(executable), 'version': result.stdout.strip()[:1000],
            'tools': tools, 'devicesEnumerated': False, 'serverStarted': False,
            'directProjectHeadTransportImplemented': False, 'vehicleVerified': False}


def inspect_capture(path):
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_CAPTURE:
        raise ValueError('btsnoop-capture-size-limit')
    records = truncated = decreases = drops_max = 0
    first = last = None
    with path.open('rb') as source:
        if source.read(16) != b'btsnoop\0' + struct.pack('>II', 1, 1002):
            raise ValueError('btsnoop-header-version-or-datalink-unqualified')
        while header := source.read(24):
            if len(header) != 24:
                raise ValueError('btsnoop-truncated-record')
            original, included, flags, drops, stamp = struct.unpack('>IIIIQ', header)
            if included > original or included > 16 * 1024 * 1024:
                raise ValueError('btsnoop-invalid-packet-length')
            if len(source.read(included)) != included:
                raise ValueError('btsnoop-truncated-payload')
            records += 1;truncated += included < original;drops_max = max(drops_max, drops)
            decreases += last is not None and stamp < last
            first = stamp if first is None else first;last = stamp
    if not records:
        raise ValueError('btsnoop-empty-capture')
    return {'bytes': size, 'sha256': sha256_file(path).lower(), 'formatVersion': 1, 'dataLink': 1002,
            'records': records, 'truncatedPacketCount': truncated, 'maximumDropCounter': drops_max,
            'firstTimestampRaw': first, 'lastTimestampRaw': last, 'timestampDecreaseCount': decreases,
            'clockBasisQualified': False, 'completeVehicleSessionQualified': False,
            'packetCompletenessQualified': truncated == 0 and drops_max == 0,
            'physicalCanCapture': False, 'vehicleVerified': False}


def _selectors(serial, remote):
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', serial):
        raise ValueError('adb-explicit-serial-required')
    if (not isinstance(remote, str) or not re.fullmatch(r'/[A-Za-z0-9._/-]{1,511}', remote)
            or any(part in ('', '.', '..') for part in remote.split('/')[1:])
            or remote.rsplit('/', 1)[-1] != 'btsnoop_hci.log'):
        raise ValueError('adb-explicit-btsnoop-file-path-required')


def collect_capture(serial, remote, output, *, env=None, runner=None, hash_mode='pull-readback'):
    _selectors(serial, remote)
    if hash_mode not in ('pull-readback', 'remote-sha256'):
        raise ValueError('adb-reference-hash-mode-invalid')
    output = Path(output)
    if output.exists():
        raise ValueError('output-already-exists')
    runtime = check_runtime(env=env, runner=runner)
    env = dict(os.environ if env is None else env)
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat();deadline = time.monotonic() + 180
    report = {'kind':'x431-android-reference-capture', 'schemaVersion':1, 'ok':False,
        'startedUtc':started, 'serial':serial, 'remotePath':remote, 'runtime':runtime,
        'collectorCodeSha256':sha256_file(Path(__file__)).lower(),
        'commands':[], 'deviceWrites':False, 'vehicleRequestsSent':False,
        'independentHeadTransportVerified':False, 'adbServerMayRemainRunning':True}
    report['copyVerificationMethod'] = hash_mode
    execute = runner or subprocess.run

    def read(arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('adb-reference-total-timeout')
        marker = None
        invocation = arguments
        if arguments[0] == 'shell':
            command, marker = _shell_command(arguments[1:])
            invocation = ['shell', command]
        args = [runtime['executable'], '-s', serial, *invocation]
        index = len(report['commands']);name = f'command-{index:02d}.txt'
        try:
            result = execute(args, check=False, capture_output=True, text=True,
                             timeout=min(60, remaining), env=env)
        except subprocess.TimeoutExpired as error:
            text = f'{error.stdout or ""}\n{error.stderr or ""}'
            (output/name).write_text(text, encoding='utf8')
            report['commands'].append({'arguments':arguments, 'exitCode':None, 'timeout':True, 'log':name,
                                       'remoteExitStatusRequired':marker is not None,
                                       'remoteExitCode':None, 'commandSucceeded':False,
                                       'logSha256':sha256_file(output/name).lower()})
            raise
        (output/name).write_text(result.stdout + '\n' + result.stderr, encoding='utf8')
        row = {'arguments':arguments, 'exitCode':result.returncode, 'log':name,
               'remoteExitStatusRequired':marker is not None, 'remoteExitCode':None,
               'commandSucceeded':False, 'logSha256':sha256_file(output/name).lower()}
        report['commands'].append(row)
        stdout = result.stdout
        if marker is not None:
            try:
                stdout, row['remoteExitCode'] = _shell_output(stdout, marker)
            except ValueError:
                row['remoteExitStatusError'] = 'adb-remote-exit-status-unqualified'
                raise
        if result.returncode:
            raise RuntimeError('adb-reference-command-failed:' + str(index))
        if row['remoteExitCode'] not in (None, 0):
            raise RuntimeError('adb-reference-remote-command-failed:' + str(index))
        row['commandSucceeded'] = True
        return stdout.strip()

    def remote_hash():
        # remote contains no shell metacharacters; explicit shell read only.
        value = read(['shell', 'sha256sum', remote])
        match = re.fullmatch(r'([0-9A-Fa-f]{64})\s+\*?' + re.escape(remote), value)
        if not match:
            raise ValueError('adb-remote-sha256-output-unqualified')
        return match[1].lower()

    def pull_hash(path, expected_size):
        read(['pull', remote, str(path.resolve())])
        if path.is_symlink() or not path.is_file():
            raise ValueError('adb-pulled-capture-file-invalid')
        size = path.stat().st_size
        if size != expected_size or not 16 <= size <= MAX_CAPTURE:
            raise ValueError('adb-capture-changed-or-copy-size-mismatch')
        digest = sha256_file(path).lower()
        report.setdefault('hostReadbackCopies', []).append(
            {'path':path.name, 'bytes':size, 'sha256':digest})
        return digest

    try:
        if read(['get-state']) != 'device':
            raise ValueError('adb-selected-device-not-ready')
        report['androidIdentity'] = {name:read(['shell','getprop',property_name]) for name,property_name in (
            ('model','ro.product.model'),('buildFingerprint','ro.build.fingerprint'),('androidRelease','ro.build.version.release'))}
        size = read(['shell','stat','-c','%s',remote])
        if not re.fullmatch(r'\d{1,12}', size) or not 16 <= int(size) <= MAX_CAPTURE:
            raise ValueError('adb-remote-capture-size-unqualified')
        report['remoteBytesBefore'] = int(size)
        path = output/'btsnoop_hci.log'
        if hash_mode == 'remote-sha256':
            before = remote_hash();report['remoteSha256Before'] = before
            read(['pull', remote, str(path.resolve())])
            after = remote_hash();report['remoteSha256After'] = after
        else:
            # Independent complete copies avoid depending on Android hash tools.
            # These are ordinary ADB file reads, never a retry after a failure.
            before = pull_hash(output/'readback-before.bin', int(size))
            pull_hash(path, int(size))
            after = pull_hash(output/'readback-after.bin', int(size))
            report['hostReadbackSha256Before'] = before
            report['hostReadbackSha256After'] = after
            final_size = read(['shell','stat','-c','%s',remote])
            if final_size != size:
                raise ValueError('adb-capture-changed-or-copy-size-mismatch')
            report['remoteBytesAfter'] = int(final_size)
        if path.is_symlink() or not path.is_file():
            raise ValueError('adb-pulled-capture-file-invalid')
        report['capture'] = inspect_capture(path)
        if before != after or before != report['capture']['sha256'] or int(size) != report['capture']['bytes']:
            raise ValueError('adb-capture-changed-or-copy-hash-mismatch')
        if time.monotonic() >= deadline:
            raise TimeoutError('adb-reference-total-timeout')
        if hash_mode == 'pull-readback':
            # Remove only redundant verified copies after every content check.
            # Earlier failures retain completed and partial files with the logs.
            report['verifiedReadbackCopiesRemoved'] = []
            for name in ('readback-before.bin', 'readback-after.bin'):
                (output/name).unlink()
                report['verifiedReadbackCopiesRemoved'].append(name)
        report['stableCopyVerified'] = True;report['ok'] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report['error'] = str(error)
    finally:
        report['endedUtc'] = datetime.now(timezone.utc).isoformat()
        (output/'manifest.json').write_text(json.dumps(report, ensure_ascii=True, indent=2)+'\n', encoding='utf8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check-runtime', action='store_true')
    action.add_argument('--pull-capture', action='store_true')
    parser.add_argument('--adb', type=Path, help='explicit executable; Windows pinned bundle is checked before execution')
    parser.add_argument('--serial')
    parser.add_argument('--remote-path')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--hash-mode', choices=('pull-readback', 'remote-sha256'), default='pull-readback',
                        help='default: three complete ADB copies with host SHA-256; remote mode requires device sha256sum')
    args = parser.parse_args();env = dict(os.environ)
    if args.adb is not None:env['PORSCHE981_ADB'] = str(args.adb.resolve())
    if args.check_runtime:
        if args.serial or args.remote_path or args.output_dir or args.hash_mode != 'pull-readback':parser.error('capture options require --pull-capture')
        print(json.dumps(check_runtime(env=env), ensure_ascii=True));return 0
    if not args.serial or not args.remote_path or not args.output_dir:parser.error('explicit serial, remote path and new output directory required')
    result = collect_capture(args.serial, args.remote_path, args.output_dir, env=env, hash_mode=args.hash_mode)
    print(json.dumps({'ok':result['ok'], 'output':str(args.output_dir.resolve()), 'error':result.get('error'),
                      'deviceWrites':False, 'vehicleRequestsSent':False}, ensure_ascii=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':raise SystemExit(main())
