"""Bind an actual specialist wrapper invocation to independently checked output.

This proves scoped synthetic artifact behavior, not implicit activation, native
execution or full source truth. The worker cannot supply its own proof event.
"""
from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

from run_direct_skill_acceptance import assess_artifacts, load_outputs, predeclared_cases
from .sessions import _python_command, hash_tree
from .handoff_evidence import _documentation_read


def wrapper_tokens(item, session):
    command = _python_command(item)
    if command:
        return command
    tokens = shlex.split(str(item.get("command", "")))
    if len(tokens) != 3 or Path(tokens[0]).name not in {"bash", "sh"} or tokens[1] not in {"-lc", "-c"}:
        return []
    script = tokens[2]
    if any(marker in script for marker in ("$", "`", "\n", "<", ">", "(", ")")):
        return []
    lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    groups = [[]]
    separators = []
    for token in lexer:
        if token in {";", "&&"}:
            groups.append([])
            separators.append(token)
        elif token and all(value in ";&|" for value in token): return []
        else: groups[-1].append(token)
    if len(groups) < 2 or any(not group for group in groups): return []
    # In an AND-list, overall exit zero proves that every group ran and
    # succeeded. A nonzero suffix cannot reveal the wrapper's own exit status.
    if "&&" in separators and item.get("exitCode") != 0: return []
    candidates = [(index, _python_command({"command": shlex.join(group)}))
                  for index, group in enumerate(groups)]
    candidates = [(index, command) for index, command in candidates if command]
    if len(candidates) != 1: return []
    index, command = candidates[0]
    if not all(_documentation_read(shlex.join(group), plugin=session.plugin_root,
                                  work=session.work, cwd=item.get("cwd")) for group in groups[:index]):
        return []
    if groups[index + 1:]:
        if any(separator != "&&" for separator in separators[index:]): return []
        # Post-wrapper artifact inspection is scoped separately from instruction
        # reads; it cannot execute code or change generated output.
        for group in groups[index + 1:]:
            if not _documentation_read(shlex.join(group), plugin=session.work,
                                       work=session.work, cwd=item.get("cwd")):
                return []
    return command


def observe_execution(session, snapshot):
    profile = session.scenario["oracle_profile"]
    case_id = profile["direct_skill_case"]
    spec = next(item for item in predeclared_cases() if item["case_id"] == case_id)
    wrapper = session.plugin_root / spec["entrypoint"]
    invocations = []
    started = {message.get("params", {}).get("item", {}).get("id"): message.get("params", {}).get("item", {})
               for message in session.state.get("transcript", []) if message.get("method") == "item/started"}
    for message in session.state.get("transcript", []):
        if message.get("method") != "item/completed":
            continue
        item = message.get("params", {}).get("item", {})
        if item.get("type") != "commandExecution":
            continue
        opening = started.get(item.get("id"), {})
        if opening.get("type") != "commandExecution" or opening.get("command") != item.get("command"):
            continue
        try:
            command = wrapper_tokens(item, session)
            base = Path(item.get("cwd") or session.work)
            if len(command) < 2 or (base / command[1]).resolve() != wrapper.resolve():
                continue
            # A composite shell expression is not a proved wrapper execution.
            if any(token in {";", "&&", "||", "|", ">", ">>"} for token in command):
                continue
        except (ValueError, OSError):
            continue
        invocations.append(item)
    checks = [{"name": "actual trusted wrapper completed", "passed": bool(invocations)}]
    checks.append({"name": "plugin unchanged", "passed": hash_tree(session.plugin_root) == session.loaded_plugin_hash})
    expected = profile["fixture_hashes"]
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in session.fixtures.iterdir() if path.is_file()}
    checks.append({"name": "all fixture bytes unchanged", "passed": actual == expected})
    if invocations:
        invocation = invocations[-1]
        receipt = {"returncode": invocation.get("exitCode"), "stdout": invocation.get("aggregatedOutput", "")}
        checks.extend(assess_artifacts(spec, receipt, load_outputs(snapshot), snapshot))
    return {"kind": "specialist-execution-observation", "origin": "trusted-rpc-and-artifact-inspection",
            "version": "specialist-execution/v1", "case_id": case_id,
            "proven": bool(checks) and all(check["passed"] for check in checks), "checks": checks,
            "wrapper_item_ids": [item.get("id") for item in invocations]}
