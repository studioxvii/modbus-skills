"""Deterministic multi-target Modbus tool-pack orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .artifacts import artifact_envelope
from .exporters import (
    Artifact,
    ExportResult,
    ExporterInputError,
    block_point_ids,
    canonical_map_hash,
    normalize_mode,
    point_area,
    point_byte_order,
    point_datatype,
    point_id,
    point_name,
    point_protocol_offset,
    point_route_id,
    point_unit_id,
    point_word_count,
    read_plan_hash,
    stable_json,
)
from .modpoll import export_modpoll
from .modscan import export_modscan
from .node_red import export_node_red


TOOL_PACK_VERSION = "1.0.0"
TOOL_PACK_MANIFEST_SCHEMA_VERSION = "modbus-tool-pack-manifest/v1"
# Backward-compatible import name. Workflow outputs use modbus-tool-pack/v1.
TOOL_PACK_SCHEMA_VERSION = TOOL_PACK_MANIFEST_SCHEMA_VERSION
SUPPORTED_TARGETS = ("node-red", "modpoll", "modscan")
_SENSITIVE_SINGLE_KEY_PARTS = frozenset(
    {"password", "passwd", "token", "secret", "credential", "credentials"}
)
_SENSITIVE_KEY_PAIRS = frozenset({("api", "key"), ("private", "key")})
_PEM_KEY_BLOCK = re.compile(r"-{5}[A-Z ]+KEY-{5}")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_EMBEDDED_WINDOWS_PATH = re.compile(
    r"(?:^|[\s'\"`(=\[,])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
_EMBEDDED_WINDOWS_UNC = re.compile(r"(?:^|[\s'\"`(=\[,])\\\\[^\\\s]+\\")
_EMBEDDED_FORWARD_UNC = re.compile(r"(?:^|[\s'\"`(=\[,])//[^/\s]+/[^\s,;\"'`)\]}]+")
_EMBEDDED_UNIX_PATH = re.compile(
    r"(?:^|[\s'\"`(=:\[,])/(?!/)[^\s,;\"'`)\]}]+"
)
_EMBEDDED_TILDE_PATH = re.compile(r"(?:^|[\s'\"`(=:\[,])~[\\/][^\s,;\"'`)\]}]+")
_EMBEDDED_HTTP_ROUTE = re.compile(
    r"(?:GET\s+|[\"'](?:url|endpoint)[\"']\s*:\s*[\"']?)/modbus-dashboard\b",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"
    r"|\b(?:gh[opusr]_|github_pat_)[A-Za-z0-9_]{12,}"
    r"|\b(?:sk-(?:proj-)?|sk_live_)[A-Za-z0-9_-]{20,}"
    r"|\bglpat-[A-Za-z0-9_-]{20,}"
    r"|\bxox[baprs]-[A-Za-z0-9-]{12,}"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    r"|\b[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"
    r"|\b(?:password|passwd|api[_ -]?key|client[_ -]?secret)\s*[:=]\s*[^\s,;]{4,}"
    r")",
    re.IGNORECASE,
)
_PORTABLE_POINT_FIELDS = (
    "logical_point_id",
    "name",
    "route_id",
    "unit_id",
    "area",
    "protocol_offset",
    "datatype",
    "word_span",
    "word_count",
    "byte_order",
    "byte_order_confirmed",
    "byte_order_status",
    "scale",
    "engineering_offset",
    "engineering_unit",
    "function_code",
    "access",
    "normalization_status",
)
_PORTABLE_HOLD_FIELDS = (
    "code",
    "severity",
    "blocking",
    "point_ids",
    "field",
)
_PORTABLE_REQUEST_GROUPS = (
    ("request_id", ("request_id", "block_id", "id")),
    ("route_id", ("route_id", "route")),
    ("unit_id", ("unit_id", "unitId", "slave_id")),
    ("area", ("area", "object_type")),
    ("function_code", ("function_code", "function")),
    (
        "start_offset",
        ("start_offset", "start_address", "protocol_offset", "start"),
    ),
    ("quantity", ("quantity", "count", "size")),
    ("poll_interval_ms", ("poll_interval_ms", "interval_ms")),
)


@dataclass(frozen=True)
class ToolPack:
    """An in-memory, deterministic tool pack."""

    status: str
    mode: str
    map_hash: str
    read_plan_hash: str
    target_results: tuple[ExportResult, ...]
    artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ExporterInputError("A tool pack cannot contain duplicate artifact paths")
        if self.status not in {"generated", "partial", "held"}:
            raise ExporterInputError(f"Invalid tool-pack status: {self.status!r}")
        unsafe_paths = _find_unsafe_artifact_paths(self.artifacts)
        if unsafe_paths:
            raise ExporterInputError(
                "Tool-pack artifacts contain sensitive values or absolute local "
                "paths: " + ", ".join(unsafe_paths)
            )

    def files(self) -> dict[str, bytes]:
        return {artifact.path: artifact.content for artifact in self.artifacts}

    def to_zip_bytes(
        self, additional_artifacts: Sequence[Artifact] = ()
    ) -> bytes:
        """Return a byte-stable ZIP with fixed entry metadata.

        A caller can add control artifacts that are not part of the core pack
        checksum graph. This supports a result envelope inside the containing
        ZIP without a self-hash or a hash of the ZIP that contains it.
        """

        extras = tuple(additional_artifacts)
        if any(not isinstance(artifact, Artifact) for artifact in extras):
            raise TypeError("additional_artifacts must contain Artifact values")
        artifacts = (*self.artifacts, *extras)
        paths = [artifact.path for artifact in artifacts]
        if len(paths) != len(set(paths)):
            raise ExporterInputError(
                "Additional ZIP artifacts must not duplicate pack paths"
            )
        unsafe_paths = _find_unsafe_artifact_paths(artifacts)
        if unsafe_paths:
            raise ExporterInputError(
                "ZIP artifacts contain sensitive values or absolute local paths: "
                + ", ".join(unsafe_paths)
            )

        buffer = BytesIO()
        with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for artifact in sorted(artifacts, key=lambda value: value.path):
                info = ZipInfo(artifact.path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, artifact.content, compress_type=ZIP_DEFLATED, compresslevel=9)
        return buffer.getvalue()

    def write_to(self, destination: str | Path, *, overwrite: bool = False) -> tuple[Path, ...]:
        """Write files below an explicit destination.

        Existing files are preserved unless the caller explicitly sets
        ``overwrite=True``.
        """

        root = Path(destination).resolve()
        root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for artifact in sorted(self.artifacts, key=lambda value: value.path):
            target = (root / artifact.path).resolve()
            if root not in target.parents:
                raise ExporterInputError(f"Artifact escapes destination: {artifact.path!r}")
            if target.exists() and not overwrite:
                raise FileExistsError(f"Refusing to replace existing file: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(artifact.content)
            written.append(target)
        return tuple(written)


def build_tool_pack(
    canonical_map: Mapping[str, Any] | Any,
    read_plan: Mapping[str, Any] | Any,
    *,
    targets: Sequence[str],
    mode: str = "final",
    target_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> ToolPack:
    """Build any non-empty combination of Node-RED, Modpoll, and ModScan."""

    mode = normalize_mode(mode)
    map_value = _as_mapping(canonical_map, label="canonical_map")
    plan_value = _as_mapping(read_plan, label="read_plan")
    selected = _normalize_targets(targets)
    target_options = dict(target_options or {})
    map_digest = canonical_map_hash(map_value)
    plan_digest = read_plan_hash(plan_value)
    portable_map_value = _portable_runtime_map(map_value, map_digest=map_digest)
    portable_plan_value = _portable_read_plan(
        plan_value,
        plan_digest=plan_digest,
    )
    sensitive_paths = [
        *_find_sensitive_paths(map_value, path="canonical_map"),
        *_find_sensitive_value_paths(portable_map_value, path="canonical_map"),
        *_find_sensitive_paths(plan_value, path="read_plan"),
        *_find_sensitive_value_paths(plan_value, path="read_plan"),
        *_find_sensitive_paths(target_options, path="target_options"),
        *_find_sensitive_value_paths(target_options, path="target_options"),
    ]
    if sensitive_paths:
        raise ExporterInputError(
            "Tool-pack inputs contain sensitive fields or private-key material: "
            + ", ".join(sorted(set(sensitive_paths)))
        )
    absolute_local_path_fields = [
        *_find_absolute_local_path_fields(map_value, path="canonical_map"),
        *_find_absolute_local_path_fields(plan_value, path="read_plan"),
        *_find_absolute_local_path_fields(target_options, path="target_options"),
        *_find_embedded_local_path_fields(
            portable_map_value, path="canonical_map"
        ),
        *_find_embedded_local_path_fields(plan_value, path="read_plan"),
        *_find_embedded_local_path_fields(
            target_options, path="target_options"
        ),
    ]
    if absolute_local_path_fields:
        raise ExporterInputError(
            "Tool-pack inputs contain absolute local path values: "
            + ", ".join(sorted(set(absolute_local_path_fields)))
        )
    unknown_option_targets = set(target_options) - set(SUPPORTED_TARGETS)
    if unknown_option_targets:
        raise ExporterInputError(
            "target_options contains unknown targets: "
            + ", ".join(sorted(unknown_option_targets))
        )

    results: list[ExportResult] = []
    for target in selected:
        options = dict(target_options.get(target, {}))
        if target == "node-red":
            result = export_node_red(map_value, plan_value, mode=mode, options=options)
        elif target == "modpoll":
            result = export_modpoll(map_value, plan_value, mode=mode, options=options)
        else:
            result = export_modscan(map_value, plan_value, mode=mode, options=options)
        results.append(result)

    portable_map_digest = canonical_map_hash(portable_map_value)
    portable_plan_digest = read_plan_hash(portable_plan_value)
    for result in results:
        if result.map_hash != map_digest or result.read_plan_hash != plan_digest:
            raise ExporterInputError(
                f"Target {result.target!r} did not preserve the shared input hashes"
            )

    if all(result.status == "generated" for result in results):
        status = "generated"
    elif all(result.status == "held" for result in results):
        status = "held"
    else:
        status = "partial"

    content_artifacts: list[Artifact] = [
        Artifact.text(
            "canonical-map.json",
            "application/json",
            stable_json(portable_map_value),
            "portable-runtime-map",
        ),
        Artifact.text(
            "read-plan.json",
            "application/json",
            stable_json(portable_plan_value),
            "portable-read-plan",
        ),
    ]
    for result in results:
        content_artifacts.extend(result.artifacts)
    content_artifacts.append(
        Artifact.text(
            "README.md",
            "text/markdown",
            _pack_readme(mode=mode, status=status, results=results),
            "tool-pack-instructions",
        )
    )
    unsafe_artifacts = _find_unsafe_artifact_paths(content_artifacts)
    if unsafe_artifacts:
        raise ExporterInputError(
            "Generated tool-pack artifacts contain sensitive values or absolute "
            "local paths: " + ", ".join(unsafe_artifacts)
        )
    paths = [artifact.path for artifact in content_artifacts]
    if len(paths) != len(set(paths)):
        raise ExporterInputError("Target adapters produced duplicate tool-pack paths")

    finding_values = [
        {"target": result.target, **finding.to_dict()}
        for result in results
        for finding in result.findings
    ]
    manifest_value = artifact_envelope({
        "tool_pack_version": TOOL_PACK_VERSION,
        "status": status,
        "mode": mode,
        "map_hash": map_digest,
        "portable_map_hash": portable_map_digest,
        "read_plan_hash": plan_digest,
        "portable_read_plan_hash": portable_plan_digest,
        "targets": [result.to_manifest() for result in results],
        "artifacts": [
            artifact.to_manifest()
            for artifact in sorted(content_artifacts, key=lambda value: value.path)
        ],
        "safety": {
            "read_only": True,
            "allowed_function_codes": [1, 2, 3, 4],
            "network_discovery": False,
            "unresolved_final_values_generate_runnable_output": False,
        },
    },
        schema_version=TOOL_PACK_MANIFEST_SCHEMA_VERSION,
        artifact_type="modbus-tool-pack-manifest",
        input_hashes={
            "canonical_map": map_digest,
            "read_plan": plan_digest,
        },
        assumptions=[],
        findings=finding_values,
        holds=group_blocking_findings(finding_values),
    )
    manifest_artifact = Artifact.text(
        "manifest.json",
        "application/json",
        stable_json(manifest_value),
        "tool-pack-manifest",
    )
    checksummed = [*content_artifacts, manifest_artifact]
    checksum_text = "".join(
        f"{artifact.sha256}  {artifact.path}\n"
        for artifact in sorted(checksummed, key=lambda value: value.path)
    )
    checksum_artifact = Artifact.text(
        "checksums.sha256",
        "text/plain",
        checksum_text,
        "checksums",
    )
    artifacts = tuple([*content_artifacts, manifest_artifact, checksum_artifact])
    unsafe_artifacts = _find_unsafe_artifact_paths(artifacts)
    if unsafe_artifacts:
        raise ExporterInputError(
            "Generated tool-pack artifacts contain sensitive values or absolute "
            "local paths: " + ", ".join(unsafe_artifacts)
        )
    return ToolPack(
        status=status,
        mode=mode,
        map_hash=map_digest,
        read_plan_hash=plan_digest,
        target_results=tuple(results),
        artifacts=artifacts,
    )


def group_blocking_findings(
    findings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Group the same blocking problem across target adapters.

    Per-target findings remain available for audit. The top-level hold list is
    a human review queue, so it reports one problem with all affected targets
    instead of repeating the same text once per adapter.
    """

    grouped: dict[str, dict[str, Any]] = {}
    targets_by_key: dict[str, list[str]] = {}
    for finding in findings:
        if str(finding.get("severity", "")).lower() not in {"error", "hold"}:
            continue
        common = {
            str(key): value
            for key, value in finding.items()
            if key not in {"target", "targets"}
        }
        key = stable_json(common)
        grouped.setdefault(key, common)
        target = str(finding.get("target", "")).strip()
        if target and target not in targets_by_key.setdefault(key, []):
            targets_by_key[key].append(target)

    result: list[dict[str, Any]] = []
    target_order = {target: index for index, target in enumerate(SUPPORTED_TARGETS)}
    for key, common in grouped.items():
        targets = sorted(
            targets_by_key.get(key, ()),
            key=lambda target: (target_order.get(target, len(target_order)), target),
        )
        result.append({**common, **({"targets": targets} if targets else {})})
    return result


def _normalize_targets(targets: Sequence[str]) -> tuple[str, ...]:
    if isinstance(targets, (str, bytes, bytearray)):
        raise ExporterInputError("targets must be a sequence, not one string")
    requested = [str(target).strip().lower() for target in targets]
    if not requested:
        raise ExporterInputError("Select at least one target")
    if len(requested) != len(set(requested)):
        raise ExporterInputError("Select each target at most once")
    unknown = set(requested) - set(SUPPORTED_TARGETS)
    if unknown:
        raise ExporterInputError(
            "Unknown targets: " + ", ".join(sorted(unknown))
        )
    return tuple(target for target in SUPPORTED_TARGETS if target in requested)


def _as_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return result
    raise ExporterInputError(f"{label} must be a mapping or provide to_dict()")


def _portable_runtime_map(
    canonical_map: Mapping[str, Any], *, map_digest: str
) -> dict[str, Any]:
    """Return the minimum validated map data needed with a portable tool pack."""

    raw_points = canonical_map.get("points", canonical_map.get("registers", ()))
    points = (
        [_portable_point(point) for point in raw_points if isinstance(point, Mapping)]
        if isinstance(raw_points, Sequence)
        and not isinstance(raw_points, (str, bytes, bytearray))
        else []
    )

    holds = _portable_holds(
        canonical_map.get("holds", ()), invalid_code="map.hold-invalid"
    )

    return artifact_envelope(
        {"points": points},
        schema_version="modbus-runtime-map/v1",
        artifact_type="modbus-runtime-map",
        input_hashes={"canonical_map": map_digest},
        assumptions=[],
        findings=[],
        holds=holds,
    )


def _portable_read_plan(
    read_plan: Mapping[str, Any], *, plan_digest: str
) -> dict[str, Any]:
    """Return an allowlisted plan without caller-supplied audit metadata."""

    raw_requests = read_plan.get(
        "requests", read_plan.get("blocks", read_plan.get("read_blocks", ()))
    )
    requests: list[dict[str, Any]] = []
    if isinstance(raw_requests, Sequence) and not isinstance(
        raw_requests, (str, bytes, bytearray)
    ):
        for request in raw_requests:
            if not isinstance(request, Mapping):
                continue
            projected: dict[str, Any] = {}
            for canonical_field, aliases in _PORTABLE_REQUEST_GROUPS:
                present, selected_value = _selected_alias(request, aliases)
                if present:
                    projected[canonical_field] = selected_value
            if "end_offset" in request:
                projected["end_offset"] = request["end_offset"]
            if "point_ids" in request:
                raw_point_ids = request.get("point_ids")
                projected["point_ids"] = (
                    _portable_point_ids(raw_point_ids)
                    if isinstance(raw_point_ids, Sequence)
                    and not isinstance(raw_point_ids, (str, bytes, bytearray))
                    else []
                )
            elif "points" in request:
                raw_points = request.get("points")
                projected["points"] = (
                    [_portable_trace(trace) for trace in raw_points]
                    if isinstance(raw_points, Sequence)
                    and not isinstance(raw_points, (str, bytes, bytearray))
                    else []
                )
            requests.append(projected)

    holds = _portable_holds(
        read_plan.get("holds", ()), invalid_code="read-plan.hold-invalid"
    )
    payload: dict[str, Any] = {
        "requests": requests,
        "has_holds": bool(holds),
        "source_read_plan_hash": plan_digest,
    }
    input_hashes = {"source_read_plan": plan_digest}
    source_hashes = read_plan.get("input_hashes")
    if isinstance(source_hashes, Mapping):
        canonical_map_hash_value = source_hashes.get("canonical_map")
        if isinstance(canonical_map_hash_value, str) and re.fullmatch(
            r"[0-9a-fA-F]{64}", canonical_map_hash_value
        ):
            input_hashes["canonical_map"] = canonical_map_hash_value.lower()
        planning_options_hash = source_hashes.get("planning_options")
        if isinstance(planning_options_hash, str) and re.fullmatch(
            r"[0-9a-fA-F]{64}", planning_options_hash
        ):
            input_hashes["planning_options"] = planning_options_hash.lower()
    raw_options = read_plan.get("planning_options")
    if isinstance(raw_options, Mapping):
        portable_options = {
            field: raw_options[field]
            for field in ("max_gap", "max_quantities")
            if field in raw_options
        }
        payload["planning_options"] = portable_options
    return artifact_envelope(
        payload,
        schema_version="modbus-read-plan/v1",
        artifact_type="modbus-read-plan",
        input_hashes=input_hashes,
        assumptions=[],
        findings=[],
        holds=holds,
    )


def _selected_alias(
    value: Mapping[str, Any], aliases: Sequence[str]
) -> tuple[bool, Any]:
    for alias in aliases:
        if alias in value:
            return True, value[alias]
    return False, None


def _portable_trace(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return str(value) if value not in (None, "") else ""
    result: dict[str, Any] = {}
    present, identifier = _selected_alias(
        value, ("point_id", "logical_point_id", "id")
    )
    if present:
        result["logical_point_id"] = identifier
    present, protocol_offset = _selected_alias(
        value, ("protocol_offset", "pdu_offset")
    )
    if present:
        result["protocol_offset"] = protocol_offset
    present, span = _selected_alias(value, ("span", "word_span", "word_count"))
    if present:
        result["span"] = span
    for field in ("relative_offset", "canonical_identity"):
        if field in value:
            result[field] = value[field]
    return result


def _portable_point_ids(values: Sequence[Any]) -> list[Any]:
    """Preserve structured traces and filter unresolved scalar identifiers."""

    result: list[Any] = []
    for value in values:
        if isinstance(value, Mapping):
            result.append(_portable_trace(value))
        else:
            result.extend(block_point_ids({"point_ids": [value]}))
    return result


def _portable_holds(value: Any, *, invalid_code: str) -> list[dict[str, Any]]:
    holds: list[dict[str, Any]] = []
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return holds
    for hold in value:
        if isinstance(hold, Mapping):
            holds.append(
                {
                    field: hold[field]
                    for field in _PORTABLE_HOLD_FIELDS
                    if field in hold
                }
            )
        else:
            holds.append(
                {
                    "code": invalid_code,
                    "severity": "hold",
                    "blocking": True,
                }
            )
    return holds


def _portable_point(point: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only fields that a target adapter can place in output."""

    result = {
        field: point[field]
        for field in _PORTABLE_POINT_FIELDS
        if field in point
    }
    identifier = point_id(point)
    if identifier is not None:
        result["logical_point_id"] = identifier
        result["name"] = point_name(point, identifier)
    route_value = point.get("route_id", point.get("route"))
    if route_value not in (None, ""):
        result["route_id"] = point_route_id(point)
    unit_value = point_unit_id(point)
    if unit_value is not None:
        result["unit_id"] = unit_value
    area_value = point_area(point)
    if area_value is not None:
        result["area"] = area_value
    offset_value = point_protocol_offset(point)
    if offset_value is not None:
        result["protocol_offset"] = offset_value
    datatype_value = point_datatype(point)
    if datatype_value is not None:
        result["datatype"] = datatype_value
    word_count_value = point_word_count(point)
    if word_count_value is not None:
        result["word_span"] = word_count_value
        result.pop("word_count", None)
    byte_order_value = point_byte_order(point)
    if byte_order_value is not None:
        result["byte_order"] = byte_order_value
    engineering_offset = point.get("engineering_offset", point.get("offset"))
    if engineering_offset is not None:
        result["engineering_offset"] = engineering_offset
    engineering_unit = point.get("engineering_unit", point.get("unit"))
    if engineering_unit is not None:
        result["engineering_unit"] = engineering_unit
    return result


def _find_sensitive_paths(value: Any, *, path: str) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if _sensitive_key(key) and _has_value(child):
                findings.append(child_path)
                continue
            findings.extend(_find_sensitive_paths(child, path=child_path))
        return findings
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            findings.extend(_find_sensitive_paths(child, path=f"{path}[{index}]"))
        return findings
    if isinstance(value, str) and _PEM_KEY_BLOCK.search(value.upper()):
        findings.append(path)
    return findings


def _find_sensitive_value_paths(value: Any, *, path: str) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            findings.extend(
                _find_sensitive_value_paths(child, path=f"{path}.{raw_key}")
            )
        return findings
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            findings.extend(
                _find_sensitive_value_paths(child, path=f"{path}[{index}]")
            )
        return findings
    if isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        findings.append(path)
    return findings


def _find_absolute_local_path_fields(value: Any, *, path: str) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            findings.extend(
                _find_absolute_local_path_fields(
                    child,
                    path=f"{path}.{raw_key}",
                )
            )
        return findings
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            findings.extend(
                _find_absolute_local_path_fields(
                    child,
                    path=f"{path}[{index}]",
                )
            )
        return findings
    if isinstance(value, str) and _is_absolute_local_path(value):
        findings.append(path)
    return findings


def _find_embedded_local_path_fields(value: Any, *, path: str) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            findings.extend(
                _find_embedded_local_path_fields(
                    child, path=f"{path}.{raw_key}"
                )
            )
        return findings
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            findings.extend(
                _find_embedded_local_path_fields(
                    child, path=f"{path}[{index}]"
                )
            )
        return findings
    if isinstance(value, str) and _contains_embedded_local_path(value):
        findings.append(path)
    return findings


def _contains_embedded_local_path(value: str) -> bool:
    # HTTP route paths are intentionally slash-prefixed, but they are not
    # local filesystem paths. Remove only the explicit route forms before
    # applying the stricter local-path checks below.
    value = _EMBEDDED_HTTP_ROUTE.sub("route", value)
    lowered = value.lower()
    return (
        "file://" in lowered
        or _EMBEDDED_WINDOWS_PATH.search(value) is not None
        or _EMBEDDED_WINDOWS_UNC.search(value) is not None
        or _EMBEDDED_FORWARD_UNC.search(value) is not None
        or _EMBEDDED_UNIX_PATH.search(value) is not None
        or _EMBEDDED_TILDE_PATH.search(value) is not None
    )


def _find_unsafe_artifact_paths(artifacts: Sequence[Artifact]) -> list[str]:
    """Return paths of emitted text artifacts that violate export safety."""

    findings: list[str] = []
    for artifact in artifacts:
        text = artifact.content.decode("utf-8", errors="ignore")
        if (
            _PEM_KEY_BLOCK.search(text.upper()) is not None
            or _SENSITIVE_VALUE.search(text) is not None
            or _contains_embedded_local_path(text)
        ):
            findings.append(artifact.path)
    return sorted(set(findings))


def _is_absolute_local_path(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    return (
        candidate.startswith("/")
        or candidate.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(candidate) is not None
        or lowered.startswith("file://")
    )


def _sensitive_key(key: str) -> bool:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    parts = tuple(
        part.lower() for part in re.findall(r"[A-Za-z0-9]+", expanded) if part
    )
    if any(part in _SENSITIVE_SINGLE_KEY_PARTS for part in parts):
        return True
    return any(pair in tuple(zip(parts, parts[1:])) for pair in _SENSITIVE_KEY_PAIRS)


def _has_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (Mapping, Sequence)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return bool(value)
    return True


def _pack_readme(
    *, mode: str, status: str, results: Sequence[ExportResult]
) -> str:
    target_lines = "\n".join(
        f"- `{result.target}`"
        + (f" (`{result.profile}`)" if result.profile else "")
        + f": `{result.status}`"
        for result in results
    )
    return f"""# Modbus Tool Pack

- Mode: `{mode}`
- Status: `{status}`

## Selected targets

{target_lines}

`canonical-map.json` and `read-plan.json` are allowlisted portable runtime
projections. They exclude review records, source evidence, and other local-only
metadata. The source map and source read-plan hashes in `manifest.json` bind the
pack to the exact reviewed inputs. Use `checksums.sha256` to detect changed
portable files.

Read each target README before use. Review endpoint values, unit IDs, areas,
protocol offsets, quantities, poll intervals, datatypes, and byte orders.
Generated artifacts contain only Modbus read functions 01 through 04. They do
not perform address scans or device discovery.

Probe output is for raw capture. It is not a final decoding. Final output is
held when a required engineering value is unresolved.
"""
