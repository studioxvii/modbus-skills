from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "compiler-workflow"
RUNNER = ROOT / "scripts" / "run_compile_workflow_tests.py"
sys.path.insert(0, str(ROOT / "scripts"))

from run_compile_workflow_tests import (  # noqa: E402
    WorkflowFailure,
    fixture_sha256,
    load_benchmark_rows,
    validate_transcript,
)


class CompileWorkflowRunnerTests(unittest.TestCase):
    def _run(self, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--fixtures",
                str(FIXTURE),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_outcome_transcripts_enforce_human_attention_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            result = self._run(output)

            self.assertEqual(0, result.returncode, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["status"], "passed")
            report = json.loads(
                (output / "compile-workflow-report.json").read_text(encoding="utf-8")
            )
            cases = {case["case_id"]: case for case in report["cases"]}
            clean = cases["clean-offline"]
            self.assertEqual(clean["invocation_count"], 1)
            self.assertEqual(clean["question_count"], 0)
            self.assertEqual(clean["stage_handoffs"], 0)
            self.assertEqual(clean["selected_point_counts"], {"csv": 2, "human": 2, "json": 2})

            fallback = cases["fallback-extraction"]
            self.assertEqual(fallback["invocation_count"], 1)
            self.assertEqual(fallback["question_count"], 0)
            self.assertEqual(fallback["strategy"], "coordinate-fallback")
            self.assertEqual(fallback["source_point_count"], 1)

            selection = cases["selection-exception"]
            self.assertEqual(selection["decision_packet_count"], 1)
            self.assertEqual(selection["resume_exchange_count"], 1)
            self.assertEqual(
                selection["state_transitions"],
                ["awaiting-selection-decision", "offline-complete"],
            )

            binding = cases["binding-readable-island"]
            self.assertEqual(
                binding["state_transitions"], ["awaiting-binding", "complete"]
            )
            self.assertTrue(binding["offline_artifacts_preserved"])
            self.assertEqual(binding["request_count"], 1)
            self.assertEqual(binding["physical_gate_count"], 0)

            self.assertEqual(report["dependencies"], [])

    def test_normal_report_is_deterministic_across_output_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._run(root / "one")
            second = self._run(root / "two")
            self.assertEqual(0, first.returncode, first.stderr)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(
                json.loads((root / "one" / "compile-workflow-report.json").read_text()),
                json.loads((root / "two" / "compile-workflow-report.json").read_text()),
            )

    def test_failure_signatures_reject_slow_human_choreography(self) -> None:
        valid = {
            "case_id": "case",
            "invocation_count": 1,
            "question_count": 0,
            "decision_packet_count": 0,
            "resume_exchange_count": 0,
            "stage_handoffs": 0,
            "repeated_hold_signatures": [],
            "events": [{"kind": "compiler-invocation"}],
        }
        validate_transcript(valid)
        variants = []
        for event in (
            "page-approval-question",
            "row-review-question",
            "dependency-install",
            "stage-skill-handoff",
        ):
            value = copy.deepcopy(valid)
            value["events"].append({"kind": event})
            variants.append(value)
        repeated = copy.deepcopy(valid)
        repeated["repeated_hold_signatures"] = ["same-hold-twice"]
        variants.append(repeated)
        excess = copy.deepcopy(valid)
        excess["question_count"] = 2
        excess["decision_packet_count"] = 2
        variants.append(excess)

        for transcript in variants:
            with self.subTest(transcript=transcript), self.assertRaises(WorkflowFailure):
                validate_transcript(transcript)

    def test_benchmark_fixture_is_rights_safe_and_approximately_150_rows(self) -> None:
        path = FIXTURE / "benchmark-registers.csv"
        rows = load_benchmark_rows(path)

        self.assertEqual(len(rows), 150)
        self.assertRegex(fixture_sha256(path), r"^[0-9a-f]{64}$")
        self.assertTrue(all(row["oem_point_id"].startswith("synthetic-") for row in rows))
        self.assertNotIn("vendor", path.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
