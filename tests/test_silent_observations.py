import time
import math
import unittest
from gamma.brain.observations import ObservationInbox


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.inbox = ObservationInbox(capacity=8, clock=time.monotonic)

    def test_bool_key_rejected(self):
        with self.assertRaises(TypeError):
            self.inbox.offer(key=True, text="x", goal_id=1)

    def test_empty_key(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="", text="x", goal_id=1)

    def test_key_too_long(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="a"*81, text="x", goal_id=1)

    def test_empty_text(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="k", text="", goal_id=1)

    def test_text_too_long(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="k", text="a"*2001, goal_id=1)

    def test_bool_goal_id(self):
        with self.assertRaises(TypeError):
            self.inbox.offer(key="k", text="x", goal_id=True)

    def test_zero_goal_id(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="k", text="x", goal_id=0)

    def test_bool_ttl(self):
        with self.assertRaises(TypeError):
            self.inbox.offer(key="k", text="x", goal_id=1, ttl_seconds=True)

    def test_ttl_nan(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="k", text="x", goal_id=1, ttl_seconds=float('nan'))

    def test_ttl_too_large(self):
        with self.assertRaises(ValueError):
            self.inbox.offer(key="k", text="x", goal_id=1, ttl_seconds=3601)

    def test_capacity_bool(self):
        with self.assertRaises(TypeError):
            ObservationInbox(capacity=True)

    def test_capacity_zero(self):
        with self.assertRaises(ValueError):
            ObservationInbox(capacity=0)

    def test_capacity_257(self):
        with self.assertRaises(ValueError):
            ObservationInbox(capacity=257)


class TestCoalesce(unittest.TestCase):
    def setUp(self):
        self.ck = FakeClock(100.0)
        self.inbox = ObservationInbox(capacity=8, clock=self.ck)

    def test_replacement_latest_text(self):
        self.inbox.offer(key="a", text="first", goal_id=1, ttl_seconds=100)
        self.inbox.offer(key="a", text="second", goal_id=1, ttl_seconds=100)
        items = self.inbox.take(1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['text'], "second")

    def test_coalesced_counter(self):
        self.inbox.offer(key="a", text="v1", goal_id=1)
        self.inbox.offer(key="a", text="v2", goal_id=1)
        s = self.inbox.status()
        self.assertEqual(s['coalesced'], 1)
        self.assertEqual(s['count'], 1)


class TestExpiry(unittest.TestCase):
    def setUp(self):
        self.ck = FakeClock(0.0)
        self.inbox = ObservationInbox(capacity=4, clock=self.ck)

    def test_expired_pruned_on_offers(self):
        self.inbox.offer(key="a", text="x", goal_id=1, ttl_seconds=10)
        self.ck.advance(11)
        self.inbox.offer(key="b", text="y", goal_id=1)
        s = self.inbox.status()
        self.assertEqual(s['expired'], 1)
        self.assertEqual(s['count'], 1)

    def test_expired_counter_increments(self):
        self.inbox.offer(key="a", text="x", goal_id=1, ttl_seconds=5)
        self.inbox.offer(key="b", text="y", goal_id=2, ttl_seconds=5)
        self.ck.advance(6)
        s = self.inbox.status()
        self.assertEqual(s['expired'], 2)


class TestOverflow(unittest.TestCase):
    def setUp(self):
        self.ck = FakeClock(0.0)
        self.inbox = ObservationInbox(capacity=2, clock=self.ck)

    def test_evict_oldest(self):
        self.inbox.offer(key="a", text="1", goal_id=1)
        self.inbox.offer(key="b", text="2", goal_id=2)
        self.inbox.offer(key="c", text="3", goal_id=3)
        s = self.inbox.status()
        self.assertEqual(s['count'], 2)
        self.assertEqual(s['dropped'], 1)
        self.assertTrue(s['overloaded'])

    def test_evicted_is_gone(self):
        self.inbox.offer(key="a", text="1", goal_id=1)
        self.inbox.offer(key="b", text="2", goal_id=2)
        self.inbox.offer(key="c", text="3", goal_id=3)
        items = self.inbox.take(1)
        self.assertEqual(len(items), 0)
        items = self.inbox.take(3)
        self.assertEqual(len(items), 1)

    def test_accepted_true_on_overflow(self):
        self.inbox.offer(key="a", text="1", goal_id=1)
        self.inbox.offer(key="b", text="2", goal_id=2)
        r = self.inbox.offer(key="c", text="3", goal_id=3)
        self.assertTrue(r['accepted'])
        self.assertEqual(r['dropped'], 1)


class TestTake(unittest.TestCase):
    def setUp(self):
        self.ck = FakeClock(0.0)
        self.inbox = ObservationInbox(capacity=16, clock=self.ck)

    def test_take_max_4(self):
        for i in range(6):
            self.inbox.offer(key=f"k{i}", text=f"t{i}", goal_id=7)
        items = self.inbox.take(7)
        self.assertEqual(len(items), 4)

    def test_take_returns_dicts(self):
        self.inbox.offer(key="k", text="hello", goal_id=5)
        items = self.inbox.take(5)
        self.assertIn('key', items[0])
        self.assertIn('text', items[0])
        self.assertIn('goal_id', items[0])
        self.assertIn('expires_at', items[0])
        self.assertNotIn('status', items[0])

    def test_take_removes(self):
        self.inbox.offer(key="k", text="v", goal_id=5)
        self.inbox.take(5)
        self.assertEqual(self.inbox.take(5), [])

    def test_take_other_goal_untouched(self):
        self.inbox.offer(key="a", text="x", goal_id=1)
        self.inbox.offer(key="b", text="y", goal_id=2)
        self.inbox.take(1)
        items = self.inbox.take(2)
        self.assertEqual(len(items), 1)


class TestClearAndStatus(unittest.TestCase):
    def setUp(self):
        self.ck = FakeClock(0.0)
        self.inbox = ObservationInbox(capacity=4, clock=self.ck)

    def test_clear_removes_pending(self):
        self.inbox.offer(key="a", text="x", goal_id=1)
        self.inbox.clear()
        self.assertEqual(self.inbox.status()['count'], 0)

    def test_clear_keeps_counters(self):
        self.inbox.offer(key="a", text="x", goal_id=1)
        self.inbox.offer(key="a", text="y", goal_id=1)
        self.inbox.clear()
        s = self.inbox.status()
        self.assertEqual(s['coalesced'], 1)
        self.assertEqual(s['count'], 0)

    def test_status_no_text(self):
        self.inbox.offer(key="a", text="secret", goal_id=1)
        s = self.inbox.status()
        for v in s.values():
            self.assertNotEqual(v, "secret")

    def test_status_fields(self):
        s = self.inbox.status()
        for f in ('count','capacity','coalesced','expired','dropped','overloaded'):
            self.assertIn(f, s)


if __name__ == '__main__':
    unittest.main()
