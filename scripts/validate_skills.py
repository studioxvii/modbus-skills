#!/usr/bin/env python3
"""Validate skill and plugin metadata without third-party packages."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "modbus-skills"
SKILLS = PLUGIN / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXPECTED_LICENSE = "Apache-2.0"
APACHE_LICENSE_MARKERS = (
    "Apache License\n                           Version 2.0, January 2004",
    "http://www.apache.org/licenses/",
    "END OF TERMS AND CONDITIONS",
)
EXPECTED_COPYRIGHT = "Copyright 2026 Studio Seventeen"


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


def missing_skill_references(skill_dir: Path, skill_text: str) -> list[str]:
    """Check concrete routed references, not example output paths or templates."""
    paths = re.findall(r"(?:`|\]\()((?:\.\./)*references/[A-Za-z0-9_./-]+\.md)(?:`|\))", skill_text)
    return sorted({path for path in paths if not (skill_dir / path).is_file()})


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    plugin = root / "plugins" / "modbus-skills"
    skills = plugin / "skills"
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    marketplace_path = root / ".agents" / "plugins" / "marketplace.json"
    pyproject_path = root / "pyproject.toml"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"manifest: {exc}"]

    if manifest.get("name") != "modbus-skills":
        errors.append("plugin name must be modbus-skills")
    if manifest.get("skills") != "./skills/":
        errors.append("plugin skills path must be ./skills/")
    if manifest.get("license") != EXPECTED_LICENSE:
        errors.append(f"plugin license must be {EXPECTED_LICENSE}")
    if pyproject.get("project", {}).get("license") != EXPECTED_LICENSE:
        errors.append(f"Python project license must be {EXPECTED_LICENSE}")
    entries = marketplace.get("plugins", [])
    if len(entries) != 1 or entries[0].get("source", {}).get("path") != "./plugins/modbus-skills":
        errors.append("marketplace must point at ./plugins/modbus-skills")

    root_license = root / "LICENSE"
    plugin_license = plugin / "LICENSE"
    root_notice = root / "NOTICE"
    plugin_notice = plugin / "NOTICE"
    for path in (root_license, plugin_license, root_notice, plugin_notice):
        if not path.exists():
            errors.append(f"missing {path.relative_to(root)}")
    if root_license.exists() and plugin_license.exists():
        if root_license.read_bytes() != plugin_license.read_bytes():
            errors.append("plugin LICENSE must match the repository LICENSE")
        license_text = root_license.read_text(encoding="utf-8")
        if not all(marker in license_text for marker in APACHE_LICENSE_MARKERS):
            errors.append("LICENSE must contain the Apache License 2.0 text")
    if root_notice.exists() and plugin_notice.exists():
        if root_notice.read_bytes() != plugin_notice.read_bytes():
            errors.append("plugin NOTICE must match the repository NOTICE")
        if EXPECTED_COPYRIGHT not in root_notice.read_text(encoding="utf-8"):
            errors.append(f"NOTICE must contain {EXPECTED_COPYRIGHT}")

    skill_dirs = sorted(path for path in skills.iterdir() if path.is_dir())
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
        if len(description) > 1024:
            errors.append(f"{skill_dir.name}: description exceeds 1024 characters")
        if "Use when" not in description:
            errors.append(
                f"{skill_dir.name}: description must include a 'Use when' activation clause"
            )
        if frontmatter.get("license") != EXPECTED_LICENSE:
            errors.append(f"{skill_dir.name}: license must be {EXPECTED_LICENSE}")
        skill_text = skill_md.read_text(encoding="utf-8")
        for reference in missing_skill_references(skill_dir, skill_text):
            errors.append(f"{skill_dir.name}: missing routed reference {reference}")
        if "TODO" in skill_text:
            errors.append(f"{skill_dir.name}: contains TODO placeholder")
        if "Completion requires" not in skill_text:
            errors.append(f"{skill_dir.name}: must name one observable completion criterion")
        if "../../references/interaction-contract.md" not in skill_text:
            errors.append(
                f"{skill_dir.name}: must reference the shared fast interaction contract"
            )
        if "\n## Output files\n" not in skill_text:
            errors.append(f"{skill_dir.name}: must explain output files")
        if "\n## Stop\n" not in skill_text:
            errors.append(f"{skill_dir.name}: must include a Stop section")
        if skill_dir.name not in {"modbus-help", "compile-user-map"}:
            if "\n## Handoff\n" not in skill_text:
                errors.append(f"{skill_dir.name}: must include a Handoff section")
        elif "\n## Finish\n" not in skill_text:
            errors.append(f"{skill_dir.name}: must include a Finish section")
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

    outcome = skills / "compile-user-map"
    if outcome.exists():
        outcome_text = (outcome / "SKILL.md").read_text(encoding="utf-8")
        wrapper_text = (outcome / "scripts" / "run.py").read_text(encoding="utf-8")
        if "references/request.md" not in outcome_text:
            errors.append("compile-user-map: must progressively disclose the request contract")
        if 'run_cli("compile-user-map"' not in wrapper_text:
            errors.append("compile-user-map: wrapper must call the outcome command directly")
        forbidden_routes = {
            "$extract-pdf-map", "$parse-map", "$normalize-map", "$review-evidence",
            "$apply-review", "$plan-reads", "$build-tool-pack",
        }
        leaked = sorted(forbidden_routes & set(re.findall(r"\$[a-z0-9-]+", outcome_text)))
        if leaked:
            errors.append(
                "compile-user-map: clean path must not expose specialist stage handoffs: "
                + ", ".join(leaked)
            )
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
