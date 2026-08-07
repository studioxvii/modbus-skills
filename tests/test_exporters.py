from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "plugins" / "modbus-skills" / "runtime"
sys.path.insert(0, str(RUNTIME))

from modbus_skills.artifacts import assert_artifact_envelope  # noqa: E402
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

    def test_probe_rejects_broadcast_unit_and_missing_route(self) -> None:
        point = sample_point(unit_id=0, route_id=None)
        canonical_map = sample_map(point)
        plan = {
            "requests": [
                {
                    "request_id": "raw",
                    "route_id": None,
                    "unit_id": 0,
                    "area": "holding-register",
                    "function_code": 3,
                    "start_offset": 100,
                    "quantity": 2,
                    "points": [{"logical_point_id": "pressure"}],
                }
            ]
        }
        codes = {
            finding.code
            for finding in preflight_common(canonical_map, plan, mode="probe")
        }
        self.assertIn("POINT_ROUTE_UNRESOLVED", codes)
        self.assertIn("POINT_UNIT_UNRESOLVED", codes)
        self.assertIn("BLOCK_ROUTE_UNRESOLVED", codes)
        self.assertIn("BLOCK_UNIT_UNRESOLVED", codes)

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
