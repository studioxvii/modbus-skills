"""Prospective small exact accounting controls against standard JSON encoding."""
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import user_map

class LiteralContextCostParityTests(unittest.TestCase):
    def test_small_escaped_metadata_matches_prior_conservative_cost(self):
        literals=[chr(i) for i in range(256)]+['\u2028','\U0001f642','\\"\n\t',0,-1,10**100,1.25,-0.0,1e30]
        for literal in literals:
            value={'oem_point_id':'point','source_field':'notes','source_ref':{'record_id':literal}}
            text=json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2,allow_nan=False)
            expected=len(text.encode())+32*(text.count('\n')+1)+256
            with self.subTest(literal=repr(literal)):
                self.assertEqual(expected,user_map._literal_context_size(value))

if __name__=='__main__':unittest.main()
