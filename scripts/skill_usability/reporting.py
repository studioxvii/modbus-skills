"""Sanitized campaign reports for skill usability trials."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import REPORT_SCHEMA, validate_report_shape


_ABS_POSIX = re.compile(r"/(?:Users|home)/[^/\s]+/")
_ABS_WINDOWS = re.compile(r"[A-Za-z]:[\\/][^\s]+")
_UNC = re.compile(r"\\\\[^\\\s]+\\[^\\\s]+")
_URL_SECRET = re.compile(r"https?://[^\s]*?(?:token|key|password|secret)=[^\s&]+", re.IGNORECASE)
_CREDENTIAL = re.compile(r"(?:sk-|ghp_|github_pat_|password\s*=)\S+", re.IGNORECASE)
PASS_COUNTED = frozenset({"passed", "failed"})


def sanitize_text(value: str) -> str:
    text = _ABS_POSIX.sub("<path>", value)
    text = _ABS_WINDOWS.sub("<path>", text)
    text = _UNC.sub("<path>", text)
    text = _URL_SECRET.sub("<redacted-url>", text)
    text = _CREDENTIAL.sub("<redacted>", text)
    return text


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, Mapping):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize(item) for item in value]
    return value


def aggregate_status(statuses: Sequence[str]) -> str:
    values = list(statuses)
    if any(item == "failed" for item in values):
        return "failed"
    if any(item == "inconclusive" for item in values):
        return "inconclusive"
    if values and all(item == "not-run" for item in values):
        return "not-run"
    if any(item == "blocked" for item in values):
        return "blocked"
    if values and all(item == "passed" for item in values):
        return "passed"
    return "inconclusive"


def build_report(
    *,
    campaign: Mapping[str, Any],
    trials: Sequence[Mapping[str, Any]],
    mode: str,
    adapter: str,
    plugin_hash: str | None,
) -> dict[str, Any]:
    statuses = [str(trial.get("status")) for trial in trials]
    repetitions = int(campaign.get("real_model_repetitions") or campaign.get("repetitions") or 1) if mode == "real-model" else 1
    expected = {
        (name, repetition)
        for name in campaign["scenarios"]
        for repetition in range(1, repetitions + 1)
    }
    observed = [(trial.get("scenario_id"), trial.get("repetition", 1)) for trial in trials]
    complete = len(observed) == len(expected) and set(observed) == expected
    issue_codes = sorted(
        {
            str(code)
            for trial in trials
            for code in trial.get("issue_codes", ())
        }
    )
    if not complete:
        issue_codes.append("campaign-coverage-incomplete")
    public_trials = [
        {
            "scenario_id": trial.get("scenario_id"),
            "status": trial.get("status"),
            "issue_codes": list(trial.get("issue_codes", ())),
            "dimensions": trial.get("dimensions") or {},
            "event_count": int(trial.get("event_count") or 0),
            "terminal_reason": trial.get("terminal_reason") or trial.get("status"),
            "repetition": int(trial.get("repetition") or 1),
        }
        for trial in trials
    ]
    report = {
        "schema_version": REPORT_SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "run_id": str(uuid.uuid4()),
        "mode": mode,
        "adapter": adapter,
        "worker_model": "fake" if mode == "deterministic" else campaign["worker_model"],
        "status": aggregate_status(statuses if complete else [*statuses, "inconclusive"]),
        "evidence_class": "simulated-user" if mode == "deterministic" else "deterministic",
        "issue_codes": issue_codes,
        "trials": public_trials,
        "coverage": {
            "scenario_count": len(campaign.get("loaded_scenarios") or campaign.get("scenarios") or ()),
            "scenarios": [trial["scenario_id"] for trial in public_trials],
        },
        "hashes": {"plugin": plugin_hash},
        "versions": {
            "campaign": campaign.get("version"),
            "adapter": adapter,
        },
        "cleanup": {"required": True},
        "pass_counts": {
            "passed": sum(1 for item in statuses if item == "passed"),
            "failed": sum(1 for item in statuses if item == "failed"),
            "blocked": sum(1 for item in statuses if item == "blocked"),
            "not-run": sum(1 for item in statuses if item == "not-run"),
            "inconclusive": sum(1 for item in statuses if item == "inconclusive"),
        },
    }
    if mode == "deterministic":
        report["evidence_class"] = "deterministic"
    sanitized = sanitize(report)
    validate_report_shape(sanitized)
    return sanitized


def markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Skill usability report",
        "",
        f"- Campaign: `{report['campaign_id']}`",
        f"- Mode: `{report['mode']}`",
        f"- Adapter: `{report['adapter']}`",
        f"- Worker model: `{report['worker_model']}`",
        f"- Status: `{report['status']}`",
        f"- Evidence class: `{report['evidence_class']}`",
        "",
        "| Scenario | Status | Issues |",
        "| --- | --- | --- |",
    ]
    for trial in report["trials"]:
        issues = ", ".join(trial.get("issue_codes") or ()) or "none"
        lines.append(
            f"| `{trial['scenario_id']}` | `{trial['status']}` | {issues} |"
        )
    lines.append("")
    counts = report.get("pass_counts", {})
    lines.append(
        "Blocked, not-run, and inconclusive trials are reported separately and are not counted as passes."
    )
    lines.append(
        f"Counts: passed={counts.get('passed', 0)}, failed={counts.get('failed', 0)}, "
        f"blocked={counts.get('blocked', 0)}, not-run={counts.get('not-run', 0)}, "
        f"inconclusive={counts.get('inconclusive', 0)}."
    )
    return sanitize_text("\n".join(lines) + "\n")


def write_report(output: Path, report: Mapping[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "skill-usability-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "skill-usability-report.md").write_text(markdown(report), encoding="utf-8")
