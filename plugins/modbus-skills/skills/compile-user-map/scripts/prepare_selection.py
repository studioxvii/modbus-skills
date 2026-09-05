#!/usr/bin/env python3
"""Prepare a typed selection reply without changing the inspected case."""
import argparse
import json
import os
from pathlib import Path
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "runtime"))

from modbus_skills.selection_resume import prepare_selection_resume


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="Existing case directory or case.json")
    parser.add_argument("--case-hash", required=True, help="Exact hash from the inspection whose packet the user answered")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--include", action="append", help="One user-chosen offered subject ID; repeat for more")
    choice.add_argument("--exclude-all", action="store_true", help="Only when the user explicitly excluded all offered candidates")
    parser.add_argument("--reason", required=True, help="Actual user's choice, not an invented approval")
    parser.add_argument("--output", required=True, help="New reply file outside the saved case; never overwritten")
    args = parser.parse_args()
    try:
        root = Path(args.case)
        if root.name == "case.json":
            root = root.parent
        output = Path(args.output)
        if output.resolve().is_relative_to(root.resolve()):
            raise ValueError("prepared reply must be outside the saved case")
        reply = prepare_selection_resume(args.case, expected_case_hash=args.case_hash,
                                         include=args.include or (), exclude_all=args.exclude_all, reason=args.reason)
        serialized = json.dumps(reply, indent=2, sort_keys=True) + "\n"
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "code": "selection-reply-invalid", "message": str(exc)[:500]}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "prepared", "path": str(output), "case_id": reply["case_id"],
                      "case_changed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
