#!/usr/bin/env python3
"""Build a machine-readable catalog from installed skill metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "modbus-skills" / "skills"
OUTPUT = ROOT / "catalog" / "skills.json"


def field(text: str, key: str) -> str:
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.*?)\s*$", text, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def build() -> list[dict[str, object]]:
    catalog: list[dict[str, object]] = []
    for skill_dir in sorted(path for path in SKILLS.iterdir() if path.is_dir()):
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        metadata_text = (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        catalog.append(
            {
                "id": field(skill_text, "name"),
                "display_name": field(metadata_text, "display_name"),
                "description": field(skill_text, "description"),
                "license": field(skill_text, "license"),
                "short_description": field(metadata_text, "short_description"),
                "default_prompt": field(metadata_text, "default_prompt"),
                "path": f"plugins/modbus-skills/skills/{skill_dir.name}",
            }
        )
    return catalog


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    content = json.dumps({"schema_version": "skill-catalog/v1", "skills": build()}, indent=2) + "\n"
    if check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != content:
            print("ERROR: catalog/skills.json is stale")
            return 1
        print("Skill catalog is current")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
