"""Real-file races and descriptor cleanup for legacy capture, without services."""
import hashlib
import os
from pathlib import Path

import pytest

from gamma.coordination import legacy_bundle_inventory as module


def test_fingerprint_matches_bytes_and_metadata_without_writes(tmp_path):
    path = tmp_path/'sample'
    path.write_bytes('cafe\u0301\n'.encode())
    original = path.stat()
    first = module.fingerprint(path, limit=100)
    assert first == module.fingerprint(path, limit=100)
    assert first == {**module.stat_fields(original), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    assert path.stat().st_mtime_ns == original.st_mtime_ns


@pytest.mark.parametrize('limit', [0, -1, True, False, 1.5, '8', None])
def test_invalid_limits_fail_before_open(tmp_path, limit):
    with pytest.raises(ValueError):
        module.fingerprint(tmp_path/'absent', limit=limit)


def test_special_files_links_and_symlink_ancestor_fail_without_waiting(tmp_path):
    path = tmp_path/'sample'
    path.write_bytes(b'abc')
    with pytest.raises(ValueError): module.fingerprint(path, limit=2)
    with pytest.raises(ValueError): module.fingerprint(tmp_path, limit=10)
    link = tmp_path/'link'; link.symlink_to(path)
    with pytest.raises(ValueError): module.fingerprint(link, limit=10)
    folder = tmp_path/'folder'; folder.mkdir(); (folder/'file').write_bytes(b'abc')
    alias = tmp_path/'alias'; alias.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError): module.fingerprint(alias/'file', limit=10)
    fifo = tmp_path/'fifo'; os.mkfifo(fifo)
    with pytest.raises(ValueError): module.fingerprint(fifo, limit=10)


def test_parent_directory_swap_during_read_is_not_hidden_by_stable_open_fd(tmp_path, monkeypatch):
    folder = tmp_path/'folder'; folder.mkdir()
    path = folder/'file'; path.write_bytes(b'original')
    read = os.read
    swapped = False
    def switch(descriptor, size):
        nonlocal swapped
        chunk = read(descriptor, size)
        if chunk and not swapped:
            swapped = True
            folder.rename(tmp_path/'retained-original')
            folder.mkdir(); path.write_bytes(b'replaced')
        return chunk
    monkeypatch.setattr(module.os, 'read', switch)
    with pytest.raises(ValueError, match='changed'):
        module.fingerprint(path, limit=100)


def test_file_growth_during_read_fails_and_does_not_leak_descriptor(tmp_path, monkeypatch):
    path = tmp_path/'sample'; path.write_bytes(b'abc')
    read = os.read
    changed = False
    def grow(descriptor, size):
        nonlocal changed
        chunk = read(descriptor, size)
        if chunk and not changed:
            changed = True
            with path.open('ab') as stream: stream.write(b'def')
        return chunk
    monkeypatch.setattr(module.os, 'read', grow)
    before = len(list(Path('/proc/self/fd').iterdir()))
    with pytest.raises(ValueError): module.fingerprint(path, limit=3)
    assert len(list(Path('/proc/self/fd').iterdir())) == before
