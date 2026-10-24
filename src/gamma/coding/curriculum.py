"""Fictional offline exercises. These never connect to Discord."""
from .practice import PracticeCheck, PracticeTask


def discord_foundations():
    checks = (PracticeCheck("regression", "regression", "test_regression"),
              PracticeCheck("feature", "feature", "feature_smoke"))
    return [
        PracticeTask(
            id="retry_budget", skill="bounded_recovery",
            instructions="Implement should_retry(error, attempt, maximum_attempts). Return a bool. Retry only error strings 'busy' or 'rate_limit' with exact integer attempt >= 1 and exact integer maximum_attempts >= 1, and attempt < maximum_attempts. Everything else returns False without raising, including bool counts, malformed fields, quota/auth errors and timeout (uncertain upstream work must never be replayed).",
            files={
                "solution.py": "def should_retry(error, attempt, maximum_attempts):\n    return True\n",
                "test_regression.py": "import unittest\nfrom solution import should_retry\nclass Regression(unittest.TestCase):\n def test_first_attempt(self): self.assertIs(should_retry('busy', 1, 5), True)\n def test_exhausted(self): self.assertIs(should_retry('busy', 5, 5), False)\n",
                "feature_smoke.py": "from solution import should_retry\nfor error in ['timeout','auth','quota',None,[],{}]:\n assert should_retry(error,1,5) is False\nfor attempt,maximum in [(True,5),(1,True),(0,5),(-1,5),(1,0),('1',5)]:\n assert should_retry('rate_limit',attempt,maximum) is False\nassert should_retry('rate_limit',4,5) is True\n",
            }, editable=("solution.py",), checks=checks,
        ),
        PracticeTask(
            id="message_chunks", skill="preserve_existing_behavior",
            instructions="Implement chunks(text, limit=2000). Return lossless chunks no longer than limit. Empty text returns []. Reject nonpositive limits with ValueError. Preserve Unicode and short-message behavior.",
            files={
                "solution.py": "def chunks(text, limit=2000):\n    return [text[:limit]] if text else []\n",
                "test_regression.py": "import unittest\nfrom solution import chunks\nclass Regression(unittest.TestCase):\n def test_short(self): self.assertEqual(chunks('hello'), ['hello'])\n def test_empty(self): self.assertEqual(chunks(''), [])\n def test_unicode(self): self.assertEqual(''.join(chunks('こんにちは🙂', 2)), 'こんにちは🙂')\n",
                "feature_smoke.py": "from solution import chunks\ntext = 'a🙂b' * 1400\nparts = chunks(text, 1000)\nassert ''.join(parts) == text\nassert len(parts) > 1 and all(0 < len(p) <= 1000 for p in parts)\nfor limit in (0, -1):\n try: chunks('hello', limit)\n except ValueError: pass\n else: raise AssertionError('nonpositive limit accepted')\n",
            }, editable=("solution.py",), checks=checks,
        ),
        PracticeTask(
            id="owner_authority", skill="authorization_boundaries",
            instructions="Implement can_command(user_id, owner_id, verified). True only when verified is exactly True and user_id and owner_id are nonempty strings that match exactly. Names and truthy values must not grant authority.",
            files={
                "solution.py": "def can_command(user_id, owner_id, verified):\n    return bool(verified)\n",
                "test_regression.py": "import unittest\nfrom solution import can_command\nclass Regression(unittest.TestCase):\n def test_owner(self): self.assertTrue(can_command('owner', 'owner', True))\n def test_unverified(self): self.assertFalse(can_command('owner', 'owner', False))\n",
                "feature_smoke.py": "from solution import can_command\nfor args in [('visitor','owner',True),('Owner','owner',True),('','',True),(None,None,True),('owner','owner','yes'),('owner','owner',1),(1,1,True)]:\n assert can_command(*args) is False, repr(args)\nassert can_command('owner','owner',True) is True\n",
            }, editable=("solution.py",), checks=checks,
        ),
        PracticeTask(
            id="command_dispatch", skill="feature_verification",
            instructions="Implement dispatch(request, owner_id). request is a dict. Only a verified exact owner may execute commands wake and stop. Return {'status':'executed','command':command} for those, {'status':'denied'} for unauthorized callers, {'status':'unsupported'} for an authorized unknown command. Missing/malformed request fields must return denied, never raise. verified must be exactly True; IDs nonempty strings.",
            files={
                "solution.py": "def dispatch(request, owner_id):\n    return {'status': 'executed', 'command': request['command']}\n",
                "test_regression.py": "import unittest\nfrom solution import dispatch\nclass Regression(unittest.TestCase):\n def test_owner(self): self.assertEqual(dispatch({'user_id':'owner','verified':True,'command':'wake'},'owner'),{'status':'executed','command':'wake'})\n",
                "feature_smoke.py": "from solution import dispatch\nfor req in [None, [], {}, {'user_id':'visitor','verified':True,'command':'stop'}, {'user_id':'owner','verified':1,'command':'stop'}, {'user_id':'owner','verified':True}]:\n assert dispatch(req,'owner') == {'status':'denied'}, repr(req)\nassert dispatch({'user_id':'owner','verified':True,'command':'other'},'owner') == {'status':'unsupported'}\nassert dispatch({'user_id':'owner','verified':True,'command':'stop'},'owner') == {'status':'executed','command':'stop'}\n",
            }, editable=("solution.py",), checks=checks,
            depends_on=("message_chunks", "owner_authority"),
        ),
    ]
