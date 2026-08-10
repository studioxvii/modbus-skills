#!/usr/bin/env python3
"""Build portable, Codex, and Claude packages from the canonical plugin source."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "modbus-skills"
PACKAGING = ROOT / "packaging"
VARIANTS = ("agent-plugin", "codex", "claude")
PACKAGE_SOURCE_NAMES = frozenset(
    {".codex-plugin", "LICENSE", "NOTICE", "references", "runtime", "scripts", "skills"}
)
GENERATED_SOURCE_PATTERNS = (
    ".DS_Store",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "*.egg-info",
    "*.py[cod]",
)


def _reject_symlinks(source: Path) -> None:
    symlinks = [entry for entry in source.rglob("*") if entry.is_symlink()]
    if symlinks:
        names = ", ".join(str(entry.relative_to(ROOT)) for entry in symlinks)
        raise ValueError(f"canonical plugin must not contain symlinks: {names}")


def _copy_canonical(source: Path, destination: Path, *, include_codex: bool) -> None:
    ignore_generated = shutil.ignore_patterns(*GENERATED_SOURCE_PATTERNS)

    def ignore(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).relative_to(source)
        ignored = ignore_generated(directory, names)
        if relative == Path("."):
            allowed = PACKAGE_SOURCE_NAMES
            if not include_codex:
                allowed = allowed - {".codex-plugin"}
            ignored.update(set(names) - allowed)
        if not include_codex and relative.parent.name == "skills" and relative.name != "skills":
            ignored.add("agents")
        return ignored.intersection(names)

    shutil.copytree(source, destination, ignore=ignore)


def _add_claude_manual_invocation(skill_path: Path) -> None:
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"missing frontmatter in {skill_path}")
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"unterminated frontmatter in {skill_path}") from exc
    lines.insert(closing, "disable-model-invocation: true\n")
    skill_path.write_text("".join(lines), encoding="utf-8")


def _build_into(staging: Path) -> None:
    portable = staging / "agent-plugin"
    _copy_canonical(PLUGIN, portable, include_codex=False)
    shutil.copy2(PACKAGING / "agent-plugin.json", portable / "plugin.json")

    codex = staging / "codex"
    _copy_canonical(PLUGIN, codex, include_codex=True)

    claude = staging / "claude"
    _copy_canonical(PLUGIN, claude, include_codex=False)
    claude_manifest = claude / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir(parents=True)
    shutil.copy2(PACKAGING / "claude-plugin.json", claude_manifest)
    for skill_path in sorted((claude / "skills").glob("*/SKILL.md")):
        _add_claude_manual_invocation(skill_path)


def build_variants(output: Path) -> None:
    """Build all variants into three known child directories of ``output``."""

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    _reject_symlinks(PLUGIN)
    with tempfile.TemporaryDirectory(prefix=".plugin-build-", dir=output) as temp_dir:
        staging = Path(temp_dir)
        _build_into(staging)
        for name in VARIANTS:
            destination = output / name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(staging / name), destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "plugins",
        help="Parent directory for agent-plugin, codex, and claude packages",
    )
    args = parser.parse_args()
    build_variants(args.output)
    print(f"Built plugin variants in {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
