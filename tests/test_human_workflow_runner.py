from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "human-workflow"
RUNNER = ROOT / "scripts" / "run_human_workflow_tests.py"
sys.path.insert(0, str(ROOT / "scripts"))

from run_human_workflow_tests import (  # noqa: E402
    WorkflowFailure,
    _byte_order_profile,
    _capture_for_point,
    _planned_point_ids,
    _sample_identity,
    _validate_output_path,
)


class HumanWorkflowRunnerTests(unittest.TestCase):
    def _run(self, corpus: Path, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--corpus-dir",
                str(corpus),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_manifest(self, corpus: Path, *, identifier: str, filename: str) -> None:
        corpus.mkdir(parents=True, exist_ok=True)
        (corpus / "corpus.json").write_text(
            json.dumps(
                {
                    "schema_version": "modbus-human-workflow-corpus/v1",
                    "maps": [{"id": identifier, "file": filename}],
                }
            ),
            encoding="utf-8",
        )

    def test_public_synthetic_corpus_exercises_the_human_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "workflow-output"
            result = self._run(FIXTURE, output)
            self.assertEqual(0, result.returncode, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual("passed", receipt["status"])
            report = json.loads((output / "human-workflow-report.json").read_text(encoding="utf-8"))
            self.assertEqual("passed", report["status"])
            self.assertEqual(12, len(report["cases"]))
            self.assertEqual(
                41,
                sum(len(case["checks"]) for case in report["cases"]),
            )
            self.assertTrue(
                all(
                    check["passed"]
                    for case in report["cases"]
                    for check in case["checks"]
                )
            )
            self.assertNotIn(str(FIXTURE), (output / "human-workflow-report.md").read_text(encoding="utf-8"))
            self.assertNotIn(str(FIXTURE), json.dumps(report))

    def test_report_uses_generic_ids_for_private_corpus_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            shutil.copytree(FIXTURE, corpus)
            manifest_path = corpus / "corpus.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            replacements = {
                entry["id"]: f"customer-private-map-{index:03d}"
                for index, entry in enumerate(manifest["maps"], start=1)
            }
            for entry in manifest["maps"]:
                entry["id"] = replacements[entry["id"]]
            manifest["workflow_cases"] = {
                role: replacements[identifier]
                for role, identifier in manifest["workflow_cases"].items()
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "output"

            result = self._run(corpus, output)

            self.assertEqual(0, result.returncode, result.stderr)
            report_json = (output / "human-workflow-report.json").read_text(
                encoding="utf-8"
            )
            report_markdown = (output / "human-workflow-report.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("customer-private-map", report_json)
            self.assertNotIn("customer-private-map", report_markdown)
            self.assertIn("map-001", report_json)

    def test_corpus_rejects_absolute_traversal_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.csv"
            outside.write_text("address,name\n1,Private\n", encoding="utf-8")
            cases: list[tuple[str, str, bool]] = [
                ("absolute", str(outside), False),
                ("traversal", "../outside.csv", False),
                ("symlink", "outside-link.csv", True),
            ]
            for case_name, filename, make_symlink in cases:
                with self.subTest(case=case_name):
                    corpus = root / case_name
                    self._write_manifest(
                        corpus, identifier="clean", filename=filename
                    )
                    if make_symlink:
                        (corpus / filename).symlink_to(outside)
                    result = self._run(corpus, root / f"{case_name}-output")
                    self.assertNotEqual(0, result.returncode)
                    self.assertNotIn(str(outside), result.stderr)

    def test_corpus_rejects_a_manifest_symlink_that_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.json"
            outside.write_text(json.dumps({"maps": []}), encoding="utf-8")
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "corpus.json").symlink_to(outside)

            result = self._run(corpus, root / "output")

            self.assertNotEqual(0, result.returncode)
            self.assertNotIn(str(outside), result.stderr)

    def test_report_drops_unknown_workflow_role_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            corpus = root / "corpus"
            shutil.copytree(FIXTURE, corpus)
            manifest_path = corpus / "corpus.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["workflow_cases"]["customer-private-role"] = "clean"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            output = root / "output"
            result = self._run(corpus, output)

            self.assertEqual(0, result.returncode, result.stderr)
            report_text = (output / "human-workflow-report.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("customer-private-role", report_text)

    def test_corpus_rejects_unsafe_and_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case_name, identifiers in (
                ("unsafe", ["../private-map"]),
                ("duplicate", ["same-id", "same-id"]),
            ):
                with self.subTest(case=case_name):
                    corpus = root / case_name
                    corpus.mkdir()
                    maps = []
                    for index, identifier in enumerate(identifiers):
                        filename = f"map-{index}.csv"
                        (corpus / filename).write_text(
                            "address,name\n1,Point\n", encoding="utf-8"
                        )
                        maps.append({"id": identifier, "file": filename})
                    (corpus / "corpus.json").write_text(
                        json.dumps({"maps": maps}), encoding="utf-8"
                    )
                    result = self._run(corpus, root / f"{case_name}-output")
                    self.assertNotEqual(0, result.returncode)

    def test_write_only_trace_is_detected_in_compiled_request_shape(self) -> None:
        unsafe_plan = {
            "requests": [
                {
                    "request_id": "unsafe-read",
                    "points": [{"logical_point_id": "reset-command"}],
                }
            ]
        }
        write_only = {"reset-command"}

        planned_ids = _planned_point_ids(unsafe_plan)

        self.assertEqual({"reset-command"}, planned_ids)
        self.assertFalse(write_only.isdisjoint(planned_ids))

    def test_nonempty_output_is_rejected_without_deleting_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            result = self._run(FIXTURE, output)
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(sentinel.is_file())

    def test_unignored_repository_output_is_rejected(self) -> None:
        output = ROOT / "site" / "human-workflow-test-output"

        with self.assertRaises(WorkflowFailure):
            _validate_output_path(output)

        self.assertFalse(output.exists())

    def test_ignored_local_repository_output_is_allowed(self) -> None:
        output = ROOT / "artifacts" / "human-workflow-test-output"

        self.assertEqual(output.resolve(), _validate_output_path(output))

    def test_four_word_point_drives_sample_types_candidates_and_layout(self) -> None:
        point = {
            "logical_point_id": "energy-total",
            "route_id": "test-route",
            "unit_id": 7,
            "area": "holding-register",
            "protocol_offset": 24,
            "datatype": "float64",
            "word_span": 4,
            "access": "read-only",
        }
        profile = _byte_order_profile(point)
        capture = _capture_for_point(point, profile)

        self.assertEqual(4, profile.word_count)
        self.assertEqual(("uint64", "int64", "float64"), profile.datatypes)
        self.assertEqual(144, profile.expected_candidate_count)
        self.assertEqual("ABCDEFGH", profile.identity_layout)
        self.assertEqual(4, len(capture["samples"][0]["raw_words"]))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_path = root / "capture.json"
            evidence_path = root / "evidence.json"
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            wrapper = (
                ROOT
                / "plugins"
                / "modbus-skills"
                / "skills"
                / "evaluate-modbus-byte-order"
                / "scripts"
                / "run.py"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(wrapper),
                    "--input",
                    str(capture_path),
                    "--types",
                    ",".join(profile.datatypes),
                    "--output",
                    str(evidence_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(64, evidence["sample"]["bit_width"])
            self.assertEqual(144, len(evidence["candidates"]))
            self.assertEqual(_sample_identity(point), evidence["sample_identity"])
            self.assertTrue(
                any(
                    candidate["layout"] == profile.identity_layout
                    and candidate["datatype"] == profile.point_datatype
                    for candidate in evidence["candidates"]
                )
            )


if __name__ == "__main__":
    unittest.main()
