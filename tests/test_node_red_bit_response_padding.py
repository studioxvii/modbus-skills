"""Execute generated gates with the installed client's observed packed-bit shape."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'plugins/modbus-skills/runtime'))
from modbus_skills.exporters import canonical_map_hash
from modbus_skills.node_red import export_node_red
from modbus_skills.read_plan import compile_read_plan

JS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const nodes = input.flow;
const state = new Map();
const flow = {get: k => state.get(k), set: (k,v) => state.set(k,v)};
const call = (name,msg) => Function('msg','flow','env',nodes.find(n => n.name === name).func)(msg,flow,{get:()=>null});
const request = {...nodes.find(n=>n.modbusSkillsBlocks).modbusSkillsBlocks[0], attempt:0, started_at_ms:Date.now()};
const source = input.source === null ? null : input.source ? {
  data: input.source.data,
  buffer: input.source.badBuffer ? input.source.bytes : Buffer.from(input.source.bytes)
} : undefined;
const msg = {modbusSkillsRequest:request,payload:input.objectPayload ? {data:input.values,...(source || {})} : input.values};
if (!input.objectPayload && input.source !== undefined) msg.responseBuffer = source;
if (input.explicitFailure) msg.error = {message:'synthetic communication failure'};
const originalPayload = JSON.stringify(msg.payload);
const originalSource = JSON.stringify(msg.responseBuffer);
state.set('modbusSkillsRunId','synthetic-padding');
state.set('modbusSkillsActiveBlockId', input.stale ? 'different-block' : request.block_id);
const gate = call('04 Validate response',msg);
let terminal = null, continuation = null;
if(gate[0]) terminal = call('06 Terminal gate',call('05 Decode points',gate[0]));
else terminal = call('06 Terminal gate',gate[1]);
if(terminal[0]) continuation = call('07 Build capture/v1',terminal[0])[1];
console.log(JSON.stringify({accepted:Boolean(gate[0]),samples:state.get('modbusSkillsCapture')||[],
  continuation,raw:msg.modbusSkillsRawValues,bytes:msg.modbusSkillsRawBytes,
  inputValues:input.values,originalPayload,sourceUnchanged:JSON.stringify(msg.responseBuffer)===originalSource}));
"""


class NodeRedBitResponsePaddingTests(unittest.TestCase):
    def run_case(self, quantity, values, *, fc=1, datatype='bool', mode='final', source=None, include_source=False, **options):
        if not shutil.which('node'): self.skipTest('Node.js required')
        area = {1:'coil',2:'discrete-input',3:'holding-register',4:'input-register'}[fc]
        points = [{'logical_point_id':f'bit-{i}','name':f'Bit {i}','route_id':'synthetic-padding','unit_id':7,'area':area,'function_code':fc,'protocol_offset':33+i,'word_span':1,'datatype':datatype,'normalization_status':'confirmed','access':'read-only','scale':1,'engineering_offset':0} for i in range(quantity)]
        canonical = {'schema_version':'modbus-map/v1','points':points}
        plan = compile_read_plan(points).to_dict(); plan['input_hashes'] = {'canonical_map':canonical_map_hash(canonical)}
        result = export_node_red(canonical,plan,mode=mode)
        self.assertEqual('generated',result.status)
        flow = json.loads(next(a.as_text() for a in result.artifacts if a.path.endswith('flow.json')))
        payload = {'flow':flow,'values':values,**options}
        if include_source: payload['source'] = source
        run = subprocess.run(['node','-e',JS],input=json.dumps(payload),text=True,capture_output=True,timeout=5,check=True)
        observed = json.loads(run.stdout)
        self.assertTrue(observed['sourceUnchanged'])
        self.assertEqual(values,observed['inputValues'])
        return observed

    @staticmethod
    def shape(quantity):
        bits = [i%3 != 1 for i in range(quantity)]
        padded = bits + [False]*((-quantity)%8)
        packed = [sum(int(v)<<j for j,v in enumerate(padded[i:i+8])) for i in range(0,len(padded),8)]
        return bits,padded,packed

    def test_native_padding_and_exact_lengths_for_both_bit_functions(self):
        for fc in (1,2):
            for quantity in (1,7,8,9,15,16):
                bits,padded,packed = self.shape(quantity)
                for values in (bits,padded,[int(v) for v in padded]):
                    for buffer in (False,True):
                        with self.subTest(fc=fc,quantity=quantity,length=len(values),buffer=buffer):
                            observed = self.run_case(quantity,values,fc=fc,include_source=buffer,source={'data':values,'bytes':packed})
                            self.assertTrue(observed['accepted'])
                            self.assertEqual(values,observed['raw'])
                            self.assertEqual(quantity,len(observed['samples']))
                            for i,sample in enumerate(observed['samples']):
                                self.assertIs(sample['derived_values']['engineering_value'],bits[i])
                                self.assertEqual([values[i]],sample['raw_words'])
                                self.assertEqual(values,sample['raw_response'])
                                if buffer: self.assertEqual(packed,sample['raw_response_bytes'])
                            self.assertFalse(observed['continuation']['modbusSkillsRetry'])

    def test_object_payload_native_buffer_and_raw_probe(self):
        bits,padded,packed = self.shape(9)
        observed = self.run_case(9,padded,mode='probe',objectPayload=True,include_source=True,source={'data':padded,'bytes':packed})
        self.assertTrue(observed['accepted'])
        self.assertFalse(observed['continuation']['modbusSkillsRetry'])
        for sample in observed['samples']:
            self.assertEqual('raw',sample['derived_values']['decode_status'])
            self.assertIsNone(sample['derived_values']['engineering_value'])
            self.assertEqual(packed,sample['raw_response_bytes'])

    def test_rejects_short_arbitrary_extra_nonzero_padding_and_nonbits(self):
        bits,padded,packed = self.shape(9)
        variants = [None, bits[:-1], padded[:-1], padded+[False], padded+[False]*8, bits+[True], bits+[False]*6+[True], [2]+padded[1:], ['0']+padded[1:], [None]+padded[1:]]
        for fc in (1,2):
            for values in variants:
                with self.subTest(fc=fc,values=values):
                    observed = self.run_case(9,values,fc=fc,mode='probe')
                    self.assertFalse(observed['accepted'])
                    self.assertFalse(observed['continuation']['modbusSkillsRetry'])
                    for sample in observed['samples']:
                        self.assertFalse(sample['success'])
                        self.assertNotIn('derived_values',sample)

    def test_mismatching_missing_malformed_native_buffer_remains_error(self):
        bits,padded,packed = self.shape(9)
        sources = [None, {'data':padded,'bytes':packed[:1]}, {'data':padded,'bytes':packed+[0]}, {'data':padded,'bytes':[packed[0]^1,packed[1]]}, {'data':padded,'bytes':[packed[0],packed[1]|128]}, {'data':padded[:-1],'bytes':packed}, {'data':[not padded[0]]+padded[1:],'bytes':packed}, {'data':padded,'bytes':packed,'badBuffer':True}]
        for source in sources:
            with self.subTest(source=source):
                observed = self.run_case(9,padded,mode='probe',include_source=True,source=source)
                self.assertFalse(observed['accepted'])
                self.assertFalse(observed['continuation']['modbusSkillsRetry'])
                self.assertTrue(all('derived_values' not in s for s in observed['samples']))

    def test_register_responses_are_never_trimmed(self):
        for fc in (3,4):
            observed = self.run_case(1,[123,0,0,0,0,0,0,0],fc=fc,datatype='uint16',mode='probe')
            self.assertFalse(observed['accepted'])
            self.assertEqual([123,0,0,0,0,0,0,0],observed['raw'])

    def test_explicit_failure_and_stale_identity_cannot_succeed(self):
        bits,padded,packed = self.shape(9)
        observed = self.run_case(9,padded,mode='probe',explicitFailure=True)
        self.assertFalse(observed['accepted'])
        self.assertTrue(all(not s['success'] for s in observed['samples']))
        observed = self.run_case(9,padded,stale=True)
        self.assertTrue(observed['accepted'])
        self.assertEqual([],observed['samples'])
        self.assertIsNone(observed['continuation'])


if __name__ == '__main__': unittest.main()
