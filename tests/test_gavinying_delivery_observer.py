"""The offline workflow checks delivered invocation behavior, not a flag substring."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'plugins/modbus-skills/runtime'))
from run_human_workflow_tests import _gavinying_bounded_delivery
from modbus_skills.exporters import canonical_map_hash
from modbus_skills.modpoll import export_modpoll
from modbus_skills.read_plan import compile_read_plan


class GavinyingDeliveryObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        points = [{'logical_point_id': 'p1', 'name': 'Synthetic fraction', 'route_id': 'loop',
                   'unit_id': 7, 'area': 'holding-register', 'protocol_offset': 17,
                   'word_span': 2, 'datatype': 'float32', 'byte_order': 'ABCD',
                   'byte_order_confirmed': True, 'normalization_status': 'confirmed', 'scale': 1}]
        canonical = {'schema_version': 'modbus-map/v1', 'points': points}
        plan = compile_read_plan(points).to_dict()
        plan['input_hashes'] = {'canonical_map': canonical_map_hash(canonical)}
        result = export_modpoll(canonical, plan, profile='gavinying-cli', mode='final')
        self.assertEqual('generated', result.status)
        for artifact in result.artifacts:
            path = base / artifact.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(artifact.as_text())
        self.directory = base / 'modpoll/gavinying-cli'
        self.launcher = self.directory / 'loop-read-final.py'

    def test_generated_launcher_intercepts_one_bounded_native_invocation(self):
        passed, details = _gavinying_bounded_delivery(self.directory)
        self.assertTrue(passed, details)
        self.assertEqual(1, details['intercepted_native_calls'])
        self.assertEqual(0, details['network_calls'])

    def test_removed_once_flag_fails_even_when_comment_contains_once(self):
        original = self.launcher.read_text()
        self.assertIn("argv = [executable, '--once',", original)
        self.launcher.write_text(original.replace("argv = [executable, '--once',", "argv = [executable,", 1))
        with (self.directory / 'commands.txt').open('a') as handle:
            handle.write('\n# --once is only a comment\n')
        self.assertFalse(_gavinying_bounded_delivery(self.directory)[0])

    def test_changed_config_stops_without_any_native_invocation(self):
        (self.directory / 'loop.csv').write_text('changed config')
        self.assertFalse(_gavinying_bounded_delivery(self.directory)[0])

    def test_bare_native_command_does_not_bypass_validated_launcher(self):
        (self.directory / 'commands.txt').write_text('modpoll --once --tcp 127.0.0.1\n')
        self.assertFalse(_gavinying_bounded_delivery(self.directory)[0])


if __name__ == '__main__':
    unittest.main()
