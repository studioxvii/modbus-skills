"""Public source-owned annotations; no address or engineering interpretation."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'plugins/modbus-skills/runtime'))
from tests.test_literal_source_context import bundle
from modbus_skills.parsers import parse_xlsx, ParseError
from modbus_skills.source_intake import compile_source_descriptor
from modbus_skills import user_map
from modbus_skills.user_map import UserMapError
from modbus_skills.worksheet_annotations import CODE
from modbus_skills.worksheet_annotations import AnnotationError, bind_annotations

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
X = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
NOTE = 'Source convention only: 1-based; μ\nDo not infer a default.'
CALLOUT = 'Conflicting source convention: 0-based'

def contexts(value):
    return [g for g in value['assumptions'] if g.get('code') == CODE]

def workbook(*, annotated=True, target='../comments1.xml', mode=None, drawing_kind='twoCellAnchor',
             note=NOTE, duplicate=False, unsupported=False, missing=False, two_sheets=False):
    def sheet(name, drawing):
        rows = [['Protocol Offset','Name','Area','Datatype','Access'],[0,name,'holding-register','uint16','Read']]
        body = ''.join('<row r="%d">%s</row>' % (i, ''.join(
            '<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (chr(65+j),i,escape(str(v)))
            for j,v in enumerate(row))) for i,row in enumerate(rows,1))
        return f'<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>{body}</sheetData>{drawing}</worksheet>'
    parts = {'xl/workbook.xml':f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="Map α" sheetId="1" r:id="s1"/>'
             + ('<sheet name="Other" sheetId="2" r:id="s2"/>' if two_sheets else '') + '</sheets></workbook>',
             'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{P}"><Relationship Id="s1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>'
             + (f'<Relationship Id="s2" Type="{R}/worksheet" Target="worksheets/sheet2.xml"/>' if two_sheets else '') + '</Relationships>',
             'xl/worksheets/sheet1.xml':sheet('One','<drawing r:id="d1"/>' if annotated else '')}
    if two_sheets: parts['xl/worksheets/sheet2.xml'] = sheet('Two','')
    if annotated:
        rel = f'<Relationship Id="c1" Type="{R}/comments" Target="{escape(target)}"' + (f' TargetMode="{mode}"' if mode else '') + '/>'
        parts['xl/worksheets/_rels/sheet1.xml.rels'] = f'<Relationships xmlns="{P}">{rel}{rel if duplicate else ""}<Relationship Id="d1" Type="{R}/drawing" Target="../drawings/drawing1.xml"/></Relationships>'
        if not missing:
            parts['xl/comments1.xml'] = f'<comments xmlns="{S}"><commentList><comment ref="A1" authorId="0"><text><r><t>{escape(note)}</t></r></text></comment></commentList></comments>'
        start = '<x:from><x:col>0</x:col><x:colOff>2</x:colOff><x:row>0</x:row><x:rowOff>3</x:rowOff></x:from>'
        end = '<x:to><x:col>3</x:col><x:colOff>4</x:colOff><x:row>2</x:row><x:rowOff>5</x:rowOff></x:to>' if drawing_kind=='twoCellAnchor' else '<x:ext cx="100" cy="200"/>'
        shape = '<x:pic/>' if unsupported else f'<x:sp><x:txBody><a:p><a:r><a:t>{escape(CALLOUT)}</a:t></a:r></a:p></x:txBody></x:sp>'
        parts['xl/drawings/drawing1.xml'] = f'<x:wsDr xmlns:x="{X}" xmlns:a="{A}"><x:{drawing_kind}>{start}{end}{shape}<x:clientData/></x:{drawing_kind}></x:wsDr>'
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for name, text in parts.items(): archive.writestr(name,text)
    return output.getvalue(), parts

def source(data):
    with tempfile.TemporaryDirectory(prefix='annotation-test-') as temp:
        path = Path(temp)/'synthetic α notes.xlsx'
        path.write_bytes(data)
        return compile_source_descriptor({'path':str(path)})[0]

class WorksheetAnnotationTests(unittest.TestCase):
    def test_exact_comments_and_callout_anchors_survive_parse_oem_user(self):
        data, parts = workbook()
        parsed = parse_xlsx(data)
        group, = contexts(parsed)
        comment, drawing = group['entries']
        self.assertEqual(NOTE, comment['text'])
        self.assertEqual('A1', comment['cell'])
        self.assertEqual('xl/comments1.xml', comment['member'])
        self.assertEqual(hashlib.sha256(parts[comment['member']].encode()).hexdigest(), comment['member_sha256'])
        self.assertEqual(CALLOUT, drawing['text'])
        self.assertEqual(('0','2','3','5'), (drawing['from_col'],drawing['from_colOff'],drawing['to_col'],drawing['to_rowOff']))
        oem = source(data)
        result = bundle(oem)
        delivered, = contexts(result['user_map'])
        self.assertEqual(group['entries'],delivered['entries'])
        self.assertEqual(hashlib.sha256(data).hexdigest(),delivered['source_sha256'])
        self.assertEqual(oem['points'][0]['source_refs'][0],delivered['bindings'][0]['source_ref'])
        self.assertEqual('source-context-only',delivered['status'])
        self.assertNotIn(NOTE,result['human_summary'])
        self.assertIn('Worksheet source notes',result['human_summary'])

    def test_engineering_ids_csv_and_original_holds_are_unchanged(self):
        plain = source(workbook(annotated=False)[0])
        annotated = source(workbook()[0])
        self.assertEqual(plain['points'],annotated['points'])
        self.assertEqual(plain['holds'],annotated['holds'])
        self.assertEqual(bundle(plain)['csv'],bundle(annotated)['csv'])
        self.assertIsNone(annotated['points'][0]['engineering_offset'])
        self.assertIsNone(annotated['points'][0]['byte_order'])

    def test_selection_keeps_only_actual_selected_worksheet_bindings(self):
        oem = source(workbook(two_sheets=True)[0])
        self.assertEqual([],contexts(bundle(oem,{'Two'})['user_map']))
        group, = contexts(bundle(oem,{'One'})['user_map'])
        self.assertEqual({p['oem_point_id'] for p in oem['points'] if p['name']=='One'},
                         {b['oem_point_id'] for b in group['bindings']})

    def test_external_traversal_missing_and_duplicate_relationships_remain_limitations(self):
        for kwargs in ({'target':'https://invalid.example/comment','mode':'External'},
                       {'target':'../../../../outside.xml'}, {'missing':True}, {'duplicate':True}):
            with self.subTest(kwargs=kwargs):
                oem = source(workbook(**kwargs)[0])
                group, = contexts(oem)
                self.assertEqual(['text-callout'],[e['kind'] for e in group['entries']])
                self.assertTrue(group['limitations'])
                self.assertTrue(any(h['code']=='source.worksheet-annotation-limitations' for h in oem['holds']))

    def test_one_cell_anchor_supported_but_images_and_absolute_anchors_not_interpreted(self):
        good, = contexts(parse_xlsx(workbook(drawing_kind='oneCellAnchor')[0]))
        self.assertEqual('oneCellAnchor',good['entries'][1]['anchor_kind'])
        for kwargs in ({'unsupported':True},{'drawing_kind':'absoluteAnchor'}):
            with self.subTest(kwargs=kwargs):
                group, = contexts(parse_xlsx(workbook(**kwargs)[0]))
                self.assertEqual(['comment'],[e['kind'] for e in group['entries']])
                self.assertEqual(1,len(group['limitations']))

    def test_no_annotations_add_no_registry_and_are_deterministic(self):
        data, _ = workbook(annotated=False)
        self.assertEqual([],contexts(parse_xlsx(data)))
        oem = source(data)
        self.assertEqual([],contexts(oem))
        self.assertEqual(bundle(oem),bundle(oem))
        self.assertNotIn('Worksheet source notes',bundle(oem)['human_summary'])

    def test_imported_mutations_are_rejected_before_selection(self):
        oem = source(workbook()[0])
        for mutation in ('text','source','binding','status'):
            changed = copy.deepcopy(oem)
            group, = contexts(changed)
            if mutation=='text': group['entries'][0]['text']='forged'
            elif mutation=='source': group['source_sha256']='0'*64
            elif mutation=='binding': group['bindings'][0]['oem_point_id']='not-selected-or-known'
            else: group['status']='approved'
            with self.subTest(mutation=mutation), self.assertRaises(UserMapError): bundle(changed)

    def test_scalar_limit_is_hard_failure_not_annotation_capacity_fallback(self):
        with self.assertRaisesRegex(ParseError,'16 KiB'):
            parse_xlsx(workbook(note='x'*16385)[0])

    def test_capacity_omits_whole_optional_registry_and_retains_existing_map(self):
        data, _ = workbook()
        original = source(data)
        for constant, cap in (('_LITERAL_CONTEXT_GROUPS',0),('_LITERAL_CONTEXT_BINDINGS',0),('_LITERAL_CONTEXT_BYTES',1000)):
            with self.subTest(constant=constant), patch.object(user_map,constant,cap):
                limited = source(data)
                self.assertEqual(original['points'],limited['points'])
                self.assertEqual([],contexts(limited))
                self.assertTrue(any(h['code']=='source.worksheet-annotations-incomplete' for h in limited['holds']))

    def test_malformed_later_import_is_not_hidden_by_an_earlier_capacity_limit(self):
        original = source(workbook()[0])
        groups = copy.deepcopy(contexts(original))
        groups[0]['entries'].append({'kind':'comment','member':'xl/comments1.xml','text':[]})
        with patch.object(user_map,'_LITERAL_CONTEXT_BYTES',1):
            with self.assertRaisesRegex(AnnotationError,'text or integers'):
                bind_annotations(groups,original['points'],original['input_hashes']['source'],imported=True)

    def test_annotation_literal_conflict_preserves_both_claims_not_a_resolved_basis(self):
        oem = source(workbook()[0])
        records = contexts(bundle(oem)['user_map'])[0]['entries']
        self.assertEqual([NOTE,CALLOUT],[r['text'] for r in records])
        self.assertEqual(0,oem['points'][0]['protocol_offset'])
        self.assertEqual('protocol-offset',oem['points'][0]['source_address']['convention'])
        self.assertEqual(1,oem['points'][0]['word_span'])  # Explicit uint16, not the note.

    def test_malformed_xml_remains_a_parse_error_and_external_drawing_links_are_not_followed(self):
        data, parts = workbook()
        parts['xl/drawings/_rels/drawing1.xml.rels'] = f'<Relationships xmlns="{P}"><Relationship Id="link" Type="{R}/hyperlink" Target="https://invalid.example/never-read" TargetMode="External"/></Relationships>'
        def packed(values):
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as archive:
                for member,text in values.items():archive.writestr(member,text)
            return output.getvalue()
        group, = contexts(parse_xlsx(packed(parts)))
        self.assertEqual('uninterpreted-drawing-relationship',group['limitations'][0]['code'])
        self.assertEqual([NOTE,CALLOUT],[e['text'] for e in group['entries']])
        parts['xl/comments1.xml']='<comments'
        with self.assertRaises(ParseError):parse_xlsx(packed(parts))

if __name__ == '__main__': unittest.main()
