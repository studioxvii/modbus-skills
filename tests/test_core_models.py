import json
import unittest

from modbus_skills.models import (
    AddressConvention,
    CanonicalPoint,
    DataType,
    Finding,
    FindingSeverity,
    RegisterArea,
    SourceAddress,
)


class CanonicalModelsTests(unittest.TestCase):
    def test_complete_identity_uses_all_five_required_fields(self):
        point = CanonicalPoint(
            logical_point_id="pressure",
            route_id="line-a",
            unit_id=7,
            area=RegisterArea.INPUT_REGISTER,
            protocol_offset=0,
            source_address=SourceAddress("30001", AddressConvention.MODICON_REFERENCE),
            datatype=DataType.FLOAT32,
            byte_order="abcd",
        )

        self.assertEqual(
            point.canonical_identity,
            ("line-a", 7, "input-register", 0, "pressure"),
        )
        self.assertEqual(point.byte_order, "ABCD")

    def test_unresolved_identity_is_none_and_values_stay_unresolved(self):
        point = CanonicalPoint.from_mapping(
            {
                "logical_point_id": "candidate",
                "source_address": {"raw": "40001", "convention": "unknown"},
            }
        )

        self.assertIsNone(point.canonical_identity)
        self.assertEqual(point.area, RegisterArea.UNKNOWN)
        self.assertIsNone(point.protocol_offset)
        self.assertEqual(point.datatype, DataType.UNKNOWN)
        self.assertIsNone(point.byte_order)
        self.assertEqual(point.source_address.raw, "40001")

    def test_unknown_byte_order_marker_stays_unresolved(self):
        point = CanonicalPoint.from_mapping(
            {
                "logical_point_id": "candidate",
                "byte_order": "unknown",
                "source_address": {"raw": 0, "convention": "protocol-offset"},
            }
        )
        self.assertIsNone(point.byte_order)

    def test_mapping_round_trip_is_json_safe_and_preserves_raw_address(self):
        original = {
            "logical_point_id": "flow",
            "name": "Flow",
            "route_id": "gateway-1",
            "unit_id": "12",
            "area": "holding_register",
            "protocol_offset": "123",
            "source_address": {
                "raw": "4_0124",
                "convention": "modicon_reference",
            },
            "datatype": "uint32",
            "word_span": "2",
            "byte_order": "cdab",
            "scale": "0.1",
            "engineering_offset": "-5",
            "function_code": "3",
        }

        result = CanonicalPoint.from_mapping(original).to_dict()

        self.assertEqual(result["source_address"]["raw"], "4_0124")
        self.assertEqual(result["source_address"]["convention"], "modicon-reference")
        self.assertEqual(result["area"], "holding-register")
        self.assertEqual(result["byte_order"], "CDAB")
        json.dumps(result, allow_nan=False)

    def test_finding_copies_details_and_serializes(self):
        details = {"expected": 2}
        finding = Finding(
            code="test",
            severity=FindingSeverity.HOLD,
            message="Review this value.",
            point_ids=("p1",),
            details=details,
        )
        details["expected"] = 4

        self.assertEqual(finding.details["expected"], 2)
        self.assertEqual(finding.to_dict()["severity"], "hold")

    def test_effective_span_is_derived_only_from_known_datatype(self):
        known = CanonicalPoint(
            logical_point_id="p1",
            route_id="r",
            unit_id=1,
            area=RegisterArea.HOLDING_REGISTER,
            protocol_offset=1,
            source_address=SourceAddress(1, AddressConvention.PROTOCOL_OFFSET),
            datatype=DataType.FLOAT64,
        )
        unknown = CanonicalPoint(
            logical_point_id="p2",
            route_id="r",
            unit_id=1,
            area=RegisterArea.HOLDING_REGISTER,
            protocol_offset=2,
            source_address=SourceAddress(2, AddressConvention.PROTOCOL_OFFSET),
        )

        self.assertEqual(known.effective_span, 4)
        self.assertIsNone(unknown.effective_span)

    def test_nested_byte_layout_preserves_pending_evidence(self):
        point = CanonicalPoint.from_mapping(
            {
                "logical_point_id": "candidate",
                "byte_order": {"layout": "cdab", "status": "pending"},
                "byte_order_status": None,
                "byte_order_confirmed": None,
                "source_address": {"raw": 0, "convention": "protocol-offset"},
            }
        )

        self.assertEqual("CDAB", point.byte_order)
        self.assertFalse(point.byte_order_confirmed)
        self.assertEqual("pending", point.byte_order_status)

    def test_integer_and_float_coercion_rejects_lossy_or_nonfinite_values(self):
        base = {
            "logical_point_id": "strict",
            "route_id": "r",
            "unit_id": 1,
            "area": "holding-register",
            "protocol_offset": 0,
            "source_address": {"raw": 0, "convention": "protocol-offset"},
            "datatype": "uint16",
        }
        for field in ("unit_id", "protocol_offset", "word_span", "function_code"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                CanonicalPoint.from_mapping({**base, field: 1.5})
        for field in ("scale", "engineering_offset"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                CanonicalPoint.from_mapping({**base, field: float("inf")})


if __name__ == "__main__":
    unittest.main()
