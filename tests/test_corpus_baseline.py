import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CorpusBaselineInputTests(unittest.TestCase):
    def test_invalid_case_or_unbounded_time_cannot_succeed(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            corpus = directory / "corpus"
            corpus.mkdir()
            (corpus / "synthetic.pdf").write_bytes(b"%PDF-1.4\n")
            for arguments in (("--case", "unknown"), ("--max-seconds", "0"), ("--max-seconds", "901")):
                completed = subprocess.run([sys.executable, str(root / "scripts/run_corpus_baseline.py"),
                    "--corpus", str(corpus), "--output", str(directory / "out"), *arguments],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(2, completed.returncode)
                self.assertFalse((directory / "out" / "baseline.json").exists())
