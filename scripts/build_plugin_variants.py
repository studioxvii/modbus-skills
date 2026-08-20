#!/usr/bin/env python3
"""Build portable, Codex, Cursor, and Claude packages from the canonical source."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "modbus-skills"
PACKAGING = ROOT / "packaging"
VARIANTS = ("agent-plugin", "codex", "cursor", "claude")
CLAUDE_ADAPTER_TOKEN = "disable-model-invocation"
CLAUDE_ADAPTER_LINE = f"{CLAUDE_ADAPTER_TOKEN}: true\n"
PACKAGE_SOURCE_NAMES = frozenset(
    {
        ".codex-plugin",
        ".cursor-plugin",
        "LICENSE",
        "NOTICE",
        "references",
        "runtime",
        "scripts",
        "skills",
    }
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


def _copy_canonical(
    source: Path,
    destination: Path,
    *,
    include_codex: bool,
    include_cursor: bool,
) -> None:
    ignore_generated = shutil.ignore_patterns(*GENERATED_SOURCE_PATTERNS)

    def ignore(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).relative_to(source)
        ignored = ignore_generated(directory, names)
        if relative == Path("."):
            allowed = PACKAGE_SOURCE_NAMES
            if not include_codex:
                allowed = allowed - {".codex-plugin"}
            if not include_cursor:
                allowed = allowed - {".cursor-plugin"}
            ignored.update(set(names) - allowed)
        if not include_codex and relative.parent.name == "skills" and relative.name != "skills":
            ignored.add("agents")
        return ignored.intersection(names)

    shutil.copytree(source, destination, ignore=ignore)


def _add_claude_manual_invocation(skill_path: Path) -> None:
    # Byte-level insertion keeps the adapter line LF and leaves the canonical bytes
    # untouched; text mode would rewrite every newline as CRLF on Windows.
    data = skill_path.read_bytes()
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].strip() != b"---":
        raise ValueError(f"missing frontmatter in {skill_path}")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == b"---"
        )
    except StopIteration as exc:
        raise ValueError(f"unterminated frontmatter in {skill_path}") from exc
    lines.insert(closing, CLAUDE_ADAPTER_LINE.encode("utf-8"))
    skill_path.write_bytes(b"".join(lines))


def _build_into(staging: Path) -> None:
    portable = staging / "agent-plugin"
    _copy_canonical(PLUGIN, portable, include_codex=False, include_cursor=False)
    shutil.copy2(PACKAGING / "agent-plugin.json", portable / "plugin.json")

    codex = staging / "codex"
    _copy_canonical(PLUGIN, codex, include_codex=True, include_cursor=False)

    cursor = staging / "cursor"
    _copy_canonical(PLUGIN, cursor, include_codex=False, include_cursor=True)
    cursor_manifest = cursor / ".cursor-plugin" / "plugin.json"
    shutil.copy2(PACKAGING / "cursor-plugin.json", cursor_manifest)

    claude = staging / "claude"
    _copy_canonical(PLUGIN, claude, include_codex=False, include_cursor=False)
    claude_manifest = claude / ".claude-plugin" / "plugin.json"
    claude_manifest.parent.mkdir(parents=True)
    shutil.copy2(PACKAGING / "claude-plugin.json", claude_manifest)
    for skill_path in sorted((claude / "skills").glob("*/SKILL.md")):
        _add_claude_manual_invocation(skill_path)


def build_variants(output: Path) -> None:
    """Build all variants into four known child directories of ``output``."""

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
        help="Parent directory for agent-plugin, codex, cursor, and claude packages",
    )
    args = parser.parse_args()
    build_variants(args.output)
    print(f"Built plugin variants in {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
