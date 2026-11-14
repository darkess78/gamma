import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).parents[1] / 'src/gamma/coordination/receipts.py'
spec = importlib.util.spec_from_file_location('receipts', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Receipts(unittest.TestCase):
    def test_completed_requires_source_bound_checks(self):
        base = {'id':'work-abc','goal':'inventory','kind':'practical','status':'completed',
                'mode':'build','source_sha256':'a'*64,'checks_source_sha256':'a'*64,
                'checks':[{'name':'actual_cli','passed':True,'exit_code':0}], 'attempt_count':2}
        text=module.work_status_text(base)
        self.assertIn('work-abc',text)
        self.assertIn('candidate',text.lower())
        self.assertIn('2',text)
        self.assertIn('actual_cli',text)
        self.assertIn('a'*64,text)
        self.assertIn('activation',text.lower())
        base['checks_source_sha256']='b'*64
        self.assertIn('not verified',module.work_status_text(base).lower())
    def test_blocker_requests_specific_help(self):
        text=module.work_status_text({'id':'work-b','goal':'tracker','status':'blocked',
              'kind':'practical','reason':'missing_capability','help_needed':'Provide the selected input file.',
              'attempt_count':1})
        self.assertIn('missing_capability',text)
        self.assertIn('Provide the selected input file.',text)
        self.assertIn('1',text)
    def test_research_has_no_build_claim(self):
        text=module.work_status_text({'id':'work-c','goal':'compare formats','status':'completed',
              'kind':'practical','mode':'research','source_sha256':'c'*64,'research_ref':'d'*64,
              'attempt_count':0})
        self.assertIn('research',text.lower())
        self.assertIn('no code',text.lower())

if __name__=='__main__':unittest.main()
