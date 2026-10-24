"""Protected acceptance contracts for registered repository coding scopes."""
CONTRACT = '''Implement summarize(process, state, now) in bot_status.py. It must be a pure function with no imports or I/O. Return exactly connected:boolean, voice:string, label:string. process and state can be malformed. Running is confirmed only when process is a dict and running is exactly True. If stopped return False, disconnected, Bot stopped. If running, read the timestamp ONLY from the top-level state.updated_at field. Ignore every heartbeat object or field; never read nested heartbeat.updated_at. state.updated_at must be a finite int/float excluding bool, age between 0 and 5 inclusive. Missing, malformed, stale or future heartbeat returns False, unknown, Bot running; status unverified. Fresh heartbeat with connected not exactly True returns False, disconnected, Bot reconnecting. Fresh connected heartbeat returns True and voice state from state.voice.state, only listening/paused/disconnected/error/unavailable accepted, otherwise unknown. Label is Bot connected; voice STATE. Never mutate arguments. Never treat strings or numeric flags as booleans.'''

REGRESSION = '''import unittest
from bot_status import summarize
class Regression(unittest.TestCase):
 def test_stopped(self):
  self.assertEqual(summarize({'running':False},{'connected':True,'updated_at':100},100),{'connected':False,'voice':'disconnected','label':'Bot stopped'})
 def test_live(self):
  self.assertEqual(summarize({'running':True},{'connected':True,'updated_at':100,'voice':{'state':'listening'}},100),{'connected':True,'voice':'listening','label':'Bot connected; voice listening'})
'''
FEATURE = '''from bot_status import summarize
assert summarize({'running':True},{'connected':True,'updated_at':80,'voice':{'state':'listening'}},100)=={'connected':False,'voice':'unknown','label':'Bot running; status unverified'}
assert summarize({'running':True},{'connected':False,'updated_at':100},100)=={'connected':False,'voice':'disconnected','label':'Bot reconnecting'}
assert summarize({'running':True},{'connected':True,'updated_at':100,'voice':{'state':'paused'}},100)['voice']=='paused'
'''

# Not supplied to the learner. Contract and regression tests are supplied.
HOLDOUT = '''import copy, math
from bot_status import summarize
for running in [None,0,1,'true',False,{},[]]:
 assert summarize({'running':running},{'connected':True,'updated_at':100},100)['label']=='Bot stopped'
for process in [None,[],True,'running']:
 assert summarize(process,{},100)['label']=='Bot stopped'
for stamp in [None,True,False,'100',float('nan'),float('inf'),float('-inf'),101,94.999]:
 assert summarize({'running':True},{'connected':True,'updated_at':stamp},100)['label']=='Bot running; status unverified'
for state in [None,[],True,'connected']:
 assert summarize({'running':True},state,100)['voice']=='unknown'
for age in [0,1,5]:
 for voice in ['listening','paused','disconnected','error','unavailable',None,[],{},1,'<script>']:
  state={'updated_at':100-age,'connected':True,'voice':{'state':voice}}
  before=copy.deepcopy(state)
  result=summarize({'running':True},state,100)
  expected=voice if isinstance(voice,str) and voice in ['listening','paused','disconnected','error','unavailable'] else 'unknown'
  assert result=={'connected':True,'voice':expected,'label':'Bot connected; voice '+expected}
  assert state==before
for flag in [1,'true',None,False]:
 assert summarize({'running':True},{'connected':flag,'updated_at':100},100)['connected'] is False
for voice in [None,[],True,'listening']:
 assert summarize({'running':True},{'connected':True,'updated_at':100,'voice':voice},100)['voice']=='unknown'
for alternate in [None,{}, {'updated_at':0}, {'updated_at':1000}]:
 state={'connected':True,'updated_at':100,'heartbeat':alternate,'voice':{'state':'paused'}}
 assert summarize({'running':True},state,100)=={'connected':True,'voice':'paused','label':'Bot connected; voice paused'}
'''
