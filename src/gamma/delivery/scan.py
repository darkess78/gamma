"""Incremental, nonrecursive enumeration of delivery-owned snapshot names."""
import os
import re
from pathlib import Path


class ArtifactScan:
    def __init__(self, *, name_pattern=r"[0-9a-f]{64}\.bin"):
        self._root = None
        self._iterator = None
        self._completed = False
        self._name_pattern = re.compile(name_pattern)

    def close(self):
        if self._iterator is not None:
            self._iterator.close()
        self._root, self._iterator, self._completed = None, None, False

    def next_batch(self, root, *, limit=256):
        if type(limit) is not int or not 1 <= limit <= 4096:
            raise ValueError("invalid artifact scan limit")
        root = Path(root)
        if root.is_symlink():
            self.close()
            raise ValueError("artifact root must not be a symlink")
        root = root.resolve()
        if root != self._root:
            self.close()
            self._root = root
        if self._completed:
            self._completed = False
            return [], 0
        paths, count = [], 0
        try:
            if self._iterator is None:
                if not root.exists():
                    return [], 0
                self._iterator = os.scandir(root)
            for _ in range(limit):
                try:
                    entry = next(self._iterator)
                except StopIteration:
                    self._iterator.close()
                    self._iterator = None
                    self._completed = count > 0
                    break
                count += 1
                if self._name_pattern.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    paths.append(root / entry.name)
        except OSError:
            self.close()
            raise
        return paths, count
