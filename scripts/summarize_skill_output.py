#!/usr/bin/env python3
"""Measure saved actual-session output without rerunning or regrading trials."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

TOKEN_FIELDS = ("totalTokens", "inputTokens", "cachedInputTokens",
                "cacheWriteInputTokens", "outputTokens", "reasoningOutputTokens")


def measure_transcript(events):
    """Count completed visible messages once; usage totals are cumulative per thread."""
    if not isinstance(events, list):
        raise ValueError("transcript must be an event list")
    messages, usage, threads = {}, {}, set()
    wait_ms = 0
    waits = set()
    for event in events:
        params = event.get("params", {})
        thread = params.get("threadId")
        if event.get("method") == "thread/started":
            thread = params.get("thread", {}).get("id")
        if isinstance(thread, str):
            threads.add(thread)
        if event.get("method") == "thread/tokenUsage/updated" and thread:
            total = params.get("tokenUsage", {}).get("total")
            if isinstance(total, dict):
                usage[thread] = total
        if event.get("method") != "item/completed":
            continue
        item = params.get("item", {})
        identity = (thread, item.get("id"))
        if not identity[1]:
            continue
        if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
            messages[identity] = item
        if item.get("type") == "sleep" or (item.get("type") == "dynamicToolCall" and item.get("tool") == "sleep"):
            args = item.get("arguments", {})
            duration = (item.get("durationMs") if item.get("type") == "sleep"
                        else args.get("duration_ms") if isinstance(args, dict) else None)
            if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0 and identity not in waits:
                waits.add(identity)
                wait_ms += duration
    visible = list(messages.values())
    finals = [item for item in visible if item.get("phase") == "final_answer"]
    progress = [item for item in visible if item.get("phase") == "commentary"]

    def counts(items):
        return {"messages": len(items), "words": sum(len(x["text"].split()) for x in items),
                "utf8_bytes": sum(len(x["text"].encode()) for x in items),
                "markdown_link_occurrences": sum(len(re.findall(r"\[[^\]\n]*\]\([^\n]*?\)", x["text"])) for x in items)}

    tokens = {}
    for field in TOKEN_FIELDS:
        values = [usage.get(thread, {}).get(field) for thread in threads]
        tokens[field] = (sum(values) if values and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values) else None)
    return {"visible_total": counts(visible), "commentary": counts(progress),
            "final_answers_all_turns": counts(finals), "last_final_answer": counts(finals[-1:]),
            "observed_threads": len(threads), "threads_with_usage": len(usage),
            "token_usage": tokens, "completed_native_sleep_calls": len(waits),
            "requested_native_sleep_ms": wait_ms}


def summarize(campaign):
    report_path = campaign / "skill-usability-report.json"
    source_bytes = report_path.read_bytes()
    report = json.loads(source_bytes)
    trials = []
    for trial in report["trials"]:
        identifier = f"{trial['scenario_id']}-{trial['repetition']}"
        if Path(identifier).name != identifier or identifier in {".", ".."}:
            raise ValueError("unsafe trial identifier")
        trial_root = campaign / "raw" / identifier
        transcript = trial_root / "transcript.json"
        if transcript.is_symlink() or trial_root.is_symlink():
            raise ValueError("symlinked transcript is not accepted")
        entry = {key: trial[key] for key in ("scenario_id", "repetition", "status", "elapsed_seconds", "final_words", "tool_calls") if key in trial}
        if transcript.is_file():
            raw = transcript.read_bytes()
            entry.update(measure_transcript(json.loads(raw)))
            entry["transcript_sha256"] = hashlib.sha256(raw).hexdigest()
        else:
            entry["measurement_status"] = "transcript-unavailable"
        output = trial_root / "output"
        if output.exists():
            paths = list(output.rglob("*"))
            if output.is_symlink() or any(p.is_symlink() for p in paths):
                raise ValueError("symlinked artifact inventory is not accepted")
            files = [p for p in paths if p.is_file()]
            entry["saved_artifacts"] = {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}
        else:
            entry["saved_artifacts"] = None
        trials.append(entry)
    return {"schema_version": "skill-output-measurement/v1",
            "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_status": report["status"], "trials": trials,
            "scope": "Read-only measurements, not a new acceptance score. All completed assistant messages are counted; link occurrences do not prove useful or valid deliverables. Token fields use the last cumulative total per observed thread; missing fields remain null. Saved artifact counts exclude unavailable or unsaved work. Native sleep duration is requested time, not measured elapsed delay. No cost or human-comprehension inference."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.campaign.resolve()), indent=2))


if __name__ == "__main__":
    main()
