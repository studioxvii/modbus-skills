"""Interpret description suffixes only under an adjacent explicit table legend."""
from __future__ import annotations

import re
from typing import Any

_LEGEND = re.compile(
    r"\s*\*?\s*R\s*[-–—:=]\s*read[ -]only\s*,\s*"
    r"W\s*[-–—:=]\s*write[ -]only\s*,\s*"
    r"R/W\s*[-–—:=]\s*read and write\s*\.?\s*", re.IGNORECASE,
)
_SUFFIX = re.compile(r"\((R/W|R|W)\)\s*$", re.IGNORECASE)
_ACCESS = {
    "r": "R", "ro": "R", "read": "R", "read only": "R", "read-only": "R",
    "w": "W", "wo": "W", "write": "W", "write only": "W", "write-only": "W",
    "rw": "R/W", "r/w": "R/W", "read/write": "R/W", "read write": "R/W",
    "read-write": "R/W", "rwc": "R/W",
}


def is_access_legend(line: str) -> bool:
    return _LEGEND.fullmatch(line) is not None


def apply_access_legend(
    records: list[dict[str, Any]], *, page: int, line: int, literal: str,
    region: str | None = None,
) -> list[dict[str, Any]]:
    """Annotate caller-proved adjacent table rows; return explicit conflicts."""
    conflicts = []
    legend_locator = ({"page": page, "region": region} if region is not None
                      else {"page": page, "line": line, "region": f"p{page}:l{line}"})
    for record in records:
        match = _SUFFIX.search(str(record.get("description", "")))
        if match is None:
            continue
        access = match.group(1).upper()
        source = record["_source"]
        locator = next((dict(item["source_locator"]) for item in record.get("_claims", [])
                        if item.get("field") == "description" and item.get("source_locator")),
                       {key: source[key] for key in ("page", "line", "region") if key in source})
        existing = record.get("access")
        claim = {
            "parser_id": source["parser_id"], "field": "access", "value": access,
            "source_locator": locator, "method": "explicit-table-access-legend",
            "literal_annotation": match.group(0).strip(),
            "legend_source_locator": legend_locator, "legend_literal": literal.strip(),
        }
        if existing not in (None, "") and _ACCESS.get(str(existing).strip().casefold()) != access:
            conflicts.append({
                "code": "pdf-access-annotation-conflict", "_source": dict(source),
                "page": page, **({"line": source["line"]} if "line" in source else {}),
                "fields": ["access"],
                "explicit_access": existing, "annotation_access": access,
                "name": record.get("name"), "source_register": record.get("source_register"),
                "_claims": [*record.get("_claims", []), claim],
            })
            continue
        if existing in (None, ""):
            record["access"] = access
        record.setdefault("_claims", []).append(claim)
    return conflicts
