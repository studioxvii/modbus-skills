"""Prospective combined-cap/malformed-source controls, using candidate01 fixtures."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from tests.test_worksheet_annotations import workbook, contexts, source, CALLOUT, S
from modbus_skills import user_map, worksheet_annotations
from modbus_skills.parsers import parse_xlsx, ParseError

CAPS=(('_LITERAL_CONTEXT_GROUPS',0),('_LITERAL_CONTEXT_BYTES',1000),('_LITERAL_CONTEXT_BINDINGS',0))
def packed(parts):
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as archive:
        for member,value in parts.items():archive.writestr(member,value)
    return result.getvalue()

class AnnotationCapacityValidationTests(unittest.TestCase):
    def test_exhausted_capacity_cannot_hide_oversized_comment(self):
        data,_=workbook(note='x'*16385)
        for constant,cap in CAPS:
            with self.subTest(constant=constant), patch.object(user_map,constant,cap):
                with self.assertRaisesRegex(ParseError,'16 KiB'):parse_xlsx(data)

    def test_exhausted_capacity_cannot_hide_later_oversized_callout(self):
        _,parts=workbook()
        parts['xl/drawings/drawing1.xml']=parts['xl/drawings/drawing1.xml'].replace(CALLOUT,'x'*16385)
        for constant,cap in CAPS:
            with self.subTest(constant=constant), patch.object(user_map,constant,cap):
                with self.assertRaisesRegex(ParseError,'16 KiB'):parse_xlsx(packed(parts))

    def test_exhausted_capacity_cannot_hide_second_comment(self):
        _,parts=workbook()
        second='<comment ref="A2" authorId="0"><text><t>'+'é'*8193+'</t></text></comment>'
        parts['xl/comments1.xml']=parts['xl/comments1.xml'].replace('</commentList>',second+'</commentList>')
        for constant,cap in CAPS:
            with self.subTest(constant=constant), patch.object(user_map,constant,cap):
                with self.assertRaisesRegex(ParseError,'16 KiB'):parse_xlsx(packed(parts))

    def test_exhausted_capacity_still_rejects_later_malformed_xml(self):
        _,parts=workbook()
        parts['xl/drawings/drawing1.xml']='<unclosed'
        with patch.object(user_map,'_LITERAL_CONTEXT_GROUPS',0):
            with self.assertRaises(ParseError):parse_xlsx(packed(parts))

    def test_valid_over_capacity_source_has_no_registry_and_preserves_existing_points(self):
        data,_=workbook()
        ordinary=source(data)
        for constant,cap in CAPS:
            with self.subTest(constant=constant), patch.object(user_map,constant,cap):
                value=source(data)
                self.assertEqual(ordinary['points'],value['points'])
                self.assertEqual([],contexts(value))
                self.assertEqual(1,sum(h['code']=='source.worksheet-annotations-incomplete' for h in value['holds']))

    def test_validation_only_mode_visits_later_literal_without_hashing_registry(self):
        data,_=workbook()
        original=worksheet_annotations._text
        seen=[]
        def observe(nodes):
            text=original(nodes);seen.append(text);return text
        with patch.object(user_map,'_LITERAL_CONTEXT_GROUPS',0), \
             patch.object(worksheet_annotations,'_text',side_effect=observe), \
             patch.object(worksheet_annotations,'_identity',side_effect=AssertionError('no retained registry hash')):
            value=parse_xlsx(data)
        self.assertEqual(2,len(seen))
        self.assertEqual(CALLOUT,seen[-1])
        self.assertEqual([],contexts(value))

if __name__=='__main__':unittest.main()
