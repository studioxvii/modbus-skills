#!/usr/bin/env python3
"""Validate skill and plugin metadata without third-party packages."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "modbus-skills"
SKILLS = PLUGIN / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("missing YAML frontmatter")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_openai_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if ":" not in stripped or stripped == "interface:":
            continue
        key, value = stripped.split(":", 1)
        values[key] = value.strip().strip('"')
    return values


def validate() -> list[str]:
    errors: list[str] = []
    manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
    marketplace_path = ROOT / ".agents" / "plugins" / "marketplace.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest: {exc}"]

    if manifest.get("name") != "modbus-skills":
        errors.append("plugin name must be modbus-skills")
    if manifest.get("skills") != "./skills/":
        errors.append("plugin skills path must be ./skills/")
    entries = marketplace.get("plugins", [])
    if len(entries) != 1 or entries[0].get("source", {}).get("path") != "./plugins/modbus-skills":
        errors.append("marketplace must point at ./plugins/modbus-skills")

    skill_dirs = sorted(path for path in SKILLS.iterdir() if path.is_dir())
    if not skill_dirs:
        errors.append("no skills found")
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        openai_yaml = skill_dir / "agents" / "openai.yaml"
        if not skill_md.exists():
            errors.append(f"{skill_dir.name}: missing SKILL.md")
            continue
        try:
            frontmatter = parse_frontmatter(skill_md)
        except ValueError as exc:
            errors.append(f"{skill_dir.name}: {exc}")
            continue
        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if name != skill_dir.name or not NAME_RE.fullmatch(name):
            errors.append(f"{skill_dir.name}: invalid or mismatched name")
        if len(description) < 40:
            errors.append(f"{skill_dir.name}: description is too short")
        if "TODO" in skill_md.read_text(encoding="utf-8"):
            errors.append(f"{skill_dir.name}: contains TODO placeholder")
        if not openai_yaml.exists():
            errors.append(f"{skill_dir.name}: missing agents/openai.yaml")
            continue
        metadata = parse_openai_yaml(openai_yaml)
        short = metadata.get("short_description", "")
        prompt = metadata.get("default_prompt", "")
        if not 25 <= len(short) <= 64:
            errors.append(f"{skill_dir.name}: short_description must be 25-64 characters")
        if f"${name}" not in prompt:
            errors.append(f"{skill_dir.name}: default_prompt must mention ${name}")
        if metadata.get("allow_implicit_invocation") != "false":
            errors.append(
                f"{skill_dir.name}: policy.allow_implicit_invocation must be false"
            )
        if name != "modbus-help" and not (skill_dir / "scripts" / "run.py").exists():
            errors.append(f"{skill_dir.name}: missing deterministic script wrapper")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Validated {len([p for p in SKILLS.iterdir() if p.is_dir()])} skills")
    return 0


if __name__ == "__main__":
    sys.exit(main())
