import importlib.util
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

import psutil

path = Path(__file__).resolve().parents[1] / "src/gamma/memory/journal_identity.py"
spec = importlib.util.spec_from_file_location("journal_identity", path)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class IdentityTests(unittest.TestCase):
    def test_current_process_identity_and_pid_reuse_are_conservative(self):
        host, pid, started = identity.capture_identity()
        self.assertEqual(host, socket.gethostname())
        self.assertEqual(pid, os.getpid())
        self.assertEqual(started, psutil.Process(pid).create_time())
        self.assertFalse(identity.owner_is_dead(host, pid, started))
        self.assertFalse(identity.owner_is_dead(host, pid, started - 1))
        self.assertFalse(identity.owner_is_dead("synthetic-other-host", pid, started))
        for invalid in (None, 0, -1, math.nan, math.inf):
            self.assertFalse(identity.owner_is_dead(host, pid, invalid))
        self.assertFalse(identity.owner_is_dead(host, None, started))

    def test_actual_live_then_exited_process(self):
        child = subprocess.Popen([sys.executable, "-c", "print('ready',flush=True); input()"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            started = psutil.Process(child.pid).create_time()
            host = socket.gethostname()
            self.assertFalse(identity.owner_is_dead(host, child.pid, started))
            child.stdin.write("exit\n")
            child.stdin.flush()
            self.assertEqual(child.wait(timeout=5), 0)
            self.assertTrue(identity.owner_is_dead(host, child.pid, started))
            for invalid in (None, False, 0, -1, math.nan, math.inf):
                self.assertFalse(identity.owner_is_dead(host, child.pid, invalid))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()

    def test_inaccessible_identity_is_preserved(self):
        host, pid, started = identity.capture_identity()
        with patch.object(psutil, "Process", side_effect=psutil.AccessDenied(pid)):
            self.assertFalse(identity.owner_is_dead(host, pid, started))


if __name__ == "__main__":
    unittest.main()
