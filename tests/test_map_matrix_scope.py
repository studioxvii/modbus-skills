import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("map_matrix_scope_worker", ROOT / "scripts/pstack/map_matrix/run_worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class MatrixScopeTests(unittest.TestCase):
    def test_early_rows_do_not_restrict_late_table_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "synthetic.pdf"
            source.write_bytes(b"%PDF synthetic")
            calls = []
            def cli(command, arguments):
                calls.append((command, arguments))
                if command == "extract-pdf-map":
                    rows = [{"name": "Early", "_source": {"page": 2}}]
                    if "--pages" not in arguments:
                        rows.append({"name": "Late", "_source": {"page": 45}})
                    output = Path(arguments[arguments.index("--output") + 1])
                    (output / "candidates.json").write_text(json.dumps({"records": rows}))
                return 0, "synthetic stage receipt"
            evals = json.loads((ROOT / "scripts/pstack/map_matrix/evals.json").read_text())
            with mock.patch.object(worker, "ARTIFACTS", directory / "runs"), mock.patch.object(worker, "run_cli_capture", side_effect=cli):
                result = worker.run_map({"id": "scope", "relative_path": str(source), "filename": source.name,
                    "format": "pdf", "bytes": source.stat().st_size, "sha256": worker.sha256(source)}, evals)
            intake = next(step for step in result["steps"] if step["step"] == "intake")
            self.assertEqual(2, intake["records"])
            self.assertEqual("auto-discovery", intake["pages"])
            extraction_calls = [args for command, args in calls if command == "extract-pdf-map"]
            self.assertEqual(1, len(extraction_calls))
            self.assertNotIn("--pages", extraction_calls[0])
            request = json.loads((directory / "runs/scope/request.json").read_text())
            self.assertNotIn("pages", request["source"])
