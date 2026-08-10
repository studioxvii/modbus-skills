#!/usr/bin/env python3
"""Validate generated plugin variants for parity and host isolation."""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from fnmatch import fnmatch
from pathlib import Path

try:
    from scripts.build_plugin_variants import (
        GENERATED_SOURCE_PATTERNS,
        PACKAGE_SOURCE_NAMES,
        PACKAGING,
        PLUGIN,
        ROOT,
        VARIANTS,
        build_variants,
    )
except ModuleNotFoundError:  # Direct execution sets scripts/ as sys.path[0].
    from build_plugin_variants import (
        GENERATED_SOURCE_PATTERNS,
        PACKAGE_SOURCE_NAMES,
        PACKAGING,
        PLUGIN,
        ROOT,
        VARIANTS,
        build_variants,
    )


EXPECTED_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
EXPECTED_LICENSE = "Apache-2.0"
AGENT_MANIFEST_KEYS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
AUTHOR_KEYS = {"name", "email", "url"}
PLUGIN_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
HOST_TOKEN_RE = re.compile(
    r"\$[a-z][a-z0-9-]*|\b(?:Codex|Claude|OpenAI|Anthropic|Cursor)\b|bundled workspace",
    re.IGNORECASE,
)


def _load_json(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path}: expected a JSON object")
        return {}
    return value


def _relative_files(root: Path) -> set[Path]:
    return {entry.relative_to(root) for entry in root.rglob("*") if entry.is_file()}


def _canonical_files() -> set[Path]:
    return {
        relative
        for relative in _relative_files(PLUGIN)
        if relative.parts[0] in PACKAGE_SOURCE_NAMES
        and not any(
            fnmatch(part, pattern)
            for part in relative.parts
            for pattern in GENERATED_SOURCE_PATTERNS
        )
    }


def _without_claude_adapter(data: bytes, relative: Path, errors: list[str]) -> bytes:
    lines = data.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        errors.append(f"claude skill missing opening frontmatter: {relative}")
        return data
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == b"---"
        )
    except StopIteration:
        errors.append(f"claude skill has unterminated frontmatter: {relative}")
        return data

    token = b"disable-model-invocation"
    exact = b"disable-model-invocation: true\n"
    token_indexes = [index for index, line in enumerate(lines) if token in line]
    exact_indexes = [index for index, line in enumerate(lines) if line == exact]
    adapter_indexes = [index for index in exact_indexes if 0 < index < closing]

    if len(exact_indexes) != 1 or len(adapter_indexes) != 1:
        errors.append(f"claude manual-invocation metadata invalid: {relative}")
    if any(not 0 < index < closing for index in token_indexes):
        errors.append(f"claude manual-invocation metadata outside frontmatter: {relative}")
    if any(index not in exact_indexes for index in token_indexes):
        errors.append(f"claude manual-invocation metadata has a wrong value or format: {relative}")

    if len(adapter_indexes) != 1:
        return data
    adapter_index = adapter_indexes[0]
    return b"".join(line for index, line in enumerate(lines) if index != adapter_index)


def _validate_agent_manifest(manifest: dict[str, object], errors: list[str]) -> None:
    unexpected = sorted(set(manifest) - AGENT_MANIFEST_KEYS)
    if unexpected:
        errors.append(f"agent-plugin manifest has unsupported fields: {unexpected}")

    missing = sorted({"$schema", "name"} - set(manifest))
    if missing:
        errors.append(f"agent-plugin manifest missing required fields: {missing}")

    schema = manifest.get("$schema")
    if "$schema" in manifest and schema != EXPECTED_SCHEMA:
        errors.append(f"agent-plugin schema must be {EXPECTED_SCHEMA}")

    name = manifest.get("name")
    if "name" in manifest:
        if not isinstance(name, str):
            errors.append("agent-plugin manifest name must be a string")
        elif not 1 <= len(name) <= 64 or PLUGIN_NAME_RE.fullmatch(name) is None:
            errors.append("agent-plugin manifest name does not satisfy the Agent Plugins 1.0 format")

    for field in ("version", "description", "homepage", "repository", "license"):
        if field in manifest and not isinstance(manifest[field], str):
            errors.append(f"agent-plugin manifest {field} must be a string")

    if "author" in manifest:
        author = manifest["author"]
        if not isinstance(author, dict):
            errors.append("agent-plugin manifest author must be an object")
        else:
            unexpected_author = sorted(set(author) - AUTHOR_KEYS)
            if unexpected_author:
                errors.append(
                    f"agent-plugin manifest author has unsupported fields: {unexpected_author}"
                )
            for field, value in author.items():
                if field in AUTHOR_KEYS and not isinstance(value, str):
                    errors.append(f"agent-plugin manifest author.{field} must be a string")

    if "keywords" in manifest:
        keywords = manifest["keywords"]
        if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
            errors.append("agent-plugin manifest keywords must be an array of strings")

    if "extensions" in manifest:
        extensions = manifest["extensions"]
        if not isinstance(extensions, dict):
            errors.append("agent-plugin manifest extensions must be an object")
        else:
            for namespace, value in extensions.items():
                if not isinstance(value, dict):
                    errors.append(
                        f"agent-plugin manifest extension {namespace!r} must be an object"
                    )


def _check_manifest_template(actual: Path, template: Path, errors: list[str]) -> None:
    try:
        actual_data = actual.read_bytes()
        template_data = template.read_bytes()
    except OSError as exc:
        errors.append(f"manifest template comparison failed: {exc}")
        return
    if actual_data != template_data:
        errors.append(f"generated manifest differs from packaging template: {actual}")


def _check_manifest_parity(output: Path, errors: list[str]) -> None:
    manifests = {
        "agent-plugin": _load_json(output / "agent-plugin" / "plugin.json", errors),
        "codex": _load_json(output / "codex" / ".codex-plugin" / "plugin.json", errors),
        "claude": _load_json(output / "claude" / ".claude-plugin" / "plugin.json", errors),
    }
    _validate_agent_manifest(manifests["agent-plugin"], errors)
    _check_manifest_template(
        output / "agent-plugin" / "plugin.json", PACKAGING / "agent-plugin.json", errors
    )
    _check_manifest_template(
        output / "claude" / ".claude-plugin" / "plugin.json",
        PACKAGING / "claude-plugin.json",
        errors,
    )
    for field in ("name", "version", "description", "repository", "license"):
        values = [manifest.get(field) for manifest in manifests.values()]
        if not all(isinstance(value, str) and value for value in values):
            errors.append(f"manifest {field} values must be non-empty strings: {values}")
        elif len(set(values)) != 1:
            errors.append(f"manifest {field} values differ: {values}")
    author_names = [
        author.get("name") if isinstance(author := manifest.get("author"), dict) else None
        for manifest in manifests.values()
    ]
    if not all(isinstance(value, str) and value for value in author_names):
        errors.append(f"manifest author.name values must be non-empty strings: {author_names}")
    elif len(set(author_names)) != 1:
        errors.append(f"manifest author.name values differ: {author_names}")

    keywords = [manifest.get("keywords") for manifest in manifests.values()]
    if not all(
        isinstance(value, list) and all(isinstance(item, str) for item in value)
        for value in keywords
    ):
        errors.append(f"manifest keywords values must be arrays of strings: {keywords}")
    elif any(value != keywords[0] for value in keywords[1:]):
        errors.append(f"manifest keywords values differ: {keywords}")

    author_urls = []
    for name, manifest in manifests.items():
        author = manifest.get("author")
        url = author.get("url") if isinstance(author, dict) else None
        if name != "claude" or url is not None:
            author_urls.append((name, url))
    if not all(isinstance(value, str) and value for _, value in author_urls):
        errors.append(f"manifest author.url values must be non-empty strings: {author_urls}")
    elif len({value for _, value in author_urls}) != 1:
        errors.append(f"manifest author.url values differ: {author_urls}")

    if manifests["agent-plugin"].get("license") != EXPECTED_LICENSE:
        errors.append(f"manifest license must be {EXPECTED_LICENSE}")


def _check_file_parity(output: Path, errors: list[str]) -> None:
    canonical_files = _canonical_files()
    portable_files = {
        relative
        for relative in canonical_files
        if relative.parts[0] != ".codex-plugin" and "agents" not in relative.parts
    }
    expected = {
        "codex": canonical_files,
        "agent-plugin": portable_files | {Path("plugin.json")},
        "claude": portable_files | {Path(".claude-plugin/plugin.json")},
    }
    for name, expected_files in expected.items():
        package = output / name
        actual_files = _relative_files(package) if package.exists() else set()
        if actual_files != expected_files:
            missing = sorted(str(item) for item in expected_files - actual_files)
            extra = sorted(str(item) for item in actual_files - expected_files)
            errors.append(f"{name} file set differs; missing={missing}, extra={extra}")

    for relative in sorted(canonical_files):
        canonical_data = (PLUGIN / relative).read_bytes()
        codex_path = output / "codex" / relative
        if codex_path.is_file() and codex_path.read_bytes() != canonical_data:
            errors.append(f"codex content differs: {relative}")
        if relative not in portable_files:
            continue
        portable_path = output / "agent-plugin" / relative
        if portable_path.is_file() and portable_path.read_bytes() != canonical_data:
            errors.append(f"agent-plugin content differs: {relative}")
        claude_path = output / "claude" / relative
        if not claude_path.is_file():
            continue
        claude_data = claude_path.read_bytes()
        if relative.name == "SKILL.md":
            without_adapter = _without_claude_adapter(claude_data, relative, errors)
            if without_adapter != canonical_data:
                errors.append(f"claude skill content differs beyond its adapter: {relative}")
        elif claude_data != canonical_data:
            errors.append(f"claude content differs: {relative}")


def _check_host_neutral_source(errors: list[str]) -> None:
    markdown = list((PLUGIN / "skills").rglob("*.md")) + list((PLUGIN / "references").rglob("*.md"))
    for source in sorted(markdown):
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            match = HOST_TOKEN_RE.search(line)
            if match:
                relative = source.relative_to(ROOT)
                errors.append(f"host-specific token {match.group(0)!r} in {relative}:{line_number}")


def _check_package_safety(output: Path, errors: list[str]) -> None:
    for name in VARIANTS:
        package = output / name
        if not package.is_dir():
            errors.append(f"missing package directory: {name}")
            continue
        for entry in package.rglob("*"):
            if entry.is_symlink():
                errors.append(f"{name} contains symlink: {entry.relative_to(package)}")
        for filename in ("LICENSE", "NOTICE"):
            packaged = package / filename
            canonical = PLUGIN / filename
            if not packaged.is_file() or not canonical.is_file():
                errors.append(f"{name} missing {filename}")
            elif packaged.read_bytes() != canonical.read_bytes():
                errors.append(f"{name} {filename} differs from canonical source")

    if (output / "agent-plugin" / ".codex-plugin").exists():
        errors.append("agent-plugin package contains Codex metadata")
    if list((output / "agent-plugin" / "skills").glob("*/agents")):
        errors.append("agent-plugin package contains host-specific skill agents")
    if (output / "claude" / ".codex-plugin").exists():
        errors.append("claude package contains Codex metadata")
    if list((output / "claude" / "skills").glob("*/agents")):
        errors.append("claude package contains Codex skill agents")


def validate_variants(output: Path) -> list[str]:
    errors: list[str] = []
    _check_manifest_parity(output, errors)
    _check_file_parity(output, errors)
    _check_host_neutral_source(errors)
    _check_package_safety(output, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Validate an existing build instead of a temporary build")
    args = parser.parse_args()
    if args.output:
        errors = validate_variants(args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="modbus-plugin-variants-") as temp_dir:
            output = Path(temp_dir)
            build_variants(output)
            errors = validate_variants(output)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Plugin variant validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
