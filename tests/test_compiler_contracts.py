from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler_contracts import (
    CompilerContractError,
    bound_point_identity,
    build_compile_case,
    build_device_binding,
    build_oem_map,
    build_user_map,
    build_user_selection,
    validate_compile_case,
    validate_device_binding,
    validate_oem_map,
    validate_user_map,
    validate_user_selection,
)


SOURCE_HASH = "a" * 64


def oem_points() -> list[dict[str, object]]:
    return [
        {
            "oem_point_id": "ambient-temperature",
            "name": "Ambient temperature",
            "area": "holding-register",
            "protocol_offset": 256,
            "datatype": "int16",
            "word_span": 1,
            "source_refs": [
                {"page_index": 12, "row_index": 4, "region_id": "table-1-row-4"}
            ],
        },
        {
            "oem_point_id": "operating-status",
            "name": "Operating status",
            "area": "holding-register",
            "protocol_offset": 300,
            "datatype": "uint16",
            "word_span": 1,
            "source_refs": [
                {"page_index": 13, "row_index": 1, "region_id": "table-2-row-1"}
            ],
        },
    ]


def selection_entries() -> dict[str, list[dict[str, object]]]:
    return {
        "included": [
            {
                "oem_point_id": "ambient-temperature",
                "reason": "Matches requested temperature measurement",
                "confidence": 0.99,
                "group": "environment",
                "alias": "ambient_temp",
            }
        ],
        "suggested": [
            {
                "oem_point_id": "operating-status",
                "reason": "Useful status context",
                "confidence": 0.72,
            }
        ],
        "excluded": [],
    }


class CompilerContractTests(unittest.TestCase):
    def test_oem_map_preserves_source_coverage_contract(self) -> None:
        coverage = {
            "status": "complete",
            "accepted_row_count": 1,
            "rejected_row_count": 0,
            "quarantined_row_count": 0,
            "detected_pages": [2],
            "covered_pages": [2],
            "detected_regions": ["p2:t0:r4"],
            "basis": "bounded-discovery",
            "discovery_complete": True,
            "independent_parser_row_count": 1,
            "single_parser_row_count": 0,
        }

        artifact = build_oem_map(
            [oem_points()[0]], source_hash="a" * 64, source_coverage=coverage
        )

        self.assertEqual(coverage, artifact["source_coverage"])
        validate_oem_map(artifact)

    def test_oem_map_rejects_invalid_source_coverage(self) -> None:
        valid = {
            "status": "complete",
            "accepted_row_count": 1,
            "rejected_row_count": 0,
            "quarantined_row_count": 0,
            "detected_pages": [2],
            "covered_pages": [2],
            "detected_regions": ["p2:t0:r4"],
            "basis": "bounded-discovery",
            "discovery_complete": True,
        }
        invalid_cases = (
            {**valid, "status": "maybe"},
            {**valid, "accepted_row_count": -1},
            {**valid, "detected_pages": "2"},
            {**valid, "discovery_complete": "yes"},
            {**valid, "covered_pages": []},
            {**valid, "detected_pages": [], "covered_pages": []},
        )

        for coverage in invalid_cases:
            with self.subTest(coverage=coverage), self.assertRaises(CompilerContractError):
                build_oem_map(
                    [oem_points()[0]],
                    source_hash="a" * 64,
                    source_coverage=coverage,
                )

    def test_legacy_complete_coverage_without_covered_pages_remains_readable(self) -> None:
        coverage = {
            "status": "complete",
            "accepted_row_count": 1,
            "rejected_row_count": 0,
            "quarantined_row_count": 0,
            "detected_pages": [2],
            "detected_regions": ["p2:t0:r4"],
            "basis": "legacy-bounded-discovery",
            "discovery_complete": True,
        }

        artifact = build_oem_map(
            [oem_points()[0]], source_hash="a" * 64, source_coverage=coverage
        )

        validate_oem_map(artifact)

    def test_pdf_field_evidence_requires_raw_and_normalized_values(self) -> None:
        point = copy.deepcopy(oem_points()[0])
        point["source_field_evidence"] = [
            {
                "field": "datatype",
                "raw_header": "Type",
                "raw_value": "int16",
                "normalized_value": "int16",
                "source_ref": "p2:t0:r4",
                "status": "confirmed",
            }
        ]
        artifact = build_oem_map(
            [point],
            source_hash="a" * 64,
            source_reference={"filename": "synthetic.pdf", "format": "pdf"},
        )
        validate_oem_map(artifact)

        invalid = copy.deepcopy(point)
        invalid["source_field_evidence"][0].pop("raw_value")
        with self.assertRaises(CompilerContractError):
            build_oem_map(
                [invalid],
                source_hash="a" * 64,
                source_reference={"filename": "synthetic.pdf", "format": "pdf"},
            )

    def test_contracts_are_deterministic_and_hash_bound(self) -> None:
        first_oem = build_oem_map(
            list(reversed(oem_points())),
            source_hash=SOURCE_HASH,
            source_reference={"document_id": "synthetic-manual", "revision": "A"},
        )
        second_oem = build_oem_map(
            oem_points(),
            source_hash=SOURCE_HASH,
            source_reference={"revision": "A", "document_id": "synthetic-manual"},
        )
        self.assertEqual(first_oem, second_oem)
        validate_oem_map(first_oem)

        selection = build_user_selection(
            first_oem,
            requested_measurements=["temperature", "status"],
            **selection_entries(),
        )
        binding = build_device_binding(
            first_oem,
            route_id="plant-a",
            unit_id=7,
            transport={"kind": "tcp"},
            read_constraints={"maximum_registers": 100},
        )
        user_map = build_user_map(
            first_oem,
            selection,
            points=[
                {
                    "oem_point_id": "ambient-temperature",
                    "display_name": "Ambient temperature",
                    "alias": "ambient_temp",
                    "group": "environment",
                }
            ],
        )

        validate_user_selection(selection, first_oem)
        validate_device_binding(binding, first_oem)
        validate_user_map(user_map, first_oem, selection)
        self.assertEqual(selection["input_hashes"]["oem_map"], stable_input_hash(first_oem))
        self.assertEqual(binding["input_hashes"]["oem_map"], stable_input_hash(first_oem))
        self.assertEqual(user_map["input_hashes"]["selection"], stable_input_hash(selection))

    def test_oem_map_needs_no_deployment_binding(self) -> None:
        artifact = build_oem_map(oem_points(), source_hash=SOURCE_HASH)
        validate_oem_map(artifact)
        serialized = repr(artifact)
        self.assertNotIn("route_id", serialized)
        self.assertNotIn("unit_id", serialized)
        self.assertNotIn("endpoint", serialized)

    def test_structured_source_uses_a_stable_record_locator_without_pages(self) -> None:
        points = oem_points()
        points[0]["source_refs"] = [
            {"record_id": "registers-row-17", "region_id": "registers"}
        ]

        artifact = build_oem_map(points, source_hash=SOURCE_HASH)

        validate_oem_map(artifact)
        self.assertEqual(
            artifact["points"][0]["source_refs"][0]["record_id"],
            "registers-row-17",
        )

    def test_unknown_points_and_stale_parent_hashes_are_rejected(self) -> None:
        oem_map = build_oem_map(oem_points(), source_hash=SOURCE_HASH)
        entries = selection_entries()
        entries["included"][0]["oem_point_id"] = "not-in-map"
        with self.assertRaisesRegex(CompilerContractError, "unknown OEM point"):
            build_user_selection(
                oem_map,
                requested_measurements=["temperature"],
                **entries,
            )

        selection = build_user_selection(
            oem_map,
            requested_measurements=["temperature"],
            **selection_entries(),
        )
        stale = copy.deepcopy(selection)
        stale["input_hashes"]["oem_map"] = "b" * 64
        with self.assertRaisesRegex(CompilerContractError, "stale OEM map hash"):
            validate_user_selection(stale, oem_map)

        binding = build_device_binding(oem_map, route_id="plant-a", unit_id=7)
        stale_binding = copy.deepcopy(binding)
        stale_binding["input_hashes"]["oem_map"] = "b" * 64
        with self.assertRaisesRegex(CompilerContractError, "stale OEM map hash"):
            validate_device_binding(stale_binding, oem_map)

        user_map = build_user_map(
            oem_map,
            selection,
            points=[{"oem_point_id": "ambient-temperature"}],
        )
        stale_user_map = copy.deepcopy(user_map)
        stale_user_map["input_hashes"]["selection"] = "b" * 64
        with self.assertRaisesRegex(CompilerContractError, "stale selection hash"):
            validate_user_map(stale_user_map, oem_map, selection)

    def test_duplicate_ids_and_point_id_collisions_are_explicit(self) -> None:
        duplicate = oem_points()
        duplicate.append(copy.deepcopy(duplicate[0]))
        with self.assertRaisesRegex(CompilerContractError, "duplicate OEM point ID"):
            build_oem_map(duplicate, source_hash=SOURCE_HASH)

        collision = oem_points()
        collision.append(
            {
                **collision[0],
                "protocol_offset": 999,
            }
        )
        with self.assertRaisesRegex(CompilerContractError, "OEM point ID collision"):
            build_oem_map(collision, source_hash=SOURCE_HASH)

    def test_bound_identity_is_deterministic_and_requires_binding(self) -> None:
        point = oem_points()[0]
        identity = bound_point_identity(point, route_id="plant-a", unit_id=7)
        self.assertEqual(
            identity,
            ("plant-a", 7, "holding-register", 256, "ambient-temperature"),
        )
        with self.assertRaises(CompilerContractError):
            bound_point_identity(point, route_id="", unit_id=7)
        with self.assertRaises(CompilerContractError):
            build_device_binding(
                build_oem_map(oem_points(), source_hash=SOURCE_HASH),
                route_id="plant-a",
                unit_id=None,
            )

    def test_selection_dispositions_are_unique_and_reasoned(self) -> None:
        oem_map = build_oem_map(oem_points(), source_hash=SOURCE_HASH)
        entries = selection_entries()
        entries["excluded"] = [
            {
                "oem_point_id": "ambient-temperature",
                "reason": "Conflicting disposition",
            }
        ]
        with self.assertRaisesRegex(CompilerContractError, "more than one disposition"):
            build_user_selection(
                oem_map,
                requested_measurements=["temperature"],
                **entries,
            )

    def test_portable_artifacts_reject_private_or_deployment_payloads(self) -> None:
        points = oem_points()
        points[0]["password"] = "do-not-copy"
        with self.assertRaisesRegex(CompilerContractError, "portable artifact field"):
            build_oem_map(points, source_hash=SOURCE_HASH)

        oem_map = build_oem_map(oem_points(), source_hash=SOURCE_HASH)
        selection = build_user_selection(
            oem_map,
            requested_measurements=["temperature"],
            **selection_entries(),
        )
        with self.assertRaisesRegex(CompilerContractError, "portable artifact field"):
            build_user_map(
                oem_map,
                selection,
                points=[
                    {
                        "oem_point_id": "ambient-temperature",
                        "raw_evidence": "private manual excerpt",
                    }
                ],
            )
        with self.assertRaisesRegex(CompilerContractError, "local absolute path"):
            build_user_map(
                oem_map,
                selection,
                points=[
                    {
                        "oem_point_id": "ambient-temperature",
                        "notes": "/private/tmp/manual.txt",
                    }
                ],
            )

    def test_compile_case_keeps_control_paths_local_and_case_relative(self) -> None:
        case = build_compile_case(
            source_hash=SOURCE_HASH,
            request_hash="b" * 64,
            compiler_version="1",
            state="running",
            artifacts={
                "oem_map": {
                    "path": "artifacts/oem-map.json",
                    "sha256": "c" * 64,
                    "schema_version": "modbus-oem-map/v1",
                }
            },
            next_action={"kind": "continue"},
        )
        validate_compile_case(case)
        self.assertEqual(case["schema_version"], "modbus-compile-case/v1")
        self.assertEqual(
            case["case_id"],
            build_compile_case(
                source_hash=SOURCE_HASH,
                request_hash="b" * 64,
                compiler_version="1",
                state="running",
            )["case_id"],
        )
        with self.assertRaisesRegex(CompilerContractError, "case-relative"):
            build_compile_case(
                source_hash=SOURCE_HASH,
                request_hash="b" * 64,
                compiler_version="1",
                state="running",
                artifacts={
                    "oem_map": {
                        "path": "/tmp/oem-map.json",
                        "sha256": "c" * 64,
                        "schema_version": "modbus-oem-map/v1",
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()
