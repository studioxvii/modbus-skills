"""Safe declarative text rendering for documented Modbus tool formats.

This module does not evaluate Python, expressions, scripts, or environment
variables. Placeholders can only read keys from supplied mappings.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from string import Formatter
from typing import Any


class CustomFormatError(ValueError):
    """Raised when a custom format is unsafe or incomplete."""


_ALLOWED_CONFIG_KEYS = {
    "name",
    "header_template",
    "record_template",
    "footer_template",
    "line_ending",
    "escape_mode",
    "delimiter",
    "spreadsheet_safe",
    "missing",
    "field_map",
    "constants",
}
_COMMON_ENVELOPE_KEYS = {
    "schema_version",
    "artifact_type",
    "input_hashes",
    "assumptions",
    "findings",
    "holds",
}
_FIELD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*")
_MAX_TEMPLATE_CHARS = 100_000
_MAX_RECORDS = 100_000
_MAX_OUTPUT_CHARS = 5_000_000


def _templates(config: Mapping[str, Any]) -> list[tuple[str, Any]]:
    output = []
    for key in ("header_template", "record_template", "footer_template"):
        value = config.get(key)
        if value is not None:
            output.append((key, value))
    return output


def _is_json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _placeholder_names(template: str) -> list[str]:
    names: list[str] = []
    try:
        parsed = Formatter().parse(template)
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if format_spec or conversion:
                raise CustomFormatError("Format specifications and conversions are not allowed.")
            if not _FIELD_RE.fullmatch(field_name):
                raise CustomFormatError(f"Placeholder {field_name!r} is not a simple mapping path.")
            if any(part.startswith("_") or "__" in part for part in field_name.split(".")):
                raise CustomFormatError(f"Placeholder {field_name!r} accesses a private field.")
            names.append(field_name)
    except ValueError as exc:
        raise CustomFormatError(f"Template braces are invalid: {exc}") from exc
    return names


def validate_custom_format(
    config: Mapping[str, Any],
    *,
    available_fields: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic validation findings for a declarative format."""

    findings: list[dict[str, Any]] = []
    if not isinstance(config, Mapping):
        return [{"severity": "error", "code": "CONFIG_NOT_OBJECT", "message": "Format config must be an object."}]
    unknown_keys = [
        key
        for key in config
        if not isinstance(key, str)
        or key not in _ALLOWED_CONFIG_KEYS | _COMMON_ENVELOPE_KEYS
    ]
    for key in sorted(unknown_keys, key=repr):
        findings.append(
            {
                "severity": "error",
                "code": "UNKNOWN_CONFIG_KEY",
                "field": str(key),
                "message": f"Config key {key!r} is not supported.",
            }
        )
    record_template = config.get("record_template")
    if not isinstance(record_template, str) or not record_template:
        findings.append(
            {
                "severity": "error",
                "code": "RECORD_TEMPLATE_REQUIRED",
                "field": "record_template",
                "message": "A non-empty record_template is required.",
            }
        )

    all_names: list[tuple[str, str]] = []
    for key, template in _templates(config):
        if not isinstance(template, str):
            findings.append(
                {
                    "severity": "error",
                    "code": "TEMPLATE_NOT_TEXT",
                    "field": key,
                    "message": f"{key} must be text.",
                }
            )
            continue
        if len(template) > _MAX_TEMPLATE_CHARS:
            findings.append(
                {
                    "severity": "error",
                    "code": "TEMPLATE_TOO_LARGE",
                    "field": key,
                    "message": f"{key} exceeds {_MAX_TEMPLATE_CHARS} characters.",
                }
            )
        try:
            all_names.extend((key, name) for name in _placeholder_names(template))
        except CustomFormatError as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "INVALID_PLACEHOLDER",
                    "field": key,
                    "message": str(exc),
                }
            )

    name = config.get("name")
    if name is not None and not isinstance(name, str):
        findings.append(
            {
                "severity": "error",
                "code": "INVALID_FORMAT_NAME",
                "field": "name",
                "message": "name must be text when supplied.",
            }
        )
    line_ending = config.get("line_ending", "\n")
    if not isinstance(line_ending, str) or line_ending not in {"\n", "\r\n"}:
        findings.append(
            {
                "severity": "error",
                "code": "INVALID_LINE_ENDING",
                "field": "line_ending",
                "message": "line_ending must be LF or CRLF.",
            }
        )
    escape_mode = config.get("escape_mode", "none")
    if not isinstance(escape_mode, str) or escape_mode not in {"none", "csv", "json"}:
        findings.append(
            {
                "severity": "error",
                "code": "INVALID_ESCAPE_MODE",
                "field": "escape_mode",
                "message": "escape_mode must be none, csv, or json.",
            }
        )
    delimiter = config.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in {"\r", "\n", '"'}:
        findings.append(
            {
                "severity": "error",
                "code": "INVALID_DELIMITER",
                "field": "delimiter",
                "message": "delimiter must be one non-newline character other than a quote.",
            }
        )
    missing = config.get("missing", "error")
    if not isinstance(missing, str) or missing not in {"error", "empty"}:
        findings.append(
            {
                "severity": "error",
                "code": "INVALID_MISSING_POLICY",
                "field": "missing",
                "message": "missing must be error or empty.",
            }
        )
    spreadsheet_safe = config.get("spreadsheet_safe", True)
    if not isinstance(spreadsheet_safe, bool):
        findings.append(
            {
                "severity": "error",
                "code": "INVALID_SPREADSHEET_SAFE",
                "field": "spreadsheet_safe",
                "message": "spreadsheet_safe must be true or false.",
            }
        )

    field_map = config.get("field_map", {})
    if not isinstance(field_map, Mapping):
        findings.append(
            {
                "severity": "error",
                "code": "FIELD_MAP_NOT_OBJECT",
                "field": "field_map",
                "message": "field_map must be an object.",
            }
        )
        field_map = {}
    else:
        for alias, path in field_map.items():
            if not isinstance(alias, str) or not _FIELD_RE.fullmatch(alias) or "." in alias:
                findings.append(
                    {
                        "severity": "error",
                        "code": "INVALID_FIELD_ALIAS",
                        "field": "field_map",
                        "message": f"Field alias {alias!r} is invalid.",
                    }
                )
            if not isinstance(path, str) or not _FIELD_RE.fullmatch(path) or any(
                part.startswith("_") or "__" in part for part in str(path).split(".")
            ):
                findings.append(
                    {
                        "severity": "error",
                        "code": "INVALID_FIELD_PATH",
                        "field": "field_map",
                        "message": f"Field path {path!r} is invalid.",
                    }
                )

    constants = config.get("constants", {})
    if not isinstance(constants, Mapping):
        findings.append(
            {
                "severity": "error",
                "code": "CONSTANTS_NOT_OBJECT",
                "field": "constants",
                "message": "constants must be an object.",
            }
        )
        constants = {}
    else:
        for key, value in constants.items():
            if not isinstance(key, str) or not _FIELD_RE.fullmatch(key) or "." in key:
                findings.append(
                    {
                        "severity": "error",
                        "code": "INVALID_CONSTANT_NAME",
                        "field": "constants",
                        "message": f"Constant name {key!r} is invalid.",
                    }
                )
            if not _is_json_scalar(value):
                findings.append(
                    {
                        "severity": "error",
                        "code": "INVALID_CONSTANT_VALUE",
                        "field": "constants",
                        "message": f"Constant {key!r} must be a scalar value.",
                    }
                )

    if available_fields is not None:
        available: set[str] = {"index", "meta"}
        if not isinstance(available_fields, Sequence) or isinstance(
            available_fields, (str, bytes, bytearray)
        ):
            findings.append(
                {
                    "severity": "error",
                    "code": "AVAILABLE_FIELDS_NOT_ARRAY",
                    "field": "available_fields",
                    "message": "available_fields must be an array of field names.",
                }
            )
        else:
            for field in available_fields:
                if isinstance(field, str):
                    available.add(field)
                else:
                    findings.append(
                        {
                            "severity": "error",
                            "code": "INVALID_AVAILABLE_FIELD",
                            "field": "available_fields",
                            "message": f"Available field {field!r} is not text.",
                        }
                    )
        available.update(key for key in field_map if isinstance(key, str))
        available.update(key for key in constants if isinstance(key, str))
        for template_key, name in all_names:
            root = name.split(".", 1)[0]
            if root not in available:
                findings.append(
                    {
                        "severity": "error",
                        "code": "UNKNOWN_PLACEHOLDER",
                        "field": template_key,
                        "message": f"Placeholder {name!r} is not available.",
                    }
                )
    return findings


def _resolve_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise KeyError(path)
        current = current[segment]
    return current


def _spreadsheet_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.lstrip("\t\r ")
    if candidate.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _escape(value: Any, config: Mapping[str, Any]) -> str:
    if value is None:
        text_value: Any = ""
    else:
        text_value = value
    if config.get("spreadsheet_safe", True) and config.get("escape_mode", "none") == "csv":
        text_value = _spreadsheet_safe(text_value)
    mode = config.get("escape_mode", "none")
    if mode == "json":
        return json.dumps(text_value, ensure_ascii=False, separators=(",", ":"))
    if mode == "csv":
        stream = io.StringIO(newline="")
        csv.writer(
            stream,
            delimiter=config.get("delimiter", ","),
            quotechar='"',
            lineterminator="",
            quoting=csv.QUOTE_MINIMAL,
        ).writerow([text_value])
        return stream.getvalue()
    return "" if text_value is None else str(text_value)


def _render_template(template: str, values: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    output: list[str] = []
    for literal, field_name, _, _ in Formatter().parse(template):
        output.append(literal)
        if field_name is None:
            continue
        try:
            value = _resolve_path(values, field_name)
        except KeyError as exc:
            if config.get("missing", "error") == "empty":
                value = ""
            else:
                raise CustomFormatError(f"Placeholder {field_name!r} has no value.") from exc
        output.append(_escape(value, config))
    return "".join(output)


def render_custom_format(
    records: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Render records through a validated template without executing code."""

    findings = validate_custom_format(config)
    errors = [finding for finding in findings if finding.get("severity") == "error"]
    if errors:
        raise CustomFormatError("; ".join(finding["message"] for finding in errors))
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise CustomFormatError("Records must be an array of objects.")
    if len(records) > _MAX_RECORDS:
        raise CustomFormatError(f"Record count exceeds the {_MAX_RECORDS} record limit.")
    if any(not isinstance(record, Mapping) for record in records):
        raise CustomFormatError("Every record must be an object.")

    field_map: Mapping[str, str] = config.get("field_map", {})
    constants: Mapping[str, Any] = config.get("constants", {})
    meta = dict(metadata or {})
    meta.setdefault("record_count", len(records))
    meta.setdefault("format_name", config.get("name", "custom"))
    base_values = {**constants, "meta": meta}
    parts: list[str] = []

    header = config.get("header_template")
    if header is not None:
        parts.append(_render_template(header, base_values, config))
    record_template = config["record_template"]
    for index, record in enumerate(records):
        values = {**record, **constants, "index": index, "meta": meta}
        for alias, path in field_map.items():
            try:
                values[alias] = _resolve_path(record, path)
            except KeyError:
                if config.get("missing", "error") == "empty":
                    values[alias] = ""
                else:
                    raise CustomFormatError(f"Mapped field {path!r} for {alias!r} has no value.")
        parts.append(_render_template(record_template, values, config))
    footer = config.get("footer_template")
    if footer is not None:
        parts.append(_render_template(footer, base_values, config))

    rendered = config.get("line_ending", "\n").join(parts)
    if len(rendered) > _MAX_OUTPUT_CHARS:
        raise CustomFormatError(f"Rendered output exceeds the {_MAX_OUTPUT_CHARS} character limit.")
    return rendered


__all__ = [
    "CustomFormatError",
    "render_custom_format",
    "validate_custom_format",
]
