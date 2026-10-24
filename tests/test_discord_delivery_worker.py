import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('delivery', Path(__file__).parents[1] / 'src/gamma/integrations/discord/delivery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def result(text='Hello'):
    return {'decision': {'decision':'reply'}, 'assistant_response': {'display_text':text, 'spoken_text':'private draft', 'response_action':'reply','delivery_mode':'text_only'}, 'safety_decision': {'action':'allow','blocked':False,'playback_approved':True}}


class DeliveryTest(unittest.TestCase):
    def test_display_only(self): self.assertEqual(module.approved_reply(result()), 'Hello')
    def test_silent_and_deferred(self):
        for action in ['stay_silent','defer','tool_first']:
            item = result(); item['assistant_response']['response_action'] = action
            self.assertIsNone(module.approved_reply(item))
        for decision in ['ignore','defer','tool_action']:
            item = result(); item['decision']['decision'] = decision
            self.assertIsNone(module.approved_reply(item))
    def test_fail_closed_safety(self):
        for safety in [{},{'action':'allow'},{'action':'allow','blocked':False,'playback_approved':1},{'action':'hold','blocked':False,'playback_approved':True},{'action':'allow','blocked':True,'playback_approved':True},{'action':'allow','blocked':False,'playback_approved':True,'review_pending':True}]:
            item=result(); item['safety_decision']=safety
            self.assertIsNone(module.approved_reply(item))
    def test_malformed_or_missing_text(self):
        for item in [None, [], {}, {'assistant_response':None}, result(''), result('   '), result(None), result(42)]:
            self.assertIsNone(module.approved_reply(item))
        item=result(); del item['assistant_response']['display_text']
        self.assertIsNone(module.approved_reply(item))
    def test_preserve_text_and_acknowledgement(self):
        item=result('  fine\n'); item['decision']['decision']='acknowledge'; item['assistant_response']['response_action']='acknowledge'
        self.assertEqual(module.approved_reply(item),'  fine\n')
    def test_chunks_preserve_unicode_under_discord_limit(self):
        for text in ['a'*4501, '😀'*2001, 'a😀\n'*1600, 'hello']:
            chunks=module.message_chunks(text)
            self.assertEqual(''.join(chunks),text)
            self.assertTrue(all(0 < len(s.encode('utf-16-le'))//2 <= 2000 for s in chunks))
    def test_empty_and_invalid_chunk_inputs(self):
        self.assertEqual(module.message_chunks(''),[])
        for value in [None, 1, []]:
            with self.assertRaises(ValueError): module.message_chunks(value)
        for limit in [0,1,-1,2001,True]:
            with self.assertRaises(ValueError): module.message_chunks('hi',limit=limit)
    def test_size_bound(self):
        with self.assertRaises(ValueError): module.message_chunks('a'*16001)
        self.assertIsNone(module.approved_reply(result('a'*16001)))
        self.assertEqual(''.join(module.message_chunks('a'*16000)),'a'*16000)
        self.assertEqual(''.join(module.message_chunks('😀'*16000)),'😀'*16000)


if __name__=='__main__': unittest.main()
