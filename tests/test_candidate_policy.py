import copy,unittest
from gamma.coding.candidate_policy import compare
class Gate(unittest.TestCase):
 def reports(self):
  b={'checkpoint':'same','revision':'a'*40,'tasks':[{'id':str(i),'fixture_sha256':'f'*64,'sandboxed':True,'passed':i!=7,'infrastructure_error':False} for i in range(8)]}
  c=copy.deepcopy(b);c['adapter_sha256']='b'*64;c['tasks'][7]['passed']=True
  return b,c
 def test_gain(self):
  b,c=self.reports();self.assertIs(compare(b,c)['passed'],True)
 def test_reject(self):
  for mutation in ['no_gain','regression','task','fixture','base','short','truthy','infra','adapter']:
   b,c=self.reports()
   if mutation=='no_gain':c['tasks'][7]['passed']=False
   if mutation=='regression':c['tasks'][0]['passed']=False
   if mutation=='task':c['tasks'][0]['id']='other'
   if mutation=='fixture':c['tasks'][0]['fixture_sha256']='e'*64
   if mutation=='base':c['revision']='c'*40
   if mutation=='short':b['tasks']=b['tasks'][:2];c['tasks']=c['tasks'][:2]
   if mutation=='truthy':c['tasks'][0]['sandboxed']=1
   if mutation=='infra':b['tasks'][0]['infrastructure_error']=True
   if mutation=='adapter':c.pop('adapter_sha256')
   self.assertIs(compare(b,c)['passed'],False,mutation)
 def test_malformed(self):
  for bad in [None,{},[],{'tasks':None}]:self.assertIs(compare(bad,bad)['passed'],False)
 def test_both_reports_fail_closed(self):
  for field,value in [('passed',1),('sandboxed',False),('infrastructure_error',None),('fixture_sha256','f'*64+'\n')]:
   for side in ['baseline','candidate']:
    b,c=self.reports();report=b if side=='baseline' else c;report['tasks'][0][field]=value
    self.assertIs(compare(b,c)['passed'],False)
  b,c=self.reports();c['tasks'][1]['id']=c['tasks'][0]['id']
  self.assertIs(compare(b,c)['passed'],False)
 def test_one_gain_from_zero_is_not_reliable_coding(self):
  b,c=self.reports()
  for t in b['tasks']:t['passed']=False
  for t in c['tasks']:t['passed']=False
  c['tasks'][0]['passed']=True
  self.assertIs(compare(b,c)['passed'],False)
if __name__=='__main__':unittest.main()
