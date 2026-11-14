import importlib.util, pathlib, unittest
p=pathlib.Path(__file__).resolve().parents[1]/'src/gamma/llm/local_metrics.py'
spec=importlib.util.spec_from_file_location('local_metrics',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Metrics(unittest.TestCase):
 def test_durations_and_counts(self):
  d=m.local_generation_metrics({'total_duration':1200000000,'load_duration':100000000,'prompt_eval_duration':200000000,'eval_duration':800000000,'prompt_eval_count':250,'eval_count':40,'response':'PRIVATE','context':[1,2]})
  self.assertEqual(d,{'total_ms':1200.0,'load_ms':100.0,'prompt_eval_ms':200.0,'eval_ms':800.0,'prompt_eval_count':250,'eval_count':40,'tokens_per_second':50.0})
 def test_missing_and_non_dict(self):
  for value in (None,False,[],42,'secret'):self.assertEqual(m.local_generation_metrics(value),{})
  self.assertEqual(m.local_generation_metrics({}),{})
 def test_invalid_numbers_and_no_secret_fields(self):
  for invalid in (True,False,-1,1.5,float('inf'),float('nan'),'123',10**30):
   self.assertEqual(m.local_generation_metrics({'total_duration':invalid,'eval_duration':invalid,'eval_count':invalid,'load_duration':invalid,'prompt_eval_count':invalid}),{})
  self.assertEqual(m.local_generation_metrics({'response':'SECRET','model':'PRIVATE','secret':'TOKEN','error':{'x':1}}),{})
 def test_zero_counts_and_duration(self):
  self.assertEqual(m.local_generation_metrics({'eval_count':0,'eval_duration':0}),{'eval_count':0,'eval_ms':0.0})
  self.assertEqual(m.local_generation_metrics({'eval_count':8}),{'eval_count':8})
 def test_no_input_mutation(self):
  v={'eval_count':12,'eval_duration':300000000};before=dict(v);m.local_generation_metrics(v);self.assertEqual(v,before)
if __name__=='__main__':unittest.main()
