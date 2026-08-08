"""Bounded, read-only analysis for recorded Modbus samples."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .byte_order import RawSample, evaluate_byte_orders


class CaptureAnalysisError(ValueError):
    """Raised when a capture is invalid or exceeds a safety bound."""


_HARD_MAX_SAMPLES = 100_000
_HARD_MAX_POINTS = 10_000
_HARD_MAX_SPAN_SECONDS = 31 * 24 * 60 * 60
_MAX_EVENTS_PER_KIND = 20
_MAX_BYTE_ORDER_SAMPLES_PER_POINT = 256


def _point_id(value: Mapping[str, Any]) -> str | None:
    result = value.get("point_id", value.get("logical_point_id", value.get("id")))
    return str(result) if result not in (None, "") else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ValueError("timestamp is not finite")
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError("timestamp must be ISO-8601 text or Unix seconds")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an explicit timezone offset or Z")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def _is_error(sample: Mapping[str, Any]) -> bool:
    error = sample.get("error")
    return sample.get("success") is False or error not in (None, "", False)


def _response_stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "minimum_ms": None, "average_ms": None, "p95_ms": None, "maximum_ms": None}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "minimum_ms": ordered[0],
        "average_ms": sum(ordered) / len(ordered),
        "p95_ms": ordered[p95_index],
        "maximum_ms": ordered[-1],
    }


def _config_by_point(capture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = capture.get("points", ())
    if raw in (None, ""):
        return {}
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise CaptureAnalysisError("capture.points must be an array.")
    if len(raw) > _HARD_MAX_POINTS:
        raise CaptureAnalysisError(f"Point count exceeds the {_HARD_MAX_POINTS} point limit.")
    output: dict[str, Mapping[str, Any]] = {}
    for index, point in enumerate(raw):
        if not isinstance(point, Mapping):
            raise CaptureAnalysisError(f"capture.points[{index}] must be an object.")
        identifier = _point_id(point)
        if identifier is None:
            raise CaptureAnalysisError(f"capture.points[{index}] has no point ID.")
        if identifier in output:
            raise CaptureAnalysisError(f"Point ID {identifier!r} is duplicated in capture.points.")
        output[identifier] = point
    return output


def _setting(
    identifier: str,
    config: Mapping[str, Any],
    overrides: Mapping[str, Any] | None,
    *names: str,
) -> Any:
    if overrides and identifier in overrides:
        override = overrides[identifier]
        if isinstance(override, Mapping):
            for name in names:
                if name in override:
                    return override[name]
        else:
            return override
    for name in names:
        if name in config:
            return config[name]
    return None


def _numeric_setting(value: Any, label: str, *, minimum: float | None = None) -> float | None:
    if value in (None, ""):
        return None
    number = _number(value)
    if number is None or (minimum is not None and number < minimum):
        raise CaptureAnalysisError(f"{label} must be a finite number" + (f" >= {minimum}." if minimum is not None else "."))
    return number


def _is_discrete(config: Mapping[str, Any]) -> bool:
    if config.get("discrete") is True:
        return True
    datatype = str(config.get("datatype", config.get("data_type", ""))).lower()
    area = str(config.get("area", "")).lower().replace("_", "-")
    return datatype in {"bool", "boolean", "bit"} or area in {"coil", "coils", "discrete-input", "discrete-inputs"}


def _event(timestamp: datetime, **values: Any) -> dict[str, Any]:
    return {"timestamp": _iso(timestamp), **values}


def _byte_order_stability(
    identifier: str,
    config: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    raw_samples = [sample for sample in samples if sample.get("raw_words") is not None]
    if not raw_samples:
        return None, []
    if len(raw_samples) > _MAX_BYTE_ORDER_SAMPLES_PER_POINT:
        return (
            {
                "status": "held",
                "automatic_selection": False,
                "raw_sample_count": len(raw_samples),
                "candidates": [],
            },
            [
                {
                    "severity": "hold",
                    "code": "BYTE_ORDER_EVIDENCE_LIMIT",
                    "point_id": identifier,
                    "message": (
                        "Byte-order evidence exceeds the per-point limit of "
                        f"{_MAX_BYTE_ORDER_SAMPLES_PER_POINT} raw samples."
                    ),
                }
            ],
        )

    layouts = config.get("byte_order_layouts", config.get("candidate_layouts"))
    datatype = config.get("byte_order_datatype", config.get("datatype"))
    if isinstance(datatype, str) and datatype.strip().lower() in {
        "",
        "unknown",
        "unresolved",
        "pending",
    }:
        datatype = None
    scale = config.get("scale", 1.0)
    engineering_offset = config.get(
        "engineering_offset", config.get("offset", 0.0)
    )
    series: dict[tuple[str, str], list[Any]] = defaultdict(list)
    classifications: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    errors: list[dict[str, Any]] = []
    widths: set[int] = set()
    for sample in raw_samples:
        words = sample.get("raw_words")
        if not isinstance(words, Sequence) or isinstance(words, (str, bytes, bytearray)):
            errors.append(
                {
                    "timestamp": _iso(sample["timestamp"]),
                    "message": "raw_words must be an array of 16-bit integers.",
                }
            )
            continue
        widths.add(len(words))
        try:
            raw_sample = RawSample(
                str(sample.get("sample_id") or f"{identifier}-{sample['index']}"),
                tuple(words),
            )
            evaluation = evaluate_byte_orders(
                raw_sample,
                datatypes=datatype,
                layouts=layouts,
                scale=1.0 if scale is None else scale,
                engineering_offset=(
                    0.0 if engineering_offset is None else engineering_offset
                ),
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                {"timestamp": _iso(sample["timestamp"]), "message": str(exc)}
            )
            continue
        for candidate in evaluation.candidates:
            key = (candidate.layout, candidate.datatype.value)
            series[key].append(candidate.scaled_value)
            classifications[key][candidate.classification] += 1

    candidate_evidence = []
    minimum = _number(config.get("minimum"))
    maximum = _number(config.get("maximum"))
    for (layout, datatype_name), values in sorted(series.items()):
        serialized = []
        finite_values = []
        for value in values:
            if isinstance(value, float) and not math.isfinite(value):
                serialized_value: Any = (
                    "NaN"
                    if math.isnan(value)
                    else "Infinity"
                    if value > 0
                    else "-Infinity"
                )
            else:
                serialized_value = value
                finite_values.append(float(value))
            serialized.append(serialized_value)
        change_count = sum(
            left != right for left, right in zip(serialized, serialized[1:])
        )
        plausible_count = sum(
            (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum)
            for value in finite_values
        )
        candidate_evidence.append(
            {
                "layout": layout,
                "datatype": datatype_name,
                "sample_count": len(values),
                "finite_count": len(finite_values),
                "classification_counts": dict(sorted(classifications[(layout, datatype_name)].items())),
                "constant_across_capture": len(set(map(repr, serialized))) <= 1,
                "change_count": change_count,
                "minimum": min(finite_values) if finite_values else None,
                "maximum": max(finite_values) if finite_values else None,
                "plausible_range": {
                    "minimum": minimum,
                    "maximum": maximum,
                    "in_range_count": plausible_count,
                },
            }
        )
    findings = []
    if len(widths) > 1:
        errors.append(
            {
                "message": "Raw samples have different word counts and cannot share one byte-layout evaluation."
            }
        )
    if errors:
        findings.append(
            {
                "severity": "hold",
                "code": "BYTE_ORDER_EVIDENCE_INVALID",
                "point_id": identifier,
                "message": "Some raw samples could not be evaluated as byte-order evidence.",
                "count": len(errors),
                "examples": errors[:_MAX_EVENTS_PER_KIND],
            }
        )
    return (
        {
            "status": "partial" if errors else "evaluated",
            "automatic_selection": False,
            "raw_sample_count": len(raw_samples),
            "evaluated_sample_count": max(
                (candidate["sample_count"] for candidate in candidate_evidence),
                default=0,
            ),
            "candidate_count": len(candidate_evidence),
            "candidates": candidate_evidence,
            "errors": errors,
        },
        findings,
    )


def analyze_capture(
    capture: Mapping[str, Any],
    *,
    now: datetime | str | int | float | None = None,
    max_samples: int = _HARD_MAX_SAMPLES,
    expected_interval_seconds: float | Mapping[str, float] | None = None,
    stale_after_seconds: float | Mapping[str, float] | None = None,
    flatline_min_samples: int = 3,
    ranges: Mapping[str, Mapping[str, Any]] | None = None,
    rate_limits: Mapping[str, Any] | None = None,
    counter_specs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Analyze an in-memory capture without connecting to a device.

    The input is bounded before analysis. The function does not open sockets,
    discover endpoints, or create Modbus write requests.
    """

    if not isinstance(capture, Mapping):
        raise CaptureAnalysisError("Capture must be an object.")
    if not isinstance(max_samples, int) or max_samples < 1 or max_samples > _HARD_MAX_SAMPLES:
        raise CaptureAnalysisError(f"max_samples must be between 1 and {_HARD_MAX_SAMPLES}.")
    if not isinstance(flatline_min_samples, int) or flatline_min_samples < 2:
        raise CaptureAnalysisError("flatline_min_samples must be an integer of at least 2.")
    raw_samples = capture.get("samples")
    if not isinstance(raw_samples, Sequence) or isinstance(raw_samples, (str, bytes, bytearray)):
        raise CaptureAnalysisError("capture.samples must be an array.")
    if len(raw_samples) > max_samples:
        raise CaptureAnalysisError(f"Sample count {len(raw_samples)} exceeds the {max_samples} sample limit.")

    configurations = _config_by_point(capture)
    rejected: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for index, raw_sample in enumerate(raw_samples):
        if not isinstance(raw_sample, Mapping):
            rejected.append(
                {"index": index, "code": "SAMPLE_NOT_OBJECT", "message": "Sample is not an object."}
            )
            continue
        identifier = _point_id(raw_sample)
        if identifier is None:
            rejected.append(
                {"index": index, "code": "POINT_ID_MISSING", "message": "Sample has no point ID."}
            )
            continue
        try:
            timestamp = _timestamp(raw_sample.get("timestamp"))
        except (ValueError, OverflowError) as exc:
            rejected.append(
                {
                    "index": index,
                    "point_id": identifier,
                    "code": "TIMESTAMP_INVALID",
                    "message": str(exc),
                }
            )
            continue
        response = raw_sample.get("response_ms", raw_sample.get("response_time_ms"))
        response_number = _number(response) if response not in (None, "") else None
        if response not in (None, "") and (response_number is None or response_number < 0):
            rejected.append(
                {
                    "index": index,
                    "point_id": identifier,
                    "code": "RESPONSE_TIME_INVALID",
                    "message": "Response time must be a nonnegative finite number.",
                }
            )
            continue
        valid.append(
            {
                "index": index,
                "point_id": identifier,
                "timestamp": timestamp,
                "value": raw_sample.get("value"),
                "response_ms": response_number,
                "error": _is_error(raw_sample),
                "error_detail": raw_sample.get("error"),
                "sample_id": raw_sample.get("sample_id"),
                "raw_words": raw_sample.get(
                    "raw_words", raw_sample.get("raw_values", raw_sample.get("words"))
                ),
            }
        )

    if valid:
        start = min(sample["timestamp"] for sample in valid)
        end = max(sample["timestamp"] for sample in valid)
        span_seconds = (end - start).total_seconds()
        if span_seconds > _HARD_MAX_SPAN_SECONDS:
            raise CaptureAnalysisError("Capture span exceeds the 31-day safety limit.")
    else:
        start = end = None
        span_seconds = 0.0

    analysis_time: datetime | None
    deterministic_time_assumption = False
    if now is not None:
        try:
            analysis_time = _timestamp(now)
        except (ValueError, OverflowError) as exc:
            raise CaptureAnalysisError(f"now is invalid: {exc}") from exc
    elif capture.get("analysis_time") is not None:
        try:
            analysis_time = _timestamp(capture["analysis_time"])
        except (ValueError, OverflowError) as exc:
            raise CaptureAnalysisError(f"capture.analysis_time is invalid: {exc}") from exc
    else:
        analysis_time = end
        deterministic_time_assumption = analysis_time is not None

    findings: list[dict[str, Any]] = []
    duplicate_events: list[dict[str, Any]] = []
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for sample in sorted(valid, key=lambda item: (item["timestamp"], item["index"])):
        identity = (
            sample["point_id"],
            ("id", str(sample["sample_id"]))
            if sample["sample_id"] not in (None, "")
            else ("time", sample["timestamp"]),
        )
        if identity in seen:
            if len(duplicate_events) < _MAX_EVENTS_PER_KIND:
                duplicate_events.append(
                    _event(sample["timestamp"], point_id=sample["point_id"], sample_index=sample["index"])
                )
            continue
        seen.add(identity)
        unique.append(sample)
    if len(unique) != len(valid):
        findings.append(
            {
                "severity": "warning",
                "code": "DUPLICATE_SAMPLES",
                "message": f"Found {len(valid) - len(unique)} duplicate samples.",
                "count": len(valid) - len(unique),
                "examples": duplicate_events,
            }
        )

    samples_by_point: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in unique:
        samples_by_point[sample["point_id"]].append(sample)
    all_point_ids = sorted(set(configurations) | set(samples_by_point))
    if len(all_point_ids) > _HARD_MAX_POINTS:
        raise CaptureAnalysisError(f"Observed point count exceeds the {_HARD_MAX_POINTS} point limit.")

    point_results: dict[str, dict[str, Any]] = {}
    global_responses: list[float] = []
    global_error_count = 0
    missing_points = 0
    stale_points = 0
    total_missing_intervals = 0
    total_range_violations = 0
    total_rate_violations = 0
    total_resets = 0
    total_wraps = 0

    for identifier in all_point_ids:
        config = configurations.get(identifier, {})
        point_samples = sorted(samples_by_point.get(identifier, []), key=lambda item: item["timestamp"])
        successful = [sample for sample in point_samples if not sample["error"]]
        value_samples = [sample for sample in successful if sample.get("value") is not None]
        responses = [sample["response_ms"] for sample in point_samples if sample["response_ms"] is not None]
        global_responses.extend(responses)
        error_count = sum(1 for sample in point_samples if sample["error"])
        global_error_count += error_count

        point_result: dict[str, Any] = {
            "sample_count": len(point_samples),
            "successful_count": len(successful),
            "error_count": error_count,
            "first_sample": _iso(point_samples[0]["timestamp"]) if point_samples else None,
            "last_sample": _iso(point_samples[-1]["timestamp"]) if point_samples else None,
            "response_ms": _response_stats(responses),
            "missing_intervals": {"count": 0, "examples": []},
            "stale": False,
            "flatline": False,
            "range_violations": {"count": 0, "examples": []},
            "rate_of_change_violations": {"count": 0, "examples": []},
            "counter": {"resets": 0, "wraps": 0, "events": []},
            "discrete_transitions": {"count": 0, "events": []},
        }
        if error_count:
            findings.append(
                {
                    "severity": "warning",
                    "code": "POINT_COMMUNICATION_ERRORS",
                    "point_id": identifier,
                    "message": f"Point has {error_count} communication errors.",
                    "count": error_count,
                }
            )
        if identifier in configurations and not point_samples:
            missing_points += 1
            findings.append(
                {
                    "severity": "warning",
                    "code": "POINT_MISSING",
                    "point_id": identifier,
                    "message": "Expected point has no samples.",
                }
            )

        if isinstance(expected_interval_seconds, Mapping):
            interval_value = expected_interval_seconds.get(identifier)
        elif expected_interval_seconds is not None:
            interval_value = expected_interval_seconds
        else:
            interval_value = _setting(
                identifier,
                config,
                None,
                "expected_interval_seconds",
                "poll_interval_seconds",
            )
        interval = _numeric_setting(
            interval_value,
            f"Expected interval for {identifier}",
            minimum=0.001,
        )
        if interval is not None:
            gap_events = []
            missing_count = 0
            for previous, current in zip(point_samples, point_samples[1:]):
                gap = (current["timestamp"] - previous["timestamp"]).total_seconds()
                if gap > interval * 1.5:
                    estimated = max(1, math.floor(gap / interval) - 1)
                    missing_count += estimated
                    if len(gap_events) < _MAX_EVENTS_PER_KIND:
                        gap_events.append(
                            _event(
                                current["timestamp"],
                                previous_timestamp=_iso(previous["timestamp"]),
                                gap_seconds=gap,
                                estimated_missing=estimated,
                            )
                        )
            point_result["missing_intervals"] = {"count": missing_count, "examples": gap_events}
            total_missing_intervals += missing_count
            if missing_count:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "SAMPLE_GAPS",
                        "point_id": identifier,
                        "message": f"Estimated {missing_count} missing sample intervals.",
                        "count": missing_count,
                        "examples": gap_events,
                    }
                )

        if isinstance(stale_after_seconds, Mapping):
            stale_value = stale_after_seconds.get(identifier)
        elif stale_after_seconds is not None:
            stale_value = stale_after_seconds
        else:
            stale_value = _setting(identifier, config, None, "stale_after_seconds")
        stale_limit = _numeric_setting(stale_value, f"Stale threshold for {identifier}", minimum=0.0)
        stale_basis = successful[-1] if successful else None
        if stale_limit is not None and stale_basis is not None and analysis_time is not None:
            age = (analysis_time - stale_basis["timestamp"]).total_seconds()
            if age > stale_limit:
                point_result["stale"] = True
                point_result["stale_age_seconds"] = age
                stale_points += 1
                findings.append(
                    {
                        "severity": "warning",
                        "code": "POINT_STALE",
                        "point_id": identifier,
                        "message": f"Latest sample is {age:g} seconds old.",
                        "age_seconds": age,
                        "threshold_seconds": stale_limit,
                    }
                )

        comparable_values = [sample["value"] for sample in value_samples]
        if len(comparable_values) >= flatline_min_samples and all(
            value == comparable_values[0] for value in comparable_values[1:]
        ):
            point_result["flatline"] = True
            point_result["flatline_value"] = comparable_values[0]
            findings.append(
                {
                    "severity": "info",
                    "code": "POINT_FLATLINE",
                    "point_id": identifier,
                    "message": f"All {len(comparable_values)} usable samples have the same value.",
                    "value": comparable_values[0],
                }
            )

        minimum = _numeric_setting(
            _setting(identifier, config, ranges, "minimum", "min"),
            f"Minimum range for {identifier}",
        )
        maximum = _numeric_setting(
            _setting(identifier, config, ranges, "maximum", "max"),
            f"Maximum range for {identifier}",
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise CaptureAnalysisError(f"Range minimum exceeds maximum for {identifier}.")
        range_events = []
        range_count = 0
        for sample in value_samples:
            number = _number(sample["value"])
            if number is None:
                continue
            if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
                range_count += 1
                if len(range_events) < _MAX_EVENTS_PER_KIND:
                    range_events.append(_event(sample["timestamp"], value=sample["value"]))
        point_result["range_violations"] = {"count": range_count, "examples": range_events}
        total_range_violations += range_count
        if range_count:
            findings.append(
                {
                    "severity": "warning",
                    "code": "RANGE_VIOLATION",
                    "point_id": identifier,
                    "message": f"Found {range_count} out-of-range samples.",
                    "count": range_count,
                    "minimum": minimum,
                    "maximum": maximum,
                    "examples": range_events,
                }
            )

        rate_limit = _numeric_setting(
            _setting(identifier, config, rate_limits, "rate_of_change_limit", "maximum_rate_per_second"),
            f"Rate-of-change limit for {identifier}",
            minimum=0.0,
        )
        rate_events = []
        rate_count = 0
        if rate_limit is not None:
            numeric_samples = [(sample, _number(sample["value"])) for sample in value_samples]
            numeric_samples = [(sample, value) for sample, value in numeric_samples if value is not None]
            for (previous, previous_value), (current, current_value) in zip(numeric_samples, numeric_samples[1:]):
                elapsed = (current["timestamp"] - previous["timestamp"]).total_seconds()
                if elapsed <= 0:
                    continue
                rate = abs(current_value - previous_value) / elapsed
                if rate > rate_limit:
                    rate_count += 1
                    if len(rate_events) < _MAX_EVENTS_PER_KIND:
                        rate_events.append(
                            _event(
                                current["timestamp"],
                                previous_timestamp=_iso(previous["timestamp"]),
                                previous_value=previous["value"],
                                value=current["value"],
                                rate_per_second=rate,
                            )
                        )
        point_result["rate_of_change_violations"] = {"count": rate_count, "examples": rate_events}
        total_rate_violations += rate_count
        if rate_count:
            findings.append(
                {
                    "severity": "warning",
                    "code": "RATE_OF_CHANGE_VIOLATION",
                    "point_id": identifier,
                    "message": f"Found {rate_count} rate-of-change violations.",
                    "count": rate_count,
                    "limit_per_second": rate_limit,
                    "examples": rate_events,
                }
            )

        counter_config: Mapping[str, Any] = {}
        if counter_specs and isinstance(counter_specs.get(identifier), Mapping):
            counter_config = counter_specs[identifier]
        elif config.get("counter") is True or config.get("counter_modulus") is not None:
            counter_config = config
        if counter_config:
            modulus = _numeric_setting(
                counter_config.get("modulus", counter_config.get("counter_modulus")),
                f"Counter modulus for {identifier}",
                minimum=1.0,
            )
            wrap_high = _numeric_setting(counter_config.get("wrap_high_fraction", 0.9), "wrap_high_fraction", minimum=0.0)
            wrap_low = _numeric_setting(counter_config.get("wrap_low_fraction", 0.1), "wrap_low_fraction", minimum=0.0)
            if (wrap_high is not None and wrap_high > 1) or (wrap_low is not None and wrap_low > 1):
                raise CaptureAnalysisError("Counter wrap fractions must not exceed 1.")
            numeric_samples = [(sample, _number(sample["value"])) for sample in value_samples]
            numeric_samples = [(sample, value) for sample, value in numeric_samples if value is not None]
            counter_events = []
            reset_count = 0
            wrap_count = 0
            for (previous, previous_value), (current, current_value) in zip(numeric_samples, numeric_samples[1:]):
                if current_value >= previous_value:
                    continue
                is_wrap = bool(
                    modulus is not None
                    and previous_value >= modulus * (wrap_high if wrap_high is not None else 0.9)
                    and current_value <= modulus * (wrap_low if wrap_low is not None else 0.1)
                )
                event_type = "wrap" if is_wrap else "reset"
                wrap_count += int(is_wrap)
                reset_count += int(not is_wrap)
                if len(counter_events) < _MAX_EVENTS_PER_KIND:
                    counter_events.append(
                        _event(
                            current["timestamp"],
                            type=event_type,
                            previous_value=previous["value"],
                            value=current["value"],
                        )
                    )
            point_result["counter"] = {"resets": reset_count, "wraps": wrap_count, "events": counter_events}
            total_resets += reset_count
            total_wraps += wrap_count
            if reset_count:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "COUNTER_RESET",
                        "point_id": identifier,
                        "message": f"Detected {reset_count} counter resets.",
                        "count": reset_count,
                    }
                )

        if _is_discrete(config):
            transitions = []
            transition_count = 0
            for previous, current in zip(value_samples, value_samples[1:]):
                if current["value"] != previous["value"]:
                    transition_count += 1
                    if len(transitions) < _MAX_EVENTS_PER_KIND:
                        transitions.append(
                            _event(
                                current["timestamp"],
                                previous_value=previous["value"],
                                value=current["value"],
                            )
                        )
            point_result["discrete_transitions"] = {"count": transition_count, "events": transitions}
        byte_order_evidence, byte_order_findings = _byte_order_stability(
            identifier, config, successful
        )
        if byte_order_evidence is not None:
            point_result["byte_order_evidence"] = byte_order_evidence
            findings.extend(byte_order_findings)
        point_results[identifier] = point_result

    if global_error_count:
        findings.append(
            {
                "severity": "warning",
                "code": "CAPTURE_COMMUNICATION_ERRORS",
                "message": f"Capture contains {global_error_count} communication errors.",
                "count": global_error_count,
            }
        )
    findings.sort(key=lambda item: (str(item.get("point_id", "")), str(item.get("code", ""))))
    assumptions = []
    if deterministic_time_assumption:
        assumptions.append(
            {
                "code": "ANALYSIS_TIME_FROM_CAPTURE_END",
                "message": "No analysis time was supplied; the latest sample time was used for deterministic stale checks.",
            }
        )
    return {
        "contract": "modbus-capture-analysis/v1",
        "capture_id": capture.get("capture_id"),
        "read_only": True,
        "bounds": {
            "input_sample_count": len(raw_samples),
            "accepted_sample_count": len(valid),
            "unique_sample_count": len(unique),
            "point_count": len(all_point_ids),
            "start": _iso(start) if start else None,
            "end": _iso(end) if end else None,
            "span_seconds": span_seconds,
            "analysis_time": _iso(analysis_time) if analysis_time else None,
        },
        "summary": {
            "expected_points": len(configurations),
            "observed_points": len(samples_by_point),
            "missing_points": missing_points,
            "stale_points": stale_points,
            "duplicate_samples": len(valid) - len(unique),
            "estimated_missing_intervals": total_missing_intervals,
            "flatline_points": sum(1 for value in point_results.values() if value["flatline"]),
            "range_violations": total_range_violations,
            "rate_of_change_violations": total_rate_violations,
            "counter_resets": total_resets,
            "counter_wraps": total_wraps,
        },
        "communications": {
            "sample_count": len(unique),
            "error_count": global_error_count,
            "error_rate": global_error_count / len(unique) if unique else 0.0,
            "response_ms": _response_stats(global_responses),
        },
        "points": point_results,
        "findings": findings,
        "rejected_samples": rejected,
        "assumptions": assumptions,
    }


__all__ = ["CaptureAnalysisError", "analyze_capture"]
