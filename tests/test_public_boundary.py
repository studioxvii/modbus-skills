from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_public_boundary as boundary


class PublicBoundaryTests(unittest.TestCase):
    def test_ignored_corpus_is_local_but_force_tracked_corpus_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("private/\n.venv-lab/\n", encoding="utf-8")
            (root / "private").mkdir()
            source = root / "private" / "fixture.pdf"
            source.write_bytes(b"synthetic test marker")
            (root / ".venv-lab").mkdir()
            (root / ".venv-lab" / "python").symlink_to(sys.executable)
            with patch.object(boundary, "ROOT", root):
                self.assertEqual([], boundary.check())
                subprocess.run(["git", "add", "-f", "private/fixture.pdf"], cwd=root, check=True)
                errors = boundary.check()
                self.assertTrue(any("private or vendor" in item for item in errors))
                self.assertTrue(any("PDF files" in item for item in errors))

    def test_untracked_public_files_and_broken_symlinks_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "bad.md").write_text("/" + "home/example/private/data", encoding="utf-8")
            (root / "broken").symlink_to("missing")
            with patch.object(boundary, "ROOT", root):
                errors = boundary.check()
            self.assertTrue(any("absolute user path" in item for item in errors))
            self.assertTrue(any("symlinks" in item for item in errors))

    def test_source_archive_retains_conservative_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fixture.pdf").write_bytes(b"synthetic test marker")
            with patch.object(boundary, "ROOT", root):
                self.assertTrue(any("PDF files" in item for item in boundary.check()))


if __name__ == "__main__":
    unittest.main()
