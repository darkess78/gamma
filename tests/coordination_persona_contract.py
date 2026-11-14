"""Standard-library contract for the canonical private persona renderer."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('canonical_persona', Path(__file__).parents[1] / 'src/gamma/persona/coordination.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CanonicalPersonaContract(unittest.TestCase):
    def test_only_current_fields_and_compact_provenance_enter_prompt(self):
        identity = {'id':'self', 'fields':{'name':'Fictional Shana', 'core_personality':'Blunt and caring.'},
                    'baseline_fields':{'name':'OLD_PRIVATE_BASELINE'}, 'revision':4,
                    'field_sources':{'core_personality':'src-' + 'a' * 24}, 'source_kind':'authored_baseline',
                    'unrelated':'UNRELATED_PRIVATE_HISTORY'}
        prompt = module.build_coordination_system_prompt(identity)
        self.assertIn('# Core Persona\n', prompt)
        self.assertIn('Fictional Shana', prompt)
        self.assertIn('Blunt and caring.', prompt)
        self.assertIn('administrative', prompt.lower())
        self.assertIn('4', prompt)
        self.assertNotIn('OLD_PRIVATE_BASELINE', prompt)
        self.assertNotIn('UNRELATED_PRIVATE_HISTORY', prompt)
        self.assertIn('# Boundaries\n', prompt)
        self.assertIn('authority', prompt.lower())
        self.assertIn('unverified', prompt.lower())

    def test_invalid_canonical_identity_fails_closed(self):
        for invalid in (None, {}, {'id':'other','fields':{'name':'Fictional'}}, {'id':'self','fields':{}},
                        {'id':'self','fields':'untrusted text'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                module.build_coordination_system_prompt(invalid)


if __name__ == '__main__':
    unittest.main()
