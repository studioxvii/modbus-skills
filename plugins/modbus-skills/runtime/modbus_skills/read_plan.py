"""Read-only Modbus request planning with point traceability."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from .models import (
    CanonicalPoint,
    Finding,
    FindingSeverity,
    ReadPlan,
    ReadPointTrace,
    ReadRequest,
    RegisterArea,
    coerce_points,
)
from .validation import READ_FUNCTION_BY_AREA, READ_FUNCTION_CODES, validate_points


DEFAULT_MAX_QUANTITY: dict[RegisterArea, int] = {
    RegisterArea.COIL: 2_000,
    RegisterArea.DISCRETE_INPUT: 2_000,
    RegisterArea.HOLDING_REGISTER: 125,
    RegisterArea.INPUT_REGISTER: 125,
}


def compile_read_plan(
    points: Iterable[CanonicalPoint | Mapping[str, object]],
    *,
    max_gap: int = 0,
    max_quantities: Mapping[RegisterArea | str, int] | None = None,
) -> ReadPlan:
    """Group resolved points into safe FC01-FC04 requests.

    A gap is the number of unused addresses permitted between two point ranges.
    The planner can still produce raw probe reads when data type or byte order is
    unresolved, provided an explicit positive ``word_span`` is available.
    """

    if isinstance(max_gap, bool) or not isinstance(max_gap, int) or max_gap < 0:
        raise ValueError("max_gap must be a non-negative integer")
    limits = _resolve_limits(max_quantities)
    resolved_points = coerce_points(points)
    findings = list(validate_points(resolved_points))

    grouped: dict[tuple[str, int, RegisterArea], list[CanonicalPoint]] = defaultdict(list)
    for point in resolved_points:
        reason = _unplannable_reason(point, limits)
        if reason is not None:
            findings.append(reason)
            continue
        assert point.unit_id is not None
        grouped[(point.route_id, point.unit_id, point.area)].append(point)

    pending_requests: list[
        tuple[str, int, RegisterArea, int, int, tuple[CanonicalPoint, ...]]
    ] = []
    for (route_id, unit_id, area), group in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2].value)
    ):
        ordered = sorted(
            group,
            key=lambda point: (point.protocol_offset or 0, point.logical_point_id),
        )
        limit = limits[area]
        current: list[CanonicalPoint] = []
        block_start = 0
        block_end = -1

        for point in ordered:
            assert point.protocol_offset is not None and point.effective_span is not None
            point_start = point.protocol_offset
            point_end = point_start + point.effective_span - 1
            if not current:
                current = [point]
                block_start = point_start
                block_end = point_end
                continue

            merged_end = max(block_end, point_end)
            merged_quantity = merged_end - block_start + 1
            gap = max(0, point_start - block_end - 1)
            if gap <= max_gap and merged_quantity <= limit:
                current.append(point)
                block_end = merged_end
            else:
                pending_requests.append(
                    (
                        route_id,
                        unit_id,
                        area,
                        block_start,
                        block_end,
                        tuple(current),
                    )
                )
                current = [point]
                block_start = point_start
                block_end = point_end

        if current:
            pending_requests.append(
                (route_id, unit_id, area, block_start, block_end, tuple(current))
            )

    requests: list[ReadRequest] = []
    for index, (route_id, unit_id, area, start, end, request_points) in enumerate(
        pending_requests, start=1
    ):
        traces: list[ReadPointTrace] = []
        for point in request_points:
            identity = point.canonical_identity
            assert (
                identity is not None
                and point.protocol_offset is not None
                and point.effective_span is not None
            )
            traces.append(
                ReadPointTrace(
                    logical_point_id=point.logical_point_id,
                    protocol_offset=point.protocol_offset,
                    span=point.effective_span,
                    relative_offset=point.protocol_offset - start,
                    canonical_identity=identity,
                )
            )
        requests.append(
            ReadRequest(
                request_id=f"read-{index:04d}",
                route_id=route_id,
                unit_id=unit_id,
                area=area,
                function_code=READ_FUNCTION_BY_AREA[area],
                start_offset=start,
                quantity=end - start + 1,
                points=tuple(traces),
            )
        )

    return ReadPlan(requests=tuple(requests), findings=_deduplicate_findings(findings))


def _resolve_limits(
    values: Mapping[RegisterArea | str, int] | None,
) -> dict[RegisterArea, int]:
    limits = dict(DEFAULT_MAX_QUANTITY)
    if values is None:
        return limits
    for raw_area, limit in values.items():
        area = RegisterArea.coerce(raw_area)
        if area not in DEFAULT_MAX_QUANTITY:
            raise ValueError(f"cannot set a read limit for area {raw_area!r}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("read limits must be positive integers")
        if limit > DEFAULT_MAX_QUANTITY[area]:
            raise ValueError(
                f"read limit for {area.value} cannot exceed "
                f"{DEFAULT_MAX_QUANTITY[area]}"
            )
        limits[area] = limit
    return limits


def _unplannable_reason(
    point: CanonicalPoint,
    limits: Mapping[RegisterArea, int],
) -> Finding | None:
    point_id = point.logical_point_id or "<unresolved>"
    if point.access == "write-only":
        return Finding(
            code="read-plan.write-only-point",
            severity=FindingSeverity.HOLD,
            message="A write-only point cannot enter a read plan.",
            point_ids=(point_id,),
            field="access",
        )
    if (
        not point.logical_point_id
        or not point.route_id
        or point.unit_id is None
        or not 1 <= point.unit_id <= 247
        or point.area is RegisterArea.UNKNOWN
        or point.protocol_offset is None
        or not 0 <= point.protocol_offset <= 65_535
    ):
        return Finding(
            code="read-plan.point-held",
            severity=FindingSeverity.HOLD,
            message="The point identity or address is unresolved or invalid.",
            point_ids=(point_id,),
        )
    span = point.effective_span
    if (
        span is None
        or isinstance(span, bool)
        or not isinstance(span, int)
        or span <= 0
    ):
        return Finding(
            code="read-plan.span-held",
            severity=FindingSeverity.HOLD,
            message="Declare a positive word span before planning this read.",
            point_ids=(point_id,),
            field="word_span",
        )
    if span > limits[point.area]:
        return Finding(
            code="read-plan.point-too-wide",
            severity=FindingSeverity.HOLD,
            message="The point span exceeds the safe request quantity.",
            point_ids=(point_id,),
            details={"span": span, "limit": limits[point.area]},
        )
    if point.protocol_offset + span - 1 > 65_535:
        return Finding(
            code="read-plan.range-out-of-bounds",
            severity=FindingSeverity.HOLD,
            message="The point range extends beyond protocol offset 65535.",
            point_ids=(point_id,),
        )
    if point.function_code is not None:
        expected = READ_FUNCTION_BY_AREA[point.area]
        if point.function_code not in READ_FUNCTION_CODES:
            return Finding(
                code="read-plan.write-forbidden",
                severity=FindingSeverity.ERROR,
                message="Only read-only FC01 through FC04 can enter a read plan.",
                point_ids=(point_id,),
                details={"function_code": point.function_code},
            )
        if point.function_code != expected:
            return Finding(
                code="read-plan.function-area-mismatch",
                severity=FindingSeverity.ERROR,
                message=f"{point.area.value} requires FC{expected:02d}.",
                point_ids=(point_id,),
                details={"function_code": point.function_code},
            )
    return None


def _deduplicate_findings(findings: list[Finding]) -> tuple[Finding, ...]:
    seen: set[tuple[object, ...]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (
            finding.code,
            finding.severity.value,
            finding.message,
            finding.point_ids,
            finding.field,
            repr(sorted(finding.details.items())),
        )
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return tuple(result)
