"""Page-local explicit non-Modbus section evidence shared by PDF readers."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

EXCLUDED_PROTOCOL_CODE = "pdf-other-protocol-section"


def protocol_heading(line: str) -> str | None:
    """Recognize section titles, never a DNP3 substring in a row or sentence."""
    if len(line) > 300:
        return None
    label = re.sub(r"\s+", " ", line.strip()).casefold().rstrip(":")
    appendix = re.match(r"appendix [a-z0-9]+\s*:\s*", label)
    if appendix:
        label = label[appendix.end():]
    else:
        label = re.sub(r"^\d+(?:\.\d+)+\.?\s+", "", label)
    if "modbus" in label and "dnp3" in label:
        # A mixed title supplies no exclusive protocol scope.
        return "ambiguous" if "points list" in label or "register" in label else None
    prefix = r"[a-z][a-z0-9 /&()-]{0,79} " if appendix else ""
    if re.fullmatch(rf"(?:{prefix})?dnp3 points? list(?:\s*[-–—]\s*[a-z0-9 /&()-]{{1,80}})?", label):
        return "dnp3"
    if re.fullmatch(rf"(?:{prefix})?modbus (?:registers?(?: map| table| list)?|points? list)", label):
        return "modbus"
    return None


def protocol_contexts(lines: Sequence[str]) -> list[int | None]:
    """Return each line's explicit DNP3 heading index, reset on each page."""
    active = None
    result = []
    for index, line in enumerate(lines):
        heading = protocol_heading(line)
        if heading == "dnp3":
            active = index
        elif heading is not None:
            active = None
        result.append(active)
    return result


def protocol_rejection(
    line: str, *, page: int, line_number: int, heading_line: int,
    heading: str, parser_id: str, region: str | None = None,
) -> dict[str, Any] | None:
    """Bounded literal accounting: title plus numeric source rows, not full pages."""
    if line_number != heading_line and not re.search(r"\d", line):
        return None
    return {
        "code": EXCLUDED_PROTOCOL_CODE,
        "protocol": "DNP3",
        "page": page,
        "line": line_number,
        "parser_id": parser_id,
        "_source": {
            "format": "pdf", "page": page, "line": line_number,
            "region": region or f"p{page}:l{line_number}",
            "parser_id": parser_id, "excerpt": line.strip()[:300],
            "context_refs": [{"page": page, "line": heading_line,
                              "region": f"p{page}:l{heading_line}", "excerpt": heading.strip()[:300]}],
        },
    }
