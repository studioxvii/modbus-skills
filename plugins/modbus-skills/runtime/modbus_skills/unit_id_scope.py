"""Shared disclosure for the package's conservative Modbus unit-ID scope."""

from __future__ import annotations


UNIT_ID_SCOPE_NOTE = (
    "Unit ID 0 is forbidden because this package does not generate broadcast requests. "
    "Modbus TCP gateway unit IDs 0 and 255 are not accepted in this release."
)


def unit_id_error(label: str = "Unit ID") -> str:
    """Return the operator-facing error for an invalid unit identifier."""

    return f"{label} must be an integer from 1 through 247. {UNIT_ID_SCOPE_NOTE}"
