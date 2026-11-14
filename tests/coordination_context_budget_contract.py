"""Standard-library checks for optional context compaction."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('context_compactor', Path(__file__).parents[1] / 'src/gamma/coordination/context_budget.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Compaction(unittest.TestCase):
    def test_protected_fields_no_mutation_and_user_text(self):
        edited = 'CURRENT_EDIT' * 10
        value = {'awake':False, 'focus':'focused', 'mood':'neutral', 'authorized_practice':{'exercise_ids':['fixture'], 'expires_at':123},
                 'activities':[{'id':'practice-exact', 'status':'paused', 'desired_state':'pause', 'goal':'fixture', 'requested_goal':'x'*1200}],
                 'recent_exchanges':[{'user':'x'*2000,'assistant':'x'*2000}],
                 'personal':{'identity':{'id':'self','revision':2,'fields':{'name':'Fictional','core_personality':'Blunt.',
                    'description':edited, 'scenario_notes':'x'*5000}, 'field_sources':{'description':'src-exact'}},
                    'recall':[{'id':'mem-exact','source_id':'src-exact','certainty':'reported','text':'x'*2000}], 'people':[]}}
        original = json.dumps(value,sort_keys=True)
        user = 'USER_INPUT_EXACT'
        seen = []
        def render(candidate):
            seen.append(candidate)
            return json.dumps(candidate)
        compacted, report = module.compact_coordination_context(value,user_text=user,render_prompt=render,usable_input_tokens=1500,reserve_tokens=0)
        self.assertEqual(json.dumps(value,sort_keys=True),original)
        self.assertEqual(compacted['authorized_practice'],value['authorized_practice'])
        self.assertEqual(compacted['activities'][0]['id'],'practice-exact')
        self.assertEqual(compacted['activities'][0]['status'],'paused')
        self.assertFalse(compacted['awake'])
        self.assertEqual(compacted['personal']['identity']['fields']['description'],edited)
        self.assertIn('Blunt.',compacted['personal']['identity']['fields']['core_personality'])
        self.assertLessEqual((len(render(compacted))+len(user)+3)//4,1500)
        self.assertTrue(report['compacted'])
        self.assertNotIn('CURRENT_EDIT',json.dumps(report))

    def test_required_edits_cannot_be_silently_truncated(self):
        value={'awake':True,'personal':{'identity':{'fields':{'name':'Fictional','core_personality':'x'*10000},
                                                       'field_sources':{'core_personality':'src-exact'}}}}
        with self.assertRaises(module.PrivateContextBudgetError):
            module.compact_coordination_context(value,user_text='exact',render_prompt=json.dumps,usable_input_tokens=512)


if __name__ == '__main__':
    unittest.main()
