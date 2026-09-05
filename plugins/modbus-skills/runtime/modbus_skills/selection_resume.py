"""Bind an explicit selection reply to one inspected compiler checkpoint."""
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .compiler import CompilerError, inspect_compile_case
from .decision_packets import validate_decision_candidate


def prepare_selection_resume(
    case_root: str | Path, *, expected_case_hash: str,
    include: Sequence[str] = (), exclude_all: bool = False, reason: str,
) -> dict[str, Any]:
    """Prepare supplied offered IDs, never select or mutate the saved case.

    The caller must first inspect the packet and obtain the user's actual choice.
    Rechecking the expected hash prevents rebinding that choice to a newer case.
    More complex packets continue to use the full typed resume interface.
    """
    if isinstance(include, (str, bytes)) or not isinstance(include, Sequence):
        raise CompilerError("include must be a sequence of offered subject IDs")
    if not isinstance(exclude_all, bool) or bool(include) == exclude_all:
        raise CompilerError("supply included subject IDs or explicit exclude-all, not both")
    inspection = inspect_compile_case(case_root)
    if inspection["case_hash"] != expected_case_hash:
        raise CompilerError("selection reply has a stale or mismatched case hash")
    packet = inspection["active_packet"]
    if (inspection["state"] != "awaiting-selection-decision"
            or inspection["next_action"].get("kind") != "provide-selection-decision"
            or not isinstance(packet, dict) or packet.get("phase") != "selection"
            or len(packet.get("decisions", [])) != 1):
        raise CompilerError("helper requires one active grouped selection decision; use typed resume for other packets")
    offered = packet["decisions"][0]
    decision = {
        "decision_id": offered["decision_id"],
        "disposition": "exclude-all" if exclude_all else "include-specified",
        "reason": reason,
        "evidence_refs": offered["evidence_refs"],
    }
    if include:
        decision["selected_subject_ids"] = list(include)
    candidate = validate_decision_candidate(packet, {
        "schema_version": "modbus-compiler-decision-candidate/v1",
        **{field: packet[field] for field in ("case_id", "phase", "packet_id", "source_hash", "input_hashes")},
        "decisions": [decision],
    })
    return {
        "schema_version": "modbus-compile-resume/v1",
        "case_id": inspection["case_id"], "case_hash": expected_case_hash,
        "action": "provide-selection-decision", "decision_candidate": candidate,
    }
