from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConfirmedDefaultsCliTests(unittest.TestCase):
    def test_documented_file_option_applies_one_scoped_fact_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "candidate.json"
            payload = {"records": [{"name": "Synthetic", "address": "7", "area": "holding-register",
                                    "datatype": "uint16", "address_convention": ""}]}
            source.write_text(json.dumps(payload))
            original = source.read_bytes()
            defaults = root / "confirmed-defaults.json"
            defaults.write_text(json.dumps({"address_convention": "protocol-offset"}))
            output = root / "normalized.json"
            subprocess.run([sys.executable, "-B", str(ROOT / "plugins/modbus-skills/skills/normalize-map/scripts/run.py"),
                            "--input", str(source), "--output", str(output), "--defaults", str(defaults)],
                           text=True, capture_output=True, check=True, timeout=10)
            result = json.loads(output.read_text())
            self.assertEqual(7, result["points"][0]["protocol_offset"])
            self.assertEqual(original, source.read_bytes())


if __name__ == "__main__": unittest.main()
