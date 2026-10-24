import unittest
from types import SimpleNamespace as T
from gamma.coding.practice_priority import prioritize
class Priority(unittest.TestCase):
 def test_weak_then_unknown_then_proven(self):
  tasks=[T(id='proven',skill='a'),T(id='unknown',skill='b'),T(id='weak',skill='c')]
  skills={'a':{'independently_evaluated':2},'c':{'failed':1,'repairs':3}}
  self.assertEqual([t.id for t in prioritize(tasks,skills)],['weak','unknown','proven'])
  self.assertEqual([t.id for t in tasks],['proven','unknown','weak'])
 def test_preserve_all_and_stable_ties(self):
  tasks=[T(id='one',skill='a'),T(id='two',skill='a')]
  self.assertEqual(prioritize(tasks,{}),tasks)
 def test_malformed_counts_not_authority(self):
  tasks=[T(id='one',skill='a'),T(id='two',skill='b')]
  for bad in [None,{},'broken',{'a':None},{'a':{'failed':True,'repairs':'9'}}]:
   self.assertEqual(prioritize(tasks,bad),tasks)
if __name__=='__main__':unittest.main()