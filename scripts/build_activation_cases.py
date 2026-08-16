#!/usr/bin/env python3
"""Expand curated activation intents into stable prompt cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "catalog" / "activation-intents.json"
OUTPUT = ROOT / "catalog" / "activation-cases.json"
PREFIXES = ["", "Please ", "Can you ", "I need you to ", "Help me "]


def build() -> dict[str, object]:
    intents = json.loads(INPUT.read_text(encoding="utf-8"))
    cases: list[dict[str, object]] = []
    for skill_id in sorted(intents):
        intent = intents[skill_id]
        positive = []
        for stem in intent["positive_stems"]:
            for prefix in PREFIXES:
                if prefix and stem.casefold().startswith(prefix.strip().casefold()):
                    continue
                text = prefix + stem
                positive.append(text[0].upper() + text[1:] + ".")
        cases.append({"skill_id": skill_id, "positive": positive, "negative": intent["negative"]})
    return {"schema_version": "skill-activation/v1", "cases": cases}


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    content = json.dumps(build(), indent=2) + "\n"
    if check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            print("ERROR: catalog/activation-cases.json is stale")
            return 1
        print("Activation cases are current")
        return 0
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
