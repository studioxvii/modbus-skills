import unittest

from modbus_skills.models import (
    AddressConvention,
    CanonicalPoint,
    DataType,
    RegisterArea,
    ReadRequest,
    SourceAddress,
)
from modbus_skills.read_plan import compile_read_plan


def point(
    logical_id,
    offset,
    *,
    route="r1",
    unit=1,
    area=RegisterArea.HOLDING_REGISTER,
    datatype=DataType.UINT16,
    word_span=None,
    byte_order=None,
    function_code=None,
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


class ReadPlanTests(unittest.TestCase):
    def test_contiguous_points_share_one_bounded_register_read(self):
        points = (
            point("temperature", 10, datatype=DataType.FLOAT32, byte_order="ABCD"),
            point("status", 12),
        )
        plan = compile_read_plan(points)

        self.assertEqual(len(plan.requests), 1)
        request = plan.requests[0]
        self.assertEqual(request.function_code, 3)
        self.assertEqual((request.start_offset, request.quantity, request.end_offset), (10, 3, 12))
        self.assertEqual(
            [(trace.logical_point_id, trace.relative_offset) for trace in request.points],
            [("temperature", 0), ("status", 2)],
        )

    def test_gap_policy_is_explicit(self):
        points = (point("a", 0), point("b", 2))

        self.assertEqual(len(compile_read_plan(points).requests), 2)
        merged = compile_read_plan(points, max_gap=1)
        self.assertEqual(len(merged.requests), 1)
        self.assertEqual(merged.requests[0].quantity, 3)

    def test_route_unit_and_area_are_never_merged(self):
        points = (
            point("holding", 0),
            point("input", 0, area=RegisterArea.INPUT_REGISTER),
            point("unit", 0, unit=2),
            point("route", 0, route="r2"),
        )
        plan = compile_read_plan(points)
        self.assertEqual(len(plan.requests), 4)

    def test_all_requests_use_read_only_function_for_their_area(self):
        points = (
            point("coil", 0, area=RegisterArea.COIL, datatype=DataType.BOOL),
            point(
                "discrete", 0, area=RegisterArea.DISCRETE_INPUT, datatype=DataType.BOOL
            ),
            point("holding", 0, area=RegisterArea.HOLDING_REGISTER),
            point("input", 0, area=RegisterArea.INPUT_REGISTER),
        )
        plan = compile_read_plan(points)
        self.assertEqual(
            {(request.area.value, request.function_code) for request in plan.requests},
            {
                ("coil", 1),
                ("discrete-input", 2),
                ("holding-register", 3),
                ("input-register", 4),
            },
        )

    def test_register_limit_splits_a_wide_gap_policy(self):
        points = (point("first", 0), point("last-in-block", 124), point("next", 125))
        plan = compile_read_plan(points, max_gap=200)

        self.assertEqual(len(plan.requests), 2)
        self.assertEqual(plan.requests[0].quantity, 125)
        self.assertEqual(plan.requests[1].quantity, 1)

    def test_custom_limit_can_only_reduce_protocol_limit(self):
        plan = compile_read_plan(
            (point("a", 0), point("b", 10)),
            max_gap=20,
            max_quantities={"holding-register": 10},
        )
        self.assertEqual(len(plan.requests), 2)
        with self.assertRaises(ValueError):
            compile_read_plan((), max_quantities={"holding-register": 126})

    def test_upper_boundary_is_safe_for_single_value(self):
        plan = compile_read_plan(
            (point("last-coil", 65_535, area=RegisterArea.COIL, datatype=DataType.BOOL),)
        )
        self.assertEqual(plan.requests[0].end_offset, 65_535)

    def test_value_crossing_upper_boundary_is_held_and_not_planned(self):
        plan = compile_read_plan(
            (
                point(
                    "too-wide",
                    65_535,
                    datatype=DataType.UINT32,
                    byte_order="ABCD",
                ),
            )
        )
        self.assertEqual(plan.requests, ())
        self.assertIn("read-plan.range-out-of-bounds", {item.code for item in plan.findings})

    def test_unknown_datatype_can_make_a_raw_probe_with_explicit_span(self):
        plan = compile_read_plan(
            (point("probe", 100, datatype=DataType.UNKNOWN, word_span=2),)
        )

        self.assertEqual(len(plan.requests), 1)
        self.assertEqual(plan.requests[0].quantity, 2)
        self.assertTrue(plan.has_holds)
        self.assertIn("point.datatype-unresolved", {item.code for item in plan.findings})

    def test_write_function_is_excluded(self):
        plan = compile_read_plan((point("write", 1, function_code=16),))

        self.assertEqual(plan.requests, ())
        self.assertIn("read-plan.write-forbidden", {item.code for item in plan.findings})

    def test_broadcast_unit_is_excluded(self):
        plan = compile_read_plan((point("broadcast", 1, unit=0),))
        self.assertEqual(plan.requests, ())
        self.assertIn(
            "point.unit-id-broadcast-forbidden", {item.code for item in plan.findings}
        )

    def test_read_request_model_rejects_write_function_codes(self):
        with self.assertRaises(ValueError):
            ReadRequest(
                request_id="bad",
                route_id="r",
                unit_id=1,
                area=RegisterArea.HOLDING_REGISTER,
                function_code=16,
                start_offset=0,
                quantity=1,
                points=(),
            )

    def test_requests_and_ids_are_deterministic_for_input_order(self):
        points = (point("c", 20), point("a", 0), point("b", 1))
        forward = compile_read_plan(points).to_dict()
        reverse = compile_read_plan(reversed(points)).to_dict()

        self.assertEqual(forward, reverse)
        self.assertEqual(
            [request["request_id"] for request in forward["requests"]],
            ["read-0001", "read-0002"],
        )

    def test_trace_contains_complete_canonical_identity(self):
        trace = compile_read_plan((point("p", 7),)).requests[0].points[0]
        self.assertEqual(
            trace.canonical_identity,
            ("r1", 1, "holding-register", 7, "p"),
        )


if __name__ == "__main__":
    unittest.main()
