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
    canonical_map_hash,
    normalize_mode,
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
    sensitive_paths = [
        *_find_sensitive_paths(map_value, path="canonical_map"),
        *_find_sensitive_paths(plan_value, path="read_plan"),
        *_find_sensitive_paths(target_options, path="target_options"),
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

    map_digest = canonical_map_hash(map_value)
    plan_digest = read_plan_hash(plan_value)
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
            stable_json(map_value),
            "canonical-map",
        ),
        Artifact.text(
            "read-plan.json",
            "application/json",
            stable_json(plan_value),
            "read-plan",
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
        "read_plan_hash": plan_digest,
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
        holds=[
            finding
            for finding in finding_values
            if str(finding.get("severity", "")).lower() in {"error", "hold"}
        ],
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
    return ToolPack(
        status=status,
        mode=mode,
        map_hash=map_digest,
        read_plan_hash=plan_digest,
        target_results=tuple(results),
        artifacts=artifacts,
    )


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

All selected targets were derived from `canonical-map.json` and
`read-plan.json`. The hashes in `manifest.json` identify those exact inputs.
Use `checksums.sha256` to detect changed files.

Read each target README before use. Review endpoint values, unit IDs, areas,
protocol offsets, quantities, poll intervals, datatypes, and byte orders.
Generated artifacts contain only Modbus read functions 01 through 04. They do
not perform address scans or device discovery.

Probe output is for raw capture. It is not a final decoding. Final output is
held when a required engineering value is unresolved.
"""
