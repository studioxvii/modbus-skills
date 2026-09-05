"""The documented correction rerun must not consume a stale result."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT/'plugins/modbus-skills/skills/normalize-map/scripts/run.py'
POINT = {'logical_point_id': 'synthetic-pressure', 'address': '2',
         'address_convention': '', 'area': 'holding-register', 'unit_id': 1,
         'route_id': 'synthetic-route', 'datatype': 'uint16', 'word_count': 1,
         'access': 'read-only', 'scale': 1}


class NormalizeRefreshContractTests(unittest.TestCase):
    def exercise(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, defaults, output = [root/name for name in ('candidates.json', 'defaults.json', 'normalized.json')]
            source.write_text(json.dumps({'records': [POINT]}))
            defaults.write_text(json.dumps({'address_convention': 'protocol-offset'}))
            original = source.read_bytes()
            command = [sys.executable, '-B', str(WRAPPER), '--input', str(source), '--output', str(output)]
            first = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(0, first.returncode, first.stderr)
            previous = output.read_bytes()
            self.assertIsNone(json.loads(previous)['points'][0]['protocol_offset'])
            command += ['--defaults', str(defaults)]
            corrected = output
            if mode == 'overwrite':
                command += ['--overwrite']
            elif mode == 'fresh':
                corrected = root/'corrected.json'
                command[command.index('--output')+1] = str(corrected)
            second = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(original, source.read_bytes())
            if mode == 'unapproved':
                self.assertNotEqual(0, second.returncode)
                self.assertEqual(previous, output.read_bytes())
            else:
                self.assertEqual(0, second.returncode, second.stderr)
                point = json.loads(corrected.read_text())['points'][0]
                self.assertEqual(2, point['protocol_offset'])
                self.assertEqual('2', point['source_address']['raw'])
                if mode == 'fresh':
                    self.assertEqual(previous, output.read_bytes())

    def test_existing_output_is_preserved_without_explicit_overwrite(self):
        self.exercise('unapproved')

    def test_explicit_refresh_applies_default_and_preserves_candidates(self):
        self.exercise('overwrite')

    def test_new_path_preserves_the_previous_result(self):
        self.exercise('fresh')


if __name__ == '__main__':
    unittest.main()
