"""Prospective exact portability semantics and native-scalar work controls."""
from collections import UserDict
from collections.abc import Mapping
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills import compiler_contracts as contracts

FORBIDDEN=('absolute_path','api_key','capture','case_path','credential',
    'credentials','endpoint','evidence_payload','host','hostname','ip_address',
    'password','private_evidence','raw_evidence','route_id','secret',
    'source_excerpt','token','transport','unit_id')

def reject_message(value,path='artifact'):
    try:
        contracts._assert_portable(value,path)
    except contracts.CompilerContractError as error:
        return str(error)
    return None

class PortableScalarFastpathTests(unittest.TestCase):
    def test_explicit_absolute_paths_keep_exact_error(self):
        paths=('/', '/synthetic/manual', '~/', '~/synthetic', '\\',
               '\\synthetic', '\\\\synthetic\\share', 'A:/', 'z:\\',
               'C:/synthetic\nrow', 'd:\\synthetic')
        for value in paths:
            with self.subTest(value=value):
                self.assertEqual('portable artifact contains a local absolute path: scope.rows[2].note',
                    reject_message(value,'scope.rows[2].note'))

    def test_safe_fragments_literals_and_unicode_lookalikes(self):
        values=('', 'A', 'A:', 'A:relative', 'relative/path', 'file.txt',
            ' /synthetic', ' ~/synthetic', '~relative', '~~/synthetic',
            '\nC:/synthetic', '0:/synthetic', '@:/synthetic', '[:/synthetic',
            '`:/synthetic', '{:/synthetic', '\u00e9:/synthetic',
            '\u212a:/synthetic', '\uff21:/synthetic', '\U0001f642:/synthetic',
            '&sol;synthetic', 'literal note with A:/inside', 'C\uff1a/synthetic')
        for value in values:
            with self.subTest(value=value):self.assertIsNone(reject_message(value))

    def test_bounded_drive_prefix_equivalence_to_original_regex(self):
        original=re.compile(r'^[A-Za-z]:[\\/]')
        firsts=[chr(i) for i in range(256)]+['\u212a','\uff21','\U0001f642']
        for first in firsts:
            for second in (':','x'):
                for third in ('/','\\','x'):
                    for tail in ('','\nsynthetic'):
                        value=first+second+third+tail
                        forbidden=value.startswith(('/','~/','\\')) or original.match(value) is not None
                        with self.subTest(value=repr(value)):
                            expected='portable artifact contains a local absolute path: artifact' if forbidden else None
                            self.assertEqual(expected,reject_message(value))

    def test_all_forbidden_key_spellings_and_locations_unchanged(self):
        self.assertEqual(set(FORBIDDEN),set(contracts._PORTABLE_FORBIDDEN_FIELDS))
        for key in FORBIDDEN:
            for spelling in (key,key.upper(),' '+key.replace('_','-').upper()+' '):
                with self.subTest(key=spelling):
                    self.assertEqual('portable artifact field is not allowed: root.rows[0].'+spelling,
                        reject_message({'rows':[{spelling:'synthetic'}]},'root'))

    def test_nested_first_rejection_order_and_exact_path(self):
        value={'safe':[None,7,{'note':'/synthetic/a'}], 'password':'synthetic'}
        self.assertEqual('portable artifact contains a local absolute path: artifact.safe[2].note',reject_message(value))
        value={'password':'synthetic','safe':[{'note':'/synthetic/a'}]}
        self.assertEqual('portable artifact field is not allowed: artifact.password',reject_message(value))
        value={'rows':[{'note':'relative'}, {'note':'C:/synthetic'}, {'note':'/other'}]}
        self.assertEqual('portable artifact contains a local absolute path: artifact.rows[1].note',reject_message(value))

    def test_every_nested_visit_and_order_are_retained(self):
        value={'rows':[{'name':'first','value':1},{'name':'second','value':None}],'flag':False}
        original=contracts._assert_portable
        with patch.object(contracts,'_assert_portable',wraps=original) as walk:
            walk(value,'root')
        paths=[call.args[1] for call in walk.call_args_list]
        self.assertEqual(['root','root.rows','root.rows[0]','root.rows[0].name',
            'root.rows[0].value','root.rows[1]','root.rows[1].name',
            'root.rows[1].value','root.flag'],paths)

    def test_mutation_is_revalidated_without_cache(self):
        value={'rows':[{'note':'relative'}]}
        self.assertIsNone(reject_message(value))
        value['rows'][0]['note']='\\synthetic'
        self.assertEqual('portable artifact contains a local absolute path: artifact.rows[0].note',reject_message(value))
        value['rows'][0]['note']='relative'
        value['rows'][0][' Source-Excerpt ']='synthetic'
        self.assertEqual('portable artifact field is not allowed: artifact.rows[0]. Source-Excerpt ',reject_message(value))

    def test_custom_strings_keep_old_startswith_and_regex_fallback(self):
        class Custom(str):
            calls=0
            answer=False
            def startswith(self,*args,**kwargs):
                type(self).calls+=1
                return type(self).answer
        Custom.answer=True
        self.assertEqual('portable artifact contains a local absolute path: artifact',reject_message(Custom('ordinary')))
        Custom.answer=False
        self.assertIsNone(reject_message(Custom('/synthetic')))
        self.assertEqual('portable artifact contains a local absolute path: artifact',reject_message(Custom('D:/synthetic')))
        self.assertEqual(3,Custom.calls)

    def test_custom_containers_and_numeric_mapping_keep_traversal(self):
        class Dictionary(dict):
            def items(self):return iter([('private-evidence','synthetic')])
        class Items(list):
            def __iter__(self):return iter(['/synthetic'])
        class NumberMapping(int,Mapping):
            def __getitem__(self,key):return '/synthetic'
            def __iter__(self):return iter(['note'])
            def __len__(self):return 1
        for value,expected in (
                (Dictionary(),'portable artifact field is not allowed: artifact.private-evidence'),
                (Items(),'portable artifact contains a local absolute path: artifact[0]'),
                (UserDict({'note':'/synthetic'}),'portable artifact contains a local absolute path: artifact.note'),
                (NumberMapping(7),'portable artifact contains a local absolute path: artifact.note')):
            with self.subTest(kind=type(value).__name__):self.assertEqual(expected,reject_message(value))

    def test_scalar_and_unsupported_type_behavior_is_not_broadened(self):
        for value in (None,True,False,0,-1,2**80,0.0,-0.0,float('nan'),float('inf'),
                      ('/synthetic',),object()):
            with self.subTest(kind=type(value).__name__):self.assertIsNone(reject_message(value))
        # This helper alone is not JSON/schema/numeric validation. Other
        # existing validators remain required and are not bypassed here.

    def test_exact_scalars_do_not_dispatch_mapping_abc(self):
        visits=[]
        class ProbeMeta(type):
            def __instancecheck__(cls,value):
                visits.append(type(value).__name__)
                return isinstance(value,Mapping)
        class Probe(metaclass=ProbeMeta):pass
        with patch.object(contracts,'Mapping',Probe):
            for value in ('ordinary', '', None, True, False, 0, -1, 2**80, 1.5, -0.0):
                contracts._assert_portable(value)
        self.assertEqual([],visits)

    def test_exact_strings_do_not_dispatch_drive_regex(self):
        original=contracts.re.match
        with patch.object(contracts.re,'match',wraps=original) as match:
            outcomes=[reject_message(value) for value in ('ordinary','','A:','A:relative','A:/synthetic','\u00e9:/synthetic')]
            calls=match.call_count
        self.assertEqual([None,None,None,None,'portable artifact contains a local absolute path: artifact',None],outcomes)
        self.assertEqual(0,calls)

if __name__=='__main__':unittest.main()
