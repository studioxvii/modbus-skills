#!/usr/bin/env python3
"""Reject files and values that are unsafe for a future public repository."""

from __future__ import annotations

import re
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", "artifacts", "dist", ".venv"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".csv", ".html", ".css", ".js"}
FORBIDDEN = {
    "absolute user path": re.compile(r"/(?:Users|home)/[^/\s]+/"),
    "secret-manager reference": re.compile("op" + "://"),
    "private product name": re.compile(
        "(?:" + "Scada" + r"\s+" + "Studio" + "|" + "Mod" + "Mapper" + ")",
        re.IGNORECASE,
    ),
    "private key": re.compile(r"BEGIN (?:RSA |OPENSSH )?PRIVATE KEY"),
    "GitHub token": re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]+"),
    "OpenAI key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
}


def iter_files() -> list[Path]:
    # Git's candidate public surface includes tracked files even when a later
    # ignore rule matches them, and untracked files that could be added normally.
    # Do not traverse ignored local corpora, environments, or nested worktrees.
    try:
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, capture_output=True, check=False,
        )
    except OSError:
        listing = None
    if listing is not None and listing.returncode == 0:
        paths = {ROOT / os.fsdecode(name) for name in listing.stdout.split(b"\0") if name}
        return sorted(path for path in paths if path.is_symlink() or path.is_file())

    # Source archives have no Git index; retain a conservative filesystem check.
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_symlink():
            result.append(path)
        elif path.is_file():
            result.append(path)
    return result


def check() -> list[str]:
    errors: list[str] = []
    for path in iter_files():
        relative = path.relative_to(ROOT)
        if path.is_symlink():
            errors.append(f"{relative}: symlinks are not allowed")
            continue
        if path.suffix.lower() == ".pdf":
            errors.append(f"{relative}: PDF files require an approved rights record and are blocked by default")
        if any(part in {"private", "vendor"} for part in relative.parts):
            errors.append(f"{relative}: private or vendor paths cannot be tracked")
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"LICENSE", "Makefile"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"{relative}: unexpected binary file")
            continue
        for label, pattern in FORBIDDEN.items():
            if pattern.search(text):
                errors.append(f"{relative}: contains {label}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Public boundary check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
