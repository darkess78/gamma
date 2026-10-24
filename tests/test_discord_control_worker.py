import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('control', 'src/gamma/integrations/discord/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)

class MailboxTests(unittest.TestCase):
    def test_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            box = control.ControlMailbox(Path(d))
            item = box.submit('join')
            self.assertEqual(item['status'], 'queued')
            claimed = box.claim()
            self.assertEqual(claimed['id'], item['id'])
            self.assertIsNone(box.claim())
            box.complete(item['id'], {'ok': True, 'state': 'listening'})
            self.assertEqual(box.read(item['id'])['status'], 'completed')
            with self.assertRaises(ValueError):
                box.complete(item['id'], {'ok': True})
    def test_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            box = control.ControlMailbox(Path(d))
            for command in ('exec', '', '../join'):
                with self.assertRaises(ValueError): box.submit(command)
            with self.assertRaises(ValueError): box.read('../secret')
    def test_interrupted_commands_not_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            first = control.ControlMailbox(Path(d))
            item = first.submit('leave')
            first.claim()
            second = control.ControlMailbox(Path(d))
            self.assertIsNone(second.claim())
            self.assertEqual(second.read(item['id'])['status'], 'running')
    def test_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / 'outside').mkdir()
            (base / 'link').symlink_to(base / 'outside', target_is_directory=True)
            with self.assertRaises(ValueError): control.ControlMailbox(base / 'link').submit('join')
    def test_no_second_outstanding_command(self):
        with tempfile.TemporaryDirectory() as d:
            box=control.ControlMailbox(Path(d))
            box.submit('join')
            with self.assertRaises(ValueError): box.submit('leave')
    def test_expired_never_executed(self):
        with tempfile.TemporaryDirectory() as d:
            box=control.ControlMailbox(Path(d))
            item=box.submit('join')
            item['created_at']=0
            box._write(item)
            self.assertIsNone(box.claim())
            self.assertEqual(box.read(item['id'])['status'],'failed')
            self.assertEqual(box.submit('status')['status'],'queued')
    def test_no_secret_error_in_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            box=control.ControlMailbox(Path(d))
            item=box.submit('join'); box.claim()
            box.complete(item['id'],{'ok':False,'reason':'token secret','raw':'private','state':'error'})
            self.assertEqual(box.read(item['id'])['result'],{'ok':False,'state':'error'})

if __name__ == '__main__': unittest.main()
