import hashlib
import json
import re
import shlex
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.diagnostics.x431_reference import (
    check_runtime, verify_windows_tools, inspect_capture, collect_capture, MAX_CAPTURE,
    _shell_command, _shell_output)


def capture(*, truncated=False, drops=0):
    payload=b'\x04\x0e\0'
    return b'btsnoop\0'+struct.pack('>II',1,1002)+struct.pack('>IIIIQ',
        len(payload)+(1 if truncated else 0),len(payload),0,drops,123456)+payload


class FakeAdb:
    def __init__(self, raw=None, failure=None, changed=False, size=None,
                 remote_exit=0, missing_marker=False, host_exit=0):
        self.raw=capture() if raw is None else raw
        self.failure=failure;self.changed=changed;self.size=size
        self.calls=[];self.hash_reads=0
        self.remote_exit=remote_exit;self.missing_marker=missing_marker;self.host_exit=host_exit

    def __call__(self, args, **kwargs):
        self.calls.append((args,kwargs))
        if args[-1]=='version':return subprocess.CompletedProcess(args,0,'Android Debug Bridge version 1.0.41\n','')
        self.assert_target(args)
        command=args[3:]
        marker=None
        if command[0]=='shell':
            if len(command)!=2:raise AssertionError('remote status wrapper missing')
            match=re.search(r'FLATSIX_EXIT_[0-9a-f]{32}',command[1])
            if not match:raise AssertionError('remote nonce missing')
            marker=match[0]
            command=['shell',*shlex.split(command[1].split('; _flat_six_status=$?;',1)[0])]
        if self.failure=='unauthorized' and command==['get-state']:
            return subprocess.CompletedProcess(args,1,'','fixture unauthorized')
        if self.failure==command[0]:
            if command[0]=='pull':Path(command[-1]).write_bytes(b'partial fixture')
            raise subprocess.TimeoutExpired(args,kwargs['timeout'],output=b'partial')
        if command==['get-state']:value='device'
        elif command[:2]==['shell','getprop']:value='fixture Android identity'
        elif command[:2]==['shell','stat']:value=str(self.size if self.size is not None else len(self.raw))
        elif command[:2]==['shell','sha256sum']:
            digest=hashlib.sha256(self.raw).hexdigest()
            if self.changed and self.hash_reads: digest='0'*64
            self.hash_reads+=1;value=digest+'  '+command[-1]
        elif command[0]=='pull':Path(command[-1]).write_bytes(self.raw);value='fixture copy'
        else:raise AssertionError('unexpected command '+repr(command))
        if marker is not None:
            if self.remote_exit:value='fixture permission denied'
            if not self.missing_marker:value+='\n'+marker+':'+str(self.remote_exit)+'\n'
        return subprocess.CompletedProcess(args,self.host_exit if marker else 0,value,'')

    def assert_target(self,args):
        if args[1:3]!=['-s','fixture-serial']:raise AssertionError('target not explicit')


class ReferenceTests(unittest.TestCase):
    def test_embedded_entry_finds_project_from_unrelated_cwd_and_ignored_pythonpath(self):
        entry=Path(__file__).resolve().parents[2]/'run-x431-reference.py'
        with tempfile.TemporaryDirectory() as temp:
            result=subprocess.run([sys.executable,'-I',str(entry),'--help'],cwd=temp,
                capture_output=True,text=True,timeout=10,check=True)
            self.assertIn('--pull-capture',result.stdout);self.assertIn('--check-runtime',result.stdout)

    def test_runtime_only_executes_version_and_never_falls_back_from_explicit_bad_windows_bundle(self):
        fake=FakeAdb()
        result=check_runtime(env={'PORSCHE981_ADB':'fixture-adb'},runner=fake)
        self.assertEqual(len(fake.calls),1);self.assertFalse(result['serverStarted'])
        self.assertFalse(result['directProjectHeadTransportImplemented'])
        with tempfile.TemporaryDirectory() as temp, patch('scripts.diagnostics.x431_reference.shutil.which') as which:
            with self.assertRaisesRegex(ValueError,'file-missing'):
                check_runtime(env={'PORSCHE981_ADB':str(Path(temp)/'adb.exe')},runner=fake)
            which.assert_not_called();self.assertEqual(len(fake.calls),1)

    def test_pinned_bundle_missing_changed_or_symlink_file_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);exe=root/'adb.exe';exe.write_bytes(b'fixture never executed')
            dll=root/'AdbWinApi.dll';dll.write_bytes(b'fixture dll')
            manifest=root/'manifest';manifest.write_text(json.dumps({'version':'37.0.1','files':[
                {'path':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (exe,dll)]}))
            with patch('scripts.diagnostics.x431_reference.TOOLS_MANIFEST',manifest):
                self.assertTrue(verify_windows_tools(exe)['filesVerified'])
                dll.write_bytes(b'changed fixture')
                with self.assertRaisesRegex(ValueError,'hash-mismatch'):verify_windows_tools(exe)
                with patch.object(Path,'is_symlink',lambda path:path==dll):
                    with self.assertRaisesRegex(ValueError,'missing-or-symlink'):verify_windows_tools(exe)

    def test_complete_stable_copy_preserves_raw_file_identity_logs_and_no_vehicle_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'new';fake=FakeAdb()
            report=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',out,
                env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
            self.assertTrue(report['ok'],report);self.assertTrue(report['stableCopyVerified'])
            self.assertEqual((out/'btsnoop_hci.log').read_bytes(),fake.raw)
            self.assertEqual(report['capture']['records'],1)
            self.assertFalse(report['capture']['completeVehicleSessionQualified'])
            self.assertFalse(report['vehicleRequestsSent']);self.assertFalse(report['deviceWrites'])
            for row in report['commands']:
                self.assertEqual(hashlib.sha256((out/row['log']).read_bytes()).hexdigest(),row['logSha256'])
                self.assertTrue(row['commandSucceeded'])
                self.assertEqual(row['remoteExitStatusRequired'],row['arguments'][0]=='shell')
                if row['remoteExitStatusRequired']:self.assertEqual(row['remoteExitCode'],0)
            commands=[args[3:] for args,_ in fake.calls[1:]]
            self.assertTrue(all(c[0] in ('get-state','shell','pull') for c in commands))
            self.assertNotIn('root',str(commands));self.assertNotIn('kill-server',str(commands))
            self.assertTrue(all(kwargs['timeout']<=60 for _,kwargs in fake.calls))

    def test_changed_or_malformed_capture_is_preserved_but_not_accepted(self):
        for fake,expected in [(FakeAdb(changed=True),'copy-hash-mismatch'),(FakeAdb(raw=b'not HCI capture data'),'header-version')]:
            with tempfile.TemporaryDirectory() as temp:
                out=Path(temp)/'new';report=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',out,
                    env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
                self.assertFalse(report['ok']);self.assertIn(expected,report['error'])
                self.assertEqual((out/'btsnoop_hci.log').read_bytes(),fake.raw)
                self.assertFalse(json.loads((out/'manifest.json').read_text())['ok'])

    def test_android_shell_remote_error_with_host_zero_stops_at_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            fake=FakeAdb(remote_exit=1)
            report=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',Path(temp)/'new',
                env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
            self.assertFalse(report['ok']);self.assertIn('remote-command-failed',report['error'])
            row=report['commands'][-1]
            self.assertEqual(row['exitCode'],0);self.assertEqual(row['remoteExitCode'],1)
            self.assertFalse(row['commandSucceeded']);self.assertNotIn('androidIdentity',report)
            self.assertEqual(row['arguments'],['shell','getprop','ro.product.model'])
            self.assertEqual(len(fake.calls),3)
            self.assertFalse(any(args[3:4]==['pull'] for args,_ in fake.calls))

    def test_missing_remote_status_and_host_failure_never_accept_capture(self):
        for fake,expected in [(FakeAdb(missing_marker=True),'remote-exit-status-unqualified'),
                              (FakeAdb(host_exit=1),'command-failed')]:
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as temp:
                report=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',Path(temp)/'new',
                    env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
                self.assertFalse(report['ok']);self.assertIn(expected,report['error'])
                self.assertFalse(report['commands'][-1]['commandSucceeded'])
                self.assertEqual(len(fake.calls),3)

    def test_remote_trailer_is_exact_final_bounded_and_payload_preserved(self):
        marker='FLATSIX_EXIT_'+'a'*32
        for raw in ('value',f'value\n{marker}:0\nextra',f'value\n{marker}:256\n',
                    f'value\n{marker}:-1\n',f'value\n{marker}:true\n',
                    'value\nFLATSIX_EXIT_'+'b'*32+':0\n'):
            with self.subTest(raw=raw),self.assertRaisesRegex(ValueError,'exit-status-unqualified'):
                _shell_output(raw,marker)
        for code in (0,1,127,255):
            raw=f'first\n{marker}:0\nlast\n\n{marker}:{code}\r\n'
            self.assertEqual(_shell_output(raw,marker),(f'first\n{marker}:0\nlast\n',code))

    def test_remote_command_nonce_and_posix_argument_quoting(self):
        command,marker=_shell_command(['stat','-c','%s','/sdcard/test file/btsnoop_hci.log'])
        self.assertRegex(marker,r'^FLATSIX_EXIT_[0-9a-f]{32}$')
        self.assertEqual(shlex.split(command.split('; _flat_six_status=$?;',1)[0]),
                         ['stat','-c','%s','/sdcard/test file/btsnoop_hci.log'])
        self.assertNotEqual(marker,_shell_command(['getprop','ro.product.model'])[1])

    def test_remote_failure_after_pull_preserves_copy_and_never_retries(self):
        class LastHashFails(FakeAdb):
            def __call__(self,args,**kwargs):
                if args[3:4]==['shell'] and 'sha256sum' in args[4] and self.hash_reads==1:
                    self.remote_exit=1
                return super().__call__(args,**kwargs)
        with tempfile.TemporaryDirectory() as temp:
            fake=LastHashFails();out=Path(temp)/'new'
            result=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',out,
                env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
            self.assertFalse(result['ok']);self.assertIn('remote-command-failed',result['error'])
            self.assertEqual((out/'btsnoop_hci.log').read_bytes(),fake.raw)
            self.assertEqual(result['commands'][-1]['remoteExitCode'],1)
            self.assertEqual(sum(args[3:4]==['pull'] for args,_ in fake.calls),1)
            self.assertEqual(fake.hash_reads,2)

    def test_timeout_keeps_partial_copy_and_does_not_retry_or_kill_shared_server(self):
        with tempfile.TemporaryDirectory() as temp:
            fake=FakeAdb(failure='pull');out=Path(temp)/'new'
            result=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',out,
                env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
            self.assertFalse(result['ok']);self.assertTrue(result['commands'][-1]['timeout'])
            self.assertEqual((out/'btsnoop_hci.log').read_bytes(),b'partial fixture')
            self.assertEqual(sum(args[3:4]==['pull'] for args,_ in fake.calls),1)
            self.assertNotIn('kill-server',str(fake.calls))

    def test_unauthorized_selected_device_stops_before_identity_or_file_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            fake=FakeAdb(failure='unauthorized')
            result=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',Path(temp)/'new',
                env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
            self.assertFalse(result['ok']);self.assertEqual(len(fake.calls),2)
            self.assertEqual(result['commands'][0]['exitCode'],1)

    def test_unsafe_selectors_and_existing_output_are_rejected_before_adb(self):
        fake=FakeAdb()
        with tempfile.TemporaryDirectory() as temp:
            for serial,remote in [('', '/sdcard/btsnoop_hci.log'),('-d','/sdcard/btsnoop_hci.log'),
                    ('fixture-serial','/sdcard/../btsnoop_hci.log'),('fixture-serial','/sdcard/a;id/btsnoop_hci.log'),
                    ('fixture-serial','/sdcard/log.txt'),('fixture-serial','/sdcard//btsnoop_hci.log')]:
                with self.assertRaises(ValueError):collect_capture(serial,remote,Path(temp)/'new',runner=fake)
            with self.assertRaisesRegex(ValueError,'already-exists'):
                collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',temp,runner=fake)
            self.assertEqual(fake.calls,[])

    def test_remote_oversize_is_rejected_before_hash_or_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            fake=FakeAdb(size=MAX_CAPTURE+1)
            report=collect_capture('fixture-serial','/sdcard/btsnoop_hci.log',Path(temp)/'new',
                env={'PORSCHE981_ADB':'fixture-adb'},runner=fake,hash_mode='remote-sha256')
            self.assertFalse(report['ok']);self.assertIn('size-unqualified',report['error'])
            self.assertFalse(any(args[3:4]==['pull'] for args,_ in fake.calls))

    def test_file_framing_truncation_drops_and_empty_captures_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'capture';p.write_bytes(capture(truncated=True,drops=3))
            report=inspect_capture(p);self.assertFalse(report['packetCompletenessQualified'])
            self.assertEqual(report['truncatedPacketCount'],1);self.assertEqual(report['maximumDropCounter'],3)
            for raw,error in [(capture()[:-1],'truncated-payload'),(capture()+b'\0','truncated-record'),
                    (capture()[:16],'empty-capture'),(b'btsnoop\0'+struct.pack('>II',2,1002),'header-version')]:
                p.write_bytes(raw)
                with self.assertRaisesRegex(ValueError,error):inspect_capture(p)


class ReadbackAdb(FakeAdb):
    def __init__(self, *, fail_pull=0, changed_pull=0, short_pull=0, final_size=None, stat_failure=False):
        super().__init__()
        self.pulls = 0
        self.stats = 0
        self.fail_pull = fail_pull
        self.changed_pull = changed_pull
        self.short_pull = short_pull
        self.final_size = final_size
        self.stat_failure = stat_failure

    def __call__(self, args, **kwargs):
        if args[3:4] == ['shell'] and 'sha256sum' in args[4]:
            raise AssertionError('Android 6 has no SHA utility')
        is_pull = args[3:4] == ['pull']
        is_stat = args[3:4] == ['shell'] and args[4].startswith('stat ')
        if is_pull:
            self.pulls += 1
        if is_stat:
            self.stats += 1
            if self.stats == 2:
                if self.final_size is not None: self.size = self.final_size
                if self.stat_failure: self.remote_exit = 1
        result = super().__call__(args, **kwargs)
        if is_pull:
            path = Path(args[-1])
            if self.pulls == self.short_pull: path.write_bytes(self.raw[:-1])
            if self.pulls == self.changed_pull: path.write_bytes(self.raw[:-1] + b'X')
            if self.pulls == self.fail_pull:
                path.write_bytes(b'partial copy')
                result.returncode = 1
        return result


class ReadbackTests(unittest.TestCase):
    def collect(self, fake, directory):
        return collect_capture('fixture-serial', '/sdcard/btsnoop_hci.log', directory,
                               env={'PORSCHE981_ADB':'fixture-adb'}, runner=fake)

    def test_android_without_hash_tool_defaults_to_complete_independent_readbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = ReadbackAdb(); out = Path(temp)/'new'
            result = self.collect(fake, out)
            self.assertTrue(result['ok'], result)
            self.assertEqual((fake.pulls, fake.stats), (3, 2))
            self.assertEqual(result['copyVerificationMethod'], 'pull-readback')
            self.assertEqual(len(result['hostReadbackCopies']), 3)
            expected = hashlib.sha256(fake.raw).hexdigest()
            self.assertEqual(result['hostReadbackSha256Before'], expected)
            self.assertEqual(result['hostReadbackSha256After'], expected)
            self.assertEqual(result['capture']['sha256'], expected)
            self.assertNotIn('remoteSha256Before', result)
            self.assertTrue(result['verifiedReadbackCopiesRemoved'])
            self.assertEqual(list(out.glob('*.bin')), [])
            self.assertEqual((out/'btsnoop_hci.log').read_bytes(), fake.raw)
            self.assertFalse(result['independentHeadTransportVerified'])
            self.assertFalse(result['vehicleRequestsSent'])
            for row in result['commands']:
                self.assertTrue(row['commandSucceeded'])
                self.assertEqual(hashlib.sha256((out/row['log']).read_bytes()).hexdigest(), row['logSha256'])

    def test_failed_copy_stops_without_fallback_retry_or_deleting_partial_files(self):
        for number in (1, 2, 3):
            with self.subTest(copy=number), tempfile.TemporaryDirectory() as temp:
                fake = ReadbackAdb(fail_pull=number); out = Path(temp)/'new'
                result = self.collect(fake, out)
                self.assertFalse(result['ok']); self.assertEqual(fake.pulls, number)
                self.assertIn('command-failed', result['error'])
                last = ('readback-before.bin', 'btsnoop_hci.log', 'readback-after.bin')[number-1]
                self.assertEqual((out/last).read_bytes(), b'partial copy')
                self.assertFalse(result['commands'][-1]['commandSucceeded'])

    def test_same_length_content_change_in_any_copy_rejects_and_retains_evidence(self):
        for number in (1, 2, 3):
            with self.subTest(copy=number), tempfile.TemporaryDirectory() as temp:
                fake = ReadbackAdb(changed_pull=number); out = Path(temp)/'new'
                result = self.collect(fake, out)
                self.assertFalse(result['ok']); self.assertIn('copy-hash-mismatch', result['error'])
                self.assertEqual(fake.pulls, 3)
                self.assertTrue((out/'readback-before.bin').is_file())
                self.assertTrue((out/'readback-after.bin').is_file())
                self.assertTrue((out/'btsnoop_hci.log').is_file())

    def test_wrong_size_copy_stops_at_that_copy_and_keeps_it(self):
        for number in (1, 2, 3):
            with self.subTest(copy=number), tempfile.TemporaryDirectory() as temp:
                fake = ReadbackAdb(short_pull=number); out = Path(temp)/'new'
                result = self.collect(fake, out)
                self.assertFalse(result['ok']); self.assertIn('copy-size-mismatch', result['error'])
                self.assertEqual(fake.pulls, number)
                self.assertEqual(len(list(out.glob('*.bin'))) + int((out/'btsnoop_hci.log').exists()), number)

    def test_remote_final_stat_error_with_host_zero_preserves_all_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = ReadbackAdb(stat_failure=True); out = Path(temp)/'new'
            result = self.collect(fake, out)
            self.assertFalse(result['ok']); self.assertIn('remote-command-failed', result['error'])
            self.assertEqual(fake.pulls, 3)
            self.assertEqual(result['commands'][-1]['exitCode'], 0)
            self.assertEqual(result['commands'][-1]['remoteExitCode'], 1)
            self.assertEqual(len(list(out.glob('*.bin'))), 2)

    def test_size_change_after_three_matching_copies_still_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = ReadbackAdb(final_size=999); out = Path(temp)/'new'
            result = self.collect(fake, out)
            self.assertFalse(result['ok']); self.assertIn('copy-size-mismatch', result['error'])
            self.assertEqual(fake.pulls, 3); self.assertEqual(len(list(out.glob('*.bin'))), 2)

    def test_consistently_malformed_file_is_not_accepted_as_a_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = ReadbackAdb(); fake.raw = b'not HCI capture data'; out = Path(temp)/'new'
            result = self.collect(fake, out)
            self.assertFalse(result['ok']); self.assertIn('header-version', result['error'])
            self.assertEqual(fake.pulls, 3); self.assertEqual(len(list(out.glob('*.bin'))), 2)

    def test_total_deadline_is_shared_across_copies(self):
        elapsed = [0]
        class Slow(ReadbackAdb):
            def __call__(self, args, **kwargs):
                result = super().__call__(args, **kwargs)
                if args[3:4] == ['pull']: elapsed[0] = 181
                return result
        with tempfile.TemporaryDirectory() as temp, patch('scripts.diagnostics.x431_reference.time.monotonic', side_effect=lambda:elapsed[0]):
            fake = Slow(); out = Path(temp)/'new'; result = self.collect(fake, out)
            self.assertFalse(result['ok']); self.assertIn('total-timeout', result['error'])
            self.assertEqual(fake.pulls, 1)
            self.assertEqual((out/'readback-before.bin').read_bytes(), fake.raw)

    def test_invalid_verification_mode_rejected_before_any_adb(self):
        fake = FakeAdb()
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, 'hash-mode-invalid'):
            collect_capture('fixture-serial', '/sdcard/btsnoop_hci.log', Path(temp)/'new', runner=fake, hash_mode='auto-retry')
        self.assertEqual(fake.calls, [])

    def test_deadline_also_covers_local_format_and_hash_checks(self):
        from scripts.diagnostics.x431_reference import inspect_capture as inspect
        elapsed = [0]
        def slow_inspect(path):
            result = inspect(path)
            elapsed[0] = 180
            return result
        with tempfile.TemporaryDirectory() as temp, patch('scripts.diagnostics.x431_reference.time.monotonic', side_effect=lambda:elapsed[0]), patch('scripts.diagnostics.x431_reference.inspect_capture', side_effect=slow_inspect):
            fake = ReadbackAdb(); out = Path(temp)/'new'; result = self.collect(fake, out)
            self.assertFalse(result['ok']); self.assertIn('total-timeout', result['error'])
            self.assertEqual(fake.pulls, 3); self.assertEqual(len(list(out.glob('*.bin'))), 2)


if __name__=='__main__':unittest.main()
