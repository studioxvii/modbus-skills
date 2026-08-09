"""Typed, hash-bound decision packets for compiler pauses.

These packets are compiler control data, not legacy map review records.  Free
form replies have no authority: a caller must translate them into the typed
candidate contract and this module validates the exact permitted scope.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from .artifacts import artifact_envelope, assert_artifact_envelope, stable_input_hash


DECISION_PACKET_SCHEMA_VERSION = "modbus-compiler-decision-packet/v1"
DECISION_CANDIDATE_SCHEMA_VERSION = "modbus-compiler-decision-candidate/v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PHASES = frozenset({"source", "selection", "binding", "physical-read", "byte-order"})


class DecisionPacketError(ValueError):
    """Raised when a compiler decision packet or candidate is unsafe."""


def build_compiler_decision_packet(
    *,
    case_id: str,
    phase: str,
    source_hash: str,
    input_hashes: Mapping[str, str] | None = None,
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one deterministic packet containing all bounded decisions."""

    normalized_case = _safe_id(case_id, "case_id")
    normalized_phase = _phase(phase)
    normalized_source = _digest(source_hash, "source_hash")
    normalized_inputs = _hashes(input_hashes or {})
    if "source" in normalized_inputs and normalized_inputs["source"] != normalized_source:
        raise DecisionPacketError("input_hashes.source must match source_hash")
    normalized_inputs["source"] = normalized_source
    normalized_decisions = [_normalize_packet_decision(item, index) for index, item in enumerate(decisions)]
    normalized_decisions.sort(key=lambda item: item["decision_id"])
    if not normalized_decisions:
        raise DecisionPacketError("decisions must contain at least one bounded decision")
    identifiers = [item["decision_id"] for item in normalized_decisions]
    if len(identifiers) != len(set(identifiers)):
        raise DecisionPacketError("decision IDs must be unique within a packet")
    packet_id = stable_input_hash(
        {
            "case_id": normalized_case,
            "phase": normalized_phase,
            "source_hash": normalized_source,
            "input_hashes": normalized_inputs,
            "decisions": normalized_decisions,
        }
    )
    packet = artifact_envelope(
        {
            "case_id": normalized_case,
            "phase": normalized_phase,
            "packet_id": packet_id,
            "source_hash": normalized_source,
            "decisions": normalized_decisions,
        },
        schema_version=DECISION_PACKET_SCHEMA_VERSION,
        input_hashes=normalized_inputs,
    )
    validate_decision_packet(packet)
    return packet


def build_selection_decision_packet(
    *,
    case_id: str,
    source_hash: str,
    oem_map_hash: str,
    candidate_ids: Sequence[str],
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    """Build one packet asking for a bounded selection when none is defensible."""

    subjects = sorted({_safe_id(item, "candidate ID") for item in candidate_ids})
    if not subjects:
        raise DecisionPacketError("selection decision needs at least one candidate")
    return build_compiler_decision_packet(
        case_id=case_id,
        phase="selection",
        source_hash=source_hash,
        input_hashes={"oem_map": _digest(oem_map_hash, "oem_map_hash")},
        decisions=[
            {
                "decision_id": "selection.choose-included-points",
                "subject_ids": subjects,
                "prompt": "Choose the defensible points to include, or exclude all candidates.",
                "permitted_dispositions": ["include-specified", "exclude-all"],
                "evidence_refs": sorted({_text(item, "evidence reference") for item in evidence_refs}),
            }
        ],
    )


def validate_decision_packet(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise DecisionPacketError("decision packet must be a typed object")
    allowed = {
        "schema_version",
        "artifact_type",
        "input_hashes",
        "assumptions",
        "findings",
        "holds",
        "case_id",
        "phase",
        "packet_id",
        "source_hash",
        "decisions",
    }
    unknown = set(value) - allowed
    if unknown:
        raise DecisionPacketError("decision packet has unknown fields: " + ", ".join(sorted(map(str, unknown))))
    try:
        assert_artifact_envelope(value)
    except ValueError as exc:
        raise DecisionPacketError(str(exc)) from exc
    if value.get("schema_version") != DECISION_PACKET_SCHEMA_VERSION:
        raise DecisionPacketError(f"schema_version must be {DECISION_PACKET_SCHEMA_VERSION}")
    case_id = _safe_id(value.get("case_id"), "case_id")
    phase = _phase(value.get("phase"))
    source_hash = _digest(value.get("source_hash"), "source_hash")
    inputs = _hashes(value.get("input_hashes"))
    if inputs.get("source") != source_hash:
        raise DecisionPacketError("packet source hash is not bound to input_hashes")
    raw_decisions = _sequence(value.get("decisions"), "decisions")
    decisions = [_normalize_packet_decision(item, index) for index, item in enumerate(raw_decisions)]
    if not decisions:
        raise DecisionPacketError("decisions must contain at least one bounded decision")
    identifiers = [item["decision_id"] for item in decisions]
    if len(identifiers) != len(set(identifiers)):
        raise DecisionPacketError("decision IDs must be unique within a packet")
    expected_packet_id = stable_input_hash(
        {
            "case_id": case_id,
            "phase": phase,
            "source_hash": source_hash,
            "input_hashes": inputs,
            "decisions": sorted(decisions, key=lambda item: item["decision_id"]),
        }
    )
    if value.get("packet_id") != expected_packet_id:
        raise DecisionPacketError("packet_id does not match the bounded packet contents")


def validate_decision_candidate(
    packet: Mapping[str, Any], candidate: Mapping[str, Any] | Any
) -> dict[str, Any]:
    """Validate and normalize a typed candidate without mutating compiler state."""

    validate_decision_packet(packet)
    if not isinstance(candidate, Mapping):
        raise DecisionPacketError("decision candidate must be a typed object; prose has no authority")
    allowed_top = {
        "schema_version",
        "case_id",
        "phase",
        "packet_id",
        "source_hash",
        "input_hashes",
        "decisions",
    }
    unknown_top = set(candidate) - allowed_top
    if unknown_top:
        raise DecisionPacketError("decision candidate has unknown fields: " + ", ".join(sorted(map(str, unknown_top))))
    if candidate.get("schema_version") != DECISION_CANDIDATE_SCHEMA_VERSION:
        raise DecisionPacketError(f"schema_version must be {DECISION_CANDIDATE_SCHEMA_VERSION}")
    for field in ("case_id", "phase", "packet_id", "source_hash"):
        if candidate.get(field) != packet.get(field):
            raise DecisionPacketError(f"decision candidate has stale or mismatched {field}")
    candidate_inputs = _hashes(candidate.get("input_hashes"))
    if candidate_inputs != dict(packet["input_hashes"]):
        raise DecisionPacketError("decision candidate has stale input hashes")

    packet_index = {item["decision_id"]: item for item in packet["decisions"]}
    raw_decisions = _sequence(candidate.get("decisions"), "candidate decisions")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_decisions):
        if not isinstance(raw, Mapping):
            raise DecisionPacketError(f"candidate decisions[{index}] must be an object")
        unknown = set(raw) - {
            "decision_id",
            "disposition",
            "reason",
            "evidence_refs",
            "selected_subject_ids",
        }
        if unknown:
            raise DecisionPacketError(
                f"candidate decisions[{index}] has unknown fields: " + ", ".join(sorted(map(str, unknown)))
            )
        decision_id = _safe_id(raw.get("decision_id"), f"candidate decisions[{index}].decision_id")
        if decision_id not in packet_index:
            raise DecisionPacketError(f"candidate references unknown decision {decision_id!r}")
        if decision_id in seen:
            raise DecisionPacketError(f"candidate repeats decision {decision_id!r}")
        seen.add(decision_id)
        permitted = packet_index[decision_id]["permitted_dispositions"]
        disposition = _text(raw.get("disposition"), f"candidate decisions[{index}].disposition")
        if disposition not in permitted:
            raise DecisionPacketError(f"disposition {disposition!r} is not permitted for {decision_id!r}")
        evidence_refs = sorted(
            {_text(item, "candidate evidence reference") for item in _sequence(raw.get("evidence_refs"), "candidate evidence_refs")}
        )
        if not evidence_refs:
            raise DecisionPacketError(f"candidate decision {decision_id!r} needs evidence")
        available_evidence = set(packet_index[decision_id]["evidence_refs"])
        if not set(evidence_refs) <= available_evidence:
            raise DecisionPacketError(f"candidate decision {decision_id!r} references evidence outside packet evidence")
        selected_subjects = sorted(
            {_safe_id(item, "selected subject ID") for item in _sequence(raw.get("selected_subject_ids", ()), "selected_subject_ids")}
        )
        available_subjects = set(packet_index[decision_id]["subject_ids"])
        if not set(selected_subjects) <= available_subjects:
            raise DecisionPacketError(f"candidate decision {decision_id!r} broadens packet subject scope")
        if disposition == "include-specified" and not selected_subjects:
            raise DecisionPacketError("include-specified needs at least one selected_subject_id")
        if disposition != "include-specified" and selected_subjects:
            raise DecisionPacketError("selected_subject_ids are only valid for include-specified")
        item = {
            "decision_id": decision_id,
            "disposition": disposition,
            "reason": _text(raw.get("reason"), f"candidate decisions[{index}].reason"),
            "evidence_refs": evidence_refs,
        }
        if selected_subjects:
            item["selected_subject_ids"] = selected_subjects
        normalized.append(item)
    if seen != set(packet_index):
        missing = sorted(set(packet_index) - seen)
        raise DecisionPacketError("candidate omits packet decisions: " + ", ".join(missing))
    normalized.sort(key=lambda item: item["decision_id"])
    return {
        "schema_version": DECISION_CANDIDATE_SCHEMA_VERSION,
        "case_id": packet["case_id"],
        "phase": packet["phase"],
        "packet_id": packet["packet_id"],
        "source_hash": packet["source_hash"],
        "input_hashes": dict(packet["input_hashes"]),
        "decisions": normalized,
    }


def decision_reply_fingerprint(packet: Mapping[str, Any], candidate: Mapping[str, Any] | Any) -> str:
    """Return the replay identity for one exact validated reply."""

    normalized = validate_decision_candidate(packet, candidate)
    return stable_input_hash({"packet_id": packet["packet_id"], "candidate": normalized})


def _normalize_packet_decision(value: Mapping[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionPacketError(f"decisions[{index}] must be an object")
    unknown = set(value) - {
        "decision_id",
        "subject_ids",
        "prompt",
        "permitted_dispositions",
        "evidence_refs",
    }
    if unknown:
        raise DecisionPacketError(f"decisions[{index}] has unknown fields: " + ", ".join(sorted(map(str, unknown))))
    subjects = sorted({_safe_id(item, "subject ID") for item in _sequence(value.get("subject_ids"), "subject_ids")})
    dispositions = sorted({_text(item, "permitted disposition") for item in _sequence(value.get("permitted_dispositions"), "permitted_dispositions")})
    evidence = sorted({_text(item, "evidence reference") for item in _sequence(value.get("evidence_refs"), "evidence_refs")})
    if not subjects or not dispositions or not evidence:
        raise DecisionPacketError("each decision needs subjects, permitted dispositions, and evidence")
    return {
        "decision_id": _safe_id(value.get("decision_id"), f"decisions[{index}].decision_id"),
        "subject_ids": subjects,
        "prompt": _text(value.get("prompt"), f"decisions[{index}].prompt"),
        "permitted_dispositions": dispositions,
        "evidence_refs": evidence,
    }


def _hashes(value: Mapping[str, str] | Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise DecisionPacketError("input_hashes must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_digest in value.items():
        name = str(raw_name)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise DecisionPacketError("input hash names must be lowercase semantic names")
        result[name] = _digest(raw_digest, f"input_hashes.{name}")
    return dict(sorted(result.items()))


def _safe_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise DecisionPacketError(f"{field} must be a safe identifier")
    return value


def _phase(value: Any) -> str:
    phase = _text(value, "phase")
    if phase not in _PHASES:
        raise DecisionPacketError(f"phase must be one of {sorted(_PHASES)}")
    return phase


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value.lower()):
        raise DecisionPacketError(f"{field} must be SHA-256 hex")
    return value.lower()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionPacketError(f"{field} must be non-empty text")
    return value.strip()


def _sequence(value: Sequence[Any] | Any, field: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, memoryview)):
        raise DecisionPacketError(f"{field} must be an array")
    return list(value)


__all__ = [
    "DECISION_CANDIDATE_SCHEMA_VERSION",
    "DECISION_PACKET_SCHEMA_VERSION",
    "DecisionPacketError",
    "build_compiler_decision_packet",
    "build_selection_decision_packet",
    "decision_reply_fingerprint",
    "validate_decision_candidate",
    "validate_decision_packet",
]
