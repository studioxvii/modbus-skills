from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from scripts.build_plugin_variants import ROOT, build_variants
from scripts.validate_plugin_variants import validate_variants


PLUGIN = ROOT / "plugins" / "modbus-skills"


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
