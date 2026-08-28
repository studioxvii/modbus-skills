from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "compile-user-map"
RUNNER = ROOT / "plugins" / "modbus-skills" / "skills" / "compile-user-map" / "scripts" / "run.py"


class ExampleCompileTests(unittest.TestCase):
    def _compile(self, request: Path, output: Path) -> dict:
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--request", str(request), "--output", str(output)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads((output / "compile-result.json").read_text(encoding="utf-8"))

    def test_completable_example_matches_committed_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="modbus-example-") as tmp:
            receipt = self._compile(EXAMPLE / "request.json", Path(tmp) / "case")
            self.assertEqual("offline-complete", receipt["status"])
            output = Path(tmp) / "case" / "output"
            for name in ("user-map.md", "user-map.csv", "user-map.json"):
                with self.subTest(name=name):
                    self.assertEqual(
                        (EXAMPLE / "output" / name).read_text(encoding="utf-8"),
                        (output / name).read_text(encoding="utf-8"),
                    )

    def test_unresolved_byte_order_does_not_invent_a_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="modbus-example-unresolved-") as tmp:
            receipt = self._compile(EXAMPLE / "request-unresolved.json", Path(tmp) / "case")
            self.assertEqual("partial", receipt["status"])
            self.assertEqual("provide-corrected-source", receipt["next_action"]["kind"])
            output = Path(tmp) / "case" / "output" / "user-map.json"
            self.assertTrue(output.is_file())
            user_map = json.loads(output.read_text(encoding="utf-8"))
            by_id = {point["oem_point_id"]: point for point in user_map["points"]}
            self.assertEqual({"tank_level", "flow_rate", "energy_total"}, set(by_id))
            for point_id in ("flow_rate", "energy_total"):
                self.assertIsNone(by_id[point_id]["byte_order"])
                self.assertFalse(by_id[point_id]["byte_order_confirmed"])


if __name__ == "__main__":
    unittest.main()
