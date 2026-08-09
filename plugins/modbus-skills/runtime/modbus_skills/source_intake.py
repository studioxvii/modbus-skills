"""Deterministic local-source intake for the OEM map compiler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from .artifacts import stable_input_hash
from .compiler_contracts import CompilerContractError, build_oem_map
from .map_workflows import MapWorkflowError, normalize_map
from .parsers import ParseError, parse_source
from .pdf_extraction import PdfExtractionError, extract_pdf, parse_page_range


SELECTION_TEMPLATE_SCHEMA_VERSION = "modbus-user-selection-template/v1"
_SOURCE_FORMATS = frozenset({"pdf", "csv", "tsv", "psv", "json", "xml", "xlsx"})
_SOURCE_FIELDS = frozenset({"path", "format", "pages", "delimiter", "defaults"})
_DEPLOYMENT_ONLY_HOLDS = frozenset(
    {"point.route-id-unresolved", "point.unit-id-unresolved"}
)
_MAX_SOURCE_BYTES = 100_000_000
_OEM_POINT_FIELDS = (
    "name",
    "description",
    "area",
    "protocol_offset",
    "datatype",
    "word_span",
    "byte_order",
    "scale",
    "engineering_offset",
    "engineering_unit",
    "access",
    "function_code",
    "minimum",
    "maximum",
)


class SourceIntakeError(ValueError):
    """Raised when a local source cannot become deterministic OEM evidence."""


def compile_source_descriptor(
    descriptor: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a binding-free OEM map and normalized public source descriptor."""

    if not isinstance(descriptor, Mapping):
        raise SourceIntakeError("source descriptor must be an object")
    unknown = set(descriptor) - _SOURCE_FIELDS
    if unknown:
        raise SourceIntakeError(
            "source descriptor has unknown fields: "
            + ", ".join(sorted(map(str, unknown)))
        )
    raw_path = descriptor.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SourceIntakeError("source.path must be non-empty local path text")
    path = Path(raw_path).expanduser()
    if path.is_symlink() or not path.is_file():
        raise SourceIntakeError("source.path must name an existing non-symlink file")
    data = path.read_bytes()
    if len(data) > _MAX_SOURCE_BYTES:
        raise SourceIntakeError(f"source exceeds the {_MAX_SOURCE_BYTES} byte intake limit")
    raw_format = descriptor.get("format")
    source_format = (
        str(raw_format).strip().lower().lstrip(".")
        if raw_format not in (None, "")
        else path.suffix.lower().lstrip(".")
    )
    if source_format not in _SOURCE_FORMATS:
        raise SourceIntakeError(
            "source format must be pdf, csv, tsv, psv, json, xml, or xlsx"
        )
    defaults = descriptor.get("defaults", {})
    if not isinstance(defaults, Mapping):
        raise SourceIntakeError("source.defaults must be an object")
    delimiter = descriptor.get("delimiter")
    if delimiter is not None and not isinstance(delimiter, str):
        raise SourceIntakeError("source.delimiter must be text")
    pages = descriptor.get("pages")
    if pages is not None and not isinstance(pages, str):
        raise SourceIntakeError("source.pages must be text")

    try:
        if source_format == "pdf":
            page_range = parse_page_range(pages)
            parsed = extract_pdf(path, data, page_range=page_range)
        else:
            if pages is not None:
                raise SourceIntakeError("source.pages is valid only for PDF input")
            parsed = _parse_structured_source(
                data, source_format=source_format, filename=path.name, delimiter=delimiter
            )
        canonical = normalize_map(parsed, defaults=defaults)
    except (PdfExtractionError, ParseError, MapWorkflowError) as exc:
        raise SourceIntakeError(str(exc)) from exc

    source_hash = hashlib.sha256(data).hexdigest()
    points = [_oem_point(point, index) for index, point in enumerate(canonical["points"])]
    holds = [
        _portable_hold(hold)
        for hold in canonical.get("holds", ())
        if isinstance(hold, Mapping)
        and str(hold.get("code", "")) not in _DEPLOYMENT_ONLY_HOLDS
    ]
    rejected = canonical.get("rejected_rows", ())
    if isinstance(rejected, Sequence) and not isinstance(
        rejected, (str, bytes, bytearray)
    ) and rejected:
        holds.append(
            {
                "code": "source.rejected-rows-unresolved",
                "severity": "hold",
                "blocking": True,
                "message": "Resolve the rejected source rows as one bounded source exception.",
                "affected_count": len(rejected),
            }
        )
    try:
        oem_map = build_oem_map(
            points,
            source_hash=source_hash,
            source_reference={"filename": path.name, "format": source_format},
            assumptions=canonical.get("assumptions", ()),
            findings=canonical.get("source_findings", ()),
            holds=holds,
        )
    except CompilerContractError as exc:
        raise SourceIntakeError(str(exc)) from exc
    normalized_descriptor = {
        "filename": path.name,
        "format": source_format,
        "source_sha256": source_hash,
        **({"pages": pages} if pages is not None else {}),
        **({"delimiter": delimiter} if delimiter is not None else {}),
        **({"defaults": dict(defaults)} if defaults else {}),
    }
    return oem_map, normalized_descriptor


def _parse_structured_source(
    data: bytes, *, source_format: str, filename: str, delimiter: str | None
) -> Mapping[str, Any]:
    if source_format == "json":
        try:
            decoded = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, Mapping) and any(
            isinstance(decoded.get(field), list) for field in ("records", "points")
        ):
            return {
                **dict(decoded),
                "format": str(decoded.get("format") or "json"),
            }
    return parse_source(
        data,
        source_format=source_format,
        filename=filename,
        delimiter=delimiter,
    )


def bind_selection_template(
    template: Mapping[str, Any], oem_map: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind typed IDs or unique exact names to one derived OEM map hash."""

    if not isinstance(template, Mapping):
        raise SourceIntakeError("selection_template must be an object")
    allowed = {
        "schema_version",
        "requested_measurements",
        "included",
        "suggested",
        "excluded",
    }
    unknown = set(template) - allowed
    if unknown:
        raise SourceIntakeError(
            "selection_template has unknown fields: "
            + ", ".join(sorted(map(str, unknown)))
        )
    if template.get("schema_version") != SELECTION_TEMPLATE_SCHEMA_VERSION:
        raise SourceIntakeError(
            f"selection_template.schema_version must be {SELECTION_TEMPLATE_SCHEMA_VERSION}"
        )
    by_id = {str(point["oem_point_id"]): point for point in oem_map["points"]}
    by_name: dict[str, list[str]] = {}
    for point_id, point in by_id.items():
        name = str(point.get("name") or "").strip().casefold()
        if name:
            by_name.setdefault(name, []).append(point_id)
    candidate: dict[str, Any] = {
        "schema_version": "modbus-user-selection-candidate/v1",
        "oem_map_hash": stable_input_hash(oem_map),
        "requested_measurements": _array(template.get("requested_measurements"), "requested_measurements"),
    }
    for disposition in ("included", "suggested", "excluded"):
        entries = _array(template.get(disposition), disposition)
        candidate[disposition] = [
            _bind_selection_entry(entry, disposition, index, by_id, by_name)
            for index, entry in enumerate(entries)
        ]
    return candidate


def _bind_selection_entry(
    raw: Any,
    disposition: str,
    index: int,
    by_id: Mapping[str, Mapping[str, Any]],
    by_name: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SourceIntakeError(f"selection_template.{disposition}[{index}] must be an object")
    entry = dict(raw)
    point_id = entry.get("oem_point_id")
    exact_name = entry.pop("exact_name", None)
    if point_id not in (None, "") and exact_name not in (None, ""):
        raise SourceIntakeError("selection template entry must use oem_point_id or exact_name, not both")
    if exact_name not in (None, ""):
        matches = by_name.get(str(exact_name).strip().casefold(), ())
        if len(matches) != 1:
            raise SourceIntakeError(
                f"selection exact_name {exact_name!r} must match exactly one OEM point"
            )
        point_id = matches[0]
    if not isinstance(point_id, str) or point_id not in by_id:
        raise SourceIntakeError(
            f"selection_template.{disposition}[{index}] references an unknown OEM point"
        )
    entry["oem_point_id"] = point_id
    return entry


def _oem_point(point: Mapping[str, Any], index: int) -> dict[str, Any]:
    point_id = point.get("logical_point_id", point.get("point_id"))
    if not isinstance(point_id, str) or not point_id:
        point_id = f"source-point-{index + 1}"
    result = {"oem_point_id": point_id}
    for field in _OEM_POINT_FIELDS:
        if field in point:
            result[field] = point[field]
    result["source_refs"] = [_source_ref(point.get("source_location"), index)]
    return result


def _source_ref(value: Any, index: int) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    page = source.get("page")
    if isinstance(page, int) and not isinstance(page, bool) and page >= 0:
        row = source.get("line", source.get("row", 0))
        row = row if isinstance(row, int) and not isinstance(row, bool) and row >= 0 else 0
        reference = {"page_index": page, "row_index": row}
        region = source.get("region")
        if isinstance(region, str) and region:
            reference["region_id"] = region
        return reference
    source_format = str(source.get("format", "record"))
    locator = source.get("row", source.get("index", index))
    return {"record_id": f"{source_format}:{locator}"}


def _portable_hold(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        str(key): item
        for key, item in value.items()
        if str(key) not in {"route_id", "unit_id", "endpoint", "source_excerpt"}
    }
    result.setdefault("severity", "hold")
    result.setdefault("blocking", True)
    return result


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise SourceIntakeError(f"selection_template.{field} must be an array")
    return value


__all__ = [
    "SELECTION_TEMPLATE_SCHEMA_VERSION",
    "SourceIntakeError",
    "bind_selection_template",
    "compile_source_descriptor",
]
