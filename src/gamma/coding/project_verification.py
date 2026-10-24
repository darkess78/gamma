"""Protected repository snapshots and offline execution of frozen checks."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

from ..improvement.validation import build_candidate_sandbox_command, _minimal_launcher_environment


def snapshot(project, destination):
    project, destination = Path(project), Path(destination)
    if destination.exists() or any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('Snapshot destination must be new and safe')
    destination.mkdir(mode=0o700, parents=True)
    (destination / 'tests').mkdir()
    (destination / 'data').mkdir()
    files = []
    for folder in ['src', 'tests', 'specs', 'evaluations', 'scripts', 'deploy']:
        start = project / folder
        for directory, dirs, names in os.walk(start, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith('.') and name != '__pycache__'
                       and not (Path(directory) / name).is_symlink()]
            for name in names:
                path = Path(directory) / name
                if path.is_symlink() or path.suffix not in {'.py', '.toml', '.md', '.json', '.yaml', '.txt',
                        '.html', '.js', '.mjs', '.css', '.svg', '.sh', '.ps1', '.service', '.conf', '.pdf'}:
                    continue
                if path.stat().st_size > (64_000_000 if path.suffix == '.pdf' else 500_000):
                    continue
                files.append(path)
    for relative in ['pyproject.toml', 'README.md', 'AGENTS.md', 'config/app.example.toml',
                     'config/voices.example.toml', 'config/voices.presets.toml',
                     'config/persona.yaml', 'config/safety_banned_words.txt', 'config/improvement.toml',
                     'config/models.toml', 'config/memory.toml',
                     'images/shana/jacket shana mouth closed eyes open.png']:
        path = project / relative
        if path.is_file() and not path.is_symlink():
            if path.suffix == '.toml':
                def check_credentials(value):
                    if isinstance(value, dict):
                        for key, entry in value.items():
                            if key.lower() in {'api_key','token','access_token','password','secret','client_secret','bot_token','refresh_token'} and entry:
                                raise ValueError('Shareable snapshot configuration contains credentials')
                            check_credentials(entry)
                    elif isinstance(value, list):
                        for entry in value:check_credentials(entry)
                check_credentials(tomllib.loads(path.read_text()))
            files.append(path)
    total = 0
    for path in files:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Unsafe snapshot source')
        total += path.stat().st_size
        if total > 128_000_000:
            raise ValueError('Snapshot exceeds source budget')
        target = destination / path.relative_to(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return {str(p.relative_to(destination)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in destination.rglob('*') if p.is_file()}


def acceptance_source(source):
    """Reject empty, skipped or test-runner-mutating acceptance programs."""
    if not isinstance(source, str) or not 1 <= len(source.encode()) <= 16000:
        raise ValueError('Invalid acceptance source')
    tree = ast.parse(source)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith('test_')]
    if not functions or not any(isinstance(n, ast.Assert) for f in functions for n in ast.walk(f)):
        raise ValueError('Acceptance must exercise observable behavior with assertions')
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {'skip', 'skipif', 'xfail', '_exit', 'exit', 'main'}:
            raise ValueError('Acceptance cannot bypass verification')
        if isinstance(node, ast.Name) and node.id in {'exec', 'eval', '__import__', 'exit', 'quit'}:
            raise ValueError('Acceptance cannot bypass verification')
    return source


def run_checks(workspace, *, acceptance=None, full_suite=False, timeout=180):
    """The source, protected tests and independent checks are mounted read-only."""
    workspace = Path(workspace)
    report = dict(passed=False, infrastructure_error=False, count=0, exit_code=-1, output_tail='')
    started = time.monotonic()
    try:
        command = build_candidate_sandbox_command(workspace, profile='full_suite', timeout_seconds=timeout)
        # Keep the existing isolation and limits; replace only its fixed pytest invocation.
        at = command.index('/opt/gamma-venv/bin/python')
        targets = ['tests'] if full_suite else [acceptance]
        if not full_suite and (not isinstance(acceptance, str) or not acceptance.startswith('tests/test_goal_')
                               or '/' in acceptance[len('tests/'):]):
            raise ValueError('Invalid acceptance target')
        bootstrap = '''import json,pytest,sys
class Receipt:
 def __init__(self): self.count=0;self.failed=0;self.skipped=0
 def pytest_runtest_logreport(self,report):
  if report.when=='call':
   self.count+=1;self.failed+=int(report.failed);self.skipped+=int(report.skipped)
r=Receipt()
code=pytest.main(TARGETS+['-q','-p','no:cacheprovider'],plugins=[r])
print('GAMMA_REPOSITORY_TEST_RECEIPT:'+json.dumps({'count':r.count,'failed':r.failed,'skipped':r.skipped,'code':int(code)}))
sys.exit(int(code))
'''.replace('TARGETS', repr(targets))
        command = command[:at] + ['/opt/gamma-venv/bin/python', '-B', '-c', bootstrap]
        with tempfile.TemporaryFile() as stream:
            try:
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout,
                                        env=_minimal_launcher_environment(), check=False)
                report['exit_code'] = result.returncode
            except subprocess.TimeoutExpired:
                report['exit_code'] = 124
            stream.seek(0); output = stream.read(2_000_001)
        if len(output) > 2_000_000:
            report['exit_code'] = 125
        decoded = output.decode('utf-8', errors='replace')
        report.update(output_tail=decoded[-6000:], output_sha256=hashlib.sha256(output).hexdigest())
        receipts = [line.split(':', 1)[1] for line in decoded.splitlines()
                    if line.startswith('GAMMA_REPOSITORY_TEST_RECEIPT:')]
        if len(receipts) == 1:
            receipt = json.loads(receipts[0]); report['count'] = receipt['count']
            report['passed'] = (report['exit_code'] == 0 and receipt['code'] == 0 and
                                receipt['count'] > 0 and receipt['failed'] == 0 and
                                (full_suite or receipt['skipped'] == 0))
        report['infrastructure_error'] = any(marker in decoded for marker in
            ['bwrap:', 'Failed to connect to bus', 'Failed to start transient'])
    except (OSError, RuntimeError, ValueError) as exc:
        report.update(infrastructure_error=True, error=type(exc).__name__)
    report['duration_ms'] = round((time.monotonic() - started) * 1000)
    return report
