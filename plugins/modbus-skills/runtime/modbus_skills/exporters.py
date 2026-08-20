"""Shared contracts and safety checks for deterministic Modbus exporters.

The exporters in this package are intentionally read-only.  They accept a
reviewed canonical map and a compiled read plan.  They never discover devices,
change addresses, or create Modbus write requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable, Mapping, Sequence

from .artifacts import artifact_envelope, stable_input_hash
from .read_plan import (
    ReadableIsland,
    UnsafeInterval,
    normalize_readable_islands,
    normalize_unsafe_intervals,
)
from .unit_id_scope import unit_id_error


TARGET_MANIFEST_SCHEMA_VERSION = "modbus-target-manifest/v1"
TARGET_RESULT_SCHEMA_VERSION = "modbus-target-result/v1"
# Backward-compatible import name. New artifacts use the explicit manifest name.
EXPORT_CONTRACT_VERSION = TARGET_MANIFEST_SCHEMA_VERSION
ALLOWED_MODES = frozenset({"probe", "final"})
ALLOWED_AREAS = frozenset(
    {"coil", "discrete-input", "input-register", "holding-register"}
)
AREA_FUNCTION_CODES = {
    "coil": 1,
    "discrete-input": 2,
    "holding-register": 3,
    "input-register": 4,
}
FUNCTION_LIMITS = {1: 2000, 2: 2000, 3: 125, 4: 125}

_UNRESOLVED = frozenset(
    {"", "unknown", "unresolved", "candidate", "assumed", "pending", "none", "null"}
)
_AREA_ALIASES = {
    "coil": "coil",
    "coils": "coil",
    "discrete": "discrete-input",
    "discrete-input": "discrete-input",
    "discrete-inputs": "discrete-input",
    "discrete_input": "discrete-input",
    "discrete_inputs": "discrete-input",
    "input": "input-register",
    "input-register": "input-register",
    "input-registers": "input-register",
    "input_register": "input-register",
    "input_registers": "input-register",
    "holding": "holding-register",
    "holding-register": "holding-register",
    "holding-registers": "holding-register",
    "holding_register": "holding-register",
    "holding_registers": "holding-register",
}
_DATATYPE_WORD_COUNTS = {
    "bool": 1,
    "boolean": 1,
    "int16": 1,
    "uint16": 1,
    "float16": 1,
    "int32": 2,
    "uint32": 2,
    "float32": 2,
    "int64": 4,
    "uint64": 4,
    "float64": 4,
    "double": 4,
}


class ExporterInputError(ValueError):
    """Raised when the caller gives an invalid exporter request."""


@dataclass(frozen=True, order=True)
class Finding:
    """A deterministic exporter finding.

    ``error`` findings stop generation.  ``warning`` findings stay in the
    generated target manifest.
    """

    severity: str
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        result = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            result["path"] = self.path
        return result


@dataclass(frozen=True)
class Artifact:
    """One generated, relative-path artifact."""

    path: str
    media_type: str
    content: bytes
    role: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.path
            or str(path) != self.path
        ):
            raise ExporterInputError(f"Artifact path must be a safe POSIX relative path: {self.path!r}")

    @classmethod
    def text(
        cls, path: str, media_type: str, content: str, role: str
    ) -> "Artifact":
        return cls(path, media_type, content.encode("utf-8"), role)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def as_text(self) -> str:
        return self.content.decode("utf-8")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "media_type": self.media_type,
            "role": self.role,
            "sha256": self.sha256,
            "bytes": len(self.content),
        }


@dataclass(frozen=True)
class ExportResult:
    """The result from one target adapter."""

    target: str
    status: str
    mode: str
    map_hash: str
    read_plan_hash: str
    adapter_version: str
    profile: str | None = None
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    artifacts: tuple[Artifact, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in {"generated", "held", "unsupported", "verification-failed"}:
            raise ExporterInputError(f"Invalid export status: {self.status!r}")
        normalize_mode(self.mode)
        artifact_paths = [artifact.path for artifact in self.artifacts]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ExporterInputError(f"Duplicate artifact path in {self.target} result")
        if self.status == "generated" and any(
            finding.severity == "error" for finding in self.findings
        ):
            raise ExporterInputError("A generated result cannot contain an error finding")

    def to_manifest(self) -> dict[str, Any]:
        finding_values = [finding.to_dict() for finding in sorted(self.findings)]
        result: dict[str, Any] = {
            "target": self.target,
            "status": self.status,
            "mode": self.mode,
            "adapter_version": self.adapter_version,
            "map_hash": self.map_hash,
            "read_plan_hash": self.read_plan_hash,
            "artifacts": [artifact.to_manifest() for artifact in self.artifacts],
            "verification": "not-run",
        }
        if self.profile is not None:
            result["profile"] = self.profile
        return artifact_envelope(
            result,
            schema_version=TARGET_RESULT_SCHEMA_VERSION,
            artifact_type="modbus-target-result",
            input_hashes={
                "canonical_map": self.map_hash,
                "read_plan": self.read_plan_hash,
            },
            assumptions=[],
            findings=finding_values,
            holds=_blocking_finding_values(finding_values),
        )


def normalize_mode(mode: str) -> str:
    normalized = str(mode).strip().lower()
    if normalized not in ALLOWED_MODES:
        raise ExporterInputError(
            f"mode must be one of {sorted(ALLOWED_MODES)}; got {mode!r}"
        )
    return normalized


def stable_json(value: Any, *, pretty: bool = True) -> str:
    """Return deterministic UTF-8 JSON text with a final newline."""

    if pretty:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        ) + "\n"
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    payload = stable_json(value, pretty=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_map_hash(canonical_map: Mapping[str, Any]) -> str:
    return stable_hash(canonical_map)


def read_plan_hash(read_plan: Mapping[str, Any]) -> str:
    return stable_hash(read_plan)


def normalize_area(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "-")
    return _AREA_ALIASES.get(text)


def normalize_function_code(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.startswith("fc"):
        text = text[2:]
    try:
        return int(text, 0)
    except ValueError:
        try:
            return int(text, 10)
        except ValueError:
            return None


def is_resolved(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _UNRESOLVED
    return True


def points_from_map(canonical_map: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = canonical_map.get("points", canonical_map.get("registers", ()))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(point for point in raw if isinstance(point, Mapping))


def blocks_from_plan(read_plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = read_plan.get(
        "requests", read_plan.get("blocks", read_plan.get("read_blocks", ()))
    )
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(block for block in raw if isinstance(block, Mapping))


def point_id(point: Mapping[str, Any], index: int | None = None) -> str | None:
    value = point.get("point_id", point.get("logical_point_id", point.get("id")))
    if is_resolved(value):
        return str(value)
    return None


def point_route_id(point: Mapping[str, Any]) -> str:
    value = point.get("route_id", point.get("route"))
    return str(value) if is_resolved(value) else "default"


def point_unit_id(point: Mapping[str, Any]) -> int | None:
    value = point.get("unit_id", point.get("unitId", point.get("slave_id")))
    return _nonnegative_int(value, minimum=1, maximum=247)


def point_area(point: Mapping[str, Any]) -> str | None:
    address = point.get("address")
    nested = address if isinstance(address, Mapping) else {}
    return normalize_area(point.get("area", nested.get("area")))


def point_protocol_offset(point: Mapping[str, Any]) -> int | None:
    value = point.get("protocol_offset", point.get("pdu_offset"))
    if value is None:
        address = point.get("address")
        if isinstance(address, Mapping):
            value = address.get("protocol_offset", address.get("pdu_offset"))
    return _nonnegative_int(value, minimum=0, maximum=65535)


def point_datatype(point: Mapping[str, Any]) -> str | None:
    value = point.get("datatype", point.get("data_type"))
    return str(value).strip().lower() if is_resolved(value) else None


def point_byte_order(point: Mapping[str, Any]) -> str | None:
    value = point.get("byte_order", point.get("byte_layout"))
    if isinstance(value, Mapping):
        value = value.get("layout", value.get("value"))
    if not is_resolved(value):
        return None
    return str(value).strip().upper()


def point_word_count(point: Mapping[str, Any]) -> int | None:
    value = point.get(
        "word_span", point.get("word_count", point.get("register_width"))
    )
    if value is not None:
        return _nonnegative_int(value, minimum=1, maximum=125)
    datatype = point_datatype(point)
    if datatype:
        if datatype.startswith("string"):
            suffix = datatype.removeprefix("string")
            if suffix.isdigit() and int(suffix) > 0:
                return (int(suffix) + 1) // 2
        return _DATATYPE_WORD_COUNTS.get(datatype)
    return None


def point_name(point: Mapping[str, Any], fallback: str) -> str:
    value = point.get("name", point.get("label"))
    return str(value).strip() if is_resolved(value) else fallback


def block_id(block: Mapping[str, Any], index: int) -> str:
    value = block.get("request_id", block.get("block_id", block.get("id")))
    return str(value) if is_resolved(value) else f"block-{index + 1:03d}"


def block_route_id(block: Mapping[str, Any]) -> str:
    value = block.get("route_id", block.get("route"))
    return str(value) if is_resolved(value) else "default"


def block_unit_id(block: Mapping[str, Any]) -> int | None:
    value = block.get("unit_id", block.get("unitId", block.get("slave_id")))
    return _nonnegative_int(value, minimum=1, maximum=247)


def block_area(block: Mapping[str, Any]) -> str | None:
    return normalize_area(block.get("area", block.get("object_type")))


def block_start(block: Mapping[str, Any]) -> int | None:
    value = block.get(
        "start_offset",
        block.get("start_address", block.get("protocol_offset", block.get("start"))),
    )
    return _nonnegative_int(value, minimum=0, maximum=65535)


def block_quantity(block: Mapping[str, Any]) -> int | None:
    value = block.get("quantity", block.get("count", block.get("size")))
    return _nonnegative_int(value, minimum=1, maximum=2000)


def block_function_code(block: Mapping[str, Any]) -> int | None:
    value = block.get("function_code", block.get("function"))
    if value is None:
        area = block_area(block)
        return AREA_FUNCTION_CODES.get(area) if area else None
    return normalize_function_code(value)


def block_point_ids(block: Mapping[str, Any]) -> tuple[str, ...]:
    raw = block.get("point_ids", block.get("points", ()))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    values: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            value = point_id(item)
        else:
            value = str(item) if is_resolved(item) else None
        if value:
            values.append(value)
    return tuple(values)


def block_interval_ms(block: Mapping[str, Any], default: int = 1000) -> int:
    value = block.get("poll_interval_ms", block.get("interval_ms", default))
    parsed = _nonnegative_int(value, minimum=1, maximum=3_600_000)
    return parsed if parsed is not None else default


def points_for_block(
    canonical_map: Mapping[str, Any], block: Mapping[str, Any], index: int
) -> tuple[Mapping[str, Any], ...]:
    points = points_from_map(canonical_map)
    requested_ids = set(block_point_ids(block))
    if requested_ids:
        return tuple(
            point
            for point_index, point in enumerate(points)
            if point_id(point, point_index) in requested_ids
        )

    route = block_route_id(block)
    unit = block_unit_id(block)
    area = block_area(block)
    start = block_start(block)
    quantity = block_quantity(block)
    if unit is None or area is None or start is None or quantity is None:
        return ()
    stop = start + quantity
    matches = []
    for point in points:
        point_start = point_protocol_offset(point)
        width = point_word_count(point) or 1
        if (
            point_route_id(point) == route
            and point_unit_id(point) == unit
            and point_area(point) == area
            and point_start is not None
            and start <= point_start
            and point_start + width <= stop
        ):
            matches.append(point)
    return tuple(matches)


def _normalized_access(point: Mapping[str, Any]) -> str | None:
    value = point.get("access")
    if not is_resolved(value):
        return None
    return str(value).strip().lower().replace("_", "-").replace(" ", "-")


def _validate_map_bound_plan(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    max_quantities: Mapping[str, int],
    readable_islands: Sequence[ReadableIsland],
    unsafe_intervals: Sequence[UnsafeInterval],
) -> list[Finding]:
    """Require each map-bound request to have one exact canonical purpose."""

    points = points_from_map(canonical_map)
    blocks = blocks_from_plan(read_plan)
    point_by_id: dict[str, Mapping[str, Any]] = {}
    for point_index, point in enumerate(points):
        identifier = point_id(point, point_index)
        if identifier is not None and identifier not in point_by_id:
            point_by_id[identifier] = point

    findings: list[Finding] = []
    point_uses: dict[str, list[int]] = {}
    intervals: dict[tuple[str, int, str], list[tuple[int, int, int, str]]] = {}

    for block_index, block in enumerate(blocks):
        path = f"blocks[{block_index}]"
        identifier = block_id(block, block_index)
        route = block_route_id(block)
        unit = block_unit_id(block)
        area = block_area(block)
        start = block_start(block)
        quantity = block_quantity(block)
        if unit is None or area is None or start is None or quantity is None:
            continue

        stop = start + quantity
        scope = (route, unit, area)
        approved_quantity = max_quantities.get(area)
        if approved_quantity is not None and quantity > approved_quantity:
            findings.append(
                Finding(
                    "error",
                    "BLOCK_QUANTITY_EXCEEDS_PLAN_OPTION",
                    f"Read block {identifier!r} has quantity {quantity}; the visible plan option permits {approved_quantity} for {area}.",
                    f"{path}.quantity",
                )
            )
        scoped_intervals = intervals.setdefault(scope, [])
        for other_start, other_stop, other_index, other_id in scoped_intervals:
            if start == other_start and stop == other_stop:
                findings.append(
                    Finding(
                        "error",
                        "BLOCK_RANGE_DUPLICATE",
                        f"Read block {identifier!r} duplicates block {other_id!r}.",
                        path,
                    )
                )
            elif start < other_stop and other_start < stop:
                findings.append(
                    Finding(
                        "error",
                        "BLOCK_RANGE_OVERLAP",
                        f"Read block {identifier!r} overlaps block {other_id!r}.",
                        path,
                    )
                )
        scoped_intervals.append((start, stop, block_index, identifier))

        requested_ids = block_point_ids(block)
        if len(requested_ids) != len(set(requested_ids)):
            findings.append(
                Finding(
                    "error",
                    "BLOCK_POINT_DUPLICATE",
                    f"Read block {identifier!r} lists a point more than once.",
                    f"{path}.point_ids",
                )
            )

        selected: list[tuple[str, Mapping[str, Any]]] = []
        if requested_ids:
            for requested_id in dict.fromkeys(requested_ids):
                point = point_by_id.get(requested_id)
                if point is not None:
                    selected.append((requested_id, point))
        else:
            for point in points_for_block(canonical_map, block, block_index):
                selected_id = point_id(point)
                if selected_id is not None:
                    selected.append((selected_id, point))

        exact_points: list[tuple[str, Mapping[str, Any], int, int]] = []
        for selected_id, point in selected:
            point_start = point_protocol_offset(point)
            width = point_word_count(point)
            if (
                point_route_id(point) == route
                and point_unit_id(point) == unit
                and point_area(point) == area
                and point_start is not None
                and width is not None
            ):
                exact_points.append((selected_id, point, point_start, width))
                point_uses.setdefault(selected_id, []).append(block_index)

        if not exact_points:
            findings.append(
                Finding(
                    "error",
                    "BLOCK_UNJUSTIFIED",
                    f"Read block {identifier!r} is not justified by a canonical point in the same route, unit, and area.",
                    path,
                )
            )
            continue

        expected_start = min(item[2] for item in exact_points)
        expected_stop = max(item[2] + item[3] for item in exact_points)
        if start != expected_start or stop != expected_stop:
            findings.append(
                Finding(
                    "error",
                    "BLOCK_RANGE_NOT_EXACT",
                    f"Read block {identifier!r} must exactly bound its canonical points at offsets {expected_start} through {expected_stop - 1}.",
                    path,
                )
            )

        ordered_ranges = sorted(
            (point_start, point_start + width)
            for _, _, point_start, width in exact_points
        )
        prior_stop = ordered_ranges[0][1]
        expected_bridges: list[dict[str, Any]] = []
        for point_start, point_stop in ordered_ranges[1:]:
            gap = max(0, point_start - prior_stop)
            if gap:
                function_code = block_function_code(block)
                island = _authorized_readable_island(
                    route,
                    unit,
                    area,
                    function_code,
                    start,
                    stop - 1,
                    readable_islands,
                    unsafe_intervals,
                )
                if island is None:
                    findings.append(
                        Finding(
                            "error",
                            "BLOCK_GAP_NOT_EVIDENCED",
                            f"Read block {identifier!r} bridges addresses without one explicit safe readable island.",
                            path,
                        )
                    )
                else:
                    expected_bridges.append(
                        {
                            "start_offset": prior_stop,
                            "end_offset": point_start - 1,
                            "quantity": gap,
                            "readable_island_id": island.island_id,
                            "reason": island.reason,
                            "evidence_refs": list(island.evidence_refs),
                        }
                    )
            prior_stop = max(prior_stop, point_stop)
        if expected_bridges or block.get("bridged_ranges"):
            raw_bridges = block.get("bridged_ranges", ())
            actual_bridges = (
                list(raw_bridges)
                if isinstance(raw_bridges, Sequence)
                and not isinstance(raw_bridges, (str, bytes, bytearray))
                else []
            )
            if actual_bridges != expected_bridges:
                findings.append(
                    Finding(
                        "error",
                        "BLOCK_BRIDGE_TRACE_MISMATCH",
                        f"Read block {identifier!r} does not exactly trace its evidenced bridged ranges.",
                        f"{path}.bridged_ranges",
                    )
                )

        selected_ids = {item[0] for item in exact_points}
        for point_index, point in enumerate(points):
            candidate_id = point_id(point, point_index)
            candidate_start = point_protocol_offset(point)
            candidate_width = point_word_count(point)
            if (
                candidate_id is not None
                and candidate_id not in selected_ids
                and point_route_id(point) == route
                and point_unit_id(point) == unit
                and point_area(point) == area
                and candidate_start is not None
                and candidate_width is not None
                and candidate_start < stop
                and start < candidate_start + candidate_width
            ):
                findings.append(
                    Finding(
                        "error",
                        "BLOCK_COVERS_UNREFERENCED_POINT",
                        f"Read block {identifier!r} covers canonical point {candidate_id!r} without tracing it.",
                        path,
                    )
                )

        raw_traces = block.get("point_ids", block.get("points", ()))
        if isinstance(raw_traces, Sequence) and not isinstance(
            raw_traces, (str, bytes, bytearray)
        ):
            for trace_index, trace in enumerate(raw_traces):
                if not isinstance(trace, Mapping):
                    continue
                trace_id = point_id(trace)
                point = point_by_id.get(trace_id or "")
                point_start = point_protocol_offset(point) if point is not None else None
                point_width = point_word_count(point) if point is not None else None
                if point is None or point_start is None or point_width is None:
                    continue
                expected_identity = [route, unit, area, point_start, trace_id]
                identity = trace.get("canonical_identity")
                identity_matches = (
                    isinstance(identity, Sequence)
                    and not isinstance(identity, (str, bytes, bytearray))
                    and list(identity) == expected_identity
                )
                mismatched = (
                    ("protocol_offset" in trace and trace.get("protocol_offset") != point_start)
                    or ("span" in trace and trace.get("span") != point_width)
                    or ("relative_offset" in trace and trace.get("relative_offset") != point_start - start)
                    or (
                        "canonical_identity" in trace
                        and not identity_matches
                    )
                )
                if mismatched:
                    findings.append(
                        Finding(
                            "error",
                            "BLOCK_POINT_TRACE_MISMATCH",
                            f"Read block {identifier!r} has a trace that does not match canonical point {trace_id!r}.",
                            f"{path}.points[{trace_index}]",
                        )
                    )

    for identifier, uses in point_uses.items():
        if len(uses) > 1:
            findings.append(
                Finding(
                    "error",
                    "POINT_PLANNED_MULTIPLE_TIMES",
                    f"Canonical point {identifier!r} is read by more than one block.",
                    f"points.{identifier}",
                )
            )
    return findings


def _authorized_readable_island(
    route_id: str,
    unit_id: int,
    area: str,
    function_code: int | None,
    start_offset: int,
    end_offset: int,
    islands: Sequence[ReadableIsland],
    unsafe_intervals: Sequence[UnsafeInterval],
) -> ReadableIsland | None:
    for island in islands:
        if (
            island.route_id == route_id
            and island.unit_id == unit_id
            and island.area.value == area
            and island.function_code == function_code
            and island.start_offset <= start_offset
            and end_offset <= island.end_offset
            and not any(
                interval.route_id == route_id
                and interval.unit_id == unit_id
                and interval.area.value == area
                and start_offset <= interval.end_offset
                and interval.start_offset <= end_offset
                for interval in unsafe_intervals
            )
        ):
            return island
    return None


def _bound_plan_options(
    read_plan: Mapping[str, Any],
) -> tuple[
    int,
    dict[str, int],
    tuple[ReadableIsland, ...],
    tuple[UnsafeInterval, ...],
    list[Finding],
]:
    """Validate and return visible read-authority and quantity limits."""

    findings: list[Finding] = []
    raw_options = read_plan.get("planning_options")
    input_hashes = read_plan.get("input_hashes")
    recorded_hash = (
        input_hashes.get("planning_options")
        if isinstance(input_hashes, Mapping)
        else None
    )
    if raw_options is None:
        if recorded_hash is not None:
            findings.append(
                Finding(
                    "error",
                    "PLAN_OPTIONS_MISSING",
                    "The plan hashes planning options but does not show them. Rebuild the plan.",
                    "planning_options",
                )
            )
        return 0, {}, (), (), findings
    if not isinstance(raw_options, Mapping):
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_INVALID",
                "planning_options must be an object.",
                "planning_options",
            )
        )
        return 0, {}, (), (), findings
    unknown = set(raw_options) - {
        "max_gap",
        "max_quantities",
        "readable_islands",
        "unsafe_intervals",
    }
    if unknown:
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_INVALID",
                "planning_options contains unsupported fields.",
                "planning_options",
            )
        )
    max_gap_value = raw_options.get("max_gap", 0)
    if (
        isinstance(max_gap_value, bool)
        or not isinstance(max_gap_value, int)
        or not 0 <= max_gap_value <= 65_535
    ):
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_INVALID",
                "planning_options.max_gap must be an integer from 0 through 65535.",
                "planning_options.max_gap",
            )
        )
        max_gap = 0
    else:
        max_gap = max_gap_value
    max_quantities = raw_options.get("max_quantities", {})
    normalized_quantities: dict[str, int] = {}
    if not isinstance(max_quantities, Mapping):
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_INVALID",
                "planning_options.max_quantities must be an object.",
                "planning_options.max_quantities",
            )
        )
    else:
        for raw_area, raw_limit in max_quantities.items():
            area = normalize_area(raw_area)
            limit = (
                FUNCTION_LIMITS[AREA_FUNCTION_CODES[area]]
                if area is not None
                else None
            )
            if (
                area is None
                or area in normalized_quantities
                or isinstance(raw_limit, bool)
                or not isinstance(raw_limit, int)
                or raw_limit <= 0
                or limit is None
                or raw_limit > limit
            ):
                findings.append(
                    Finding(
                        "error",
                        "PLAN_OPTIONS_INVALID",
                        "Each planning_options.max_quantities entry must name one area and use a positive value within its protocol limit.",
                        "planning_options.max_quantities",
                    )
                )
                continue
            normalized_quantities[area] = raw_limit
    try:
        readable_islands = normalize_readable_islands(
            raw_options.get("readable_islands", ())
        )
    except (TypeError, ValueError) as exc:
        readable_islands = ()
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_INVALID",
                f"planning_options.readable_islands is invalid: {exc}",
                "planning_options.readable_islands",
            )
        )
    try:
        unsafe_intervals = normalize_unsafe_intervals(
            raw_options.get("unsafe_intervals", ())
        )
    except (TypeError, ValueError) as exc:
        unsafe_intervals = ()
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_INVALID",
                f"planning_options.unsafe_intervals is invalid: {exc}",
                "planning_options.unsafe_intervals",
            )
        )
    if recorded_hash is None:
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_HASH_MISSING",
                "The plan must hash its visible planning options. Rebuild the plan.",
                "input_hashes.planning_options",
            )
        )
    elif (
        not isinstance(recorded_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", recorded_hash) is None
    ):
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_HASH_INVALID",
                "The planning-options hash must be a SHA-256 hex value.",
                "input_hashes.planning_options",
            )
        )
    elif recorded_hash.lower() != stable_input_hash(dict(raw_options)):
        findings.append(
            Finding(
                "error",
                "PLAN_OPTIONS_HASH_MISMATCH",
                "Visible planning options do not match their plan hash. Rebuild the plan.",
                "input_hashes.planning_options",
            )
        )
    return max_gap, normalized_quantities, readable_islands, unsafe_intervals, findings


def preflight_common(
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str,
) -> tuple[Finding, ...]:
    """Validate the shared map and read-plan contract for an exporter."""

    mode = normalize_mode(mode)
    findings: list[Finding] = []
    points = points_from_map(canonical_map)
    blocks = blocks_from_plan(read_plan)

    plan_input_hashes = read_plan.get("input_hashes")
    planned_map_hash = (
        plan_input_hashes.get("canonical_map")
        if isinstance(plan_input_hashes, Mapping)
        else None
    )
    plan_is_bound = False
    if mode == "final" and planned_map_hash is None:
        findings.append(
            Finding(
                "error",
                "PLAN_MAP_HASH_MISSING",
                "The final read plan must identify the exact canonical map used to compile it. Rebuild the plan from the validated map.",
                "input_hashes.canonical_map",
            )
        )
    elif planned_map_hash is not None and (
        not isinstance(planned_map_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", planned_map_hash) is None
    ):
        findings.append(
            Finding(
                "error",
                "PLAN_MAP_HASH_INVALID",
                "The read plan canonical-map hash must be a SHA-256 hex value.",
                "input_hashes.canonical_map",
            )
        )
    elif isinstance(planned_map_hash, str) and (
        planned_map_hash.lower() != canonical_map_hash(canonical_map)
    ):
        findings.append(
            Finding(
                "error",
                "PLAN_MAP_HASH_MISMATCH",
                "The read plan was compiled from a different canonical map. Rebuild the plan after map changes.",
                "input_hashes.canonical_map",
            )
        )
    elif isinstance(planned_map_hash, str):
        plan_is_bound = True

    if not points:
        findings.append(Finding("error", "MAP_HAS_NO_POINTS", "The canonical map has no points.", "points"))
    if not blocks:
        findings.append(Finding("error", "PLAN_HAS_NO_BLOCKS", "The read plan has no blocks.", "blocks"))

    if mode == "final":
        raw_holds = canonical_map.get("holds", ())
        if isinstance(raw_holds, Sequence) and not isinstance(raw_holds, (str, bytes, bytearray)):
            for hold_index, hold in enumerate(raw_holds):
                if not isinstance(hold, Mapping) or hold.get("blocking", True):
                    findings.append(
                        Finding(
                            "error",
                            "MAP_HAS_BLOCKING_HOLD",
                            "The canonical map contains an unresolved blocking hold.",
                            f"holds[{hold_index}]",
                        )
                    )

    raw_holds = canonical_map.get("holds", ())
    if isinstance(raw_holds, Sequence) and not isinstance(
        raw_holds, (str, bytes, bytearray)
    ):
        for hold_index, hold in enumerate(raw_holds):
            if not isinstance(hold, Mapping) or hold.get("blocking", True) is False:
                continue
            hold_code = str(hold.get("code", "")).lower()
            hold_field = str(hold.get("field", "")).lower()
            if (
                hold_field in {"access", "access_readable", "access_writable"}
                or "access" in hold_code
                or "not-readable" in hold_code
                or "write-only" in hold_code
            ):
                findings.append(
                    Finding(
                        "error",
                        "MAP_HAS_ACCESS_HOLD",
                        "Resolve point read access before a capture or export.",
                        f"holds[{hold_index}]",
                    )
                )

    seen_point_ids: set[str] = set()
    point_by_id: dict[str, Mapping[str, Any]] = {}
    for index, point in enumerate(points):
        path = f"points[{index}]"
        identifier = point_id(point, index)
        if identifier is None:
            findings.append(Finding("error", "POINT_ID_UNRESOLVED", "Point ID is unresolved.", f"{path}.point_id"))
        elif identifier in seen_point_ids:
            findings.append(Finding("error", "POINT_ID_DUPLICATE", f"Point ID {identifier!r} is duplicated.", f"{path}.point_id"))
        else:
            seen_point_ids.add(identifier)
            point_by_id[identifier] = point

        if not is_resolved(point.get("route_id", point.get("route"))):
            findings.append(Finding("error", "POINT_ROUTE_UNRESOLVED", "Route ID is unresolved.", f"{path}.route_id"))

        if point_area(point) is None:
            findings.append(Finding("error", "POINT_AREA_UNRESOLVED", "Register area is unresolved.", f"{path}.area"))
        if point_protocol_offset(point) is None:
            findings.append(Finding("error", "POINT_ADDRESS_UNRESOLVED", "Protocol offset is unresolved.", f"{path}.protocol_offset"))
        if point_unit_id(point) is None:
            findings.append(
                Finding(
                    "error",
                    "POINT_UNIT_UNRESOLVED",
                    unit_id_error("Point unit ID"),
                    f"{path}.unit_id",
                )
            )

        access = _normalized_access(point)
        if access == "write-only":
            findings.append(
                Finding(
                    "error",
                    "POINT_WRITE_ONLY_ACTIVE",
                    "A write-only point cannot remain in the active read map.",
                    f"{path}.access",
                )
            )
        elif point.get("access") not in (None, "") and access not in {
            "read-only",
            "read-write",
        }:
            findings.append(
                Finding(
                    "error",
                    "POINT_ACCESS_UNRESOLVED",
                    "Point read access is unresolved.",
                    f"{path}.access",
                )
            )
        if point.get("source_include") is False:
            findings.append(
                Finding(
                    "error",
                    "POINT_SOURCE_EXCLUDED_ACTIVE",
                    "A source-excluded point cannot remain in the active read map.",
                    f"{path}.source_include",
                )
            )

        status = point.get("normalization_status")
        if mode == "final" and status is not None and not is_resolved(status):
            findings.append(Finding("error", "POINT_NOT_CONFIRMED", "Point normalization is not confirmed.", f"{path}.normalization_status"))

        if mode == "final":
            datatype = point_datatype(point)
            if datatype is None:
                findings.append(Finding("error", "POINT_DATATYPE_UNRESOLVED", "Datatype is unresolved.", f"{path}.datatype"))
            width = point_word_count(point)
            if width is None:
                findings.append(Finding("error", "POINT_WIDTH_UNRESOLVED", "Word count is unresolved.", f"{path}.word_count"))
            elif width > 1 and not (datatype or "").startswith("string"):
                order = point_byte_order(point)
                confirmed = point.get("byte_order_confirmed", point.get("byte_layout_confirmed", True))
                order_status = point.get("byte_order_status", point.get("byte_layout_status"))
                if order is None or confirmed is False or (
                    order_status is not None and not is_resolved(order_status)
                ):
                    findings.append(Finding("error", "POINT_BYTE_ORDER_UNRESOLVED", "A multiword point needs a confirmed byte order.", f"{path}.byte_order"))

    seen_blocks: set[str] = set()
    planned_point_ids: set[str] = set()
    for index, block in enumerate(blocks):
        path = f"blocks[{index}]"
        identifier = block_id(block, index)
        if identifier in seen_blocks:
            findings.append(Finding("error", "BLOCK_ID_DUPLICATE", f"Read block ID {identifier!r} is duplicated.", f"{path}.block_id"))
        seen_blocks.add(identifier)

        if not is_resolved(block.get("route_id", block.get("route"))):
            findings.append(Finding("error", "BLOCK_ROUTE_UNRESOLVED", "Read block route ID is unresolved.", f"{path}.route_id"))

        area = block_area(block)
        unit = block_unit_id(block)
        start = block_start(block)
        quantity = block_quantity(block)
        function_code = block_function_code(block)
        if area is None:
            findings.append(Finding("error", "BLOCK_AREA_UNRESOLVED", "Read block area is unresolved.", f"{path}.area"))
        if unit is None:
            findings.append(
                Finding(
                    "error",
                    "BLOCK_UNIT_UNRESOLVED",
                    unit_id_error("Read block unit ID"),
                    f"{path}.unit_id",
                )
            )
        if start is None:
            findings.append(Finding("error", "BLOCK_ADDRESS_UNRESOLVED", "Read block start offset is unresolved.", f"{path}.start_offset"))
        if quantity is None:
            findings.append(Finding("error", "BLOCK_QUANTITY_INVALID", "Read block quantity is invalid.", f"{path}.quantity"))
        if function_code not in FUNCTION_LIMITS:
            findings.append(Finding("error", "UNSAFE_FUNCTION_CODE", "Only Modbus read functions 01 through 04 are permitted.", f"{path}.function_code"))
        elif area is not None and AREA_FUNCTION_CODES[area] != function_code:
            findings.append(Finding("error", "FUNCTION_AREA_MISMATCH", "Read function does not match the register area.", f"{path}.function_code"))
        if quantity is not None and function_code in FUNCTION_LIMITS and quantity > FUNCTION_LIMITS[function_code]:
            findings.append(Finding("error", "BLOCK_LIMIT_EXCEEDED", f"Function {function_code:02d} permits at most {FUNCTION_LIMITS[function_code]} values per read.", f"{path}.quantity"))
        if start is not None and quantity is not None and start + quantity > 65536:
            findings.append(Finding("error", "BLOCK_RANGE_EXCEEDED", "Read block extends past protocol offset 65535.", path))

        for identifier_for_point in block_point_ids(block):
            if identifier_for_point not in seen_point_ids:
                findings.append(Finding("error", "BLOCK_POINT_UNKNOWN", f"Read block references unknown point {identifier_for_point!r}.", f"{path}.point_ids"))
            else:
                referenced_point = point_by_id[identifier_for_point]
                block_route = block_route_id(block)
                referenced_route = point_route_id(referenced_point)
                referenced_unit = point_unit_id(referenced_point)
                referenced_area = point_area(referenced_point)
                if referenced_route != block_route:
                    findings.append(
                        Finding(
                            "error",
                            "BLOCK_POINT_ROUTE_MISMATCH",
                            f"Point {identifier_for_point!r} belongs to route {referenced_route!r}, not block route {block_route!r}.",
                            f"{path}.point_ids",
                        )
                    )
                if unit is not None and referenced_unit is not None and referenced_unit != unit:
                    findings.append(
                        Finding(
                            "error",
                            "BLOCK_POINT_UNIT_MISMATCH",
                            f"Point {identifier_for_point!r} belongs to unit {referenced_unit}, not block unit {unit}.",
                            f"{path}.point_ids",
                        )
                    )
                if area is not None and referenced_area is not None and referenced_area != area:
                    findings.append(
                        Finding(
                            "error",
                            "BLOCK_POINT_AREA_MISMATCH",
                            f"Point {identifier_for_point!r} belongs to area {referenced_area!r}, not block area {area!r}.",
                            f"{path}.point_ids",
                        )
                    )
            planned_point_ids.add(identifier_for_point)

        for point in points_for_block(canonical_map, block, index):
            identifier_for_point = point_id(point)
            if identifier_for_point:
                planned_point_ids.add(identifier_for_point)
            point_start = point_protocol_offset(point)
            width = point_word_count(point) or 1
            if (
                point_start is not None
                and start is not None
                and quantity is not None
                and not (start <= point_start and point_start + width <= start + quantity)
            ):
                findings.append(Finding("error", "POINT_OUTSIDE_BLOCK", f"Point {identifier_for_point!r} does not fit in read block {identifier!r}.", path))

    if mode == "final" and blocks:
        for index, point in enumerate(points):
            identifier = point_id(point, index)
            if identifier is not None and identifier not in planned_point_ids:
                findings.append(Finding("error", "POINT_NOT_PLANNED", f"Point {identifier!r} is not covered by the read plan.", f"points[{index}]"))

    if plan_is_bound:
        (
            _max_gap,
            max_quantities,
            readable_islands,
            unsafe_intervals,
            option_findings,
        ) = _bound_plan_options(read_plan)
        findings.extend(option_findings)
        findings.extend(
            _validate_map_bound_plan(
                canonical_map,
                read_plan,
                max_quantities=max_quantities,
                readable_islands=readable_islands,
                unsafe_intervals=unsafe_intervals,
            )
        )

    return tuple(sorted(set(findings)))


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


def held_result(
    target: str,
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    *,
    mode: str,
    adapter_version: str,
    findings: Iterable[Finding],
    profile: str | None = None,
) -> ExportResult:
    return ExportResult(
        target=target,
        status="held",
        mode=normalize_mode(mode),
        map_hash=canonical_map_hash(canonical_map),
        read_plan_hash=read_plan_hash(read_plan),
        adapter_version=adapter_version,
        profile=profile,
        findings=tuple(sorted(set(findings))),
    )


def target_manifest(
    *,
    target: str,
    profile: str | None,
    mode: str,
    adapter_version: str,
    canonical_map: Mapping[str, Any],
    read_plan: Mapping[str, Any],
    findings: Iterable[Finding],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    map_digest = canonical_map_hash(canonical_map)
    plan_digest = read_plan_hash(read_plan)
    finding_values = [
        finding.to_dict() for finding in sorted(set(findings))
    ]
    manifest: dict[str, Any] = {
        "target": target,
        "mode": normalize_mode(mode),
        "adapter_version": adapter_version,
        "map_hash": map_digest,
        "read_plan_hash": plan_digest,
        "safety": {
            "read_only": True,
            "allowed_function_codes": [1, 2, 3, 4],
            "network_discovery": False,
        },
        "verification": "not-run",
    }
    if profile is not None:
        manifest["profile"] = profile
    if extra:
        manifest.update(extra)
    return artifact_envelope(
        manifest,
        schema_version=TARGET_MANIFEST_SCHEMA_VERSION,
        artifact_type="modbus-target-manifest",
        input_hashes={
            "canonical_map": map_digest,
            "read_plan": plan_digest,
        },
        assumptions=[],
        findings=finding_values,
        holds=_blocking_finding_values(finding_values),
    )


def _blocking_finding_values(
    findings: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        finding
        for finding in findings
        if str(finding.get("severity", "")).lower() in {"error", "hold"}
    ]


def safe_slug(value: Any, *, fallback: str = "item") -> str:
    text = str(value).strip().lower()
    slug = "".join(character if character.isalnum() else "-" for character in text)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or fallback


def env_prefix_for_route(route_id: str, *, multiple_routes: bool) -> str:
    if not multiple_routes and route_id == "default":
        return "MODBUS"
    slug = safe_slug(route_id, fallback="DEFAULT").replace("-", "_").upper()
    return f"MODBUS_{slug}"


def spreadsheet_safe_cell(value: Any) -> Any:
    """Neutralize spreadsheet formula prefixes in an untrusted CSV cell.

    Numeric values stay numeric. String values that a spreadsheet can interpret
    as formulas get an apostrophe prefix. The original text remains visible.
    """

    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip(" \t\r\n\ufeff")
    if value[0] in "\t\r\n" or (stripped and stripped[0] in "=+-@"):
        return "'" + value
    return value


def write_csv_row(writer: Any, values: Iterable[Any]) -> None:
    """Write one CSV row after formula neutralization of every cell."""

    writer.writerow([spreadsheet_safe_cell(value) for value in values])


def _nonnegative_int(
    value: Any, *, minimum: int, maximum: int
) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed
