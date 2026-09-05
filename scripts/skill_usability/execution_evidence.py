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
    if any(marker in script for marker in ("$", "`", "\n", "<", ">", "(", ")", "&", "|")):
        return []
    lexer = shlex.shlex(script, posix=True, punctuation_chars=";")
    lexer.whitespace_split = True
    groups = [[]]
    for token in lexer:
        if token == ";": groups.append([])
        else: groups[-1].append(token)
    if len(groups) < 2 or any(not group for group in groups): return []
    if not all(_documentation_read(shlex.join(group), plugin=session.plugin_root,
                                  work=session.work, cwd=item.get("cwd")) for group in groups[:-1]):
        return []
    return _python_command({"command": shlex.join(groups[-1])})


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
