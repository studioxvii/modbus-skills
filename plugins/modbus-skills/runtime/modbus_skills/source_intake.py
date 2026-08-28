"""Deterministic local-source intake for the OEM map compiler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import stat
from typing import Any

from .artifacts import stable_input_hash
from .compiler_contracts import CompilerContractError, build_oem_map, point_evidence_refs
from .map_workflows import MapWorkflowError, normalize_map
from .parsers import ParseError, parse_source
from .pdf_extraction import PdfExtractionError, extract_pdf, parse_page_range
from .pdf_table_extraction import prepare_pdf_records


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
    "source_address",
    "source_register",
    "datatype",
    "word_span",
    "byte_order",
    "byte_order_confirmed",
    "bit_order",
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
    data = _read_bounded_source(path)
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
            parsed = prepare_pdf_records(parsed)
        else:
            if pages is not None:
                raise SourceIntakeError("source.pages is valid only for PDF input")
            parsed = _parse_structured_source(
                data, source_format=source_format, filename=path.name, delimiter=delimiter
            )
        canonical = normalize_map(parsed, defaults=defaults)
    except (PdfExtractionError, ParseError, MapWorkflowError) as exc:
        raise SourceIntakeError(str(exc)) from exc

    source_hash = stable_input_hash(data)
    points = _disambiguate_oem_point_ids(
        [_oem_point(point, index) for index, point in enumerate(canonical["points"])]
    )
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
            source_coverage=(
                parsed.get("source_coverage", {})
                if isinstance(parsed, Mapping)
                else {}
            ),
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


def source_request_identity(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Return a cheap path-free identity without parsing or extracting the source."""

    if not isinstance(descriptor, Mapping):
        raise SourceIntakeError("source descriptor must be an object")
    raw_path = descriptor.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SourceIntakeError("source.path must be non-empty local path text")
    path = Path(raw_path).expanduser()
    data = _read_bounded_source(path)
    result = {str(key): value for key, value in descriptor.items() if key != "path"}
    result.update({"filename": path.name, "source_sha256": stable_input_hash(data)})
    return result


def _read_bounded_source(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceIntakeError(
            "source.path must name an existing non-symlink file"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SourceIntakeError("source.path must name a regular file")
        if metadata.st_size > _MAX_SOURCE_BYTES:
            raise SourceIntakeError(
                f"source exceeds the {_MAX_SOURCE_BYTES} byte intake limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(_MAX_SOURCE_BYTES + 1)
        if len(data) > _MAX_SOURCE_BYTES:
            raise SourceIntakeError(
                f"source exceeds the {_MAX_SOURCE_BYTES} byte intake limit"
            )
        return data
    finally:
        os.close(descriptor)


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
        "mode",
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
    mode = template.get("mode")
    if mode is not None:
        if mode != "all-readable":
            raise SourceIntakeError("selection_template.mode must be all-readable")
        if any(template.get(field) not in (None, []) for field in ("included", "suggested", "excluded")):
            raise SourceIntakeError(
                "selection_template.mode cannot be combined with explicit dispositions"
            )
        included: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for point in oem_map["points"]:
            evidence_refs = point_evidence_refs(point)
            if not evidence_refs:
                raise SourceIntakeError(
                    f"OEM point {point.get('oem_point_id')!r} has no usable source evidence"
                )
            entry = {
                "oem_point_id": point["oem_point_id"],
                "reason": "Included by the explicit all-readable selection mode.",
                "evidence_refs": evidence_refs,
            }
            if point.get("access") == "write-only":
                excluded.append(
                    {
                        **entry,
                        "reason": "Excluded because the OEM map marks this point write-only.",
                    }
                )
            else:
                included.append(
                    {
                        **entry,
                        "matched_intent": "all documented Modbus read points",
                        "match_quality": "exact",
                        "selection_basis": "typed-all-readable",
                    }
                )
        candidate.update({"included": included, "suggested": [], "excluded": excluded})
        return candidate
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


def _disambiguate_oem_point_ids(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every OEM point in this map a unique ``oem_point_id``.

    ``normalize_map`` intentionally assigns two source rows the same generated
    ``logical_point_id`` when the vendor source repeats a label (e.g. a
    register list that names several distinct addresses "Reactive Energy
    Received") and raises a ``point.generated-logical-id-collision`` hold so a
    human can supply explicit unique IDs. That hold stays in the OEM map's
    ``holds`` list, but the OEM map contract requires one distinct ID per
    point, so append a stable, order-based suffix to every id after the first
    in a colliding group rather than dropping points or failing the whole
    compile.
    """

    seen: dict[str, int] = {}
    for point in points:
        point_id = point["oem_point_id"]
        occurrence = seen.get(point_id, 0)
        seen[point_id] = occurrence + 1
        if occurrence:
            point["oem_point_id"] = f"{point_id}-dup{occurrence + 1}"
    return points


def _oem_point(point: Mapping[str, Any], index: int) -> dict[str, Any]:
    point_id = point.get("logical_point_id", point.get("point_id"))
    if not isinstance(point_id, str) or not point_id:
        point_id = f"source-point-{index + 1}"
    result = {"oem_point_id": point_id}
    for field in _OEM_POINT_FIELDS:
        if field in point:
            result[field] = point[field]
    unmapped = point.get("unmapped_fields")
    source_register = unmapped.get("source_register") if isinstance(unmapped, Mapping) else None
    source_address = point.get("source_address")
    if source_register not in (None, ""):
        result["source_register"] = str(source_register)
    elif isinstance(source_address, Mapping) and source_address.get("raw") not in (None, ""):
        result["source_register"] = str(source_address["raw"])
    source_refs = [_source_ref(point.get("source_location"), index)]
    for claim in point.get("source_claims", ()):
        if not isinstance(claim, Mapping):
            continue
        locator = claim.get("source_locator", claim.get("source"))
        if not isinstance(locator, Mapping):
            continue
        reference = _source_ref(locator, index)
        if reference not in source_refs:
            source_refs.append(reference)
    result["source_refs"] = source_refs
    field_evidence = _source_field_evidence(point, result["source_refs"][0])
    if field_evidence:
        result["source_field_evidence"] = field_evidence
    return result


def _disambiguate_oem_point_ids(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every OEM point in this map a unique ``oem_point_id``.

    ``normalize_map`` intentionally assigns two source rows the same generated
    ``logical_point_id`` when the vendor source repeats a label (e.g. two
    registers both named "Reserved") and raises a
    ``point.generated-logical-id-collision`` hold so a human can supply
    explicit unique IDs. That hold stays in the OEM map's ``holds`` list, but
    the OEM map contract requires one distinct ID per point, so append a
    stable, order-based suffix to every id after the first in a colliding
    group rather than dropping points or failing the whole compile.
    """

    seen: dict[str, int] = {}
    for point in points:
        point_id = point["oem_point_id"]
        occurrence = seen.get(point_id, 0)
        seen[point_id] = occurrence + 1
        if occurrence:
            point["oem_point_id"] = f"{point_id}-dup{occurrence + 1}"
    return points


_CLAIM_TO_POINT_FIELD = {
    "address": "protocol_offset",
    "protocol_offset": "protocol_offset",
    "access": "access",
    "format": "datatype",
    "datatype": "datatype",
    "units": "engineering_unit",
    "engineering_unit": "engineering_unit",
    "scale": "scale",
    "word_count": "word_span",
    "word_span": "word_span",
    "description": "description",
}


def _source_field_evidence(
    point: Mapping[str, Any], source_ref: Mapping[str, Any]
) -> list[dict[str, Any]]:
    claims = point.get("source_claims", ())
    raw_claims: dict[str, Mapping[str, Any]] = {}
    if isinstance(claims, Sequence) and not isinstance(
        claims, (str, bytes, bytearray)
    ):
        for claim in claims:
            if isinstance(claim, Mapping) and isinstance(claim.get("field"), str):
                target = _CLAIM_TO_POINT_FIELD.get(str(claim["field"]))
                if target is not None:
                    raw_claims.setdefault(target, claim)
    raw_evidence = point.get("source_evidence", ())
    evidence = (
        raw_evidence
        if isinstance(raw_evidence, Sequence)
        and not isinstance(raw_evidence, (str, bytes, bytearray))
        else ()
    )
    normalized_evidence: dict[str, Mapping[str, Any]] = {}
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        source_field = str(item.get("source_field", item.get("field", "")))
        target = _CLAIM_TO_POINT_FIELD.get(
            str(item.get("field", "")), _CLAIM_TO_POINT_FIELD.get(source_field)
        )
        if target is not None:
            normalized_evidence.setdefault(target, item)
    source_ref_text = point_evidence_refs({"source_refs": [source_ref]})[0]
    result: list[dict[str, Any]] = []
    for target in sorted(set(raw_claims) | set(normalized_evidence)):
        item = normalized_evidence.get(target, {})
        source_field = str(item.get("source_field", target))
        raw_claim = raw_claims.get(target, {})
        raw_value = raw_claim.get(
            "raw_value", raw_claim.get("value", item.get("source_value"))
        )
        normalized_value = point.get(target)
        if raw_value in (None, "") and normalized_value in (None, ""):
            continue
        locator = raw_claim.get("source_locator", raw_claim.get("source"))
        claim_ref = (
            _source_ref(locator, 0) if isinstance(locator, Mapping) else source_ref
        )
        claim_ref_text = point_evidence_refs({"source_refs": [claim_ref]})[0]
        result.append(
            {
                "field": target,
                "raw_header": str(
                    raw_claim.get("raw_header", source_field or target)
                ),
                "raw_value": raw_value,
                "normalized_value": normalized_value,
                "source_ref": claim_ref_text,
                "status": (
                    "unresolved"
                    if raw_value not in (None, "") and normalized_value in (None, "")
                    else "contradiction"
                    if item.get("value", normalized_value) != normalized_value
                    else "confirmed"
                ),
            }
        )
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
    "source_request_identity",
]
