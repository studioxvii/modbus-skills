"""Canonical, JSON-safe data models for the Modbus skills runtime.

The models in this module deliberately keep unresolved engineering values as
``None`` or ``UNKNOWN``.  Callers must not infer an address area, data type, or
byte layout merely to make a record pass validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any, ClassVar, Iterable, Mapping


class _StringEnum(str, Enum):
    """String-valued enum with conservative coercion."""

    @classmethod
    def coerce(cls, value: object) -> "_StringEnum":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls("unknown")
        normalized = str(value).strip().lower().replace("_", "-")
        try:
            return cls(normalized)
        except ValueError:
            return cls("unknown")


class RegisterArea(_StringEnum):
    COIL = "coil"
    DISCRETE_INPUT = "discrete-input"
    INPUT_REGISTER = "input-register"
    HOLDING_REGISTER = "holding-register"
    UNKNOWN = "unknown"


class AddressConvention(_StringEnum):
    PROTOCOL_OFFSET = "protocol-offset"
    ONE_BASED_OFFSET = "one-based-offset"
    MODICON_REFERENCE = "modicon-reference"
    UNKNOWN = "unknown"


class DataType(_StringEnum):
    BOOL = "bool"
    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    FLOAT32 = "float32"
    UINT64 = "uint64"
    INT64 = "int64"
    FLOAT64 = "float64"
    STRING = "string"
    UNKNOWN = "unknown"

    @property
    def span(self) -> int | None:
        """Return the number of Modbus registers used by this data type."""

        return {
            DataType.BOOL: 1,
            DataType.UINT16: 1,
            DataType.INT16: 1,
            DataType.UINT32: 2,
            DataType.INT32: 2,
            DataType.FLOAT32: 2,
            DataType.UINT64: 4,
            DataType.INT64: 4,
            DataType.FLOAT64: 4,
        }.get(self)

    @property
    def bit_width(self) -> int | None:
        span = self.span
        return span * 16 if span is not None else None


class FindingSeverity(_StringEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    HOLD = "hold"
    UNKNOWN = "unknown"


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class Finding:
    """A structured validation, planning, or conversion result."""

    code: str
    severity: FindingSeverity
    message: str
    point_ids: tuple[str, ...] = ()
    field: str | None = None
    details: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", FindingSeverity.coerce(self.severity))
        object.__setattr__(self, "point_ids", tuple(self.point_ids))
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "point_ids": list(self.point_ids),
            "details": dict(self.details),
        }
        if self.field is not None:
            result["field"] = self.field
        return result


@dataclass(frozen=True, slots=True)
class SourceAddress:
    """The address exactly as supplied and its declared convention."""

    raw: str | int | None
    convention: AddressConvention = AddressConvention.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "convention", AddressConvention.coerce(self.convention))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "SourceAddress":
        value = value or {}
        return cls(
            raw=value.get("raw"),
            convention=AddressConvention.coerce(value.get("convention")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "convention": self.convention.value}


@dataclass(frozen=True, slots=True)
class CanonicalPoint:
    """A point in the reviewed canonical map.

    ``protocol_offset`` is always the zero-based value placed in the Modbus
    protocol data unit.  It is never a 3xxxx or 4xxxx display reference.
    """

    SCHEMA_VERSION: ClassVar[str] = "modbus-map/v1"

    logical_point_id: str
    route_id: str
    unit_id: int | None
    area: RegisterArea
    protocol_offset: int | None
    source_address: SourceAddress
    datatype: DataType = DataType.UNKNOWN
    name: str | None = None
    word_span: int | None = None
    byte_order: str | None = None
    byte_order_confirmed: bool | None = None
    byte_order_status: str | None = None
    bit_order: str | None = None
    scale: float | None = None
    engineering_offset: float | None = None
    function_code: int | None = None
    access: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "area", RegisterArea.coerce(self.area))
        object.__setattr__(self, "datatype", DataType.coerce(self.datatype))
        normalized_access = (
            str(self.access).strip().lower().replace("_", "-").replace(" ", "-")
            if self.access not in (None, "")
            else None
        )
        object.__setattr__(self, "access", normalized_access)
        if not isinstance(self.source_address, SourceAddress):
            object.__setattr__(
                self,
                "source_address",
                SourceAddress.from_mapping(self.source_address),  # type: ignore[arg-type]
            )
        raw_order: Any = self.byte_order
        nested_status = None
        nested_confirmed = None
        if isinstance(raw_order, Mapping):
            nested_status = raw_order.get("status")
            nested_confirmed = raw_order.get("confirmed")
            raw_order = raw_order.get("layout", raw_order.get("value"))
        normalized_order = str(raw_order).strip().upper() if raw_order else None
        if normalized_order in {"", "?", "UNKNOWN", "UNRESOLVED"}:
            normalized_order = None
        object.__setattr__(self, "byte_order", normalized_order)
        status = self.byte_order_status if self.byte_order_status is not None else nested_status
        normalized_status = (
            str(status).strip().lower().replace("_", "-") if status not in (None, "") else None
        )
        object.__setattr__(self, "byte_order_status", normalized_status)
        confirmed_value = (
            self.byte_order_confirmed
            if self.byte_order_confirmed is not None
            else nested_confirmed
        )
        if confirmed_value is None and normalized_status is not None:
            if normalized_status in {"confirmed", "approved", "reviewed"}:
                confirmed_value = True
            elif normalized_status in {
                "pending",
                "candidate",
                "assumed",
                "unconfirmed",
                "unresolved",
            }:
                confirmed_value = False
        if confirmed_value is None and normalized_order is not None:
            confirmed_value = True
        object.__setattr__(self, "byte_order_confirmed", _optional_bool(confirmed_value))

    @property
    def effective_span(self) -> int | None:
        """Return an explicit span, or the deterministic data-type span."""

        return self.word_span if self.word_span is not None else self.datatype.span

    @property
    def canonical_identity(self) -> tuple[str, int, str, int, str] | None:
        """Return the complete identity, or ``None`` while it is unresolved."""

        if (
            not self.route_id
            or self.unit_id is None
            or self.area is RegisterArea.UNKNOWN
            or self.protocol_offset is None
            or not self.logical_point_id
        ):
            return None
        return (
            self.route_id,
            self.unit_id,
            self.area.value,
            self.protocol_offset,
            self.logical_point_id,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanonicalPoint":
        source_value = value.get("source_address")
        if isinstance(source_value, SourceAddress):
            source = source_value
        elif isinstance(source_value, Mapping):
            source = SourceAddress.from_mapping(source_value)
        else:
            source = SourceAddress(
                raw=value.get("source_address_raw"),
                convention=AddressConvention.coerce(
                    value.get("source_address_convention")
                ),
            )

        raw_byte_order = value.get("byte_order")
        if raw_byte_order is None:
            raw_byte_order = value.get("byte_layout")
        nested_byte_order = raw_byte_order if isinstance(raw_byte_order, Mapping) else {}
        byte_layout = (
            nested_byte_order.get("layout", nested_byte_order.get("value"))
            if nested_byte_order
            else raw_byte_order
        )
        byte_status = value.get("byte_order_status")
        if byte_status is None:
            byte_status = value.get("byte_layout_status")
        if byte_status is None:
            byte_status = nested_byte_order.get("status")
        byte_confirmed = value.get("byte_order_confirmed")
        if byte_confirmed is None:
            byte_confirmed = value.get("byte_layout_confirmed")
        if byte_confirmed is None:
            byte_confirmed = nested_byte_order.get("confirmed")

        return cls(
            logical_point_id=str(
                value.get("logical_point_id") or value.get("point_id") or ""
            ),
            name=(str(value["name"]) if value.get("name") is not None else None),
            route_id=str(value.get("route_id") or ""),
            unit_id=_optional_int(value.get("unit_id")),
            area=RegisterArea.coerce(value.get("area")),
            protocol_offset=_optional_int(value.get("protocol_offset")),
            source_address=source,
            datatype=DataType.coerce(value.get("datatype")),
            word_span=_optional_int(
                value.get("word_span", value.get("word_count", value.get("register_width")))
            ),
            byte_order=(str(byte_layout) if byte_layout is not None else None),
            byte_order_confirmed=_optional_bool(byte_confirmed),
            byte_order_status=(str(byte_status) if byte_status is not None else None),
            bit_order=(
                str(value["bit_order"]).strip().lower().replace("_", "-")
                if value.get("bit_order") not in (None, "")
                else None
            ),
            scale=_optional_float(value.get("scale")),
            engineering_offset=_optional_float(
                value.get("engineering_offset", value.get("offset"))
            ),
            function_code=_optional_int(value.get("function_code")),
            access=(str(value["access"]) if value.get("access") is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "logical_point_id": self.logical_point_id,
            "name": self.name,
            "route_id": self.route_id,
            "unit_id": self.unit_id,
            "area": self.area.value,
            "protocol_offset": self.protocol_offset,
            "source_address": self.source_address.to_dict(),
            "datatype": self.datatype.value,
            "word_span": self.word_span,
            "byte_order": self.byte_order,
            "byte_order_confirmed": self.byte_order_confirmed,
            "byte_order_status": self.byte_order_status,
            "bit_order": self.bit_order,
            "scale": self.scale,
            "engineering_offset": self.engineering_offset,
            "function_code": self.function_code,
            "access": self.access,
        }


def coerce_point(value: CanonicalPoint | Mapping[str, Any]) -> CanonicalPoint:
    if isinstance(value, CanonicalPoint):
        return value
    return CanonicalPoint.from_mapping(value)


def coerce_points(
    values: Iterable[CanonicalPoint | Mapping[str, Any]],
) -> tuple[CanonicalPoint, ...]:
    return tuple(coerce_point(value) for value in values)


@dataclass(frozen=True, slots=True)
class AddressResolution:
    source_address: SourceAddress
    area: RegisterArea
    protocol_offset: int | None
    findings: tuple[Finding, ...] = ()

    @property
    def resolved(self) -> bool:
        return (
            self.area is not RegisterArea.UNKNOWN
            and self.protocol_offset is not None
            and not any(
                finding.severity in {FindingSeverity.ERROR, FindingSeverity.HOLD}
                for finding in self.findings
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_address": self.source_address.to_dict(),
            "area": self.area.value,
            "protocol_offset": self.protocol_offset,
            "resolved": self.resolved,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True, slots=True)
class ReadPointTrace:
    logical_point_id: str
    protocol_offset: int
    span: int
    relative_offset: int
    canonical_identity: tuple[str, int, str, int, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_point_id": self.logical_point_id,
            "protocol_offset": self.protocol_offset,
            "span": self.span,
            "relative_offset": self.relative_offset,
            "canonical_identity": list(self.canonical_identity),
        }


@dataclass(frozen=True, slots=True)
class ReadBridgeTrace:
    """An unselected interval read under explicit continuous-read evidence."""

    start_offset: int
    end_offset: int
    readable_island_id: str
    reason: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if self.end_offset < self.start_offset:
            raise ValueError("a bridged read range cannot end before it starts")
        if not self.readable_island_id or not self.reason or not self.evidence_refs:
            raise ValueError("a bridged read range needs island identity and evidence")

    @property
    def quantity(self) -> int:
        return self.end_offset - self.start_offset + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "quantity": self.quantity,
            "readable_island_id": self.readable_island_id,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ReadRequest:
    request_id: str
    route_id: str
    unit_id: int
    area: RegisterArea
    function_code: int
    start_offset: int
    quantity: int
    points: tuple[ReadPointTrace, ...]
    readable_island_id: str | None = None
    bridged_ranges: tuple[ReadBridgeTrace, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "area", RegisterArea.coerce(self.area))
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "bridged_ranges", tuple(self.bridged_ranges))
        expected_function = {
            RegisterArea.COIL: 1,
            RegisterArea.DISCRETE_INPUT: 2,
            RegisterArea.HOLDING_REGISTER: 3,
            RegisterArea.INPUT_REGISTER: 4,
        }.get(self.area)
        if expected_function is None:
            raise ValueError("a read request requires a known Modbus area")
        if self.function_code != expected_function:
            raise ValueError(
                f"{self.area.value} read requests require FC{expected_function:02d}"
            )
        if isinstance(self.unit_id, bool) or not isinstance(self.unit_id, int):
            raise TypeError("read request unit ID must be an integer")
        if not 1 <= self.unit_id <= 247:
            raise ValueError("read request unit ID must be from 1 through 247")
        if isinstance(self.start_offset, bool) or not isinstance(self.start_offset, int):
            raise TypeError("read request start offset must be an integer")
        if not 0 <= self.start_offset <= 65_535:
            raise ValueError("read request start offset must be from 0 through 65535")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError("read request quantity must be an integer")
        maximum = 2_000 if self.area in {
            RegisterArea.COIL,
            RegisterArea.DISCRETE_INPUT,
        } else 125
        if not 1 <= self.quantity <= maximum:
            raise ValueError(
                f"read request quantity must be from 1 through {maximum}"
            )
        if self.start_offset + self.quantity - 1 > 65_535:
            raise ValueError("read request range extends beyond protocol offset 65535")

    @property
    def end_offset(self) -> int:
        return self.start_offset + self.quantity - 1

    def to_dict(self) -> dict[str, Any]:
        result = {
            "request_id": self.request_id,
            "route_id": self.route_id,
            "unit_id": self.unit_id,
            "area": self.area.value,
            "function_code": self.function_code,
            "start_offset": self.start_offset,
            "quantity": self.quantity,
            "end_offset": self.end_offset,
            "points": [point.to_dict() for point in self.points],
        }
        if self.readable_island_id is not None:
            result["readable_island_id"] = self.readable_island_id
        if self.bridged_ranges:
            result["bridged_ranges"] = [item.to_dict() for item in self.bridged_ranges]
        return result


@dataclass(frozen=True, slots=True)
class ReadPlan:
    SCHEMA_VERSION: ClassVar[str] = "modbus-read-plan/v1"

    requests: tuple[ReadRequest, ...]
    findings: tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requests", tuple(self.requests))
        object.__setattr__(self, "findings", tuple(self.findings))

    @property
    def has_holds(self) -> bool:
        return any(
            finding.severity in {FindingSeverity.ERROR, FindingSeverity.HOLD}
            for finding in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "requests": [request.to_dict() for request in self.requests],
            "findings": [finding.to_dict() for finding in self.findings],
            "has_holds": self.has_holds,
        }


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid integers")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("Fractional or non-finite values are not valid integers")
        return int(value)
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError("Integer values must contain decimal digits only")
    return int(text)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean values are not valid numbers")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Numbers must be finite")
    return result


def _optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError("Confirmation values must be true or false")
