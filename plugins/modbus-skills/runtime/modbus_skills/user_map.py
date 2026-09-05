"""Compile a validated user selection into a compact offline map bundle."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import csv
import html
import io
import re
from typing import Any

from .artifacts import artifact_envelope, stable_input_hash
from .compiler_contracts import (
    CompilerContractError,
    build_user_map,
    build_user_selection,
    point_evidence_refs,
    validate_oem_map,
    validate_user_selection,
)
from .decision_packets import build_selection_decision_packet
from .exporters import stable_json, write_csv_row


USER_MAP_BUNDLE_MANIFEST_SCHEMA_VERSION = "modbus-user-map-bundle-manifest/v1"
_DISPOSITIONS = ("included", "suggested", "excluded")
_AMBIGUOUS_MATCHES = frozenset({"ambiguous", "near", "weak"})
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_POINT_FIELDS = (
    "oem_point_id",
    "name",
    "description",
    "area",
    "protocol_offset",
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
    "source_refs",
)
_CSV_FIELDS = (
    "requested_measurement",
    "group",
    "alias",
    "oem_point_id",
    "name",
    "source_register",
    "area",
    "protocol_offset",
    "datatype",
    "word_span",
    "engineering_unit",
    "confidence",
    "reason",
    "evidence_refs",
    "description",
    "display_name",
)


class UserMapError(ValueError):
    """Raised when selection intent is stale, ambiguous, or unsafe."""


def validate_selection_candidate(
    oem_map: Mapping[str, Any], candidate: Mapping[str, Any] | Any
) -> dict[str, Any]:
    """Validate typed dispositions and return a hash-bound selection artifact.

    Near or ambiguous generated matches are downgraded to suggestions.  A later
    typed override is the only way to promote those candidates.
    """

    try:
        validate_oem_map(oem_map)
    except CompilerContractError as exc:
        raise UserMapError(str(exc)) from exc
    if not isinstance(candidate, Mapping):
        raise UserMapError("selection candidate must be a typed object")
    allowed = {
        "schema_version",
        "oem_map_hash",
        "requested_measurements",
        "included",
        "suggested",
        "excluded",
    }
    unknown = set(candidate) - allowed
    if unknown:
        raise UserMapError("selection candidate has unknown fields: " + ", ".join(sorted(map(str, unknown))))
    if candidate.get("schema_version") not in (None, "modbus-user-selection-candidate/v1"):
        raise UserMapError("selection candidate schema_version is unsupported")
    expected_hash = stable_input_hash(oem_map)
    if candidate.get("oem_map_hash") != expected_hash:
        raise UserMapError("selection candidate OEM map hash does not match the supplied OEM map")
    measurements = _text_array(candidate.get("requested_measurements"), "requested_measurements")
    known = {str(point["oem_point_id"]): point for point in oem_map["points"]}
    normalized: dict[str, list[dict[str, Any]]] = {field: [] for field in _DISPOSITIONS}
    seen: set[str] = set()
    for disposition in _DISPOSITIONS:
        for index, raw in enumerate(_array(candidate.get(disposition), disposition)):
            entry = _selection_entry(raw, disposition, index, known)
            point_id = entry["oem_point_id"]
            if point_id in seen:
                raise UserMapError(f"OEM point {point_id!r} has more than one disposition")
            seen.add(point_id)
            target = disposition
            if disposition == "included" and entry.get("match_quality") in _AMBIGUOUS_MATCHES:
                target = "suggested"
            normalized[target].append(entry)
    try:
        return build_user_selection(
            oem_map,
            requested_measurements=measurements,
            included=normalized["included"],
            suggested=normalized["suggested"],
            excluded=normalized["excluded"],
        )
    except CompilerContractError as exc:
        raise UserMapError(str(exc)) from exc


def apply_selection_override(
    oem_map: Mapping[str, Any],
    selection: Mapping[str, Any],
    overrides: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply explicit typed promotion/exclusion decisions to suggestions."""

    try:
        validate_user_selection(selection, oem_map)
    except CompilerContractError as exc:
        raise UserMapError(str(exc)) from exc
    buckets = {
        disposition: {item["oem_point_id"]: dict(item) for item in selection[disposition]}
        for disposition in _DISPOSITIONS
    }
    seen: set[str] = set()
    for index, raw in enumerate(_array(overrides, "selection overrides")):
        if not isinstance(raw, Mapping):
            raise UserMapError(f"selection overrides[{index}] must be an object")
        unknown = set(raw) - {"oem_point_id", "disposition", "reason", "evidence_refs"}
        if unknown:
            raise UserMapError(f"selection overrides[{index}] has unknown fields: " + ", ".join(sorted(map(str, unknown))))
        point_id = _text(raw.get("oem_point_id"), f"selection overrides[{index}].oem_point_id")
        if point_id in seen:
            raise UserMapError(f"selection override repeats OEM point {point_id!r}")
        seen.add(point_id)
        if point_id not in buckets["suggested"]:
            raise UserMapError(f"selection override can only change suggested point {point_id!r}")
        target = _text(raw.get("disposition"), f"selection overrides[{index}].disposition")
        if target not in {"included", "excluded"}:
            raise UserMapError("selection override disposition must be included or excluded")
        evidence_refs = _text_array(raw.get("evidence_refs"), "selection override evidence_refs")
        if not evidence_refs:
            raise UserMapError("selection override needs at least one evidence reference")
        entry = buckets["suggested"].pop(point_id)
        entry.update(
            {
                "reason": _text(raw.get("reason"), f"selection overrides[{index}].reason"),
                "evidence_refs": evidence_refs,
                "selection_basis": "typed-override",
                "match_quality": "override",
            }
        )
        buckets[target][point_id] = entry
    try:
        return build_user_selection(
            oem_map,
            requested_measurements=selection["requested_measurements"],
            included=list(buckets["included"].values()),
            suggested=list(buckets["suggested"].values()),
            excluded=list(buckets["excluded"].values()),
            assumptions=selection.get("assumptions", ()),
            findings=selection.get("findings", ()),
            holds=selection.get("holds", ()),
        )
    except CompilerContractError as exc:
        raise UserMapError(str(exc)) from exc


def compile_user_map_bundle(
    oem_map: Mapping[str, Any],
    selection_candidate: Mapping[str, Any],
    *,
    case_id: str,
    selection_decision_resolved: bool = False,
) -> dict[str, Any]:
    """Return an offline user-map bundle or one bounded selection packet."""

    normalized_case_id = _case_id(case_id)
    selection = validate_selection_candidate(oem_map, selection_candidate)
    resolved_empty = selection_decision_resolved and not selection["suggested"]
    if not selection["included"] and not resolved_empty:
        candidates = selection["suggested"] or selection["excluded"]
        if not candidates:
            candidates = [
                {
                    "oem_point_id": point["oem_point_id"],
                    "evidence_refs": point_evidence_refs(point),
                }
                for point in oem_map["points"]
            ]
        evidence = sorted(
            {
                reference
                for entry in candidates
                for reference in (
                    entry.get("evidence_refs") or point_evidence_refs(
                        next(point for point in oem_map["points"] if point["oem_point_id"] == entry["oem_point_id"])
                    )
                )
            }
        )
        return {
            "status": "needs-selection-decision",
            "selection": selection,
            "user_map": None,
            "human_summary": "",
            "json": "",
            "csv": "",
            "manifest": None,
            "decision_packet": build_selection_decision_packet(
                case_id=normalized_case_id,
                source_hash=oem_map["source_sha256"],
                oem_map_hash=stable_input_hash(oem_map),
                candidate_ids=[entry["oem_point_id"] for entry in candidates],
                evidence_refs=evidence,
            ),
        }

    point_index = {point["oem_point_id"]: point for point in oem_map["points"]}
    rendered_points = [
        _render_point(point_index[entry["oem_point_id"]], entry)
        for entry in selection["included"]
    ]
    included_ids = {point["oem_point_id"] for point in rendered_points}
    selected_holds, annex_holds = _partition_holds(oem_map.get("holds", ()), included_ids)
    selected_holds = _group_holds(selected_holds)
    annex = [
        {
            "kind": "excluded",
            "oem_point_id": entry["oem_point_id"],
            "reason": entry["reason"],
            "evidence_refs": list(entry.get("evidence_refs", ())),
        }
        for entry in selection["excluded"]
    ]
    annex.extend({"kind": "unselected-hold", **hold} for hold in annex_holds)
    try:
        user_map = build_user_map(
            oem_map,
            selection,
            points=rendered_points,
            exception_annex=annex,
            assumptions=_source_datatype_notes(oem_map, included_ids),
            holds=selected_holds,
        )
    except CompilerContractError as exc:
        raise UserMapError(str(exc)) from exc
    human_summary = render_human_summary(user_map, selection)
    json_text = stable_json(user_map)
    csv_text = render_user_map_csv(user_map)
    manifest = artifact_envelope(
        {
            "case_id": normalized_case_id,
            "point_count": len(rendered_points),
            "artifacts": [
                {"name": "user-map.md", "sha256": stable_input_hash(human_summary.encode("utf-8"))},
                {"name": "user-map.json", "sha256": stable_input_hash(json_text.encode("utf-8"))},
                {"name": "user-map.csv", "sha256": stable_input_hash(csv_text.encode("utf-8"))},
            ],
        },
        schema_version=USER_MAP_BUNDLE_MANIFEST_SCHEMA_VERSION,
        input_hashes={
            "oem_map": stable_input_hash(oem_map),
            "selection": stable_input_hash(selection),
        },
    )
    return {
        "status": "offline-complete",
        "selection": selection,
        "user_map": user_map,
        "human_summary": human_summary,
        "json": json_text,
        "csv": csv_text,
        "manifest": manifest,
        "decision_packet": None,
    }


def render_human_summary(user_map: Mapping[str, Any], selection: Mapping[str, Any]) -> str:
    """Render the compact human hierarchy without duplicating included rows."""

    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for point in user_map["points"]:
        groups[str(point.get("requested_measurement") or "Other")].append(point)
    lines = ["# User map", "", "## Included"]
    if not user_map["points"]:
        lines.extend(["", "No points selected. Exclusions are retained below; no device read or target workflow is requested by this empty map."])
    for measurement in sorted(groups, key=str.casefold):
        lines.extend(["", f"### {measurement}"])
        for point in groups[measurement]:
            alias = f" as `{point['alias']}`" if point.get("alias") else ""
            address = _address_label(point)
            label = _display_label(
                point.get("display_name"), point.get("alias"), point.get("name"),
                point.get("description"), point["oem_point_id"],
            )
            lines.append(f"- `{point['oem_point_id']}`{alias} — {label} ({address})")
    notes = [note for note in user_map.get("assumptions", ())
             if note.get("code") == "source-datatype-definition"]
    if notes:
        lines.extend(["", "## Source datatype definitions", "",
                      "Source context only; these definitions do not configure decoding. "
                      "Distinct definitions are retained without resolving conflicts.", ""])
        for note in notes:
            location = note["source_location"]
            evidence = f"{location['sheet']}!{location['datatype_cell']}, {location['definition_cell']}"
            lines.append(
                f"- {_note_markdown(note['source_datatype'])}: {_note_markdown(note['definition'])} "
                f"({_note_markdown(evidence)}; "
                f"{len(note['matching_datatype_evidence'])} included datatype evidence records)"
            )
    lines.extend(["", "## Suggestions"])
    if selection["suggested"]:
        for entry in selection["suggested"]:
            lines.append(f"- `{entry['oem_point_id']}` — {entry['reason']}")
    else:
        lines.append("- None")
    lines.extend(["", "## Blocking exceptions"])
    if user_map["holds"]:
        for hold in user_map["holds"]:
            affected = hold.get("affected_count")
            suffix = f" ({affected} points)" if isinstance(affected, int) and affected > 1 else ""
            lines.append(f"- {hold.get('code', 'UNRESOLVED')}: {hold.get('message', hold.get('reason', 'Review required'))}{suffix}")
    else:
        lines.append("- None")
    scale_notes: dict[str, dict[str, Any]] = {}
    scale_subjects: set[str] = set()
    possible_scale_scope = False
    missing_scale_column = False
    for hold in user_map["holds"]:
        if hold.get("code") != "source.scale-conversion-unresolved":
            continue
        scale_subjects.update(hold.get("subject_ids", ()))
        details = hold.get("details", {})
        possible_scale_scope |= details.get("possible_scope", False) is True
        for evidence in details.get("scale_evidence", ()):
            missing_scale_column |= evidence.get("scale_source") == "absent-column"
            for note in evidence.get("conversion_notes", ()):
                key = stable_input_hash({"literal": note.get("literal"), "scope": note.get("scope")})
                grouped_note = scale_notes.setdefault(key, {"literal": note.get("literal"), "locations": {}})
                locator = note.get("source_locator", {})
                grouped_note["locations"][stable_input_hash(locator)] = locator
    if scale_notes:
        lines.extend(["", "## Unresolved source scaling", "",
                      f"Engineering scaling is withheld for {len(scale_subjects)} selected points. "
                      + ("The possible workbook scope is conservative; this does not mean every raw factor is wrong. "
                         if possible_scale_scope else "")
                      + "Resolve the shared source rule once, not point by point. "
                      + ("A bound conversion statement has no Scale column; no source cell is invented. "
                         if missing_scale_column else "")
                      + "Available Scale-cell evidence and affected identities are retained in [the JSON map](user-map.json).", ""])
        for key in sorted(scale_notes):
            note = scale_notes[key]
            locations = [f"{locator.get('sheet', '')}: row {locator.get('row', '')}, column {locator.get('column', '')}"
                         for _, locator in sorted(note["locations"].items())]
            lines.append(f"- {_note_markdown(str(note.get('literal', '')))} ({_note_markdown('; '.join(locations))})")
    lines.extend(["", "## Exclusions and evidence annex"])
    if user_map["exception_annex"]:
        annex_groups: list[tuple[Mapping[str, Any], int]] = []
        group_indexes: dict[str, int] = {}
        for item in user_map["exception_annex"]:
            # Only repeated unselected holds are presentation groups. Preserve
            # semantic differences and every original record in the JSON map.
            key = None
            if item.get("kind") == "unselected-hold":
                excluded_keys = {"source", "point_ids", "subject_ids"}
                if item.get("code") == "source.scale-conversion-unresolved":
                    # Full per-row scale/note evidence remains in the JSON
                    # annex; it must not turn one shared issue into N prose rows.
                    excluded_keys.add("details")
                key = stable_input_hash({k: v for k, v in item.items() if k not in excluded_keys})
            if key is not None and key in group_indexes:
                index = group_indexes[key]
                first, count = annex_groups[index]
                annex_groups[index] = (first, count + 1)
            else:
                if key is not None:
                    group_indexes[key] = len(annex_groups)
                annex_groups.append((item, 1))
        for item, count in annex_groups:
            point_id = item.get("oem_point_id")
            code = item.get("code")
            label = " / ".join(str(value) for value in (point_id, code) if value) or "unselected evidence"
            reason = item.get("reason", item.get("message", "Retained outside selected output"))
            suffix = f" ({count} unselected source records)" if count > 1 else ""
            lines.append(f"- {label}: {reason}{suffix}")
        if any(count > 1 for _item, count in annex_groups):
            lines.extend(["", "Individual records and source locations remain in [the JSON map](user-map.json)."])
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def render_user_map_csv(user_map: Mapping[str, Any]) -> str:
    """Render one deterministic spreadsheet-safe row per included point."""

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    write_csv_row(writer, _CSV_FIELDS)
    for point in sorted(user_map["points"], key=_point_sort_key):
        values = []
        for field in _CSV_FIELDS:
            value = point.get(field, "")
            if field == "evidence_refs":
                value = ";".join(str(item) for item in value)
            values.append(value)
        write_csv_row(writer, values)
    return stream.getvalue()


def _note_markdown(value: str) -> str:
    """Keep literal source context from becoming Markdown or HTML instructions."""
    text = html.escape(" ".join(value.split()), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def _source_datatype_notes(
    oem_map: Mapping[str, Any], included_ids: set[str]
) -> list[dict[str, Any]]:
    """Retain explicit XLSX dictionary context, never infer executable enums.

    Only the parser's literal, non-formula datatype dictionary assumptions qualify.
    Matching uses included points' raw datatype evidence, not names or normalized
    aliases. A workbook may contain conflicting dictionaries: all matching source
    contexts survive, with no inferred worksheet precedence or label selection.
    """
    if oem_map.get("source_reference", {}).get("format") != "xlsx":
        return []
    matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for point in oem_map["points"]:
        if point["oem_point_id"] not in included_ids:
            continue
        for evidence in point.get("source_field_evidence", ()):
            raw = evidence.get("raw_value")
            if (evidence.get("field") == "datatype" and evidence.get("status") == "confirmed"
                    and evidence.get("normalized_value") == point.get("datatype")
                    and isinstance(raw, str) and raw.strip()):
                match = {"oem_point_id": point["oem_point_id"], **evidence}
                if match not in matches[raw.strip().casefold()]:
                    matches[raw.strip().casefold()].append(match)

    def cell(column: int, row: int) -> str:
        letters = ""
        column += 1
        while column:
            column, remainder = divmod(column - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{row}"

    notes: list[dict[str, Any]] = []
    for assumption in oem_map.get("assumptions", ()):
        if assumption.get("code") != "excluded_xlsx_datatype_legend":
            continue
        rows = assumption.get("source_rows")
        sheet = assumption.get("sheet")
        if not isinstance(rows, list) or len(rows) < 3 or not isinstance(sheet, str):
            continue
        headers = rows[1].get("values") if isinstance(rows[1], Mapping) else None
        if not isinstance(headers, list):
            continue
        normalized = [re.sub(r"[^a-z]", "", value.lower()) if isinstance(value, str) else ""
                      for value in headers]
        datatype_columns = [i for i, value in enumerate(normalized)
                            if value in {"datatype", "modbusdatatype"}]
        description_columns = [i for i, value in enumerate(normalized) if value == "description"]
        if len(datatype_columns) != 1 or len(description_columns) != 1:
            continue
        datatype_column, description_column = datatype_columns[0], description_columns[0]
        for record in rows[2:]:
            if not isinstance(record, Mapping):
                continue
            values, row = record.get("values"), record.get("row")
            if (not isinstance(values, list) or type(row) is not int or row < 1
                    or len(values) <= max(datatype_column, description_column)):
                continue
            datatype, definition = values[datatype_column], values[description_column]
            if not isinstance(datatype, str) or not isinstance(definition, str) or not definition.strip():
                continue
            evidence = matches.get(datatype.strip().casefold())
            if not evidence:
                continue
            note = {
                "code": "source-datatype-definition",
                "interpretation": "source-context-only; not executable decoding or resolved enum labels",
                "source_datatype": datatype,
                "definition": definition,
                "source_location": {"format": "xlsx", "sheet": sheet, "row": row,
                                    "datatype_cell": cell(datatype_column, row),
                                    "definition_cell": cell(description_column, row)},
                "source_headers": {"datatype": headers[datatype_column],
                                   "definition": headers[description_column]},
                "matching_datatype_evidence": evidence,
            }
            if note not in notes:
                notes.append(note)
    return notes


def _selection_entry(
    value: Any,
    disposition: str,
    index: int,
    known: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    entry = validate_selection_entry_structure(value, disposition, index)
    if entry["oem_point_id"] not in known:
        raise UserMapError(f"selection references unknown OEM point: {entry['oem_point_id']}")
    return entry


def validate_selection_entry_structure(
    value: Any, disposition: str, index: int, *, selector: str = "oem_point_id",
) -> dict[str, Any]:
    """Validate entry fields without manufacturing or resolving a source point."""
    if selector not in {"oem_point_id", "exact_name"}:
        raise UserMapError("selection selector must be oem_point_id or exact_name")
    if not isinstance(value, Mapping):
        raise UserMapError(f"{disposition}[{index}] must be an object")
    allowed = {
        selector,
        "reason",
        "confidence",
        "group",
        "alias",
        "matched_intent",
        "match_quality",
        "selection_basis",
        "evidence_refs",
    }
    unknown = set(value) - allowed
    if unknown:
        raise UserMapError(f"{disposition}[{index}] has unknown fields: " + ", ".join(sorted(map(str, unknown))))
    point_id = _text(value.get(selector), f"{disposition}[{index}].{selector}")
    entry = {key: value[key] for key in allowed if key in value}
    entry[selector] = point_id
    confidence = entry.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise UserMapError("selection confidence must be from 0 through 1")
    entry["reason"] = _text(value.get("reason"), f"{disposition}[{index}].reason")
    evidence_refs = _text_array(value.get("evidence_refs"), f"{disposition}[{index}].evidence_refs")
    if not evidence_refs:
        raise UserMapError(f"{disposition}[{index}] needs at least one evidence reference")
    entry["evidence_refs"] = evidence_refs
    if disposition == "included":
        entry["matched_intent"] = _text(value.get("matched_intent"), f"{disposition}[{index}].matched_intent")
        quality = _text(value.get("match_quality"), f"{disposition}[{index}].match_quality").lower()
        if quality not in {"exact", "near", "ambiguous", "weak", "override"}:
            raise UserMapError(f"{disposition}[{index}].match_quality is unsupported")
        entry["match_quality"] = quality
    elif "match_quality" in entry:
        entry["match_quality"] = _text(entry["match_quality"], f"{disposition}[{index}].match_quality").lower()
    return entry


def _display_label(*values: Any) -> str:
    """Choose a nonblank display label without changing source field values."""
    return next((value for value in values if isinstance(value, str) and value.strip()), "")


def _render_point(oem_point: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    result = {field: oem_point[field] for field in _POINT_FIELDS if field in oem_point}
    result.update(
        {
            "display_name": _display_label(
                entry.get("alias"), oem_point.get("name"),
                oem_point.get("description"), oem_point["oem_point_id"],
            ),
            "alias": entry.get("alias"),
            "group": entry.get("group") or entry.get("matched_intent") or "Other",
            "requested_measurement": entry.get("matched_intent") or "Other",
            "selection_reason": entry["reason"],
            "confidence": entry.get("confidence"),
            "evidence_refs": list(entry.get("evidence_refs", ())),
        }
    )
    return result


def _partition_holds(
    holds: Sequence[Any], included_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    annex: list[dict[str, Any]] = []
    for raw in holds:
        if not isinstance(raw, Mapping):
            continue
        hold = dict(raw)
        point_ids = {
            str(value)
            for value in (
                hold.get("oem_point_id"),
                hold.get("point_id"),
            )
            if value not in (None, "")
        }
        raw_subjects = hold.get("subject_ids", ())
        if isinstance(raw_subjects, Sequence) and not isinstance(raw_subjects, (str, bytes, bytearray)):
            point_ids.update(str(value) for value in raw_subjects)
        raw_point_ids = hold.get("point_ids", ())
        if isinstance(raw_point_ids, Sequence) and not isinstance(raw_point_ids, (str, bytes, bytearray)):
            point_ids.update(str(value) for value in raw_point_ids)
        if not point_ids or point_ids & included_ids:
            selected.append(hold)
        else:
            annex.append(hold)
    return selected, annex


def _group_holds(holds: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, bool], dict[str, Any]] = {}
    for raw in holds:
        code = str(raw.get("code", "UNRESOLVED"))
        message = str(raw.get("message", raw.get("reason", "Review required")))
        if code == "address.area-unresolved":
            code = "point.area-unresolved"
            message = "Declare the Modbus area before address conversion."
        elif code == "point.area-unresolved":
            message = "Declare the Modbus area before address conversion."
        field = str(raw.get("field", ""))
        severity = str(raw.get("severity", "hold"))
        blocking = raw.get("blocking", True) is not False
        key = (code, message, field, severity, blocking)
        group = groups.setdefault(
            key,
            {
                "code": code,
                "message": message,
                **({"field": field} if field else {}),
                "severity": severity,
                "blocking": blocking,
                "affected_count": 0,
                "_subject_ids": set(),
                "_occurrences": 0,
            },
        )
        group["_occurrences"] += 1
        if code == "source.scale-conversion-unresolved" and isinstance(raw.get("details"), Mapping):
            details = group.setdefault("details", {"scale_evidence": [], "possible_scope": False})
            details["possible_scope"] |= raw["details"].get("possible_scope") is True
            for evidence in raw["details"].get("scale_evidence", ()):
                if isinstance(evidence, Mapping):
                    details["scale_evidence"].append({**evidence, "point_ids": list(raw.get("point_ids", ()))})
        subject_ids = group["_subject_ids"]
        for value in raw.get("point_ids", ()):
            subject_ids.add(str(value))
        for field_name in ("oem_point_id", "point_id"):
            if raw.get(field_name) not in (None, ""):
                subject_ids.add(str(raw[field_name]))
    result = []
    for key in sorted(groups):
        group = groups[key]
        subject_ids = sorted(group.pop("_subject_ids"))
        group["subject_ids"] = subject_ids
        group["affected_count"] = len(subject_ids) or group.pop("_occurrences")
        group.pop("_occurrences", None)
        result.append(group)
    return result


def _point_sort_key(point: Mapping[str, Any]) -> tuple[str, str, int, str]:
    offset = point.get("protocol_offset")
    if not isinstance(offset, int) or isinstance(offset, bool):
        match = re.match(r"\d+", str(point.get("source_register", "")))
        offset = int(match.group()) if match else 65_536
    return (
        str(point.get("requested_measurement", "")).casefold(),
        str(point.get("area", "")),
        offset,
        str(point.get("oem_point_id", "")),
    )


def _address_label(point: Mapping[str, Any]) -> str:
    area = point.get("area") or "area unresolved"
    offset = point.get("protocol_offset")
    if offset is not None:
        return f"{area} offset {offset}"
    source_register = point.get("source_register")
    if source_register not in (None, ""):
        return f"source register {source_register}; {area}"
    return f"{area}, offset unresolved"


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, memoryview)):
        raise UserMapError(f"{field} must be an array")
    return list(value)


def _text_array(value: Any, field: str) -> list[str]:
    return sorted({_text(item, field) for item in _array(value, field)})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UserMapError(f"{field} must be non-empty text")
    return value.strip()


def _case_id(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_CASE_ID.fullmatch(value):
        raise UserMapError("case_id must be a safe identifier")
    return value


__all__ = [
    "USER_MAP_BUNDLE_MANIFEST_SCHEMA_VERSION",
    "UserMapError",
    "apply_selection_override",
    "compile_user_map_bundle",
    "render_human_summary",
    "render_user_map_csv",
    "validate_selection_candidate",
    "validate_selection_entry_structure",
]
