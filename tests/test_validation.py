import unittest

from modbus_skills.models import (
    AddressConvention,
    CanonicalPoint,
    DataType,
    RegisterArea,
    SourceAddress,
)
from modbus_skills.validation import validate_function_codes, validate_points


def point(
    logical_id="p1",
    *,
    route="route-a",
    unit=1,
    area=RegisterArea.HOLDING_REGISTER,
    offset=0,
    datatype=DataType.UINT16,
    word_span=None,
    byte_order=None,
    function_code=3,
):
    return CanonicalPoint(
        logical_point_id=logical_id,
        route_id=route,
        unit_id=unit,
        area=area,
        protocol_offset=offset,
        source_address=SourceAddress(offset, AddressConvention.PROTOCOL_OFFSET),
        datatype=datatype,
        word_span=word_span,
        byte_order=byte_order,
        function_code=function_code,
    )


class PointValidationTests(unittest.TestCase):
    def test_valid_single_register_point_has_no_findings(self):
        self.assertEqual(validate_points((point(),)), ())

    def test_unknown_values_remain_holds(self):
        candidate = CanonicalPoint.from_mapping(
            {
                "logical_point_id": "candidate",
                "source_address": {"raw": "?", "convention": "unknown"},
            }
        )

        codes = {finding.code for finding in validate_points((candidate,))}

        self.assertTrue(
            {
                "point.route-unresolved",
                "point.unit-id-unresolved",
                "point.area-unresolved",
                "point.address-unresolved",
                "point.datatype-unresolved",
            }.issubset(codes)
        )

    def test_multi_register_point_requires_explicit_byte_layout(self):
        findings = validate_points((point(datatype=DataType.FLOAT32),))
        self.assertIn("point.byte-order-unresolved", {item.code for item in findings})

    def test_pending_byte_layout_remains_a_blocking_hold(self):
        candidate = point(datatype=DataType.FLOAT32, byte_order="ABCD")
        candidate = CanonicalPoint.from_mapping(
            {**candidate.to_dict(), "byte_order_confirmed": False}
        )

        findings = validate_points((candidate,))

        self.assertIn("point.byte-order-unconfirmed", {item.code for item in findings})

    def test_one_register_byte_order_not_applicable_is_valid(self):
        candidate = CanonicalPoint.from_mapping(
            {
                **point(datatype=DataType.UINT16).to_dict(),
                "byte_order": None,
                "byte_order_confirmed": None,
                "byte_order_status": "not-applicable",
            }
        )

        findings = validate_points((candidate,))

        self.assertNotIn("point.byte-order-status-invalid", {item.code for item in findings})

    def test_word_span_must_match_known_data_type(self):
        findings = validate_points(
            (point(datatype=DataType.FLOAT64, word_span=2, byte_order="ABCDEFGH"),)
        )
        self.assertIn("point.datatype-span-mismatch", {item.code for item in findings})

    def test_byte_layout_must_match_the_data_width(self):
        findings = validate_points(
            (point(datatype=DataType.FLOAT32, byte_order="ABCDEFGH"),)
        )
        self.assertIn("point.byte-order-invalid", {item.code for item in findings})

    def test_bit_areas_require_boolean_points(self):
        findings = validate_points(
            (
                point(
                    area=RegisterArea.COIL,
                    datatype=DataType.UINT16,
                    function_code=1,
                ),
            )
        )
        self.assertIn("point.datatype-area-mismatch", {item.code for item in findings})

    def test_duplicate_canonical_identity_is_an_error(self):
        duplicate = point()
        findings = validate_points((duplicate, duplicate))
        self.assertIn("point.duplicate-identity", {item.code for item in findings})

    def test_overlapping_ranges_are_reported(self):
        left = point("left", offset=10, datatype=DataType.UINT32, byte_order="ABCD")
        right = point("right", offset=11)
        findings = validate_points((left, right))
        overlap = next(item for item in findings if item.code == "point.overlapping-range")

        self.assertEqual(overlap.point_ids, ("left", "right"))

    def test_same_offset_in_different_area_unit_or_route_does_not_overlap(self):
        points = (
            point("base"),
            point("input", area=RegisterArea.INPUT_REGISTER, function_code=4),
            point("unit", unit=2),
            point("route", route="route-b"),
        )
        findings = validate_points(points)

        self.assertNotIn("point.overlapping-range", {item.code for item in findings})

    def test_write_and_other_function_codes_are_forbidden(self):
        findings = validate_function_codes((1, 4, 5, 6, 15, 16, 22, 23, 99))
        self.assertEqual(len(findings), 7)
        self.assertEqual(
            sum(item.code == "function-code.write-forbidden" for item in findings),
            6,
        )
        self.assertEqual(findings[-1].code, "function-code.unsupported")

    def test_area_function_mismatch_is_an_error(self):
        findings = validate_points(
            (point(area=RegisterArea.INPUT_REGISTER, function_code=3),)
        )
        self.assertIn("function-code.area-mismatch", {item.code for item in findings})

    def test_broadcast_unit_id_is_forbidden(self):
        findings = validate_points((point(unit=0),))
        self.assertIn(
            "point.unit-id-broadcast-forbidden", {item.code for item in findings}
        )

    def test_point_range_cannot_extend_above_65535(self):
        findings = validate_points(
            (
                point(
                    offset=65_535,
                    datatype=DataType.UINT32,
                    byte_order="ABCD",
                ),
            )
        )
        self.assertIn("point.range-out-of-bounds", {item.code for item in findings})

    def test_mapping_inputs_are_supported_without_address_guessing(self):
        findings = validate_points(
            (
                {
                    "logical_point_id": "mapped",
                    "route_id": "r",
                    "unit_id": 1,
                    "area": "holding-register",
                    "protocol_offset": 0,
                    "source_address": {
                        "raw": "40001",
                        "convention": "modicon-reference",
                    },
                    "datatype": "uint16",
                    "function_code": 3,
                },
            )
        )
        self.assertEqual(findings, ())


if __name__ == "__main__":
    unittest.main()
