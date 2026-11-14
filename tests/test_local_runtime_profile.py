import importlib.util
from pathlib import Path
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

path=Path(__file__).resolve().parents[1]/'src/gamma/llm/local_runtime.py'
spec=importlib.util.spec_from_file_location('private_runtime_under_test',path)
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)

class RuntimeProfileContract(unittest.TestCase):
 def test_defaults_and_restoration(self):
  self.assertEqual(module.current_local_runtime_profile(),{})
  with module.local_runtime_profile(primary_keep_alive='5m',review_endpoint='http://127.0.0.1:11435/',review_keep_alive=300,review_num_gpu=0):
   self.assertEqual(module.current_local_runtime_profile(),{'primary_keep_alive':'5m','review_endpoint':'http://127.0.0.1:11435','review_keep_alive':300,'review_num_gpu':0})
   with module.local_runtime_profile(primary_keep_alive='30m'):
    self.assertEqual(module.current_local_runtime_profile(),{'primary_keep_alive':'30m'})
   self.assertEqual(module.current_local_runtime_profile()['primary_keep_alive'],'5m')
  self.assertEqual(module.current_local_runtime_profile(),{})
 def test_exception_and_caller_copy(self):
  with self.assertRaises(RuntimeError):
   with module.local_runtime_profile(primary_keep_alive=300):
    copy=module.current_local_runtime_profile();copy['primary_keep_alive']=-1
    self.assertEqual(module.current_local_runtime_profile()['primary_keep_alive'],300)
    raise RuntimeError('stop')
  self.assertEqual(module.current_local_runtime_profile(),{})
 def test_thread_scope_isolation(self):
  with module.local_runtime_profile(primary_keep_alive='5m'):
   with ThreadPoolExecutor(max_workers=1) as pool:self.assertEqual(pool.submit(module.current_local_runtime_profile).result(),{})
 def test_endpoint_rejections(self):
  for value in ['http://localhost:11435','http://127.0.0.1:11434','https://127.0.0.1:11435','http://127.0.0.1:11435/x','http://127.0.0.1:11435?x=1','http://127.0.0.1:11435#x','http://x@127.0.0.1:11435','http://127.0.0.1:11435\n','http://127.0.0.1:11435/?','http://127.0.0.1:11435/#','http://[::1]:11435/x',True,1]:
   with self.subTest(value=value),self.assertRaises(ValueError):module.validate_review_endpoint(value)
  self.assertEqual(module.validate_review_endpoint('http://[::1]:11435/'),'http://[::1]:11435')
 def test_retention_and_cpu_validation(self):
  for value in [True,-1,1801,1.5,'-1','0s','00','0m','31m','1801s','1h','5m\n',' 5m',None]:
   with self.subTest(value=value),self.assertRaises(ValueError):module.validate_keep_alive(value)
  for value in [0,300,1800,'0','5m','30m','1s','1800s']:self.assertEqual(module.validate_keep_alive(value),value)
  for value in [True,1,-1,0.0,None]:
   with self.subTest(value=value),self.assertRaises(ValueError):module.validate_review_num_gpu(value)
  self.assertEqual(module.validate_review_num_gpu(0),0)
 def test_profile_invalid_fails_without_state_mutation(self):
  with module.local_runtime_profile(primary_keep_alive='5m') as yielded:
   yielded['review_endpoint']='http://evil.example:11435'
   self.assertNotIn('review_endpoint',module.current_local_runtime_profile())
   with self.assertRaises(ValueError):
    with module.local_runtime_profile(review_endpoint='http://evil.example:11435'):pass
   self.assertEqual(module.current_local_runtime_profile(),{'primary_keep_alive':'5m'})

class ExactTypes(unittest.TestCase):
 def test_subclasses_rejected(self):
  class Integer(int):pass
  class Text(str):pass
  for f,value in [(module.validate_keep_alive,Integer(0)),(module.validate_keep_alive,Text('5m')),(module.validate_review_num_gpu,Integer(0)),(module.validate_review_endpoint,Text('http://127.0.0.1:11435'))]:
   with self.subTest(value=value),self.assertRaises(ValueError):f(value)

if __name__=='__main__':unittest.main()
