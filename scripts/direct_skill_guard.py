#!/usr/bin/env python3
"""Run one offline skill wrapper with a Python operation audit guard.

This is not an OS sandbox. Only installed pdftotext and the exact bundled PDF
grid worker are allowed; child internals are outside this Python audit stream.
Generated tools are never run.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
from pathlib import Path
import runpy
import shutil
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--temporary-root", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    allowed_roots = [Path(args.output_root).resolve(), Path(args.temporary_root).resolve()]
    audit_path = Path(args.audit).resolve()
    events = []
    denied = []
    pdftotext = shutil.which("pdftotext")
    grid_worker = Path(args.wrapper).resolve().parents[3] / "runtime/modbus_skills/pdf_table_extraction.py"

    def allowed_process(executable, argv):
        resolved = shutil.which(os.fsdecode(executable))
        if not resolved:
            return False
        if pdftotext and Path(resolved).resolve() == Path(pdftotext).resolve():
            return True
        return (Path(resolved).resolve() == Path(sys.executable).resolve()
                and isinstance(argv, (list, tuple)) and len(argv) in {4, 5}
                and Path(os.fsdecode(argv[1])).resolve() == grid_worker
                and argv[2] == "--worker")

    def writable(value):
        if isinstance(value, int):
            return True
        try:
            path = Path(os.fsdecode(value)).resolve()
        except (TypeError, ValueError):
            return False
        return path == audit_path or any(path.is_relative_to(root) for root in allowed_roots)

    def guard(event, arguments):
        reason = None
        if event.startswith("socket.") or event in {"os.system", "os.exec", "os.spawn"}:
            reason = "network-or-unapproved-execution"
        elif event in {"subprocess.Popen", "os.posix_spawn"}:
            executable = arguments[0]
            if not allowed_process(executable, arguments[1]):
                reason = "unapproved-subprocess"
            events.append({"event": event, "executable": Path(os.fsdecode(executable)).name, "allowed": reason is None})
        elif event == "open":
            path, mode, flags = arguments
            writes = bool(set(str(mode or "")) & set("wax+")) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writes and not writable(path):
                reason = "write-outside-output-or-private-temporary-root"
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
            if not writable(arguments[0]):
                reason = "mutation-outside-output-or-private-temporary-root"
        elif event in {"os.rename", "os.link", "os.symlink"}:
            if not writable(arguments[0]) or not writable(arguments[1]):
                reason = "mutation-outside-output-or-private-temporary-root"
        if reason:
            denied.append({"event": event, "reason": reason})
            raise PermissionError("direct-skill audit guard: " + reason)

    def save():
        audit_path.write_text(json.dumps({"guard": "python-audit-v1", "denied": denied, "events": events,
            "native_subprocess_limit": "Only installed pdftotext and the exact frozen bundled grid worker allowed; child internals are not Python-audited."}, indent=2) + "\n")

    atexit.register(save)
    sys.addaudithook(guard)
    sys.argv = [args.wrapper, *(args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments)]
    runpy.run_path(args.wrapper, run_name="__main__")


if __name__ == "__main__":
    main()
