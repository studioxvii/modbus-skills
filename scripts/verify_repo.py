#!/usr/bin/env python3
"""Run the complete dependency-free repository verification."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> int:
    print(f"+ {' '.join(args)}", flush=True)
    env = os.environ.copy()
    runtime = str(ROOT / "plugins" / "modbus-skills" / "runtime")
    env["PYTHONPATH"] = runtime + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(args, cwd=ROOT, env=env, check=False).returncode


def main() -> int:
    commands = [
        (sys.executable, "scripts/validate_skills.py"),
        (sys.executable, "scripts/check_public_boundary.py"),
        (sys.executable, "scripts/build_catalog.py", "--check"),
        (sys.executable, "scripts/build_activation_cases.py", "--check"),
        (sys.executable, "scripts/build_site.py", "--check"),
        (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
    ]
    for command in commands:
        if run(*command):
            return 1
    print("Repository verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
