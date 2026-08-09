from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "modbus-skills" / "runtime"))

from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler_contracts import (
    build_device_binding,
    build_oem_map,
    build_user_map,
    build_user_selection,
)
from modbus_skills.map_linking import MapLinkError, link_selected_map


def artifacts() -> tuple[dict[str, object], ...]:
    oem = build_oem_map(
        [
            {
                "oem_point_id": "temperature",
                "name": "Temperature",
                "area": "holding-register",
                "protocol_offset": 257,
                "datatype": "float32",
                "word_span": 2,
                "source_refs": [{"page_index": 1, "row_index": 1, "region_id": "r1"}],
            },
            {
                "oem_point_id": "status",
                "name": "Status",
                "area": "holding-register",
                "protocol_offset": 308,
                "datatype": "uint16",
                "word_span": 1,
                "source_refs": [{"page_index": 1, "row_index": 2, "region_id": "r2"}],
            },
            {
                "oem_point_id": "diagnostic",
                "name": "Diagnostic",
                "area": "holding-register",
                "protocol_offset": 400,
                "datatype": "uint16",
                "word_span": 1,
                "source_refs": [{"page_index": 2, "row_index": 1, "region_id": "r3"}],
            },
        ],
        source_hash="a" * 64,
        holds=[
            {"code": "STATUS_HELD", "oem_point_id": "status", "message": "Status needs review"},
            {"code": "DIAGNOSTIC_HELD", "oem_point_id": "diagnostic", "message": "Diagnostic needs review"},
        ],
    )
    selection = build_user_selection(
        oem,
        requested_measurements=["temperature", "status"],
        included=[
            {"oem_point_id": "temperature", "reason": "requested", "evidence_refs": ["r1"]},
            {"oem_point_id": "status", "reason": "requested", "evidence_refs": ["r2"]},
        ],
        excluded=[{"oem_point_id": "diagnostic", "reason": "not requested", "evidence_refs": ["r3"]}],
    )
    user_map = build_user_map(
        oem,
        selection,
        points=[
            {"oem_point_id": "temperature", "alias": "temp"},
            {"oem_point_id": "status", "alias": "state"},
        ],
        exception_annex=[
            {"kind": "unselected-hold", "oem_point_id": "diagnostic", "code": "DIAGNOSTIC_HELD"}
        ],
    )
    binding = build_device_binding(
        oem,
        route_id="plant-a",
        unit_id=7,
        read_constraints={
            "max_quantities": {"holding-register": 100},
            "readable_islands": [
                {
                    "island_id": "main-table",
                    "route_id": "plant-a",
                    "unit_id": 7,
                    "area": "holding-register",
                    "function_code": 3,
                    "start_offset": 257,
                    "end_offset": 308,
                    "reason": "OEM table declares a continuous readable block",
                    "evidence_refs": ["manual:p1:table-main"],
                }
            ],
            "unsafe_intervals": [],
        },
    )
    return oem, selection, user_map, binding


class MapLinkingTests(unittest.TestCase):
    def test_selected_points_are_projected_with_exact_bound_identity(self) -> None:
        oem, selection, user_map, binding = artifacts()

        linked = link_selected_map(oem, selection, user_map, binding)
        again = link_selected_map(oem, selection, user_map, binding)

        self.assertEqual(linked, again)
        self.assertEqual(linked["schema_version"], "modbus-map/v1")
        self.assertEqual(
            [point["logical_point_id"] for point in linked["points"]],
            ["temperature", "status"],
        )
        self.assertEqual(
            [point["canonical_identity"] for point in linked["points"]],
            [
                ["plant-a", 7, "holding-register", 257, "temperature"],
                ["plant-a", 7, "holding-register", 308, "status"],
            ],
        )
        self.assertEqual(linked["input_hashes"]["binding"], stable_input_hash(binding))
        self.assertEqual(linked["read_constraints"], binding["read_constraints"])

    def test_only_selected_holds_block_and_unselected_holds_stay_in_annex(self) -> None:
        oem, selection, user_map, binding = artifacts()

        linked = link_selected_map(oem, selection, user_map, binding)

        self.assertEqual([hold["code"] for hold in linked["holds"]], ["STATUS_HELD"])
        self.assertEqual(
            [item["code"] for item in linked["exception_annex"]],
            ["DIAGNOSTIC_HELD"],
        )

    def test_stale_or_incomplete_artifact_chain_is_rejected(self) -> None:
        oem, selection, user_map, binding = artifacts()
        stale = copy.deepcopy(binding)
        stale["input_hashes"]["oem_map"] = "b" * 64
        with self.assertRaisesRegex(MapLinkError, "stale OEM map hash"):
            link_selected_map(oem, selection, user_map, stale)

        incomplete = copy.deepcopy(user_map)
        incomplete["points"] = incomplete["points"][:1]
        with self.assertRaisesRegex(MapLinkError, "exactly the included selection"):
            link_selected_map(oem, selection, incomplete, binding)

    def test_point_override_is_allowlisted_and_identity_is_recomputed(self) -> None:
        oem, selection, user_map, _ = artifacts()
        binding = build_device_binding(
            oem,
            route_id="plant-a",
            unit_id=7,
            point_overrides=[
                {"oem_point_id": "temperature", "protocol_offset": 500}
            ],
        )
        linked = link_selected_map(oem, selection, user_map, binding)
        temperature = next(
            point for point in linked["points"] if point["logical_point_id"] == "temperature"
        )
        self.assertEqual(temperature["protocol_offset"], 500)
        self.assertEqual(temperature["canonical_identity"][3], 500)

        unsafe = copy.deepcopy(binding)
        unsafe["point_overrides"][0]["route_id"] = "other"
        with self.assertRaisesRegex(MapLinkError, "unsupported fields"):
            link_selected_map(oem, selection, user_map, unsafe)


if __name__ == "__main__":
    unittest.main()
