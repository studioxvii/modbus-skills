"""Deterministic byte-layout evaluation from one immutable raw sample."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
import struct
from typing import Any, Iterable

from .models import DataType


@dataclass(frozen=True, slots=True)
class RawSample:
    """One immutable set of 16-bit words read in protocol order."""

    sample_id: str
    words: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.sample_id.strip():
            raise ValueError("sample_id must not be empty")
        object.__setattr__(self, "words", tuple(self.words))
        if len(self.words) not in {1, 2, 4}:
            raise ValueError("a byte-order sample must contain one, two, or four words")
        for word in self.words:
            if isinstance(word, bool) or not isinstance(word, int):
                raise TypeError("sample words must be integers")
            if not 0 <= word <= 0xFFFF:
                raise ValueError("sample words must be from 0 through 65535")

    @property
    def bit_width(self) -> int:
        return len(self.words) * 16

    @property
    def raw_hex(self) -> tuple[str, ...]:
        return tuple(f"0x{word:04X}" for word in self.words)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "words": list(self.words),
            "raw_hex": list(self.raw_hex),
            "bit_width": self.bit_width,
        }


@dataclass(frozen=True, slots=True)
class ByteOrderCandidate:
    sample_id: str
    layout: str
    datatype: DataType
    ordered_hex: str
    decoded_value: int | float
    scaled_value: int | float
    scale: float
    engineering_offset: float
    classification: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "layout": self.layout,
            "datatype": self.datatype.value,
            "ordered_hex": self.ordered_hex,
            "decoded_value": _json_number(self.decoded_value),
            "scaled_value": _json_number(self.scaled_value),
            "scale": self.scale,
            "engineering_offset": self.engineering_offset,
            "classification": self.classification,
        }


@dataclass(frozen=True, slots=True)
class ByteOrderEvaluation:
    """All requested interpretations.  It intentionally has no winner field."""

    schema_version: str
    sample: RawSample
    candidates: tuple[ByteOrderCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sample": self.sample.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def all_modbus_layouts(bit_width: int) -> tuple[str, ...]:
    """Return explicit layouts for register permutations and byte swapping.

    For 16 bits this returns ``AB`` and ``BA``. For 32 bits this returns
    ``ABCD``, ``BADC``, ``CDAB``, and ``DCBA``.
    For 64 bits it returns 48 explicit layouts: every ordering of the four
    16-bit source words, with consistent normal or swapped byte order inside
    each word.  A caller can also pass any explicit ``ABCDEFGH`` permutation
    to :func:`evaluate_byte_orders`.
    """

    if bit_width not in {16, 32, 64}:
        raise ValueError("bit width must be 16, 32, or 64")
    word_count = bit_width // 16
    labels = "ABCDEFGH"[: bit_width // 8]
    words = tuple(labels[index : index + 2] for index in range(0, len(labels), 2))
    layouts: list[str] = []
    for permutation in itertools.permutations(range(word_count)):
        ordered_words = tuple(words[index] for index in permutation)
        layouts.append("".join(ordered_words))
        layouts.append("".join(word[::-1] for word in ordered_words))
    return tuple(layouts)


def evaluate_byte_orders(
    sample: RawSample,
    *,
    datatypes: Iterable[DataType | str] | DataType | str | None = None,
    layouts: Iterable[str] | str | None = None,
    scale: float = 1.0,
    engineering_offset: float = 0.0,
) -> ByteOrderEvaluation:
    """Evaluate byte layouts and types from one unchanged sample.

    Scaling uses ``decoded_value * scale + engineering_offset`` and therefore
    occurs only after the raw integer or IEEE-754 value is decoded.  Results
    are evidence only.  This function never ranks or selects a layout.
    """

    if not isinstance(sample, RawSample):
        raise TypeError("sample must be a RawSample")
    scale = _finite_number(scale, "scale")
    engineering_offset = _finite_number(engineering_offset, "engineering_offset")

    selected_layouts = (
        (layouts,)
        if isinstance(layouts, str)
        else tuple(layouts)
        if layouts is not None
        else all_modbus_layouts(sample.bit_width)
    )
    selected_layouts = tuple(
        _validate_layout(layout, sample.bit_width) for layout in selected_layouts
    )
    if len(set(selected_layouts)) != len(selected_layouts):
        raise ValueError("byte layouts must be unique")

    if datatypes is None:
        selected_types = (
            (DataType.UINT16, DataType.INT16)
            if sample.bit_width == 16
            else (DataType.UINT32, DataType.INT32, DataType.FLOAT32)
            if sample.bit_width == 32
            else (DataType.UINT64, DataType.INT64, DataType.FLOAT64)
        )
    elif isinstance(datatypes, (str, DataType)):
        selected_types = (DataType.coerce(datatypes),)
    else:
        selected_types = tuple(DataType.coerce(value) for value in datatypes)
    if not selected_types:
        raise ValueError("at least one data type is required")
    if DataType.UNKNOWN in selected_types:
        raise ValueError("unknown is not a decodable data type")
    if len(set(selected_types)) != len(selected_types):
        raise ValueError("data types must be unique")
    for datatype in selected_types:
        if datatype.bit_width != sample.bit_width:
            raise ValueError(
                f"{datatype.value} does not match the {sample.bit_width}-bit sample"
            )

    source_bytes = b"".join(word.to_bytes(2, "big") for word in sample.words)
    source_labels = "ABCDEFGH"[: len(source_bytes)]
    by_label = dict(zip(source_labels, source_bytes, strict=True))

    candidates: list[ByteOrderCandidate] = []
    for layout in selected_layouts:
        ordered = bytes(by_label[label] for label in layout)
        for datatype in selected_types:
            decoded = _decode(ordered, datatype)
            scaled = decoded * scale + engineering_offset
            candidates.append(
                ByteOrderCandidate(
                    sample_id=sample.sample_id,
                    layout=layout,
                    datatype=datatype,
                    ordered_hex=ordered.hex().upper(),
                    decoded_value=decoded,
                    scaled_value=scaled,
                    scale=scale,
                    engineering_offset=engineering_offset,
                    classification=_classify(decoded, datatype),
                )
            )

    return ByteOrderEvaluation(
        schema_version="modbus-byte-order-evidence/v1",
        sample=sample,
        candidates=tuple(candidates),
    )


def candidate_for(
    evaluation: ByteOrderEvaluation,
    layout: str,
    datatype: DataType | str,
) -> ByteOrderCandidate:
    """Return one exact candidate without assigning it special status."""

    normalized_type = DataType.coerce(datatype)
    normalized_layout = layout.strip().upper()
    matches = [
        candidate
        for candidate in evaluation.candidates
        if candidate.layout == normalized_layout and candidate.datatype is normalized_type
    ]
    if len(matches) != 1:
        raise KeyError(f"candidate not found: {normalized_layout}/{normalized_type.value}")
    return matches[0]


def _validate_layout(layout: str, bit_width: int) -> str:
    normalized = str(layout).strip().upper()
    expected = "ABCDEFGH"[: bit_width // 8]
    if len(normalized) != len(expected) or sorted(normalized) != sorted(expected):
        raise ValueError(
            f"layout must be an explicit permutation of {expected}: {layout!r}"
        )
    return normalized


def _decode(value: bytes, datatype: DataType) -> int | float:
    if datatype in {DataType.UINT16, DataType.UINT32, DataType.UINT64}:
        return int.from_bytes(value, "big", signed=False)
    if datatype in {DataType.INT16, DataType.INT32, DataType.INT64}:
        return int.from_bytes(value, "big", signed=True)
    if datatype is DataType.FLOAT32:
        return struct.unpack(">f", value)[0]
    if datatype is DataType.FLOAT64:
        return struct.unpack(">d", value)[0]
    raise ValueError(f"unsupported data type: {datatype.value}")


def _classify(value: int | float, datatype: DataType) -> str:
    if datatype not in {DataType.FLOAT32, DataType.FLOAT64}:
        return "integer"
    float_value = float(value)
    if math.isnan(float_value):
        return "nan"
    if math.isinf(float_value):
        return "positive-infinity" if float_value > 0 else "negative-infinity"
    if float_value == 0:
        return "negative-zero" if math.copysign(1.0, float_value) < 0 else "zero"
    minimum_normal = 2.0**-126 if datatype is DataType.FLOAT32 else 2.0**-1022
    if abs(float_value) < minimum_normal:
        return "subnormal"
    return "finite"


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _json_number(value: int | float) -> int | float | str:
    if isinstance(value, int):
        return value
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value
