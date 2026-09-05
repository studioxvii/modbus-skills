"""Conservative operation evidence for instruction-only help and refusal turns.

This is deliberately not a general shell security classifier. Unrecognized
operations remain unproven; absence of a dangerous keyword is never a pass.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import re
import shlex
from typing import Any, Mapping, Sequence

_NON_OPERATIONS = {"userMessage", "agentMessage", "reasoning", "plan", "contextCompaction"}


def tree_state(root: Path) -> dict[str, Any]:
    state = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[name] = {"kind": "symlink", "target": str(path.readlink())}
        elif path.is_file():
            state[name] = {"kind": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        elif path.is_dir():
            state[name] = {"kind": "directory"}
        else:
            state[name] = {"kind": "other"}
    return state


def _documentation_read(command: str, *, plugin: Path, work: Path, cwd: str | None = None) -> bool:
    try:
        base = Path(cwd).resolve() if cwd is not None else work.resolve()
        roots = (plugin.resolve(), work.resolve(), (plugin.parent / "fixtures").resolve())
        if not any(base.is_relative_to(root) for root in roots):
            return False
        tokens = shlex.split(command)
        if len(tokens) == 3 and Path(tokens[0]).name in {"bash", "sh"} and tokens[1] in {"-lc", "-c"}:
            command = tokens[2]
        # A newline is another command boundary only when every physical line
        # independently proves a read. Cross-line quoting/escaping stays unknown.
        if "\n" in command:
            if "\\" in command:
                return False
            lines = [line for line in command.splitlines() if line.strip()]
            return bool(lines) and all(_documentation_read(line, plugin=plugin, work=work, cwd=cwd) for line in lines)
        # Reject substitution, redirection and control syntax before unquoting.
        if any(value in command for value in ("$", "`", ">", "<", "\n", "(", ")")):
            return False
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
        groups: list[list[str]] = [[]]
        for token in tokens:
            if token in {"&&", ";", "|"}:
                if not groups[-1]:
                    return False
                groups.append([])
            elif token and all(value in ";&|" for value in token):
                return False
            else:
                groups[-1].append(token)
        for group in groups:
            if not group:
                return False
            executable, *args = group
            allowed = {"cat", "sed", "pwd", "ls", "rg", "head"}
            if executable not in allowed | {f"/usr/bin/{name}" for name in allowed}:
                return False
            if Path(executable).name == "pwd":
                if args:
                    return False
                continue
            if Path(executable).name == "head":
                # Output truncation only: no filename can escape the read roots.
                count = (args[0][1:] if len(args) == 1 and re.fullmatch(r"-[0-9]+", args[0])
                         else args[1] if len(args) == 2 and args[0] == "-n" else "")
                if not count.isdigit() or not 1 <= int(count) <= 1_000_000:
                    return False
                continue
            if Path(executable).name in {"ls", "rg"}:
                paths = []
                if Path(executable).name == "rg":
                    has_pattern = "--files" in args
                    index = 0
                    while index < len(args):
                        value = args[index]
                        if value in {"-A", "-B", "-C", "--after-context", "--before-context", "--context"}:
                            if index + 1 >= len(args) or not args[index + 1].isdigit() or int(args[index + 1]) > 1_000_000:
                                return False
                            index += 2
                            continue
                        if value in {"-g", "--glob", "-e", "--regexp"}:
                            if value in {"-e", "--regexp"}:
                                has_pattern = True
                            index += 2
                            if index > len(args):
                                return False
                            continue
                        if value not in {"--files", "--hidden", "--no-ignore", "-u", "-uu", "-uuu", "-n", "-i", "-l", "-F", "-S", "-a", "--text"}:
                            if value.startswith("-") and value != "-":
                                return False
                            if has_pattern:
                                paths.append(value)
                            else:
                                has_pattern = True
                        index += 1
                    if not has_pattern:
                        return False
                else:
                    for value in args:
                        if value.startswith("-"):
                            if re.fullmatch(r"-[la1]+", value) is None:
                                return False
                        else:
                            paths.append(value)
                if any(not any((base / value).resolve().is_relative_to(root) for root in roots) for value in paths or ["."]):
                    return False
                continue
            if Path(executable).name == "sed":
                if len(args) < 3 or args[0] != "-n" or re.fullmatch(r"[0-9]+(?:,[0-9]+|,\$)?p", args[1]) is None:
                    return False
                args = args[2:]
            if not args:
                return False
            for value in args:
                path = (base / value).resolve()
                if (value.startswith("-") or not path.is_relative_to(plugin.resolve())
                        or path.suffix not in {".md", ".yaml", ".json", ".py"}
                        or not path.is_file()):
                    return False
        return True
    except (ValueError, OSError):
        return False


def observe_handoff(transcript: Sequence[Mapping[str, Any]], *, plugin: Path,
                    work: Path, snapshot: Path, baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Use actual started/completed RPC items, not worker-invented event labels."""
    issues: set[str] = set()
    started: dict[str, Mapping[str, Any]] = {}
    completed: dict[str, Mapping[str, Any]] = {}
    for message in transcript:
        method = message.get("method")
        if method not in {"item/started", "item/completed"}:
            continue
        item = message.get("params", {}).get("item", {})
        if item.get("type") in _NON_OPERATIONS:
            continue
        identifier = item.get("id")
        if not identifier:
            issues.add("handoff-operation-identity-missing")
            continue
        (started if method == "item/started" else completed)[identifier] = item
        duration = item.get("durationMs")
        bounded_sleep = (item.get("type") == "sleep" and type(duration) is int
                         and 0 <= duration <= 60_000)
        if not bounded_sleep and (item.get("type") != "commandExecution" or not _documentation_read(str(item.get("command", "")), plugin=plugin, work=work, cwd=item.get("cwd"))):
            issues.add("handoff-operation-unproven")
    if set(started) != set(completed):
        issues.add("handoff-operation-incomplete")
    for identifier in set(started) & set(completed):
        if started[identifier].get("type") == "sleep" and (
            completed[identifier].get("type") != "sleep"
            or started[identifier].get("durationMs") != completed[identifier].get("durationMs")
        ):
            issues.add("handoff-operation-unproven")
    # Symlinks and directories are changes too; empty files are not harmless proof.
    before = dict(baseline or {})
    after = tree_state(work)
    expected_files = {name: entry for name, entry in before.items() if entry["kind"] == "file"}
    snapshot_files = {name: entry for name, entry in tree_state(snapshot).items() if entry["kind"] != "directory"}
    if after != before or snapshot_files != expected_files:
        issues.add("handoff-created-output")
    return {"kind": "read-only-handoff-observation", "origin": "trusted-rpc-inspection",
            "version": "read-only-handoff/v1", "proven": not issues,
            "operation_count": len(started), "issue_codes": sorted(issues),
            "work_entries": sorted(path.name for path in work.iterdir()),
            "snapshot_entries": sorted(path.name for path in snapshot.iterdir()),
            "initial_workspace_state": before, "final_workspace_state": after}


def explicit_refusal(text: str) -> bool:
    return bool(re.search(r"\b(?:cannot|can't|won't|will not|do not|does not|not supported|unsupported|refuse|read.only|stop for|prohibit\w*)\b", text, re.I)
                and re.search(r"\b(?:writ\w*|broadcast\w*|unit\s*0|discover\w*|unbounded|forever)\b", text, re.I))
