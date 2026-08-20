from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.artifacts import (  # noqa: E402
    artifact_envelope,
    assert_artifact_envelope,
)
from modbus_skills.exporters import (  # noqa: E402
    Artifact,
    ExportResult,
    ExporterInputError,
    Finding,
    preflight_common,
    stable_hash,
    stable_json,
    target_manifest,
)
from modbus_skills.read_plan import compile_read_plan  # noqa: E402


def sample_point(**updates: object) -> dict[str, object]:
    point: dict[str, object] = {
        "logical_point_id": "pressure",
        "name": "Discharge Pressure",
        "route_id": "default",
        "unit_id": 1,
        "area": "holding-register",
        "protocol_offset": 100,
        "source_address": {"raw": "40101", "convention": "modicon-reference"},
        "datatype": "float32",
        "word_span": 2,
        "byte_order": "ABCD",
        "byte_order_confirmed": True,
        "normalization_status": "confirmed",
        "scale": 0.1,
        "engineering_offset": 0.0,
    }
    point.update(updates)
    return point


def sample_map(*points: dict[str, object]) -> dict[str, object]:
    return {"schema_version": "modbus-map/v1", "points": list(points or (sample_point(),))}


def sample_plan(*points: dict[str, object]) -> dict[str, object]:
    return compile_read_plan(points or (sample_point(),)).to_dict()


class ExporterContractTests(unittest.TestCase):
    def test_json_and_hash_are_stable_across_mapping_order(self) -> None:
        left = {"b": 2, "a": {"d": 4, "c": 3}}
        right = {"a": {"c": 3, "d": 4}, "b": 2}
        self.assertEqual(stable_json(left), stable_json(right))
        self.assertEqual(stable_hash(left), stable_hash(right))

    def test_artifact_rejects_path_traversal_and_absolute_paths(self) -> None:
        for path in ("../secret", "/absolute", "safe/../../secret", "win\\path"):
            with self.subTest(path=path), self.assertRaises(ExporterInputError):
                Artifact.text(path, "text/plain", "value", "test")

    def test_final_mode_holds_each_required_unresolved_value(self) -> None:
        cases = {
            "route_id": (None, "POINT_ROUTE_UNRESOLVED"),
            "area": (None, "POINT_AREA_UNRESOLVED"),
            "protocol_offset": (None, "POINT_ADDRESS_UNRESOLVED"),
            "unit_id": (None, "POINT_UNIT_UNRESOLVED"),
            "datatype": (None, "POINT_DATATYPE_UNRESOLVED"),
            "byte_order": (None, "POINT_BYTE_ORDER_UNRESOLVED"),
        }
        for field, (value, expected_code) in cases.items():
            with self.subTest(field=field):
                point = sample_point(**{field: value})
                canonical_map = sample_map(point)
                plan_point = sample_point()
                plan = sample_plan(plan_point)
                findings = preflight_common(canonical_map, plan, mode="final")
                self.assertIn(expected_code, {finding.code for finding in findings})

    def test_probe_allows_unknown_datatype_and_byte_order(self) -> None:
        point = sample_point(
            datatype=None,
            byte_order=None,
            byte_order_confirmed=False,
            normalization_status="pending",
        )
        canonical_map = sample_map(point)
        # Raw probing still needs an explicit span so one safe read can be made.
        plan = compile_read_plan([point]).to_dict()
        findings = preflight_common(canonical_map, plan, mode="probe")
        self.assertFalse(any(finding.severity == "error" for finding in findings))

    def test_probe_rejects_tcp_gateway_unit_ids_and_discloses_scope(self) -> None:
        for unit_id in (0, 255):
            with self.subTest(unit_id=unit_id):
                point = sample_point(unit_id=unit_id, route_id=None)
                canonical_map = sample_map(point)
                plan = {
                    "requests": [
                        {
                            "request_id": "raw",
                            "route_id": None,
                            "unit_id": unit_id,
                            "area": "holding-register",
                            "function_code": 3,
                            "start_offset": 100,
                            "quantity": 2,
                            "points": [{"logical_point_id": "pressure"}],
                        }
                    ]
                }
                findings = {
                    finding.code: finding
                    for finding in preflight_common(canonical_map, plan, mode="probe")
                }
                self.assertIn("POINT_ROUTE_UNRESOLVED", findings)
                self.assertIn("BLOCK_ROUTE_UNRESOLVED", findings)
                for code in ("POINT_UNIT_UNRESOLVED", "BLOCK_UNIT_UNRESOLVED"):
                    self.assertIn(code, findings)
                    message = findings[code].message
                    self.assertIn("1 through 247", message)
                    self.assertIn("broadcast requests", message)
                    self.assertIn("Modbus TCP gateway unit IDs 0 and 255", message)

    def test_plan_hash_must_match_the_current_map_after_review_changes(self) -> None:
        original_map = sample_map()
        plan = artifact_envelope(
            sample_plan(),
            schema_version="modbus-read-plan/v1",
            inputs={"canonical_map": original_map},
        )
        changed_map = sample_map(sample_point(byte_order="CDAB"))

        codes = {
            finding.code
            for finding in preflight_common(changed_map, plan, mode="final")
        }

        self.assertIn("PLAN_MAP_HASH_MISMATCH", codes)

    def test_final_plan_hash_is_required_and_must_be_valid(self) -> None:
        canonical_map = sample_map()
        raw_plan = compile_read_plan((sample_point(),)).to_dict()
        missing_codes = {
            finding.code
            for finding in preflight_common(canonical_map, raw_plan, mode="final")
        }
        self.assertIn("PLAN_MAP_HASH_MISSING", missing_codes)

        malformed_plan = dict(raw_plan)
        malformed_plan["input_hashes"] = {"canonical_map": "not-a-sha256"}
        invalid_codes = {
            finding.code
            for finding in preflight_common(
                canonical_map, malformed_plan, mode="final"
            )
        }
        self.assertIn("PLAN_MAP_HASH_INVALID", invalid_codes)

    def test_probe_plan_hash_can_be_missing_but_not_malformed(self) -> None:
        canonical_map = sample_map()
        raw_plan = compile_read_plan((sample_point(),)).to_dict()
        missing_codes = {
            finding.code
            for finding in preflight_common(canonical_map, raw_plan, mode="probe")
        }
        self.assertNotIn("PLAN_MAP_HASH_MISSING", missing_codes)

        malformed_plan = dict(raw_plan)
        malformed_plan["input_hashes"] = {"canonical_map": 123}
        invalid_codes = {
            finding.code
            for finding in preflight_common(
                canonical_map, malformed_plan, mode="probe"
            )
        }
        self.assertIn("PLAN_MAP_HASH_INVALID", invalid_codes)

    def test_active_write_only_and_source_excluded_points_are_rejected(self) -> None:
        readable = sample_point()
        unsafe_points = (
            sample_point(
                logical_point_id="write-command",
                protocol_offset=110,
                datatype="uint16",
                word_span=1,
                byte_order=None,
                access="write-only",
            ),
            sample_point(
                logical_point_id="source-excluded",
                protocol_offset=120,
                datatype="uint16",
                word_span=1,
                byte_order=None,
                source_include=False,
            ),
        )
        canonical_map = sample_map(readable, *unsafe_points)
        plan = sample_plan(readable)
        plan["input_hashes"] = {
            "canonical_map": stable_hash(canonical_map),
        }

        codes = {
            finding.code
            for finding in preflight_common(canonical_map, plan, mode="probe")
        }

        self.assertIn("POINT_WRITE_ONLY_ACTIVE", codes)
        self.assertIn("POINT_SOURCE_EXCLUDED_ACTIVE", codes)

    def test_map_bound_plan_rejects_unjustified_ranges_and_traces(self) -> None:
        first = sample_point()
        second = sample_point(
            logical_point_id="temperature",
            protocol_offset=110,
            datatype="uint16",
            word_span=1,
            byte_order=None,
        )
        canonical_map = sample_map(first, second)
        base_plan = sample_plan(first, second)
        base_plan["input_hashes"] = {
            "canonical_map": stable_hash(canonical_map),
        }

        cases: tuple[tuple[str, dict[str, object], str], ...] = ()
        excess = copy.deepcopy(base_plan)
        excess["requests"][0]["quantity"] += 1  # type: ignore[index,operator]
        cases += (("excess", excess, "BLOCK_RANGE_NOT_EXACT"),)

        unrelated = copy.deepcopy(base_plan)
        unrelated["requests"].append(  # type: ignore[union-attr]
            {
                "request_id": "unrelated",
                "route_id": "other-route",
                "unit_id": 2,
                "area": "holding-register",
                "function_code": 3,
                "start_offset": 500,
                "quantity": 1,
                "points": [],
            }
        )
        cases += (("unrelated", unrelated, "BLOCK_UNJUSTIFIED"),)

        duplicate = copy.deepcopy(base_plan)
        duplicate_block = copy.deepcopy(duplicate["requests"][0])  # type: ignore[index]
        duplicate_block["request_id"] = "duplicate-range"
        duplicate["requests"].append(duplicate_block)  # type: ignore[union-attr]
        cases += (("duplicate", duplicate, "BLOCK_RANGE_DUPLICATE"),)

        overlapping = copy.deepcopy(base_plan)
        overlapping["requests"][1]["start_offset"] = 101  # type: ignore[index]
        overlapping["requests"][1]["quantity"] = 10  # type: ignore[index]
        cases += (("overlap", overlapping, "BLOCK_RANGE_OVERLAP"),)

        bad_trace = copy.deepcopy(base_plan)
        bad_trace["requests"][0]["points"][0]["relative_offset"] = 1  # type: ignore[index]
        cases += (("trace", bad_trace, "BLOCK_POINT_TRACE_MISMATCH"),)

        for label, plan, expected_code in cases:
            with self.subTest(label=label):
                codes = {
                    finding.code
                    for finding in preflight_common(
                        canonical_map,
                        plan,
                        mode="probe",
                    )
                }
                self.assertIn(expected_code, codes)

    def test_map_bound_gap_requires_visible_hashed_readable_island(self) -> None:
        first = sample_point()
        second = sample_point(
            logical_point_id="temperature",
            protocol_offset=110,
            datatype="uint16",
            word_span=1,
            byte_order=None,
        )
        canonical_map = sample_map(first, second)
        readable_island = {
            "island_id": "pressure-table",
            "route_id": "default",
            "unit_id": 1,
            "area": "holding-register",
            "function_code": 3,
            "start_offset": 100,
            "end_offset": 110,
            "reason": "OEM table is continuously readable",
            "evidence_refs": ["manual:table-pressure"],
        }
        options = {
            "max_gap": 0,
            "max_quantities": {},
            "readable_islands": [readable_island],
            "unsafe_intervals": [],
        }
        plan = compile_read_plan((first, second), readable_islands=[readable_island]).to_dict()
        plan["planning_options"] = options
        plan = artifact_envelope(
            plan,
            schema_version="modbus-read-plan/v1",
            inputs={
                "canonical_map": canonical_map,
                "planning_options": options,
            },
        )

        accepted = {
            finding.code
            for finding in preflight_common(canonical_map, plan, mode="final")
        }
        self.assertNotIn("BLOCK_GAP_NOT_EVIDENCED", accepted)
        self.assertNotIn("BLOCK_BRIDGE_TRACE_MISMATCH", accepted)
        self.assertNotIn("PLAN_OPTIONS_HASH_MISMATCH", accepted)

        tampered = copy.deepcopy(plan)
        tampered["planning_options"]["readable_islands"] = []
        tampered_codes = {
            finding.code
            for finding in preflight_common(
                canonical_map, tampered, mode="final"
            )
        }
        self.assertIn("PLAN_OPTIONS_HASH_MISMATCH", tampered_codes)
        self.assertIn("BLOCK_GAP_NOT_EVIDENCED", tampered_codes)

        hidden = copy.deepcopy(plan)
        hidden.pop("planning_options")
        hidden_codes = {
            finding.code
            for finding in preflight_common(canonical_map, hidden, mode="final")
        }
        self.assertIn("PLAN_OPTIONS_MISSING", hidden_codes)
        self.assertIn("BLOCK_GAP_NOT_EVIDENCED", hidden_codes)

        bad_trace = copy.deepcopy(plan)
        bad_trace["requests"][0]["bridged_ranges"][0]["end_offset"] = 108
        bad_trace_codes = {
            finding.code
            for finding in preflight_common(canonical_map, bad_trace, mode="final")
        }
        self.assertIn("BLOCK_BRIDGE_TRACE_MISMATCH", bad_trace_codes)

    def test_map_bound_quantity_obeys_visible_area_limit(self) -> None:
        first = sample_point()
        second = sample_point(
            logical_point_id="temperature",
            protocol_offset=102,
        )
        canonical_map = sample_map(first, second)
        options = {
            "max_gap": 0,
            "max_quantities": {"holding-register": 2},
            "readable_islands": [],
            "unsafe_intervals": [],
        }
        plan = compile_read_plan((first, second)).to_dict()
        plan["planning_options"] = options
        plan = artifact_envelope(
            plan,
            schema_version="modbus-read-plan/v1",
            inputs={
                "canonical_map": canonical_map,
                "planning_options": options,
            },
        )

        codes = {
            finding.code
            for finding in preflight_common(canonical_map, plan, mode="final")
        }

        self.assertIn("BLOCK_QUANTITY_EXCEEDS_PLAN_OPTION", codes)

    def test_same_offset_in_two_areas_is_not_a_duplicate(self) -> None:
        holding = sample_point()
        input_point = sample_point(
            logical_point_id="input-pressure",
            area="input-register",
        )
        canonical_map = sample_map(holding, input_point)
        plan = sample_plan(holding, input_point)
        codes = {
            finding.code
            for finding in preflight_common(canonical_map, plan, mode="final")
        }
        self.assertNotIn("POINT_ID_DUPLICATE", codes)
        self.assertNotIn("POINT_NOT_PLANNED", codes)

    def test_target_manifest_and_result_use_distinct_common_envelopes(self) -> None:
        canonical_map = sample_map()
        read_plan = sample_plan()
        finding = Finding("warning", "REVIEW", "Review this synthetic result.")
        manifest = target_manifest(
            target="synthetic",
            profile=None,
            mode="final",
            adapter_version="1.0.0",
            canonical_map=canonical_map,
            read_plan=read_plan,
            findings=(finding,),
        )
        assert_artifact_envelope(manifest)
        self.assertEqual("modbus-target-manifest/v1", manifest["schema_version"])
        self.assertEqual("modbus-target-manifest", manifest["artifact_type"])
        self.assertEqual([], manifest["holds"])

        result = ExportResult(
            target="synthetic",
            status="generated",
            mode="final",
            map_hash=manifest["input_hashes"]["canonical_map"],
            read_plan_hash=manifest["input_hashes"]["read_plan"],
            adapter_version="1.0.0",
            findings=(finding,),
        ).to_manifest()
        assert_artifact_envelope(result)
        self.assertEqual("modbus-target-result/v1", result["schema_version"])
        self.assertEqual("modbus-target-result", result["artifact_type"])


if __name__ == "__main__":
    unittest.main()
