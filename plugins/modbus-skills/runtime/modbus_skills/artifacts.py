"""Common deterministic envelopes for public Modbus workflow artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any


_SCHEMA_VERSION = re.compile(r"^[a-z0-9][a-z0-9-]*/v[1-9][0-9]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ArtifactContractError(ValueError):
    """Raised when a public artifact cannot meet the common contract."""


def stable_input_hash(value: Any) -> str:
    """Hash one input without retaining its path or value in the artifact."""

    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, bytearray):
        payload = bytes(value)
    elif isinstance(value, memoryview):
        payload = value.tobytes()
    else:
        try:
            payload = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ArtifactContractError(
                "artifact inputs must have deterministic JSON forms or be bytes"
            ) from exc
    return hashlib.sha256(payload).hexdigest()


def hash_inputs(**values: Any) -> dict[str, str]:
    """Return stable semantic input names and SHA-256 digests only."""

    return {
        name: stable_input_hash(values[name])
        for name in sorted(values)
        if values[name] is not None
    }


def artifact_envelope(
    value: Mapping[str, Any],
    *,
    schema_version: str,
    artifact_type: str | None = None,
    inputs: Mapping[str, Any] | None = None,
    input_hashes: Mapping[str, str] | None = None,
    assumptions: Sequence[Any] | None = None,
    findings: Sequence[Any] | None = None,
    holds: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Add and validate the common public artifact fields.

    Existing payload fields stay intact. Explicit common-field arguments take
    precedence. Callers can provide values to hash or already-computed digests,
    but not both.
    """

    if not isinstance(value, Mapping):
        raise ArtifactContractError("artifact payload must be an object")
    if not _SCHEMA_VERSION.fullmatch(schema_version):
        raise ArtifactContractError(
            "schema_version must use a lowercase name followed by /vN"
        )
    if inputs is not None and input_hashes is not None:
        raise ArtifactContractError("provide inputs or input_hashes, not both")

    resolved_hashes = (
        hash_inputs(**dict(inputs or {}))
        if input_hashes is None
        else _validate_hashes(input_hashes)
    )
    resolved_type = artifact_type or schema_version.rsplit("/v", 1)[0]
    if not resolved_type or not isinstance(resolved_type, str):
        raise ArtifactContractError("artifact_type must be non-empty text")

    result = dict(value)
    result["schema_version"] = schema_version
    result["artifact_type"] = resolved_type
    result["input_hashes"] = resolved_hashes
    result["assumptions"] = _items(
        assumptions if assumptions is not None else result.get("assumptions", ())
    )
    result["findings"] = _items(
        findings if findings is not None else result.get("findings", ())
    )
    result["holds"] = _items(
        holds if holds is not None else result.get("holds", ())
    )
    return result


def assert_artifact_envelope(value: Any) -> None:
    """Validate the required common fields on one decoded JSON artifact."""

    if not isinstance(value, Mapping):
        raise ArtifactContractError("artifact must be a JSON object")
    missing = {
        "schema_version",
        "artifact_type",
        "input_hashes",
        "assumptions",
        "findings",
        "holds",
    } - set(value)
    if missing:
        raise ArtifactContractError(
            "artifact is missing common fields: " + ", ".join(sorted(missing))
        )
    schema_version = value["schema_version"]
    if not isinstance(schema_version, str) or not _SCHEMA_VERSION.fullmatch(
        schema_version
    ):
        raise ArtifactContractError("artifact schema_version is invalid")
    if not isinstance(value["artifact_type"], str) or not value["artifact_type"]:
        raise ArtifactContractError("artifact_type must be non-empty text")
    _validate_hashes(value["input_hashes"])
    for field in ("assumptions", "findings", "holds"):
        if not isinstance(value[field], list):
            raise ArtifactContractError(f"{field} must be an array")


def _validate_hashes(value: Mapping[str, str] | Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ArtifactContractError("input_hashes must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_digest in sorted(value.items(), key=lambda item: str(item[0])):
        name = str(raw_name)
        digest = str(raw_digest).lower()
        if not name or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ArtifactContractError(
                "input_hashes keys must be lowercase semantic names"
            )
        if not _SHA256.fullmatch(digest):
            raise ArtifactContractError("input_hashes values must be SHA-256 hex")
        result[name] = digest
    return result


def _items(value: Sequence[Any] | Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        raise ArtifactContractError("artifact collection fields must be arrays")
    return list(value)


__all__ = [
    "ArtifactContractError",
    "artifact_envelope",
    "assert_artifact_envelope",
    "hash_inputs",
    "stable_input_hash",
]
