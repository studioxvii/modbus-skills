"""Explicit protocol sections are source scope, not a numeric blacklist."""
from pathlib import Path
import re
import sys
import tempfile
import unittest
from xml.etree.ElementTree import Element,SubElement,tostring

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/modbus-skills/runtime'))
from modbus_skills import pdf_extraction as pdf
from modbus_skills.pdf_table_extraction import _extract_pdf_table_rows_in_process, extract_pdf_table_evidence

DNP='Appendix B: DNP3 Points List'
MOD='Modbus Register Map'
HEADER=f"{'Address':12}{'Name':28}Type"
def row(address,name):return f'{address:<12}{name:<28}uint16'
TABLE=HEADER+'\n'+row('17','Remote state')
GOOD=HEADER+'\n'+row('25','DNP3 Link Status')
CODE='pdf-other-protocol-section'

def bbox(text):
    root=Element('doc')
    for content in text.split('\f'):
        page=SubElement(root,'page')
        for i,line in enumerate(content.splitlines()):
            for t in re.finditer(r'\S+',line):
                node=SubElement(page,'word',{'xMin':str(t.start()*5),'xMax':str(t.end()*5),'yMin':str(i*15),'yMax':str(i*15+10)})
                node.text=t.group()
    return tostring(root,encoding='unicode')

def drawn_pdf(sections):
    commands=['0.5 w']
    for index,(heading,address,name) in enumerate(sections):
        top=450-index*150
        commands.append(f'BT /F1 11 Tf 20 {top+20} Td ({heading}) Tj ET')
        cuts=[20,110,310,410]
        for x in cuts:commands.append(f'{x} {top-64} m {x} {top} l S')
        for y in (top,top-32,top-64):commands.append(f'20 {y} m 410 {y} l S')
        for r,cells in enumerate((['Address','Name','Type'],[address,name,'uint16'])):
            for c,v in enumerate(cells):commands.append(f'BT /F1 9 Tf {cuts[c]+4} {top-20-r*32} Td ({v}) Tj ET')
    stream='\n'.join(commands).encode()
    objs=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 450 520] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=bytearray(b'%PDF-1.4\n');offsets=[0]
    for i,obj in enumerate(objs,1):offsets.append(len(data));data.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref=len(data);data.extend(f'xref\n0 {len(objs)+1}\n0000000000 65535 f \n'.encode())
    for n in offsets[1:]:data.extend(f'{n:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(data)

class ProtocolSectionTests(unittest.TestCase):
    def parse_both(self,text):
        rows,rejected=pdf.parse_layout_rows(text,first_page=4)
        other=[];coords=pdf.parse_bbox_rows(bbox(text),first_page=4,rejected=other)
        return ((rows,rejected),(coords,other))

    def test_explicit_section_excludes_generic_address_table_in_both_readers(self):
        for rows,rejected in self.parse_both(DNP+'\n'+TABLE):
            self.assertEqual([],rows)
            excluded=[r for r in rejected if r.get('code')==CODE]
            self.assertTrue(excluded)
            self.assertTrue(any('17' in r['_source']['excerpt'] for r in excluded))
            self.assertEqual(4,excluded[-1]['_source']['context_refs'][0]['page'])

    def test_later_modbus_heading_and_named_dnp3_point_survive(self):
        for rows,_ in self.parse_both(DNP+'\n'+TABLE+'\n'+MOD+'\n'+GOOD):
            self.assertEqual(['25'],[r['source_register'] for r in rows])
            self.assertEqual('DNP3 Link Status',rows[0]['name'])

    def test_no_cross_page_scope_and_repeated_heading_is_rechecked(self):
        for rows,_ in self.parse_both(DNP+'\n'+TABLE+'\f'+GOOD):
            self.assertEqual(['25'],[r['source_register'] for r in rows])
        for rows,_ in self.parse_both(DNP+'\n'+TABLE+'\f'+DNP+'\n'+TABLE):self.assertEqual([],rows)

    def test_incidental_and_ambiguous_titles_are_not_exclusions(self):
        for heading in ('DNP3 status is available over Modbus.','Modbus and DNP3 Points List','Appendix B: Modbus / DNP3 Points List','DNP3 Link Status'):
            for rows,_ in self.parse_both(heading+'\n'+GOOD):
                self.assertEqual(['25'],[r['source_register'] for r in rows])

    def test_qualified_appendix_heading_is_not_vendor_specific(self):
        for heading in ('Appendix C: Plant-Level DNP3 Points List','APPENDIX B: SITE DNP3 POINTS LIST - EXAMPLE PROJECTS'):
            for rows,_ in self.parse_both(heading+'\n'+TABLE):self.assertEqual([],rows)

    def test_discovery_excludes_section_and_layout_retains_unselected_evidence(self):
        self.assertEqual([],pdf.discover_register_pages(DNP+'\n'+TABLE))
        self.assertEqual([2],pdf.discover_register_pages(DNP+'\n'+TABLE+'\f'+GOOD))
        rows,rejected=pdf.parse_layout_rows(DNP+'\n'+TABLE,pages=set())
        self.assertEqual([],rows);self.assertTrue(any(r.get('code')==CODE for r in rejected))

    def test_legitimate_headerless_modbus_point_is_unchanged(self):
        text='40056  uint16  DNP3 Link Status'
        before=pdf.parse_layout_rows(text)[0]
        self.assertEqual(1,len(before))
        self.assertEqual('40056',pdf.parse_layout_rows(DNP+'\n'+TABLE+'\n'+MOD+'\n'+text)[0][0]['source_register'])

    def test_grid_filters_actual_vertical_scope_and_retains_source_rows(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'public.pdf';p.write_bytes(drawn_pdf([(DNP,'17','Remote state'),(MOD,'25','DNP3 Link Status')]))
            e=_extract_pdf_table_rows_in_process(p)
        self.assertEqual(['25'],[r['source_register'] for r in e['records']])
        excluded=[r for r in e['quarantined_records'] if r.get('code')==CODE]
        self.assertEqual(['17'],[r['source_register'] for r in excluded])
        self.assertEqual('p1:t0:r1',excluded[0]['_source']['region'])
        self.assertTrue(excluded[0]['_source']['context_refs'])

    def test_grid_dnp3_word_and_dual_protocol_title_are_not_filter(self):
        for heading in (MOD,'Modbus and DNP3 Points List'):
            with tempfile.TemporaryDirectory() as td:
                p=Path(td)/'public.pdf';p.write_bytes(drawn_pdf([(heading,'25','DNP3 Link Status')]))
                e=_extract_pdf_table_rows_in_process(p)
            self.assertEqual(['25'],[r['source_register'] for r in e['records']])

    def test_isolated_grid_worker_import_and_protocol_scope(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'public.pdf';p.write_bytes(drawn_pdf([(DNP,'17','Remote state'),(MOD,'25','DNP3 Link Status')]))
            e=extract_pdf_table_evidence(p)
        self.assertEqual(['25'],[r['source_register'] for r in e['records']])
        self.assertEqual(['17'],[r['source_register'] for r in e['quarantined_records'] if r.get('code')==CODE])

    def test_envelope_keeps_exclusion_as_non_executable_source_accounting(self):
        excluded={'code':CODE,'source_register':'17','_source':{'page':1,'row':1,'region':'p1:t0:r1','excerpt':'17 Remote state'}}
        e=pdf._envelope(Path('public.pdf'),b'synthetic',[],[],[],[],None,quarantined=[excluded])
        self.assertEqual([],e['records']);self.assertIn(excluded,e['rejected_rows'])

    def test_other_protocol_same_address_does_not_quarantine_later_modbus(self):
        good=pdf.parse_layout_rows(GOOD)[0][0]
        excluded={**good,'code':CODE,'protocol':'DNP3','_source':{**good['_source'],'region':'p1:t0:r1'}}
        rows,held,_=pdf._reconcile([], [good], quarantined_records=[excluded])
        self.assertEqual([good],rows)
        self.assertIn(excluded,held)

if __name__=='__main__':unittest.main()
