"""Independent stdlib-only coding-worker contract, run from repository root."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('scope', Path('src/gamma/coordination/discord_scope.py'))
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class ScopeTests(unittest.TestCase):
    def test_ids(self):
        for valid in ['1', '123456789012345678', str(2**64 - 1)]:
            self.assertEqual(scope.discord_id(valid), valid)
        for value in [True, False, 1, None, '', '0', '01', ' 1', '1 ', '+1', '-1', '1.0', '\u0661', str(2**64), '1\n', '1'*21]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                scope.discord_id(value)

    def test_complete_scope(self):
        self.assertEqual(scope.validate_scope('1','2'), {'owner_user_id':'1','dm_channel_id':'2','voice_guild_id':None,'voice_channel_id':None})
        self.assertEqual(scope.validate_scope('1','2','3','4')['voice_channel_id'], '4')
        for pair in [('3', None),(None,'4'),('', ''),('0','4')]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                scope.validate_scope('1','2',*pair)

    def test_exact_admission(self):
        s = scope.validate_scope('1','2','3','4')
        self.assertTrue(scope.admit_event(s,user_id='1',channel_id='2'))
        self.assertTrue(scope.admit_event(s,user_id='1',channel_id='4',guild_id='3',voice=True))
        for changes in [{'user_id':'8'},{'channel_id':'8'},{'guild_id':'3'},{'is_bot':True},{'is_bot':1},{'voice':1},{'user_id':1}]:
            event={'user_id':'1','channel_id':'2',**changes}
            self.assertFalse(scope.admit_event(s,**event))
        for changes in [{'user_id':'8'},{'channel_id':'8'},{'guild_id':None},{'guild_id':'8'},{'is_bot':True}]:
            event={'user_id':'1','channel_id':'4','guild_id':'3','voice':True,**changes}
            self.assertFalse(scope.admit_event(s,**event))
        self.assertFalse(scope.admit_event(scope.validate_scope('1','2'),user_id='1',channel_id='4',guild_id='3',voice=True))
        for malformed in [None, {}, {'owner_user_id':'1','dm_channel_id':'2','voice_guild_id':'3','voice_channel_id':None}]:
            self.assertFalse(scope.admit_event(malformed,user_id='1',channel_id='2'))


if __name__ == '__main__':
    unittest.main()
