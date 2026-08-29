"""Small persisted state machine for one OEM-map compilation outcome."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import time
from typing import Any, Callable

from .artifacts import artifact_envelope, stable_input_hash
from .compiler_contracts import (
    COMPILE_CASE_SCHEMA_VERSION,
    CompilerContractError,
    build_compile_case,
    pdf_completion_issues,
    validate_compile_case,
    validate_device_binding,
    validate_oem_map,
)
from .decision_packets import (
    DECISION_CANDIDATE_SCHEMA_VERSION,
    DecisionPacketError,
    build_compiler_decision_packet,
    decision_reply_fingerprint,
    validate_decision_candidate,
)
from .exporters import ExporterInputError, stable_json
from .map_linking import MapLinkError, link_selected_map
from .read_plan import compile_read_plan
from .source_intake import (
    SourceIntakeError,
    bind_selection_template,
    compile_source_descriptor,
    source_request_identity,
)
from .tool_pack import SUPPORTED_TARGETS, build_tool_pack
from .user_map import UserMapError, compile_user_map_bundle


COMPILE_REQUEST_SCHEMA_VERSION = "modbus-compile-request/v1"
COMPILE_RESUME_SCHEMA_VERSION = "modbus-compile-resume/v1"
COMPILE_RESULT_SCHEMA_VERSION = "modbus-compile-result/v1"
COMPILER_VERSION = "1"

# POSIX owner-only modes. Windows honours only the read-only bit, and lacks
# os.fchmod entirely, so permission calls are best-effort off POSIX.
_CASE_DIRECTORY_MODE = 0o700
_ARTIFACT_MODE = 0o600

_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "oem_map",
        "source",
        "selection_candidate",
        "selection_template",
        "targets",
        "target_options",
        "binding",
    }
)
_RESUME_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "case_hash",
        "action",
        "binding",
        "decision_candidate",
    }
)
_UNSAFE_FIELDS = frozenset(
    {
        "api_key",
        "broadcast",
        "credential",
        "credentials",
        "endpoint",
        "host",
        "hostname",
        "ip_address",
        "password",
        "poll",
        "scan",
        "secret",
        "token",
        "write",
        "writes",
    }
)


class CompilerError(ValueError):
    """Raised when a compiler request or persisted transition is unsafe."""


def compile_user_map(
    request: Mapping[str, Any] | None,
    case_root: str | Path,
    *,
    resume: Mapping[str, Any] | None = None,
    timer: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Start or resume one exact local compiler case.

    The function never opens a network connection. All writes are atomic,
    owner-only, and contained by ``case_root``.
    """

    started = timer()
    root = Path(case_root)
    _reject_symlink(root)
    case_path = root / "case.json"

    if resume is not None:
        if request is not None:
            raise CompilerError("provide either a new request or a resume, not both")
        return _resume_case(resume, root, started=started, timer=timer)
    if request is None:
        raise CompilerError("a new compile requires a request")

    if case_path.exists():
        case = _read_case(root)
        stored_identity = _read_indexed_json(root, case, "request_identity")
        if stable_input_hash(stored_identity) != stable_input_hash(_request_identity(request)):
            raise CompilerError("case root belongs to a different request")
        return _read_result(root)
    if root.exists() and any(root.iterdir()):
        raise CompilerError("case root is not empty and has no compiler case")

    normalized = _validate_request(request)
    request_identity = _request_identity(request)
    _prepare_directory(root)
    oem_map = normalized["oem_map"]
    request_hash = stable_input_hash(normalized)
    case_id = build_compile_case(
        source_hash=oem_map["source_sha256"],
        request_hash=request_hash,
        compiler_version=COMPILER_VERSION,
        state="running",
    )["case_id"]
    index: dict[str, dict[str, str]] = {}
    _store_json(
        root,
        index,
        "request_identity",
        "control/request-identity.json",
        request_identity,
    )
    _store_json(root, index, "request", "control/request.json", normalized)
    _store_json(root, index, "oem_map", "artifacts/oem-map.json", oem_map)
    if "source" in normalized and _requires_source_correction(oem_map):
        packet = _source_decision_packet(case_id, oem_map)
        _store_json(
            root,
            index,
            "source_packet",
            "control/source-packet.json",
            packet,
        )
        return _commit(
            root,
            normalized,
            case_id=case_id,
            state="awaiting-source-decision",
            index=index,
            receipts=[],
            target_statuses=[],
            next_action={
                "kind": "provide-corrected-source",
                "accepted_schema": COMPILE_REQUEST_SCHEMA_VERSION,
                "packet_id": packet["packet_id"],
                "reason": "material source exceptions remain grouped in one packet",
                "starts_new_case": True,
            },
            active_packet=packet,
            started=started,
            timer=timer,
        )
    return _advance(
        normalized,
        root,
        case_id=case_id,
        index=index,
        completed_receipts=[],
        binding=normalized.get("binding"),
        started=started,
        timer=timer,
    )


def _resume_case(
    raw_resume: Mapping[str, Any],
    root: Path,
    *,
    started: float,
    timer: Callable[[], float],
) -> dict[str, Any]:
    resume = _validate_resume(raw_resume)
    case = _read_case(root)
    if resume["case_id"] != case["case_id"]:
        raise CompilerError("resume case_id does not match the persisted case")
    resume_hash = stable_input_hash(resume)
    receipts = list(case.get("completed_receipts", ()))
    if any(
        isinstance(receipt, Mapping) and receipt.get("resume_hash") == resume_hash
        for receipt in receipts
    ):
        return _read_result(root)
    if resume["case_hash"] != stable_input_hash(case):
        raise CompilerError("resume has a stale case hash")
    request = _read_indexed_json(root, case, "request")
    oem_map = _read_indexed_json(root, case, "oem_map")
    selection_candidate: Mapping[str, Any] | None = None
    binding: Mapping[str, Any] | None = None
    receipt: dict[str, Any] = {"action": resume["action"], "resume_hash": resume_hash}
    if resume["action"] == "provide-binding" and case["state"] == "awaiting-binding":
        binding = resume.get("binding")
        if not isinstance(binding, Mapping):
            raise CompilerError("provide-binding resume requires a binding artifact")
        try:
            validate_device_binding(binding, oem_map)
        except CompilerContractError as exc:
            raise CompilerError(str(exc)) from exc
    elif (
        resume["action"] == "provide-selection-decision"
        and case["state"] == "awaiting-selection-decision"
    ):
        packet = _read_indexed_json(root, case, "selection_packet")
        candidate = resume.get("decision_candidate")
        try:
            normalized_decision = validate_decision_candidate(packet, candidate)
            receipt["decision_fingerprint"] = decision_reply_fingerprint(
                packet, candidate
            )
        except DecisionPacketError as exc:
            raise CompilerError(str(exc)) from exc
        prior_selection = _read_indexed_json(root, case, "selection")
        selection_candidate = _selection_candidate_from_decision(
            oem_map, prior_selection, packet, normalized_decision
        )
        stored_binding = request.get("binding")
        binding = stored_binding if isinstance(stored_binding, Mapping) else None
    else:
        raise CompilerError("resume action is not permitted by the current case state")
    receipts.append(receipt)
    next_index = {
        name: dict(record)
        for name, record in case["artifacts"].items()
        if name != "compile_result"
    }
    if selection_candidate is not None:
        _store_json(
            root,
            next_index,
            "selection_decision",
            "control/selection-decision.json",
            normalized_decision,
        )
    return _advance(
        request,
        root,
        case_id=case["case_id"],
        index=next_index,
        completed_receipts=receipts,
        binding=binding,
        selection_candidate=selection_candidate,
        started=started,
        timer=timer,
    )


def _advance(
    request: Mapping[str, Any],
    root: Path,
    *,
    case_id: str,
    index: dict[str, dict[str, str]],
    completed_receipts: list[dict[str, Any]],
    binding: Any,
    selection_candidate: Mapping[str, Any] | None = None,
    started: float,
    timer: Callable[[], float],
) -> dict[str, Any]:
    oem_map = request["oem_map"]
    try:
        bundle = compile_user_map_bundle(
            oem_map,
            selection_candidate or request["selection_candidate"],
            case_id=case_id,
        )
    except (CompilerContractError, UserMapError) as exc:
        raise CompilerError(str(exc)) from exc
    _store_json(root, index, "selection", "artifacts/selection.json", bundle["selection"])
    if bundle["decision_packet"] is not None:
        _store_json(
            root,
            index,
            "selection_packet",
            "control/selection-packet.json",
            bundle["decision_packet"],
        )
        return _commit(
            root,
            request,
            case_id=case_id,
            state="awaiting-selection-decision",
            index=index,
            receipts=completed_receipts,
            target_statuses=[],
            next_action={
                "kind": "provide-selection-decision",
                "accepted_schema": DECISION_CANDIDATE_SCHEMA_VERSION,
                "packet_id": bundle["decision_packet"]["packet_id"],
            },
            active_packet=bundle["decision_packet"],
            started=started,
            timer=timer,
        )

    _store_bundle(root, index, bundle)
    blocking_user_holds = _blocking_holds(bundle["user_map"])
    source_coverage = oem_map.get("source_coverage", {})
    coverage_incomplete = (
        not _pdf_coverage_complete(oem_map)
        if _is_pdf_source(oem_map)
        else bool(source_coverage)
        and (
            source_coverage.get("status") != "complete"
            or source_coverage.get("discovery_complete") is not True
        )
    )
    pdf_evidence_issues = pdf_completion_issues(oem_map, bundle["selection"])
    if blocking_user_holds or coverage_incomplete or pdf_evidence_issues:
        affected_count = sum(
            int(hold.get("affected_count", 1)) for hold in blocking_user_holds
        ) + len(pdf_evidence_issues)
        correction_issues = _correction_issue_refs(
            blocking_user_holds,
            coverage_incomplete=coverage_incomplete,
            pdf_evidence_issues=pdf_evidence_issues,
        )
        return _commit(
            root,
            request,
            case_id=case_id,
            state="partial",
            index=index,
            receipts=completed_receipts,
            target_statuses=[
                {"target": target, "status": "held"}
                for target in request["targets"]
            ],
            next_action={
                "kind": "provide-corrected-source",
                "accepted_schema": COMPILE_REQUEST_SCHEMA_VERSION,
                "starts_new_case": True,
                "reason": "selected points still have blocking source exceptions or unconfirmed PDF evidence; keep the useful output and start a corrected hash-bound case",
                "affected_count": max(1, affected_count),
                "issues": correction_issues,
                "evidence_artifact": "artifacts/oem-map.json",
            },
            started=started,
            timer=timer,
        )
    targets = request["targets"]
    if not targets:
        return _commit(
            root,
            request,
            case_id=case_id,
            state="offline-complete",
            index=index,
            receipts=completed_receipts,
            target_statuses=[],
            next_action={"kind": "none", "reason": "offline bundle complete"},
            started=started,
            timer=timer,
        )

    if binding is None:
        return _commit(
            root,
            request,
            case_id=case_id,
            state="awaiting-binding",
            index=index,
            receipts=completed_receipts,
            target_statuses=[{"target": target, "status": "held"} for target in targets],
            next_action={
                "kind": "provide-binding",
                "accepted_schema": "modbus-device-binding/v1",
                "affected_targets": list(targets),
            },
            started=started,
            timer=timer,
        )

    try:
        validate_device_binding(binding, oem_map)
        linked = link_selected_map(
            oem_map, bundle["selection"], bundle["user_map"], binding
        )
    except (CompilerContractError, MapLinkError) as exc:
        raise CompilerError(str(exc)) from exc
    _store_json(root, index, "binding", "control/device-binding.json", binding)
    _store_json(root, index, "linked_map", "artifacts/linked-map.json", linked)
    plan = _compile_plan(linked, binding)
    _store_json(root, index, "read_plan", "artifacts/read-plan.json", plan)

    try:
        pack = build_tool_pack(
            linked,
            plan,
            targets=targets,
            mode="final",
            target_options=request["target_options"],
        )
    except ExporterInputError as exc:
        raise CompilerError(str(exc)) from exc
    for artifact in pack.artifacts:
        _store_bytes(
            root,
            index,
            f"target:{artifact.path}",
            f"targets/{artifact.path}",
            artifact.content,
            schema_version="target-native",
        )
    statuses = [
        {"target": result.target, "status": result.status}
        for result in pack.target_results
    ]
    if all(item["status"] == "generated" for item in statuses):
        state = "complete"
        next_action = {"kind": "none", "reason": "all requested outputs complete"}
    elif _has_byte_order_hold(pack):
        state = "awaiting-physical-read"
        next_action = {
            "kind": "perform-physical-read",
            "reason": "byte order needs an immutable sample before confirmation",
            "separate_confirmation_required": True,
        }
    else:
        state = "partial"
        next_action = {
            "kind": "resolve-target-holds",
            "affected_targets": [
                item["target"] for item in statuses if item["status"] != "generated"
            ],
        }
    return _commit(
        root,
        request,
        case_id=case_id,
        state=state,
        index=index,
        receipts=completed_receipts,
        target_statuses=statuses,
        next_action=next_action,
        started=started,
        timer=timer,
    )


def _compile_plan(
    linked: Mapping[str, Any], binding: Mapping[str, Any]
) -> dict[str, Any]:
    constraints = binding.get("read_constraints", {})
    constraints = constraints if isinstance(constraints, Mapping) else {}
    options = {
        "max_gap": 0,
        "max_quantities": dict(constraints.get("max_quantities", {})),
        "readable_islands": list(constraints.get("readable_islands", ())),
        "unsafe_intervals": list(constraints.get("unsafe_intervals", ())),
    }
    raw = compile_read_plan(
        linked["points"],
        max_gap=0,
        max_quantities=options["max_quantities"],
        readable_islands=options["readable_islands"],
        unsafe_intervals=options["unsafe_intervals"],
    ).to_dict()
    raw["planning_options"] = options
    findings = list(raw.get("findings", ()))
    findings.extend(
        dict(hold) for hold in linked.get("holds", ()) if isinstance(hold, Mapping)
    )
    holds = [
        finding
        for finding in findings
        if str(finding.get("severity", "hold")).lower() in {"error", "hold"}
        and finding.get("blocking", True) is not False
    ]
    raw.update(
        {
            "findings": findings,
            "holds": holds,
            "has_holds": bool(holds),
            "status": "held" if holds else "planned",
        }
    )
    return artifact_envelope(
        raw,
        schema_version="modbus-read-plan/v1",
        inputs={"canonical_map": linked, "planning_options": options},
        findings=findings,
        holds=holds,
    )


def _selection_candidate_from_decision(
    oem_map: Mapping[str, Any],
    selection: Mapping[str, Any],
    packet: Mapping[str, Any],
    decision_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Translate one validated bounded decision into the existing typed shape."""

    packet_subjects = {
        str(subject)
        for decision in packet["decisions"]
        for subject in decision["subject_ids"]
    }
    entries = {
        str(entry["oem_point_id"]): dict(entry)
        for disposition in ("included", "suggested", "excluded")
        for entry in selection[disposition]
    }
    if not packet_subjects <= set(entries):
        raise CompilerError("selection packet subjects are stale against selection")
    decision = decision_candidate["decisions"][0]
    selected = set(decision.get("selected_subject_ids", ()))
    included: list[dict[str, Any]] = []
    suggested = [
        dict(entry)
        for entry in selection["suggested"]
        if entry["oem_point_id"] not in packet_subjects
    ]
    excluded = [
        dict(entry)
        for entry in selection["excluded"]
        if entry["oem_point_id"] not in packet_subjects
    ]
    fallback_intent = (
        selection["requested_measurements"][0]
        if selection["requested_measurements"]
        else "selected-point"
    )
    for point_id in sorted(packet_subjects):
        entry = entries[point_id]
        entry.update(
            {
                "reason": decision["reason"],
                "evidence_refs": list(decision["evidence_refs"]),
                "selection_basis": "typed-decision",
            }
        )
        if point_id in selected:
            entry["matched_intent"] = entry.get("matched_intent") or fallback_intent
            entry["match_quality"] = "override"
            included.append(entry)
        else:
            excluded.append(entry)
    return {
        "schema_version": "modbus-user-selection-candidate/v1",
        "oem_map_hash": stable_input_hash(oem_map),
        "requested_measurements": list(selection["requested_measurements"]),
        "included": included,
        "suggested": suggested,
        "excluded": excluded,
    }


def _source_decision_packet(
    case_id: str, oem_map: Mapping[str, Any]
) -> dict[str, Any]:
    subjects = sorted(
        {
            str(point_id)
            for hold in oem_map.get("holds", ())
            if isinstance(hold, Mapping)
            for point_id in hold.get("point_ids", ())
            if isinstance(point_id, str) and point_id
        }
    )
    if not subjects:
        subjects = ["source-document"]
    evidence_refs = sorted(
        {
            str(reference.get("region_id", reference.get("record_id", "")))
            for point in oem_map.get("points", ())
            if isinstance(point, Mapping)
            for reference in point.get("source_refs", ())
            if isinstance(reference, Mapping)
            and reference.get("region_id", reference.get("record_id"))
        }
    )
    if not evidence_refs:
        evidence_refs = [f"source-sha256:{oem_map['source_sha256']}"]
    return build_compiler_decision_packet(
        case_id=case_id,
        phase="source",
        source_hash=oem_map["source_sha256"],
        input_hashes={"oem_map": stable_input_hash(oem_map)},
        decisions=[
            {
                "decision_id": "source.resolve-exceptions",
                "subject_ids": subjects,
                "prompt": "Supply one corrected local source or typed replacement that resolves this grouped source exception packet.",
                "permitted_dispositions": ["supply-corrected-source"],
                "evidence_refs": evidence_refs,
            }
        ],
    )


def _blocking_holds(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        hold
        for hold in value.get("holds", ())
        if isinstance(hold, Mapping)
        and hold.get("blocking", True) is not False
        and str(hold.get("severity", "hold")).lower() in {"error", "hold"}
    ]


def _correction_issue_refs(
    holds: Sequence[Mapping[str, Any]],
    *,
    coverage_incomplete: bool,
    pdf_evidence_issues: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    issues = [
        {
            "code": str(hold.get("code", "source-exception")),
            "point_ids": sorted(
                str(point_id) for point_id in hold.get("point_ids", ())
            ),
            "field": hold.get("field"),
        }
        for hold in holds
    ]
    if coverage_incomplete:
        issues.append(
            {
                "code": "pdf-source-coverage-incomplete",
                "point_ids": [],
                "field": None,
            }
        )
    issues.extend(dict(issue) for issue in pdf_evidence_issues)
    return issues


def _is_pdf_source(oem_map: Mapping[str, Any]) -> bool:
    reference = oem_map.get("source_reference", {})
    return isinstance(reference, Mapping) and reference.get("format") == "pdf"


def _pdf_coverage_complete(oem_map: Mapping[str, Any]) -> bool:
    coverage = oem_map.get("source_coverage")
    if not isinstance(coverage, Mapping):
        return False
    detected = coverage.get("detected_pages")
    covered = coverage.get("covered_pages")
    return (
        coverage.get("status") == "complete"
        and coverage.get("discovery_complete") is True
        and isinstance(detected, Sequence)
        and not isinstance(detected, (str, bytes, bytearray))
        and bool(detected)
        and isinstance(covered, Sequence)
        and not isinstance(covered, (str, bytes, bytearray))
        and set(detected).issubset(set(covered))
    )


def _requires_source_correction(oem_map: Mapping[str, Any]) -> bool:
    """Return whether the source cannot produce a useful offline map.

    A source that yields zero points cannot proceed to selection at all, so
    it pauses immediately for a corrected source. Per-point exceptions
    (unresolved area, datatype, byte order, address convention, ...) belong
    in the generated map's exception annex instead: ``_advance`` already
    keeps every resolvable point, holds the affected ones, and groups the
    remaining exceptions into one ``provide-corrected-source`` packet. Both
    PDF and structured sources rely on that same downstream handling, so
    holds alone are not evidence that the user must replace the source
    document.
    """

    return not bool(oem_map.get("points"))


def _request_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompilerError("compile request must be an object")
    _assert_safe(value)
    identity = _json_copy(value)
    source = value.get("source")
    if source is not None:
        if not isinstance(source, Mapping):
            raise CompilerError("source must be an object")
        try:
            identity["source"] = source_request_identity(source)
        except SourceIntakeError as exc:
            raise CompilerError(str(exc)) from exc
    return {
        "schema_version": "modbus-compile-request-identity/v1",
        "request": identity,
    }


def _commit(
    root: Path,
    request: Mapping[str, Any],
    *,
    case_id: str,
    state: str,
    index: dict[str, dict[str, str]],
    receipts: list[dict[str, Any]],
    target_statuses: list[dict[str, str]],
    next_action: Mapping[str, Any],
    started: float,
    timer: Callable[[], float],
    active_packet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    elapsed_ms = max(0, round((timer() - started) * 1000, 3))
    oem_map = request["oem_map"]
    hashes = {
        "oem_map": stable_input_hash(oem_map),
        "request": stable_input_hash(request),
        "source": oem_map["source_sha256"],
    }
    for name in ("selection", "binding", "linked_map", "read_plan"):
        if name in index:
            hashes[name] = index[name]["sha256"]
    result = artifact_envelope(
        {
            "case_id": case_id,
            "state": state,
            "status": state,
            "artifact_index": {
                name: dict(record) for name, record in sorted(index.items())
            },
            "target_statuses": target_statuses,
            "elapsed_ms": elapsed_ms,
            "next_action": dict(next_action),
        },
        schema_version=COMPILE_RESULT_SCHEMA_VERSION,
        input_hashes=hashes,
        holds=(
            []
            if next_action.get("kind") == "none"
            else [
                {
                    "code": f"compiler.{state}",
                    "blocking": True,
                    "message": str(next_action.get("reason", next_action["kind"])),
                }
            ]
        ),
    )
    result_bytes = stable_json(result).encode("utf-8")
    _atomic_write(root, "compile-result.json", result_bytes)
    index["compile_result"] = _record(
        "compile-result.json", result_bytes, COMPILE_RESULT_SCHEMA_VERSION
    )
    case = build_compile_case(
        source_hash=oem_map["source_sha256"],
        request_hash=stable_input_hash(request),
        compiler_version=COMPILER_VERSION,
        state=state,
        artifacts=index,
        completed_receipts=receipts,
        active_packet=active_packet,
        requested_targets=request["targets"],
        next_action=next_action,
    )
    if case["case_id"] != case_id:
        raise CompilerError("case identity changed during compilation")
    _atomic_write(root, "case.json", stable_json(case).encode("utf-8"))
    return result


def _store_bundle(
    root: Path, index: dict[str, dict[str, str]], bundle: Mapping[str, Any]
) -> None:
    _store_json(root, index, "user_map", "output/user-map.json", bundle["user_map"])
    _store_bytes(
        root,
        index,
        "user_map_csv",
        "output/user-map.csv",
        str(bundle["csv"]).encode("utf-8"),
        schema_version="text/csv",
    )
    _store_bytes(
        root,
        index,
        "user_map_human",
        "output/user-map.md",
        str(bundle["human_summary"]).encode("utf-8"),
        schema_version="text/markdown",
    )
    _store_json(
        root,
        index,
        "user_map_manifest",
        "artifacts/user-map-manifest.json",
        bundle["manifest"],
    )


def _validate_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompilerError("compile request must be an object")
    _assert_safe(value)
    unknown = set(value) - _REQUEST_FIELDS
    if unknown:
        raise CompilerError("compile request has unknown fields: " + ", ".join(sorted(map(str, unknown))))
    if value.get("schema_version") != COMPILE_REQUEST_SCHEMA_VERSION:
        raise CompilerError(f"schema_version must be {COMPILE_REQUEST_SCHEMA_VERSION}")
    raw_oem_map = value.get("oem_map")
    raw_source = value.get("source")
    if (raw_oem_map is None) == (raw_source is None):
        raise CompilerError("compile request needs exactly one of oem_map or source")
    source_descriptor: Mapping[str, Any] | None = None
    if raw_source is not None:
        if not isinstance(raw_source, Mapping):
            raise CompilerError("source must be an object")
        try:
            oem_map, source_descriptor = compile_source_descriptor(raw_source)
        except SourceIntakeError as exc:
            raise CompilerError(str(exc)) from exc
    else:
        oem_map = raw_oem_map
    candidate = value.get("selection_candidate")
    template = value.get("selection_template")
    if raw_source is None and template is not None:
        raise CompilerError("selection_template is accepted only with a local source descriptor")
    if (candidate is None) == (template is None):
        raise CompilerError("compile request needs exactly one typed selection_candidate or selection_template")
    if not isinstance(oem_map, Mapping):
        raise CompilerError("compile request OEM map must be an object")
    try:
        validate_oem_map(oem_map)
    except CompilerContractError as exc:
        raise CompilerError(str(exc)) from exc
    if template is not None:
        if not isinstance(template, Mapping):
            raise CompilerError("selection_template must be an object")
        try:
            candidate = bind_selection_template(template, oem_map)
        except SourceIntakeError as exc:
            raise CompilerError(str(exc)) from exc
    if not isinstance(candidate, Mapping):
        raise CompilerError("selection_candidate must be a typed object")
    targets = _targets(value.get("targets", ()))
    options = value.get("target_options", {})
    if not isinstance(options, Mapping) or any(not isinstance(item, Mapping) for item in options.values()):
        raise CompilerError("target_options must map target IDs to objects")
    if set(options) - set(targets):
        raise CompilerError("target_options contains an unselected target")
    binding = value.get("binding")
    if binding is not None:
        if not isinstance(binding, Mapping):
            raise CompilerError("binding must be an object")
        try:
            validate_device_binding(binding, oem_map)
        except CompilerContractError as exc:
            raise CompilerError(str(exc)) from exc
    return _json_copy(
        {
            "schema_version": COMPILE_REQUEST_SCHEMA_VERSION,
            "oem_map": oem_map,
            "selection_candidate": candidate,
            **({"source": source_descriptor} if source_descriptor is not None else {}),
            "targets": targets,
            "target_options": {str(key): dict(options[key]) for key in sorted(options)},
            **({"binding": binding} if binding is not None else {}),
        }
    )


def _validate_resume(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompilerError("compile resume must be an object")
    unknown = set(value) - _RESUME_FIELDS
    if unknown:
        raise CompilerError("compile resume has unknown fields: " + ", ".join(sorted(map(str, unknown))))
    if value.get("schema_version") != COMPILE_RESUME_SCHEMA_VERSION:
        raise CompilerError(f"schema_version must be {COMPILE_RESUME_SCHEMA_VERSION}")
    _assert_safe(value)
    for field in ("case_id", "case_hash", "action"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise CompilerError(f"resume {field} must be non-empty text")
    return _json_copy(value)


def _targets(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompilerError("targets must be an array")
    targets = [str(item).strip().lower() for item in value]
    if len(targets) != len(set(targets)):
        raise CompilerError("targets must be unique")
    unknown = set(targets) - set(SUPPORTED_TARGETS)
    if unknown:
        raise CompilerError("unknown targets: " + ", ".join(sorted(unknown)))
    return [target for target in SUPPORTED_TARGETS if target in targets]


def _assert_safe(value: Any, path: str = "request") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in _UNSAFE_FIELDS:
                raise CompilerError(f"unsafe field is not accepted: {path}.{key}")
            _assert_safe(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _assert_safe(item, f"{path}[{index}]")


def _read_case(root: Path) -> dict[str, Any]:
    case = _read_json_file(root, "case.json")
    try:
        validate_compile_case(case)
    except CompilerContractError as exc:
        raise CompilerError(str(exc)) from exc
    if case.get("schema_version") != COMPILE_CASE_SCHEMA_VERSION:
        raise CompilerError("compile case contract version is incompatible")
    return case


def _read_indexed_json(
    root: Path, case: Mapping[str, Any], name: str
) -> dict[str, Any]:
    record = case.get("artifacts", {}).get(name)
    if not isinstance(record, Mapping):
        raise CompilerError(f"compile case is missing {name} artifact")
    relative = str(record.get("path", ""))
    target = _contained_path(root, relative)
    if target.is_symlink() or not target.is_file():
        raise CompilerError(f"case artifact is missing or unsafe: {relative}")
    data = target.read_bytes()
    if stable_input_hash(data) != record.get("sha256"):
        raise CompilerError(f"compile case {name} artifact hash is stale")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompilerError(f"case artifact is not valid JSON: {relative}") from exc
    if not isinstance(value, dict):
        raise CompilerError(f"case artifact must be an object: {relative}")
    return value


def _read_result(root: Path) -> dict[str, Any]:
    value = _read_json_file(root, "compile-result.json")
    if value.get("schema_version") != COMPILE_RESULT_SCHEMA_VERSION:
        raise CompilerError("compile result contract version is incompatible")
    return value


def _read_json_file(root: Path, relative: str) -> dict[str, Any]:
    target = _contained_path(root, relative)
    if target.is_symlink() or not target.is_file():
        raise CompilerError(f"case artifact is missing or unsafe: {relative}")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompilerError(f"case artifact is not valid JSON: {relative}") from exc
    if not isinstance(value, dict):
        raise CompilerError(f"case artifact must be an object: {relative}")
    return value


def _store_json(
    root: Path,
    index: dict[str, dict[str, str]],
    name: str,
    relative: str,
    value: Mapping[str, Any],
) -> None:
    data = stable_json(value).encode("utf-8")
    _store_bytes(
        root,
        index,
        name,
        relative,
        data,
        schema_version=str(value.get("schema_version", "json/v1")),
    )


def _store_bytes(
    root: Path,
    index: dict[str, dict[str, str]],
    name: str,
    relative: str,
    data: bytes,
    *,
    schema_version: str,
) -> None:
    _atomic_write(root, relative, data)
    index[name] = _record(relative, data, schema_version)


def _record(relative: str, data: bytes, schema_version: str) -> dict[str, str]:
    return {
        "path": relative,
        "sha256": stable_input_hash(data),
        "schema_version": schema_version,
    }


def _atomic_write(root: Path, relative: str, data: bytes) -> None:
    target = _contained_path(root, relative)
    _prepare_directory(target.parent)
    if target.exists() and target.is_symlink():
        raise CompilerError(f"refusing to replace symbolic-link artifact: {relative}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary_path = Path(temporary)
    try:
        stream = os.fdopen(descriptor, "wb")
    except BaseException:
        _close_descriptor(descriptor)
        _discard_temporary(temporary_path)
        raise
    try:
        with stream:
            _restrict_descriptor(descriptor)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    except BaseException:
        _discard_temporary(temporary_path)
        raise
    _restrict_path(target, _ARTIFACT_MODE)


def _contained_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or "\\" in relative:
        raise CompilerError("case artifact path must be normalized and case-relative")
    resolved_root = root.resolve()
    target = (resolved_root / Path(*pure.parts)).resolve(strict=False)
    if target != resolved_root and resolved_root not in target.parents:
        raise CompilerError("case artifact path escapes the case root")
    return target


def _prepare_directory(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise CompilerError(f"case directory must not be a symbolic link: {path.name}")
    path.mkdir(parents=True, exist_ok=True, mode=_CASE_DIRECTORY_MODE)
    _restrict_path(path, _CASE_DIRECTORY_MODE)


def _close_descriptor(descriptor: int) -> None:
    """Release a temporary descriptor so Windows can unlink or replace the file."""
    try:
        os.close(descriptor)
    except OSError:
        return


def _discard_temporary(path: Path) -> None:
    """Remove a partially written temporary artifact without masking the original error."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _restrict_descriptor(descriptor: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if fchmod is None:
        return
    try:
        fchmod(descriptor, _ARTIFACT_MODE)
    except (AttributeError, NotImplementedError, OSError):
        return


def _restrict_path(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except (NotImplementedError, OSError):
        return


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise CompilerError("case root must not be a symbolic link")


def _has_byte_order_hold(pack: Any) -> bool:
    return any(
        finding.code == "POINT_BYTE_ORDER_UNRESOLVED"
        for result in pack.target_results
        for finding in result.findings
    )


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise CompilerError("compiler input must be deterministic JSON") from exc


__all__ = [
    "COMPILE_REQUEST_SCHEMA_VERSION",
    "COMPILE_RESULT_SCHEMA_VERSION",
    "COMPILE_RESUME_SCHEMA_VERSION",
    "COMPILER_VERSION",
    "CompilerError",
    "compile_user_map",
]
