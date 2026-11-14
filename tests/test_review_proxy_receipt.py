"""Actual nonprivileged candidate binding; never invokes Nginx or a model."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import uuid

import pytest


def test_actual_prefixed_launcher_receipt_binds_authenticated_owner_and_exact_process(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('Actual Linux proc/socket binding on Gamma')
    project = Path(__file__).resolve().parents[1]
    specification = importlib.util.spec_from_file_location('proxy_receipt_actual', project / 'scripts/review_proxy_lease.py')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    credentials = {key: secrets.token_urlsafe(32) for key in ('api_token', 'password', 'session_secret')}
    log = (tmp_path / 'launcher.log').open('w')
    child = subprocess.Popen([sys.executable, str(project / 'scripts/review_coordination.py'),
        '--session-id', uuid.uuid4().hex, '--model', 'fixture', '--mock', '--dashboard-port', '18708',
        '--review-prefix', '/review/shana', '--duration', '15'], cwd=project,
        env={**os.environ, 'PYTHONPATH': str(project / 'src')}, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        child.stdin.write(json.dumps(credentials) + '\n')
        child.stdin.flush()
        assert select.select([child.stdout], [], [], 25)[0], 'Candidate startup did not report its result.'
        ready = json.loads(child.stdout.readline())
        assert ready['phase'] == 'ready', ready
        packet = ready['proxy_receipt']
        path = Path(packet['path'])
        assert path.stat().st_mode & 0o777 == 0o600
        raw = path.read_bytes()
        assert module.sha(raw) == packet['sha256']
        receipt = json.loads(raw)
        assert receipt['pid'] == child.pid
        assert receipt['resolved_source'] == str(project / 'src/gamma/__init__.py')
        assert receipt['expires_at'] - receipt['created_at'] == 15
        probe = module.CandidateProbe(receipt,
            {'username': 'isolated-owner', 'password': credentials['password']})
        probe()  # Real /proc/socket checks, scoped cookie login and both real APIs.
        child.stdin.write('stop\n')
        child.stdin.flush()
        child.wait(timeout=25)
        assert child.returncode == 0
        assert json.loads(child.stdout.readline())['phase'] == 'closed'
        with pytest.raises((OSError, ValueError)):
            probe.process_binding()
    finally:
        if child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=25)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        log.close()
