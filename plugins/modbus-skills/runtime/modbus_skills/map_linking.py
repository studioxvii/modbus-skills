"""Project selected OEM points and device binding into ``modbus-map/v1``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .artifacts import artifact_envelope, stable_input_hash
from .compiler_contracts import (
    CompilerContractError,
    bound_point_identity,
    validate_device_binding,
    validate_oem_map,
    validate_user_map,
    validate_user_selection,
)
from .models import RegisterArea
from .validation import READ_FUNCTION_BY_AREA


_OVERRIDE_FIELDS = frozenset(
    {"oem_point_id", "area", "protocol_offset", "function_code", "source_address"}
)


class MapLinkError(ValueError):
    """Raised when compiler artifacts cannot form one exact bound map."""


def link_selected_map(
    oem_map: Mapping[str, Any],
    selection: Mapping[str, Any],
    user_map: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a deterministic selected-only compatibility projection."""

    try:
        validate_oem_map(oem_map)
        validate_user_selection(selection, oem_map)
        validate_user_map(user_map, oem_map, selection)
        validate_device_binding(binding, oem_map)
    except CompilerContractError as exc:
        raise MapLinkError(str(exc)) from exc

    selected_ids = {entry["oem_point_id"] for entry in selection["included"]}
    user_points = {point["oem_point_id"]: point for point in user_map["points"]}
    if set(user_points) != selected_ids:
        raise MapLinkError("user map points must match exactly the included selection")
    oem_points = {point["oem_point_id"]: point for point in oem_map["points"]}
    overrides = _point_overrides(binding.get("point_overrides", ()), selected_ids)

    points = [
        _linked_point(
            oem_points[point_id],
            user_points[point_id],
            binding,
            overrides.get(point_id, {}),
        )
        for point_id in selected_ids
    ]
    points.sort(
        key=lambda point: (
            str(point.get("route_id", "")),
            int(point.get("unit_id", 0)),
            str(point.get("area", "")),
            int(point.get("protocol_offset", 65_536)),
            str(point.get("logical_point_id", "")),
        )
    )

    selected_holds, unselected_holds = _partition_holds(
        [
            *list(oem_map.get("holds", ())),
            *list(selection.get("holds", ())),
            *list(user_map.get("holds", ())),
            *list(binding.get("holds", ())),
        ],
        selected_ids,
    )
    annex = [dict(item) for item in user_map.get("exception_annex", ()) if isinstance(item, Mapping)]
    annex_keys = {_hold_key(item) for item in annex}
    for hold in unselected_holds:
        candidate = {"kind": "unselected-hold", **hold}
        if _hold_key(candidate) not in annex_keys:
            annex.append(candidate)
            annex_keys.add(_hold_key(candidate))
    annex.sort(key=lambda item: (str(item.get("oem_point_id", "")), str(item.get("code", ""))))

    return artifact_envelope(
        {
            "points": points,
            "read_constraints": dict(binding.get("read_constraints", {})),
            "exception_annex": annex,
        },
        schema_version="modbus-map/v1",
        input_hashes={
            "binding": stable_input_hash(binding),
            "oem_map": stable_input_hash(oem_map),
            "selection": stable_input_hash(selection),
            "user_map": stable_input_hash(user_map),
        },
        assumptions=[
            *list(oem_map.get("assumptions", ())),
            *list(binding.get("assumptions", ())),
        ],
        findings=[
            *list(oem_map.get("findings", ())),
            *list(binding.get("findings", ())),
        ],
        holds=selected_holds,
    )


def _linked_point(
    oem_point: Mapping[str, Any],
    user_point: Mapping[str, Any],
    binding: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    point_id = str(oem_point["oem_point_id"])
    point = {
        key: value
        for key, value in {**dict(oem_point), **dict(user_point)}.items()
        if key not in {"schema_version", "canonical_identity"}
    }
    point.update({key: value for key, value in override.items() if key != "oem_point_id"})
    point.update(
        {
            "schema_version": "modbus-map/v1",
            "logical_point_id": point_id,
            "route_id": binding["route_id"],
            "unit_id": binding["unit_id"],
        }
    )
    if "source_address" not in point:
        point["source_address"] = {
            "raw": point.get("protocol_offset"),
            "convention": "protocol-offset",
        }
    area = RegisterArea.coerce(point.get("area"))
    if point.get("function_code") is None and area in READ_FUNCTION_BY_AREA:
        point["function_code"] = READ_FUNCTION_BY_AREA[area]
    offset = point.get("protocol_offset")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise MapLinkError(f"selected OEM point {point_id!r} has no resolved protocol offset")
    point["area"] = area.value
    point["canonical_identity"] = list(
        bound_point_identity(
            point,
            route_id=binding["route_id"],
            unit_id=binding["unit_id"],
        )
    )
    return point


def _point_overrides(
    values: Any, selected_ids: set[str]
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise MapLinkError("binding point_overrides must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise MapLinkError(f"point_overrides[{index}] must be an object")
        unknown = set(raw) - _OVERRIDE_FIELDS
        if unknown:
            raise MapLinkError(
                f"point_overrides[{index}] contains unsupported fields: "
                + ", ".join(sorted(map(str, unknown)))
            )
        point_id = str(raw.get("oem_point_id", "")).strip()
        if point_id not in selected_ids:
            continue
        result[point_id] = dict(raw)
    return result


def _partition_holds(
    values: Sequence[Any], selected_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    unselected: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        hold = dict(raw)
        key = _hold_key(hold)
        if key in seen:
            continue
        seen.add(key)
        scope = set(key[1])
        if not scope or scope & selected_ids:
            selected.append(hold)
        else:
            unselected.append(hold)
    selected.sort(key=lambda item: _hold_key(item))
    unselected.sort(key=lambda item: _hold_key(item))
    return selected, unselected


def _hold_key(value: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    point_ids = {
        str(item)
        for item in (value.get("oem_point_id"), value.get("point_id"))
        if item not in (None, "")
    }
    for field in ("point_ids", "subject_ids"):
        raw = value.get(field, ())
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            point_ids.update(str(item) for item in raw)
    return (str(value.get("code", "")), tuple(sorted(point_ids)))


__all__ = ["MapLinkError", "link_selected_map"]
