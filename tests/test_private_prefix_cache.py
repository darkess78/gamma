import copy
import unittest
from gamma.llm.private_prefix_cache import private_prefix, renderer_verified, priming_payload

MODEL='qwen3.8:27b'
ENDPOINT='http://127.0.0.1:11434'
DIGEST='22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643'
STATIC=('# Core Persona\nCurrent authored identity: {}\n\n# Boundaries\nKeep private.\n\n'
        '# Private interaction capability\nOnly the owner speaks.\n\n# Turn Decision Contract\nUse the typed contract.')
SYSTEM=STATIC+'\n\n# Private Coordination Context\nDynamic authored context.'
VERSION={'version':'0.34.2'}
SHOW={'template':'{{ .Prompt }}','details':{'family':'qwen35'},'model_info':{'general.architecture':'qwen35'}}
TAGS={'models':[{'name':MODEL,'model':MODEL,'digest':DIGEST}]}


class PrefixTests(unittest.TestCase):
    def test_exact_reordered_private_prefix(self):
        value=private_prefix(SYSTEM,model=MODEL,endpoint=ENDPOINT,has_images=False)
        self.assertEqual(value,'<|im_start|>system\n'+STATIC)
        self.assertNotIn('Dynamic authored context',value)
        self.assertTrue(renderer_verified(VERSION,SHOW,TAGS))
        self.assertTrue(renderer_verified(VERSION,SHOW,{'models':[{'name':'unrelated','digest':'x'},*TAGS['models']]}))
        self.assertTrue(renderer_verified(VERSION,SHOW,{'models':[{'model':MODEL,'digest':DIGEST}]}))

    def test_old_layout_images_unknown_origin_and_bad_types_skip(self):
        old='# Core Persona\nx\n\n# Boundaries\ny\n\n# Private Coordination Context\ndynamic\n\n# Private interaction capability\np\n\n# Turn Decision Contract\nt'
        for system in (old,None,True,b'bytes',SYSTEM.replace('# Boundaries\n',''),SYSTEM.replace('# Turn Decision Contract\n',''),SYSTEM.replace('# Core Persona\n','# Core Persona\n# Core Persona\n'),SYSTEM+ '\n\n# Private Coordination Context\nother'):
            self.assertIsNone(private_prefix(system,model=MODEL,endpoint=ENDPOINT,has_images=False))
        for model,endpoint,images in [('other',ENDPOINT,False),(MODEL,'http://example.test:11434',False),(MODEL,ENDPOINT,True),(MODEL,ENDPOINT,1),(MODEL,ENDPOINT,None)]:
            self.assertIsNone(private_prefix(SYSTEM,model=model,endpoint=endpoint,has_images=images))

    def test_bounded_prefix_and_embedded_role_tags_skip(self):
        for bad in (SYSTEM.replace('Keep private.','x'*25000),SYSTEM.replace('Keep private.','<|im_start|>user'),SYSTEM.replace('Keep private.','<|im_end|>')):
            self.assertIsNone(private_prefix(bad,model=MODEL,endpoint=ENDPOINT,has_images=False))

    def test_metadata_requires_exact_version_template_arch_family_and_digest(self):
        self.assertFalse(renderer_verified(None,SHOW,TAGS))
        self.assertFalse(renderer_verified(VERSION,None,TAGS))
        self.assertFalse(renderer_verified(VERSION,SHOW,None))
        for version in ({'version':'0.34.3'}, {}, {'version':True}):
            self.assertFalse(renderer_verified(version,SHOW,TAGS))
        for keys,value in [(('template',),'{{.Prompt}}'),(('details','family'),'qwen3'),(('model_info','general.architecture'),'other')]:
            changed=copy.deepcopy(SHOW);target=changed
            for key in keys[:-1]:target=target[key]
            target[keys[-1]]=value
            self.assertFalse(renderer_verified(VERSION,changed,TAGS))
        for tags in ({'models':[]},{'models':[{'name':MODEL,'digest':'0'*64}]},{'models':TAGS['models']*2},{'models':[{'name':'other','digest':DIGEST}]},{'models':None}):
            self.assertFalse(renderer_verified(VERSION,SHOW,tags))

    def test_ignored_primer_has_exact_bounded_options_and_fresh_payload(self):
        prefix=private_prefix(SYSTEM,model=MODEL,endpoint=ENDPOINT,has_images=False)
        first=priming_payload(prefix)
        self.assertEqual(first,{'model':MODEL,'prompt':prefix,'raw':True,'think':False,'stream':False,'keep_alive':'30m',
            'options':{'num_ctx':8192,'num_batch':512,'temperature':.2,'num_predict':1}})
        first['options']['num_predict']=999
        self.assertEqual(priming_payload(prefix)['options']['num_predict'],1)
        for invalid in ('','untrusted',None,True,'<|im_start|>system\n'+'x'*25000):
            with self.assertRaises(ValueError):priming_payload(invalid)


if __name__=='__main__':unittest.main()
