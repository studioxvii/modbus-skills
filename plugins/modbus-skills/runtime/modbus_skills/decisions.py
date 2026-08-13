"""Apply explicit human review decisions to a normalized Modbus map."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
import math
import re
from typing import Any

from .artifacts import ArtifactContractError, assert_artifact_envelope, stable_input_hash
from .byte_order import RawSample, evaluate_byte_orders
from .map_workflows import lint_map
from .models import DataType, RegisterArea, normalize_bit_order


class ReviewDecisionError(ValueError):
    """Raised when a review decision is incomplete or unsafe."""


_ALLOWED_FIELDS = frozenset(
    {
        "route_id",
        "unit_id",
        "area",
        "protocol_offset",
        "datatype",
        "word_span",
        "byte_order",
        "bit_order",
        "scale",
        "engineering_offset",
        "engineering_unit",
        "access",
        "function_code",
    }
)
_ACCESS = frozenset({"read-only", "read-write", "write-only"})
_FIELD_HOLD_ALIASES = {
    "byte_order": frozenset({"byte_order", "byte_order_confirmed", "byte_order_status"}),
    "word_span": frozenset({"word_span", "word_count"}),
    "engineering_offset": frozenset({"engineering_offset", "offset"}),
}
_FIELD_APPLICATION_ORDER = {
    field: index
    for index, field in enumerate(
        (
            "route_id",
            "unit_id",
            "area",
            "protocol_offset",
            "datatype",
            "word_span",
            "scale",
            "engineering_offset",
            "engineering_unit",
            "access",
            "function_code",
            "byte_order",
            "bit_order",
        )
    )
}


def apply_review_decisions(
    canonical_map: Mapping[str, Any],
    decision_record: Mapping[str, Any],
    *,
    evidence_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a new map with reviewed values and evidence.

    The source map is never modified. Each change needs a point ID, a reason,
    and either one whitelisted field or an explicit exclusion action.
    """

    schema_version = _required_text(
        decision_record.get("schema_version"), "schema_version"
    )
    if schema_version != "modbus-review-decisions/v1":
        raise ReviewDecisionError(
            "schema_version must be modbus-review-decisions/v1"
        )
    expected_map_hash = _required_hash(
        decision_record.get("canonical_map_hash"), "canonical_map_hash"
    )
    actual_map_hash = stable_input_hash(canonical_map)
    if expected_map_hash != actual_map_hash:
        raise ReviewDecisionError(
            "canonical_map_hash does not match the supplied map"
        )
    evidence_index = _evidence_index(evidence_artifacts)
    review_id = _required_text(decision_record.get("review_id"), "review_id")
    reviewed_at = _timestamp(decision_record.get("reviewed_at"))
    reviewer = _required_text(decision_record.get("reviewer"), "reviewer")
    approve_map = decision_record.get("approve_map", False)
    if not isinstance(approve_map, bool):
        raise ReviewDecisionError("approve_map must be true or false")

    raw_decisions = decision_record.get("decisions")
    if not isinstance(raw_decisions, Sequence) or isinstance(
        raw_decisions, (str, bytes, bytearray)
    ):
        raise ReviewDecisionError("decisions must be an array")
    raw_hold_decisions = decision_record.get("hold_decisions", ())
    if not isinstance(raw_hold_decisions, Sequence) or isinstance(
        raw_hold_decisions, (str, bytes, bytearray)
    ):
        raise ReviewDecisionError("hold_decisions must be an array")

    result = deepcopy(dict(canonical_map))
    raw_points = result.get("points")
    if not isinstance(raw_points, list) or any(
        not isinstance(point, Mapping) for point in raw_points
    ):
        raise ReviewDecisionError("map must contain a points array")
    points = [dict(point) for point in raw_points]
    point_index: dict[str, dict[str, Any]] = {}
    for index, point in enumerate(points):
        identifier = str(
            point.get("logical_point_id", point.get("point_id", ""))
        ).strip()
        if not identifier:
            raise ReviewDecisionError(f"points[{index}] has no logical point ID")
        if identifier in point_index:
            raise ReviewDecisionError(f"logical point ID {identifier!r} is duplicated")
        point_index[identifier] = point

    prepared_decisions: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str]] = set()
    actions_by_point: dict[str, str] = {}
    for index, raw_decision in enumerate(raw_decisions):
        if not isinstance(raw_decision, Mapping):
            raise ReviewDecisionError(f"decisions[{index}] must be an object")
        unknown = set(raw_decision) - {
            "point_id",
            "action",
            "field",
            "value",
            "reason",
            "evidence_refs",
        }
        if unknown:
            raise ReviewDecisionError(
                f"decisions[{index}] has unknown fields: "
                + ", ".join(sorted(str(value) for value in unknown))
            )
        point_id = _required_text(raw_decision.get("point_id"), f"decisions[{index}].point_id")
        if point_id not in point_index:
            raise ReviewDecisionError(
                f"decisions[{index}] references unknown point {point_id!r}"
            )
        reason = _required_text(raw_decision.get("reason"), f"decisions[{index}].reason")
        evidence_refs = raw_decision.get("evidence_refs", [])
        if not isinstance(evidence_refs, Sequence) or isinstance(
            evidence_refs, (str, bytes, bytearray)
        ):
            raise ReviewDecisionError(
                f"decisions[{index}].evidence_refs must be an array"
            )
        normalized_refs = [
            _required_text(reference, f"decisions[{index}].evidence_refs")
            for reference in evidence_refs
        ]
        action = str(raw_decision.get("action", "set")).strip().lower()
        if action not in {"set", "exclude"}:
            raise ReviewDecisionError(
                f"decisions[{index}].action must be set or exclude"
            )
        prior_action = actions_by_point.setdefault(point_id, action)
        if prior_action != action:
            raise ReviewDecisionError(
                f"decisions[{index}] cannot combine set and exclude decisions for {point_id!r}"
            )
        field = None
        if action == "set":
            field = _normalized_field(
                raw_decision.get("field"), f"decisions[{index}].field"
            )
            if field not in _ALLOWED_FIELDS:
                raise ReviewDecisionError(
                    f"decisions[{index}].field must be one of {sorted(_ALLOWED_FIELDS)}"
                )
        target = (point_id, "*" if action == "exclude" else field)
        if target in seen_targets:
            raise ReviewDecisionError(
                f"decisions[{index}] duplicates a decision for {point_id!r}"
            )
        seen_targets.add(target)
        if action == "set" and not normalized_refs:
            raise ReviewDecisionError(
                f"decisions[{index}].evidence_refs needs at least one reference for a set decision"
            )
        prepared_decisions.append(
            {
                "index": index,
                "point_id": point_id,
                "action": action,
                "field": field,
                "raw_value": raw_decision.get("value"),
                "reason": reason,
                "evidence_refs": normalized_refs,
            }
        )

    prepared_hold_decisions: list[dict[str, Any]] = []
    seen_hold_codes: set[str] = set()
    source_hold_codes = {
        str(hold.get("code", "")).strip()
        for hold in result.get("source_holds", ())
        if isinstance(hold, Mapping)
        and str(hold.get("code", "")).strip()
        and not _hold_has_point_scope(hold)
    }
    active_global_hold_codes = {
        str(hold.get("code", "")).strip()
        for hold in result.get("holds", ())
        if isinstance(hold, Mapping)
        and str(hold.get("code", "")).strip()
        and not _hold_has_point_scope(hold)
    }
    available_hold_codes = source_hold_codes & active_global_hold_codes
    for index, raw_decision in enumerate(raw_hold_decisions):
        if not isinstance(raw_decision, Mapping):
            raise ReviewDecisionError(f"hold_decisions[{index}] must be an object")
        unknown = set(raw_decision) - {"code", "reason", "evidence_refs"}
        if unknown:
            raise ReviewDecisionError(
                f"hold_decisions[{index}] has unknown fields: "
                + ", ".join(sorted(str(value) for value in unknown))
            )
        code = _required_text(
            raw_decision.get("code"), f"hold_decisions[{index}].code"
        )
        if code in seen_hold_codes:
            raise ReviewDecisionError(
                f"hold_decisions[{index}] duplicates hold code {code!r}"
            )
        if code not in available_hold_codes:
            raise ReviewDecisionError(
                f"hold_decisions[{index}] references unknown hold code {code!r}"
            )
        reason = _required_text(
            raw_decision.get("reason"), f"hold_decisions[{index}].reason"
        )
        evidence_refs = raw_decision.get("evidence_refs", ())
        if not isinstance(evidence_refs, Sequence) or isinstance(
            evidence_refs, (str, bytes, bytearray)
        ):
            raise ReviewDecisionError(
                f"hold_decisions[{index}].evidence_refs must be an array"
            )
        normalized_refs = [
            _required_text(reference, f"hold_decisions[{index}].evidence_refs")
            for reference in evidence_refs
        ]
        if not normalized_refs:
            raise ReviewDecisionError(
                f"hold_decisions[{index}].evidence_refs needs at least one reference"
            )
        seen_hold_codes.add(code)
        prepared_hold_decisions.append(
            {
                "code": code,
                "reason": reason,
                "evidence_refs": normalized_refs,
            }
        )

    # Apply fields in one fixed order. This makes a record with related fields,
    # such as datatype and byte_order, independent of array order.
    for point_id, point in point_index.items():
        point_decisions = [
            decision
            for decision in prepared_decisions
            if decision["point_id"] == point_id and decision["action"] == "set"
        ]
        for decision in sorted(
            point_decisions,
            key=lambda item: _FIELD_APPLICATION_ORDER[str(item["field"])],
        ):
            field = str(decision["field"])
            value = _decision_value(field, decision["raw_value"], point)
            if field == "byte_order":
                evidence_hash, evidence = _referenced_byte_order_evidence(
                    decision["evidence_refs"], evidence_index, decision["index"]
                )
                _validate_byte_order_evidence(point, value, evidence)
                decision["evidence_hash"] = evidence_hash
            decision["value"] = value
            point[field] = value
            if field == "word_span":
                point["word_count"] = value
            elif field == "engineering_offset":
                point["offset"] = value
            elif field == "byte_order":
                point["byte_order_confirmed"] = True
                point["byte_order_status"] = "confirmed"

    normalized_decisions: list[dict[str, Any]] = []
    excluded_ids: set[str] = set()
    resolved_fields: dict[str, set[str]] = {}
    for decision in prepared_decisions:
        point_id = str(decision["point_id"])
        action = str(decision["action"])
        normalized_refs = decision["evidence_refs"]
        audit = {
            "review_id": review_id,
            "reviewed_at": reviewed_at,
            "reviewer": reviewer,
            "point_id": point_id,
            "action": action,
            "reason": decision["reason"],
            "evidence_refs": deepcopy(normalized_refs),
        }
        if action == "exclude":
            excluded_ids.add(point_id)
            normalized_decisions.append(audit)
            continue

        field = str(decision["field"])
        value = decision["value"]
        point = point_index[point_id]
        if field == "byte_order":
            evidence_hash = decision["evidence_hash"]
            audit["evidence_hash"] = evidence_hash
        audit.update({"field": field, "value": value})
        point.setdefault("review_decisions", []).append(deepcopy(audit))
        point.setdefault("source_evidence", []).append(
            {
                "field": field,
                "source_field": "human_review_decision",
                "source_value": decision["raw_value"],
                "value": value,
                "review_id": review_id,
                "evidence_refs": deepcopy(normalized_refs),
            }
        )
        resolved_fields.setdefault(point_id, set()).update(
            _FIELD_HOLD_ALIASES.get(field, frozenset({field}))
        )
        normalized_decisions.append(audit)

    resolved_hold_codes = {
        str(decision["code"]) for decision in prepared_hold_decisions
    }
    for decision in prepared_hold_decisions:
        normalized_decisions.append(
            {
                "review_id": review_id,
                "reviewed_at": reviewed_at,
                "reviewer": reviewer,
                "action": "resolve-hold",
                "hold_code": decision["code"],
                "reason": decision["reason"],
                "evidence_refs": deepcopy(decision["evidence_refs"]),
            }
        )

    raw_source_holds = result.get("source_holds", ())
    if isinstance(raw_source_holds, Sequence) and not isinstance(
        raw_source_holds, (str, bytes, bytearray)
    ):
        updated_source_holds = []
        for hold in raw_source_holds:
            copied = deepcopy(hold)
            if (
                isinstance(copied, Mapping)
                and copied.get("code") in resolved_hold_codes
                and not _hold_has_point_scope(copied)
            ):
                copied = dict(copied)
                matching = next(
                    decision
                    for decision in prepared_hold_decisions
                    if decision["code"] == copied.get("code")
                )
                copied["disposition"] = {
                    "status": "resolved",
                    "review_id": review_id,
                    "reviewed_at": reviewed_at,
                    "reviewer": reviewer,
                    "reason": matching["reason"],
                    "evidence_refs": deepcopy(matching["evidence_refs"]),
                }
            updated_source_holds.append(copied)
        result["source_holds"] = updated_source_holds

    excluded_points = []
    active_points = []
    for point in points:
        point_id = str(point.get("logical_point_id", point.get("point_id", "")))
        if point_id in excluded_ids:
            excluded = deepcopy(point)
            excluded["review_disposition"] = next(
                decision
                for decision in normalized_decisions
                if decision["point_id"] == point_id and decision["action"] == "exclude"
            )
            excluded_points.append(excluded)
        else:
            active_points.append(point)
    result["points"] = active_points
    prior_excluded = result.get("excluded_points", ())
    if prior_excluded is None:
        prior_excluded = ()
    if not isinstance(prior_excluded, Sequence) or isinstance(
        prior_excluded, (str, bytes, bytearray)
    ):
        raise ReviewDecisionError("map.excluded_points must be an array")
    result["excluded_points"] = [
        *list(prior_excluded),
        *excluded_points,
    ]

    unresolved_holds = []
    for hold in result.get("holds", ()):
        if not isinstance(hold, Mapping):
            unresolved_holds.append(hold)
            continue
        if (
            str(hold.get("code", "")) in resolved_hold_codes
            and not _hold_has_point_scope(hold)
        ):
            continue
        point_ids = hold.get("point_ids", ())
        ids = {
            str(value)
            for value in point_ids
        } if isinstance(point_ids, Sequence) and not isinstance(
            point_ids, (str, bytes, bytearray)
        ) else set()
        if ids and ids <= excluded_ids:
            continue
        field = str(hold.get("field", ""))
        if ids and all(field in resolved_fields.get(point_id, set()) for point_id in ids):
            continue
        unresolved_holds.append(deepcopy(hold))
    result["holds"] = unresolved_holds

    lint = lint_map(result)
    findings = list(lint.get("findings", ()))
    holds = [
        finding
        for finding in findings
        if isinstance(finding, Mapping)
        and finding.get("severity") in {"hold", "error"}
        and finding.get("blocking", True) is not False
    ]
    held_point_ids = {
        str(point_id)
        for hold in holds
        for point_id in hold.get("point_ids", ())
        if isinstance(hold, Mapping)
        and isinstance(hold.get("point_ids", ()), Sequence)
        and not isinstance(hold.get("point_ids", ()), (str, bytes, bytearray))
    }
    for point in active_points:
        point_id = str(point.get("logical_point_id", point.get("point_id", "")))
        if (
            str(point.get("access", "")).strip().lower() == "write-only"
            and point_id not in held_point_ids
        ):
            holds.append(
                {
                    "code": "review.write-only-point-active",
                    "severity": "hold",
                    "blocking": True,
                    "message": "Exclude a write-only point from the active read map.",
                    "point_ids": [point_id],
                    "field": "access",
                    "details": {},
                }
            )
    if not active_points:
        holds.append(
            {
                "code": "map.no-active-points",
                "severity": "hold",
                "blocking": True,
                "message": "Keep at least one reviewed point in an approved map.",
                "point_ids": [],
                "field": "points",
                "details": {},
            }
        )
    blocked_ids = {
        str(point_id)
        for hold in holds
        for point_id in hold.get("point_ids", ())
        if isinstance(hold, Mapping)
        and isinstance(hold.get("point_ids", ()), Sequence)
        and not isinstance(hold.get("point_ids", ()), (str, bytes, bytearray))
    }
    for point in active_points:
        point_id = str(point.get("logical_point_id", point.get("point_id", "")))
        point["normalization_status"] = (
            "pending" if point_id in blocked_ids else "confirmed"
        )

    result["holds"] = holds
    result["findings"] = findings
    prior_decisions = result.get("review_decisions", ())
    if prior_decisions is None:
        prior_decisions = ()
    if not isinstance(prior_decisions, Sequence) or isinstance(
        prior_decisions, (str, bytes, bytearray)
    ):
        raise ReviewDecisionError("map.review_decisions must be an array")
    result["review_decisions"] = [
        *list(prior_decisions),
        *normalized_decisions,
    ]
    approved = approve_map and not holds
    result["review_status"] = (
        "approved" if approved else "blocked" if holds else "ready"
    )
    result["approval"] = (
        {
            "review_id": review_id,
            "reviewed_at": reviewed_at,
            "reviewer": reviewer,
            "input_map_hash": actual_map_hash,
        }
        if approved
        else None
    )
    summary = dict(result.get("summary", {}))
    summary.update(
        {
            "active_points": len(active_points),
            "excluded_points": len(result["excluded_points"]),
            "applied_decisions": len(normalized_decisions),
            "blocking_holds": len(holds),
        }
    )
    result["summary"] = summary
    return result


def _hold_has_point_scope(hold: Mapping[str, Any]) -> bool:
    point_ids = hold.get("point_ids", ())
    return (
        isinstance(point_ids, Sequence)
        and not isinstance(point_ids, (str, bytes, bytearray))
        and bool(point_ids)
    )


def _required_text(value: Any, label: str) -> str:
    text = str(value).strip() if value not in (None, "") else ""
    if not text:
        raise ReviewDecisionError(f"{label} must be non-empty text")
    return text


def _normalized_field(value: Any, label: str) -> str:
    return re.sub(r"[-\s]+", "_", _required_text(value, label).lower())


def _sample_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewDecisionError(f"{label} must be non-empty text")
    return value


def _timestamp(value: Any, label: str = "reviewed_at") -> str:
    text = _required_text(value, label)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ReviewDecisionError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReviewDecisionError(f"{label} must include a timezone offset or Z")
    return text


def _required_hash(value: Any, label: str) -> str:
    digest = _required_text(value, label).lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ReviewDecisionError(f"{label} must be a SHA-256 hex value")
    return digest


def _evidence_index(
    values: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for raw_digest, artifact in dict(values or {}).items():
        if not isinstance(artifact, Mapping):
            raise ReviewDecisionError("evidence artifacts must be JSON objects")
        digest = _required_hash(raw_digest, "evidence artifact hash")
        if stable_input_hash(artifact) != digest:
            raise ReviewDecisionError(
                "evidence artifact hash does not match its content"
            )
        result[digest] = artifact
    return result


def _evidence_digest(reference: str) -> str | None:
    normalized = reference.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[7:]
    return normalized if re.fullmatch(r"[0-9a-f]{64}", normalized) else None


def _referenced_byte_order_evidence(
    references: Sequence[str],
    evidence_index: Mapping[str, Mapping[str, Any]],
    decision_index: int,
) -> tuple[str, Mapping[str, Any]]:
    matches: list[tuple[str, Mapping[str, Any]]] = []
    for reference in references:
        digest = _evidence_digest(reference)
        if digest is None or digest not in evidence_index:
            continue
        artifact = evidence_index[digest]
        if artifact.get("schema_version") == "modbus-byte-order-evidence/v1":
            matches.append((digest, artifact))
    if len(matches) != 1:
        raise ReviewDecisionError(
            f"decisions[{decision_index}] must reference exactly one supplied modbus-byte-order-evidence/v1 artifact by SHA-256"
        )
    return matches[0]


def _validate_byte_order_evidence(
    point: Mapping[str, Any],
    layout: str,
    evidence: Mapping[str, Any],
) -> None:
    try:
        assert_artifact_envelope(evidence)
    except ArtifactContractError as exc:
        raise ReviewDecisionError(
            f"byte-order evidence common envelope is invalid: {exc}"
        ) from exc
    if evidence.get("artifact_type") != "modbus-byte-order-evidence":
        raise ReviewDecisionError(
            "byte-order evidence artifact_type is invalid"
        )
    if "winner" in evidence or "selected_layout" in evidence:
        raise ReviewDecisionError(
            "byte-order evidence must not contain an automatic selection"
        )
    identity = evidence.get("sample_identity")
    if not isinstance(identity, Mapping):
        raise ReviewDecisionError("byte-order evidence has no sample identity")
    identity_sample_id = _sample_id(
        identity.get("sample_id"), "byte-order evidence sample_identity.sample_id"
    )
    expected = {
        "point_id": str(
            point.get("logical_point_id", point.get("point_id", ""))
        ),
        "route_id": point.get("route_id"),
        "unit_id": point.get("unit_id"),
        "area": point.get("area"),
        "protocol_offset": point.get("protocol_offset"),
    }
    for field in ("point_id", "route_id"):
        actual_value = identity.get(field)
        expected_value = expected[field]
        if not isinstance(actual_value, str) or actual_value.strip() != str(
            expected_value
        ).strip():
            raise ReviewDecisionError(
                f"byte-order evidence {field} does not match the reviewed point"
            )
    try:
        identity_unit = _integer(
            identity.get("unit_id"), "byte-order evidence unit_id", 1, 247
        )
        identity_offset = _integer(
            identity.get("protocol_offset"),
            "byte-order evidence protocol_offset",
            0,
            65_535,
        )
    except ReviewDecisionError as exc:
        raise ReviewDecisionError(str(exc)) from exc
    if identity_unit != expected["unit_id"]:
        raise ReviewDecisionError(
            "byte-order evidence unit_id does not match the reviewed point"
        )
    if identity_offset != expected["protocol_offset"]:
        raise ReviewDecisionError(
            "byte-order evidence protocol_offset does not match the reviewed point"
        )
    if RegisterArea.coerce(identity.get("area")) is not RegisterArea.coerce(
        expected["area"]
    ):
        raise ReviewDecisionError(
            "byte-order evidence area does not match the reviewed point"
        )
    _timestamp(identity.get("timestamp"), "byte-order evidence timestamp")
    sample = evidence.get("sample")
    if not isinstance(sample, Mapping):
        raise ReviewDecisionError("byte-order evidence has no raw sample")
    raw_sample_id = _sample_id(
        sample.get("sample_id"), "byte-order evidence sample.sample_id"
    )
    if raw_sample_id != identity_sample_id:
        raise ReviewDecisionError(
            "byte-order evidence sample_id does not match its sample identity"
        )
    words = sample.get("words")
    if not isinstance(words, Sequence) or isinstance(
        words, (str, bytes, bytearray)
    ):
        raise ReviewDecisionError("byte-order evidence sample.words must be an array")
    try:
        raw_sample = RawSample(raw_sample_id, tuple(words))
    except (TypeError, ValueError) as exc:
        raise ReviewDecisionError(
            f"byte-order evidence raw words are invalid: {exc}"
        ) from exc
    declared_bit_width = sample.get("bit_width")
    if isinstance(declared_bit_width, bool) or not isinstance(
        declared_bit_width, int
    ):
        raise ReviewDecisionError(
            "byte-order evidence sample.bit_width must be an integer"
        )
    if declared_bit_width != raw_sample.bit_width:
        raise ReviewDecisionError(
            "byte-order evidence sample.bit_width does not match its raw words"
        )
    raw_hex = sample.get("raw_hex")
    if not isinstance(raw_hex, Sequence) or isinstance(
        raw_hex, (str, bytes, bytearray)
    ) or tuple(raw_hex) != raw_sample.raw_hex:
        raise ReviewDecisionError(
            "byte-order evidence sample.raw_hex does not match its raw words"
        )
    datatype = DataType.coerce(point.get("datatype"))
    if datatype.bit_width != raw_sample.bit_width:
        raise ReviewDecisionError(
            "byte-order evidence raw words do not match the reviewed point datatype"
        )
    point_span = _integer(
        point.get("word_span", point.get("word_count")),
        "reviewed point word span",
        1,
        4,
    )
    if point_span != len(raw_sample.words):
        raise ReviewDecisionError(
            "byte-order evidence raw words do not match the reviewed point word span"
        )
    candidates = evidence.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(
        candidates, (str, bytes, bytearray)
    ):
        raise ReviewDecisionError("byte-order evidence candidates must be an array")
    selected = [
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and str(candidate.get("layout", "")).strip().upper() == layout
        and candidate.get("datatype") == datatype.value
    ]
    if len(selected) != 1:
        raise ReviewDecisionError(
            "byte-order evidence must contain exactly one selected layout "
            "and point datatype candidate"
        )
    candidate = selected[0]
    candidate_sample_id = _sample_id(
        candidate.get("sample_id"),
        "byte-order evidence selected candidate sample_id",
    )
    if candidate_sample_id != identity_sample_id:
        raise ReviewDecisionError(
            "byte-order evidence selected candidate sample_id does not match the sample identity"
        )
    candidate_scale = _number(
        candidate.get("scale"), "byte-order evidence selected candidate scale"
    )
    candidate_offset = _number(
        candidate.get("engineering_offset"),
        "byte-order evidence selected candidate engineering_offset",
    )
    try:
        computed = evaluate_byte_orders(
            raw_sample,
            datatypes=(datatype,),
            layouts=(layout,),
            scale=candidate_scale,
            engineering_offset=candidate_offset,
        ).candidates[0].to_dict()
    except (TypeError, ValueError) as exc:
        raise ReviewDecisionError(
            f"byte-order evidence selected candidate cannot be recomputed: {exc}"
        ) from exc
    for field in (
        "sample_id",
        "layout",
        "datatype",
        "ordered_hex",
        "decoded_value",
        "scaled_value",
        "scale",
        "engineering_offset",
        "classification",
    ):
        if field not in candidate or not _same_evidence_value(
            candidate[field], computed[field]
        ):
            raise ReviewDecisionError(
                f"byte-order evidence selected candidate {field} does not match the raw words"
            )


def _same_evidence_value(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if float(actual) == 0.0 and float(expected) == 0.0:
            return math.copysign(1.0, float(actual)) == math.copysign(
                1.0, float(expected)
            )
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ReviewDecisionError(f"{label} must be an integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        result = int(value)
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        result = int(value.strip())
    else:
        raise ReviewDecisionError(f"{label} must be an integer")
    if not minimum <= result <= maximum:
        raise ReviewDecisionError(f"{label} must be from {minimum} through {maximum}")
    return result


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ReviewDecisionError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReviewDecisionError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ReviewDecisionError(f"{label} must be a finite number")
    return result


def _decision_value(field: str, value: Any, point: Mapping[str, Any]) -> Any:
    if field == "route_id":
        return _required_text(value, field)
    if field == "unit_id":
        return _integer(value, field, 1, 247)
    if field == "protocol_offset":
        return _integer(value, field, 0, 65_535)
    if field == "word_span":
        return _integer(value, field, 1, 125)
    if field == "function_code":
        if str(point.get("access", "")).strip().lower() == "write-only":
            raise ReviewDecisionError(
                "a write-only point cannot be assigned a read function code; exclude it"
            )
        return _integer(value, field, 1, 4)
    if field in {"scale", "engineering_offset"}:
        return _number(value, field)
    if field == "engineering_unit":
        return _required_text(value, field)
    if field == "area":
        area = RegisterArea.coerce(value)
        if area is RegisterArea.UNKNOWN:
            raise ReviewDecisionError("area is not recognized")
        return area.value
    if field == "datatype":
        datatype = DataType.coerce(value)
        if datatype is DataType.UNKNOWN:
            raise ReviewDecisionError("datatype is not recognized")
        return datatype.value
    if field == "access":
        access = str(value).strip().lower().replace("_", "-").replace(" ", "-")
        if access not in _ACCESS:
            raise ReviewDecisionError(f"access must be one of {sorted(_ACCESS)}")
        if access == "write-only":
            raise ReviewDecisionError(
                "a write-only point cannot remain in the active read map; exclude it"
            )
        if (
            str(point.get("access", "")).strip().lower() == "write-only"
            and access != "write-only"
        ):
            raise ReviewDecisionError(
                "a write-only source point cannot be converted into a readable point; exclude it"
            )
        return access
    if field == "byte_order":
        span = point.get("word_span", point.get("word_count"))
        span_value = _integer(span, "point word span", 1, 4)
        compact = re.sub(r"[^A-Za-z]", "", str(value)).upper()
        expected = "ABCDEFGH"[: span_value * 2]
        if len(compact) != len(expected) or sorted(compact) != sorted(expected):
            raise ReviewDecisionError(
                f"byte_order must be an explicit permutation of {expected}"
            )
        return compact
    if field == "bit_order":
        convention = normalize_bit_order(value)
        if convention is None:
            raise ReviewDecisionError(
                "bit_order must be an explicit packed-bit or coil numbering convention"
            )
        return convention
    raise ReviewDecisionError(f"field {field!r} is not supported")


__all__ = ["ReviewDecisionError", "apply_review_decisions"]
