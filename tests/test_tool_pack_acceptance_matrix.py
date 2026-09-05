"""Synthetic packaging acceptance; these checks do not claim native execution."""
from __future__ import annotations

import csv
import hashlib
import io
from itertools import combinations
import json
from pathlib import Path
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/modbus-skills/runtime"))

from modbus_skills.artifacts import artifact_envelope  # noqa: E402
from modbus_skills.read_plan import compile_read_plan  # noqa: E402
from modbus_skills.tool_pack import build_tool_pack  # noqa: E402

TARGETS = ("node-red", "modpoll", "modscan")
PROFILES = ("gavinying-cli", "proconx-cli", "witte-desktop", "witte-v12-xml")
PRIVATE_SENTINEL = "SYNTHETIC_REVIEW_DETAIL_NOT_FOR_PORTABLE_PACK"


class ToolPackAcceptanceMatrixTests(unittest.TestCase):
    def test_every_combination_mode_and_profile_is_portable_exact_and_repeatable(self):
        point = {
            "logical_point_id": "synthetic-value", "name": "Synthetic Value",
            "route_id": "synthetic-loop", "unit_id": 7,
            "area": "holding-register", "function_code": 3,
            "protocol_offset": 0, "datatype": "uint16", "word_span": 1,
            "normalization_status": "confirmed", "access": "read-only",
            "scale": 1, "engineering_offset": 0,
            "source_evidence": [{"note": PRIVATE_SENTINEL}],
        }
        canonical = {"schema_version": "modbus-map/v1", "points": [point],
                     "review_decisions": [{"reason": PRIVATE_SENTINEL}]}
        plan = artifact_envelope(compile_read_plan([point]).to_dict(),
            schema_version="modbus-read-plan/v1", inputs={"canonical_map": canonical})
        selections = [selection for size in range(1, 4) for selection in combinations(TARGETS, size)]
        attempted = 0
        for selection in selections:
            for mode in ("probe", "final"):
                for profile in PROFILES:
                    with self.subTest(targets=selection, mode=mode, profile=profile):
                        attempted += 1
                        options = {"modpoll": {"profile": profile}} if "modpoll" in selection else {}
                        pack = build_tool_pack(canonical, plan, targets=selection, mode=mode, target_options=options)
                        held = mode == "probe" and profile == "gavinying-cli" and "modpoll" in selection
                        expected = "held" if held and len(selection) == 1 else "partial" if held else "generated"
                        self.assertEqual(expected, pack.status)
                        self.assertEqual(set(selection), {result.target for result in pack.target_results})
                        for result in pack.target_results:
                            self.assertEqual("held" if held and result.target == "modpoll" else "generated", result.status)
                            self.assertEqual(pack.map_hash, result.map_hash)
                            self.assertEqual(pack.read_plan_hash, result.read_plan_hash)
                        files = pack.files()
                        folders = {path.split("/", 1)[0] for path in files if "/" in path}
                        # A held target has a manifest disposition, not runnable files.
                        self.assertEqual(set(selection) - ({"modpoll"} if held else set()), folders)
                        if held:
                            result = next(result for result in pack.target_results if result.target == "modpoll")
                            self.assertIn("MODPOLL_SINGLE_ATTEMPT_UNSUPPORTED", {finding.code for finding in result.findings})
                        for data in files.values():
                            self.assertNotIn(PRIVATE_SENTINEL.encode(), data)
                        runtime_map = json.loads(files["canonical-map.json"])
                        runtime_plan = json.loads(files["read-plan.json"])
                        self.assertEqual(["synthetic-value"], [row["logical_point_id"] for row in runtime_map["points"]])
                        requests = runtime_plan["requests"]
                        self.assertEqual(1, len(requests))
                        self.assertEqual((7, 3, 0, 1), tuple(requests[0][key] for key in ("unit_id", "function_code", "start_offset", "quantity")))
                        if "modscan" in selection:
                            rows = list(csv.DictReader(io.StringIO(files["modscan/read-plan.csv"].decode())))
                            self.assertEqual(1, len(rows))
                            self.assertEqual(("7", "03", "0", "1"), tuple(rows[0][key] for key in ("unit_id", "function_code", "protocol_offset_base_0", "quantity")))
                        recorded = dict(row.split("  ", 1)[::-1] for row in files["checksums.sha256"].decode().splitlines())
                        self.assertEqual(set(files) - {"checksums.sha256"}, set(recorded))
                        for path, digest in recorded.items():
                            self.assertEqual(hashlib.sha256(files[path]).hexdigest(), digest)
                        archive_bytes = pack.to_zip_bytes()
                        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                            self.assertEqual(set(files), set(archive.namelist()))
                            for path, data in files.items():
                                self.assertEqual(data, archive.read(path))
                        repeated = build_tool_pack(canonical, plan, targets=tuple(reversed(selection)), mode=mode, target_options=options)
                        self.assertEqual(files, repeated.files())
                        self.assertEqual(archive_bytes, repeated.to_zip_bytes())
        self.assertEqual(56, attempted)


if __name__ == "__main__":
    unittest.main()
