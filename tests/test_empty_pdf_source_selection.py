"""An image-only source must retain its source blocker before name matching."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills.compiler import CompilerError, compile_user_map
from modbus_skills.artifacts import stable_input_hash
from modbus_skills.compiler_contracts import build_oem_map
from modbus_skills.source_intake import SourceIntakeError, validate_selection_template_structure


def synthetic_image_only_pdf():
    """Public synthetic bitmap table, with no PDF text operators or OCR payload."""
    font={
        'A':['01110','10001','10001','11111','10001','10001','10001'],
        'D':['11110','10001','10001','10001','10001','10001','11110'],
        'R':['11110','10001','10001','11110','10100','10010','10001'],
        'E':['11111','10000','10000','11110','10000','10000','11111'],
        'S':['01111','10000','10000','01110','00001','00001','11110'],
        'N':['10001','11001','10101','10011','10001','10001','10001'],
        'M':['10001','11011','10101','10101','10001','10001','10001'],
        'T':['11111','00100','00100','00100','00100','00100','00100'],
        'O':['01110','10001','10001','10001','10001','10001','01110'],
        'L':['10000','10000','10000','10000','10000','10000','11111'],
        'I':['11111','00100','00100','00100','00100','00100','11111'],
        'Z':['11111','00001','00010','00100','01000','10000','11111'],
        '0':['01110','10001','10011','10101','11001','10001','01110'],
    }
    width,height,scale=420,100,4
    pixels=bytearray([255])*(width*height)
    for line,text in enumerate(('ADDRESS NAME','0       TOTALIZER')):
        for column,char in enumerate(text):
            if char==' ':continue
            for y,row in enumerate(font[char]):
                for x,bit in enumerate(row):
                    if bit=='1':
                        for dy in range(scale):
                            start=(10+line*44+y*scale+dy)*width+10+column*6*scale+x*scale
                            pixels[start:start+scale]=b'\x00'*scale
    compressed=zlib.compress(bytes(pixels))
    content=b'q 420 0 0 100 0 0 cm /Im0 Do Q'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 100] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /XObject /Subtype /Image /Width 420 /Height 100 /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length '+str(len(compressed)).encode()+b' >>\nstream\n'+compressed+b'\nendstream',
        b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream']
    document=bytearray(b'%PDF-1.4\n');offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(document));document.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref=len(document);document.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets[1:]:document.extend(f'{offset:010d} 00000 n \n'.encode())
    document.extend(f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(document)


@unittest.skipUnless(importlib.util.find_spec('pdfplumber'),'PDF dependency unavailable')
class EmptyPdfSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);self.source=self.root/'synthetic-image.pdf'
        self.source.write_bytes(synthetic_image_only_pdf())

    def request(self,mode):
        template={'schema_version':'modbus-user-selection-template/v1','requested_measurements':['TOTALIZER']}
        if mode=='full':template['mode']='all-readable'
        else:template.update({'included':[{'exact_name':'TOTALIZER','matched_intent':'TOTALIZER','match_quality':'exact',
            'reason':'User requests the visibly named synthetic table point','evidence_refs':['pdf:page:1:table:1:row:1']}], 'suggested':[],'excluded':[]})
        return {'schema_version':'modbus-compile-request/v1','source':{'path':str(self.source),'format':'pdf'},
                'selection_template':template,'targets':[],'target_options':{}}

    def check_source_case(self,mode):
        case=self.root/mode;request=self.request(mode)
        result=compile_user_map(request,case)
        self.assertEqual('awaiting-source-decision',result['state'])
        self.assertEqual('provide-corrected-source',result['next_action']['kind'])
        self.assertTrue(result['next_action']['starts_new_case'])
        oem=json.loads((case/'artifacts/oem-map.json').read_text())
        self.assertEqual([],oem['points'])
        self.assertIn('pdf-ocr-required',{h['code'] for h in oem['holds']})
        original=json.loads((case/'control/request-identity.json').read_text())
        self.assertEqual(request['selection_template'],original['request']['selection_template'])
        normalized=json.loads((case/'control/request.json').read_text())
        self.assertEqual(request['selection_template'],normalized['selection_template'])
        self.assertEqual([],normalized['selection_candidate']['included'])
        self.assertEqual([],normalized['selection_candidate']['suggested'])
        self.assertEqual([],normalized['selection_candidate']['excluded'])
        self.assertFalse((case/'artifacts/selection.json').exists())
        self.assertFalse((case/'output/user-map.json').exists())

    def test_fixture_has_visible_image_but_no_extractable_text(self):
        import pdfplumber
        with pdfplumber.open(self.source) as pdf:
            self.assertEqual(1,len(pdf.pages));self.assertEqual(1,len(pdf.pages[0].images))
            self.assertEqual('',pdf.pages[0].extract_text())

    def test_full_request_preserves_ocr_source_blocker(self):self.check_source_case('full')

    def test_curated_request_preserves_ocr_source_blocker_before_matching(self):self.check_source_case('curated')

    def test_malformed_curated_template_is_not_silently_deferred(self):
        for label,mutate in (
            ('unknown-template-field',lambda t:t.update({'unexpected':True})),
            ('bad-schema',lambda t:t.update({'schema_version':'wrong'})),
            ('bad-entry-shape',lambda t:t.update({'included':[17]})),
            ('two-selectors',lambda t:t['included'][0].update({'oem_point_id':'invented'})),
            ('missing-reason',lambda t:t['included'][0].pop('reason')),
            ('empty-evidence',lambda t:t['included'][0].update({'evidence_refs':[]})),
        ):
            with self.subTest(label=label):
                request=copy.deepcopy(self.request('curated'));mutate(request['selection_template'])
                with self.assertRaises(CompilerError):compile_user_map(request,self.root/label)
                self.assertFalse((self.root/label/'case.json').exists())

    def test_deferred_template_structure_checks_all_entry_fields_without_source_ids(self):
        template=self.request('curated')['selection_template']
        validate_selection_template_structure(template)
        for label,mutate in (
            ('unknown-entry',lambda t:t['included'][0].update({'unexpected':True})),
            ('missing-selector',lambda t:t['included'][0].pop('exact_name')),
            ('nontext-selector',lambda t:t['included'][0].update({'exact_name':12})),
            ('blank-selector',lambda t:t['included'][0].update({'exact_name':' '})),
            ('missing-intent',lambda t:t['included'][0].pop('matched_intent')),
            ('invalid-quality',lambda t:t['included'][0].update({'match_quality':'unsupported'})),
            ('bad-confidence',lambda t:t['included'][0].update({'confidence':float('nan')})),
            ('boolean-confidence',lambda t:t['included'][0].update({'confidence':True})),
            ('out-of-range-confidence',lambda t:t['included'][0].update({'confidence':2})),
            ('bad-reason',lambda t:t['included'][0].update({'reason':False})),
            ('bad-evidence',lambda t:t['included'][0].update({'evidence_refs':[1]})),
            ('bad-measurements',lambda t:t.update({'requested_measurements':[None]})),
            ('bad-dispositions',lambda t:t.update({'suggested':'none'})),
            ('duplicate-selector',lambda t:t['excluded'].append(copy.deepcopy(t['included'][0]))),
            ('mode-mixed-with-entries',lambda t:t.update({'mode':'all-readable'})),
            ('unsupported-mode',lambda t:t.update({'mode':'scan'})),
        ):
            with self.subTest(label=label):
                invalid=copy.deepcopy(template);mutate(invalid)
                with self.assertRaises(SourceIntakeError):validate_selection_template_structure(invalid)

    def test_explicit_id_is_retained_as_intent_not_bound_to_an_invented_point(self):
        request=self.request('curated')
        entry=request['selection_template']['included'][0]
        entry.pop('exact_name');entry['oem_point_id']='user-supplied-unresolved-id'
        case=self.root/'explicit-id'
        result=compile_user_map(request,case)
        self.assertEqual('awaiting-source-decision',result['state'])
        normalized=json.loads((case/'control/request.json').read_text())
        self.assertEqual(request['selection_template'],normalized['selection_template'])
        self.assertEqual([],normalized['oem_map']['points'])
        self.assertEqual([],normalized['selection_candidate']['included'])

    def test_confidence_contract_boundaries_and_nonempty_source_rejection(self):
        # compiler_contracts.validate_user_selection already specifies numeric 0..1.
        # Structural validation now rejects violations before source-name lookup,
        # including at the direct user-map entry boundary.
        from modbus_skills.user_map import UserMapError, validate_selection_entry_structure
        source=self.root/'confidence.csv'
        source.write_text('Name,Protocol Offset,Area,Datatype,Access,Scale\nTOTALIZER,0,Input Registers,uint16,read-only,1\n')
        for index,confidence in enumerate(('absent',None,0,1,0.5)):
            with self.subTest(valid=confidence):
                request=self.request('curated');request['source']={'path':str(source),'format':'csv'}
                entry=request['selection_template']['included'][0]
                if confidence!='absent':entry['confidence']=confidence
                validate_selection_template_structure(request['selection_template'])
                validate_selection_entry_structure(entry,'included',0,selector='exact_name')
                result=compile_user_map(request,self.root/f'confidence-valid-{index}')
                self.assertEqual('offline-complete',result['state'])
        for index,confidence in enumerate((True,False,'0.5',-0.1,1.1,float('nan'),float('inf'),-float('inf'))):
            with self.subTest(invalid=confidence):
                request=self.request('curated');request['source']={'path':str(source),'format':'csv'}
                entry=request['selection_template']['included'][0];entry['confidence']=confidence
                with self.assertRaises(UserMapError):
                    validate_selection_entry_structure(entry,'included',0,selector='exact_name')
                with self.assertRaises(CompilerError):
                    compile_user_map(request,self.root/f'confidence-invalid-{index}')
                self.assertFalse((self.root/f'confidence-invalid-{index}'/'case.json').exists())

    def test_source_correction_is_a_new_case_and_unsafe_input_remains_rejected(self):
        request=self.request('curated');case=self.root/'original'
        compile_user_map(request,case)
        original=(case/'case.json').read_bytes();checkpoint=json.loads(original)
        with self.assertRaises(CompilerError):
            compile_user_map(None,case,resume={'schema_version':'modbus-compile-resume/v1',
                'case_id':checkpoint['case_id'],'case_hash':stable_input_hash(checkpoint),'action':'provide-corrected-source'})
        self.assertEqual(original,(case/'case.json').read_bytes())
        corrected=self.root/'corrected.csv'
        corrected.write_text('Name,Protocol Offset,Area,Datatype,Access,Scale\nTOTALIZER,0,Input Registers,uint16,read-only,1\n')
        next_request=copy.deepcopy(request);next_request['source']={'path':str(corrected),'format':'csv'}
        next_request['selection_template']['included'][0]['evidence_refs']=['csv:row:2']
        result=compile_user_map(next_request,self.root/'corrected-case')
        self.assertEqual('offline-complete',result['state'])
        self.assertEqual(original,(case/'case.json').read_bytes())
        unsafe=copy.deepcopy(request);unsafe['selection_template']['included'][0]['function_code']=16
        with self.assertRaises(CompilerError):compile_user_map(unsafe,self.root/'unsafe')
        self.assertFalse((self.root/'unsafe').exists())

    def test_nonempty_source_still_requires_exact_names_even_when_fields_are_held(self):
        source=self.root/'held.csv'
        source.write_text('Name,Protocol Offset,Area,Datatype\nTOTALIZER,0,Unknown Area,uint16\n')
        request=self.request('curated');request['source']={'path':str(source),'format':'csv'}
        result=compile_user_map(request,self.root/'valid-name')
        self.assertEqual('partial',result['state'])
        normalized=json.loads((self.root/'valid-name/control/request.json').read_text())
        self.assertNotIn('selection_template',normalized)
        request['selection_template']['included'][0]['exact_name']='NOT PRESENT'
        with self.assertRaisesRegex(CompilerError,'must match exactly one'):
            compile_user_map(request,self.root/'wrong-name')

    def test_zero_rows_without_blocking_source_hold_do_not_bypass_matching(self):
        oem=build_oem_map([],source_hash='a'*64)
        with patch('modbus_skills.compiler.compile_source_descriptor',return_value=(oem,{'format':'pdf'})):
            with self.assertRaisesRegex(CompilerError,'must match exactly one'):
                compile_user_map(self.request('curated'),self.root/'no-source-hold')

if __name__=='__main__':unittest.main()
