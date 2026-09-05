from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))
from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler import CompilerError, compile_user_map, inspect_compile_case
from test_compiler import request, selection_pause_request


class CompileCaseInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "case"
        compile_user_map(selection_pause_request(), self.root)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def test_valid_inspection_is_read_only_and_binds_current_case(self):
        before = self.snapshot()
        case = json.loads((self.root / "case.json").read_text())
        result = inspect_compile_case(self.root / "case.json")
        self.assertEqual("valid", result["status"])
        self.assertEqual(stable_input_hash(case), result["case_hash"])
        self.assertEqual(len(case["artifacts"]), result["verified_artifact_count"])
        self.assertEqual(case["active_packet"], result["active_packet"])
        self.assertEqual(before, self.snapshot())

    def test_offline_bundle_including_non_json_files_is_valid(self):
        root = Path(self.tmp.name) / "complete"
        compile_user_map(request(), root)
        self.assertEqual("offline-complete", inspect_compile_case(root)["state"])

    def test_tampered_state_is_rejected_without_mutation(self):
        path = self.root / "case.json"
        case = json.loads(path.read_text())
        case["state"] = "tampered"
        path.write_text(json.dumps(case))
        before = self.snapshot()
        with self.assertRaises(CompilerError):
            inspect_compile_case(self.root)
        self.assertEqual(before, self.snapshot())

    def test_changed_and_missing_indexed_artifact_are_rejected(self):
        case = json.loads((self.root / "case.json").read_text())
        path = self.root / case["artifacts"]["oem_map"]["path"]
        path.write_text('{}')
        with self.assertRaisesRegex(CompilerError, "hash is stale"):
            inspect_compile_case(self.root)
        path.unlink()
        with self.assertRaisesRegex(CompilerError, "missing or unsafe"):
            inspect_compile_case(self.root)

    def test_active_packet_mismatch_is_rejected(self):
        path = self.root / "case.json"
        case = json.loads(path.read_text())
        case["active_packet"]["decisions"][0]["prompt"] = "Changed question"
        path.write_text(json.dumps(case))
        with self.assertRaises(CompilerError):
            inspect_compile_case(self.root)

    def test_symlink_case_and_artifact_are_rejected(self):
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(CompilerError):
            inspect_compile_case(alias)
        case = json.loads((self.root / "case.json").read_text())
        path = self.root / case["artifacts"]["oem_map"]["path"]
        outside = Path(self.tmp.name) / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
        with self.assertRaises(CompilerError):
            inspect_compile_case(self.root)

    def test_cli_failure_is_structured_and_does_not_repair(self):
        path = self.root / "case.json"
        path.write_text('{broken')
        before = self.snapshot()
        command = [sys.executable, str(ROOT / "plugins/modbus-skills/skills/compile-user-map/scripts/inspect_case.py"), str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        receipt = json.loads(result.stderr)
        self.assertEqual("case-integrity-invalid", receipt["code"])
        self.assertEqual("error", receipt["status"])
        self.assertEqual(before, self.snapshot())

    def test_malformed_case_contract_is_a_validation_error(self):
        path = self.root / "case.json"
        case = json.loads(path.read_text())
        del case["input_hashes"]
        path.write_text(json.dumps(case))
        with self.assertRaises(CompilerError):
            inspect_compile_case(self.root)

    def test_case_symlink_within_same_directory_is_rejected(self):
        path = self.root / "case.json"
        copy = self.root / "case-copy.json"
        path.rename(copy)
        path.symlink_to(copy)
        with self.assertRaises(CompilerError):
            inspect_compile_case(self.root)


if __name__ == "__main__":
    unittest.main()
