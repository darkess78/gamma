"""Turning a reviewed proposal into a branch the owner can merge.

The running checkout is never touched: the diff is applied in a throwaway git
worktree at the current HEAD and committed to a new branch ``shana/<job>``.
If it no longer applies cleanly (the code moved on), nothing is created and
the owner is told. Merging and restarting stay with the owner (D5).
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from . import autonomous_store as store

AUTHOR = ("-c", "user.name=Shana", "-c", "user.email=shana@gamma.local")


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[:300])
    return done.stdout


def apply_to_branch(repo: Path, root: Path, identifier: str) -> dict:
    record = store.read_job(root, identifier)
    if record.get('status') != 'awaiting_owner_review':
        raise ValueError('Only finished work waiting for review can be applied')
    diff = store.read_proposal(root, identifier)
    if not diff.strip():
        raise ValueError('The proposal is empty')
    branch = 'shana/' + identifier.removeprefix('work-')[:12]
    if _git(repo, 'branch', '--list', branch).strip():
        raise ValueError(f'Branch {branch} already exists')
    work = Path(tempfile.mkdtemp(prefix='shana-apply-')) / 'repo'
    _git(repo, 'worktree', 'add', '-q', '-b', branch, str(work), 'HEAD')
    try:
        try:
            _git(work, 'apply', '--index', '--whitespace=nowarn', '-', stdin=diff)
        except RuntimeError:
            raise ValueError('The proposal no longer applies cleanly to the current code') from None
        title = record['goal'].splitlines()[0][:72]
        _git(work, *AUTHOR, 'commit', '-q', '-m', f'{title}\n\nProposed by Shana for coding goal {identifier}.')
        commit = _git(work, 'rev-parse', 'HEAD').strip()
    except Exception:
        subprocess.run(['git', '-C', str(repo), 'worktree', 'remove', '--force', str(work)], capture_output=True)
        subprocess.run(['git', '-C', str(repo), 'branch', '-D', branch], capture_output=True)
        raise
    subprocess.run(['git', '-C', str(repo), 'worktree', 'remove', '--force', str(work)], capture_output=True)
    record.update(status='applied', stage='applied', applied_branch=branch, applied_commit=commit,
                  applied_at=time.time(), reason=f'Committed to branch {branch} for you to merge')
    store.write_json(store.job_path(root, identifier) / 'state.json', record)
    return {'id': identifier, 'branch': branch, 'commit': commit}
