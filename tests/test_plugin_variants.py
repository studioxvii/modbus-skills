from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from scripts.build_plugin_variants import (
    CLAUDE_ADAPTER_LINE,
    ROOT,
    _add_claude_manual_invocation,
    build_variants,
)
from scripts.validate_plugin_variants import _without_claude_adapter, validate_variants


PLUGIN = ROOT / "plugins" / "modbus-skills"
SKILL_RELATIVE = Path("skills") / "check-map" / "SKILL.md"
ADAPTER_LF = CLAUDE_ADAPTER_LINE.encode("utf-8")
ADAPTER_CRLF = CLAUDE_ADAPTER_LINE.replace("\n", "\r\n").encode("utf-8")


def windows_write_text(
    path: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    """Emulate the Windows text-mode default that rewrites "\\n" as "\\r\\n"."""

    if newline is None:
        data = data.replace("\n", "\r\n")
    return Path.write_bytes(path, data.encode(encoding or "utf-8", errors or "strict"))


@contextmanager
def temporary_file(path: Path, data: bytes) -> Iterator[None]:
    original = path.read_bytes() if path.exists() else None
    created_directories: list[Path] = []
    parent = path.parent
    while not parent.exists():
        created_directories.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(data)
        yield
    finally:
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
        for directory in created_directories:
            try:
                directory.rmdir()
            except OSError:
                break


class PluginVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp_dir.name)
        build_variants(cls.output)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def assert_validation_error(self, expected: str, output: Path | None = None) -> None:
        errors = validate_variants(output or self.output)
        self.assertTrue(any(expected in error for error in errors), errors)

    def assert_no_validation_error(self, unexpected: str, output: Path | None = None) -> None:
        errors = validate_variants(output or self.output)
        self.assertEqual([], [error for error in errors if unexpected in error])

    def strip_adapter(self, data: bytes) -> tuple[bytes, list[str]]:
        errors: list[str] = []
        return _without_claude_adapter(data, SKILL_RELATIVE, errors), errors

    def test_builds_valid_host_specific_variants(self) -> None:
        self.assertEqual([], validate_variants(self.output))
        self.assertTrue((self.output / "agent-plugin" / "plugin.json").is_file())
        self.assertTrue((self.output / "codex" / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((self.output / "cursor" / ".cursor-plugin" / "plugin.json").is_file())
        self.assertTrue((self.output / "claude" / ".claude-plugin" / "plugin.json").is_file())

    def test_portable_variant_preserves_canonical_skill_content(self) -> None:
        canonical = PLUGIN / "skills" / "check-map" / "SKILL.md"
        portable = self.output / "agent-plugin" / "skills" / "check-map" / "SKILL.md"
        self.assertEqual(canonical.read_bytes(), portable.read_bytes())
        self.assertFalse((self.output / "agent-plugin" / "skills" / "check-map" / "agents").exists())

    def test_claude_variant_adds_only_manual_invocation_metadata(self) -> None:
        skill = (self.output / "claude" / "skills" / "check-map" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("disable-model-invocation: true\n", skill)
        self.assertFalse((self.output / "claude" / "skills" / "check-map" / "agents").exists())

    def test_claude_variant_skills_keep_lf_newlines_and_canonical_bytes(self) -> None:
        packaged = sorted((self.output / "claude" / "skills").glob("*/SKILL.md"))
        self.assertNotEqual([], packaged)
        for skill_path in packaged:
            with self.subTest(skill=skill_path.name):
                data = skill_path.read_bytes()
                self.assertNotIn(b"\r", data)
                self.assertIn(ADAPTER_LF, data)
                stripped, errors = self.strip_adapter(data)
                self.assertEqual([], errors)
                canonical = PLUGIN / skill_path.relative_to(self.output / "claude")
                self.assertEqual(canonical.read_bytes(), stripped)

    def test_builder_writes_lf_adapter_when_the_platform_translates_newlines(self) -> None:
        canonical = (PLUGIN / SKILL_RELATIVE).read_bytes()
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = Path(temp_dir) / "SKILL.md"
            skill_path.write_bytes(canonical)
            with mock.patch.object(Path, "write_text", windows_write_text):
                _add_claude_manual_invocation(skill_path)
            data = skill_path.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertIn(ADAPTER_LF, data)
        stripped, errors = self.strip_adapter(data)
        self.assertEqual([], errors)
        self.assertEqual(canonical, stripped)

    def test_builder_inserts_lf_adapter_without_rewriting_source_newlines(self) -> None:
        canonical = (PLUGIN / SKILL_RELATIVE).read_bytes()
        crlf_source = canonical.replace(b"\n", b"\r\n")
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = Path(temp_dir) / "SKILL.md"
            skill_path.write_bytes(crlf_source)
            _add_claude_manual_invocation(skill_path)
            data = skill_path.read_bytes()
        self.assertEqual(crlf_source, data.replace(ADAPTER_LF, b""))
        stripped, errors = self.strip_adapter(data)
        self.assertEqual([], errors)
        self.assertEqual(crlf_source, stripped)

    def test_validator_strips_a_crlf_adapter_line_from_lf_skill_content(self) -> None:
        skill_path = self.output / "claude" / "skills" / "check-map" / "SKILL.md"
        crlf_adapter = skill_path.read_bytes().replace(ADAPTER_LF, ADAPTER_CRLF)
        self.assertIn(ADAPTER_CRLF, crlf_adapter)
        with temporary_file(skill_path, crlf_adapter):
            self.assertEqual([], validate_variants(self.output))

    def test_validator_strips_the_adapter_from_windows_rewritten_skill_bytes(self) -> None:
        canonical = (PLUGIN / SKILL_RELATIVE).read_bytes()
        closing = canonical.index(b"---\n", len(b"---\n"))
        packaged = canonical[:closing] + ADAPTER_LF + canonical[closing:]
        stripped, errors = self.strip_adapter(packaged.replace(b"\n", b"\r\n"))
        self.assertEqual([], errors)
        self.assertNotIn(b"disable-model-invocation", stripped)
        self.assertEqual(canonical.replace(b"\n", b"\r\n"), stripped)

    def test_validator_reports_line_ending_drift_beyond_the_claude_adapter(self) -> None:
        skill_path = self.output / "claude" / "skills" / "check-map" / "SKILL.md"
        rewritten = skill_path.read_bytes().replace(b"\n", b"\r\n")
        with temporary_file(skill_path, rewritten):
            self.assert_validation_error(
                f"claude skill content differs beyond its adapter: {SKILL_RELATIVE}"
            )
            self.assert_no_validation_error("claude manual-invocation metadata")

    def test_validator_rejects_missing_or_wrong_claude_adapter(self) -> None:
        skill_path = self.output / "claude" / "skills" / "check-map" / "SKILL.md"
        packaged = skill_path.read_bytes()
        cases = (
            ("missing", packaged.replace(ADAPTER_LF, b""), "metadata invalid"),
            (
                "wrong value",
                packaged.replace(ADAPTER_LF, b"disable-model-invocation: false\n"),
                "metadata has a wrong value or format",
            ),
            (
                "wrong format",
                packaged.replace(ADAPTER_LF, b"disable-model-invocation:true\n"),
                "metadata has a wrong value or format",
            ),
            ("duplicated", packaged.replace(ADAPTER_LF, ADAPTER_LF * 2), "metadata invalid"),
        )
        for name, data, expected in cases:
            with self.subTest(case=name):
                self.assertNotEqual(packaged, data)
                with temporary_file(skill_path, data):
                    self.assert_validation_error(f"claude manual-invocation {expected}")

    def test_validator_rejects_incomplete_claude_skill_frontmatter(self) -> None:
        skill_path = self.output / "claude" / "skills" / "check-map" / "SKILL.md"
        cases = (
            (
                "missing opening",
                b"name: check-map\n" + ADAPTER_LF + b"---\n\n# Check Map\n",
                "claude skill missing opening frontmatter",
            ),
            (
                "unterminated",
                b"---\nname: check-map\n" + ADAPTER_LF + b"\n# Check Map\n",
                "claude skill has unterminated frontmatter",
            ),
        )
        for name, data, expected in cases:
            with self.subTest(case=name):
                with temporary_file(skill_path, data):
                    self.assert_validation_error(expected)

    def test_builder_rejects_incomplete_skill_frontmatter(self) -> None:
        cases = (
            ("missing opening", b"name: check-map\n---\n\n# Check Map\n", "missing frontmatter"),
            ("empty file", b"", "missing frontmatter"),
            ("unterminated", b"---\nname: check-map\n\n# Check Map\n", "unterminated frontmatter"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for name, data, expected in cases:
                with self.subTest(case=name):
                    skill_path = Path(temp_dir) / f"{name}.md"
                    skill_path.write_bytes(data)
                    with self.assertRaises(ValueError) as raised:
                        _add_claude_manual_invocation(skill_path)
                    self.assertIn(expected, str(raised.exception))
                    self.assertEqual(data, skill_path.read_bytes())

    def test_generated_python_artifacts_are_excluded_without_source_residue(self) -> None:
        generated = (
            PLUGIN / "runtime" / "modbus_skills" / "__pycache__" / "release_guard_test.pyc",
            PLUGIN / "runtime" / "modbus_skills" / "release_guard_test.pyc",
        )
        original = {path: path.read_bytes() if path.exists() else None for path in generated}
        with ExitStack() as stack:
            for path in generated:
                stack.enter_context(temporary_file(path, b"synthetic bytecode"))
            with tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir)
                build_variants(output)
                self.assertEqual([], validate_variants(output))
                for variant in ("agent-plugin", "codex", "cursor", "claude"):
                    package = output / variant
                    self.assertEqual([], list(package.rglob("__pycache__")))
                    self.assertEqual([], list(package.rglob("*.pyc")))

        for path in generated:
            if original[path] is None:
                self.assertFalse(path.exists())
            else:
                self.assertEqual(original[path], path.read_bytes())

    def test_validator_rejects_claude_adapter_token_outside_frontmatter(self) -> None:
        skill_path = self.output / "claude" / "skills" / "check-map" / "SKILL.md"
        with temporary_file(
            skill_path,
            skill_path.read_bytes() + b"\ndisable-model-invocation: true\n",
        ):
            self.assert_validation_error("claude manual-invocation metadata outside frontmatter")

    def test_validator_rejects_malformed_agent_plugin_manifest_fields(self) -> None:
        manifest_path = self.output / "agent-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        cases = (
            ("author", "Studio Seventeen", "author must be an object"),
            ("keywords", "modbus", "keywords must be an array of strings"),
            ("unsupported", True, "manifest has unsupported fields"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                changed = dict(manifest)
                changed[field] = value
                with temporary_file(
                    manifest_path,
                    json.dumps(changed).encode("utf-8"),
                ):
                    self.assert_validation_error(expected)

    def test_validator_rejects_cursor_manifest_author_url(self) -> None:
        manifest_path = self.output / "cursor" / ".cursor-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["author"]["url"] = "https://github.com/studioxvii"
        with temporary_file(manifest_path, json.dumps(manifest).encode("utf-8")):
            self.assert_validation_error("cursor manifest author has unsupported fields")

    def test_validator_detects_exact_manifest_template_drift(self) -> None:
        manifests = (
            self.output / "agent-plugin" / "plugin.json",
            self.output / "cursor" / ".cursor-plugin" / "plugin.json",
            self.output / "claude" / ".claude-plugin" / "plugin.json",
        )
        for manifest_path in manifests:
            with self.subTest(manifest=manifest_path):
                with temporary_file(manifest_path, manifest_path.read_bytes() + b"\n"):
                    self.assert_validation_error(
                        f"generated manifest differs from manifest template: {manifest_path}"
                    )

    def test_validator_detects_shared_portable_content_drift(self) -> None:
        relative = Path("references/user-paths.md")
        for variant in ("agent-plugin", "claude"):
            with self.subTest(variant=variant):
                shared_path = self.output / variant / relative
                with temporary_file(shared_path, shared_path.read_bytes() + b"\ncontent drift\n"):
                    self.assert_validation_error(f"{variant} content differs: {relative}")

    def test_validator_rejects_codex_agent_metadata_in_portable_package(self) -> None:
        metadata = (
            self.output
            / "agent-plugin"
            / "skills"
            / "check-map"
            / "agents"
            / "openai.yaml"
        )
        with temporary_file(metadata, b"interface: {}\n"):
            self.assert_validation_error(
                "agent-plugin package contains host-specific skill agents"
            )

    def test_validator_detects_manifest_drift(self) -> None:
        manifest_path = self.output / "agent-plugin" / "plugin.json"
        original = manifest_path.read_bytes()
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = 999
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            errors = validate_variants(self.output)
            self.assertTrue(
                any("version values must be non-empty strings" in error for error in errors),
                errors,
            )
        finally:
            manifest_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
