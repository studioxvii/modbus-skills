"""Structured validation for canonical Modbus points."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from .models import (
    CanonicalPoint,
    DataType,
    Finding,
    FindingSeverity,
    RegisterArea,
    coerce_points,
)


READ_FUNCTION_BY_AREA: dict[RegisterArea, int] = {
    RegisterArea.COIL: 1,
    RegisterArea.DISCRETE_INPUT: 2,
    RegisterArea.HOLDING_REGISTER: 3,
    RegisterArea.INPUT_REGISTER: 4,
}
READ_FUNCTION_CODES = frozenset(READ_FUNCTION_BY_AREA.values())
WRITE_FUNCTION_CODES = frozenset({5, 6, 15, 16, 22, 23})


def validate_points(
    points: Iterable[CanonicalPoint | Mapping[str, object]],
) -> tuple[Finding, ...]:
    """Validate points without changing or completing their values."""

    resolved_points = coerce_points(points)
    findings: list[Finding] = []
    for point in resolved_points:
        findings.extend(_validate_point(point))
    findings.extend(_validate_duplicates_and_overlaps(resolved_points))
    return tuple(findings)


def validate_function_codes(function_codes: Iterable[int]) -> tuple[Finding, ...]:
    """Reject any function code outside the read-only FC01-FC04 set."""

    findings: list[Finding] = []
    for function_code in function_codes:
        if isinstance(function_code, bool) or not isinstance(function_code, int):
            findings.append(
                Finding(
                    code="function-code.invalid",
                    severity=FindingSeverity.ERROR,
                    message="Function codes must be integers.",
                    field="function_code",
                    details={"value": function_code},
                )
            )
        elif function_code not in READ_FUNCTION_CODES:
            code = (
                "function-code.write-forbidden"
                if function_code in WRITE_FUNCTION_CODES
                else "function-code.unsupported"
            )
            findings.append(
                Finding(
                    code=code,
                    severity=FindingSeverity.ERROR,
                    message=(
                        f"FC{function_code:02d} is not permitted. "
                        "This runtime supports read-only FC01 through FC04."
                    ),
                    field="function_code",
                    details={"function_code": function_code},
                )
            )
    return tuple(findings)


def _validate_point(point: CanonicalPoint) -> list[Finding]:
    point_id = point.logical_point_id or "<unresolved>"
    findings: list[Finding] = []

    def add(
        code: str,
        severity: FindingSeverity,
        message: str,
        field: str,
        **details: object,
    ) -> None:
        findings.append(
            Finding(
                code=code,
                severity=severity,
                message=message,
                point_ids=(point_id,),
                field=field,
                details=details,
            )
        )

    if not point.logical_point_id:
        add(
            "point.logical-id-unresolved",
            FindingSeverity.HOLD,
            "Declare a stable logical point ID.",
            "logical_point_id",
        )
    if not point.route_id:
        add(
            "point.route-unresolved",
            FindingSeverity.HOLD,
            "Declare the route ID.",
            "route_id",
        )
    if point.unit_id is None:
        add(
            "point.unit-id-unresolved",
            FindingSeverity.HOLD,
            "Declare the Modbus unit ID.",
            "unit_id",
        )
    elif point.unit_id == 0:
        add(
            "point.unit-id-broadcast-forbidden",
            FindingSeverity.ERROR,
            "Unit ID 0 is not permitted because broadcast requests are disabled.",
            "unit_id",
            value=point.unit_id,
        )
    elif (
        isinstance(point.unit_id, bool)
        or not isinstance(point.unit_id, int)
        or not 1 <= point.unit_id <= 247
    ):
        add(
            "point.unit-id-invalid",
            FindingSeverity.ERROR,
            "Unit ID must be from 1 through 247.",
            "unit_id",
            value=point.unit_id,
        )
    if point.area is RegisterArea.UNKNOWN:
        add(
            "point.area-unresolved",
            FindingSeverity.HOLD,
            "Declare the Modbus area.",
            "area",
        )
    if point.protocol_offset is None:
        add(
            "point.address-unresolved",
            FindingSeverity.HOLD,
            "Resolve the zero-based protocol offset.",
            "protocol_offset",
        )
    elif (
        isinstance(point.protocol_offset, bool)
        or not isinstance(point.protocol_offset, int)
        or not 0 <= point.protocol_offset <= 65_535
    ):
        add(
            "point.address-out-of-range",
            FindingSeverity.ERROR,
            "Protocol offset must be from 0 through 65535.",
            "protocol_offset",
            value=point.protocol_offset,
        )
    if point.datatype is DataType.UNKNOWN:
        add(
            "point.datatype-unresolved",
            FindingSeverity.HOLD,
            "Declare the data type.",
            "datatype",
        )
    elif (
        point.area in {RegisterArea.COIL, RegisterArea.DISCRETE_INPUT}
        and point.datatype is not DataType.BOOL
    ):
        add(
            "point.datatype-area-mismatch",
            FindingSeverity.ERROR,
            f"{point.area.value} points require the bool data type.",
            "datatype",
            value=point.datatype.value,
        )

    expected_span = point.datatype.span
    span = point.effective_span
    span_is_valid = (
        isinstance(span, int) and not isinstance(span, bool) and span > 0
    )
    if point.word_span is not None and (
        isinstance(point.word_span, bool)
        or not isinstance(point.word_span, int)
        or point.word_span <= 0
    ):
        add(
            "point.span-invalid",
            FindingSeverity.ERROR,
            "Word span must be a positive integer.",
            "word_span",
            value=point.word_span,
        )
    elif (
        point.word_span is not None
        and expected_span is not None
        and point.word_span != expected_span
    ):
        add(
            "point.datatype-span-mismatch",
            FindingSeverity.ERROR,
            f"{point.datatype.value} requires {expected_span} register(s).",
            "word_span",
            value=point.word_span,
            expected=expected_span,
        )

    bit_area = point.area in {RegisterArea.COIL, RegisterArea.DISCRETE_INPUT}
    packed_bitfield = point.datatype is DataType.BOOL and (
        point.area in {RegisterArea.HOLDING_REGISTER, RegisterArea.INPUT_REGISTER}
        or (point.word_span is not None and point.word_span > 1)
    )
    if bit_area and point.byte_order and len(point.byte_order) >= 4:
        add(
            "point.byte-order-inapplicable",
            FindingSeverity.ERROR,
            "Coil and discrete points do not use multi-register byte layouts.",
            "byte_order",
            value=point.byte_order,
        )
    if packed_bitfield and not point.bit_order:
        add(
            "point.bit-order-unresolved",
            FindingSeverity.HOLD,
            "Declare the packed-bit or coil bit numbering convention.",
            "bit_order",
        )

    if (
        span_is_valid
        and span > 1
        and point.datatype is not DataType.STRING
        and not bit_area
        and not point.byte_order
    ):
        add(
            "point.byte-order-unresolved",
            FindingSeverity.HOLD,
            "Confirm an explicit byte layout for this multi-register value.",
            "byte_order",
        )
    elif point.byte_order and span_is_valid:
        expected_labels = "ABCDEFGH"[: span * 2]
        if (
            len(point.byte_order) != len(expected_labels)
            or sorted(point.byte_order) != sorted(expected_labels)
        ):
            add(
                "point.byte-order-invalid",
                FindingSeverity.ERROR,
                f"Byte layout must be an explicit permutation of {expected_labels}.",
                "byte_order",
                value=point.byte_order,
            )
    if point.byte_order and (
        point.byte_order_confirmed is False
        or point.byte_order_status
        in {"pending", "candidate", "assumed", "unconfirmed", "unresolved"}
    ):
        add(
            "point.byte-order-unconfirmed",
            FindingSeverity.HOLD,
            "The byte layout is evidence only until a human confirms it.",
            "byte_order_confirmed",
            value=point.byte_order,
            status=point.byte_order_status,
        )
    if point.byte_order_status not in {
        None,
        "confirmed",
        "approved",
        "reviewed",
        "pending",
        "candidate",
        "assumed",
        "unconfirmed",
        "unresolved",
        "not-applicable",
    }:
        add(
            "point.byte-order-status-invalid",
            FindingSeverity.ERROR,
            "Byte-order status is not recognized.",
            "byte_order_status",
            value=point.byte_order_status,
        )

    if point.function_code is not None:
        code_findings = validate_function_codes((point.function_code,))
        for finding in code_findings:
            findings.append(
                Finding(
                    code=finding.code,
                    severity=finding.severity,
                    message=finding.message,
                    point_ids=(point_id,),
                    field=finding.field,
                    details=finding.details,
                )
            )
        expected_function = READ_FUNCTION_BY_AREA.get(point.area)
        if (
            point.function_code in READ_FUNCTION_CODES
            and expected_function is not None
            and point.function_code != expected_function
        ):
            add(
                "function-code.area-mismatch",
                FindingSeverity.ERROR,
                f"{point.area.value} requires FC{expected_function:02d}.",
                "function_code",
                value=point.function_code,
                expected=expected_function,
            )

    if (
        point.protocol_offset is not None
        and isinstance(point.protocol_offset, int)
        and not isinstance(point.protocol_offset, bool)
        and span_is_valid
        and point.protocol_offset + span - 1 > 65_535
    ):
        add(
            "point.range-out-of-bounds",
            FindingSeverity.ERROR,
            "The point range extends beyond protocol offset 65535.",
            "word_span",
            end_offset=point.protocol_offset + span - 1,
        )

    return findings


def _validate_duplicates_and_overlaps(
    points: tuple[CanonicalPoint, ...],
) -> list[Finding]:
    findings: list[Finding] = []

    identities: dict[tuple[str, int, str, int, str], list[CanonicalPoint]] = defaultdict(list)
    physical_groups: dict[tuple[str, int, str], list[CanonicalPoint]] = defaultdict(list)
    for point in points:
        if point.canonical_identity is not None:
            identities[point.canonical_identity].append(point)
        if (
            point.route_id
            and point.unit_id is not None
            and point.area is not RegisterArea.UNKNOWN
            and point.protocol_offset is not None
            and isinstance(point.effective_span, int)
            and not isinstance(point.effective_span, bool)
            and point.effective_span > 0
        ):
            physical_groups[(point.route_id, point.unit_id, point.area.value)].append(point)

    for identity, matches in sorted(identities.items()):
        if len(matches) > 1:
            point_ids = tuple(point.logical_point_id for point in matches)
            findings.append(
                Finding(
                    code="point.duplicate-identity",
                    severity=FindingSeverity.ERROR,
                    message="Multiple records have the same canonical identity.",
                    point_ids=point_ids,
                    details={"canonical_identity": list(identity)},
                )
            )

    for group_key, group in sorted(physical_groups.items()):
        ordered = sorted(
            group,
            key=lambda point: (point.protocol_offset or 0, point.logical_point_id),
        )
        for index, left in enumerate(ordered):
            left_start = left.protocol_offset
            left_span = left.effective_span
            assert left_start is not None and left_span is not None
            left_end = left_start + left_span - 1
            for right in ordered[index + 1 :]:
                right_start = right.protocol_offset
                right_span = right.effective_span
                assert right_start is not None and right_span is not None
                if right_start > left_end:
                    break
                right_end = right_start + right_span - 1
                if left.canonical_identity == right.canonical_identity:
                    continue
                findings.append(
                    Finding(
                        code="point.overlapping-range",
                        severity=FindingSeverity.ERROR,
                        message="Point ranges overlap in the same route, unit, and area.",
                        point_ids=(left.logical_point_id, right.logical_point_id),
                        details={
                            "route_id": group_key[0],
                            "unit_id": group_key[1],
                            "area": group_key[2],
                            "left_range": [left_start, left_end],
                            "right_range": [right_start, right_end],
                        },
                    )
                )
    return findings
