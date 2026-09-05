#!/usr/bin/env python3
"""Read-only integrity check and bounded resume context for a compiler case."""
import argparse
import json
from pathlib import Path
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from modbus_skills.compiler import CompilerError, inspect_compile_case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="Existing case directory or case.json")
    args = parser.parse_args()
    try:
        result = inspect_compile_case(args.case)
    except (CompilerError, OSError, ValueError) as exc:
        print(json.dumps({
            "schema_version": "modbus-compile-inspection/v1", "status": "error",
            "code": "case-integrity-invalid", "message": str(exc)[:500],
        }), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
