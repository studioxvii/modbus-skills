#!/usr/bin/env python3
"""Run any deterministic command bundled with the Modbus skills plugin."""

from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from modbus_skills.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
