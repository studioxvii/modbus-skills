"""Literal compound headings and centered multiline glyph spans; public data."""
from pathlib import Path
from copy import deepcopy
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from xml.etree.ElementTree import Element, SubElement, tostring

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf
from modbus_skills.pdf_table_extraction import parse_pdf_table_evidence
from modbus_skills import pdf_table_extraction as grid


def line(cells):
    result = ''
    for position, value in cells:
        result += ' ' * (position - len(result)) + value
    return result


def bbox(text):
    root = Element('doc'); page = SubElement(root, 'page')
    for index, text_line in enumerate(text.splitlines()):
        for token in re.finditer(r'\S+', text_line):
            node = SubElement(page, 'word', {
                'xMin': str(token.start() * 5), 'xMax': str(token.end() * 5),
                'yMin': str(index * 15), 'yMax': str(index * 15 + 10),
            })
            node.text = token.group()
    return tostring(root, encoding='unicode')


def parameter_table():
    return '\n'.join([
        line([(0, 'Parameters'), (25, 'Description'), (65, 'Read/Write'), (80, 'Modbus Register'), (104, 'Width'), (116, 'Type')]),
        line([(0, 'Output ceiling'), (25, 'Upper output limit'), (65, 'R'), (80, '20001'), (104, '2'), (116, 'float32')]),
    ])


def compound_table(center=True, token='RO'):
    return '\n'.join([
        line([(0, 'Register'), (14, 'Register'), (28, 'Parameter'), (49, 'Parameter'), (92, 'Data Range'), (111, 'Units'), (125, 'Firmware')]),
        line([(2 if center else 0, 'Address'), (18 if center else 14, 'Type'), (32 if center else 28, 'Name'), (49, 'Description')]),
        line([(0, '40011'), (14, token), (28, 'VAX'), (49, 'Phase X line to neutral voltage'), (92, '0 - 59999'), (111, 'Volt'), (125, 'R1')]),
    ])


def drawn_geometry(extra=False):
    values = [['Register Address', 'Register Type', 'Parameter Name', 'Parameter Description'],
              ['40011', 'RO', 'VAX', 'Phase X voltage']]
    bounds = [0, 90, 170, 280, 520]
    if extra:
        values[0].extend(['Units','Data Range']);values[1].extend(['Volt','0 - 500'])
        bounds.extend([620,720])
    rows = [SimpleNamespace(cells=[(bounds[c], r*30, bounds[c+1], (r+1)*30) for c in range(len(values[0]))]) for r in range(2)]
    chars = []
    for r, cells in enumerate(values):
        for c, text in enumerate(cells):
            x = bounds[c] + 3 if r else (bounds[c] + bounds[c+1] - len(text)*3) / 2
            for i, char in enumerate(text):
                chars.append({'text':char,'x0':x+i*3,'x1':x+(i+1)*3,'top':r*30+5,'bottom':r*30+15,'upright':True})
    page = SimpleNamespace(chars=chars)
    table = SimpleNamespace(rows=rows)
    record = parse_pdf_table_evidence(values,page_number=1,table_index=0)['quarantined_records'][0]
    return page, table, values, record


def drawn_projection_input():
    page, table, values, record = drawn_geometry()
    proof = grid._drawn_name_partition(page,table,values,record,'a'*64)
    words = [(c['x0'],c['x1'],c['top'],c['bottom'],c['text']) for c in page.chars if c['top']>=30 and c['text'].strip()]
    row = {'source_register':'40011','name':'VAX Phase X','description':'voltage',
           '_source':{'page':1,'region':'p1:y35','parser_id':'pdftotext-bbox-layout/v1'},
           '_claims':[{'field':'address','value':'40011','source_locator':{'page':1,'region':'p1:y35','bbox':[3,35,18,45]}}]}
    return row, {1:words}, proof


class ExplicitColumnRoleTests(unittest.TestCase):
    def readers(self, text):
        return pdf.parse_layout_rows(text)[0], pdf.parse_bbox_rows(bbox(text))

    def test_plural_parameters_is_name_not_description_fallback(self):
        for rows in self.readers(parameter_table()):
            self.assertEqual(1, len(rows))
            self.assertEqual('Output ceiling', rows[0]['name'])
            self.assertEqual('Upper output limit', rows[0]['description'])

    def test_parameter_description_is_literal_description(self):
        for rows in self.readers(compound_table(False)):
            self.assertEqual('VAX', rows[0]['name'])
            self.assertEqual('Phase X line to neutral voltage', rows[0]['description'])

    def test_centered_multiline_spans_join_without_larger_distance_threshold(self):
        rows = pdf.parse_bbox_rows(bbox(compound_table()))
        self.assertEqual(1, len(rows))
        for key, value in {'name': 'VAX', 'description': 'Phase X line to neutral voltage', 'source_register': '40011', 'engineering_unit': 'Volt'}.items():
            self.assertEqual(value, rows[0][key])
        self.assertNotIn('datatype', rows[0])
        self.assertNotIn('access', rows[0])
        self.assertEqual('RO', rows[0]['_extra']['register_type'])

    def test_new_semantic_aliases_preserve_original_header_claim(self):
        for text, field, heading in ((parameter_table(), 'name', 'Parameters'), (compound_table(False), 'description', 'Parameter Description')):
            for rows in self.readers(text):
                claims = [c for c in rows[0]['_claims'] if c.get('field') == field and c.get('raw_header') == heading]
                self.assertEqual(1, len(claims))
                self.assertEqual(rows[0][field], claims[0]['raw_value'])

    def test_register_type_remains_literal_for_access_type_and_unknown_tokens(self):
        for token in ('RO', 'W', 'float32', 'holding', 'unresolved'):
            row = pdf.parse_bbox_rows(bbox(compound_table(token=token)))[0]
            self.assertNotIn('access', row)
            self.assertNotIn('datatype', row)
            self.assertEqual(token, row['_extra']['register_type'])

    def test_grid_uses_exact_aliases_and_preserves_header_literals(self):
        result = parse_pdf_table_evidence([
            ['Parameters', 'Parameter Description', 'Address', 'Type'],
            ['Output ceiling', 'Upper output limit', '20001', 'float32'],
        ], page_number=1, table_index=0)
        row = result['records'][0]
        self.assertEqual('Output ceiling', row['name'])
        self.assertEqual('Upper output limit', row['description'])
        self.assertTrue(any(c.get('raw_header') == 'Parameters' for c in row['_claims']))

    def test_unrelated_header_phrases_are_not_new_aliases(self):
        for raw in ('Parameter setting', 'Parameter Description Notes', 'All Parameters', 'Register Type'):
            self.assertTrue(pdf._layout_field(raw, 0).startswith('_extra:'))

    def test_separate_same_line_labels_do_not_join_through_glyph_overlap(self):
        header = [(0, [(0, 35, 10, 'Address'), (90, 110, 10, 'Name'), (190, 210, 10, 'Type')])]
        result = pdf._bbox_header_at(header, 0)
        self.assertEqual(['address', 'name', 'datatype'], [c[1] for c in result[1]])

    def test_ambiguous_overlap_does_not_select_one_parent(self):
        items = [
            (0, [(0, 35, 10, 'Address'), (100, 145, 10, 'Parameter'), (150, 195, 10, 'Parameter')]),
            (15, [(130, 165, 25, 'Name')]),
        ]
        result = pdf._bbox_header_at(items, 0)
        self.assertFalse(result and any(c[2] == 'Parameter Name' for c in result[1]))

    def test_no_coordinate_body_value_is_used_to_guess_header_role(self):
        for token in ('RO', 'W', 'uint16'):
            rows = pdf.parse_bbox_rows(bbox(compound_table(token=token)))
            self.assertEqual('VAX', rows[0]['name'])
            self.assertEqual('40011', rows[0]['source_register'])
            self.assertNotIn('access', rows[0])

    def test_mixed_pipe_and_wide_spacing_exposes_all_address_candidates(self):
        text = '50023 | 450024 | 16 | signed | 209 | Auxiliary frequency Hz      001'
        rows, _ = pdf.parse_layout_rows(text, parser_id='ocr-text/v1')
        self.assertEqual(1, len(rows))
        self.assertEqual('450024', rows[0]['source_register'])
        self.assertEqual('Auxiliary frequency Hz', rows[0]['name'])
        self.assertEqual('signed', rows[0]['datatype'])
        self.assertNotIn('scale', rows[0])
        self.assertEqual(text, rows[0]['_source']['excerpt'])

    def test_mixed_delimiters_do_not_hide_competing_display_addresses(self):
        text = '40001 | 40002 | uint16 | Flow rate      001'
        rows, rejected = pdf.parse_layout_rows(text)
        self.assertEqual([], rows)
        self.assertTrue(any(r['code'] == 'pdf-headerless-address-roles-unresolved' for r in rejected))

    def test_pipe_inside_wide_spaced_name_is_not_a_column_separator(self):
        text = '40001  Signal A | B  uint16'
        rows, _ = pdf.parse_layout_rows(text)
        self.assertEqual('Signal A | B', rows[0]['name'])
        self.assertEqual('40001', rows[0]['source_register'])

    def test_existing_single_spaced_pipe_and_plain_rows_remain_supported(self):
        for text in ('40001 | uint16 | Flow rate', '40001  Flow rate  uint16'):
            rows, _ = pdf.parse_layout_rows(text)
            self.assertEqual('Flow rate', rows[0]['name'])
            self.assertEqual('40001', rows[0]['source_register'])

    def test_prose_keywords_cannot_consume_multiline_header_prefixes(self):
        prose = 'The actual address sent is the Register Address shown in the map minus its base.'
        text = prose + '\nImplemented in\n' + compound_table(False)
        for rows in self.readers(text):
            self.assertEqual(1, len(rows))
            self.assertEqual('VAX', rows[0]['name'])
            self.assertNotIn('datatype', rows[0])
            self.assertEqual('RO', rows[0]['_extra']['register_type'])

    def test_header_word_inside_prose_is_not_a_standalone_header_cell(self):
        text = 'Address conversion is documented separately.\n' + compound_table(False)
        self.assertIsNone(pdf._layout_header_at(text.splitlines(), 0))
        words = [(m.start() * 5, m.end() * 5, 10, m.group()) for m in re.finditer(r'\S+', text.splitlines()[0])]
        self.assertIsNone(pdf._bbox_header_at([(0, words)], 0))

    def test_single_spaced_known_headers_and_unknown_supplemental_columns_survive(self):
        self.assertIsNotNone(pdf._layout_header_at(['Address Name Type'], 0))
        text = line([(0, 'Address'), (20, 'Name'), (45, 'Firmware version'), (75, 'Type')])
        self.assertIsNotNone(pdf._layout_header_at([text], 0))
        for prefix, suffix in (('Protocol', 'Offset'), ('Display', 'Address'), ('Holding', 'Address')):
            # These established partial header prefixes must remain eligible;
            # the following standalone row still supplies its literal roles.
            text = prefix + '\n' + line([(0, suffix), (20, 'Name'), (50, 'Type')])
            self.assertIsNotNone(pdf._layout_header_at(text.splitlines(), 0))
        text = 'Read\n' + line([(0, 'Write'), (20, 'Name'), (50, 'Address')])
        self.assertIsNotNone(pdf._layout_header_at(text.splitlines(), 0))

    def test_drawn_cells_correct_name_and_description_without_type_promotion(self):
        row, words, proof = drawn_projection_input()
        before = deepcopy(row)
        result = pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64)
        self.assertEqual('VAX',result[0]['name'])
        self.assertEqual('Phase X voltage',result[0]['description'])
        self.assertNotIn('datatype',result[0])
        self.assertNotIn('access',result[0])
        self.assertEqual(before,row)
        self.assertEqual(before['_claims'],result[0]['_claims'][:1])
        self.assertTrue(any(c.get('drawn_cell_evidence') == proof for c in result[0]['_claims']))

    def test_drawn_cells_keep_unchanged_output_and_wrong_source_unchanged(self):
        row, words, proof = drawn_projection_input()
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof],'b'*64))
        row.update(name='VAX',description='Phase X voltage')
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64))

    def test_malformed_partition_metadata_never_licenses_correction(self):
        row,words,proof=drawn_projection_input()
        for key,value in (('cells',[*proof['cells'],None]),('cells',[None]*3),
                          ('source_locator',None),('source_locator',{'page':1,'row':None,'region':''})):
            bad=deepcopy(proof);bad[key]=value
            self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[bad],'a'*64))
        for key,value in (('bbox',[170,30,float('nan'),60]),('raw_header','Unlabelled text')):
            bad=deepcopy(proof);bad['cells'][1][key]=value
            self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[bad],'a'*64))
        bad=deepcopy(proof);bad['cells'][1]['header_source_locator']['page']=2
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[bad],'a'*64))

    def test_subscript_glyphs_require_same_cell_and_vertical_overlap(self):
        row,words,proof=drawn_projection_input()
        # Only the final two name glyphs move within the same physical cell.
        for i,w in enumerate(words[1]):
            if 176 <= w[0] < 183:
                words[1][i]=(w[0],w[1],39,48,w[4])
        result=pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64)
        self.assertEqual('VAX',result[0]['name'])
        words[1]=[(x0,x1,46 if 176<=x0<183 else top,55 if 176<=x0<183 else bottom,text)
                  for x0,x1,top,bottom,text in words[1]]
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64))

    def test_exact_header_cell_can_start_before_joined_header_end(self):
        page,table,values,_record=drawn_geometry()
        for char in page.chars:
            if char['top']>=30:
                char['top']+=60;char['bottom']+=60
        header=SimpleNamespace(cells=[(b[0],0,b[2],90) for b in table.rows[0].cells])
        body=SimpleNamespace(cells=[(b[0],90,b[2],120) for b in table.rows[1].cells])
        table.rows=[header,SimpleNamespace(cells=[None]*4),SimpleNamespace(cells=[None]*4),body]
        values=[values[0],[None]*4,[None]*4,values[1]]
        record=parse_pdf_table_evidence(values,page_number=1,table_index=0)['quarantined_records'][0]
        geometry=grid._prepare_description_cell_geometry(page,table,values,include_name=True)
        proof=grid._drawn_name_partition(page,table,values,record,'a'*64,geometry=geometry)
        self.assertIsNotNone(proof)
        self.assertTrue(all(c['header_source_locator']['row']==0 for c in proof['cells']))

    def test_optional_drawn_unit_and_range_correct_only_their_raw_header_link(self):
        page,table,values,record=drawn_geometry(extra=True)
        proof=grid._drawn_name_partition(page,table,values,record,'a'*64)
        row,_words,_proof=drawn_projection_input()
        row.update(engineering_unit='Volt release-X',range='500',_extra={'units':'Volt release-X','unrelated':'keep'})
        words={1:[(c['x0'],c['x1'],c['top'],c['bottom'],c['text']) for c in page.chars if c['top']>=30 and c['text'].strip()]}
        corrected=pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64)[0]
        self.assertEqual('Volt',corrected['engineering_unit'])
        self.assertEqual('0 - 500',corrected['range'])
        self.assertEqual({'units':'Volt','unrelated':'keep'},corrected['_extra'])
        self.assertNotIn('scale',corrected)
        self.assertNotIn('datatype',corrected)
        self.assertEqual('Volt release-X',row['_extra']['units'])

    def test_unproved_optional_unit_or_range_stays_unmodified(self):
        page,table,values,record=drawn_geometry(extra=True)
        # A conflicting native unit literal and an unrelated prose heading
        # are not source authority for either optional field.
        values[1][4]='Another unit';values[0][5]='Operating range commentary'
        proof=grid._drawn_name_partition(page,table,values,record,'a'*64)
        self.assertEqual(['address','name','description'],[c['field'] for c in proof['cells']])
        row,_words,_proof=drawn_projection_input()
        row.update(engineering_unit='Unresolved unit',range='Unresolved range',_extra={'units':'Unresolved unit'})
        words={1:[(c['x0'],c['x1'],c['top'],c['bottom'],c['text']) for c in page.chars if c['top']>=30 and c['text'].strip()]}
        corrected=pdf._apply_drawn_name_partitions([row],words,[proof],'a'*64)[0]
        self.assertEqual('VAX',corrected['name'])
        for field in ('engineering_unit','range','_extra'):
            self.assertEqual(row[field],corrected[field])

    def test_drawn_cells_reject_missing_or_crossing_glyphs_and_duplicate_identity(self):
        row, words, proof = drawn_projection_input()
        for mutate in ('missing','crossing','duplicate','other_address'):
            altered=deepcopy(words)
            if mutate=='missing':altered[1].pop()
            elif mutate=='crossing':altered[1].append((279,283,35,45,'Q'))
            elif mutate=='duplicate':altered[1].append(altered[1][-1])
            else:altered[1][0]=(3,6,35,45,'9')
            self.assertEqual([row],pdf._apply_drawn_name_partitions([row],altered,[proof],'a'*64),mutate)
        self.assertEqual([row],pdf._apply_drawn_name_partitions([row],words,[proof,deepcopy(proof)],'a'*64))

    def test_drawn_partition_requires_literal_header_and_unique_full_cells(self):
        for variant in ('missing','overlap','wrong_header','wrong_literal','duplicate_glyph'):
            page,table,values,record=drawn_geometry()
            if variant=='missing':table.rows[1].cells[2]=None
            elif variant=='overlap':table.rows[1].cells[2]=(170,30,300,60)
            elif variant=='wrong_header':values[0][2]='Display prose'
            elif variant=='wrong_literal':values[1][2]='Another name'
            else:page.chars.append(deepcopy(page.chars[-1]))
            self.assertIsNone(grid._drawn_name_partition(page,table,values,record,'a'*64),variant)

    @unittest.skipUnless(importlib.util.find_spec('pdfplumber') and shutil.which('pdftotext'), 'PDF readers unavailable')
    def test_real_centered_header_uses_one_grid_pass_and_keeps_type_hold(self):
        from test_pdf_description_cells import write_pdf
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'centered.pdf'
            write_pdf(path,[['Register Address','Register Type','Parameter Name','            Parameter Description'],
                            ['40011','RO','VAX','Phase X voltage']], [20,110,200,290,620])
            with mock.patch.object(pdf,'_recover_grid_rows',wraps=pdf._recover_grid_rows) as worker:
                result=pdf.extract_pdf(path,path.read_bytes())
            self.assertEqual(1,worker.call_count)
        self.assertEqual(1,len(result['records']))
        row=result['records'][0]
        self.assertEqual('VAX',row['name'])
        self.assertEqual('Phase X voltage',row['description'])
        self.assertNotIn('access',row)
        self.assertTrue(any(r.get('code')=='pdf-grid-type-unresolved' for r in result['quarantined_records']))
        self.assertTrue(any(c.get('parser_id')=='pdf-drawn-name-cell-projection/v1' for c in row['_claims']))
        self.assertFalse(any('_drawn_name_partition' in r for r in result['records']+result['quarantined_records']))

    def test_exact_addr_and_system_name_headers_preserve_literals_and_unknown_basis(self):
        for heading in ('Addr.','Addr'):
            result=parse_pdf_table_evidence([[heading,'System\nName','Access','Specifications','Comments'],
                ['23','Example Device ID','R/W','1 through 20','Source note']],page_number=1,table_index=0)
            self.assertEqual(1,len(result['records']))
            row=result['records'][0]
            self.assertEqual('Example Device ID',row['name'])
            self.assertEqual('23',row['source_register'])
            self.assertEqual('unknown',row['address_convention'])
            self.assertNotIn('datatype',row)
            self.assertEqual('1 through 20',row['_extra']['Specifications'])
            self.assertTrue(any(c.get('raw_header')==heading for c in row['_claims']))
            self.assertTrue(any(c.get('raw_header')=='System Name' for c in row['_claims']))
        for heading in ('System Name Notes','Source Addr','Addr. details'):
            self.assertTrue(pdf._layout_field(heading,0).startswith('_extra:'))

    def test_successful_large_layout_reuses_capture_in_bounded_grid_chunks(self):
        text='\f'.join(['Address  Name  Type\n40001  Flow  uint16']*257)
        calls=[]
        def call(argv,**kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv,0,text.encode() if '-layout' in argv else b'<doc/>',b'')
        with mock.patch.object(pdf,'_preflight',return_value=('pdftotext',{'name':'synthetic'},None)), \
             mock.patch.object(pdf,'_call',side_effect=call), \
             mock.patch.object(pdf,'_recover_grid_rows',return_value=([],[],[])) as worker:
            result=pdf.extract_pdf(Path('large.pdf'),b'synthetic')
        self.assertEqual(1,sum('-layout' in c for c in calls))
        self.assertEqual(2,worker.call_count)
        self.assertTrue(all(len(c.kwargs['pages'])<=256 for c in worker.call_args_list))
        self.assertTrue(all(int(c[c.index('-l')+1])-int(c[c.index('-f')+1])<256 for c in calls if '-bbox-layout' in c))
        self.assertEqual(257,len(result['records']))

    def test_expired_captured_chunk_keeps_prior_rows_and_source_inventory(self):
        text='Address  Name  Type\n40001  Flow  uint16\f'
        rows,rejected=pdf.parse_layout_rows(text)
        with mock.patch.object(pdf,'_call') as call:
            result=pdf._extract_large_pdf_in_chunks(Path('large.pdf'),b'synthetic',executable='pdftotext',capability={},
                deadline=0,captured_text=text+'\f',captured_rows=rows,captured_rejected=rejected,captured_discovered=[1])
        call.assert_not_called()
        self.assertEqual(rows,result['records'])
        self.assertFalse(result['source_coverage']['discovery_complete'])
        self.assertIn('pdf-chunk-scan-limit',[h['code'] for h in result['holds']])
        self.assertEqual([[2,2]],result['source_coverage']['page_text_evidence']['no_alphanumeric_text_ranges'])

    def test_large_route_preserves_original_deadline(self):
        text='\f'.join(['Address  Name  Type\n40001  Flow  uint16']*257)
        with mock.patch.object(pdf.time,'monotonic',return_value=100), \
             mock.patch.object(pdf,'_preflight',return_value=('pdftotext',{},None)), \
             mock.patch.object(pdf,'_call',return_value=subprocess.CompletedProcess([],0,text.encode(),b'')), \
             mock.patch.object(pdf,'_extract_large_pdf_in_chunks',return_value={'sentinel':True}) as chunk:
            self.assertEqual({'sentinel':True},pdf.extract_pdf(Path('large.pdf'),b'synthetic'))
        self.assertEqual(280,chunk.call_args.kwargs['deadline'])
        self.assertEqual(text,chunk.call_args.kwargs['captured_text'])

    def test_expired_capture_respects_existing_record_cap_with_explicit_omission(self):
        text='Address  Name  Type\n40001  Flow  uint16\n40002  Power  uint16'
        rows,rejected=pdf.parse_layout_rows(text)
        with mock.patch.object(pdf,'_MAX_CHUNK_RECORDS',1):
            result=pdf._extract_large_pdf_in_chunks(Path('large.pdf'),b'synthetic',executable='pdftotext',capability={},
                deadline=0,captured_text=text,captured_rows=rows,captured_rejected=rejected,captured_discovered=[1])
        self.assertEqual(1,len(result['records']))
        hold=next(h for h in result['holds'] if h['code']=='pdf-chunk-prior-layout-incomplete')
        self.assertEqual(1,hold['retained_prior_rows'])
        self.assertEqual(1,hold['omitted_due_to_record_limit'])

    def test_ordinary_and_explicit_bounded_requests_do_not_route_to_chunks(self):
        text='Address  Name  Type\n40001  Flow  uint16'
        for page_range in (None,(1,1)):
            with mock.patch.object(pdf,'_preflight',return_value=('pdftotext',{},None)), \
                 mock.patch.object(pdf,'_call',side_effect=[subprocess.CompletedProcess([],0,text.encode(),b''),subprocess.CompletedProcess([],0,b'<doc/>',b'')]), \
                 mock.patch.object(pdf,'_recover_grid_rows',return_value=([],[],[])), \
                 mock.patch.object(pdf,'_extract_large_pdf_in_chunks') as chunk:
                result=pdf.extract_pdf(Path('small.pdf'),b'synthetic',page_range=page_range)
            chunk.assert_not_called()
            self.assertEqual(1,len(result['records']))


if __name__ == '__main__':
    unittest.main()
