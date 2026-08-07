from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
FIXTURES = ROOT / "tests" / "fixtures" / "workflows"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.artifacts import assert_artifact_envelope  # noqa: E402
from modbus_skills.artifacts import stable_input_hash  # noqa: E402
from modbus_skills.cli import COMMANDS, run_cli  # noqa: E402


class CliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, command: str, *arguments: object) -> dict[str, object]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_cli(command, [str(value) for value in arguments])
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual("", stderr.getvalue())
        return json.loads(stdout.getvalue())

    def prepare_map_and_plan(self) -> tuple[Path, Path, Path]:
        parsed = self.root / "candidate.json"
        canonical = self.root / "canonical.json"
        lint = self.root / "lint.json"
        plan = self.root / "read-plan.json"
        self.run_command("parse-map", "--input", FIXTURES / "source_map.json", "--output", parsed)
        self.run_command("normalize-map", "--input", parsed, "--output", canonical)
        lint_receipt = self.run_command("lint-map", "--input", canonical, "--output", lint)
        self.assertEqual(0, lint_receipt["blocking"])
        plan_receipt = self.run_command("compile-read-plan", "--input", canonical, "--output", plan)
        self.assertEqual("planned", plan_receipt["status"])
        self.assertEqual(
            "planned", json.loads(plan.read_text(encoding="utf-8"))["status"]
        )
        return canonical, lint, plan

    def test_every_skill_wrapper_command_is_registered(self) -> None:
        discovered = set()
        for wrapper in (ROOT / "plugins" / "modbus-skills" / "skills").glob("*/scripts/run.py"):
            text = wrapper.read_text(encoding="utf-8")
            marker = 'run_cli("'
            start = text.index(marker) + len(marker)
            discovered.add(text[start : text.index('"', start)])
        self.assertEqual(set(COMMANDS), discovered)

    def test_map_review_plan_and_target_generation_workflow(self) -> None:
        canonical, lint, plan = self.prepare_map_and_plan()

        review = self.root / "review.json"
        review_receipt = self.run_command(
            "review-evidence", "--input", canonical, "--lint", lint, "--output", review
        )
        self.assertEqual("ready-for-human-review", review_receipt["status"])

        diagnosis = self.root / "diagnosis"
        diagnose_receipt = self.run_command(
            "diagnose-map", "--input", FIXTURES / "source_map.json", "--output", diagnosis
        )
        self.assertEqual("ready-for-human-review", diagnose_receipt["status"])
        self.assertTrue((diagnosis / "parsed.json").is_file())
        self.assertTrue((diagnosis / "map-draft.json").is_file())

        for command, folder, extra in (
            ("generate-node-red", "node", ()),
            ("generate-modpoll", "modpoll", ("--profile", "gavinying-cli")),
            ("generate-modscan", "modscan", ()),
        ):
            receipt = self.run_command(
                command,
                "--map",
                canonical,
                "--plan",
                plan,
                *extra,
                "--output",
                self.root / folder,
            )
            self.assertEqual("generated", receipt["status"])

        self.assertTrue((self.root / "node" / "node-red" / "flow.json").is_file())
        self.assertTrue((self.root / "modpoll" / "modpoll" / "gavinying-cli" / "synthetic-loop.csv").is_file())
        self.assertTrue((self.root / "modscan" / "modscan" / "read-plan.csv").is_file())

        witte = self.run_command(
            "generate-modpoll",
            "--map",
            canonical,
            "--plan",
            plan,
            "--profile",
            "witte-v12-xml",
            "--output",
            self.root / "witte-v12",
        )
        self.assertEqual("generated", witte["status"])
        self.assertTrue(any((self.root / "witte-v12" / "modpoll" / "witte-v12-xml").glob("*.xml")))

    def test_read_plan_preserves_map_holds_and_excludes_access_held_points(self) -> None:
        source = self.root / "access-held-map.json"
        source.write_text(
            json.dumps(
                {
                    "points": [
                        {
                            "logical_point_id": "unknown-access",
                            "route_id": "lab",
                            "unit_id": 1,
                            "area": "holding-register",
                            "protocol_offset": 5,
                            "source_address": {
                                "raw": 5,
                                "convention": "protocol-offset",
                            },
                            "datatype": "uint16",
                            "word_span": 1,
                        }
                    ],
                    "holds": [
                        {
                            "code": "point.access-unresolved",
                            "severity": "hold",
                            "blocking": True,
                            "field": "access",
                            "point_ids": ["unknown-access"],
                            "message": "Review access.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "access-held-plan.json"

        receipt = self.run_command(
            "compile-read-plan", "--input", source, "--output", output
        )
        result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("held", receipt["status"])
        self.assertEqual([], result["requests"])
        self.assertIn(
            "point.access-unresolved",
            {finding["code"] for finding in result["findings"]},
        )

    def test_probe_and_final_combination_packs_are_deterministic(self) -> None:
        canonical, _, plan = self.prepare_map_and_plan()
        probe_output = self.root / "probe"
        probe = self.run_command(
            "capture-sample",
            "--request",
            FIXTURES / "probe_request.json",
            "--output",
            probe_output,
        )
        self.assertEqual("generated", probe["status"])
        probe_manifest = json.loads((probe_output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("probe", probe_manifest["mode"])
        self.assertEqual(["node-red", "modpoll", "modscan"], [item["target"] for item in probe_manifest["targets"]])

        request = {
            "canonical_map": json.loads(canonical.read_text(encoding="utf-8")),
            "read_plan": json.loads(plan.read_text(encoding="utf-8")),
            "targets": [
                {"id": "node-red"},
                {"id": "modpoll", "profile": "gavinying-cli"},
                {"id": "modscan"},
            ],
            "mode": "final",
        }
        request_path = self.root / "pack-request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        first = self.root / "pack-one"
        second = self.root / "pack-two"
        for output in (first, second):
            receipt = self.run_command("build-tool-pack", "--request", request_path, "--output", output)
            self.assertEqual("generated", receipt["status"])

        first_files = {path.relative_to(first): hashlib.sha256(path.read_bytes()).hexdigest() for path in first.rglob("*") if path.is_file()}
        second_files = {path.relative_to(second): hashlib.sha256(path.read_bytes()).hexdigest() for path in second.rglob("*") if path.is_file()}
        self.assertEqual(first_files, second_files)
        self.assertIn(Path("tool-pack.zip"), first_files)
        manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
        modpoll = next(item for item in manifest["targets"] if item["target"] == "modpoll")
        self.assertEqual("gavinying-cli", modpoll["profile"])
        result_envelope = json.loads(
            (first / "tool-pack-result.json").read_text(encoding="utf-8")
        )
        self.assertFalse(result_envelope["container"]["hash_claimed"])
        self.assertNotIn(
            "tool-pack.zip",
            {artifact["path"] for artifact in result_envelope["artifacts"]},
        )
        with ZipFile(first / "tool-pack.zip") as archive:
            self.assertIn("tool-pack-result.json", archive.namelist())
            zipped_result = json.loads(
                archive.read("tool-pack-result.json").decode("utf-8")
            )
        self.assertEqual(result_envelope, zipped_result)

    def test_capture_request_accepts_relative_map_and_plan_paths(self) -> None:
        canonical, _, plan = self.prepare_map_and_plan()
        request = {
            "canonical_map": canonical.name,
            "read_plan": plan.name,
            "targets": [{"id": "node-red"}],
        }
        request_path = self.root / "relative-probe.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        receipt = self.run_command(
            "capture-sample", "--request", request_path, "--output", self.root / "relative-probe"
        )
        self.assertEqual("generated", receipt["status"])

    def test_capture_does_not_plan_excluded_unresolved_or_write_only_points(self) -> None:
        def probe_point(identifier: str, offset: int, **updates: object) -> dict[str, object]:
            value: dict[str, object] = {
                "logical_point_id": identifier,
                "route_id": "lab",
                "unit_id": 1,
                "area": "holding-register",
                "protocol_offset": offset,
                "datatype": "uint16",
                "word_span": 1,
                "access": "read-only",
            }
            value.update(updates)
            return value

        request = {
            "canonical_map": {
                "points": [
                    probe_point("safe", 0),
                    probe_point("source-excluded", 1, source_include=False),
                    probe_point("unresolved-access", 2, access=None),
                    probe_point("write-only", 3, access="write-only"),
                ],
                "holds": [
                    {
                        "code": "point.access-unresolved",
                        "severity": "hold",
                        "blocking": True,
                        "field": "access",
                        "point_ids": ["unresolved-access"],
                        "message": "Review access.",
                    }
                ],
            },
            "targets": ["node-red"],
        }
        request_path = self.root / "unsafe-capture.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        output = self.root / "unsafe-capture"

        receipt = self.run_command(
            "capture-sample",
            "--request",
            request_path,
            "--output",
            output,
        )

        self.assertEqual("held", receipt["status"])
        plan = json.loads((output / "read-plan.json").read_text(encoding="utf-8"))
        planned_ids = {
            trace["logical_point_id"]
            for block in plan["requests"]
            for trace in block["points"]
        }
        self.assertEqual({"safe"}, planned_ids)
        self.assertFalse((output / "node-red" / "flow.json").exists())

    def test_byte_order_analysis_comparison_remap_and_custom_format(self) -> None:
        canonical, _, _ = self.prepare_map_and_plan()

        byte_evidence = self.root / "byte-order.json"
        byte_receipt = self.run_command(
            "evaluate-byte-order",
            "--input",
            FIXTURES / "raw_sample.json",
            "--types",
            "uint32,int32,float32",
            "--output",
            byte_evidence,
        )
        self.assertEqual(12, byte_receipt["candidates"])
        incomplete_evidence = json.loads(byte_evidence.read_text(encoding="utf-8"))
        self.assertIn(
            "byte-order-sample-identity-incomplete",
            {hold["code"] for hold in incomplete_evidence["holds"]},
        )
        self.assertNotIn("winner", incomplete_evidence)

        analysis = self.root / "analysis.json"
        analysis_receipt = self.run_command(
            "analyze-capture", "--input", FIXTURES / "capture.json", "--output", analysis
        )
        self.assertEqual("analyzed", analysis_receipt["status"])
        self.assertTrue(json.loads(analysis.read_text(encoding="utf-8"))["read_only"])

        comparison = self.root / "comparison.json"
        compare_receipt = self.run_command(
            "compare-maps", "--before", canonical, "--after", canonical, "--output", comparison
        )
        self.assertEqual(2, compare_receipt["unchanged"])

        remap = self.root / "remap.json"
        remap_receipt = self.run_command(
            "remap-addresses",
            "--input",
            canonical,
            "--from",
            "protocol-offset",
            "--to",
            "modicon-reference",
            "--output",
            remap,
        )
        self.assertEqual("ready-for-review", remap_receipt["status"])

        custom = self.root / "custom"
        custom_receipt = self.run_command(
            "infer-custom-format",
            "--example",
            FIXTURES / "custom_format_example.csv",
            "--map",
            canonical,
            "--output",
            custom,
        )
        self.assertEqual("ready-for-human-review", custom_receipt["status"])
        self.assertIn("tank_level", (custom / "rendered-output.txt").read_text(encoding="utf-8"))

    def test_byte_order_evidence_keeps_complete_sample_identity(self) -> None:
        capture = self.root / "identified-capture.json"
        capture.write_text(
            json.dumps(
                {
                    "schema_version": "capture/v1",
                    "points": [
                        {
                            "logical_point_id": "run-time",
                            "route_id": "serial-a",
                            "unit_id": 7,
                            "area": "holding-register",
                            "address": {"protocol_offset": 25},
                        }
                    ],
                    "samples": [
                        {
                            "sample_id": "sample-identified-001",
                            "point_id": "run-time",
                            "timestamp": "2026-08-07T15:30:00-04:00",
                            "raw_words": [0, 3600],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "identified-byte-order.json"

        receipt = self.run_command(
            "evaluate-byte-order",
            "--input",
            capture,
            "--types",
            "uint32,int32,float32",
            "--output",
            output,
        )

        self.assertEqual(12, receipt["candidates"])
        evidence = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "sample_id": "sample-identified-001",
                "point_id": "run-time",
                "route_id": "serial-a",
                "unit_id": 7,
                "area": "holding-register",
                "protocol_offset": 25,
                "timestamp": "2026-08-07T15:30:00-04:00",
            },
            evidence["sample_identity"],
        )
        self.assertEqual(
            {"byte-order-human-confirmation-required"},
            {hold["code"] for hold in evidence["holds"]},
        )
        self.assertEqual(
            {"sample-identified-001"},
            {candidate["sample_id"] for candidate in evidence["candidates"]},
        )
        self.assertNotIn("winner", evidence)

    def test_byte_order_rejects_sample_id_relabeling(self) -> None:
        capture = self.root / "sample-id-capture.json"
        capture.write_text(
            json.dumps(
                {
                    "sample_id": "actual-sample",
                    "raw_words": [0, 3600],
                    "point_id": "runtime",
                    "route_id": "lab",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 10,
                    "timestamp": "2026-08-07T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            stderr
        ):
            code = run_cli(
                "evaluate-byte-order",
                [
                    "--input",
                    str(capture),
                    "--sample-id",
                    "relabelled-sample",
                    "--output",
                    str(self.root / "relabelled.json"),
                ],
            )
        self.assertNotEqual(0, code)
        self.assertIn("does not match", stderr.getvalue())

    def test_capture_analysis_accepts_flat_csv_samples(self) -> None:
        capture = self.root / "capture.csv"
        capture.write_text(
            "sample_id,point_id,timestamp,value,response_ms,raw_words\n"
            "sample-1,runtime,2026-08-07T12:00:00Z,3600,8,0x0000 0x0E10\n"
            "sample-2,runtime,2026-08-07T12:00:10Z,3590,9,0x0000 0x0E06\n",
            encoding="utf-8",
        )
        output = self.root / "csv-analysis.json"

        receipt = self.run_command(
            "analyze-capture", "--input", capture, "--output", output
        )
        result = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("analyzed", receipt["status"])
        self.assertEqual(2, result["points"]["runtime"]["sample_count"])
        self.assertEqual(
            12,
            result["points"]["runtime"]["byte_order_evidence"]["candidate_count"],
        )

    def test_capture_analysis_ignores_blank_csv_raw_word_cells(self) -> None:
        capture = self.root / "capture-with-blank-raw-words.csv"
        capture.write_text(
            "sample_id,point_id,timestamp,value,raw_words\n"
            "sample-1,voltage,2026-08-07T12:00:00Z,230,17254;0\n"
            "sample-2,current,2026-08-07T12:00:00Z,5,\n",
            encoding="utf-8",
        )
        output = self.root / "blank-raw-words-analysis.json"

        self.run_command("analyze-capture", "--input", capture, "--output", output)
        result = json.loads(output.read_text(encoding="utf-8"))

        self.assertNotIn(
            "BYTE_ORDER_EVIDENCE_INVALID",
            {finding["code"] for finding in result["findings"]},
        )
        self.assertNotIn("byte_order_evidence", result["points"]["current"])

    def test_pdf_extraction_returns_a_clear_hold_without_extractor(self) -> None:
        source = self.root / "synthetic.pdf"
        source.write_bytes(b"%PDF-1.4\n% rights-safe synthetic test input\n")
        output = self.root / "pdf"
        with mock.patch("modbus_skills.cli.shutil.which", return_value=None):
            receipt = self.run_command("extract-pdf", "--input", source, "--output", output)
        self.assertEqual("held", receipt["status"])
        result = json.loads((output / "pdf-extraction.json").read_text(encoding="utf-8"))
        self.assertEqual("pdf-text-extractor-unavailable", result["holds"][0]["code"])
        self.assertEqual([], result["records"])

    def test_invalid_json_is_concise_and_nonzero(self) -> None:
        source = self.root / "broken.json"
        source.write_text("{", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_cli("normalize-map", ["--input", str(source), "--output", str(self.root / "out.json")])
        self.assertNotEqual(0, code)
        self.assertEqual("", stdout.getvalue())
        self.assertRegex(stderr.getvalue(), r"^error: JSON input is invalid at line 1, column 2\n$")

    def test_target_object_rejects_conflicting_profile_sources(self) -> None:
        canonical, _, plan = self.prepare_map_and_plan()
        request = {
            "canonical_map": json.loads(canonical.read_text(encoding="utf-8")),
            "read_plan": json.loads(plan.read_text(encoding="utf-8")),
            "targets": [{"id": "modpoll", "profile": "gavinying-cli"}],
            "target_options": {"modpoll": {"profile": "witte-desktop"}},
        }
        request_path = self.root / "conflict.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = run_cli(
                "build-tool-pack",
                ["--request", str(request_path), "--output", str(self.root / "conflict")],
            )
        self.assertNotEqual(0, code)
        self.assertIn("conflicting 'modpoll' option 'profile'", stderr.getvalue())

    def test_all_public_commands_emit_conforming_workflow_artifacts(self) -> None:
        exercised: set[str] = set()
        artifacts: list[tuple[Path, str]] = []

        def run(
            command: str,
            *arguments: object,
            artifact: Path | None = None,
            schema_version: str | None = None,
        ) -> dict[str, object]:
            exercised.add(command)
            receipt = self.run_command(command, *arguments)
            if artifact is not None and schema_version is not None:
                artifacts.append((artifact, schema_version))
            return receipt

        candidate = self.root / "contract-candidate.json"
        run(
            "parse-map",
            "--input",
            FIXTURES / "source_map.json",
            "--output",
            candidate,
            artifact=candidate,
            schema_version="candidate-map/v1",
        )
        canonical = self.root / "contract-canonical.json"
        run(
            "normalize-map",
            "--input",
            candidate,
            "--output",
            canonical,
            artifact=canonical,
            schema_version="modbus-map/v1",
        )
        lint = self.root / "contract-lint.json"
        run(
            "lint-map",
            "--input",
            canonical,
            "--output",
            lint,
            artifact=lint,
            schema_version="modbus-map-lint/v1",
        )
        review = self.root / "contract-review.json"
        run(
            "review-evidence",
            "--input",
            canonical,
            "--lint",
            lint,
            "--output",
            review,
            artifact=review,
            schema_version="modbus-map-evidence-review/v1",
        )
        decisions = self.root / "contract-decisions.json"
        decisions.write_text(
            json.dumps(
                {
                    "schema_version": "modbus-review-decisions/v1",
                    "canonical_map_hash": stable_input_hash(
                        json.loads(canonical.read_text(encoding="utf-8"))
                    ),
                    "review_id": "contract-review-001",
                    "reviewed_at": "2026-08-07T12:00:00Z",
                    "reviewer": "test-reviewer",
                    "approve_map": True,
                    "decisions": [],
                }
            ),
            encoding="utf-8",
        )
        approved = self.root / "contract-approved-map.json"
        run(
            "apply-review-decisions",
            "--map",
            canonical,
            "--decisions",
            decisions,
            "--output",
            approved,
            artifact=approved,
            schema_version="modbus-map/v1",
        )
        plan = self.root / "contract-plan.json"
        run(
            "compile-read-plan",
            "--input",
            canonical,
            "--output",
            plan,
            artifact=plan,
            schema_version="modbus-read-plan/v1",
        )

        diagnosis = self.root / "contract-diagnosis"
        run(
            "diagnose-map",
            "--input",
            FIXTURES / "source_map.json",
            "--output",
            diagnosis,
        )
        artifacts.extend(
            [
                (diagnosis / "parsed.json", "candidate-map/v1"),
                (diagnosis / "map-draft.json", "modbus-map/v1"),
                (diagnosis / "lint.json", "modbus-map-lint/v1"),
                (
                    diagnosis / "review.json",
                    "modbus-map-evidence-review/v1",
                ),
            ]
        )

        remap = self.root / "contract-remap.json"
        run(
            "remap-addresses",
            "--input",
            canonical,
            "--from",
            "protocol-offset",
            "--to",
            "modicon-reference",
            "--output",
            remap,
            artifact=remap,
            schema_version="modbus-address-remap-preview/v1",
        )
        comparison = self.root / "contract-comparison.json"
        run(
            "compare-maps",
            "--before",
            canonical,
            "--after",
            canonical,
            "--output",
            comparison,
            artifact=comparison,
            schema_version="modbus-map-diff/v1",
        )
        byte_order = self.root / "contract-byte-order.json"
        run(
            "evaluate-byte-order",
            "--input",
            FIXTURES / "raw_sample.json",
            "--types",
            "uint32",
            "--output",
            byte_order,
            artifact=byte_order,
            schema_version="modbus-byte-order-evidence/v1",
        )
        analysis = self.root / "contract-analysis.json"
        run(
            "analyze-capture",
            "--input",
            FIXTURES / "capture.json",
            "--output",
            analysis,
            artifact=analysis,
            schema_version="modbus-capture-analysis/v1",
        )

        for command, folder, result_name in (
            ("generate-node-red", "contract-node-red", "node-red-result.json"),
            ("generate-modpoll", "contract-modpoll", "modpoll-result.json"),
            ("generate-modscan", "contract-modscan", "modscan-result.json"),
        ):
            output = self.root / folder
            arguments: list[object] = [
                "--map",
                canonical,
                "--plan",
                plan,
            ]
            if command == "generate-modpoll":
                arguments.extend(["--profile", "gavinying-cli"])
            arguments.extend(["--output", output])
            run(command, *arguments)
            artifacts.append(
                (output / result_name, "modbus-target-result/v1")
            )

        probe_output = self.root / "contract-probe"
        run(
            "capture-sample",
            "--request",
            FIXTURES / "probe_request.json",
            "--output",
            probe_output,
        )
        artifacts.append(
            (probe_output / "tool-pack-result.json", "modbus-tool-pack/v1")
        )

        pack_request = self.root / "contract-pack-request.json"
        pack_request.write_text(
            json.dumps(
                {
                    "canonical_map": json.loads(
                        canonical.read_text(encoding="utf-8")
                    ),
                    "read_plan": json.loads(plan.read_text(encoding="utf-8")),
                    "targets": ["node-red", "modscan"],
                    "mode": "final",
                }
            ),
            encoding="utf-8",
        )
        pack_output = self.root / "contract-pack"
        run(
            "build-tool-pack",
            "--request",
            pack_request,
            "--output",
            pack_output,
        )
        artifacts.append(
            (pack_output / "tool-pack-result.json", "modbus-tool-pack/v1")
        )

        custom_output = self.root / "contract-custom"
        run(
            "infer-custom-format",
            "--example",
            FIXTURES / "custom_format_example.csv",
            "--map",
            canonical,
            "--output",
            custom_output,
        )
        artifacts.extend(
            [
                (
                    custom_output / "format-config.json",
                    "modbus-custom-format-config/v1",
                ),
                (
                    custom_output / "evidence.json",
                    "modbus-custom-format-evidence/v1",
                ),
            ]
        )

        pdf_source = self.root / "contract.pdf"
        pdf_source.write_bytes(b"%PDF-1.4\n% synthetic contract fixture\n")
        pdf_output = self.root / "contract-pdf"
        with mock.patch("modbus_skills.cli.shutil.which", return_value=None):
            run(
                "extract-pdf",
                "--input",
                pdf_source,
                "--pages",
                "42-48",
                "--output",
                pdf_output,
            )
        artifacts.append(
            (
                pdf_output / "pdf-extraction.json",
                "modbus-pdf-extraction/v1",
            )
        )

        self.assertEqual(set(COMMANDS), exercised)
        for path, expected_schema in artifacts:
            with self.subTest(path=path.name, schema=expected_schema):
                raw = path.read_text(encoding="utf-8")
                decoded = json.loads(raw)
                assert_artifact_envelope(decoded)
                self.assertEqual(expected_schema, decoded["schema_version"])
                self.assertEqual(decoded, json.loads(json.dumps(decoded)))
                for digest in decoded["input_hashes"].values():
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")
                    self.assertNotIn(str(self.root), digest)

    def test_pdf_page_range_is_bounded_and_ocr_is_held(self) -> None:
        source = self.root / "ocr.pdf"
        source.write_bytes(b"%PDF-1.4\n% synthetic OCR fixture\n")
        output = self.root / "ocr-output"
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"\f", stderr=b""
        )
        with mock.patch(
            "modbus_skills.cli.shutil.which", return_value="/usr/bin/pdftotext"
        ), mock.patch(
            "modbus_skills.cli.subprocess.run", return_value=completed
        ) as run_mock:
            receipt = self.run_command(
                "extract-pdf",
                "--input",
                source,
                "--pages",
                "42,43-48",
                "--output",
                output,
            )
        self.assertEqual("held", receipt["status"])
        command = run_mock.call_args.args[0]
        self.assertEqual("42", command[command.index("-f") + 1])
        self.assertEqual("48", command[command.index("-l") + 1])
        artifact = json.loads(
            (output / "pdf-extraction.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pdf-ocr-required", artifact["holds"][0]["code"])

    def test_pdf_accepts_bounded_local_ocr_evidence_without_storing_full_text(self) -> None:
        source = self.root / "scanned.pdf"
        source.write_bytes(b"%PDF-1.4\n% synthetic scanned fixture\n")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        ocr = self.root / "ocr-evidence.json"
        sentinel = "FULL OCR TEXT MUST NOT ENTER THE OUTPUT"
        ocr.write_text(
            json.dumps(
                {
                    "schema_version": "modbus-ocr-evidence/v1",
                    "artifact_type": "modbus-ocr-evidence",
                    "input_hashes": {"source_pdf": source_hash},
                    "assumptions": [],
                    "findings": [],
                    "holds": [],
                    "source_sha256": source_hash,
                    "tool": {"name": "Synthetic OCR", "version": "1.0"},
                    "pages": [
                        {
                            "page_index": 42,
                            "printed_page_label": "A-7",
                            "text": (
                                "Address  Name  Data Type\n"
                                "40001  Tank Level  float32\n"
                                f"{sentinel}\n"
                            ),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "ocr-import-output"

        receipt = self.run_command(
            "extract-pdf",
            "--input",
            source,
            "--pages",
            "42",
            "--ocr-evidence",
            ocr,
            "--output",
            output,
        )

        self.assertEqual("held", receipt["status"])
        raw_output = (output / "pdf-extraction.json").read_text(encoding="utf-8")
        self.assertNotIn(sentinel, raw_output)
        artifact = json.loads(raw_output)
        self.assertEqual("pdf-ocr-human-review-required", artifact["holds"][0]["code"])
        self.assertEqual("Synthetic OCR", artifact["ocr_tool"]["name"])
        self.assertEqual(1, len(artifact["records"]))
        source_evidence = artifact["records"][0]["_source"]
        self.assertEqual(42, source_evidence["page"])
        self.assertEqual("A-7", source_evidence["printed_page_label"])
        self.assertEqual("ocr-derived", source_evidence["method"])
        self.assertIn("ocr_evidence", artifact["input_hashes"])

    def test_pdf_rejects_ocr_evidence_for_another_source(self) -> None:
        source = self.root / "source.pdf"
        source.write_bytes(b"%PDF-1.4\n% source one\n")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        ocr = self.root / "wrong-source.json"
        ocr.write_text(
            json.dumps(
                {
                    "schema_version": "modbus-ocr-evidence/v1",
                    "artifact_type": "modbus-ocr-evidence",
                    "input_hashes": {"source_pdf": source_hash},
                    "assumptions": [],
                    "findings": [],
                    "holds": [],
                    "source_sha256": "0" * 64,
                    "tool": {"name": "Synthetic OCR", "version": "1.0"},
                    "pages": [{"page_index": 1, "text": "Address  Name\n1  Test\n"}],
                }
            ),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            stderr
        ):
            code = run_cli(
                "extract-pdf",
                [
                    "--input",
                    str(source),
                    "--pages",
                    "1",
                    "--ocr-evidence",
                    str(ocr),
                    "--output",
                    str(self.root / "wrong-source-output"),
                ],
            )
        self.assertEqual(2, code)
        self.assertIn("does not match the input PDF", stderr.getvalue())

    def test_pdf_requires_the_ocr_common_envelope(self) -> None:
        source = self.root / "source.pdf"
        source.write_bytes(b"%PDF-1.4\n% common envelope fixture\n")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        ocr = self.root / "missing-envelope.json"
        ocr.write_text(
            json.dumps(
                {
                    "schema_version": "modbus-ocr-evidence/v1",
                    "source_sha256": source_hash,
                    "tool": {"name": "Synthetic OCR", "version": "1.0"},
                    "pages": [{"page_index": 1, "text": "Address  Name\n1  Test\n"}],
                }
            ),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            stderr
        ):
            code = run_cli(
                "extract-pdf",
                [
                    "--input",
                    str(source),
                    "--pages",
                    "1",
                    "--ocr-evidence",
                    str(ocr),
                    "--output",
                    str(self.root / "missing-envelope-output"),
                ],
            )
        self.assertEqual(2, code)
        self.assertIn("common envelope is invalid", stderr.getvalue())

    def test_pdf_page_selection_rejects_noncontiguous_pages(self) -> None:
        source = self.root / "pages.pdf"
        source.write_bytes(b"%PDF-1.4\n% synthetic page fixture\n")
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            stderr
        ):
            code = run_cli(
                "extract-pdf",
                [
                    "--input",
                    str(source),
                    "--pages",
                    "1,3",
                    "--output",
                    str(self.root / "pages-output"),
                ],
            )
        self.assertEqual(2, code)
        self.assertIn("must resolve to one contiguous range", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
