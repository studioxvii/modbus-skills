"""A custom metaclass must not impersonate an exact built-in scalar type."""
from collections import UserDict
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import compiler_contracts as contracts

class PortableMetaclassIdentityTests(unittest.TestCase):
    def test_builtin_equal_metaclass_does_not_skip_mapping_path_validation(self):
        for target in (int,float,bool):
            class EqualBuiltin(type(UserDict)):
                def __eq__(cls,other):return other is target or cls is other
                __hash__=type.__hash__
            class DisguisedMapping(UserDict,metaclass=EqualBuiltin):pass
            value=DisguisedMapping({'nested':[{'note':'/synthetic'}]})
            with self.subTest(target=target.__name__):
                with self.assertRaises(contracts.CompilerContractError) as caught:
                    contracts._assert_portable(value)
                self.assertEqual('portable artifact contains a local absolute path: artifact.nested[0].note',str(caught.exception))

if __name__=='__main__':unittest.main()
