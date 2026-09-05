from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_direct_skill_acceptance import (  # noqa: E402
    CATEGORIES, SKILLS, assess_case, predeclared_cases, run_matrix, simple_pdf,
)


class DirectSkillAcceptanceHarnessTests(unittest.TestCase):
    def test_exact80_predeclared_cells_keep_instruction_only_skill_unexecuted(self):
        cases = predeclared_cases()
        self.assertEqual(80, len(cases))
        self.assertEqual({(skill, category) for skill in SKILLS for category in CATEGORIES},
                         {(case["skill"], case["category"]) for case in cases})
        self.assertEqual(4, sum(case["model_needed"] for case in cases))
        for case in cases:
            self.assertTrue(case["expected"])
            self.assertTrue(case["required_checks"])
            self.assertEqual(case["skill"] == "modbus-help", case["entrypoint"] is None)

    def test_notation_conversion_may_apply_but_cannot_erase_a_source_hold(self):
        case = next(item for item in predeclared_cases() if item["case_id"] == "remap-addresses--unsafe")
        receipt = {"returncode": 0, "inputs_unchanged": True, "plugin_unchanged": True,
                   "audit": {"denied": []}, "stderr": ""}
        result = {"status": "ready", "points": [{"function_code": 6, "access": "write-only", "protocol_offset": 10,
                                                    "normalization_status": "pending"}]}
        checks = assess_case(case, receipt, {"result.json": result}, ROOT)
        failures = {item["name"] for item in checks if not item["passed"]}
        self.assertEqual({"explicit existing safety hold survives conversion"}, failures)
        result["holds"] = [{"code": "point.write-only-not-readable", "blocking": True}]
        self.assertTrue(all(check["passed"] for check in assess_case(case, receipt, {"result.json": result}, ROOT)))

    def test_explicit_row_rejection_counts_as_rejection_not_successful_acceptance(self):
        case = next(item for item in predeclared_cases() if item["case_id"] == "analyze-capture--negative")
        receipt = {"returncode": 0, "inputs_unchanged": True, "plugin_unchanged": True,
                   "audit": {"denied": []}, "stderr": ""}
        result = {"rejected_samples": [{"code": "TIMESTAMP_INVALID"}], "points": {}}
        self.assertTrue(all(check["passed"] for check in assess_case(case, receipt, {"result.json": result}, ROOT)))
        result["rejected_samples"] = []
        self.assertFalse(all(check["passed"] for check in assess_case(case, receipt, {"result.json": result}, ROOT)))

    def test_guard_denial_is_never_a_successful_product_refusal(self):
        case = next(item for item in predeclared_cases() if item["case_id"] == "parse-map--unsafe")
        receipt = {"returncode": 2, "inputs_unchanged": True, "plugin_unchanged": True,
                   "audit": {"denied": [{"event": "socket.connect"}]}, "stderr": "denied"}
        checks = assess_case(case, receipt, {}, ROOT)
        self.assertFalse(all(item["passed"] for item in checks))

    def test_byte_order_oracle_uses_declared_decoded_field_and_shared_sample(self):
        case = next(item for item in predeclared_cases() if item["case_id"] == "check-byte-order--positive")
        receipt = {"returncode": 0, "inputs_unchanged": True, "plugin_unchanged": True,
                   "audit": {"denied": []}, "stderr": ""}
        candidates = [{"layout": layout, "decoded_value": 123 if layout == "ABCD" else 0,
                       "sample_id": "synthetic-sample"} for layout in ("ABCD", "BADC", "CDAB", "DCBA")]
        self.assertTrue(all(check["passed"] for check in assess_case(case, receipt, {"result.json": {"candidates": candidates}}, ROOT)))
        candidates[1]["sample_id"] = "different-sample"
        self.assertFalse(all(check["passed"] for check in assess_case(case, receipt, {"result.json": {"candidates": candidates}}, ROOT)))

    def test_synthetic_pdf_has_complete_xref_and_trailer(self):
        payload = simple_pdf(["Address   Name   Data Type", "40011   Synthetic   uint16"])
        self.assertTrue(payload.startswith(b"%PDF-1.4"))
        offset = int(payload.split(b"startxref\n")[1].splitlines()[0])
        self.assertTrue(payload[offset:].startswith(b"xref\n"))
        self.assertTrue(payload.endswith(b"%%EOF\n"))

    def test_nonempty_outputs_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            sentinel = output / "prior-evidence.json"
            sentinel.write_text("preserve")
            with self.assertRaisesRegex(ValueError, "historical receipts"):
                run_matrix(output)
            self.assertEqual("preserve", sentinel.read_text())

    def test_python_guard_blocks_network_and_out_of_scope_writes(self):
        for body, expected in (("import socket\nsocket.socket()", "network-or-unapproved-execution"),
                               ("from pathlib import Path\nPath(SENTINEL).write_text('bad')", "write-outside-output-or-private-temporary-root")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                wrapper = root / "plugin/skills/synthetic/scripts/run.py"
                wrapper.parent.mkdir(parents=True)
                sentinel = root / "outside.txt"
                wrapper.write_text("SENTINEL = " + repr(str(sentinel)) + "\n" + body)
                output = root / "output"
                private = root / "private"
                output.mkdir()
                private.mkdir()
                audit = output / "audit.json"
                completed = subprocess.run([sys.executable, str(ROOT / "scripts/direct_skill_guard.py"),
                    "--wrapper", str(wrapper), "--output-root", str(output), "--temporary-root", str(private),
                    "--audit", str(audit)], text=True, capture_output=True, timeout=10)
                self.assertNotEqual(0, completed.returncode)
                self.assertFalse(sentinel.exists())
                self.assertIn(expected, [item["reason"] for item in json.loads(audit.read_text())["denied"]])


if __name__ == "__main__":
    unittest.main()
