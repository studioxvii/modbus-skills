"""Read-only Modbus request planning with point traceability."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    CanonicalPoint,
    Finding,
    FindingSeverity,
    ReadPlan,
    ReadBridgeTrace,
    ReadPointTrace,
    ReadRequest,
    RegisterArea,
    coerce_points,
)
from .unit_id_scope import unit_id_error
from .validation import READ_FUNCTION_BY_AREA, READ_FUNCTION_CODES, validate_points


DEFAULT_MAX_QUANTITY: dict[RegisterArea, int] = {
    RegisterArea.COIL: 2_000,
    RegisterArea.DISCRETE_INPUT: 2_000,
    RegisterArea.HOLDING_REGISTER: 125,
    RegisterArea.INPUT_REGISTER: 125,
}


@dataclass(frozen=True, slots=True)
class ReadableIsland:
    island_id: str
    route_id: str
    unit_id: int
    area: RegisterArea
    function_code: int
    start_offset: int
    end_offset: int
    reason: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "island_id": self.island_id,
            "route_id": self.route_id,
            "unit_id": self.unit_id,
            "area": self.area.value,
            "function_code": self.function_code,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class UnsafeInterval:
    route_id: str
    unit_id: int
    area: RegisterArea
    start_offset: int
    end_offset: int
    reason: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "unit_id": self.unit_id,
            "area": self.area.value,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


def compile_read_plan(
    points: Iterable[CanonicalPoint | Mapping[str, object]],
    *,
    max_gap: int = 0,
    max_quantities: Mapping[RegisterArea | str, int] | None = None,
    readable_islands: Sequence[ReadableIsland | Mapping[str, object]] = (),
    unsafe_intervals: Sequence[UnsafeInterval | Mapping[str, object]] = (),
) -> ReadPlan:
    """Group resolved points into safe FC01-FC04 requests.

    Sparse gaps merge only inside one explicitly evidenced readable island.
    ``max_gap`` remains accepted for compatibility but grants no read authority.
    The planner can still produce raw probe reads when data type or byte order is
    unresolved, provided an explicit positive ``word_span`` is available.
    """

    if isinstance(max_gap, bool) or not isinstance(max_gap, int) or max_gap < 0:
        raise ValueError("max_gap must be a non-negative integer")
    limits = _resolve_limits(max_quantities)
    islands = normalize_readable_islands(readable_islands)
    unsafe = normalize_unsafe_intervals(unsafe_intervals)
    resolved_points = coerce_points(points)
    findings = list(validate_points(resolved_points))
    if max_gap:
        findings.append(
            Finding(
                code="read-plan.max-gap-no-authority",
                severity=FindingSeverity.INFO,
                message="max_gap does not authorize sparse reads; provide an evidenced readable island.",
                details={"max_gap": max_gap},
            )
        )

    grouped: dict[
        tuple[str, int, RegisterArea, int, str],
        list[tuple[CanonicalPoint, ReadableIsland | None]],
    ] = defaultdict(list)
    for point in resolved_points:
        reason = _unplannable_reason(point, limits)
        if reason is not None:
            findings.append(reason)
            continue
        assert point.unit_id is not None
        if _point_overlaps_unsafe(point, unsafe):
            findings.append(
                Finding(
                    code="read-plan.unsafe-point",
                    severity=FindingSeverity.HOLD,
                    message="The selected point overlaps an explicitly unsafe interval.",
                    point_ids=(point.logical_point_id,),
                )
            )
            continue
        island = _point_island(point, islands)
        function_code = READ_FUNCTION_BY_AREA[point.area]
        grouped[
            (
                point.route_id,
                point.unit_id,
                point.area,
                function_code,
                island.island_id if island is not None else "",
            )
        ].append((point, island))

    pending_requests: list[
        tuple[
            str,
            int,
            RegisterArea,
            int,
            int,
            tuple[CanonicalPoint, ...],
            ReadableIsland | None,
            tuple[ReadBridgeTrace, ...],
        ]
    ] = []
    for (route_id, unit_id, area, _function_code, _island_id), group in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].value, item[0][3], item[0][4]),
    ):
        ordered = sorted(
            group,
            key=lambda item: (item[0].protocol_offset or 0, item[0].logical_point_id),
        )
        limit = limits[area]
        current: list[CanonicalPoint] = []
        current_island: ReadableIsland | None = None
        bridges: list[ReadBridgeTrace] = []
        block_start = 0
        block_end = -1

        for point, island in ordered:
            assert point.protocol_offset is not None and point.effective_span is not None
            point_start = point.protocol_offset
            point_end = point_start + point.effective_span - 1
            if not current:
                current = [point]
                current_island = island
                block_start = point_start
                block_end = point_end
                continue

            merged_end = max(block_end, point_end)
            merged_quantity = merged_end - block_start + 1
            gap = max(0, point_start - block_end - 1)
            can_bridge = gap == 0 or (
                current_island is not None
                and island == current_island
                and _gap_is_safe(
                    route_id,
                    unit_id,
                    area,
                    block_end + 1,
                    point_start - 1,
                    unsafe,
                )
            )
            if can_bridge and merged_quantity <= limit:
                if gap:
                    assert current_island is not None
                    bridges.append(
                        ReadBridgeTrace(
                            start_offset=block_end + 1,
                            end_offset=point_start - 1,
                            readable_island_id=current_island.island_id,
                            reason=current_island.reason,
                            evidence_refs=current_island.evidence_refs,
                        )
                    )
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
                        current_island,
                        tuple(bridges),
                    )
                )
                current = [point]
                current_island = island
                bridges = []
                block_start = point_start
                block_end = point_end

        if current:
            pending_requests.append(
                (
                    route_id,
                    unit_id,
                    area,
                    block_start,
                    block_end,
                    tuple(current),
                    current_island,
                    tuple(bridges),
                )
            )

    requests: list[ReadRequest] = []
    for index, (
        route_id,
        unit_id,
        area,
        start,
        end,
        request_points,
        island,
        bridges,
    ) in enumerate(
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
                readable_island_id=island.island_id if island is not None else None,
                bridged_ranges=bridges,
            )
        )

    return ReadPlan(requests=tuple(requests), findings=_deduplicate_findings(findings))


def normalize_readable_islands(
    values: Sequence[ReadableIsland | Mapping[str, object]],
) -> tuple[ReadableIsland, ...]:
    result: list[ReadableIsland] = []
    for index, raw in enumerate(_sequence(values, "readable_islands")):
        if isinstance(raw, ReadableIsland):
            island = raw
        else:
            island = ReadableIsland(
                island_id=_required_text(raw.get("island_id"), f"readable_islands[{index}].island_id"),
                route_id=_required_text(raw.get("route_id"), f"readable_islands[{index}].route_id"),
                unit_id=_unit_id(raw.get("unit_id"), f"readable_islands[{index}].unit_id"),
                area=_known_area(raw.get("area"), f"readable_islands[{index}].area"),
                function_code=_positive_int(raw.get("function_code"), f"readable_islands[{index}].function_code"),
                start_offset=_offset(raw.get("start_offset"), f"readable_islands[{index}].start_offset"),
                end_offset=_offset(raw.get("end_offset"), f"readable_islands[{index}].end_offset"),
                reason=_required_text(raw.get("reason"), f"readable_islands[{index}].reason"),
                evidence_refs=tuple(_text_array(raw.get("evidence_refs"), f"readable_islands[{index}].evidence_refs")),
            )
        if island.end_offset < island.start_offset:
            raise ValueError("readable island end_offset cannot precede start_offset")
        if island.function_code != READ_FUNCTION_BY_AREA[island.area]:
            raise ValueError("readable island function_code does not match its area")
        if not island.evidence_refs:
            raise ValueError("readable island needs at least one evidence reference")
        result.append(island)
    result.sort(key=_interval_sort_key)
    _reject_overlapping_islands(result)
    return tuple(result)


def normalize_unsafe_intervals(
    values: Sequence[UnsafeInterval | Mapping[str, object]],
) -> tuple[UnsafeInterval, ...]:
    result: list[UnsafeInterval] = []
    for index, raw in enumerate(_sequence(values, "unsafe_intervals")):
        if isinstance(raw, UnsafeInterval):
            interval = raw
        else:
            interval = UnsafeInterval(
                route_id=_required_text(raw.get("route_id"), f"unsafe_intervals[{index}].route_id"),
                unit_id=_unit_id(raw.get("unit_id"), f"unsafe_intervals[{index}].unit_id"),
                area=_known_area(raw.get("area"), f"unsafe_intervals[{index}].area"),
                start_offset=_offset(raw.get("start_offset"), f"unsafe_intervals[{index}].start_offset"),
                end_offset=_offset(raw.get("end_offset"), f"unsafe_intervals[{index}].end_offset"),
                reason=_required_text(raw.get("reason"), f"unsafe_intervals[{index}].reason"),
                evidence_refs=tuple(_text_array(raw.get("evidence_refs"), f"unsafe_intervals[{index}].evidence_refs")),
            )
        if interval.end_offset < interval.start_offset:
            raise ValueError("unsafe interval end_offset cannot precede start_offset")
        if not interval.evidence_refs:
            raise ValueError("unsafe interval needs at least one evidence reference")
        result.append(interval)
    result.sort(key=_interval_sort_key)
    return tuple(result)


def _point_island(
    point: CanonicalPoint, islands: Sequence[ReadableIsland]
) -> ReadableIsland | None:
    assert point.protocol_offset is not None and point.effective_span is not None
    point_end = point.protocol_offset + point.effective_span - 1
    expected_function = READ_FUNCTION_BY_AREA[point.area]
    for island in islands:
        if (
            island.route_id == point.route_id
            and island.unit_id == point.unit_id
            and island.area is point.area
            and island.function_code == expected_function
            and island.start_offset <= point.protocol_offset
            and point_end <= island.end_offset
        ):
            return island
    return None


def _point_overlaps_unsafe(
    point: CanonicalPoint, intervals: Sequence[UnsafeInterval]
) -> bool:
    assert point.protocol_offset is not None and point.effective_span is not None
    return not _gap_is_safe(
        point.route_id,
        int(point.unit_id),
        point.area,
        point.protocol_offset,
        point.protocol_offset + point.effective_span - 1,
        intervals,
    )


def _gap_is_safe(
    route_id: str,
    unit_id: int,
    area: RegisterArea,
    start_offset: int,
    end_offset: int,
    intervals: Sequence[UnsafeInterval],
) -> bool:
    if end_offset < start_offset:
        return True
    return not any(
        interval.route_id == route_id
        and interval.unit_id == unit_id
        and interval.area is area
        and start_offset <= interval.end_offset
        and interval.start_offset <= end_offset
        for interval in intervals
    )


def _reject_overlapping_islands(islands: Sequence[ReadableIsland]) -> None:
    prior: dict[tuple[str, int, RegisterArea, int], ReadableIsland] = {}
    for island in islands:
        scope = (island.route_id, island.unit_id, island.area, island.function_code)
        previous = prior.get(scope)
        if previous is not None and island.start_offset <= previous.end_offset:
            raise ValueError(
                f"readable islands {previous.island_id!r} and {island.island_id!r} overlap"
            )
        prior[scope] = island


def _interval_sort_key(value: ReadableIsland | UnsafeInterval) -> tuple[str, int, str, int, int, str]:
    return (
        value.route_id,
        value.unit_id,
        value.area.value,
        value.start_offset,
        value.end_offset,
        value.island_id if isinstance(value, ReadableIsland) else value.reason,
    )


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be an array")
    return list(value)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _text_array(value: Any, field: str) -> list[str]:
    return sorted({_required_text(item, field) for item in _sequence(value, field)})


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _unit_id(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 247:
        raise ValueError(unit_id_error(field))
    return value


def _offset(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65_535:
        raise ValueError(f"{field} must be an integer from 0 through 65535")
    return value


def _known_area(value: Any, field: str) -> RegisterArea:
    area = RegisterArea.coerce(value)
    if area not in DEFAULT_MAX_QUANTITY:
        raise ValueError(f"{field} must name a readable Modbus area")
    return area


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
