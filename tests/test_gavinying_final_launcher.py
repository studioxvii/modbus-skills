"""Public safety/precision controls for the generated final native launcher."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from modbus_skills import gavinying_launcher as launcher
from modbus_skills.exporters import canonical_map_hash
from modbus_skills.modpoll import export_modpoll
from modbus_skills.read_plan import compile_read_plan


def setup(config: bytes = b'device,test_7,7,,\n') -> dict:
    return {'schema_version': 'gavinying-final-setup/v1', 'route_id': 'test',
            'config_filename': 'test.csv', 'config_sha256': hashlib.sha256(config).hexdigest(),
            'map_sha256': 'a' * 64, 'plan_sha256': 'b' * 64, 'max_runtime_seconds': 20,
            'output_directory': 'test-native-result', 'requests': [{'function_code': 3, 'unit_id': 7, 'start_offset': 17, 'quantity': 6}],
            'expected': {'test_7': {'integer': {'datatype': 'uint64', 'scale': 1},
                                    'fraction': {'datatype': 'float32', 'scale': 1},
                                    'scaled': {'datatype': 'int16', 'scale': .5}}}}


VALUES = {'test_7': {'integer': 18446744073709551615, 'fraction': .15625, 'scaled': -.5}}


class ExportValidationTests(unittest.TestCase):
    def test_full_precision_and_scaled_integer_engineering_number(self):
        self.assertEqual(VALUES, launcher.validate_values(VALUES, setup()['expected']))

    def test_reject_null_missing_extra_bool_nonfinite_and_lossy_integer(self):
        changes = [None, True, float('nan'), float('inf'), 18446744073709551616,
                   18446744073709551615.0, '18446744073709551615']
        for value in changes:
            with self.subTest(value=value):
                data = copy.deepcopy(VALUES)
                data['test_7']['integer'] = value
                with self.assertRaises(launcher.LauncherError):
                    launcher.validate_values(data, setup()['expected'])
        for data in ({}, {'test_7': {}}, {**VALUES, 'extra': {}}, {'test_7': {**VALUES['test_7'], 'extra': 1}}):
            with self.subTest(data=data), self.assertRaises(launcher.LauncherError):
                launcher.validate_values(data, setup()['expected'])

    def test_scaled_float_nonfinite_is_invalid(self):
        for key in ('fraction', 'scaled'):
            data = copy.deepcopy(VALUES)
            data['test_7'][key] = float('inf')
            with self.assertRaises(launcher.LauncherError):
                launcher.validate_values(data, setup()['expected'])

    def test_duplicate_json_keys_and_nonfinite_constants_rejected(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(launcher.LauncherError):
                launcher.parse_json(text)

    def test_bounded_identity_string_preserved(self):
        schema = {'device': {'text': {'datatype': 'string6', 'scale': 1}}}
        self.assertEqual({'device': {'text': 'ABC'}}, launcher.validate_values({'device': {'text': 'ABC'}}, schema))
        for value in (None, 123, 'toolong'):
            with self.assertRaises(launcher.LauncherError):
                launcher.validate_values({'device': {'text': value}}, schema)


@unittest.skipUnless(os.name == 'posix', 'Secure directory-fd publication needs POSIX primitives')
class LauncherLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.config = self.base / 'test.csv'
        self.config.write_bytes(b'device,test_7,7,,\n')
        self.spec = setup(self.config.read_bytes())
        self.output = self.base / 'result'
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def fake_native(self, argv, *, cwd, deadline_seconds, max_output_bytes):
        self.calls.append(argv)
        path = Path(argv[argv.index('--export') + 1])
        self.assertFalse(path.exists(), 'Native destination must be fresh')
        path.write_text(json.dumps(VALUES))
        return {'returncode': 0, 'stdout': 'rounded console', 'stderr': '',
                'timed_out': False, 'output_limited': False, 'elapsed_seconds': .01}

    def run_launcher(self, native=None, **kwargs):
        with patch.object(launcher, 'execute_native', native or self.fake_native):
            return launcher.run(self.spec, config_directory=self.base, output_directory=self.output,
                                host='127.0.0.1', port=12345, executable='modpoll', **kwargs)

    def test_success_fixed_envelope_and_owned_staging_cleanup(self):
        result = self.run_launcher()
        self.assertEqual('succeeded', result['status'])
        self.assertEqual(VALUES, result['values'])
        self.assertEqual(result, json.loads((self.output / 'result.json').read_text()))
        self.assertEqual({'.owner.json', 'result.json'}, {p.name for p in self.output.iterdir()})
        self.assertEqual(1, len(self.calls))
        argv = self.calls[0]
        self.assertEqual(['modpoll', '--once', '--tcp', '127.0.0.1', '--tcp-port', '12345', '--config'], argv[:7])
        self.assertNotIn('--timeout', argv)
        self.assertEqual(1, argv.count('--export'))

    def test_existing_success_becomes_failed_without_prior_values(self):
        self.run_launcher()
        def nulls(argv, **kwargs):
            Path(argv[argv.index('--export') + 1]).write_text(json.dumps({'test_7': {key: None for key in VALUES['test_7']}}))
            return {'returncode': 0, 'stdout': 'None', 'stderr': '', 'timed_out': False, 'output_limited': False, 'elapsed_seconds': .01}
        result = self.run_launcher(nulls)
        self.assertEqual('failed', result['status'])
        self.assertNotIn('values', result)
        published = json.loads((self.output / 'result.json').read_text())
        self.assertEqual('failed', published['status'])
        self.assertNotIn('values', published)

    def test_missing_export_exit0_and_nonzero_with_valid_export_fail(self):
        for code, write in ((0, False), (1, True)):
            def native(argv, **kwargs):
                if write:
                    Path(argv[argv.index('--export') + 1]).write_text(json.dumps(VALUES))
                return {'returncode': code, 'stdout': '', 'stderr': '', 'timed_out': False, 'output_limited': False, 'elapsed_seconds': .01}
            result = self.run_launcher(native)
            self.assertEqual('failed', result['status'])
            self.assertNotIn('values', result)

    def test_changed_config_after_success_invalidates_before_native(self):
        self.run_launcher()
        self.config.write_text('changed')
        self.calls.clear()
        result = self.run_launcher()
        self.assertEqual('failed', result['status'])
        self.assertFalse(self.calls)
        self.assertNotIn('values', json.loads((self.output / 'result.json').read_text()))

    def test_foreign_output_directory_refused_without_writes_or_native(self):
        self.output.mkdir()
        marker = self.output / 'foreign.txt'
        marker.write_text('preserve')
        with self.assertRaises(launcher.LauncherError):
            self.run_launcher()
        self.assertEqual(['foreign.txt'], [p.name for p in self.output.iterdir()])
        self.assertFalse(self.calls)

    def test_symlink_directory_and_result_refused_before_native(self):
        target = self.base / 'foreign'
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(launcher.LauncherError):
            self.run_launcher()
        self.output.unlink()
        self.run_launcher()
        (self.output / 'result.json').unlink()
        foreign = target / 'untouched.json'
        foreign.write_text('{"foreign":true}')
        (self.output / 'result.json').symlink_to(foreign)
        self.calls.clear()
        with self.assertRaises(launcher.LauncherError):
            self.run_launcher()
        self.assertEqual('{"foreign":true}', foreign.read_text())
        self.assertFalse(self.calls)

    def test_concurrent_lock_refused_and_not_removed(self):
        self.run_launcher()
        lock = self.output / '.lock'
        lock.write_text('other owner')
        self.calls.clear()
        with self.assertRaises(launcher.LauncherError):
            self.run_launcher()
        self.assertEqual('other owner', lock.read_text())
        self.assertFalse(self.calls)

    def test_collision_with_different_binding_refused(self):
        self.run_launcher()
        self.spec['map_sha256'] = 'c' * 64
        self.calls.clear()
        with self.assertRaises(launcher.LauncherError):
            self.run_launcher()
        self.assertFalse(self.calls)

    def test_native_destination_symlink_rejected_without_following(self):
        foreign = self.base / 'foreign.json'
        foreign.write_text(json.dumps(VALUES))
        def native(argv, **kwargs):
            Path(argv[argv.index('--export') + 1]).symlink_to(foreign)
            return {'returncode': 0, 'stdout': '', 'stderr': '', 'timed_out': False, 'output_limited': False, 'elapsed_seconds': .01}
        result = self.run_launcher(native)
        self.assertEqual('failed', result['status'])
        self.assertEqual(VALUES, json.loads(foreign.read_text()))

    def test_native_destination_directory_cleanup_and_failed_envelope(self):
        def native(argv, **kwargs):
            path = Path(argv[argv.index('--export') + 1])
            path.mkdir()
            (path / 'owned-native-extra.txt').write_text('diagnostic')
            return {'returncode': 0, 'stdout': '', 'stderr': '', 'timed_out': False, 'output_limited': False, 'elapsed_seconds': .01}
        result = self.run_launcher(native)
        self.assertEqual('failed', result['status'])
        self.assertEqual({'.owner.json', 'result.json'}, {p.name for p in self.output.iterdir()})

    def test_actual_overlapping_run_fails_before_second_native_call(self):
        def native(argv, **kwargs):
            with self.assertRaises(launcher.LauncherError):
                self.run_launcher()
            current = json.loads((self.output / 'result.json').read_text())
            self.assertEqual('running', current['status'])
            self.assertNotIn('values', current)
            return self.fake_native(argv, **kwargs)
        self.assertEqual('succeeded', self.run_launcher(native)['status'])
        self.assertEqual(1, len(self.calls))

    def test_config_symlink_and_hardlink_fail_before_native(self):
        original = self.base / 'original.csv'
        self.config.rename(original)
        self.config.symlink_to(original)
        self.assertEqual('failed', self.run_launcher()['status'])
        self.config.unlink()
        os.link(original, self.config)
        self.assertEqual('failed', self.run_launcher()['status'])
        self.assertFalse(self.calls)

    def test_invalid_json_and_timeout_flags_rejected(self):
        for content, timed_out in (('{"bad":NaN}', False), (json.dumps(VALUES), True)):
            def native(argv, **kwargs):
                Path(argv[argv.index('--export') + 1]).write_text(content)
                return {'returncode': 0, 'stdout': '', 'stderr': '', 'timed_out': timed_out, 'output_limited': False, 'elapsed_seconds': .01}
            result = self.run_launcher(native)
            self.assertEqual('failed', result['status'])
            self.assertNotIn('values', result)


@unittest.skipUnless(os.name == 'posix', 'Native process group test needs POSIX')
class SubprocessBoundTests(unittest.TestCase):
    def test_owned_child_timeout_and_output_cap(self):
        result = launcher.execute_native([sys.executable, '-c', 'import time; time.sleep(2)'], cwd=ROOT,
                                         deadline_seconds=.1, max_output_bytes=1024)
        self.assertTrue(result['timed_out'])
        self.assertIsNotNone(result['returncode'])
        result = launcher.execute_native([sys.executable, '-c', 'print("x" * 10000)'], cwd=ROOT,
                                         deadline_seconds=2, max_output_bytes=1024)
        self.assertTrue(result['output_limited'])
        self.assertLessEqual(len(result['stdout'].encode()) + len(result['stderr'].encode()), 1024)

    def test_single_sigterm_reaps_owned_native_child_and_publishes_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            spec = setup()
            (base / 'test.csv').write_bytes(b'device,test_7,7,,\n')
            launcher_file = base / 'read-final.py'
            launcher_file.write_text(Path(launcher.__file__).read_text().replace('COMPILED_SETUP = None', 'COMPILED_SETUP = '+repr(spec), 1))
            pidfile = base / 'native.pid'
            fake = base / 'fake-native'
            fake.write_text('#!' + sys.executable + '\nimport os,time\nfrom pathlib import Path\nPath('+repr(str(pidfile))+').write_text(str(os.getpid()))\ntime.sleep(4)\n')
            fake.chmod(0o700)
            child = subprocess.Popen([sys.executable, str(launcher_file), '--host', '127.0.0.1', '--port', '12345',
                                      '--modpoll', str(fake), '--confirm-read', 'READ'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 2
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(pidfile.exists())
                native_pid = int(pidfile.read_text())
                child.send_signal(signal.SIGTERM)
                stdout, stderr = child.communicate(timeout=3)
                self.assertNotEqual(0, child.returncode)
                receipt = json.loads(stdout)
                self.assertEqual('failed', receipt['status'])
                self.assertTrue(receipt['published'])
                self.assertNotIn('values', receipt)
                envelope = json.loads(Path(receipt['result_path']).read_text())
                self.assertEqual('failed', envelope['status'])
                self.assertNotIn('values', envelope)
                self.assertIn('signal', envelope['error'])
                self.assertEqual({'.owner.json', 'result.json'}, {p.name for p in Path(receipt['result_path']).parent.iterdir()})
                with self.assertRaises(ProcessLookupError):
                    os.kill(native_pid, 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=2)


class InvocationReceiptTests(unittest.TestCase):
    def test_success_stdout_compact_and_binds_envelope(self):
        result = {'status': 'succeeded', 'run_id': 'run', 'binding_sha256': 'a'*64,
                  'values': VALUES, 'native': {'stdout': 'x'*10000, 'stderr': 'y'*10000}}
        receipt = launcher.invocation_receipt(result, '/result.json', published=True)
        self.assertEqual(3, receipt['value_count'])
        self.assertEqual('run', receipt['run_id'])
        self.assertEqual(result['binding_sha256'], receipt['binding_sha256'])
        self.assertNotIn('values', receipt)
        self.assertNotIn('native', receipt)
        self.assertLess(len(json.dumps(receipt)), 500)

    def test_publication_failure_no_prior_result_reference(self):
        receipt = launcher.invocation_receipt({'status': 'failed', 'error': 'x'*2000}, None, published=False)
        self.assertFalse(receipt['published'])
        self.assertIsNone(receipt['result_path'])
        self.assertIsNone(receipt['run_id'])
        self.assertEqual(0, receipt['value_count'])
        self.assertLessEqual(len(receipt['error']), 300)

    def test_argument_error_is_concise_failure_not_old_result(self):
        with patch.object(sys, 'argv', ['launcher']), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            with self.assertRaises(SystemExit) as raised:
                launcher.main()
            self.assertNotEqual(0, raised.exception.code)
            receipt = json.loads(stdout.getvalue())
        self.assertEqual('failed', receipt['status'])
        self.assertFalse(receipt['published'])
        self.assertIsNone(receipt['result_path'])


class GenerationTests(unittest.TestCase):
    def inputs(self, names=('Exact Fraction',)):
        points = [{'logical_point_id': f'p{n}', 'name': name, 'route_id': 'loop', 'unit_id': 7,
                   'area': 'holding-register', 'protocol_offset': n * 2, 'word_span': 2,
                   'datatype': 'float32', 'byte_order': 'ABCD', 'byte_order_confirmed': True,
                   'normalization_status': 'confirmed', 'scale': 1} for n, name in enumerate(names)]
        canonical = {'schema_version': 'modbus-map/v1', 'points': points}
        plan = compile_read_plan(points).to_dict()
        plan['input_hashes'] = {'canonical_map': canonical_map_hash(canonical)}
        return canonical, plan

    def test_generated_launcher_and_manifest_bind_precision_carrier(self):
        result = export_modpoll(*self.inputs(), profile='gavinying-cli', mode='final')
        self.assertEqual('generated', result.status)
        files = {a.path.rsplit('/', 1)[-1]: a.as_text() for a in result.artifacts}
        self.assertIn('loop-read-final.py', files)
        compile(files['loop-read-final.py'], 'loop-read-final.py', 'exec')
        self.assertIn('loop-read-final.py', files['commands.txt'])
        self.assertNotIn('modpoll --once', files['commands.txt'])
        self.assertIn('rounded', files['README.md'].lower())
        self.assertIn('validated', files['README.md'].lower())

    def test_duplicate_sanitized_reference_names_held(self):
        result = export_modpoll(*self.inputs(('A-B', 'A B')), profile='gavinying-cli', mode='final')
        self.assertEqual('held', result.status)
        self.assertTrue(any(f.code == 'modpoll.reference-collision' for f in result.findings))


if __name__ == '__main__':
    unittest.main()
