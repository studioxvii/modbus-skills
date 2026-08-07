"""Explicit Modbus address conversion.

Address conversion never guesses a register area.  A conversion that lacks a
declared area or convention returns a structured hold.
"""

from __future__ import annotations

import re
from typing import Final

from .models import (
    AddressConvention,
    AddressResolution,
    Finding,
    FindingSeverity,
    RegisterArea,
    SourceAddress,
)


MIN_PROTOCOL_OFFSET: Final = 0
MAX_PROTOCOL_OFFSET: Final = 65_535

_AREA_PREFIX: Final[dict[RegisterArea, str]] = {
    RegisterArea.COIL: "0",
    RegisterArea.DISCRETE_INPUT: "1",
    RegisterArea.INPUT_REGISTER: "3",
    RegisterArea.HOLDING_REGISTER: "4",
}


def resolve_address(
    raw: str | int | None,
    convention: AddressConvention | str | None,
    area: RegisterArea | str | None,
) -> AddressResolution:
    """Convert a declared source address to a zero-based protocol offset.

    Supported conventions are:

    * ``protocol-offset``: 0 through 65535.
    * ``one-based-offset``: 1 through 65536.
    * ``modicon-reference``: explicit 0xxxx, 1xxxx, 3xxxx, or 4xxxx form.

    A five-digit Modicon reference can represent offsets 0 through 9998.  A
    six-digit reference can represent offsets 0 through 65535.  The explicit
    area must agree with the reference prefix.
    """

    resolved_area = RegisterArea.coerce(area)
    resolved_convention = AddressConvention.coerce(convention)
    source = SourceAddress(raw=raw, convention=resolved_convention)
    findings: list[Finding] = []

    if resolved_area is RegisterArea.UNKNOWN:
        findings.append(
            Finding(
                code="address.area-unresolved",
                severity=FindingSeverity.HOLD,
                message="Declare the Modbus area before address conversion.",
                field="area",
            )
        )

    if resolved_convention is AddressConvention.UNKNOWN:
        findings.append(
            Finding(
                code="address.convention-unresolved",
                severity=FindingSeverity.HOLD,
                message="Declare the source address convention.",
                field="source_address.convention",
            )
        )

    if findings:
        return AddressResolution(source, resolved_area, None, tuple(findings))

    try:
        if resolved_convention is AddressConvention.PROTOCOL_OFFSET:
            offset = _parse_offset(raw)
        elif resolved_convention is AddressConvention.ONE_BASED_OFFSET:
            one_based = _parse_offset(raw)
            if not 1 <= one_based <= MAX_PROTOCOL_OFFSET + 1:
                raise ValueError("one-based offset must be from 1 through 65536")
            offset = one_based - 1
        elif resolved_convention is AddressConvention.MODICON_REFERENCE:
            offset = _parse_modicon_reference(raw, resolved_area)
        else:  # Defensive guard for future enum values.
            raise ValueError("unsupported address convention")

        if not MIN_PROTOCOL_OFFSET <= offset <= MAX_PROTOCOL_OFFSET:
            raise ValueError("protocol offset must be from 0 through 65535")
    except (TypeError, ValueError) as error:
        findings.append(
            Finding(
                code="address.invalid",
                severity=FindingSeverity.ERROR,
                message=str(error),
                field="source_address.raw",
                details={"raw": raw, "convention": resolved_convention.value},
            )
        )
        return AddressResolution(source, resolved_area, None, tuple(findings))

    return AddressResolution(source, resolved_area, offset, ())


def format_modicon_reference(
    area: RegisterArea | str,
    protocol_offset: int,
    *,
    width: int | None = None,
) -> str:
    """Format an offset as an unambiguous Modicon display reference.

    ``width`` can be 5 or 6.  Automatic mode uses five digits when possible
    and six digits for larger offsets.
    """

    resolved_area = RegisterArea.coerce(area)
    if resolved_area is RegisterArea.UNKNOWN:
        raise ValueError("a known Modbus area is required")
    if isinstance(protocol_offset, bool) or not isinstance(protocol_offset, int):
        raise TypeError("protocol offset must be an integer")
    if not MIN_PROTOCOL_OFFSET <= protocol_offset <= MAX_PROTOCOL_OFFSET:
        raise ValueError("protocol offset must be from 0 through 65535")

    if width is None:
        width = 5 if protocol_offset <= 9_998 else 6
    if width not in {5, 6}:
        raise ValueError("Modicon reference width must be 5 or 6")
    if width == 5 and protocol_offset > 9_998:
        raise ValueError("five-digit references cannot preserve this area prefix")

    prefix = _AREA_PREFIX[resolved_area]
    suffix_width = width - 1
    return f"{prefix}{protocol_offset + 1:0{suffix_width}d}"


def _parse_offset(raw: str | int | None) -> int:
    if raw is None or isinstance(raw, bool):
        raise ValueError("address must be an integer")
    if isinstance(raw, int):
        return raw
    text = str(raw).strip().replace("_", "")
    if not text:
        raise ValueError("address must not be empty")
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", text):
        return int(text, 16)
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError("address must be a decimal integer or hexadecimal offset")
    return int(text, 10)


def _parse_modicon_reference(raw: str | int | None, area: RegisterArea) -> int:
    if raw is None or isinstance(raw, bool):
        raise ValueError("Modicon reference must be a decimal value")
    text = str(raw).strip().replace("_", "")
    if not re.fullmatch(r"\d+", text):
        raise ValueError("Modicon reference must contain decimal digits only")

    prefix = _AREA_PREFIX[area]
    if area is RegisterArea.COIL and isinstance(raw, int):
        # Integers cannot retain the leading zero used by coil references.
        if not 1 <= raw <= MAX_PROTOCOL_OFFSET + 1:
            raise ValueError("coil reference must be from 1 through 65536")
        return raw - 1

    if len(text) not in {5, 6}:
        raise ValueError("Modicon reference must use a five- or six-digit form")
    if text[0] != prefix:
        raise ValueError(
            f"reference prefix {text[0]} does not match area {area.value}"
        )
    reference_value = int(text[1:])
    if reference_value < 1:
        raise ValueError("Modicon reference suffix must start at 0001 or 00001")
    offset = reference_value - 1
    max_for_width = 9_998 if len(text) == 5 else MAX_PROTOCOL_OFFSET
    if offset > max_for_width:
        raise ValueError("Modicon reference is outside the declared area")
    return offset
