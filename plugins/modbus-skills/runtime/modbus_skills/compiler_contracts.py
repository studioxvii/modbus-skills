"""Deterministic artifact contracts for the OEM-to-user-map compiler.

The artifacts in this module keep OEM semantics, user intent, and deployment
binding separate.  Builders return ordinary JSON-safe dictionaries so later
compiler stages can persist them with the repository's existing envelope and
hash conventions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import PurePosixPath
import re
from typing import Any

from .artifacts import (
    ArtifactContractError,
    artifact_envelope,
    assert_artifact_envelope,
    stable_input_hash,
)
from .models import DataType
from .unit_id_scope import unit_id_error


OEM_MAP_SCHEMA_VERSION = "modbus-oem-map/v1"
USER_SELECTION_SCHEMA_VERSION = "modbus-user-selection/v1"
DEVICE_BINDING_SCHEMA_VERSION = "modbus-device-binding/v1"
USER_MAP_SCHEMA_VERSION = "modbus-user-map/v1"
COMPILE_CASE_SCHEMA_VERSION = "modbus-compile-case/v1"

_MATERIAL_PDF_FIELDS = frozenset(
    {
        "protocol_offset",
        "datatype",
        "access",
        "engineering_unit",
        "scale",
        "description",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POINT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CASE_STATES = frozenset(
    {
        "running",
        "awaiting-source-decision",
        "awaiting-selection-decision",
        "offline-complete",
        "awaiting-binding",
        "awaiting-physical-read",
        "awaiting-byte-order-decision",
        "partial",
        "complete",
        "failed",
    }
)
_PORTABLE_FORBIDDEN_FIELDS = frozenset(
    {
        "absolute_path",
        "api_key",
        "capture",
        "case_path",
        "credential",
        "credentials",
        "endpoint",
        "evidence_payload",
        "host",
        "hostname",
        "ip_address",
        "password",
        "private_evidence",
        "raw_evidence",
        "route_id",
        "secret",
        "source_excerpt",
        "token",
        "transport",
        "unit_id",
    }
)


class CompilerContractError(ArtifactContractError):
    """Raised when compiler artifacts are malformed, stale, or unsafe."""


def build_oem_map(
    points: Sequence[Mapping[str, Any]],
    *,
    source_hash: str,
    source_reference: Mapping[str, Any] | None = None,
    source_coverage: Mapping[str, Any] | None = None,
    assumptions: Sequence[Any] = (),
    findings: Sequence[Any] = (),
    holds: Sequence[Any] = (),
) -> dict[str, Any]:
    """Build a portable OEM semantic map with binding-free point identity."""

    digest = _digest(source_hash, "source_hash")
    normalized_points = [_json_object(point, "OEM point") for point in points]
    normalized_points.sort(key=_oem_sort_key)
    result = artifact_envelope(
        {
            "source_sha256": digest,
            "source_reference": _json_object(
                source_reference or {}, "source_reference"
            ),
            "source_coverage": _json_object(
                source_coverage or {}, "source_coverage"
            ),
            "points": normalized_points,
        },
        schema_version=OEM_MAP_SCHEMA_VERSION,
        input_hashes={"source": digest},
        assumptions=assumptions,
        findings=findings,
        holds=holds,
    )
    validate_oem_map(result)
    return result


def validate_oem_map(value: Mapping[str, Any]) -> None:
    _validate_schema(value, OEM_MAP_SCHEMA_VERSION)
    _assert_portable(value)
    source_hash = _digest(value.get("source_sha256"), "source_sha256")
    if value["input_hashes"].get("source") != source_hash:
        raise CompilerContractError("OEM map source hash does not match its input hash")
    points = _array(value.get("points"), "OEM map points")
    coverage = _mapping(value.get("source_coverage", {}), "source_coverage")
    if coverage:
        if coverage.get("status") not in {"complete", "unknown"}:
            raise CompilerContractError(
                "source_coverage.status must be complete or unknown"
            )
        for field in (
            "accepted_row_count",
            "rejected_row_count",
            "quarantined_row_count",
        ):
            count = coverage.get(field)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise CompilerContractError(
                    f"source_coverage.{field} must be a non-negative integer"
                )
        detected_pages = _array(
            coverage.get("detected_pages"), "source_coverage.detected_pages"
        )
        for page in detected_pages:
            if isinstance(page, bool) or not isinstance(page, int) or page < 0:
                raise CompilerContractError(
                    "source_coverage.detected_pages must contain non-negative integers"
                )
        if (
            coverage.get("status") == "complete"
            and coverage["accepted_row_count"] > 0
            and not detected_pages
        ):
            raise CompilerContractError(
                "complete source coverage with accepted rows requires detected_pages"
            )
        _array(coverage.get("detected_regions"), "source_coverage.detected_regions")
        covered_pages = coverage.get("covered_pages")
        if covered_pages is not None:
            covered = _array(covered_pages, "source_coverage.covered_pages")
            if any(
                isinstance(page, bool) or not isinstance(page, int) or page < 0
                for page in covered
            ):
                raise CompilerContractError(
                    "source_coverage.covered_pages must contain non-negative integers"
                )
            if (
                not set(detected_pages).issubset(set(covered))
                and coverage.get("status") == "complete"
            ):
                raise CompilerContractError(
                    "complete source coverage must cover every detected page"
                )
        independent = coverage.get("independent_parser_row_count")
        single = coverage.get("single_parser_row_count")
        if independent is not None or single is not None:
            if any(
                isinstance(count, bool) or not isinstance(count, int) or count < 0
                for count in (independent, single)
            ):
                raise CompilerContractError(
                    "source coverage parser row counts must be non-negative integers"
                )
            if independent + single != coverage["accepted_row_count"]:
                raise CompilerContractError(
                    "source coverage parser row counts must equal accepted rows"
                )
        if not isinstance(coverage.get("discovery_complete"), bool):
            raise CompilerContractError(
                "source_coverage.discovery_complete must be boolean"
            )
    seen: dict[str, Mapping[str, Any]] = {}
    for raw_point in points:
        point = _mapping(raw_point, "OEM point")
        point_id = _text(point.get("oem_point_id"), "OEM point oem_point_id")
        if "route_id" in point or "unit_id" in point or "endpoint" in point:
            raise CompilerContractError(
                "OEM points must not contain route, unit, or endpoint binding"
            )
        _validate_oem_point(point)
        if point_id in seen:
            if _json_copy(seen[point_id]) == _json_copy(point):
                raise CompilerContractError(f"duplicate OEM point ID: {point_id}")
            raise CompilerContractError(f"OEM point ID collision: {point_id}")
        seen[point_id] = point


def pdf_completion_issues(
    oem_map: Mapping[str, Any], selection: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return selected PDF points that cannot support a completion claim."""

    reference = oem_map.get("source_reference", {})
    if not isinstance(reference, Mapping) or reference.get("format") != "pdf":
        return []
    selected = {
        item.get("oem_point_id")
        for item in selection.get("included", ())
        if isinstance(item, Mapping) and isinstance(item.get("oem_point_id"), str)
    }
    issues = []
    for point in oem_map.get("points", ()):
        if not isinstance(point, Mapping) or point.get("oem_point_id") not in selected:
            continue
        fields = _pdf_point_issue_fields(point)
        if fields:
            issues.append(
                {
                    "code": "pdf-field-evidence-unconfirmed",
                    "point_id": str(point["oem_point_id"]),
                    "fields": fields,
                    "evidence_refs": point_evidence_refs(point),
                }
            )
    return sorted(issues, key=lambda item: str(item["point_id"]))


def _pdf_point_issue_fields(point: Mapping[str, Any]) -> list[str]:
    required = {
        field
        for field in _MATERIAL_PDF_FIELDS
        if point.get(field) not in (None, "")
    }
    if point.get("word_span") not in (None, ""):
        required.add("word_span")
    evidence = point.get("source_field_evidence")
    if (
        not isinstance(evidence, Sequence)
        or isinstance(evidence, (str, bytes, bytearray))
        or not evidence
    ):
        return sorted(required)
    evidence_by_field = {
        str(item["field"]): item for item in evidence if isinstance(item, Mapping)
    }
    source_refs = set(point_evidence_refs(point))
    compared_fields = _MATERIAL_PDF_FIELDS | {"word_span"}
    issues = set()
    for item in evidence:
        if not isinstance(item, Mapping):
            issues.update(required)
            continue
        field = str(item.get("field"))
        if item.get("status") != "confirmed" or (
            field in compared_fields
            and (
                item.get("normalized_value") != point.get(field)
                or item.get("source_ref") not in source_refs
            )
        ):
            issues.add(field)
    confirmed = {
        field
        for field, item in evidence_by_field.items()
        if item.get("status") == "confirmed"
        and item.get("raw_value") not in (None, "")
    }
    issues.update(required - confirmed)
    word_span = point.get("word_span")
    width = evidence_by_field.get("word_span")
    if word_span not in (None, "") and (
        width is None or width.get("raw_value") in (None, "")
    ):
        if DataType.coerce(point.get("datatype")).span == word_span:
            issues.discard("word_span")
    return sorted(issues)


def build_user_selection(
    oem_map: Mapping[str, Any],
    *,
    requested_measurements: Sequence[str],
    included: Sequence[Mapping[str, Any]],
    suggested: Sequence[Mapping[str, Any]] = (),
    excluded: Sequence[Mapping[str, Any]] = (),
    assumptions: Sequence[Any] = (),
    findings: Sequence[Any] = (),
    holds: Sequence[Any] = (),
) -> dict[str, Any]:
    validate_oem_map(oem_map)
    result = artifact_envelope(
        {
            "requested_measurements": sorted(
                {_text(item, "requested measurement") for item in requested_measurements}
            ),
            "included": _sorted_dispositions(included),
            "suggested": _sorted_dispositions(suggested),
            "excluded": _sorted_dispositions(excluded),
        },
        schema_version=USER_SELECTION_SCHEMA_VERSION,
        input_hashes={"oem_map": stable_input_hash(oem_map)},
        assumptions=assumptions,
        findings=findings,
        holds=holds,
    )
    validate_user_selection(result, oem_map)
    return result


def validate_user_selection(
    value: Mapping[str, Any], oem_map: Mapping[str, Any]
) -> None:
    validate_oem_map(oem_map)
    _validate_schema(value, USER_SELECTION_SCHEMA_VERSION)
    _assert_portable(value)
    _require_parent_hash(value, "oem_map", oem_map, "OEM map")
    for measurement in _array(
        value.get("requested_measurements"), "requested_measurements"
    ):
        _text(measurement, "requested measurement")
    known = {point["oem_point_id"] for point in oem_map["points"]}
    dispositions: dict[str, str] = {}
    for field in ("included", "suggested", "excluded"):
        for raw_entry in _array(value.get(field), f"selection {field}"):
            entry = _mapping(raw_entry, f"selection {field} entry")
            point_id = _text(entry.get("oem_point_id"), "selection oem_point_id")
            _text(entry.get("reason"), "selection reason")
            if point_id not in known:
                raise CompilerContractError(
                    f"selection references unknown OEM point: {point_id}"
                )
            if point_id in dispositions:
                raise CompilerContractError(
                    f"OEM point {point_id} has more than one disposition"
                )
            dispositions[point_id] = field
            confidence = entry.get("confidence")
            if confidence is not None and (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0 <= confidence <= 1
            ):
                raise CompilerContractError("selection confidence must be from 0 through 1")


def point_evidence_refs(point: Mapping[str, Any]) -> list[str]:
    """Render portable source references into stable selection evidence IDs."""

    refs: list[str] = []
    for raw in point.get("source_refs", ()):
        if not isinstance(raw, Mapping):
            continue
        if raw.get("region_id"):
            refs.append(str(raw["region_id"]))
        elif raw.get("record_id"):
            refs.append(str(raw["record_id"]))
        else:
            refs.append(
                f"page-{raw.get('page_index', 'unknown')}-row-{raw.get('row_index', 'unknown')}"
            )
    return refs


def build_device_binding(
    oem_map: Mapping[str, Any],
    *,
    route_id: str,
    unit_id: int | None,
    transport: Mapping[str, Any] | None = None,
    read_constraints: Mapping[str, Any] | None = None,
    point_overrides: Sequence[Mapping[str, Any]] = (),
    assumptions: Sequence[Any] = (),
    findings: Sequence[Any] = (),
    holds: Sequence[Any] = (),
) -> dict[str, Any]:
    validate_oem_map(oem_map)
    result = artifact_envelope(
        {
            "route_id": route_id,
            "unit_id": unit_id,
            "transport": _json_object(transport or {}, "transport"),
            "read_constraints": _json_object(
                read_constraints or {}, "read_constraints"
            ),
            "point_overrides": sorted(
                (_json_object(item, "point override") for item in point_overrides),
                key=lambda item: str(item.get("oem_point_id", "")),
            ),
        },
        schema_version=DEVICE_BINDING_SCHEMA_VERSION,
        input_hashes={"oem_map": stable_input_hash(oem_map)},
        assumptions=assumptions,
        findings=findings,
        holds=holds,
    )
    validate_device_binding(result, oem_map)
    return result


def validate_device_binding(
    value: Mapping[str, Any], oem_map: Mapping[str, Any]
) -> None:
    validate_oem_map(oem_map)
    _validate_schema(value, DEVICE_BINDING_SCHEMA_VERSION)
    _require_parent_hash(value, "oem_map", oem_map, "OEM map")
    route_id = _text(value.get("route_id"), "binding route_id")
    unit_id = value.get("unit_id")
    if isinstance(unit_id, bool) or not isinstance(unit_id, int) or not 1 <= unit_id <= 247:
        raise CompilerContractError(unit_id_error("Binding unit_id"))
    if not route_id:
        raise CompilerContractError("binding route_id must be non-empty text")
    _mapping(value.get("transport"), "binding transport")
    _mapping(value.get("read_constraints"), "binding read_constraints")
    known = {point["oem_point_id"] for point in oem_map["points"]}
    seen: set[str] = set()
    for raw_override in _array(value.get("point_overrides", []), "point_overrides"):
        override = _mapping(raw_override, "point override")
        point_id = _text(override.get("oem_point_id"), "point override oem_point_id")
        if point_id not in known:
            raise CompilerContractError(
                f"binding references unknown OEM point: {point_id}"
            )
        if point_id in seen:
            raise CompilerContractError(f"duplicate point override: {point_id}")
        seen.add(point_id)


def build_user_map(
    oem_map: Mapping[str, Any],
    selection: Mapping[str, Any],
    *,
    points: Sequence[Mapping[str, Any]],
    exception_annex: Sequence[Mapping[str, Any]] = (),
    assumptions: Sequence[Any] = (),
    findings: Sequence[Any] = (),
    holds: Sequence[Any] = (),
) -> dict[str, Any]:
    validate_user_selection(selection, oem_map)
    result = artifact_envelope(
        {
            "points": sorted(
                (_json_object(point, "user-map point") for point in points),
                key=_user_map_sort_key,
            ),
            "exception_annex": sorted(
                (_json_object(item, "exception annex item") for item in exception_annex),
                key=lambda item: (
                    str(item.get("oem_point_id", "")), str(item.get("code", ""))
                ),
            ),
        },
        schema_version=USER_MAP_SCHEMA_VERSION,
        input_hashes={
            "oem_map": stable_input_hash(oem_map),
            "selection": stable_input_hash(selection),
        },
        assumptions=assumptions,
        findings=findings,
        holds=holds,
    )
    validate_user_map(result, oem_map, selection)
    return result


def validate_user_map(
    value: Mapping[str, Any],
    oem_map: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> None:
    validate_user_selection(selection, oem_map)
    _validate_schema(value, USER_MAP_SCHEMA_VERSION)
    _assert_portable(value)
    _require_parent_hash(value, "oem_map", oem_map, "OEM map")
    _require_parent_hash(value, "selection", selection, "selection")
    included = {item["oem_point_id"] for item in selection["included"]}
    seen: set[str] = set()
    for raw_point in _array(value.get("points"), "user-map points"):
        point = _mapping(raw_point, "user-map point")
        point_id = _text(point.get("oem_point_id"), "user-map oem_point_id")
        if point_id not in included:
            raise CompilerContractError(
                f"user map contains point not included by selection: {point_id}"
            )
        if point_id in seen:
            raise CompilerContractError(f"duplicate user-map point: {point_id}")
        seen.add(point_id)
    for raw_item in _array(value.get("exception_annex"), "exception_annex"):
        _mapping(raw_item, "exception annex item")


def build_compile_case(
    *,
    source_hash: str,
    request_hash: str,
    compiler_version: str,
    state: str,
    artifacts: Mapping[str, Mapping[str, Any]] | None = None,
    completed_receipts: Sequence[Mapping[str, Any]] = (),
    active_packet: Mapping[str, Any] | None = None,
    requested_targets: Sequence[str] = (),
    next_action: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build local case control data; this artifact is not portable output."""

    source_digest = _digest(source_hash, "source_hash")
    request_digest = _digest(request_hash, "request_hash")
    version = _text(compiler_version, "compiler_version")
    case_id = stable_input_hash(
        {
            "source_hash": source_digest,
            "request_hash": request_digest,
            "compiler_version": version,
        }
    )[:24]
    result = artifact_envelope(
        {
            "case_id": case_id,
            "compiler_version": version,
            "state": state,
            "artifacts": {
                name: _json_object(artifact, f"case artifact {name}")
                for name, artifact in sorted((artifacts or {}).items())
            },
            "completed_receipts": sorted(
                (_json_object(item, "completed receipt") for item in completed_receipts),
                key=stable_input_hash,
            ),
            "active_packet": (
                _json_object(active_packet, "active packet")
                if active_packet is not None
                else None
            ),
            "requested_targets": sorted(
                {_text(item, "requested target") for item in requested_targets}
            ),
            "next_action": (
                _json_object(next_action, "next action")
                if next_action is not None
                else None
            ),
        },
        schema_version=COMPILE_CASE_SCHEMA_VERSION,
        input_hashes={"request": request_digest, "source": source_digest},
    )
    validate_compile_case(result)
    return result


def validate_compile_case(value: Mapping[str, Any]) -> None:
    _validate_schema(value, COMPILE_CASE_SCHEMA_VERSION)
    if value.get("state") not in _CASE_STATES:
        raise CompilerContractError("compile case state is invalid")
    version = _text(value.get("compiler_version"), "compiler_version")
    source_hash = _digest(value["input_hashes"].get("source"), "source input hash")
    request_hash = _digest(value["input_hashes"].get("request"), "request input hash")
    expected_id = stable_input_hash(
        {
            "source_hash": source_hash,
            "request_hash": request_hash,
            "compiler_version": version,
        }
    )[:24]
    if value.get("case_id") != expected_id:
        raise CompilerContractError("compile case ID does not match immutable inputs")
    artifacts = _mapping(value.get("artifacts", {}), "case artifacts")
    for name, raw_record in artifacts.items():
        _text(name, "case artifact name")
        record = _mapping(raw_record, f"case artifact {name}")
        _case_relative_path(record.get("path"))
        _digest(record.get("sha256"), f"case artifact {name} sha256")
        _text(record.get("schema_version"), f"case artifact {name} schema_version")
    for raw_receipt in _array(value.get("completed_receipts"), "completed_receipts"):
        _mapping(raw_receipt, "completed receipt")
    active_packet = value.get("active_packet")
    if active_packet is not None:
        _mapping(active_packet, "active_packet")
    for target in _array(value.get("requested_targets"), "requested_targets"):
        _text(target, "requested target")
    next_action = value.get("next_action")
    if next_action is not None:
        _mapping(next_action, "next_action")


def bound_point_identity(
    point: Mapping[str, Any], *, route_id: str, unit_id: int
) -> tuple[str, int, str, int, str]:
    """Map an OEM identity to the legacy bound composite identity."""

    route = _text(route_id, "route_id")
    if isinstance(unit_id, bool) or not isinstance(unit_id, int) or not 1 <= unit_id <= 247:
        raise CompilerContractError(unit_id_error("unit_id"))
    point_id = _text(point.get("oem_point_id"), "OEM point oem_point_id")
    area = _text(point.get("area"), "OEM point area")
    offset = point.get("protocol_offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 65_535:
        raise CompilerContractError(
            "a bound point requires protocol_offset from 0 through 65535"
        )
    return (route, unit_id, area, offset, point_id)


def _validate_oem_point(point: Mapping[str, Any]) -> None:
    point_id = _text(point.get("oem_point_id"), "OEM point oem_point_id")
    if not _POINT_ID.fullmatch(point_id):
        raise CompilerContractError(
            "OEM point oem_point_id must contain only letters, digits, dot, dash, or underscore"
        )
    area = point.get("area")
    if area is not None and not isinstance(area, str):
        raise CompilerContractError("OEM point area must be text or null")
    offset = point.get("protocol_offset")
    if offset is not None and (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= 65_535
    ):
        raise CompilerContractError(
            "OEM point protocol_offset must be null or an integer from 0 through 65535"
        )
    refs = _array(point.get("source_refs"), "OEM point source_refs")
    if not refs:
        raise CompilerContractError("OEM point source_refs must contain at least one reference")
    for raw_ref in refs:
        ref = _mapping(raw_ref, "OEM point source reference")
        has_page_locator = "page_index" in ref or "row_index" in ref
        has_record_locator = ref.get("record_id") not in (None, "")
        if not has_page_locator and not has_record_locator:
            raise CompilerContractError(
                "OEM point source reference needs page/row or a stable record_id"
            )
        if has_page_locator:
            for field in ("page_index", "row_index"):
                index = ref.get(field)
                if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                    raise CompilerContractError(
                        f"OEM point source reference {field} must be a non-negative integer"
                    )
        if has_record_locator:
            _text(ref.get("record_id"), "OEM point source reference record_id")
    field_evidence = point.get("source_field_evidence")
    if field_evidence is not None:
        seen_fields: set[str] = set()
        for raw_item in _array(field_evidence, "OEM point source_field_evidence"):
            item = _mapping(raw_item, "OEM point source field evidence")
            field = _text(item.get("field"), "source field evidence field")
            if field in seen_fields:
                raise CompilerContractError(
                    f"duplicate source field evidence: {field}"
                )
            seen_fields.add(field)
            _text(item.get("raw_header"), "source field evidence raw_header")
            if "raw_value" not in item:
                raise CompilerContractError(
                    "source field evidence raw_value is required"
                )
            if "normalized_value" not in item:
                raise CompilerContractError(
                    "source field evidence normalized_value is required"
                )
            _text(item.get("source_ref"), "source field evidence source_ref")
            if item.get("status") not in {"confirmed", "contradiction", "unresolved"}:
                raise CompilerContractError(
                    "source field evidence status must be confirmed, contradiction, or unresolved"
                )


def _validate_schema(value: Mapping[str, Any], schema_version: str) -> None:
    try:
        assert_artifact_envelope(value)
    except ArtifactContractError as exc:
        raise CompilerContractError(str(exc)) from exc
    if value.get("schema_version") != schema_version:
        raise CompilerContractError(f"expected {schema_version} artifact")


def _require_parent_hash(
    artifact: Mapping[str, Any],
    field: str,
    parent: Mapping[str, Any],
    label: str,
) -> None:
    expected = stable_input_hash(parent)
    actual = artifact["input_hashes"].get(field)
    if actual != expected:
        raise CompilerContractError(f"stale {label} hash")


def _sorted_dispositions(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        (_json_object(value, "selection disposition") for value in values),
        key=lambda item: str(item.get("oem_point_id", "")),
    )


def _oem_sort_key(point: Mapping[str, Any]) -> tuple[str, int, str]:
    offset = point.get("protocol_offset")
    sortable_offset = offset if isinstance(offset, int) and not isinstance(offset, bool) else 65_536
    return (
        str(point.get("area", "")),
        sortable_offset,
        str(point.get("oem_point_id", "")),
    )


def _user_map_sort_key(point: Mapping[str, Any]) -> tuple[str, str, int, str]:
    offset = point.get("protocol_offset")
    if not isinstance(offset, int) or isinstance(offset, bool):
        match = re.match(r"\d+", str(point.get("source_register", "")))
        offset = int(match.group()) if match else 65_536
    return (
        str(point.get("group", point.get("requested_measurement", ""))).casefold(),
        str(point.get("area", "")),
        offset,
        str(point.get("oem_point_id", "")),
    )


def _assert_portable(value: Any, path: str = "artifact") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _PORTABLE_FORBIDDEN_FIELDS:
                raise CompilerContractError(
                    f"portable artifact field is not allowed: {path}.{key}"
                )
            _assert_portable(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_portable(item, f"{path}[{index}]")
    elif isinstance(value, str) and (
        value.startswith(("/", "~/", "\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
    ):
        raise CompilerContractError(
            f"portable artifact contains a local absolute path: {path}"
        )


def _case_relative_path(value: Any) -> str:
    path = _text(value, "case artifact path")
    candidate = PurePosixPath(path)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in path
        or path.startswith("~")
    ):
        raise CompilerContractError("case artifact paths must be case-relative")
    if not candidate.parts or "." in candidate.parts:
        raise CompilerContractError("case artifact paths must be normalized case-relative paths")
    return path


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value.lower()):
        raise CompilerContractError(f"{field} must be SHA-256 hex")
    return value.lower()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompilerContractError(f"{field} must be non-empty text")
    return value.strip()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompilerContractError(f"{field} must be an object")
    return value


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise CompilerContractError(f"{field} must be an array")
    return value


def _json_object(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompilerContractError(f"{field} must be an object")
    result = _json_copy(value)
    if not isinstance(result, dict):
        raise CompilerContractError(f"{field} must be an object")
    return result


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise CompilerContractError("compiler artifact values must be deterministic JSON") from exc


__all__ = [
    "COMPILE_CASE_SCHEMA_VERSION",
    "CompilerContractError",
    "DEVICE_BINDING_SCHEMA_VERSION",
    "OEM_MAP_SCHEMA_VERSION",
    "USER_MAP_SCHEMA_VERSION",
    "USER_SELECTION_SCHEMA_VERSION",
    "bound_point_identity",
    "build_compile_case",
    "build_device_binding",
    "build_oem_map",
    "build_user_map",
    "build_user_selection",
    "point_evidence_refs",
    "pdf_completion_issues",
    "validate_compile_case",
    "validate_device_binding",
    "validate_oem_map",
    "validate_user_map",
    "validate_user_selection",
]
