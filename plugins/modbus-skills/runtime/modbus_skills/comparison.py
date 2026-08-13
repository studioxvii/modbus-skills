"""Deterministic semantic comparison for canonical Modbus maps."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Iterable


IDENTITY_FIELDS = (
    "route_id",
    "unit_id",
    "area",
    "protocol_offset",
    "logical_point_id",
)

DEFAULT_COMPARE_FIELDS = (
    "name",
    "description",
    "datatype",
    "word_span",
    "byte_order",
    "byte_order_confirmed",
    "bit_order",
    "scale",
    "engineering_offset",
    "engineering_unit",
    "access",
    "source_include",
    "source_reviewed",
    "minimum",
    "maximum",
    "expected_interval_seconds",
    "normalization_status",
)


class MapComparisonError(ValueError):
    """Raised when a map cannot be compared deterministically."""


def _points(value: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        candidate = value.get("points", value.get("records", value.get("registers", ())))
    else:
        candidate = value
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes, bytearray)):
        raise MapComparisonError("Map must contain a points, records, or registers array.")
    output = [point for point in candidate if isinstance(point, Mapping)]
    if len(output) != len(candidate):
        raise MapComparisonError("Every compared point must be an object.")
    return output


def _value(point: Mapping[str, Any], field: str) -> Any:
    if field == "logical_point_id":
        return point.get("logical_point_id", point.get("point_id", point.get("id")))
    if field == "protocol_offset":
        direct = point.get("protocol_offset", point.get("pdu_offset"))
        if direct is not None:
            return direct
        address = point.get("address")
        return address.get("protocol_offset", address.get("pdu_offset")) if isinstance(address, Mapping) else None
    if field == "area":
        direct = point.get("area")
        if direct is not None:
            return direct
        address = point.get("address")
        return address.get("area") if isinstance(address, Mapping) else None
    if field == "byte_order":
        return point.get("byte_order", point.get("byte_layout"))
    if field == "word_span":
        return point.get(
            "word_span", point.get("word_count", point.get("register_width"))
        )
    if field == "engineering_offset":
        return point.get("engineering_offset", point.get("offset"))
    return point.get(field)


def composite_identity(point: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the full identity used by the public map contract."""

    return tuple(_value(point, field) for field in IDENTITY_FIELDS)


def identity_dict(identity: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(IDENTITY_FIELDS, identity))


def _sort_key(identity: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple("" if value is None else str(value) for value in identity)


def _move_identity(identity: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the stable logical identity used to pair physical moves."""

    return (identity.get("logical_point_id"),)


def _index(points: Iterable[Mapping[str, Any]]) -> dict[tuple[Any, ...], list[Mapping[str, Any]]]:
    output: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for point in points:
        output[composite_identity(point)].append(point)
    return dict(output)


def compare_maps(
    before: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    after: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    compare_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compare maps by composite identity and report field-level changes.

    Duplicate identities are reported as ambiguous. They are not silently
    collapsed into one point.
    """

    fields = tuple(compare_fields or DEFAULT_COMPARE_FIELDS)
    if any(not isinstance(field, str) or not field for field in fields):
        raise MapComparisonError("Comparison field names must be non-empty strings.")
    old_index = _index(_points(before))
    new_index = _index(_points(after))
    all_identities = sorted(set(old_index) | set(new_index), key=_sort_key)

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []

    for identity in all_identities:
        old_points = old_index.get(identity, [])
        new_points = new_index.get(identity, [])
        identity_value = identity_dict(identity)
        if len(old_points) > 1 or len(new_points) > 1:
            duplicates.append(
                {
                    "identity": identity_value,
                    "before_count": len(old_points),
                    "after_count": len(new_points),
                    "message": "Composite identity is duplicated; comparison is ambiguous.",
                }
            )
            continue
        if not old_points:
            added.append({"identity": identity_value, "point": dict(new_points[0])})
            continue
        if not new_points:
            removed.append({"identity": identity_value, "point": dict(old_points[0])})
            continue

        old_point = old_points[0]
        new_point = new_points[0]
        field_changes = []
        for field in fields:
            old_value = _value(old_point, field)
            new_value = _value(new_point, field)
            if old_value != new_value:
                field_changes.append({"field": field, "before": old_value, "after": new_value})
        if field_changes:
            changed.append({"identity": identity_value, "changes": field_changes})
        else:
            unchanged.append({"identity": identity_value})

    removed_by_move: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    added_by_move: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in removed:
        removed_by_move[_move_identity(item["identity"])].append(item)
    for item in added:
        added_by_move[_move_identity(item["identity"])].append(item)

    paired_removed: set[int] = set()
    paired_added: set[int] = set()
    for key in sorted(set(removed_by_move) & set(added_by_move), key=_sort_key):
        old_matches = removed_by_move[key]
        new_matches = added_by_move[key]
        if any(value in (None, "") for value in key) or len(old_matches) != 1 or len(new_matches) != 1:
            continue
        old_item = old_matches[0]
        new_item = new_matches[0]
        old_point = old_item["point"]
        new_point = new_item["point"]
        field_changes = []
        for field in IDENTITY_FIELDS:
            if field == "logical_point_id":
                continue
            old_value = old_item["identity"][field]
            new_value = new_item["identity"][field]
            if old_value != new_value:
                field_changes.append(
                    {"field": field, "before": old_value, "after": new_value}
                )
        for field in fields:
            if field in IDENTITY_FIELDS:
                continue
            old_value = _value(old_point, field)
            new_value = _value(new_point, field)
            if old_value != new_value:
                field_changes.append(
                    {"field": field, "before": old_value, "after": new_value}
                )
        moved.append(
            {
                "logical_point_id": key[0],
                "before_identity": old_item["identity"],
                "after_identity": new_item["identity"],
                "changes": field_changes,
            }
        )
        paired_removed.add(id(old_item))
        paired_added.add(id(new_item))

    removed = [item for item in removed if id(item) not in paired_removed]
    added = [item for item in added if id(item) not in paired_added]

    return {
        "contract": "modbus-map-diff/v1",
        "identity_fields": list(IDENTITY_FIELDS),
        "compare_fields": list(fields),
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "moved": len(moved),
            "unchanged": len(unchanged),
            "ambiguous": len(duplicates),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
        "moved": moved,
        "unchanged": unchanged,
        "duplicates": duplicates,
    }


__all__ = [
    "DEFAULT_COMPARE_FIELDS",
    "IDENTITY_FIELDS",
    "MapComparisonError",
    "compare_maps",
    "composite_identity",
    "identity_dict",
]
