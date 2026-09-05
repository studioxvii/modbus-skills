from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from skill_usability.contracts import load_campaign
from skill_usability.oracles import evaluate_trial


class ByteCandidateOracleTests(unittest.TestCase):
    def test_known_type_needs_four_faithful_layouts_not_extra_types(self):
        scenario = load_campaign()["loaded_scenarios"][4]
        identity = scenario["oracle_profile"]["expected_sample_identity"]
        permutations = {"ABCD": b"\x42\xf6\x00\x00", "BADC": b"\xf6\x42\x00\x00",
                        "CDAB": b"\x00\x00\x42\xf6", "DCBA": b"\x00\x00\xf6\x42"}
        payload = {"schema_version": "modbus-byte-order-evidence/v1", "sample_identity": identity,
                   "sample": {"words": [17142, 0]}, "candidates": [
                       {"layout": layout, "datatype": "float32", "sample_id": identity["sample_id"],
                        "decoded_value": struct.unpack(">f", raw)[0]}
                       for layout, raw in permutations.items()]}
        events = [{"kind": "skill-selected", "skill": "check-byte-order"},
                  {"kind": "hold", "code": "byte-order-human-confirmation-required"}]
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary)
            def evaluate(value):
                (snapshot / "candidate-table.json").write_text(json.dumps(value))
                return evaluate_trial(scenario=scenario, events=events, artifacts=[], snapshot=snapshot,
                                      terminal_reason="completed", execution_status="completed")
            self.assertEqual("passed", evaluate(payload)["status"])
            for mutation in ("missing", "duplicate", "wrong-value", "wrong-identity", "winner"):
                with self.subTest(mutation=mutation):
                    broken = copy.deepcopy(payload)
                    if mutation == "missing":
                        broken["candidates"].pop()
                    elif mutation == "duplicate":
                        broken["candidates"].append(broken["candidates"][0])
                    elif mutation == "wrong-value":
                        broken["candidates"][0]["decoded_value"] = 124
                    elif mutation == "wrong-identity":
                        broken["sample_identity"]["unit_id"] = 2
                    else:
                        broken["winner"] = "ABCD"
                    self.assertEqual("failed", evaluate(broken)["status"])


if __name__ == "__main__":
    unittest.main()
