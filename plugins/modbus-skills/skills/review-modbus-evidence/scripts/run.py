#!/usr/bin/env python3
from pathlib import Path
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from modbus_skills.cli import run_cli


if __name__ == "__main__":
    raise SystemExit(run_cli("review-evidence", sys.argv[1:]))
